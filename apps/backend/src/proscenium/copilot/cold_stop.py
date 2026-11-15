"""Positive RPC inactivity proof for one existing uncertain departed read-only Stop."""

import hashlib
import time
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.contracts import canonical

METHODS = (
    "session.queue.pendingItems",
    "session.queue.setDrainPaused",
    "session.queue.pendingItems",
    "session.tasks.list",
    "session.tools.listPendingRequests",
    "session.queue.pendingItems",
    "session.abort",
    "session.queue.pendingItems",
    "session.interruptMainTurn",
    "session.queue.pendingItems",
)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise DomainError("attribution_unavailable", 409, reason=reason)


def queue_empty(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and isinstance(value.get("items"), list)
        and isinstance(value.get("steeringMessages"), list)
        and value["items"] == []
        and value["steeringMessages"] == []
        and type(value.get("inFlightSteeringCount")) is int
        and value["inFlightSteeringCount"] == 0
        and value.get("withdrawableTurnMessageId") is None
    )


def tasks_inactive(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and isinstance(value.get("tasks"), list)
        and all(
            isinstance(t, dict)
            and isinstance(t.get("id"), str)
            and bool(t["id"])
            and t.get("type") in {"agent", "client", "shell"}
            and t.get("status") in {"completed", "failed", "cancelled"}
            for t in value["tasks"]
        )
    )


def external_empty(value: Any) -> bool:
    return isinstance(value, dict) and value.get("items") == []


def validate(snapshot: dict[str, Any], session_id: str) -> None:
    require(
        snapshot.get("session_id") == session_id and snapshot.get("non_continuing") is True,
        "wrong_cold_session",
    )
    require(snapshot.get("complete") is True, "incomplete_rpc_snapshot")
    records = snapshot.get("rpc", [])
    require(
        isinstance(records, list) and all(isinstance(r, dict) for r in records),
        "malformed_rpc_records",
    )
    require(tuple(r.get("method") for r in records) == METHODS, "wrong_rpc_sequence")
    require(len({r.get("rpc_request_id") for r in records}) == len(METHODS), "duplicate_rpc_id")
    for record in records:
        require(
            isinstance(record.get("rpc_request_id"), str)
            and bool(record["rpc_request_id"])
            and record.get("status") == "succeeded"
            and record.get("session_id") == session_id,
            "unproven_rpc",
        )
        require(
            record.get("summary_sha256")
            == hashlib.sha256(canonical(record.get("response"))).hexdigest(),
            "changed_rpc_summary",
        )
        require(
            isinstance(record.get("response_sha256"), str) and len(record["response_sha256"]) == 64,
            "missing_rpc_hash",
        )
    for n in (0, 2, 5, 7, 9):
        require(queue_empty(records[n]["response"]), "queued_or_inflight_work")
    require(
        records[1].get("parameters") == {"paused": True} and records[1]["response"] is None,
        "no_pause_ack",
    )
    require(tasks_inactive(records[3]["response"]), "background_task_active_or_unknown")
    require(external_empty(records[4]["response"]), "external_tool_pending")
    require(
        isinstance(records[6]["response"], dict)
        and records[6]["response"].get("success") is True
        and not records[6]["response"].get("error"),
        "no_abort_ack",
    )
    require(
        records[8].get("parameters") == {"flushQueued": False}
        and isinstance(records[8]["response"], dict)
        and records[8]["response"].get("interrupted") is False,
        "main_loop_not_proven_inactive",
    )
    finish, start = snapshot.get("finished_monotonic"), snapshot.get("started_monotonic")
    require(
        isinstance(finish, (float, int)) and isinstance(start, (float, int)),
        "missing_control_clock",
    )
    assert isinstance(finish, (float, int)) and isinstance(start, (float, int))
    require(
        0 <= time.monotonic() - finish <= 5 and 0 <= finish - start <= 30, "stale_control_proof"
    )


