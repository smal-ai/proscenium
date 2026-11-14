"""Export allowlisted real M05 effects and retained limitations. No execution/replay."""

import hashlib
import importlib.metadata
import json
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

from proscenium.contracts import canonical, validate

REPO = Path(__file__).resolve().parents[3]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    root = Path(sys.argv[1]).resolve()
    project = root / "project"
    output = REPO / "docs/evidence/m05"
    output.mkdir(parents=True, exist_ok=True)

    def read(name):
        return json.loads((root / name).read_text())

    def write(name, value):
        (output / name).write_text(json.dumps(value, indent=2) + "\n")

    seed = read("public-seed.json")
    before = read("public-before-adoption.json")
    final = read("public-final.json")
    versions = {v["version_id"]: v for page in final["versions"].values() for v in page["items"]}
    assert set(versions) == {
        "asset-cube-v1",
        "asset-cube-v2",
        "asset-cube-v3",
        "asset-companion-v1",
        "scene-main-v1",
        "scene-alternate-v1",
    }
    for item in ("scene-main", "scene-alternate", "asset-companion"):
        assert seed["versions"][item] == before["versions"][item] == final["versions"][item]
    assert len(final["requests"]["items"]) == 3
    assert all(r["receipt"]["state"] == "completed" for r in final["requests"]["items"])
    file_checks = {}
    for version_id, version in versions.items():
        validate("VersionRecord", version)
        closure = version["source"]
        assert (
            sha(canonical({k: v for k, v in closure.items() if k != "digest"})) == closure["digest"]
        )
        for entry in closure["files"]:
            origin = entry["origin"]
            content = subprocess.check_output(
                ["git", "show", f"{origin['commit']}:{origin['path']}"], cwd=project
            )
            if content.startswith(b"version https://git-lfs.github.com/spec/v1"):
                oid = next(
                    line.split(b"sha256:")[1].decode()
                    for line in content.splitlines()
                    if line.startswith(b"oid ")
                )
                content = (project / ".git/lfs/objects" / oid[:2] / oid[2:4] / oid).read_bytes()
            assert sha(content) == origin["sha256"]
            file_checks[f"{version_id}:{entry['destination']}"] = {
                "sha256": sha(content),
                "bytes": len(content),
                "commit": origin["commit"],
            }
    fixture = read("receipts/seed.json")
    assert fixture["status"] == "completed"
    original_root = Path("/tmp/opencode/proscenium-f01-20261114-a")
    original_checks = []
    for row in fixture["result"]["copied"]:
        actual = sha((original_root / "historical" / row["path"]).read_bytes())
        assert actual == row["source_sha256"] == row["sha256"]
        original_checks.append(
            {"path": row["path"], "original_sha256_after": actual, "unchanged": True}
        )
    ledger = json.loads((project / "research/box-ledger.json").read_text())
    assert ledger["download_bytes"] <= 5 * 1024**2
    for row in ledger["downloads"]:
        data = (project / row["path"]).read_bytes()
        assert sha(data) == row["sha256"] and len(data) == row["bytes"]
    jobs = final["jobs"]["items"]
    assert len(jobs) == 3 and Counter(j["state"] for j in jobs) == {"failed": 2, "succeeded": 1}
    assert Counter(j["render_spec"]["purpose"] for j in jobs) == {"keyframe": 2, "proxy": 1}
    for job in jobs:
        validate("JobRecord", job)
        spec = job["render_spec"]
        assert job["attempt_number"] == 1 and spec["engine"] == "CYCLES" and spec["device"] == "CPU"
        assert spec["samples"] == 8 and spec["dimensions"] == {"width": 640, "height": 360}
    successful = next(j for j in jobs if j["state"] == "succeeded")
    assert successful["target"]["version"]["version_id"] == "asset-cube-v3"
    frame = final["frames"][successful["job_id"]]["items"][0]
    artifact = final["artifacts"][frame["artifact_id"]]
    image = (project / ".proscenium/artefacts/sha256" / artifact["sha256"] / "content").read_bytes()
    assert sha(image) == artifact["sha256"] == frame["sha256"]
    (output / "keyframe-v3.png").write_bytes(image)
    connection = sqlite3.connect(f"file:{project}/.proscenium/state.sqlite3?mode=ro", uri=True)

    def rows(namespace):
        return [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT body FROM records WHERE namespace=?", (namespace,)
            )
        ]

    publications = rows("publications")
    assert len(publications) == 3  # Labelled setup plus the two real native asset publications.
    checkpoints = [
        {k: row[k] for k in ("state", "parent", "receipt", "files")}
        for row in rows("checkpoint_intents")
    ]
    assert len(checkpoints) == 4  # Includes final honest failure-evidence checkpoint, not adoption.
    native_path = next((root / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"))
    native = [json.loads(line) for line in native_path.read_text().splitlines()]
    start = next(e for e in native if e["type"] == "session.start")
    assert start["data"]["selectedModel"] == "gpt-6.1-sol"
    users = [e for e in native if e["type"] == "user.message"]
    assert len(users) == 3
    calls = [
        {
            "native_event_id": e["id"],
            "timestamp": e["timestamp"],
            **{k: e["data"][k] for k in ("toolCallId", "toolName", "arguments")},
        }
        for e in native
        if e["type"] == "tool.execution_start"
    ]
    counts = Counter(call["toolName"] for call in calls)
    assert counts["list_items"] == 3 and counts["get_version"] >= 3
    assert counts["publish_outputs"] == 2 and counts["checkpoint_sources"] == 3
    assert not set(counts) & {
        "task",
        "submit_generation",
        "request_vlm_review",
        "run_unity_workflow",
    }
    durations = [read(f"request-{n}.json")["elapsed_ms"] for n in (1, 2, 3)]
    assert all(ms <= 180000 for ms in durations)
    authoring = [
        json.loads(p.read_text()) for p in (project / "reports/native-allocation").glob("*.json")
    ]
    assert len(authoring) == 3 and all(row["reaped"] for row in authoring)
    assert all(row["available_bytes_before"] >= 4 * 1024**3 for row in authoring)
    write("public-final.json", final)
    write("before-adoption.json", before)
    write("labelled-setup.json", fixture)
    write("publications.json", publications)
    write("checkpoints.json", checkpoints)
    write("native-tool-invocations.json", calls)
    write("host-invocations.json", [row for row in rows("invocations") if "native_id" in row])
    write(
        "native-messages-redacted.json",
        [
            {
                "native_event_id": e["id"],
                "type": e["type"],
                "timestamp": e["timestamp"],
                "content": e["data"].get("content", ""),
            }
            for e in native
            if e["type"] in {"user.message", "assistant.message"}
        ],
    )
    write("native-authoring-processes.json", authoring)
    for row in authoring:
        shutil.copyfile(
            project / "reports/native-allocation" / f"{row['name']}.log",
            output / f"native-{row['name']}.log",
        )
    write(
        "source-verification.json",
        {
            "files": file_checks,
            "original_f01": original_checks,
            "both_scene_versions_and_dependencies_unchanged": True,
            "companion_unchanged": True,
            "native_roots": 3,
            "native_children": 0,
            "root_durations_ms": durations,
            "job_states": dict(Counter(j["state"] for j in jobs)),
            "actual_completed_keyframes": 1,
            "successful_proxies": 0,
        },
    )
    worker_records = []
    for task in (project / ".proscenium/jobs/previews").glob("*/*/task.json"):
        worker_records.append(
            {
                "task": json.loads(task.read_text()),
                **{
                    name.removesuffix(".json"): json.loads((task.parent / name).read_text())
                    for name in (
                        "process.json",
                        "native-process.json",
                        "terminal.json",
                        "native-error.json",
                    )
                    if (task.parent / name).exists()
                },
                "blender_log": (task.parent / "blender.log").read_text(),
            }
        )
    write("workers.json", worker_records)
    write(
        "known-ui-context-defect.json",
        {
            "status": "remaining",
            "reproduction": "Click the already-current asset-cube-v3 catalogue version a second time. The central image remains visible but onSelect clears parent target and the reviewer does not emit it again. Navigating to the companion version and back recovers context.",
            "observed": read("public-same-version-context-defect.json")["view"],
        },
    )
    for source, target in [
        ("research/box-ledger.json", "acquisition-ledger.json"),
        ("research/box-metadata.json", "khronos-metadata.json"),
        ("research/box-README.md", "khronos-README.md.txt"),
        ("research/Box.glb", "Box.glb"),
        ("reports/import-inspection.json", "import-inspection.json"),
        ("reports/revision-inspection.json", "revision-inspection.json"),
        ("reports/history-adoption.json", "history-adoption.json"),
    ]:
        shutil.copyfile(project / source, output / target)
    for name in ("acquire_box.py", "import_box.py", "revise_box.py", "history_adopt.py"):
        shutil.copyfile(project / "tools" / name, output / (name + ".txt"))
    for name in (
        "ui-local-search.json",
        "ui-known-provenance.json",
        "reopen-observation.json",
        "cleanup.json",
        "ui-search-null-provenance.png",
        "ui-known-provenance.png",
        "asset-v3-real-render.png",
        "final-asset-review.png",
        "retained-v1-history.png",
    ):
        shutil.copyfile(root / name, output / name)
    write(
        "identities.json",
        {
            "accepted_base": "a2a2097725bb18bb95b64f1a7ab82b63e0fb301e",
            "private_root": str(root),
            "sdk": importlib.metadata.version("github-copilot-sdk"),
            "cli_version": start["data"]["copilotVersion"],
            "cli_sha256": sha(
                (REPO / "node_modules/@github/copilot-linux-x64/copilot").read_bytes()
            ),
            "model": start["data"]["selectedModel"],
            "session_id": start["data"]["sessionId"],
            "browser": final["browser"],
            "viewport": final["view"]["viewport"],
            "compiled_frontend": {
                str(p.relative_to(REPO)): sha(p.read_bytes())
                for p in (REPO / "apps/frontend/dist").rglob("*")
                if p.is_file()
            },
        },
    )
    write(
        "manifest.json",
        {
            p.name: {"sha256": sha(p.read_bytes()), "bytes": p.stat().st_size}
            for p in sorted(output.iterdir())
            if p.is_file() and p.name not in {"README.md", "manifest.json"}
        },
    )
    connection.close()
    print(
        json.dumps(
            {
                "result": "remaining: final history/adoption process failed; no adoption published",
                "native_roots": len(users),
                "keyframes_complete": 1,
                "proxy_complete": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
