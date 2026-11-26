"""Dependency container. Feature routers consume this single accepted shared API."""

import asyncio
import secrets
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Protocol

from fastapi import Request

from proscenium.api.errors import DomainError
from proscenium.auth import Auth, Caller
from proscenium.configuration import Settings
from proscenium.contracts import validate
from proscenium.db import Store
from proscenium.db.ownership import Ownership
from proscenium.db.pagination import Cursors
from proscenium.db.registry import Registry
from proscenium.jobs import JobService
from proscenium.jobs.gate import ExecutionGate
from proscenium.jobs.resources import Resources


def _project_lock(runtime: Path) -> Ownership:
    from proscenium.projects.workspace_fs import workspace_ancestor, workspace_errors

    with workspace_errors(runtime), workspace_ancestor(runtime) as (runtime_fd, _, _):
        return Ownership(runtime / "owner.lock", dir_fd=runtime_fd)


class RuntimeAdapter(Protocol):
    """W01 supplies the tested E01 adapter. No implicit construction or execution on read."""

    async def reconcile(self, project_id: str) -> dict[str, Any]: ...
    async def resume(self, project_id: str, operation_id: str) -> None: ...
    async def stop(self, project_id: str, work_id: str, operation_id: str) -> None: ...


class CommitSignals:
    """Loop-owned signals, published by SQLite writers in any thread."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._waiters: dict[tuple[asyncio.AbstractEventLoop, asyncio.Event], str | None] = {}

    @contextmanager
    def subscribe(self, project_id: str | None = None) -> Iterator[asyncio.Event]:
        event = asyncio.Event()
        key = (asyncio.get_running_loop(), event)
        with self._lock:
            self._waiters[key] = project_id
        try:
            yield event
        finally:
            with self._lock:
                del self._waiters[key]

    def notify(self, project_id: str | None = None) -> None:
        with self._lock:
            for (loop, event), subscribed in self._waiters.items():
                if project_id is None or project_id == subscribed:
                    with suppress(RuntimeError):  # The subscriber's loop may have closed.
                        loop.call_soon_threadsafe(event.set)


@dataclass
class Services:
    settings: Settings
    ownership: Ownership
    registry: Registry
    auth: Auth
    gate: ExecutionGate
    jobs: JobService
    resources: Resources
    cursors: Cursors
    runtime: RuntimeAdapter | None = None
    extensions: dict[str, Any] = field(default_factory=dict)
    project_ownership: dict[str, Ownership] = field(default_factory=dict)
    activation_lock: Any = field(default_factory=RLock, repr=False)
    projection_running: set[str] = field(default_factory=set, repr=False)
    event_signals: CommitSignals = field(default_factory=CommitSignals, repr=False)
    lifecycle_signal: CommitSignals = field(default_factory=CommitSignals, repr=False)

    @classmethod
    def create(cls, settings: Settings) -> "Services":
        ownership = Ownership(settings.data_directory / "owner.lock")
        services: Services | None = None
        try:
            barrier = RLock()
            store = Store(settings.data_directory / "registry.sqlite3", write_barrier=barrier)
            registry = Registry(store)
            with store.transaction() as tx:
                secret = tx.get("private", "cursor_key", required=False)
                if secret is None:
                    secret = {"key": secrets.token_hex(32)}
                    tx.put("private", "cursor_key", secret)
                tx.put(
                    "private",
                    "owner",
                    {
                        "epoch": ownership.epoch,
                        "pid": ownership.pid,
                        "process_start": ownership.process_start,
                    },
                    expected_revision=tx.revision("private", "owner")
                    if tx.get("private", "owner", required=False)
                    else None,
                )
            gate = ExecutionGate()
            services = cls(
                settings,
                ownership,
                registry,
                Auth(store),
                gate,
                JobService(registry, gate, settings, ownership.epoch),
                Resources(store),
                Cursors(bytes.fromhex(secret["key"])),
                activation_lock=barrier,
            )
            registry.project_committed = services.reconcile_project_projection
            registry.on_project_commit = services._project_commit
            registry.on_registry_commit = services._registry_commit
            active = registry.active_id()
            if active:
                services.acquire_project(active)
                services.reconcile_project_projection(active)
            return services
        except Exception:
            if services is not None:
                services.close()
            else:
                ownership.close()
            raise

    def _project_commit(self, project_id: str, namespaces: set[str], emitted_events: bool) -> None:
        if emitted_events:
            self.event_signals.notify(project_id)
        if emitted_events or namespaces & {
            "jobs",
            "outbox",
            "work",
            "decisions",
            "requests",
            "native_workflows",
            "preview_intents",
            "reviews",
        }:
            self.lifecycle_signal.notify()

    def _registry_commit(self, namespaces: set[str]) -> None:
        self.lifecycle_signal.notify()
        if namespaces & {"registry", "projects"}:
            self.event_signals.notify()

    def acquire_project(self, project_id: str) -> None:
        if project_id not in self.project_ownership:
            store = self.registry.project_store(project_id)
            self.project_ownership[project_id] = _project_lock(store.path.parent)
        # Activation never opens execution. W01 resumes after reconciliation explicitly.
        self.gate.close(project_id)

    def _profile_state(self, tx: Any) -> dict[str, Any]:
        return tx.get("private", "configuration.profiles", required=False) or dict(
            revision=0, overrides={}, deleted=[], order=[]
        )

    def _effective_profiles(self, stored: dict[str, Any]) -> list[dict[str, Any]]:
        base_ids = {p["profile_id"] for p in self.settings.profiles}
        identities = [p["profile_id"] for p in self.settings.profiles] + [
            identity for identity in stored["order"] if identity not in base_ids
        ]
        profiles = {p["profile_id"]: p for p in self.settings.profiles} | stored["overrides"]
        return deepcopy([profiles[i] for i in identities if i not in stored["deleted"]])

    def effective_profiles(self) -> list[dict[str, Any]]:
        with self.registry.store.transaction(write=False) as tx:
            return self._effective_profiles(self._profile_state(tx))

    def configuration_view(self) -> dict[str, Any]:
        with self.registry.store.transaction(write=False) as tx:
            stored = self._profile_state(tx)
        view = deepcopy(self.settings.view())
        view.update(
            revision=self.settings.revision + stored["revision"],
            profiles=self._effective_profiles(stored),
        )
        for service in view["services"]:
            recipe = self.settings.provider_options.get("image_recipes", {}).get(
                service["provider_id"]
            )
            if service["model_id"] is None and isinstance(recipe, dict):
                service["model_id"] = recipe.get("unet") or recipe.get("checkpoint")
        return validate("ConfigurationView", view)

    def upsert_profile(
        self, caller: Caller, profile_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self._mutate_profile(caller, profile_id, body, delete=False)

    def delete_profile(
        self, caller: Caller, profile_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self._mutate_profile(caller, profile_id, body, delete=True)

    def _mutate_profile(
        self, caller: Caller, profile_id: str, body: dict[str, Any], *, delete: bool
    ) -> dict[str, Any]:
        if caller.channel != "owner":
            raise DomainError("scope_denied", 403)
        validate("AuthoringProfileDelete" if delete else "AuthoringProfileUpsert", body)
        if body["profile_id"] != profile_id:
            raise DomainError("invalid_request", 422)
        selected = None
        if not delete:
            selected = dict(
                profile_id=profile_id,
                name=body["name"].strip(),
                lead_model_id=body["lead_model_id"].strip(),
                worker_model_ids=[m.strip() for m in body["worker_model_ids"]],
                scope_status="project_scoped",
            )
            if not all(
                [selected["name"], selected["lead_model_id"], *selected["worker_model_ids"]]
            ):
                raise DomainError("invalid_request", 422)
        with self.registry.store.transaction() as tx:

            def effect() -> dict[str, Any]:
                stored = self._profile_state(tx)
                current = self.settings.revision + stored["revision"]
                if body["expected_revision"] != current:
                    raise DomainError("revision_conflict", 409, current_revision=current)
                profiles = self._effective_profiles(stored)
                if delete:
                    if not any(p["profile_id"] == profile_id for p in profiles):
                        raise DomainError("not_found", 404)
                    if not any(
                        p["profile_id"] != profile_id and p["scope_status"] == "project_scoped"
                        for p in profiles
                    ):
                        raise DomainError(
                            "invalid_request",
                            422,
                            "At least one authoring profile must remain configured",
                        )
                    if any(
                        (
                            tx.get("private", "settings:" + p["project_id"], required=False) or {}
                        ).get("default_profile_id")
                        == profile_id
                        for p in tx.all("projects")
                    ):
                        raise DomainError(
                            "project_busy",
                            409,
                            "Switch the project to another profile before deleting this profile",
                        )
                    stored["deleted"].append(profile_id)
                else:
                    stored["overrides"][profile_id] = selected
                    if profile_id not in stored["order"]:
                        stored["order"].append(profile_id)
                    stored["deleted"] = [i for i in stored["deleted"] if i != profile_id]
                stored["revision"] += 1
                existing = tx.get("private", "configuration.profiles", required=False)
                tx.put(
                    "private",
                    "configuration.profiles",
                    stored,
                    expected_revision=tx.revision("private", "configuration.profiles")
                    if existing
                    else None,
                )
                return {"revision": self.settings.revision + stored["revision"]}

            tx.operation("configuration.profiles", body["operation_id"], body, effect)
        return self.configuration_view()

    def activate_project(self, project_id: str) -> None:
        """W02 calls after validating owner command/project identity, before emitting its receipt."""
        with self.activation_lock:
            old = self.registry.active_id()
            if old == project_id:
                return
            if old:
                self.registry.assert_settled(old)
                self.gate.close(old)
                self.registry.assert_settled(old)
            target = self.registry.project_store(project_id, active=False)
            owner = _project_lock(target.path.parent)
            try:
                self.registry.activate(project_id)
            except Exception:
                owner.close()
                raise
            self.project_ownership[project_id] = owner
            self.gate.close(project_id)
            if old and old in self.project_ownership:
                self.project_ownership.pop(old).close()

    def extension(self, name: str) -> Any:
        if name not in self.extensions:
            raise DomainError("capability_unavailable", 501, f"{name} extension is not installed")
        return self.extensions[name]

    def reconcile_project_projection(self, project_id: str) -> None:
        from proscenium.api.project_projection import reconcile

        reconcile(self, project_id)

    def close(self) -> None:
        lifecycle = self.extensions.get("lifecycle")
        if lifecycle is not None and lifecycle.tasks:
            raise RuntimeError("Await the feature lifecycle before closing service ownership")
        workflows = self.extensions.get("workflows")
        if workflows is not None:
            # Safe for passive/idle containers. An active callback, IO writer or
            # export must instead finish await workflows.close() first.
            workflows.close_idle()
        provider = self.extensions.get("providers")
        try:
            if provider is not None and getattr(provider, "owns_client", False):
                provider.client.close()  # Also closes an unused caller-managed container's client.
        finally:
            for owner in self.project_ownership.values():
                owner.close()
            self.ownership.close()


def get_services(request: Request) -> Services:
    return request.app.state.services
