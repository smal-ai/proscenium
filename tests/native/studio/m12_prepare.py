"""Private stopped M01 backup/restore. Never opens an SDK or authoring tool."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.operations.recovery import private_modes, write_json
from tools.operations.safety import digest, private_directory, tree_files

REPO = Path(__file__).resolve().parents[3]
ORIGINAL = Path("/home/quintin/.cache/proscenium-development/milestones/m01-20261114T132348Z")
SESSION = "d939999f-bff2-448c-9d24-028c1dcff527"
PROJECT = "project-90687708-fe6d-465d-8e11-cd881eced35e"


def fingerprint(original: Path = ORIGINAL) -> dict:
    result = {}
    for label, root in (
        ("project", original / "project"),
        ("native", original / "machine/copilot-runtime/home/session-state" / SESSION),
    ):
        result[label] = {
            path.relative_to(root).as_posix(): digest(path) for path in tree_files(root)
        }
    result["registry"] = digest(original / "machine/registry.sqlite3")
    result["head"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=original / "project", text=True
    ).strip()
    return result


def public_fingerprint(value: dict) -> dict:
    return {
        "head": value["head"],
        "index_sha256": value["project"].get(".git/index"),
        "index_present": ".git/index" in value["project"],
        "project_database_sha256": value["project"][".proscenium/state.sqlite3"],
        "registry_database_sha256": value["registry"],
        **{
            label + "_tree_sha256": hashlib.sha256(
                json.dumps(value[label], sort_keys=True).encode()
            ).hexdigest()
            for label in ("project", "native")
        },
        "project_files": len(value["project"]),
        "native_files": len(value["native"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.absolute()
    private_directory(root)
    before = fingerprint()
    write_json(root / "original-before-private.json", before)
    commands = []
    for argv in (
        [
            "backup",
            "--config",
            str(ORIGINAL / "config.json"),
            "--project-id",
            PROJECT,
            "--target",
            str(root / "backup"),
        ],
        ["verify", str(root / "backup")],
        [
            "restore",
            str(root / "backup"),
            "--target",
            str(root / "restored"),
            "--rebind-fresh-registry",
        ],
    ):
        command = [sys.executable, "-m", "tools.operations.recovery", *argv]
        result = subprocess.run(command, cwd=REPO, capture_output=True, timeout=180, text=True)
        commands.append({"command": command, "exit_code": result.returncode})
        write_json(root / f"recovery-step-{len(commands)}.json", commands[-1])
        if result.returncode:
            write_json(
                root / "prepare-failed.json", {"commands": commands, "error": result.stderr.strip()}
            )
            raise RuntimeError("Accepted recovery tool refused. See private failure receipt.")
    after = fingerprint()
    assert before == after, "Original M01 bytes changed"
    write_json(root / "original-after-private.json", after)
    write_json(
        root / "prepared.json",
        {
            "commands": commands,
            "original_unchanged": True,
            "original": public_fingerprint(before),
            "session_id": SESSION,
            "project_id": PROJECT,
            "cloud_sends": 0,
            "backup_files": len(json.loads((root / "backup/manifest.json").read_text())["files"]),
        },
    )
    private_modes(root)
    print(json.dumps({"root": str(root), "original_unchanged": True, "cloud_sends": 0}))


if __name__ == "__main__":
    main()
