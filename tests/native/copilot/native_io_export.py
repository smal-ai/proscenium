"""Read retained copies/results only. Never opens an SDK runtime or changes work state."""

import json
import sqlite3
from pathlib import Path

from native_io_recovery import ORIGINALS, REPO, ROOT, SESSIONS, WORKS
from proscenium.copilot.launcher import identity
from proscenium.copilot.state import key
from runtime_recovery_history import sha


def load(path):
    return json.loads(path.read_text())


def checkpoint(snapshot):
    db = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        row = db.execute(
            "SELECT result FROM operations WHERE scope='sources.checkpoint' AND id='m11-unity-source-checkpoint'"
        ).fetchone()
        assert row
        return json.loads(row[0])
    finally:
        db.close()


def main():
    destination = REPO / "docs/evidence/native-io-policy"
    destination.mkdir(parents=True, exist_ok=True)
    for label in ["m11", "m10"]:
        root = ROOT / label
        if label == "m11":
            root = root / "after-preflight-import-fix"
        data = load(root / "result.json")
        before = load(root / "before.private.json")
        after = load(root / "after.private.json")
        hashes = load(root / "before-hashes.private.json")
        assert all(
            sha(root / "before" / relative) == expected for relative, expected in hashes.items()
        )
        data["counts_scope"] = (
            "One cold SDK metadata runtime; cached raw history shared by typed projection. Python tool/provider-HTTP and prompt callbacks blocked. Not an OS network sandbox."
        )
        process = data["process"]
        process["parent_same_identity_alive"] = (
            identity(process["parent_pid"]) == process["parent_start"]
        )
        assert not process["same_identity_alive"] and not process["parent_same_identity_alive"]
        original_request = data["work_before"]["originating_request_id"]
        data["original_receipt_before"] = next(
            r["receipt"]
            for r in before["records"]["requests"]
            if r["request_id"] == original_request
        )
        data["original_receipt_after"] = next(
            r["receipt"]
            for r in after["records"]["requests"]
            if r["request_id"] == original_request
        )
        data["new_native_events"] = [
            json.loads(line) for line in (root / "native-events.jsonl").read_text().splitlines()
        ]
        if label == "m11":
            data["stop_receipt"] = next(
                r["receipt"]
                for r in after["records"]["requests"]
                if r["request_id"] == data["stop_command"]["request_id"]
            )
            data["read_outcomes"] = [
                o for o in data["read_outcomes"] if o.get("kind") == "stopped_native_read_wait"
            ]
            data["missing_observations"] = [
                "No native permission.completed or rg tool.execution_complete for the retained read.",
                "Pause/Abort RPCs acknowledged but cold runtime emitted neither a fresh native abort nor session.idle.",
                "No final verified pending/idle/tool barrier was committed; dead owner plus unchanged source does not prove a read return.",
            ]
            data["preflight_correction"] = load(ROOT / "m11/preflight-failure.json")
            receipt = checkpoint(root / "before/project/.proscenium/state.sqlite3")
            db = sqlite3.connect(
                f"file:{ORIGINALS[label]}/project/.proscenium/state.sqlite3?mode=ro", uri=True
            )
            try:
                current_receipt = json.loads(
                    db.execute(
                        "SELECT result FROM operations WHERE scope='sources.checkpoint' AND id='m11-unity-source-checkpoint'"
                    ).fetchone()[0]
                )
            finally:
                db.close()
            assert (
                receipt == current_receipt
                and receipt["source_commit"] == "8d49caaee980d18a140111e81b15a46c71e229ce"
            )
            for entry in receipt["files"]:
                assert sha(ORIGINALS[label] / "project" / entry["path"]) == entry["sha256"]
            (destination / "m11-checkpoint.json").write_text(
                json.dumps(
                    dict(
                        receipt=receipt,
                        receipt_unchanged=True,
                        checkpoint_files_match_actual_bytes=True,
                    ),
                    indent=2,
                )
                + "\n"
            )
            filename = "m11-stop.json"
        else:
            relative = (
                Path("machine/copilot-runtime/home/session-state")
                / SESSIONS[label]
                / "events.jsonl"
            )
            events = [
                json.loads(line) for line in (root / "before" / relative).read_text().splitlines()
            ]
            start = next(
                n
                for n, e in enumerate(events)
                if e["type"] == "user.message"
                and e.get("data", {}).get("clientTurnId") == "dae26d99-3ef2-49ff-a5d0-78b2ee82f739"
            )
            trace = []
            for event in events[start:]:
                item = {k: event[k] for k in ["id", "type", "timestamp", "parentId"] if k in event}
                payload = event.get("data", {})
                if event["type"] == "assistant.message":
                    item.update(
                        public_content_nonempty=bool((payload.get("content") or "").strip()),
                        tool_requests=len(payload.get("toolRequests") or []),
                    )
                elif event["type"] == "tool.execution_complete":
                    item.update(
                        tool_call_id=payload.get("toolCallId"),
                        native_tool_success=payload.get("success"),
                        error_code=(payload.get("error") or {}).get("code"),
                    )
                elif event["type"] == "user.message":
                    item.update(
                        client_turn_id=payload.get("clientTurnId"),
                        native_message_id=payload.get("messageId"),
                    )
                elif event["type"] == "permission.completed":
                    item.update(
                        native_permission_id=payload.get("requestId"),
                        tool_call_id=payload.get("toolCallId"),
                        result=payload.get("result", {}).get("kind"),
                    )
                if event["type"] in {
                    "user.message",
                    "assistant.turn_start",
                    "assistant.message",
                    "tool.execution_start",
                    "tool.execution_complete",
                    "permission.requested",
                    "permission.completed",
                    "assistant.turn_end",
                    "session.task_complete",
                    "session.shutdown",
                }:
                    trace.append(item)
                if event["type"] == "session.shutdown":
                    break
            terminal = next(e for e in trace if e["type"] == "assistant.turn_end")
            db = sqlite3.connect(
                f"file:{root}/before/project/.proscenium/state.sqlite3?mode=ro", uri=True
            )
            try:
                event_binding = json.loads(
                    db.execute(
                        "SELECT body FROM records WHERE namespace='copilot.bindings' AND id=?",
                        (key(SESSIONS[label], "event", terminal["id"]),),
                    ).fetchone()[0]
                )
            finally:
                db.close()
            assert (
                event_binding["request_id"] == original_request
                and event_binding["work_id"] == WORKS[label]
            )
            cached = next(
                c
                for c in before["records"]["copilot.completion"]
                if c["request_id"] == original_request
            )
            data["completion_diagnostic"] = dict(
                source_commit="e18310c6872c166a6a7b3989730d5f3f23e85ca1",
                producer="State.reduce assistant.turn_end candidate -> Copilot.settle after idle/pending/tools/decision checks",
                producer_native_event_id=terminal["id"],
                producer_binding=event_binding,
                cached_completion_before=cached,
                durable_idle_before=before["records"].get("copilot.idle"),
                full_task_terminal_observed=any(
                    e["type"] == "session.task_complete" for e in trace
                ),
                nonempty_public_final_observed=any(
                    e["type"] == "assistant.message"
                    and e["public_content_nonempty"]
                    and not e["tool_requests"]
                    for e in trace
                ),
                native_trace=trace,
                historical_completed_record_unchanged=data["work_before"] == data["work_after"],
                interpretation="Application completed from an empty tool-batch turn boundary. No independent full-task or business-goal completion is certified. Future producer guard is fixed; this historical completed row is not reopened.",
            )
            assert cached["candidate"] and terminal["id"] == "3c0c7062-ce70-4e7e-be44-01f7260fbd0e"
            data["read_outcomes"] = []
            filename = "m10-settlement.json"
        (destination / filename).write_text(json.dumps(data, indent=2) + "\n")
    hashes = {
        p.name: sha(p) for p in destination.iterdir() if p.is_file() and p.name != "manifest.json"
    }
    (destination / "manifest.json").write_text(json.dumps(hashes, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                metadata_sends=0,
                original_checkpoints_preserved=True,
                m11_ready=False,
                m10_historical_state_preserved=True,
                owned_processes_closed=True,
            )
        )
    )


if __name__ == "__main__":
    main()