def apply(
    state: Any, tx: Any, stop_id: str, proofs: list[dict[str, Any]], observation: dict[str, Any]
) -> bool:
    from proscenium.db.store import now

    from .read_recovery import interrupt_cold
    from .state import TERMINAL, save

    phase = tx.require("copilot.stops", stop_id)
    if phase["phase"] == "confirmed" and phase.get("cold_observation_id") == observation["id"]:
        return False
    validate(observation["snapshot"], observation["session_id"])
    require(observation["epoch"] == state.services.ownership.epoch, "wrong_observation_epoch")
    require(phase == observation["previous_stop_phase"], "changed_stop_intent")
    stop = tx.require("requests", stop_id)
    require(
        stop["submission"] == observation["stop_payload"]
        and stop["receipt"] == observation["previous_stop_receipt"],
        "changed_stop_receipt",
    )
    require(
        stop["submission"]["type"] == "stop" and stop["receipt"]["state"] == "outcome_uncertain",
        "not_uncertain_owner_stop",
    )
    work_id = phase["work_id"]
    work = tx.require("work", work_id)
    require(
        work == observation["previous_work"]
        and work["state"] in {"stop_requested", "awaiting_resume", "outcome_uncertain"},
        "changed_original_work",
    )
    require(
        all(
            hashlib.sha256(canonical(tx.require(r["namespace"], r["id"]))).hexdigest()
            == r["sha256"]
            for r in observation["candidate_records"]
        ),
        "changed_read_binding",
    )
    require(
        len(proofs) == 1
        and proofs[0]["existing_stop_request_id"] == stop_id
        and proofs[0]["work_id"] == work_id,
        "not_exact_read_candidate",
    )
    allowed = {p["invocation_id"] for p in proofs}
    require(
        {i.get("invocation_id") for i in tx.all("invocations") if i["state"] not in TERMINAL}
        == allowed,
        "other_live_invocation",
    )
    require(
        not any(
            w["work_id"] != work_id and w["state"] not in TERMINAL | {"pending"}
            for w in tx.all("work")
        ),
        "foreign_work",
    )
    require(
        not any(
            r["submission"]["type"] == "steer" and r["receipt"]["state"] not in TERMINAL
            for r in tx.all("requests")
        ),
        "unsettled_steer",
    )
    require(
        not any(
            s.get("state") != "acknowledged"
            for s in tx.all("copilot.sends") + tx.all("copilot.continuation_sends")
        ),
        "unsettled_send",
    )
    require(
        not any(not e["resolved"] for e in tx.all("copilot.pending_events"))
        and not tx.all("copilot.duplicates"),
        "unknown_attribution",
    )
    require(
        interrupt_cold(
            tx, observation["session_id"], stop_id, proofs, observation["epoch"], observation["id"]
        )
        == len(proofs),
        "read_proof_changed",
    )
    for request in tx.all("requests"):
        if (
            request["receipt"]["work_id"] == work_id
            and request["submission"]["type"] != "stop"
            and request["receipt"]["state"] not in TERMINAL
        ):
            state.request_state(tx, request["request_id"], "stopped")
    state.work_state(
        tx,
        work_id,
        "stopped",
        evidence="Verified cold Stop RPC barrier: inactive main loop, paused empty queue, no active background tasks/external calls; departed read wait interrupted, result unknown",
    )
    state.request_state(tx, stop_id, "completed", detail_code="cold_read_stop_confirmed")
    save(
        tx,
        "copilot.stops",
        stop_id,
        {
            **phase,
            "phase": "confirmed",
            "barrier_at": now(),
            "pending_empty": True,
            "completion_basis": "cold_inactive_main_loop_rpc",
            "cold_observation_id": observation["id"],
        },
    )
    tx.settle_intent(
        stop_id,
        "acknowledged",
        {
            "cold_observation_id": observation["id"],
            "snapshot_sha256": hashlib.sha256(canonical(observation["snapshot"])).hexdigest(),
        },
    )
    return True
