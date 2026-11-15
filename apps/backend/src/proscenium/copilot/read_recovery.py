"""Interrupt a known lost native read wait only through an evidenced explicit Stop."""

import hashlib
import json
from typing import Any

from proscenium.contracts import canonical
from proscenium.db import Transaction

from .state import SYSTEM, TERMINAL, State, key, save


def cancelled_by_stop(
    tx: Transaction, decision: dict[str, Any], work_id: str, stop_id: str
) -> bool:
    stop = tx.get("requests", stop_id, required=False)
    phase = tx.get("copilot.stops", stop_id, required=False)
    if (
        not stop
        or not phase
        or phase["phase"] != "outcome_uncertain"
        or stop["submission"]["type"] != "stop"
        or stop["submission"].get("target_work_id") != work_id
        or stop["receipt"]["state"] != "outcome_uncertain"
        or phase["work_id"] != work_id
    ):
        return False
    operation = tx.connection.execute(
        "SELECT fingerprint FROM operations WHERE scope='request.submit' AND id=?", (stop_id,)
    ).fetchone()
    if not operation or operation[0] != hashlib.sha256(canonical(stop["submission"])).hexdigest():
        return False
    events = [
        json.loads(row[0])
        for row in tx.connection.execute("SELECT body FROM events ORDER BY sequence")
    ]
    for n, event in enumerate(events):
        if (
            n >= 2
            and event["event_type"] == "request.accepted"
            and event["request_id"] == stop_id
            and events[n - 1]["event_type"] == "work.state_changed"
            and events[n - 1]["work_id"] == work_id
            and events[n - 1]["payload"]["state"] == "stop_requested"
            and events[n - 2]["event_type"] == "decision.resolved"
            and events[n - 2]["payload"] == decision
            and events[n - 2]["payload"]["state"] == "cancelled"
        ):
            return True
    return False


