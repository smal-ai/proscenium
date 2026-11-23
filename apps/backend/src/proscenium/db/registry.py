from collections.abc import Callable
from pathlib import Path
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.contracts import validate
from proscenium.db import Transaction
from proscenium.db.outbox import has_unsettled_outbox
from proscenium.db.store import Store


class Registry:
    def __init__(self, store: Store):
        self.store = store
        self.project_committed: Callable[[str], None] | None = None
        previous = store.before_commit

        def rebind_guard(tx: Transaction) -> None:
            if previous is not None:
                previous(tx)
            if "projects" not in tx.changed_namespaces:
                return
            # W02 separately validates a moved/copied target even when the original
            # path is gone. Check its private preparation state before publishing
            # the registry relocation too, without relying on installed callbacks.
            with store.transaction(write=False) as old:
                originals = {row["project_id"]: row for row in old.all("projects")}
            for record in tx.all("projects"):
                original = originals.get(record["project_id"])
                if original is None or original["path"] == record["path"]:
                    continue
                path = Path(record["path"]) / ".proscenium/state.sqlite3"
                if not path.is_file():
                    raise DomainError("source_unavailable", 422)
                with Store(path, record["project_id"]).transaction(write=False) as target:
                    self.assert_preparations_settled(target)

        store.before_commit = rebind_guard

    def register(self, record: dict[str, Any], *, expected_root: Path | None = None) -> None:
        validate("ProjectRecord", record)
        with self.store.transaction() as tx:
            resolved = Path(record["path"]).resolve(strict=True)
            if expected_root is not None and resolved != expected_root:
                raise DomainError(
                    "scope_denied", 403, f"Workspace directory changed: {expected_root}"
                )
            record = {**record, "path": str(resolved)}
            existing = tx.get("projects", record["project_id"], required=False)
            if existing:
                if existing["path"] != record["path"]:
                    raise DomainError("project_location_conflict", 409)
                if existing["repository_id"] != record["repository_id"]:
                    raise DomainError("source_conflict", 409)
                return
            if any(p["path"] == record["path"] for p in tx.all("projects")):
                raise DomainError("project_location_conflict", 409)
            tx.put("projects", record["project_id"], record, schema="ProjectRecord")

    def get(self, project_id: str) -> dict[str, Any]:
        with self.store.transaction(write=False) as tx:
            return tx.require("projects", project_id)

    def active_id(self) -> str | None:
        with self.store.transaction(write=False) as tx:
            state = tx.get("registry", "active", required=False)
            return state["project_id"] if state else None

    def project_store(self, project_id: str, *, active: bool = True) -> Store:
        if active and project_id != self.active_id():
            raise DomainError("scope_denied", 403)
        registered = Path(self.get(project_id)["path"])
        root = registered.resolve(strict=True)
        if root != registered:
            raise DomainError("project_location_conflict", 409)
        runtime = root / ".proscenium"
        if runtime.is_symlink() or (runtime / "state.sqlite3").is_symlink():
            raise DomainError("scope_denied", 403)

        def guard() -> None:
            # A previously obtained handle may outlive rebind/activation while doing
            # filesystem work. It must not mutate the abandoned original afterwards.
            if self.get(project_id)["path"] != str(root):
                raise DomainError("source_conflict", 409, "Project location changed")
            if active and self.active_id() != project_id:
                raise DomainError("scope_denied", 403)

        def before(tx) -> None:
            if "copilot.session" in tx.changed_namespaces:
                association = tx.get("copilot.session", "native", required=False)
                known = self.get(project_id)["native_session_id"]
                if association and known and association["session_id"] != known:
                    raise DomainError("source_conflict", 409, "Native session identity changed")

        def after(tx) -> None:
            if self.project_committed and tx.changed_namespaces & {
                "copilot.session",
                "copilot.control",
            }:
                self.project_committed(project_id)

        return Store(
            runtime / "state.sqlite3",
            project_id,
            write_barrier=self.store.write_barrier,
            write_guard=guard,
            before_commit=before,
            after_commit=after,
        )

    def activate(self, project_id: str) -> None:
        """W02 verifies project/source identities. This shared guard enforces settlement."""
        self.get(project_id)
        old = self.active_id()
        if old and old != project_id:
            self.assert_settled(old)
        with self.store.transaction() as tx:
            revision = tx.revision("registry", "active") if old else None
            tx.put("registry", "active", {"project_id": project_id}, expected_revision=revision)

    def assert_settled(self, project_id: str) -> None:
        with self.project_store(project_id, active=False).transaction(write=False) as tx:
            self.assert_preparations_settled(tx)
            blockers = {
                "jobs": {"succeeded", "failed", "cancelled"},
                "work": {"completed", "failed", "stopped"},
                "decisions": {"answered", "declined", "cancelled", "expired"},
                "invocations": {"completed", "failed", "cancelled"},
            }
            for namespace, terminal in blockers.items():
                if any(record.get("state") not in terminal for record in tx.all(namespace)):
                    raise DomainError("project_busy", 409)
            if has_unsettled_outbox(tx):
                raise DomainError("project_busy", 409)

    @staticmethod
    def assert_preparations_settled(tx: Transaction) -> None:
        """Durable pre-admission blockers, including operator-created Registry instances."""
        if any(
            row.get("phase") not in {"failed", "launched"} for row in tx.all("native_workflows")
        ):
            raise DomainError(
                "project_busy", 409, "Native workflow preparation requires reconciliation"
            )
