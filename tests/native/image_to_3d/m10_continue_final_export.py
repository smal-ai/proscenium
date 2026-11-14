"""Read-only export of the original Continue's real no-v2 guard. No retry or repair."""

import json
import sqlite3
import subprocess
from pathlib import Path

from m10_continue_final_snapshot import FINAL
from m10_continue_run import ticks
from m10_continue_snapshot import ORIGINAL, REPO, REQUEST, SESSION, WORK, records, sha, write


def main():
    out = REPO / "docs/evidence/m10-continuation/final"
    out.mkdir(parents=True, exist_ok=True)
    before = json.loads((FINAL / "before.json").read_text())
    result = json.loads((FINAL / "result.private.json").read_text())
    host = json.loads((FINAL / "host.json").read_text())
    history = ORIGINAL / "machine/copilot-runtime/home/session-state" / SESSION / "events.jsonl"
    prior = (FINAL / "before-private/native-session/events.jsonl").read_bytes()
    after = history.read_bytes()
    old_events = [json.loads(line) for line in prior.splitlines()]
    all_events = [json.loads(line) for line in after.splitlines()]
    new_events = all_events[len(old_events) :]
    permissions = records("copilot.permissions")
    fresh_read = next(
        row
        for row in permissions.values()
        if row.get("native_id") == "67539b84-6fdc-4a6e-89e2-4c802647f66b"
    )
    original_context = records("contexts")[before["original_context"]["context_id"]]
    original_request = records("requests")[REQUEST]
    original_work = records("work")[WORK]
    files_after = {name: sha(ORIGINAL / name) for name in before["file_hashes"]}
    failed_reports_after = {name: sha(Path(name)) for name in before["failed_report_hashes"]}
    refs_after = subprocess.check_output(
        ["git", "-C", str(ORIGINAL / "project"), "show-ref"], text=True
    ).splitlines()
    with (
        sqlite3.connect(f"file:{FINAL}/before-private/project.sqlite3?mode=ro", uri=True) as old_db,
        sqlite3.connect(
            f"file:{ORIGINAL}/project/.proscenium/state.sqlite3?mode=ro", uri=True
        ) as db,
    ):

        def ids(connection, namespace):
            return [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM records WHERE namespace=? ORDER BY id", (namespace,)
                )
            ]

        identities = {
            name: {"before": ids(old_db, name), "after": ids(db, name)}
            for name in ("versions", "jobs", "attempts", "frames", "publications")
        }
        reservations = list(
            db.execute("SELECT id,task_id,target_id,state FROM budget_reservations")
        )
    e04 = Path("/home/quintin/.cache/proscenium-development/image-to-3d/runs/e04-live-b/project")
    e04_after = {name: sha(e04 / name) for name in before["e04_file_hashes"]}
    with sqlite3.connect(f"file:{e04}/.proscenium/state.sqlite3?mode=ro", uri=True) as db:
        e04_reservations = list(
            db.execute("SELECT id,task_id,target_id,state FROM budget_reservations")
        )
    runtime = json.loads((ORIGINAL / "machine/copilot-runtime/process.json").read_text())
    remaining = []
    for role, record in (("server", host["server"]), ("driver", host["driver"]), ("sdk", runtime)):
        if ticks(record["pid"]) == str(record["start"]):
            fields = Path(f"/proc/{record['pid']}/stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] != "Z":
                remaining.append({"role": role, "pid": record["pid"], "start": record["start"]})
    assert not remaining
    browser_remaining = [
        row
        for row in result.get("browser_processes", [])
        if ticks(row["pid"]) is not None
        and Path(f"/proc/{row['pid']}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    ]
    assert not browser_remaining
    assert (
        files_after == before["file_hashes"]
        and failed_reports_after == before["failed_report_hashes"]
    )
    assert refs_after == before["git_refs"] and e04_after == before["e04_file_hashes"]
    assert (
        original_context == before["original_context"]
        and original_request["submission"] == before["request"]["submission"]
    )
    assert after.startswith(prior)
    assert len(records("copilot.continuation_sends")) == 1
    assert not (ORIGINAL / "project/assets/e04/asset-v2.blend").exists()
    legacy_before = next(
        row
        for row in before["permissions"].values()
        if row.get("native_id") == "1fbd60bd-5e63-4553-af72-8b6d50648dc1"
    )
    legacy_after = next(
        row
        for row in permissions.values()
        if row.get("native_id") == "1fbd60bd-5e63-4553-af72-8b6d50648dc1"
    )
    legacy_fields = ("authority", "reply", "delivery_state")
    assert {key: legacy_before.get(key) for key in legacy_fields} == {
        key: legacy_after.get(key) for key in legacy_fields
    }
    trace = []
    for event in new_events:
        kind, data = event["type"], event.get("data", {})
        item = {
            key: event[key]
            for key in ("type", "id", "timestamp", "parentId", "agentId")
            if key in event
        }
        if kind == "user.message":
            item["data"] = {
                key: data[key]
                for key in ("messageId", "clientTurnId", "isAutopilotContinuation")
                if key in data
            }
        elif kind == "assistant.message":
            item["public_content_empty"] = not bool(data.get("content"))
            item["tool_requests"] = len(data.get("toolRequests", []))
        elif kind == "tool.execution_start":
            item["data"] = {
                key: data[key] for key in ("toolCallId", "toolName", "model") if key in data
            }
            item["arguments"] = data.get("arguments", {})
        elif kind == "tool.execution_complete":
            item["data"] = {
                key: data[key] for key in ("toolCallId", "success", "error") if key in data
            }
        elif kind == "permission.requested":
            item["data"] = {
                key: data[key]
                for key in ("requestId", "permissionRequest", "promptRequest")
                if key in data
            }
        elif kind == "permission.completed":
            item["data"] = {
                key: data[key] for key in ("requestId", "toolCallId", "result") if key in data
            }
        elif kind in {
            "assistant.turn_start",
            "assistant.turn_end",
            "session.idle",
            "session.task_complete",
            "session.shutdown",
        }:
            if kind == "session.shutdown":
                item["shutdown_type"] = data.get("shutdownType")
        else:
            continue
        trace.append(item)
    write(out / "native-delta-redacted.json", {"events": trace})
    write(out / "read-permission.json", fresh_read)
    summary = {
        "overall": "BLOCKED_AFTER_ORIGINAL_CONTINUE_NO_V2",
        "base": "904f73383d6e9a3e0a5daa411f3628efd90bc2cf",
        "harness_commit": host["base"],
        "attempt_root": str(FINAL),
        "original_fixture": str(ORIGINAL),
        "original_ids": {
            "request": REQUEST,
            "work": WORK,
            "session": SESSION,
            "generation_task": original_work["generation_task_id"],
        },
        "metadata_resume": result["metadata_resume"],
        "metadata_operation": result["metadata_operation"],
        "owner_continue": result["owner_continue"],
        "continuation_sends": list(records("copilot.continuation_sends").values()),
        "sdk_continuation_sends": 1,
        "replacement_queue_requests": 0,
        "native_user_messages_before": sum(e["type"] == "user.message" for e in old_events),
        "native_user_messages_after": sum(e["type"] == "user.message" for e in all_events),
        "new_native_children": sum(e["type"] == "subagent.started" for e in new_events),
        "viewport": result["viewport"],
        "browser_version": result["browser_version"],
        "browser_connections": 1,
        "software_webgl_requested": True,
        "permission_decisions_reported": result["permission_decisions_reported"],
        "owner_decision_rows": records("decisions"),
        "automatic_read_rejection": fresh_read,
        "rejected_read": {
            "tool_call_id": "call_pKoAp26w293LCPhPJ3iV6uz1",
            "native_permission_id": "67539b84-6fdc-4a6e-89e2-4c802647f66b",
            "path": "assets/e04/revision-measurements.json",
            "file_exists": False,
            "authority": "automatic_policy",
            "reply": "reject",
            "human_denial_inferred": False,
        },
        "legacy_reply_authority_ack_before": {key: legacy_before.get(key) for key in legacy_fields},
        "legacy_reply_authority_ack_after": {key: legacy_after.get(key) for key in legacy_fields},
        "actual_guard": result["blocker"],
        "product_work_after": original_work,
        "product_request_receipt_after": original_request["receipt"],
        "native_final_nonempty_message_observed": any(
            e["type"] == "assistant.message" and bool(e.get("data", {}).get("content"))
            for e in new_events
        ),
        "native_task_complete_observed": any(
            e["type"] == "session.task_complete" for e in new_events
        ),
        "shutdown_interpretation": "Harness observed production Work.completed and all existing local jobs terminal, then failed its semantic no-v2 guard and closed the owned host. No independent task_complete/final-answer proof exists; natural business completion is not claimed.",
        "identities": identities,
        "authoring_processes": 0,
        "readbacks": 0,
        "new_proxy_exports": 0,
        "new_cpu_stills": 0,
        "v2_exists": False,
        "generation_reservations_after": reservations,
        "e04_generation_reservations_after": e04_reservations,
        "original_context_preserved": original_context == before["original_context"],
        "original_submission_preserved": original_request["submission"]
        == before["request"]["submission"],
        "original_source_proxy_media_hashes_preserved": files_after == before["file_hashes"],
        "failed_reports_preserved": failed_reports_after == before["failed_report_hashes"],
        "original_git_refs_preserved": refs_after == before["git_refs"],
        "e04_hashes_preserved": e04_after == before["e04_file_hashes"],
        "native_history_prefix_preserved": after.startswith(prior),
        "native_history_sha256_after": sha(history),
        "remaining_owned_processes": remaining,
        "browser_processes_remaining": browser_remaining,
        "server_exit": host["server_exit"],
        "driver_exit": host["driver_exit"],
        "protected_blender_start_before": host.get("protected_blender_3834895_start_before"),
        "protected_blender_start_after": host.get("protected_blender_3834895_start_after"),
        "mem_available_before_host": host["mem_available_before_host"],
        "mem_available_before_continue": result["mem_available_before_continue"],
        "readback_helper_prepared_but_not_executed": True,
        "shared_implementation_edits": False,
        "criteria": {
            "1": "PASS_ACCEPTED_E04",
            "2": "PASS_RETAINED_INSPECTION",
            "3": "REMAINING_NO_NATIVE_REVISION",
            "4": "PASS_RECORDED_SCOPES",
            "5": "REMAINING_NO_V2_CHECKPOINT_PUBLICATION_REPLAY",
        },
    }
    write(out / "summary.json", summary)
    write(
        out / "preservation.json",
        {
            "source_hashes_before": before["file_hashes"],
            "source_hashes_after": files_after,
            "failed_report_hashes_before": before["failed_report_hashes"],
            "failed_report_hashes_after": failed_reports_after,
            "e04_hashes_before": before["e04_file_hashes"],
            "e04_hashes_after": e04_after,
            "original_context_before": before["original_context"],
            "original_context_after": original_context,
        },
    )
    files = [
        FINAL / "before.json",
        FINAL / "host.json",
        FINAL / "result.private.json",
        FINAL / "browser-driver.private.log",
        history,
        FINAL / "before-private/project.sqlite3",
        FINAL / "before-private/machine-registry.sqlite3",
    ]
    write(
        out / "retained-files.json",
        {
            "files": [{"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p)} for p in files],
            "raw_authentication_history_or_private_chat_exported": False,
        },
    )
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "overall",
                    "sdk_continuation_sends",
                    "authoring_processes",
                    "v2_exists",
                    "remaining_owned_processes",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
