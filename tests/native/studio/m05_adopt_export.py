"""Read retained follow-up results, preserve old evidence, export no authentication."""

import hashlib
import importlib.metadata
import json
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OLD = Path.home() / ".cache/proscenium-development/milestones/m05-20261114T165850"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    proof = Path(sys.argv[1]).resolve()
    project = OLD / "project"
    output = REPO / "docs/evidence/m05-adoption"
    output.mkdir(parents=True, exist_ok=True)

    def read(name):
        return json.loads((proof / name).read_text())

    def write(name, value):
        (output / name).write_text(json.dumps(value, indent=2) + "\n")

    before = read("before.json")
    prior = read("public-before-release.json")
    final = read("public-final.json")
    request = read("single-request.json")
    assert request["request"]["receipt"]["state"] == "completed"
    assert request["elapsed_ms"] <= 180000
    assert len(prior["requests"]["items"]) == 3 and len(final["requests"]["items"]) == 4
    assert prior["jobs"] == final["jobs"]
    assert prior["versions"] == final["versions"]
    assert prior["selections"] == final["selections"]
    protected = {
        name: {"before": value, "after": sha(project / name)}
        for name, value in before["source_files"].items()
    }
    assert all(row["before"] == row["after"] for row in protected.values())
    old_private = {name: sha(OLD / name) for name in before["old_evidence"]}
    assert old_private == before["old_evidence"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    index = project / ".git/index"
    assert head == before["head"]
    assert (sha(index) if index.exists() else None) == before["index_sha256"]
    inspection = json.loads((project / "reports/history-adoption-completion.json").read_text())
    assert inspection["status"] == "historical_verification_passed_adoption_blocked"
    assert len(inspection["historical_inspections"]) == 2
    for history in inspection["historical_inspections"]:
        assert history["status"] == "actual_reopen_verified" and not history["saved"]
        assert history["hashes_before"] == history["hashes_after"]
        assert history["units"] == {"system": "METRIC", "scale_length": 1}
        assert {o["ids"]["proscenium_instance_id"] for o in history["instances"]} == {
            "instance-blue",
            "instance-renamed",
        }
        for obj in history["instances"]:
            assert obj["dimensions_m"] == [1, 1, 1]
        texture = history["dependencies"]["textures"][0]
        assert texture["dimensions"] == [8, 8]
        assert (
            texture["sha256"] == "fcf1e3f027e9b1ea14df033cba5b5134a7db305f9fc69239f517abb1be932568"
        )
        for relative, digest in history["hashes_after"].items():
            assert sha(project / history["snapshot_root"] / relative) == digest
    allocation = project / "reports/native-adoption-allocation"
    terminal = json.loads((allocation / "terminal.json").read_text())
    assert terminal["exit_code"] == 1 and terminal["reaped"]
    assert terminal["available_bytes_before"] >= 4 * 1024**3
    assert len(list(allocation.glob("*.claim"))) == 1
    connection = sqlite3.connect(f"file:{project}/.proscenium/state.sqlite3?mode=ro", uri=True)
    invocations = [
        json.loads(row[0])
        for row in connection.execute("SELECT body FROM records WHERE namespace='invocations'")
    ]
    invocations = [
        row for row in invocations if row.get("request_id") == request["request"]["request_id"]
    ]
    assert invocations and all(row["state"] == "completed" for row in invocations)
    current_events = next(
        (OLD / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl")
    )
    old_events = next((proof / "private-before/native-session-state").glob("*/events.jsonl"))
    original = [json.loads(line) for line in old_events.read_text().splitlines()]
    current = [json.loads(line) for line in current_events.read_text().splitlines()]
    original_ids = {row["id"] for row in original}
    delta = [row for row in current if row["id"] not in original_ids]
    users = [row for row in delta if row["type"] == "user.message"]
    assert len(users) == 1
    calls = [
        {
            "native_event_id": row["id"],
            "timestamp": row["timestamp"],
            **{key: row["data"][key] for key in ("toolCallId", "toolName", "arguments")},
        }
        for row in delta
        if row["type"] == "tool.execution_start"
    ]
    counts = Counter(row["toolName"] for row in calls)
    assert counts["get_version"] == 3
    assert not set(counts) & {
        "task",
        "publish_outputs",
        "checkpoint_sources",
        "submit_generation",
        "request_preview",
        "request_vlm_review",
    }
    start = next(row for row in current if row["type"] == "session.start")
    write(
        "verification.json",
        {
            "strict_result": "remaining",
            "historical_scenes_verified": 2,
            "new_model_sends": 1,
            "new_children": 0,
            "elapsed_ms": request["elapsed_ms"],
            "new_native_processes": 1,
            "new_preview_jobs": 0,
            "head_before": before["head"],
            "head_after": head,
            "index_before": before["index_sha256"],
            "index_after": sha(index) if index.exists() else None,
            "protected_files": protected,
            "old_private_reports_preserved": True,
            "all_original_versions_jobs_owner_choices_preserved": True,
            "adoption_saved": False,
            "adoption_published": False,
        },
    )
    write("public-before.json", prior)
    write("public-final.json", final)
    write("native-tool-invocations.json", calls)
    write("host-invocations.json", invocations)
    write(
        "native-messages-redacted.json",
        [
            {
                "native_event_id": row["id"],
                "type": row["type"],
                "timestamp": row["timestamp"],
                "content": row["data"].get("content", ""),
            }
            for row in delta
            if row["type"] in {"user.message", "assistant.message"}
        ],
    )
    for name in (
        "same-current-context.json",
        "reopen-observation.json",
        "release-receipt.json",
        "single-request.json",
        "request-observations.json",
        "cleanup.json",
        "processes.json",
        "old-asset-history.png",
        "current-asset-reclicked.png",
    ):
        shutil.copyfile(proof / name, output / name)
    shutil.copyfile(
        project / "reports/history-adoption-completion.json",
        output / "history-adoption-completion.json",
    )
    shutil.copyfile(
        project / "tools/history_adopt_completion.py",
        output / "model-authored-history_adopt_completion.py.txt",
    )
    shutil.copyfile(allocation / "terminal.json", output / "native-terminal.json")
    shutil.copyfile(allocation / "blender.log", output / "native-blender.log")
    write(
        "identities.json",
        {
            "accepted_base": "bdea12ca46c0be24aa98a4e2a72ef12d02dbf695",
            "private_proof_root": str(proof),
            "retained_project_root": str(project),
            "sdk": importlib.metadata.version("github-copilot-sdk"),
            "cli_version": start["data"]["copilotVersion"],
            "model": start["data"]["selectedModel"],
            "native_session_id": start["data"]["sessionId"],
            "browser": final["browser"],
            "viewport": final["view"]["viewport"],
            "compiled_frontend": {
                str(path.relative_to(REPO)): sha(path)
                for path in (REPO / "apps/frontend/dist").rglob("*")
                if path.is_file()
            },
        },
    )
    write(
        "manifest.json",
        {
            path.name: {"sha256": sha(path), "bytes": path.stat().st_size}
            for path in sorted(output.iterdir())
            if path.is_file() and path.name not in {"manifest.json", "README.md"}
        },
    )
    connection.close()
    print(
        json.dumps(
            {
                "historical_scenes_verified": 2,
                "new_model_sends": 1,
                "new_native_processes": 1,
                "adoption_published": False,
                "result": "remaining",
            }
        )
    )


if __name__ == "__main__":
    main()
