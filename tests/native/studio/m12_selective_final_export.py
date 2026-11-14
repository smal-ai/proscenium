"""Allowlisted final admission-recovery evidence, not native private payloads."""

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

    browser = read("browser-result-private.json")
    supervisor = read("supervisor-private.json")
    plan = read("plan-private.json")
    reservation = read("send-reservation.json")
    audits = [read(f"host-{number}-audit.json") for number in (1, 2)]
    stages = {
        path.stem: json.loads(path.read_text())
        for path in sorted((root / "checkpoints").glob("*.json"))
    }
    native = []
    path = (
        root / "d/copilot-runtime/home/session-state" / reservation["session_id"] / "events.jsonl"
    )
    for line in path.open():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if value.get("type") != "user.message":
            continue
        data = value.get("data", {})
        if data.get("clientTurnId") == plan["request_id"]:
            native.append(
                {
                    "event_id": value["id"],
                    "native_message_id": data.get("messageId"),
                    "client_turn_id": data["clientTurnId"],
                    "timestamp": value.get("timestamp"),
                    "safe_content_matches": data.get("content") == plan["prompt"],
                }
            )
    with closing(
        sqlite3.connect((root / "p/.proscenium/state.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as db:
        row = db.execute(
            "SELECT body FROM records WHERE namespace='work' AND id=?",
            (browser["final"]["request_receipt"]["work_id"],),
        ).fetchone()
        work_state = json.loads(row[0])["state"] if row else None
    output_value = {
        "format": "proscenium-m12-final-accepted-ack-evidence-1",
        "status": browser["status"],
        "fault_type": "injected_selective_rpc_response_loss",
        "m12_complete": False,
        "m14_complete": False,
        "model_or_work_completion_claimed": False,
        "base": "03dbad1",
        "executed_commit": "41a08ba",
        "export_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "private_disk_root": str(root),
        "project_id": browser["project_id"],
        "native_session_id": reservation["session_id"],
        "original_request_id": plan["request_id"],
        "send_reservations": reservation["count"],
        "safe_public_input": plan["prompt"],
        "actual_owner_api": browser["api"],
        "quiet_initial": browser["quiet_initial"],
        "actual_cut": browser["cut"],
        "request_before_restart": browser["before_restart"]["request_receipt"],
        "quiet_restart": browser["quiet_restart"],
        "explicit_owner_reconcile": browser["reconcile"],
        "final_request_receipt": browser["final"]["request_receipt"],
        "final_work_state": work_state,
        "execution_gate_remains_closed": not browser["final"]["gate_open"],
        "joined_original": browser["joined_original"],
        "matching_native_user_messages": native,
        "identity_was_already_projected_from_forwarded_events_before_cut": browser["cut"][
            "application_receipt_before_pipe_close"
        ]["native_message_id"]
        == browser["cut"]["native_user_message_id"],
        "durable_checkpoints": stages,
        "checkpoint_file_hashes": {
            str(path.relative_to(root)): sha(path)
            for path in sorted((root / "checkpoints").glob("*.json"))
        },
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
        "host_audits": [
            {
                "generation": a["generation"],
                "options": a["options"],
                "session_ids": a["session_ids"],
                "continue_pending_work": a.get("continue_pending_work"),
                "native_processes": a["native_processes"],
                "native_identities_gone": a["native_identities_gone"],
                "provider_http_attempts": len(a["forbidden"]),
            }
            for a in audits
        ],
        "jobs": browser["final"]["jobs"],
        "tool_invocations": browser["final"]["invocations"],
        "cleanup": {
            "supervisor": supervisor["supervisor"],
            "controller": supervisor["controller"],
            "controller_exit_code": supervisor["node_exit_code"],
            "recorded_processes": supervisor["recorded_process_cleanup"],
            "reaped_adopted_children": supervisor["reaped_adopted_children"],
            "remaining_children": supervisor["remaining_children"],
            "signals_to_owned_children": supervisor["signals"],
            "browser_context_close_awaited": browser["browser_close_awaited"],
            "all_recorded_kernel_identities_gone": all(
                p["original_kernel_identity_gone"] for p in supervisor["recorded_process_cleanup"]
            ),
        },
        "source_hashes_at_export": {
            str(path.relative_to(REPO)): sha(path)
            for path in sorted((REPO / "tests/native/studio").glob("m12_selective*.*"))
        },
        "accepted_fixture_sources_used_read_only": {
            str(path.relative_to(REPO)): sha(path)
            for path in (
                REPO / "tests/native/studio/m12_ack_host.py",
                REPO / "tests/native/studio/m12_ack_prepare.py",
                REPO / "tests/native/studio/m12_ack_run.py",
            )
        },
        "limits": [
            "This is injected selective loss of one actual parsed RPC response, not spontaneous network loss. All non-target traffic/genuine hook replies flowed unchanged.",
            "Native identity was already projected from a forwarded user event before the cut. The test does not claim that the ACK was the only possible identity source.",
            "Final request acknowledgement is admission recovery, not proof of completed model/tool/work execution. No continue_work or second send was issued.",
            "No previous attempt/root/uncertain request was modified or retried. No production SDK/service/state/hook file changed; no auth traffic or private native content was exported.",
            "No child agents, tools, local providers, editor/native authoring, GPU or SSH operation. Remaining M12/M14 criteria stay separate.",
        ],
    }
    assert output_value["status"] == "selective_accepted_ack_recovery_pass"
    assert (
        len(native) == 1
        and native[0]["native_message_id"] == browser["cut"]["native_user_message_id"]
    )
    with output.open("x") as stream:
        json.dump(output_value, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "evidence": str(output),
                "status": output_value["status"],
                "sdk_sends": 1,
                "native_matches": len(native),
                "work_state": work_state,
            }
        )
    )


if __name__ == "__main__":
    main()
