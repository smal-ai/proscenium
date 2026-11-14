"""Reconcile an existing terminal M07 readback. Never launch, resume or signal work."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reconcile(root):
    evidence = REPO / "docs/evidence/m07-review/final"
    manifest_path = evidence / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for name, receipt in manifest.items():
        path = evidence / name
        assert path.stat().st_size == receipt["bytes"] and sha(path) == receipt["sha256"]
    names = ("run.json", "browser-result.json", "server-audit.json", "protected-before.json")
    hashes = {}
    for name in names:
        hashes[name] = sha(root / name)
        assert hashes[name] == sha(evidence / name), "Export differs from durable final receipt"
    run = json.loads((root / "run.json").read_text())
    browser = json.loads((root / "browser-result.json").read_text())
    audit = json.loads((root / "server-audit.json").read_text())
    assert not run.get("pass") and not browser.get("pass") and browser["views"] == []
    assert browser["source"] == run["source"]
    assert audit["forbidden"] == ["subprocess"]
    assert audit["sdk_sends"] == audit["provider_sends"] == 0
    assert audit["gate_closed"] and audit["lifecycle_errors"] == {}
    assert run["original_unchanged"] and run["protected_identities_unchanged"]
    assert run["port_released"] and run["elapsed"] <= 300
    assert min(sample["available"] for sample in run["samples"]) >= 4 * 1024**3
    children = {}
    for name, child in run["children"].items():
        assert child["reaped"]
        current = None
        try:
            fields = Path(f"/proc/{child['pid']}/stat").read_text().rsplit(") ", 1)[1].split()
            current = {"state": fields[0], "start": fields[19]}
        except FileNotFoundError:
            pass
        live_original = bool(
            current
            and current["start"] == child["start"]
            and current["state"] not in {"Z", "X", "x"}
        )
        assert not live_original, "Original owned child unexpectedly remains live"
        children[name] = {**child, "original_identity_live": live_original}
    report = {
        "reconciled_at": datetime.now(UTC).isoformat(),
        "status": "TERMINAL_FAILED_BEFORE_REVIEW",
        "actual_source": run["source"],
        "actual_browser": browser["browser"],
        "actual_views_verified": 0,
        "image_job_shot_context_result": "NOT_VERIFIED",
        "failure": "Proof guard rejected activation's read-only Git validation before child creation",
        "durable_error": browser["error"],
        "elapsed_seconds": run["elapsed"],
        "cleanup": {"children": children, "port_released_at_shutdown": run["port_released"]},
        "preservation": {
            "basis": "existing_terminal_receipt",
            "original_inventory_unchanged": True,
            "protected_project_and_budget_hashes_unchanged": True,
        },
        "sdk_sends": 0,
        "provider_sends": 0,
        "receipt_sha256": hashes,
        "historical_manifest_sha256": sha(manifest_path),
        "historical_manifest_files_verified": len(manifest),
        "continuation_executed": "continuation_sha256" in browser,
        "new_stack_started": False,
        "new_model_or_provider_work": False,
        "parent_integrated_revision_not_exercised_by_this_old_run": "abedc19",
    }
    assert not report["continuation_executed"]
    target = evidence / "reconciliation.json"
    if target.exists():
        prior = json.loads(target.read_text())
        assert prior["receipt_sha256"] == hashes and prior["status"] == report["status"]
        return prior
    with target.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    return report


if __name__ == "__main__":
    result = reconcile(Path(sys.argv[1]))
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "status",
                    "actual_source",
                    "actual_views_verified",
                    "image_job_shot_context_result",
                    "historical_manifest_files_verified",
                    "new_stack_started",
                )
            },
            indent=2,
        )
    )