def probe(
    state: State, tx: Transaction, sid: str, history: list[dict[str, Any]], context: dict[str, Any]
) -> list[dict[str, Any]]:
    departed = context.get("departed", {})
    uncertain_stop = context.get("uncertain_stop_id")
    if (
        departed.get("session_id") != sid
        or departed.get("observed_identity") == departed.get("start")
        or context.get("current_send") is not False
        or context.get("pending_empty") is not True
    ):
        return []
    result = []
    for inv in tx.all("invocations"):
        if (
            inv.get("session_id") != sid
            or inv["state"] in TERMINAL
            or inv.get("name") != "rg"
            or inv.get("kind") != "tool"
            or inv.get("invocation_id") not in context["prior_invocations"]
            or inv.get("owner_epoch") == state.services.ownership.epoch
        ):
            continue
        tool = inv["native_id"]
        starts = [
            e
            for e in history
            if e["type"] == "tool.execution_start" and e.get("data", {}).get("toolCallId") == tool
        ]
        requests = [
            e
            for e in history
            if e["type"] == "permission.requested"
            and e.get("data", {}).get("permissionRequest", {}).get("toolCallId") == tool
        ]
        if len(starts) != 1 or len(requests) != 1:
            continue
        start, event = starts[0], requests[0]
        data, args = event["data"], start["data"].get("arguments", {})
        permission = data["permissionRequest"]
        native_id = data["requestId"]
        if (
            permission.get("kind") != "read"
            or data.get("ownerSessionId") != sid
            or start["data"].get("toolName") != "rg"
            or history.index(start) >= history.index(event)
            or set(args) - {"pattern", "paths", "output_mode", "head_limit"}
            or not isinstance(args.get("pattern"), str)
            or not args["pattern"]
            or not isinstance(args.get("paths"), str)
            or args.get("output_mode") != "content"
            or type(args.get("head_limit")) is not int
            or not 1 <= args["head_limit"] <= 1000
            or inv.get("arguments_sha256") != hashlib.sha256(canonical(args)).hexdigest()
            or event["id"] not in context["validated_targets"]
        ):
            continue
        # No successful/failed native return is assumed. A completed permission is a different case.
        if any(
            (
                e["type"] == "permission.completed"
                and e.get("data", {}).get("requestId") == native_id
            )
            or (
                e["type"] not in {"permission.requested", "tool.execution_start"}
                and e.get("data", {}).get("toolCallId") == tool
            )
            for e in history
        ):
            continue
        binding = state.binding(tx, sid, "tool", tool)
        stage = tx.get("copilot.permissions", key(sid, native_id), required=False)
        work = tx.get("work", inv["work_id"], required=False)
        decisions = [d for d in tx.all("decisions") if d["invocation_id"] == inv["invocation_id"]]
        decision_binding = state.binding(tx, sid, "decision", native_id)
        fields = ("work_id", "request_id", "delivery_id", "invocation_id")
        if (
            not binding
            or not stage
            or not work
            or not decision_binding
            or len(decisions) != 1
            or work["native_session_id"] != sid
            or work["state"]
            not in (
                {"stop_requested", "awaiting_resume", "outcome_uncertain"}
                if uncertain_stop
                else {"awaiting_user", "awaiting_resume", "outcome_uncertain"}
            )
            or any(binding.get(k) != inv[k] for k in fields[:-1])
            or any(stage.get(k) != inv[k] or decision_binding.get(k) != inv[k] for k in fields)
            or stage.get("session_id") != sid
            or stage.get("tool_call_id") != tool
            or stage.get("native_id") != native_id
            or stage.get("fingerprint") != context["permission_fingerprints"].get(event["id"])
            or stage.get("reply") not in {None, "reject"}
            or stage.get("authority") not in {None, "host"}
            or stage.get("delivery_state") not in {"not_sent", "outcome_uncertain"}
            or any(
                s.get("native_id") != native_id
                for s in tx.all("copilot.permissions")
                if s.get("session_id") == sid and s.get("tool_call_id") == tool
            )
        ):
            continue
        decision = decisions[0]
        if (
            decision["native_decision_id"] != native_id
            or decision["kind"] != "permission"
            or decision["work_id"] != inv["work_id"]
            or (
                decision["state"] != "pending"
                if not uncertain_stop
                else decision["state"] != "cancelled"
                or not cancelled_by_stop(tx, decision, inv["work_id"], uncertain_stop)
            )
            or decision["response"] is not None
            or decision["resolved_by"] is not None
            or decision["delivery_state"] != "not_sent"
        ):
            continue
        result.append(
            {
                **{k: inv[k] for k in fields},
                "session_id": sid,
                "native_id": native_id,
                "tool_call_id": tool,
                "arguments_sha256": inv["arguments_sha256"],
                "permission_fingerprint": stage["fingerprint"],
                "decision_id": decision["decision_id"],
                "decision_revision": decision["state_revision"] - (1 if uncertain_stop else 0),
                "existing_stop_request_id": uncertain_stop,
                "decision_fingerprint": decision["fingerprint"],
                "source_event_ids": [start["id"], event["id"]],
                "target": context["validated_targets"][event["id"]],
                "departed_process": departed,
                "epoch": state.services.ownership.epoch,
            }
        )
    return result


def interrupt(
    tx: Transaction,
    sid: str,
    stop_id: str,
    proofs: list[dict[str, Any]],
    epoch: str,
    pending: dict[str, Any],
) -> int:
    phase = tx.require("copilot.stops", stop_id)
    request = tx.require("requests", stop_id)
    if (
        not {"items", "steeringMessages", "inFlightSteeringCount"} <= pending.keys()
        or pending["items"]
        or pending["steeringMessages"]
        or pending["inFlightSteeringCount"]
        or phase["phase"] != "reconciling"
        or phase["pause"] != "acknowledged"
        or phase["abort"] != "acknowledged"
        or not phase.get("abort_native_event_id")
        or not phase.get("idle_native_event_id")
        or request["submission"]["type"] != "stop"
        or request["receipt"]["state"] != "stop_requested"
    ):
        return 0
    return _interrupt_waits(
        tx,
        sid,
        stop_id,
        proofs,
        epoch,
        {
            "completion_basis": "native_stop_events",
            "control_event_ids": [phase["abort_native_event_id"], phase["idle_native_event_id"]],
        },
    )


