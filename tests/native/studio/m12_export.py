"""Allowlisted operational evidence only. Never export private native text or auth."""

import hashlib
import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from m12_host import process_identity
from m12_prepare import ORIGINAL, PROJECT, REPO, SESSION, fingerprint, public_fingerprint

sys.path.insert(0, str(REPO))
from tools.operations.recovery import existing_store, write_json
from tools.operations.safety import digest, locks


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main():
    root = Path(sys.argv[1])
    output = Path(sys.argv[2])
    first = root.with_name("m12-native-20261114-a")
    initial = json.loads((first / "original-before-private.json").read_text())
    fixed = json.loads((root / "original-before-private.json").read_text())
    current = fingerprint()
    primary = json.loads((root / "driver-result-private.json").read_text())
    loss = json.loads((root / "loss-finish-result-private.json").read_text())
    plan = json.loads((root / "plan-private.json").read_text())
    ledger = json.loads((root / "sends-private.json").read_text())
    changed = [
        name
        for name in initial["project"]
        if current["project"].get(name) != initial["project"][name]
    ]
    sidecars = [".proscenium/state.sqlite3-shm", ".proscenium/state.sqlite3-wal"]
    preserved = {}
    original_requests = {}
    for label, base in (("original", ORIGINAL), ("restored", root / "restored")):
        with locks([base / "machine/owner.lock", base / "project/.proscenium/owner.lock"]):
            store = existing_store(
                base / "project/.proscenium/state.sqlite3", PROJECT, read_only=True
            )
            with store.transaction(write=False) as tx:
                preserved[label] = {
                    name: canonical_hash(tx.all(name))
                    for name in (
                        "items",
                        "versions",
                        "annotations",
                        "selections",
                        "publications",
                        "artifacts",
                        "retained_sources",
                        "jobs",
                    )
                }
                original_requests[label] = {
                    r["request_id"]: {
                        "submission": r["submission"],
                        "native_message_id": r["receipt"]["native_message_id"],
                    }
                    for r in tx.all("requests")
                    if r["request_id"] not in plan["request_ids"]
                }
                if label == "restored":
                    latest_request = tx.require("requests", plan["request_ids"][1])["receipt"]
    assert fingerprint() == current
    native = root / "restored/machine/copilot-runtime/home/session-state" / SESSION / "events.jsonl"
    user_metadata, wire_files, hook_metadata = [], [], []
    extension_matches = False
    primary_hook_cwd = False
    new_children = 0
    for line in native.open():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        data = value.get("data", {})
        if value.get("type") == "user.message":
            user_metadata.append({name: data.get(name) for name in ("messageId", "clientTurnId")})
            if data.get("clientTurnId") == plan["request_ids"][0]:
                for attachment in data.get("attachments", []):
                    if attachment.get("type") == "file":
                        path = Path(attachment["path"])
                        assert path.is_relative_to(root / "restored/project/.proscenium/artefacts")
                        wire_files.append(
                            {
                                "project_relative_path": str(
                                    path.relative_to(root / "restored/project")
                                ),
                                "sha256": digest(path),
                            }
                        )
                    elif attachment.get("type") == "extension_context":
                        payload = attachment.get("payload", {})
                        extension_matches = (
                            payload.get("annotations") == plan["contexts"][0]["annotations"]
                            and payload.get("context_id") == plan["contexts"][0]["context_id"]
                        )
        if value.get("type") == "hook.start" and data.get("hookType") == "userPromptSubmitted":
            source = data.get("input", {})
            if source.get("prompt") == plan["prompts"][0]:
                primary_hook_cwd = source.get("cwd") == str(root / "restored/project")
            if source.get("prompt") == plan["prompts"][1]:
                hook_metadata.append(
                    {
                        "event_id": value["id"],
                        "hook_type": data["hookType"],
                        "hook_invocation_id": data.get("hookInvocationId"),
                        "matches_second_public_input": True,
                    }
                )
        if value.get("type") == "subagent.started":
            new_children += 1
    audit = [json.loads(path.read_text()) for path in sorted(root.glob("host-*-audit.json"))]
    known_processes = (
        [entry for host in audit for entry in host["native_processes"]]
        + primary["processes"]
        + loss["processes"]
    )
    journal = json.loads((root / "restored/machine/copilot-runtime/process.json").read_text())
    known_processes.append({"pid": journal["pid"], "start": journal["start"]})
    cleanup = [
        {
            "pid": p["pid"],
            "start": p["start"],
            "original_identity_gone": process_identity(p["pid"]) != p["start"],
        }
        for p in known_processes
    ]
    assert all(value["original_identity_gone"] for value in cleanup)
    manifest = json.loads((root / "backup/manifest.json").read_text())
    working_source_matches = []
    for name, metadata in manifest["files"].items():
        if name.startswith("source/working/"):
            path = root / "restored/project" / name.removeprefix("source/working/")
            working_source_matches.append(digest(path) == metadata["sha256"])
    source_files = [
        REPO / "tools/operations/recovery.py",
        *sorted((REPO / "tests/native/studio").glob("m12*.*")),
        *sorted((REPO / "tests/acceptance").glob("m12*.py")),
    ]
    import copilot._jsonrpc as rpc

    report = {
        "format": "proscenium-m12-native-recovery-evidence-1",
        "status": "native_continuity_pass_transport_admission_recovery_unproved",
        "m12_complete": False,
        "project_id": PROJECT,
        "native_session_id": SESSION,
        "source_base": "ad47260",
        "export_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "executed_continuity_commit": "a620049",
        "executed_transport_commit": "6c5999f",
        "runtime": {
            "sdk": version("github-copilot-sdk"),
            "cli_package": json.loads(
                (REPO / "node_modules/@github/copilot/package.json").read_text()
            )["version"],
            "cli_sha256": digest(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            "sdk_jsonrpc_source_sha256": digest(Path(rpc.__file__)),
            "model_requested": "gpt-6.1-sol",
            "browser_viewport": {"width": 1920, "height": 1080, "dpr": 1},
        },
        "backup": {
            "commands": json.loads((root / "prepared.json").read_text())["commands"],
            "files": len(manifest["files"]),
            "manifest_limits": manifest["limits"],
            "restored_native_ids": manifest["native_session_ids"],
            "private_disk_root": str(root),
            "no_private_native_bytes_in_git": True,
        },
        "original_preservation": {
            "initial": public_fingerprint(initial),
            "final": public_fingerprint(current),
            "initial_whole_project_tree_unchanged": initial["project"] == current["project"],
            "initial_deviation_changed_paths": changed,
            "only_initial_auxiliary_sidecars_removed": sorted(changed) == sorted(sidecars)
            and all(name not in current["project"] for name in sidecars),
            "durable_project_bytes_unchanged": all(
                current["project"].get(name) == sha
                for name, sha in initial["project"].items()
                if name not in sidecars
            ),
            "initial_registry_main_unchanged": initial["registry"] == current["registry"],
            "initial_native_history_unchanged": initial["native"] == current["native"],
            "corrected_backup_through_final_all_measured_files_unchanged": fixed == current,
            "registry_auxiliary_files_in_first_baseline": "not separately inventoried",
        },
        "restoration": {
            "namespace_hashes_unchanged": {
                name: preserved["original"][name] == preserved["restored"][name]
                for name in preserved["original"]
            },
            "original_request_submissions_and_native_ids_unchanged": original_requests["original"]
            == original_requests["restored"],
            "backed_up_working_source_bytes_unchanged": all(working_source_matches),
            "media_http_checks": primary["media"],
            "actual_sdk_image_files": wire_files,
            "actual_sdk_annotation_extension_matches_captured_context": extension_matches,
        },
        "native_continuity": {
            "old_public_message_ids_restored": primary["old_public_ids_restored"],
            "held_restart": primary["held_restart"],
            "explicit_release": primary["release"],
            "request_receipt": primary["continuation_receipt"],
            "checks": primary["continuity"],
            "native_prompt_hook_cwd_matches_restored_root": primary_hook_cwd,
        },
        "native_resume_options": [options for host in audit for options in host["resumes"]],
        "cloud_send_reservations": ledger,
        "total_new_sdk_send_calls": len(ledger),
        "new_native_user_messages_confirmed": sum(
            v["clientTurnId"] in plan["request_ids"] for v in user_metadata
        ),
        "native_public_user_metadata": user_metadata,
        "children_observed": new_children,
        "fresh_session_attempts": sum(host["create_session_attempts"] for host in audit),
        "provider_http_attempts": sum(len(host["forbidden_effects"]) for host in audit),
        "transport_loss": {
            "physical_cut": loss.get("cut"),
            "quiet_restart": loss.get("quiet_restart"),
            "reconcile": loss.get("reconcile"),
            "original_request_receipt": latest_request,
            "native_match_count": sum(
                v["clientTurnId"] == plan["request_ids"][1] for v in user_metadata
            ),
            "matching_user_prompt_hooks": hook_metadata,
            "accepted_native_admission_ack_loss_proved": False,
            "request_reposted": False,
            "sdk_send_retried": False,
            "reason": "Actual stdout loss interrupted a pending session.send, but the registered userPromptSubmitted hook required the paused reader before native user.message admission. No message ID/history match exists. Outcome stays uncertain; no retry.",
        },
        "cleanup": {
            "server_and_sdk_identities": cleanup,
            "browser_context_closes_awaited": True,
            "browser_pid_start_receipts": "not captured; do not claim independent OS-PID verification of browser children",
            "independent_blender_9876": "not accessed or signalled",
        },
        "source_hashes_at_export": {
            str(path.relative_to(REPO)): digest(path) for path in source_files
        },
        "limits": [
            "Original main DB/Git/media/native bytes stayed intact, but the first old-tool backup removed two transient project SQLite sidecars. That first attempt is not called read-only.",
            "The corrected Linux unix-none/EXCLUSIVE reader requires the external registry/project locks and fully stopped external writers. No immutable SQLite mode or raw database copy is used.",
            "The genuine transport disconnect was pre-admission/hook-blocked, not proven accepted-send ACK loss. Original uncertain request is not resubmitted and the two-send allocation is exhausted.",
            "A late fixture get_events after an uncertain Resume raced SDK shutdown and returned JsonRpcError -32000. No native admission join was inferred from it.",
            "Browser contexts were closed by Playwright, but browser PID/start receipts were not persisted.",
            "No provider/render/Unity restart, pending-decision/child recovery or whole M12 acceptance is claimed. Those require separate allocations and accepted milestone inputs.",
        ],
    }
    write_json(output, report)
    print(
        json.dumps(
            {
                "evidence": str(output),
                "native_continuity": all(primary["continuity"].values()),
                "sdk_sends": len(ledger),
                "native_users_new": report["new_native_user_messages_confirmed"],
                "accepted_send_ack_loss_proved": False,
                "original_durable_bytes_unchanged": report["original_preservation"][
                    "durable_project_bytes_unchanged"
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
