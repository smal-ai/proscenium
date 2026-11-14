"""Explicit allowlist export. No native private content, hook inputs or auth."""

import hashlib
import json
import subprocess
import sys
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

    browser = read("browser-result-private.json")
    supervisor = read("supervisor-private.json")
    reservation = read("send-reservation.json")
    plan = read("plan-private.json")
    audit = [read(f"host-{number}-audit.json") for number in (1, 2)]
    users = []
    history = (
        root / "d/copilot-runtime/home/session-state" / reservation["session_id"] / "events.jsonl"
    )
    for line in history.open():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if value.get("type") != "user.message":
            continue
        data = value.get("data", {})
        if data.get("clientTurnId") != plan["request_id"]:
            continue
        users.append(
            {
                "event_id": value["id"],
                "native_message_id": data.get("messageId"),
                "client_turn_id": data["clientTurnId"],
                "timestamp": value.get("timestamp"),
                "our_safe_content_matches": data.get("content") == plan["prompt"],
            }
        )
    report = {
        "format": "proscenium-m12-accepted-ack-evidence-1",
        "status": "not_proved_one_send_exhausted",
        "m12_complete": False,
        "m14_complete": False,
        "base": "0b8e9ba",
        "executed_commit": "243b34a",
        "export_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "root": str(root),
        "project_id": browser["project_id"],
        "native_session_id": reservation["session_id"],
        "request_id": plan["request_id"],
        "send_reservations": reservation["count"],
        "safe_public_input": plan["prompt"],
        "runtime": {
            "sdk": version("github-copilot-sdk"),
            "sdk_jsonrpc_source_sha256": sha(Path(copilot._jsonrpc.__file__)),
            "cli_package": json.loads(
                (REPO / "node_modules/@github/copilot/package.json").read_text()
            )["version"],
            "direct_cli_sha256": sha(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            "browser": browser["browser_version"],
            "viewport": browser["viewport"],
        },
        "same_continuous_browser_context": True,
        "actual_owner_api": browser["api"],
        "quiet_initial": browser["quiet_initial"],
        "actual_cut_facts": browser["cut"],
        "request_before_restart": browser["before_restart"]["request_receipt"],
        "quiet_restart": browser["quiet_restart"],
        "explicit_reconcile": browser["reconcile"],
        "final_request_receipt": browser["final"]["request_receipt"],
        "final_gate_open": browser["final"]["gate_open"],
        "matching_native_user_messages_after_run": users,
        "accepted_native_user_before_cut": False,
        "send_ack_was_lost": False,
        "original_native_id_join_proved": False,
        "completion_claimed": False,
        "classification": "The actual enqueue ACK reached the application before the selected hook. Callback-aware reader gating did not retract an already-delivered ACK. No matching native user.message was observed before or after cut. This is not accepted-send ACK loss.",
        "host_audits": [
            {
                "generation": a["generation"],
                "session_ids": a["session_ids"],
                "options": a["options"],
                "continue_pending_work": a.get("continue_pending_work"),
                "native_processes": a["native_processes"],
                "native_identities_gone": a["native_identities_gone"],
                "provider_http_attempts": len(a["forbidden"]),
            }
            for a in audit
        ],
        "cleanup": {
            "recorded_processes": supervisor["recorded_process_cleanup"],
            "controller": supervisor["controller"],
            "controller_exit_code": supervisor["node_exit_code"],
            "reaped_adopted_children": supervisor["reaped_adopted_children"],
            "remaining_children": supervisor["remaining_children"],
            "signals_to_recorded_own_children": supervisor["signals"],
            "browser_context_close_awaited": browser["browser_close_awaited"],
            "all_recorded_kernel_identities_gone": all(
                p["original_kernel_identity_gone"] for p in supervisor["recorded_process_cleanup"]
            ),
        },
        "source_hashes": {
            str(p.relative_to(REPO)): sha(p)
            for p in sorted((REPO / "tests/native/studio").glob("m12_ack*.*"))
        },
        "limits": [
            "One actual SDK send only. No second root or replay after the missed boundary.",
            "No accepted native user message ID existed before the cut; the application already had the normal enqueue ACK. Admission/model/tool completion is not inferred.",
            "The original fresh request remains outcome_uncertain and the execution gate is closed. No legacy request or old M12 directory was retried or modified.",
            "A future separately allocated proof would need to drop only the known session.send response before application dispatch while forwarding genuine hook traffic, then close the real pipe after observing native acceptance. That alternative was not run.",
            "No production SDK/service/state/hook implementation was edited, no hook ACK fabricated, no private reasoning/auth exported, and no CLI hash bypass used.",
            "No child agents, local providers, native authoring, GPU work or SSH daemon was used. Full M12/M14 not claimed.",
        ],
    }
    with output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "evidence": str(output),
                "status": report["status"],
                "send_reservations": 1,
                "hook_matches": report["actual_cut_facts"]["hook_matches"],
                "all_recorded_processes_gone": report["cleanup"][
                    "all_recorded_kernel_identities_gone"
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