def interrupt_cold(
    tx: Transaction,
    sid: str,
    stop_id: str,
    proofs: list[dict[str, Any]],
    epoch: str,
    observation_id: str,
) -> int:
    """Called only after validating the positive cold RPC barrier, not synthetic phase changes."""
    phase = tx.require("copilot.stops", stop_id)
    request = tx.require("requests", stop_id)
    if phase["phase"] != "outcome_uncertain" or request["receipt"]["state"] != "outcome_uncertain":
        return 0
    if any(p.get("existing_stop_request_id") != stop_id for p in proofs):
        return 0
    return _interrupt_waits(
        tx,
        sid,
        stop_id,
        proofs,
        epoch,
        {
            "completion_basis": "cold_inactive_main_loop_rpc",
            "rpc_observation_id": observation_id,
            "control_event_ids": [],
            "native_main_loop_interrupted": False,
        },
    )


def _interrupt_waits(
    tx: Transaction,
    sid: str,
    stop_id: str,
    proofs: list[dict[str, Any]],
    epoch: str,
    basis: dict[str, Any],
) -> int:
    phase = tx.require("copilot.stops", stop_id)
    count = 0
    for proof in proofs:
        if (
            proof["session_id"] != sid
            or proof["work_id"] != phase["work_id"]
            or proof["epoch"] != epoch
        ):
            continue
        inv = tx.require("invocations", proof["invocation_id"])
        decision = tx.require("decisions", proof["decision_id"])
        stage = tx.require("copilot.permissions", key(sid, proof["native_id"]))
        if (
            inv["state"] != "running"
            or inv.get("arguments_sha256") != proof["arguments_sha256"]
            or any(
                inv.get(k) != proof[k]
                for k in ("session_id", "work_id", "request_id", "delivery_id", "invocation_id")
            )
            or inv["native_id"] != proof["tool_call_id"]
            or inv["name"] != "rg"
            or decision["state"] != "cancelled"
            or decision["state_revision"] != proof["decision_revision"] + 1
            or decision["fingerprint"] != proof["decision_fingerprint"]
            or decision["response"] is not None
            or decision["resolved_by"] is not None
            or decision["delivery_state"] != "not_sent"
            or decision["native_decision_id"] != proof["native_id"]
            or stage["fingerprint"] != proof["permission_fingerprint"]
            or stage.get("reply") not in {None, "reject"}
            or stage.get("authority") not in {None, "host"}
            or any(
                stage.get(k) != proof[k]
                for k in (
                    "session_id",
                    "native_id",
                    "tool_call_id",
                    "work_id",
                    "request_id",
                    "delivery_id",
                    "invocation_id",
                )
            )
            or any(
                c.get("result") in {"approved", "approve-once"}
                for c in stage.get("completions", [])
            )
        ):
            continue
        completion_ids = [
            c["native_event_id"] for c in stage.get("completions", []) if c.get("native_event_id")
        ]
        evidence = {
            **proof,
            "kind": "stopped_native_read_wait",
            "stop_request_id": stop_id,
            "source_event_ids": [
                *proof["source_event_ids"],
                *completion_ids,
                *basis["control_event_ids"],
            ],
            "native_permission_completion_observed": bool(completion_ids),
            "native_return_observed": False,
            "native_exit_code": None,
            "result": "Read wait interrupted by verified Stop; native search result remains unknown.",
        }
        evidence.update({k: v for k, v in basis.items() if k != "control_event_ids"})
        evidence["pending_empty"] = True
        tx.put("copilot.permission_outcomes", key(sid, proof["native_id"]), evidence)
        save(
            tx,
            "invocations",
            inv["invocation_id"],
            {**inv, "state": "cancelled", "recovery": evidence},
        )
        tx.emit(
            "tool.completed",
            {
                "invocation_id": inv["invocation_id"],
                "name": "rg",
                "state": "cancelled",
                "summary": evidence["result"],
                "artifact_ids": [],
                "model_id": inv.get("model_id"),
            },
            actor=SYSTEM,
            work_id=inv["work_id"],
            request_id=inv["request_id"],
        )
        count += 1
    return count


def retired_stage(tx: Transaction, stage: dict[str, Any]) -> bool:
    evidence = tx.get(
        "copilot.permission_outcomes", key(stage["session_id"], stage["native_id"]), required=False
    )
    return bool(
        evidence
        and evidence.get("kind") == "stopped_native_read_wait"
        and all(
            evidence.get(k) == stage.get(k)
            for k in (
                "session_id",
                "native_id",
                "tool_call_id",
                "work_id",
                "request_id",
                "invocation_id",
            )
        )
    )
