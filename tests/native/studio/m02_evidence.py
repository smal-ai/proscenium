"""Verify/export retained REAL M02 effects. No services, SDK or native process starts."""

import hashlib
import importlib.metadata
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import rfc8785

root = Path(sys.argv[1])
repo = Path(__file__).resolve().parents[3]
output = repo / "docs/evidence/m02"
output.mkdir(parents=True, exist_ok=True)


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(database, namespace):
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT body FROM records WHERE namespace=? ORDER BY id", (namespace,)
            )
        ]


receipts = {p.stem: load(p) for p in (root / "receipts").glob("*.json")}
assert receipts["01-seed"]["status"] == "failed"  # Known pre-effect schema rejection retained.
assert all(v["status"] == "completed" for k, v in receipts.items() if k != "01-seed")
seed, advance, finish = (receipts[k]["result"] for k in ("02-seed", "03-advance", "04-finish"))
assert len(seed["receipt"]["versions"]) == 4 and len(advance["receipt"]["versions"]) == 2
assert "asset-delayed" not in {v["item_id"] for v in seed["receipt"]["versions"]}
assert "asset-delayed" in {v["item_id"] for v in finish["receipt"]["versions"]}
inspection = receipts["06-old-open"]["result"]
assert inspection["report"]["native_exit_code"] == 0 and not inspection["report"]["missing"]
assert inspection["report"]["object_dimensions"]["Blue cube"] == [1, 1, 1]
assert inspection["report"]["object_dimensions"]["Renamed cube"] == [1, 1, 1]
assert inspection["report"]["dependencies"] == ["assets/cube/asset.blend", "assets/cube/colour.png"]
assert (
    len(inspection["processes"]) == 1
    and not Path(f"/proc/{inspection['processes'][0]['pid']}").exists()
)
assert inspection["before"] == inspection["after"] == receipts["05-dirty"]["result"]
assert receipts["07-after-browse"]["result"] == receipts["05-dirty"]["result"]
assert receipts["10-final-source-state"]["result"] == receipts["09-mtime-trap"]["result"]["state"]
assert receipts["11-diagnose-old-event"]["result"]["reducer_error"] == "scope_denied"
assert receipts["08-explicit-cancel"]["result"]["job"]["state"] == "cancelled"
assert load(root / "cancel.json")["status"] == 202
database_records = {}
for label in ("a", "b"):
    database = root / f"project-{label}/.proscenium/state.sqlite3"
    data = {
        n: records(database, n)
        for n in (
            "items",
            "versions",
            "artifacts",
            "jobs",
            "frames",
            "requests",
            "contexts",
            "work",
            "invocations",
            "copilot.session",
            "copilot.resumes",
            "copilot.sends",
        )
    }
    database_records[label] = data
    assert len(data["requests"]) == len(data["copilot.session"]) == len(data["copilot.sends"]) == 1
    assert data["requests"][0]["receipt"]["state"] == "completed"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        events = [
            json.loads(row[0])
            for row in connection.execute("SELECT body FROM events ORDER BY sequence")
        ]
    data["events"] = [
        e
        for e in events
        if e["event_type"]
        in {
            "publication.created",
            "request.accepted",
            "request.state_changed",
            "work.state_changed",
            "assistant.completed",
            "job.state_changed",
            "project.state_changed",
        }
    ]
    for version in data["versions"]:
        source = version["source"]
        assert (
            hashlib.sha256(
                rfc8785.dumps({k: v for k, v in source.items() if k != "digest"})
            ).hexdigest()
            == source["digest"]
        )
        for file in source["files"]:
            origin = file["origin"]
            content = subprocess.check_output(
                ["git", "show", f"{origin['commit']}:{origin['path']}"],
                cwd=root / f"project-{label}",
            )
            if content.startswith(b"version https://git-lfs.github.com/spec/v1"):
                oid = next(
                    line.split(b"sha256:")[1].decode()
                    for line in content.splitlines()
                    if line.startswith(b"oid ")
                )
                content = (
                    root / f"project-{label}/.git/lfs/objects" / oid[:2] / oid[2:4] / oid
                ).read_bytes()
            assert hashlib.sha256(content).hexdigest() == origin["sha256"]
    for artifact in data["artifacts"]:
        path = (
            root / f"project-{label}/.proscenium/artefacts/sha256" / artifact["sha256"] / "content"
        )
        assert sha(path) == artifact["sha256"] and path.stat().st_size == artifact["bytes"]
