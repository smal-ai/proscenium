"""Dependency container. Feature routers consume this single accepted shared API."""

import secrets
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol

from fastapi import Request

from proscenium.api.errors import DomainError
from proscenium.auth import Auth
from proscenium.configuration import Settings
from proscenium.db import Store
from proscenium.db.ownership import Ownership
from proscenium.db.pagination import Cursors
from proscenium.db.registry import Registry
from proscenium.jobs import JobService
from proscenium.jobs.gate import ExecutionGate
from proscenium.jobs.resources import Resources


class RuntimeAdapter(Protocol):
    """W01 supplies the tested E01 adapter. No implicit construction or execution on read."""

    async def reconcile(self, project_id: str) -> dict[str, Any]: ...
    async def resume(self, project_id: str, operation_id: str) -> None: ...
    async def stop(self, project_id: str, work_id: str, operation_id: str) -> None: ...


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

    @classmethod
    def create(cls, settings: Settings) -> "Services":
        ownership = Ownership(settings.data_directory / "owner.lock")
        try:
            store = Store(settings.data_directory / "registry.sqlite3")
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
            )
            active = registry.active_id()
            if active:
                services.acquire_project(active)
            return services
        except Exception:
            ownership.close()
            raise

    def acquire_project(self, project_id: str) -> None:
        if project_id not in self.project_ownership:
            store = self.registry.project_store(project_id)
            self.project_ownership[project_id] = Ownership(store.path.parent / "owner.lock")
        # Activation never opens execution. W01 resumes after reconciliation explicitly.
        self.gate.close(project_id)

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
            owner = Ownership(target.path.parent / "owner.lock")
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

    def close(self) -> None:
        for owner in self.project_ownership.values():
            owner.close()
        self.ownership.close()


def get_services(request: Request) -> Services:
    return request.app.state.services
