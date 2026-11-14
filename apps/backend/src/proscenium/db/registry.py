from pathlib import Path
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.contracts import validate
from proscenium.db.store import Store


class Registry:
    def __init__(self, store: Store):
        self.store = store

    def register(self, record: dict[str, Any]) -> None:
        validate("ProjectRecord", record)
        record = {**record, "path": str(Path(record["path"]).resolve(strict=True))}
        with self.store.transaction() as tx:
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
        return Store(runtime / "state.sqlite3", project_id)

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
            blockers = {
                "jobs": {"succeeded", "failed", "cancelled"},
                "work": {"completed", "failed", "stopped"},
                "decisions": {"answered", "declined", "cancelled", "expired"},
                "invocations": {"completed", "failed", "cancelled"},
            }
            for namespace, terminal in blockers.items():
                if any(record.get("state") not in terminal for record in tx.all(namespace)):
                    raise DomainError("project_busy", 409)
            if tx.connection.execute(
                "SELECT 1 FROM outbox WHERE state IN ('dispatching','outcome_uncertain') LIMIT 1"
            ).fetchone():
                raise DomainError("project_busy", 409)
