"""Private before-snapshot and terminal-state inspection. Sends nothing."""

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

OLD = Path.home() / ".cache/proscenium-development/milestones/m05-20261114T165850"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    proof = Path(sys.argv[1]).resolve()
    if proof.exists():
        assert not any(proof.iterdir()), "Existing preparation evidence must not be overwritten"
    else:
        proof.mkdir(mode=0o700, parents=True, exist_ok=False)
    project = OLD / "project"
    connection = sqlite3.connect(f"file:{project}/.proscenium/state.sqlite3?mode=ro", uri=True)
    requests = [
        json.loads(row[0])
        for row in connection.execute("SELECT body FROM records WHERE namespace='requests'")
    ]
    invocations = [
        json.loads(row[0])
        for row in connection.execute("SELECT body FROM records WHERE namespace='invocations'")
    ]
    assert len(requests) == 3
    assert all(row["receipt"]["state"] == "completed" for row in requests)
    assert all(row["state"] == "completed" for row in invocations)
    before = {
        "requests": requests,
        "invocations": invocations,
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip(),
        "index_sha256": sha(project / ".git/index") if (project / ".git/index").exists() else None,
        "status": subprocess.check_output(
            ["git", "--no-optional-locks", "status", "--porcelain=v1"], cwd=project, text=True
        ),
        "source_files": {
            str(p.relative_to(project)): sha(p)
            for directory in ("assets", "scenes", "research", "reports", "tools")
            for p in (project / directory).rglob("*")
            if p.is_file()
        },
        "old_evidence": {str(p.relative_to(OLD)): sha(p) for p in OLD.glob("public-*.json")},
    }
    (proof / "before.json").write_text(json.dumps(before, indent=2))
    snapshot = proof / "private-before"
    snapshot.mkdir(mode=0o700)
    for source, target in (
        (project / ".proscenium/state.sqlite3", snapshot / "project.sqlite3"),
        (OLD / "machine/registry.sqlite3", snapshot / "registry.sqlite3"),
    ):
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original:
            with sqlite3.connect(target) as copy:
                original.backup(copy)
    shutil.copytree(project / ".git", snapshot / "git")
    for directory in ("assets", "scenes", "research", "reports", "tools"):
        shutil.copytree(project / directory, snapshot / directory)
    shutil.copytree(
        OLD / "machine/copilot-runtime/home/session-state", snapshot / "native-session-state"
    )
    config = json.loads((OLD / "config.json").read_text())
    (proof / "original-config.json").write_text(json.dumps(config, indent=2))
    connection.close()
    print(
        json.dumps(
            {
                "proof": str(proof),
                "original_requests": len(requests),
                "all_original_completed": True,
                "head": before["head"],
            }
        )
    )


if __name__ == "__main__":
    main()
