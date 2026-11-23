from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from proscenium.db.store import Store, Transaction

if TYPE_CHECKING:
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


def has_unsettled_outbox(tx: Transaction) -> bool:
    """Check unresolved effects once the caller has verified terminal work and jobs."""
    # Resolve Copilot's proof predicates lazily, after the registry and extensions load.
    from proscenium.copilot.cold_stop import proven_departed_abandonment, settled_send_record

    rows = tx.connection.execute(
        "SELECT id, kind, state FROM outbox WHERE state IN ('dispatching', 'outcome_uncertain')"
    )
    for row_id, kind, state in rows:
        if state == "dispatching":
            return True
        if kind == "copilot.send":
            send = tx.get("copilot.sends", row_id, required=False) or {}
            request = tx.get("requests", row_id, required=False) or {}
            if proven_departed_abandonment(send) and request.get("receipt", {}).get("state") in {
                "completed",
                "failed",
                "stopped",
                "cancelled",
                "outcome_uncertain",
            }:
                continue
        elif kind == "copilot.resume":
            resume = tx.get("copilot.resumes", row_id, required=False) or {}
            continuation = tx.get("copilot.continuation_sends", row_id, required=False)
            pause_release = tx.get("copilot.pause_release", row_id, required=False)
            if (
                resume.get("state") == "outcome_uncertain"
                and (continuation is None or settled_send_record(continuation))
                and (pause_release is None or pause_release.get("state") == "acknowledged")
            ):
                continue
        return True
    return False
