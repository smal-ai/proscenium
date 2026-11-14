"""Allowlisted failed-slice evidence. No private native text or auth export."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from importlib.metadata import version
from pathlib import Path

import copilot._jsonrpc

REPO = Path(__file__).resolve().parents[3]


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    root = Path(sys.argv[1])
    output = Path(sys.argv[2])

    def read(name):
        return json.loads((root / name).read_text())

    plan = read("plan-private.json")
    browser = read("browser-result-private.json")
    supervisor = read("supervisor-private.json")
    reservation = read("send-reservation.json")
    audit = read("host-1-audit.json")
    users = []
    for line in (
        root / "d/copilot-runtime/home/session-state" / reservation["session_id"] / "events.jsonl"
    ).open():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if value.get("type") != "user.message":
            continue
        data = value.get("data", {})
        if data.get("clientTurnId") == plan["request_id"]:
            users.append(
                {
                    "event_id": value["id"],
                    "native_message_id": data.get("messageId"),
                    "client_turn_id": data["clientTurnId"],
                    "timestamp": value.get("timestamp"),
                    "safe_public_content_matches": data.get("content") == plan["prompt"],
                }
            )
    with closing(
        sqlite3.connect((root / "p/.proscenium/state.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as db:
        request = json.loads(
            db.execute(
                "SELECT body FROM records WHERE namespace='requests' AND id=?",
                (plan["request_id"],),
            ).fetchone()[0]
        )
        invocations = db.execute(
            "SELECT count(*) FROM records WHERE namespace='invocations'"
        ).fetchone()[0]
        jobs = db.execute("SELECT count(*) FROM records WHERE namespace='jobs'").fetchone()[0]
    failed = []
    for path in (root / "receipts").glob("*.json"):
        receipt = json.loads(path.read_text())
        if receipt["status"] == "failed":
            failed.append(
                {
                    "receipt_id": path.stem,
                    "error_type": receipt.get("error_type"),
                    "code": receipt.get("code"),
                }
            )
    report = {
        "format": "proscenium-m12-selective-ack-evidence-1",
        "status": "failed_explicit_pipe_cut_one_send_spent",
        "fault_type": "injected_selective_rpc_response_loss",
        "m12_complete": False,
        "m14_complete": False,
        "base": "2f1281e",
        "executed_commit": "63898fe",
        "export_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "root": str(root),
        "project_id": browser["project_id"],
        "native_session_id": reservation["session_id"],
        "request_id": plan["request_id"],
        "send_reservations": reservation["count"],
        "safe_public_input": plan["prompt"],
        "actual_owner_api": browser["api"],
        "browser": browser["browser_version"],
        "viewport": browser["viewport"],
        "quiet_initial": browser["quiet_initial"],
        "driver_phase": browser["phase"],
        "failed_operator_receipts": failed,
        "matching_native_user_messages_after_run": users,
        "original_request_receipt_after_cleanup": request["receipt"],
        "application_send_ack_delivered": audit.get("application_send_ack_delivered", False),
        "send_await_finished_ns": audit.get("send_await_finished_ns"),
        "target_rpc_id_and_response_hash": "not durably persisted before cut helper raised; do not reconstruct/fabricate",
        "explicit_descriptor_cut_proved": False,
        "accepted_before_explicit_cut_proved": False,
        "quiet_restart_performed": False,
        "explicit_reconcile_performed": False,
        "full_criterion_closed": False,
        "host_audit": {
            "native_processes": audit["native_processes"],
            "native_identities_gone": audit["native_identities_gone"],
            "options": audit["options"],
            "session_ids": audit["session_ids"],
            "provider_attempts": len(audit["forbidden"]),
        },
        "jobs": jobs,
        "invocations": invocations,
        "runtime": {
            "sdk": version("github-copilot-sdk"),
            "sdk_jsonrpc_source_sha256": sha(Path(copilot._jsonrpc.__file__)),
            "cli_package": json.loads(
                (REPO / "node_modules/@github/copilot/package.json").read_text()
            )["version"],
            "direct_cli_sha256": sha(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
        },
        "cleanup": {
            "recorded_processes": supervisor["recorded_process_cleanup"],
            "controller": supervisor["controller"],
            "controller_exit_code": supervisor["node_exit_code"],
            "reaped_adopted_children": supervisor["reaped_adopted_children"],
            "remaining_children": supervisor["remaining_children"],
            "signals_to_owned_children": supervisor["signals"],
            "browser_context_close_awaited": browser["browser_close_awaited"],
            "all_recorded_kernel_identities_gone": all(
                p["original_kernel_identity_gone"] for p in supervisor["recorded_process_cleanup"]
            ),
        },
        "source_hashes_at_export": {
            str(p.relative_to(REPO)): sha(p)
            for p in sorted((REPO / "tests/native/studio").glob("m12_selective*.*"))
        },
        "accepted_fixture_sources_used_read_only": {
            str(p.relative_to(REPO)): sha(p)
            for p in (
                REPO / "tests/native/studio/m12_ack_host.py",
                REPO / "tests/native/studio/m12_ack_prepare.py",
                REPO / "tests/native/studio/m12_ack_run.py",
            )
        },
        "limits": [
            "The native user message matches the one fresh request and the SDK send ACK did not return. But the explicit cut helper raised AttributeError before closing the live unbuffered FileIO descriptor.",
            "The tested offline pipe was buffered; the actual pinned SDK Popen uses bufsize=0. The helper now supports both forms and has an offline regression, but no second live send or gate variant was run.",
            "Runtime shutdown cleaned the owned transport/processes. It is not substituted for the missing targeted cut/pending-future/EOF receipt.",
            "Filter RPC ID/withheld-response hash were only in memory and were not persisted before failure. They are not fabricated from history.",
            "Native identity was projected onto the original application request through forwarded events. No post-restart admission reconciliation is claimed: restart/reconcile did not execute after the failed cut.",
            "The original fresh request remains outcome_uncertain after cleanup. No completion or rejection is inferred and no resend is attempted.",
            "No old M12 directory or uncertain request was retried, no production SDK/service/state/hook file changed, and no child/provider/editor/GPU/SSH work ran. Whole M12/M14 remains open.",
        ],
    }
    with output.open("x") as out:
        json.dump(report, out, indent=2)
        out.write("\n")
    print(
        json.dumps(
            {
                "evidence": str(output),
                "status": report["status"],
                "native_matches": len(users),
                "sends": 1,
                "all_recorded_identities_gone": report["cleanup"][
                    "all_recorded_kernel_identities_gone"
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
