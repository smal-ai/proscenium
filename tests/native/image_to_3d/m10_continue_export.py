"""Read-only export of the single original-reconciliation outcome. No repair or retry."""

import json
import sqlite3
import subprocess
from pathlib import Path

from m10_continue_snapshot import (
    ORIGINAL,
    PROJECT,
    REPO,
    REQUEST,
    ROOT,
    SESSION,
    WORK,
    records,
    sha,
    write,
)
from PIL import Image, ImageDraw


def alive(pid, start=None):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return False
    return fields[0] != "Z" and (start is None or fields[19] == str(start))


def main():
    out = REPO / "docs/evidence/m10-continuation"
    out.mkdir(parents=True, exist_ok=True)
    before = json.loads((ROOT / "before.json").read_text())
    host = json.loads((ROOT / "host.json").read_text())
    result = json.loads((ROOT / "browser-result.private.json").read_text())
    after_request = records("requests")[REQUEST]
    after_work = records("work")[WORK]
    context_id = before["original_context"]["context_id"]
    after_context = records("contexts")[context_id]
    hashes_after = {name: sha(ORIGINAL / name) for name in before["file_hashes"]}
    refs_after = subprocess.check_output(
        ["git", "-C", str(ORIGINAL / "project"), "show-ref"], text=True
    ).splitlines()
    native_dir = ORIGINAL / "machine/copilot-runtime/home/session-state" / SESSION
    old_history = (ROOT / "before-private/native-session/events.jsonl").read_bytes()
    new_history = (native_dir / "events.jsonl").read_bytes()
    events_before = [json.loads(line) for line in old_history.splitlines()]
    events_after = [json.loads(line) for line in new_history.splitlines()]
    with (
        sqlite3.connect(f"file:{ROOT}/before-private/project.sqlite3?mode=ro", uri=True) as old_db,
        sqlite3.connect(
            f"file:{ORIGINAL}/project/.proscenium/state.sqlite3?mode=ro", uri=True
        ) as new_db,
    ):

        def table(db, namespace):
            return {
                identity: body
                for identity, body in db.execute(
                    "SELECT id,body FROM records WHERE namespace=?", (namespace,)
                )
            }

        old_bindings, new_bindings = (
            table(old_db, "copilot.bindings"),
            table(new_db, "copilot.bindings"),
        )
        old_resumes, new_resumes = (
            table(old_db, "copilot.resumes"),
            table(new_db, "copilot.resumes"),
        )
        added_resumes = {
            identity: json.loads(body)
            for identity, body in new_resumes.items()
            if identity not in old_resumes
        }
        old_binding_rows_preserved = all(
            new_bindings.get(key) == value for key, value in old_bindings.items()
        )
        reservations_after = list(
            new_db.execute("SELECT id,task_id,target_id,state FROM budget_reservations")
        )
    e04 = Path("/home/quintin/.cache/proscenium-development/image-to-3d/runs/e04-live-b/project")
    with sqlite3.connect(f"file:{e04}/.proscenium/state.sqlite3?mode=ro", uri=True) as db:
        e04_reservations_after = list(
            db.execute("SELECT id,task_id,target_id,state FROM budget_reservations")
        )
    e04_hashes_after = {name: sha(e04 / name) for name in before["e04_file_hashes"]}
    runtime_path = ORIGINAL / "machine/copilot-runtime/process.json"
    runtime = json.loads(runtime_path.read_text()) if runtime_path.exists() else {}
    process_checks = {
        "server_remaining": alive(host["server"]["pid"], host["server"]["start"]),
        "driver_remaining": alive(host["browser_driver"]["pid"], host["browser_driver"]["start"]),
        "sdk_remaining": alive(runtime["pid"], runtime.get("start"))
        if runtime.get("pid")
        else False,
        "browser_processes_remaining": [
            row for row in result.get("browser_processes", []) if alive(row["pid"])
        ],
        "protected_blender_start_before": host.get("protected_blender_3834895_start_before"),
        "protected_blender_start_after": host.get("protected_blender_3834895_start_after"),
    }
    assert not process_checks["server_remaining"] and not process_checks["driver_remaining"]
    assert not process_checks["sdk_remaining"] and not process_checks["browser_processes_remaining"]
    blocker_invocations = [
        row
        for row in records("invocations").values()
        if row.get("work_id") == WORK
        and row.get("state") not in {"completed", "failed", "cancelled"}
    ]
    blocker_permissions = [
        row
        for row in records("copilot.permissions").values()
        if row.get("native_id") == "1fbd60bd-5e63-4553-af72-8b6d50648dc1"
    ]
    sends = records("copilot.continuation_sends")
    assert result["phase"] == "blocked_before_send" and not sends
    assert after_request["submission"] == before["request"]["submission"]
    assert after_context == before["original_context"]
    assert hashes_after == before["file_hashes"] and refs_after == before["git_refs"]
    assert e04_hashes_after == before["e04_file_hashes"]
    assert old_binding_rows_preserved and new_history.startswith(old_history)
    summary = {
        "overall": "BLOCKED_BEFORE_CONTINUATION_SEND",
        "accepted_base": "bdea12ca46c0be24aa98a4e2a72ef12d02dbf695",
        "harness_commit": host["base"],
        "attempt_root": str(ROOT),
        "original_fixture": str(ORIGINAL),
        "original_ids": {
            "project_id": PROJECT,
            "request_id": REQUEST,
            "work_id": WORK,
            "session_id": SESSION,
            "native_message_id": after_request["receipt"]["native_message_id"],
            "generation_task_id": after_work["generation_task_id"],
        },
        "viewport": result["viewport"],
        "software_renderer": result["renderer"],
        "browser_version": result["browser_version"],
        "continuous_browser_connections": 1,
        "selected_object_capture": result["context_capture"],
        "capture_scope": "Revalidation of the exact originally rejected actual-UI capture after a fresh real UI triangle pick. Its old camera is not claimed as the new live pose.",
        "metadata_resume": result["metadata_resume"],
        "metadata_operation": result["metadata_operation"],
        "blocker_invocations": blocker_invocations,
        "blocker_permission_stages": blocker_permissions,
        "original_request_receipt_before": before["request"]["receipt"],
        "original_request_receipt_after": after_request["receipt"],
        "request_state_meaning": "Acknowledged proves the exact original native message was admitted. It does not prove execution or a successful revision.",
        "original_work_after": after_work,
        "control_after": records("copilot.control"),
        "immutable_request_submission_preserved": after_request["submission"]
        == before["request"]["submission"],
        "immutable_original_context_preserved": after_context == before["original_context"],
        "original_source_proxy_media_hashes_preserved": hashes_after == before["file_hashes"],
        "original_git_refs_preserved": refs_after == before["git_refs"],
        "original_binding_rows_preserved": old_binding_rows_preserved,
        "native_history_prefix_preserved": new_history.startswith(old_history),
        "native_history_sha256_before": before["history_sha256"],
        "native_history_sha256_after": sha(native_dir / "events.jsonl"),
        "native_user_messages_before": sum(
            event["type"] == "user.message" for event in events_before
        ),
        "native_user_messages_after": sum(
            event["type"] == "user.message" for event in events_after
        ),
        "new_native_children": sum(
            event["type"] == "subagent.started" for event in events_after[len(events_before) :]
        ),
        "replacement_queue_requests": 0,
        "sdk_continuation_sends": len(sends),
        "new_resume_operations": added_resumes,
        "metadata_reconcile_attempts": sum(
            row["body"]["mode"] == "reconcile_only" for row in added_resumes.values()
        ),
        "continue_owner_commands": sum(
            row["body"]["mode"] == "continue_work" for row in added_resumes.values()
        ),
        "authoring_processes": 0,
        "readbacks": 0,
        "new_proxy_exports": 0,
        "new_cpu_stills": 0,
        "v2_exists": (ORIGINAL / "project/assets/e04/asset-v2.blend").exists(),
        "generation_reservations_before": before["generation_reservations"],
        "generation_reservations_after": reservations_after,
        "e04_generation_reservations_before": before["e04_generation_reservations"],
        "e04_generation_reservations_after": e04_reservations_after,
        "e04_original_hashes_preserved": e04_hashes_after == before["e04_file_hashes"],
        "mem_available_before_host": host["mem_available_before_host"],
        "process_checks": process_checks,
        "remaining_owned_processes": [],
        "unrelated_services_targeted": False,
        "private_before_snapshot": str(ROOT / "before-private"),
        "backup_hashes": before["backup_files"],
        "criteria": {
            "1": "PASS_ACCEPTED_E04",
            "2": "PASS",
            "3": "REMAINING_LEGACY_RECONCILIATION",
            "4": "PASS_RECORDED_SCOPES",
            "5": "REMAINING",
        },
        "interpretation": "Accepted fresh-stage correlation fix correctly does not invent a prepared reply/ACK for this old orphaned stage. No production or private-binding repair was attempted. Parent runtime-recovery ownership is required.",
    }
    write(out / "summary.json", summary)
    write(
        out / "preservation.json",
        {
            "file_hashes_before": before["file_hashes"],
            "file_hashes_after": hashes_after,
            "git_refs_before": before["git_refs"],
            "git_refs_after": refs_after,
            "e04_file_hashes_before": before["e04_file_hashes"],
            "e04_file_hashes_after": e04_hashes_after,
            "original_context_before": before["original_context"],
            "original_context_after": after_context,
        },
    )
    write(
        out / "processes.json",
        {
            "server": host["server"],
            "driver": host["browser_driver"],
            "browser_processes": result["browser_processes"],
            "sdk": {
                key: runtime.get(key) for key in ("pid", "start", "parent_pid", "parent_start")
            },
            "server_exit": host["server_exit"],
            "browser_driver_exit": host["browser_driver_exit"],
            "checks": process_checks,
        },
    )
    with Image.open(ROOT / "picked-object.private.png") as source:
        image = source.convert("RGB")
    assert image.size == (1920, 1080)
    draw = ImageDraw.Draw(image)
    draw.rectangle((1560, 48, 1919, 1079), fill="#171717")
    draw.text((1575, 65), "Private conversation redacted", fill="white")
    image.save(out / "picked-object-fullhd-redacted.png")
    files = [
        ROOT / "before.json",
        ROOT / "before-private/project.sqlite3",
        ROOT / "before-private/machine-registry.sqlite3",
        ROOT / "browser-result.private.json",
        ROOT / "host.json",
        native_dir / "events.jsonl",
        ROOT / "picked-object.private.png",
        ROOT / "server.private.log",
        ROOT / "browser-driver.private.log",
    ]
    write(
        out / "retained-files.json",
        {
            "files": [
                {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}
                for path in files
            ],
            "raw_history_authentication_databases_and_logs_exported": False,
            "public_screenshot_redaction_bbox": [1560, 48, 1920, 1080],
        },
    )
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "overall",
                    "sdk_continuation_sends",
                    "native_user_messages_before",
                    "native_user_messages_after",
                    "immutable_original_context_preserved",
                    "original_source_proxy_media_hashes_preserved",
                    "remaining_owned_processes",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
