"""Deterministic, read-only execution controls and recorded output projections."""

import json
from typing import Any

from proscenium.contracts import validate
from proscenium.db import Transaction

from .activity import safe_text

TERMINAL_WORK = {"completed", "failed", "stopped"}
ACTIVE_WORK = {"running", "awaiting_user"}
UNRESOLVED_STOP = {"stop_requested", "outcome_uncertain"}


def control_status(tx: Transaction, epoch: str, project: dict[str, Any]) -> dict[str, Any]:
    """Apply the owner epoch and project activation fences without mutating the gate."""
    control = tx.get("copilot.control", "gate", required=False) or {
        "fenced": False,
        "epoch": epoch,
        "revision": 0,
    }
    session = tx.get("copilot.session", "native", required=False)
    previous_epoch = control["epoch"] != epoch or (
        session is not None and session["owner_epoch"] != epoch
    )
    if not session and control.get("reconciled_epoch") != epoch:
        previous_epoch = previous_epoch or any(
            e["owner_epoch"] != epoch for e in tx.all("copilot.effective")
        )
    activation_changed = (
        project["state"] == "awaiting_resume"
        and project["state_revision"] != control.get("activation_revision")
        and bool(tx.all("requests"))
    )
    return (
        {**control, "fenced": True, "reason": "awaiting_resume"}
        if previous_epoch or activation_changed
        else control
    )


def work_outputs(tx: Transaction) -> list[dict[str, Any]]:
    """Only recorded work associations confer attribution. Ready media are not publications."""
    ready = {a["artifact_id"] for a in tx.all("artifacts") if a["state"] == "ready"}
    versions = {v["version_id"]: v for v in tx.all("versions")}
    publications = tx.all("publications")
    jobs = tx.all("jobs")
    # The shared outcomes deliberately contain no invented clock. Use persisted event
    # order when present, otherwise actual SQLite insertion order, never UUID order.
    outcomes = [
        json.loads(row[0])
        for row in tx.connection.execute(
            "SELECT body FROM records WHERE namespace='copilot.tool_outcomes' ORDER BY rowid"
        )
    ]
    completions = {}
    for row in tx.connection.execute("SELECT sequence,body FROM events ORDER BY sequence"):
        event = json.loads(row[1])
        if event["event_type"] == "tool.completed":
            completions[event["payload"]["invocation_id"]] = row[0]
    # Missing completions are not older than every observed event. Keep their
    # recorded insertion slots, ordering only the known completions amongst themselves.
    known = iter(
        sorted(
            (o for o in outcomes if o["invocation_id"] in completions),
            key=lambda o: completions[o["invocation_id"]],
        )
    )
    outcomes = [next(known) if o["invocation_id"] in completions else o for o in outcomes]
    result = []
    for work in tx.all("work"):
        identity = work["work_id"]
        artifacts: set[str] = set()
        pins: set[tuple[str, str]] = set()
        blocked = None
        for outcome in outcomes:
            if outcome.get("work_id") != identity or outcome.get("project_id") != tx.project_id:
                continue
            artifacts.update(set(outcome.get("artifact_ids", [])) & ready)
            if outcome.get("status") == "succeeded":
                pins.update(
                    (ref["item_id"], ref["version_id"])
                    for ref in outcome.get("published_versions", [])
                    if ref["version_id"] in versions
                    and versions[ref["version_id"]]["item_id"] == ref["item_id"]
                )
            if outcome["tool_name"] == "publish_outputs":
                if outcome["status"] == "succeeded" and outcome.get("published_versions"):
                    blocked = None
                elif outcome["status"] in {"failed", "outcome_uncertain"}:
                    blocked = outcome
        for publication in publications:
            if publication.get("work_id") != identity:
                continue
            # Publications are stored only after successful admission/CAS, with receipt.
            for ref in publication.get("receipt", {}).get("versions", []):
                version = versions.get(ref["version_id"])
                if version and version["item_id"] == ref["item_id"]:
                    pins.add((ref["item_id"], ref["version_id"]))
                    artifacts.update(set(version.get("artifact_ids", [])) & ready)
        for job in jobs:
            if job.get("work_id") == identity:
                artifacts.update(set(job.get("artifact_ids", [])) & ready)
        result.append(
            dict(
                work_id=identity,
                request_id=work["originating_request_id"],
                status="publication_blocked"
                if blocked
                else "published"
                if pins
                else "retained_unpublished"
                if artifacts
                else "none",
                artifact_ids=sorted(artifacts),
                published_versions=[
                    {"item_id": item, "version_id": version} for item, version in sorted(pins)
                ],
                reason_code=safe_text(blocked.get("error_code")) or None if blocked else None,
                summary=safe_text(blocked.get("safe_summary")) or None if blocked else None,
            )
        )
    return result


