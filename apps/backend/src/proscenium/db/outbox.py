from collections.abc import Callable
from typing import Any

from proscenium.db.store import Store
from proscenium.jobs.gate import ExecutionGate


def dispatch_one(
    store: Store,
    identity: str,
    gate: ExecutionGate,
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
) -> None:
    """Explicit execution only. Never automatically run this during restart restoration.

    A crash after claim leaves dispatching, not pending. Reconcile before any retry.
    The handler's result is private acknowledgement evidence, not a public success claim.
    """
    with store.transaction() as tx:
        row = tx.connection.execute("SELECT kind FROM outbox WHERE id=?", (identity,)).fetchone()
        if row is None:
            from proscenium.api.errors import DomainError

            raise DomainError("not_found", 404)
        kind = row[0]
        if kind != "job.cancel":
            gate.require(str(store.project_id))
        handler = handlers[kind]  # Missing implementation must not claim or consume the intent.
        body = tx.claim_intent(identity)
    try:
        evidence = handler(body)
        if not evidence:
            raise ValueError("Missing dispatch acknowledgement")
    except Exception:
        with store.transaction() as tx:
            tx.settle_intent(
                identity, "outcome_uncertain", {"reason": "dispatch_acknowledgement_unavailable"}
            )
        raise
    with store.transaction() as tx:
        tx.settle_intent(identity, "acknowledged", evidence)
