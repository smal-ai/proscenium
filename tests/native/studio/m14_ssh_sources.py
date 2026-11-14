"""Portable evidence source identities. Never substitutes the executed revision fact."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = "docs/evidence/m14-ssh/complete-source-mirrors.json"


def source_bytes(executed_revision, path, expected_digest=None, *, root=ROOT):
    manifest = json.loads((root / MANIFEST).read_text())
    entry = manifest["sources"][executed_revision]
    assert entry["original_revision"] == executed_revision
    record = entry["files"][path]
    if expected_digest is not None:
        assert record["sha256"] == expected_digest, "Evidence/mirror hash disagreement"
    if entry["mode"] == "accepted_git_mirror":
        accepted = entry["accepted_revision"]
        # The explicit accepted source mirror must be reachable in this checkout.
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", accepted, "HEAD"], cwd=root, check=True
        )
        data = subprocess.check_output(["git", "show", f"{accepted}:{path}"], cwd=root)
    elif entry["mode"] == "retained_exact_source":
        retained = (root / record["retained_path"]).resolve(strict=True)
        assert retained.is_relative_to((root / "docs/evidence/m14-ssh/complete-sources").resolve())
        data = retained.read_bytes()
    else:
        raise AssertionError("Unknown explicit source mirror mode")
    assert hashlib.sha256(data).hexdigest() == record["sha256"], "Source mirror bytes changed"
    return data