def latest_work_completed(tx: Transaction) -> bool:
    works = tx.all("work")
    if any(w["state"] not in TERMINAL_WORK for w in works):
        return False
    if all(w["state"] == "completed" for w in works):
        return True
    row = tx.connection.execute(
        "SELECT json_extract(body, '$.payload.work_id') FROM events "
        "WHERE json_extract(body, '$.event_type') = 'work.state_changed' "
        "ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    work_by_id = {w["work_id"]: w for w in works}
    return bool(
        row and isinstance(row[0], str) and work_by_id.get(row[0], {}).get("state") == "completed"
    )


def has_unreleased_stop(tx: Transaction) -> bool:
    return any(
        s.get("phase") == "confirmed"
        and s.get("pause") != "released"
        and s.get("hold_released") is not True
        for s in tx.all("copilot.stops")
    )


def idle_restart_hold(tx: Transaction, control: dict[str, Any]) -> bool:
    raw_gate = tx.get("copilot.control", "gate", required=False) or {}
    return (
        control["fenced"] is True
        and control.get("reason") == "awaiting_resume"
        and raw_gate.get("queue_released", True) is not False
        and latest_work_completed(tx)
        and not any(s.get("phase") != "confirmed" for s in tx.all("copilot.stops"))
        and not has_unreleased_stop(tx)
        and not any(r["state"] in {"pending", "dispatching"} for r in tx.all("copilot.resumes"))
        and not any(d["state"] == "pending" for d in tx.all("decisions"))
    )


def execution_status(tx: Transaction, epoch: str, project: dict[str, Any]) -> dict[str, Any]:
    control = control_status(tx, epoch, project)
    fenced = bool(control["fenced"])
    released = not fenced and control.get("queue_released", True) is not False
    works = tx.all("work")
    work_by_id = {w["work_id"]: w for w in works}
    active = [w for w in works if w["state"] in ACTIVE_WORK]
    non_terminal = [w for w in works if w["state"] not in TERMINAL_WORK]
    requests = tx.all("requests")
    stops = [
        r
        for r in requests
        if r["submission"]["type"] == "stop" and r["receipt"]["state"] in UNRESOLVED_STOP
    ]
    stop_targets = {r["receipt"]["work_id"] for r in stops}
    # Preserve an uncertain phase even if the receipt has not yet been reconciled.
    stop_targets.update(
        s["work_id"]
        for s in tx.all("copilot.stops")
        if s.get("phase") in {"outcome_uncertain", "admitted", "pausing", "aborting", "reconciling"}
    )
    steer = active[0]["work_id"] if len(active) == 1 and not fenced else None
    if steer in stop_targets:
        steer = None
    stoppable = [w for w in non_terminal if w["work_id"] not in stop_targets]
    stop = stoppable[0]["work_id"] if len(stoppable) == 1 else None
    reason = control.get("reason") if fenced else None
    code, message = None, None
    if stops or stop_targets:
        code = (
            "uncertain_stop"
            if any(r["receipt"]["state"] == "outcome_uncertain" for r in stops)
            or any(s.get("phase") == "outcome_uncertain" for s in tx.all("copilot.stops"))
            else "stop_requested"
        )
        message = "An earlier Stop awaits reconciliation before new work can run."
    elif fenced or any(w["state"] in {"awaiting_resume", "outcome_uncertain"} for w in works):
        code = reason or "awaiting_resume"
        message = "Execution is held awaiting explicit Resume or queue release."
        if (
            fenced
            and code == "awaiting_resume"
            and not non_terminal
            and not idle_restart_hold(tx, control)
        ):
            if (
                has_unreleased_stop(tx)
                or (tx.get("copilot.control", "gate", required=False) or {}).get(
                    "queue_released", True
                )
                is False
            ):
                code, message = "queue_paused", "Execution is paused until explicit Resume."
            elif not latest_work_completed(tx):
                code, message = (
                    "work_stopped",
                    "Previous work did not finish. Use Resume to continue it or release the queue.",
                )
            elif any(r["state"] in {"pending", "dispatching"} for r in tx.all("copilot.resumes")):
                code, message = (
                    "resume_pending",
                    "The original Resume operation awaits reconciliation.",
                )
    elif any(r["state"] in {"pending", "dispatching"} for r in tx.all("copilot.resumes")):
        code, message = "resume_pending", "The original Resume operation awaits reconciliation."
    elif active:
        awaiting = any(w["state"] == "awaiting_user" for w in active) or any(
            d["state"] == "pending" for d in tx.all("decisions")
        )
        code = "awaiting_user" if awaiting else "active_work_running"
        message = "Queued requests wait until the current work finishes."
    elif not released:
        code, message = "queue_paused", "The held queue awaits explicit release."
    elif any(r["receipt"]["state"] == "dispatching" for r in requests):
        code, message = (
            "active_work_running",
            "Queued requests wait until the current dispatch settles.",
        )
    agents = []
    for inv in tx.all("invocations"):
        if inv.get("kind") != "child" or inv["state"] not in {"running", "outcome_uncertain"}:
            continue
        live = (
            inv["state"] == "running"
            and inv.get("owner_epoch") == epoch
            and not fenced
            and work_by_id.get(inv.get("work_id"), {}).get("state") in ACTIVE_WORK
        )
        agents.append(
            dict(
                invocation_id=inv["invocation_id"],
                native_agent_id=str(inv["native_id"]),
                name=inv["name"],
                display_name=inv.get("display_name"),
                model_id=inv.get("model_id"),
                work_id=inv.get("work_id"),
                request_id=inv.get("request_id"),
                state=inv["state"],
                freshness="live" if live else "uncertain",
                updated_at=inv.get("updated_at"),
            )
        )
    result = dict(
        fenced=fenced,
        fence_reason=reason,
        queue_released=released,
        can_steer_work_id=steer,
        can_stop_work_id=stop,
        hold_reason_code=code,
        hold_reason=message,
        active_agents=sorted(agents, key=lambda a: a["invocation_id"]),
        work_outputs=work_outputs(tx),
    )
    validate("ExecutionStatus", result)
    return result