assert len(database_records["a"]["items"]) == 5 and len(database_records["a"]["versions"]) == 9
assert not database_records["b"]["items"] and not database_records["b"]["jobs"]
assert not database_records["a"]["frames"]
assert (
    len(database_records["a"]["jobs"]) == 1
    and database_records["a"]["jobs"][0]["state"] == "cancelled"
)
assert not list((root / "project-a/.proscenium/jobs").rglob("launch.claim"))
ids = [database_records[label]["copilot.session"][0]["session_id"] for label in ("a", "b")]
assert len(set(ids)) == 2
native = []
for path in sorted((root / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl")):
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert sum(e["type"] == "user.message" for e in events) == 1
    assert not any(e["type"].startswith("subagent.") for e in events)
    start = next(e for e in events if e["type"] == "session.start")
    assert start["data"]["selectedModel"] == "gpt-6.1-sol"
    user = next(e for e in events if e["type"] == "user.message")
    end = max(e["timestamp"] for e in events if e["type"] == "assistant.turn_end")
    seconds = (
        datetime.fromisoformat(end) - datetime.fromisoformat(user["timestamp"])
    ).total_seconds()
    assert 0 < seconds <= 120
    # No raw SDK messages, private reasoning, tool results or system content.
    safe = [
        {
            "id": e["id"],
            "type": e["type"],
            "timestamp": e["timestamp"],
            "data": {
                k: v
                for k, v in e["data"].items()
                if k
                in {
                    "sessionId",
                    "selectedModel",
                    "copilotVersion",
                    "messageId",
                    "clientTurnId",
                    "toolName",
                    "toolCallId",
                    "model",
                    "success",
                }
            },
        }
        for e in events
        if e["type"]
        in {
            "session.start",
            "session.shutdown",
            "user.message",
            "tool.execution_start",
            "tool.execution_complete",
            "assistant.turn_end",
        }
    ]
    native.append(
        {
            "session_id": path.parent.name,
            "events": safe,
            "cloud_execution_seconds": seconds,
            "history_sha256": sha(path),
        }
    )
assert {n["session_id"] for n in native} == set(ids)
for path in root.glob("browser-*.json"):
    value = load(path)
    assert value["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    assert sum(p["state"] != "inactive" for p in value["projects"]["items"]) == 1
    for p in value["projects"]["items"]:
        if p["native_session_id"]:
            assert p["native_session_id"] == ids[0 if p["name"].endswith(" A") else 1]
    shutil.copyfile(path, output / path.name)
pinned = load(root / "browser-pinned-after-advance.json")
assert "scene-main-v1" in pinned["visible"]["source"]
assert (
    next(i for i in pinned["items"]["items"] if i["item_id"] == "scene-main")["current_version_id"]
    == "scene-main-v2"
)
assert len(load(root / "metadata-scene-main.json")["items"]) == 2
assert load(root / "m01-before.json") == load(root / "m01-after.json")
assert load(root / "cleanup.json")["m01_unchanged"]
assert load(root / "browser-post-cancel.json")["jobs"]["items"][0]["state"] == "cancel_requested"
for name in (
    "browser-pinned-after-advance.png",
    "browser-scene-main-v2.png",
    "browser-reopened-a.png",
    "browser-reopened-b.png",
    "metadata-scene-main.png",
    "browser-asset-cube-v2.png",
):
    shutil.copyfile(root / name, output / name)
for name in (
    "m01-before.json",
    "m01-after.json",
    "cleanup.json",
    "processes.json",
    "cancel.json",
    "metadata-scene-main.json",
    "metadata-scene-main-v1.json",
):
    shutil.copyfile(root / name, output / name)
(output / "fixture-receipts.json").write_text(json.dumps(receipts, indent=2))
(output / "production-records.json").write_text(json.dumps(database_records, indent=2))
(output / "native-redacted.json").write_text(json.dumps(native, indent=2))
paths = subprocess.check_output(
    ["git", "ls-files", "apps/backend/src", "apps/frontend/src", "uv.lock", "package-lock.json"],
    cwd=repo,
    text=True,
).splitlines()
identity = {
    "test_base": "4e8b2337e09dc8416fdc833532f20db473c873a7",
    "m01_dependency": subprocess.check_output(
        ["git", "rev-parse", "milestone/m01"], cwd=repo, text=True
    ).strip(),
    "sdk_version": importlib.metadata.version("github-copilot-sdk"),
    "cli_version": "1.0.112",
    "model": "gpt-6.1-sol",
    "browser": "chromium-1255 / 159.0.8154.6",
    "source_sha256": {p: sha(repo / p) for p in paths},
    "compiled_sha256": {
        str(p.relative_to(repo)): sha(p)
        for p in (repo / "apps/frontend/dist").rglob("*")
        if p.is_file()
    },
    "fixture": load(Path("/tmp/opencode/proscenium-f01-20261114-a/fixture-evidence.json")),
    "root": str(root),
}
if (output / "identities.json").exists():
    # Rechecking retained evidence on a later driver revision must not relabel
    # that revision or a rebuilt bundle as the original native execution.
    original_identity = load(output / "identities.json")
    assert original_identity["test_base"] == identity["test_base"]
    identity = original_identity
(output / "identities.json").write_text(json.dumps(identity, indent=2))
runtime_process = load(root / "machine/copilot-runtime/process.json")
assert not Path(f"/proc/{runtime_process['pid']}").exists()
runtime_process["binary_sha256"] = sha(Path(runtime_process["binary"]))
assert (
    runtime_process["binary_sha256"]
    == "463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69"
)
(output / "runtime-process.json").write_text(json.dumps(runtime_process, indent=2))
summary = {
    "strict_result": "FAIL",
    "blocked_by": [
        "Old native session housekeeping/disconnect event fences newly active project.",
        "Closed-gate lifecycle does not dispatch accepted owner cancellation without trusted intervention.",
    ],
    "successful_recovered_observations": {
        "native_sessions": ids,
        "cloud_roots": 2,
        "native_children": 0,
        "background_inspections": 1,
        "rendered_frames": 0,
        "items": 5,
        "versions": 9,
        "source_browsing_unchanged": True,
        "m01_unchanged": True,
    },
    "notes": [
        "Full source/version provenance inspected using actual authenticated browser listItemVersions JSON; this is not a claim that the catalogue UI exposes all metadata.",
        "Imported M01 PNGs are reference artefacts, never receipts for the unrendered M02 sources.",
        "SDK effects and scripted source-import work are explicitly separate.",
    ],
}
(output / "verification.json").write_text(json.dumps(summary, indent=2))
manifest = {
    p.name: {"sha256": sha(p), "bytes": p.stat().st_size}
    for p in output.iterdir()
    if p.is_file() and p.name not in {"manifest.json", "README.md", "parent-proposal.md"}
}
(output / "manifest.json").write_text(json.dumps(manifest, indent=2))
print(json.dumps(summary, indent=2))
