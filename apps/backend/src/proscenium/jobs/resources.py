import shutil
from pathlib import Path

from proscenium.api.errors import DomainError
from proscenium.db.store import Store, Transaction


class Resources:
    """Cooperating application jobs only. No queue clearing, expiry stealing or process killing."""

    def __init__(self, registry_store: Store):
        self.store = registry_store

    def acquire(self, resource: str, project_id: str, job_id: str, attempt_id: str) -> bool:
        with self.store.transaction() as tx:
            row = tx.connection.execute(
                "SELECT project_id,job_id,attempt_id FROM resource_claims WHERE resource=?",
                (resource,),
            ).fetchone()
            if row:
                return tuple(row) == (project_id, job_id, attempt_id)
            tx.connection.execute(
                "INSERT INTO resource_claims VALUES (?,?,?,?)",
                (resource, project_id, job_id, attempt_id),
            )
            return True

    def release(
        self,
        resource: str,
        project_id: str,
        job_id: str,
        attempt_id: str,
        *,
        terminal_evidence: str,
    ) -> None:
        if not terminal_evidence:
            raise DomainError("invalid_request")
        with self.store.transaction() as tx:
            row = tx.connection.execute(
                "SELECT project_id,job_id,attempt_id FROM resource_claims WHERE resource=?",
                (resource,),
            ).fetchone()
            if row and tuple(row) != (project_id, job_id, attempt_id):
                raise DomainError("scope_denied", 403)
            tx.connection.execute("DELETE FROM resource_claims WHERE resource=?", (resource,))

    def release_if_owned(
        self,
        resource: str,
        project_id: str,
        job_id: str,
        attempt_id: str,
        *,
        terminal_evidence: str,
    ) -> None:
        """Reconcile a terminal receipt without releasing a newer attempt's claim."""
        if not terminal_evidence:
            raise DomainError("invalid_request")
        with self.store.transaction() as tx:
            # Match and delete atomically: a separate read before release would race
            # another reconciliation followed by acquisition by the next job.
            tx.connection.execute(
                "DELETE FROM resource_claims "
                "WHERE resource=? AND project_id=? AND job_id=? AND attempt_id=?",
                (resource, project_id, job_id, attempt_id),
            )


def check_storage(
    root: Path, reserve_bytes: int, estimated_remaining_bytes: int | None = None
) -> None:
    required = max(reserve_bytes, estimated_remaining_bytes or 0)
    if shutil.disk_usage(root).free < required:
        raise DomainError("storage_full", 507)


def reserve_generation(
    tx: Transaction, reservation_id: str, task_id: str, target_id: str, limit: int
) -> None:
    if limit < 1:
        raise DomainError("invalid_request")
    row = tx.connection.execute(
        "SELECT task_id,target_id,state FROM budget_reservations WHERE id=?", (reservation_id,)
    ).fetchone()
    if row:
        if tuple(row)[:2] != (task_id, target_id):
            raise DomainError("idempotency_conflict", 409)
        return
    count = tx.connection.execute(
        "SELECT COUNT(*) FROM budget_reservations WHERE task_id=? AND "
        "target_id=? AND state!='released'",
        (task_id, target_id),
    ).fetchone()[0]
    if count >= limit:
        raise DomainError("generation_budget_exhausted", 429)
    tx.connection.execute(
        "INSERT INTO budget_reservations VALUES (?,?,?,'reserved')",
        (reservation_id, task_id, target_id),
    )


def settle_generation(
    tx: Transaction, reservation_id: str, *, executed: bool, evidence: str
) -> None:
    if not evidence:
        raise DomainError("invalid_request")
    state = "consumed" if executed else "released"
    row = tx.connection.execute(
        "SELECT state FROM budget_reservations WHERE id=?", (reservation_id,)
    ).fetchone()
    if not row:
        raise DomainError("not_found", 404)
    if row[0] == state:
        return
    if row[0] != "reserved":
        raise DomainError("invalid_state_transition")
    tx.connection.execute(
        "UPDATE budget_reservations SET state=? WHERE id=?", (state, reservation_id)
    )
