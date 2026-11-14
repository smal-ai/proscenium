"""Export only sanitised own receipts, never execute a browser or external service."""

import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(root, browser, suffix=""):
    assert suffix in {"", "failed-stack-a"}
    destination = REPO / "docs/evidence/m07-review/final" / suffix
    destination.mkdir(parents=True, exist_ok=True)
    for name in (
        "run.json",
        "browser-result.json",
        "server-audit.json",
        "preparation.json",
        "import-report.json",
        "protected-before.json",
    ):
        target = destination / name
        assert not target.exists(), "Never overwrite an exported execution receipt"
        shutil.copyfile(root / name, target)
    regression = destination / "browser-regressions"
    regression.mkdir()
    shutil.copyfile(browser / "report.json", regression / "report.json")
    for path in sorted(browser.rglob("*.png")):
        target = regression / path.relative_to(browser)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    captures = destination / "actual-captures"
    captures.mkdir()
    for path in sorted(root.glob("*.png")):
        shutil.copyfile(path, captures / path.name)
    browser_result = json.loads((root / "browser-result.json").read_text())
    run = json.loads((root / "run.json").read_text())
    audit = json.loads((root / "server-audit.json").read_text())
    summary = {
        "actual_readback": "PASS" if browser_result.get("pass") and run.get("pass") else "FAILED",
        "actual_source": run["source"],
        "actual_browser": browser_result["browser"],
        "actual_views_verified": len(browser_result["views"]),
        "elapsed_seconds": run["elapsed"],
        "minimum_available_bytes": min(row["available"] for row in run["samples"]),
        "original_unchanged_since_import": run["original_unchanged"],
        "protected_identities_unchanged": run["protected_identities_unchanged"],
        "guard_rejections": audit["forbidden"],
        "sdk_sends": audit["sdk_sends"],
        "provider_sends": audit["provider_sends"],
        "parent_acceptance": "NOT_CLAIMED",
        "cobalt_blue_instruction": "NOT_ACHIEVED_IN_ORIGINAL_GENERATION",
        "fresh_generation": False,
    }
    (destination / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    manifest = {
        str(path.relative_to(destination)): {"bytes": path.stat().st_size, "sha256": sha(path)}
        for path in sorted(destination.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "")
