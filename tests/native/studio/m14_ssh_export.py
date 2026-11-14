"""Read-only export of the completed allocation, exact source lineage and limits."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from tools.operations.recovery import write_json  # noqa: E402


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git_bytes(revision, path):
    from m14_ssh_sources import MANIFEST, source_bytes

    if (REPO / MANIFEST).exists():
        sources = json.loads((REPO / MANIFEST).read_text())["sources"]
        if revision in sources:
            return source_bytes(revision, path)
    return subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=REPO)


def retain_sources():
    """One read-only historical-source export, no runtime or private data access."""
    directory = REPO / "docs/evidence/m14-ssh"
    sources = {}
    for report_name, original_key, hashes_key, mirror in (
        (
            "result.json",
            "executed_driver_revision",
            "executed_source_hashes",
            "a46b510af2f1a257a59e3b56a6359dac882a532f",
        ),
        (
            "final-result.json",
            "driver_revision",
            "source_hashes",
            "3d3fc64b0c47d64edc01cf67e2e08f77b53eda98",
        ),
    ):
        report = json.loads((directory / report_name).read_text())
        original = report[original_key]
        entry = {
            "original_revision": original,
            "mode": "accepted_git_mirror",
            "accepted_revision": mirror,
            "files": {},
        }
        subprocess.run(["git", "merge-base", "--is-ancestor", mirror, "HEAD"], cwd=REPO, check=True)
        for path, digest in report[hashes_key].items():
            data = subprocess.check_output(["git", "show", f"{mirror}:{path}"], cwd=REPO)
            assert hashlib.sha256(data).hexdigest() == digest
            entry["files"][path] = {"sha256": digest}
        sources[original] = entry
    summary = json.loads((directory / "complete-summary.json").read_text())["source_lineage"]
    for revision, files in (
        (
            summary["launched_driver_host_browser_import_revision"],
            summary["launched_source_hashes"],
        ),
        (summary["pre_send_reviewed_commit"], summary["runtime_recovery_module_hashes"]),
    ):
        entry = {"original_revision": revision, "mode": "retained_exact_source", "files": {}}
        retained = directory / "complete-sources" / revision
        retained.mkdir(parents=True, mode=0o700, exist_ok=False)
        for path, digest in files.items():
            # Export the actual committed public proof source, not a private runtime.
            data = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=REPO)
            assert hashlib.sha256(data).hexdigest() == digest
            target = retained / (Path(path).name + ".txt")
            with target.open("xb") as stream:
                stream.write(data)
            entry["files"][path] = {
                "sha256": digest,
                "retained_path": str(target.relative_to(REPO)),
            }
        sources[revision] = entry
    write_json(
        directory / "complete-source-mirrors.json",
        {
            "format": "proscenium-m14-explicit-evidence-source-mirrors-1",
            "semantics": "Original executed revision facts remain unchanged. Accepted mirrors and retained exact source bytes are separate hash-checked inspection identities.",
            "sources": sources,
        },
    )


def export(root, destination):
    phase = root / "a"
    report = json.loads((phase / "result-private.json").read_text())
    assert report["status"] == "passed" and all(report["checks"].values())
    browser = report["browser"]
    guard = json.loads((root / "sdk-send-reservation.json").read_text())
    budget = json.loads((root / "complete-budget.json").read_text())
    started = json.loads((phase / "allocation-started.json").read_text())
    authorisation = json.loads((phase / "authorised-send").read_text())
    startup = [json.loads(p.read_text()) for p in sorted(root.glob("startup-*.json"))]
    assert len(startup) == 1 and guard["count"] == 1
    assert report["send_reservation"] == guard
    launch = report["driver_revision"]
    loaded = [
        "tests/native/studio/" + name
        for name in (
            "m14_ssh_run.py",
            "m14_ssh_host.py",
            "m14_ssh_browser.mjs",
            "m14_ssh_open.mjs",
            "m14_ssh_oracle.py",
            "m14_ssh_prepare.py",
        )
    ]
    pre_send = authorisation["preflighted_commit"]
    recovery = [
        "tests/native/studio/" + name
        for name in ("m14_ssh_recover_activation.mjs", "m14_ssh_recover_upload.mjs")
    ]
    old_a = root.parent / "s14a"
    old_b = root.parent / "s14b"
    old_a_sha = sha(old_a / "result-private.json")
    old_b_sha = sha(old_b / "result-private.json")
    assert old_a_sha == "87b0da954ccacb2101e16fcebcbbacd00f1b7a7719dbdfddd9ee33d100113dc9"
    assert (
        old_b_sha
        == hashlib.sha256(
            git_bytes("04ad181", "docs/evidence/m14-ssh/final-result.json")
        ).hexdigest()
    )
    summary = {
        "status": "passed_bounded_m14_ssh_message_media_unavailable_review",
        "m12_m14_release_acceptance": "coordinator-owned, not claimed",
        "accepted_product_base": "04ad1810b5abe1334364b5074fe4123a703c4c2f",
        "retained_root": str(root),
        "phase_root": str(phase),
        "raw_report_sha256": sha(phase / "result-private.json"),
        "source_lineage": {
            "launched_driver_host_browser_import_revision": launch,
            "launched_source_hashes": {
                path: hashlib.sha256(git_bytes(launch, path)).hexdigest() for path in loaded
            },
            "pre_send_reviewed_commit": pre_send,
            "runtime_recovery_module_hashes": {
                path: hashlib.sha256(git_bytes(pre_send, path)).hexdigest() for path in recovery
            },
            "raw_report_source_hashes_meaning": "filesystem bytes inspected at shutdown, not a claim that edited browser/import files were reloaded",
            "runtime_correction": "The original living browser loaded only the two explicit recovery modules. Empty configured project directories were restored in the owned import. No stack/model restart.",
            "queue_observer": "The launched browser's original response.json observer succeeded for the actual Queue. Its later on-disk durable-metadata correction was not retroactively called executed.",
        },
        "compiled_ui_assets": {
            str(p.relative_to(REPO)): sha(p)
            for p in sorted((REPO / "apps/frontend/dist").rglob("*"))
            if p.is_file()
        },
        "versions": {
            **report["versions"],
            "sdk": report["host_1"]["sdk"],
            "browser": browser["browser"],
            "model": "gpt-6.1-sol",
        },
        "allocation": {
            "startup_sets": len(startup),
            "maximum_startup_sets": 3,
            "maximum_seconds": 1200,
            "phase_seconds": report["elapsed_seconds"],
            "including_pre_phase_key_setup_seconds": round(
                (started["ns"] - budget["started_ns"]) / 1e9 + report["elapsed_seconds"], 3
            ),
            "available_ram_bytes_before_start": report["available_ram_bytes_before_start"],
            "minimum_ram_gate_bytes": 4 * 1024**3,
            "resampling_waits": 0,
            "sshd": 1,
            "sequential_forwarding_clients": 2,
            "continuous_browser_profiles": 1,
            "sequential_api_hosts": 2,
            "sdk_sends": 1,
            "sdk_send_limit": 1,
            "sdk_turn_limit_seconds": 180,
            "children_authoring_encodes_provider_calls": 0,
            "shared_exclusive_send_guard": guard,
            "completion_after_tunnel_cut_seconds": round(
                (report["completion_disconnected"]["observed_ns"] - report["cut"]["after_cut_ns"])
                / 1e9,
                6,
            ),
            "send_to_observed_completion_seconds": round(
                (report["completion_disconnected"]["observed_ns"] - guard["before_send_ns"]) / 1e9,
                6,
            ),
        },
        "case_failures_retained": browser["observer_failures"],
        "actual_ui_preflight_sha256": sha(phase / "ui-preflight.json"),
        "pre_send_authorisation": authorisation,
        "case_failure_classification": [
            "Fixture import omitted empty brief/storyboards roots. Normal activation rejected the incomplete layout. Same retained owner action succeeded after restoring those empty roots.",
            "Inspector response-cache eviction after a successful UI upload. Durable artefact metadata verified the original upload ID/bytes without repeating the upload.",
        ],
        "criteria": report["checks"],
        "actual_presented_ordinals": browser["presented_ordinals"],
        "frame_pin": browser["annotation"],
        "unavailable_frame_pin": browser["unavailable_annotation"],
        "downloads": browser["downloads"],
        "range": browser["range"],
        "upload": browser["upload_metadata"],
        "ws_replay_sequences_after_reconnect": browser["replay_after"]["sequences"],
        "ws_replay_sequences_after_restart": browser["replay_restart"]["sequences"],
        "native_user_messages": report["native_facts"]["user_messages"],
        "native_assistant_messages": report["native_facts"]["assistant_messages"],
        "unavailable_configuration": browser["configuration"],
        "unavailable_state_semantics": "Configured unavailable missing native executable and unused closed loopback provider ports. Public statuses are declarative, not successful health probes or stopped shared models.",
        "closed_endpoints_observed": report["closed_endpoints_observed"],
        "boundary": report["boundary"],
        "off_origin_page_requests": len(browser["off_origin"]),
        "cleanup": report["cleanup"],
        "process_identities": report["processes"],
        "native_process_identities": report["host_1"]["native_processes"],
        "native_process_identities_gone": report["host_1"]["native_identities_gone"],
        "owned_descendants_remaining": report["owned_descendants_remaining"],
        "lifespan_audits": {
            generation: {
                "sha256": sha(phase / f"host-{generation}-audit.json"),
                "sends": report[f"host_{generation}"]["sends"],
                "forbidden": report[f"host_{generation}"]["forbidden"],
                "gate_closed": report[f"host_{generation}"]["gate_closed"],
            }
            for generation in ("1", "2")
        },
        "original_m13_preserved": report["original_m13_byte_preserved"],
        "original_m13_inventory_files": len(
            json.loads((phase / "original-before.json").read_text())
        ),
        "older_failure_receipts_unchanged": {
            "s14a_private_report_sha256": old_a_sha,
            "s14b_private_report_sha256": old_b_sha,
            "s14a_inventory_sha256": sha(old_a / "original-before.json"),
            "s14b_inventory_sha256": sha(old_b / "original-before.json"),
        },
        "limits": report["limits"],
    }
    mirror = REPO / "docs/evidence/m14-ssh/complete-source-mirrors.json"
    if mirror.exists():
        summary["portable_source_inspection"] = {
            "manifest": str(mirror.relative_to(REPO)),
            "manifest_sha256": sha(mirror),
            "semantics": "Accepted source mirrors and retained exact source bytes are inspection identities only. Original executed revision facts remain unchanged. No fleet-only Git objects are required by the evidence checks.",
        }
    write_json(destination, summary)
    return summary


if __name__ == "__main__":
    result = export(Path(sys.argv[1]), Path(sys.argv[2]))
    print(
        json.dumps(
            {
                "status": result["status"],
                "startup_sets": result["allocation"]["startup_sets"],
                "sends": 1,
            }
        )
    )
