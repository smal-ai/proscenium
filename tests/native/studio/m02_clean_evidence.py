"""Verify/export the retained clean R1 proof. No application or model is started."""

import hashlib
import importlib.metadata
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(sys.argv[1])
REPO = Path(__file__).resolve().parents[3]
OUTPUT = REPO / "docs/evidence/m02-clean"
OUTPUT.mkdir(parents=True, exist_ok=True)


def load(name):
    return json.loads((ROOT / name).read_text())


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


info, browser = load("processes.json"), load("browser-result.json")
assert importlib.metadata.version("github-copilot-sdk") == "1.0.34"
assert info["test_base"] == "e3cddf8e90f49a627c7630dab12669d97f1c3dfd"
assert info["driver_exit"] == 0 and info["elapsed_seconds"] < 300
assert browser["result"] == "PASS" and browser["resume_requests"] == 0
assert [browser[k] for k in ("connections", "contexts", "pages")] == [1, 1, 1]
assert load("protected-before.json") == load("protected-after.json")
data, native, native_ids = {}, {}, {}
for label in ("A", "B"):
    capture = load(f"{label}.json")
    assert capture["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    assert capture["snapshot"]["project"]["state"] == "active"
    assert sum(p["state"] != "inactive" for p in capture["projects"]["items"]) == 1
    database = ROOT / label.lower() / ".proscenium/state.sqlite3"
    row = {
        n: records(database, n)
        for n in (
            "requests",
            "contexts",
            "work",
            "invocations",
            "copilot.sends",
            "copilot.session",
            "copilot.resumes",
            "copilot.control",
            "jobs",
            "items",
        )
    }
    assert len(row["requests"]) == len(row["copilot.sends"]) == len(row["copilot.session"]) == 1
    assert not row["copilot.resumes"] and not row["jobs"] and not row["items"]
    request, send = row["requests"][0], row["copilot.sends"][0]
    assert request["receipt"]["state"] == "completed" and send["state"] == "acknowledged"
    sid = row["copilot.session"][0]["session_id"]
    assert sid == capture["snapshot"]["project"]["native_session_id"] == send["session_id"]
    assert f"M02-CLEAN-{label}-20261114" not in request["submission"]["text"]
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        events = [
            json.loads(r[0])
            for r in connection.execute("SELECT body FROM events ORDER BY sequence")
        ]
    text = "\n".join(
        e["payload"]["text"] for e in events if e["event_type"] == "assistant.completed"
    )
    assert f"M02-CLEAN-{label}-20261114" in text
    assert f"M02-CLEAN-{'B' if label == 'A' else 'A'}-20261114" not in text
    assert all(e.get("native_session_id") in (None, sid) for e in events)
    assert all(i["session_id"] == sid for i in row["invocations"])
    assert all(w["native_session_id"] == sid for w in row["work"])
    row["all_public_native_event_ids"] = sorted(
        {e["native_event_id"] for e in events if e.get("native_event_id")}
    )
    row["events"] = [
        e
        for e in events
        if e["event_type"]
        in {
            "project.state_changed",
            "request.accepted",
            "request.state_changed",
            "work.state_changed",
            "assistant.completed",
            "tool.started",
            "tool.completed",
        }
    ]
    assert "attribution_unavailable" not in json.dumps(row)
    data[label] = row
    path = ROOT / "m/copilot-runtime/home/session-state" / sid / "events.jsonl"
    raw = [json.loads(line) for line in path.read_text().splitlines()]
    users = [e for e in raw if e["type"] == "user.message"]
    assert len(users) == 1 and users[0]["data"]["messageId"] == send["message_id"]
    assert users[0]["data"]["clientTurnId"] == request["request_id"]
    assert not any(e["type"].startswith("subagent.") or e["type"] == "session.error" for e in raw)
    start = next(e for e in raw if e["type"] == "session.start")
    assert start["data"]["selectedModel"] == "gpt-6.1-sol"
    assert {e["data"]["toolName"] for e in raw if e["type"] == "tool.execution_start"} == {"view"}
    seconds = (
        datetime.fromisoformat(
            max(e["timestamp"] for e in raw if e["type"] == "assistant.turn_end")
        )
        - datetime.fromisoformat(users[0]["timestamp"])
    ).total_seconds()
    assert 0 < seconds <= 120
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
        for e in raw
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
    native[label] = {
        "session_id": sid,
        "cloud_execution_seconds": seconds,
        "events": safe,
        "private_history_sha256": sha(path),
        "brief_sha256": sha(ROOT / label.lower() / "brief/title.md"),
    }
    native_ids[label] = {e["id"] for e in raw}
assert native["A"]["session_id"] != native["B"]["session_id"]
assert not native_ids["A"].intersection(e.get("native_event_id") for e in data["B"]["events"])
assert not native_ids["A"].intersection(data["B"]["all_public_native_event_ids"])
assert native["A"]["session_id"] not in json.dumps(data["B"])
assert browser["shutdown"]["id"] in native_ids["A"]
b_request = data["B"]["requests"][0]
b_post = next(
    r["at"]
    for r in browser["trace"]
    if r["path"] == f"/api/v1/projects/{b_request['project_id']}/requests"
)
assert datetime.fromisoformat(browser["shutdown"]["timestamp"]) < datetime.fromisoformat(b_post)
assert len([r for r in browser["trace"] if r["path"].endswith("/requests")]) == 2
quiet = load("quiet-reopen.json")
assert quiet["snapshot"]["project"]["state"] == "active"
assert quiet["requests"] == load("B.json")["requests"]
assert quiet["snapshot"]["project"]["native_session_id"] == native["B"]["session_id"]
for name in (
    "setup.json",
    "A.json",
    "B.json",
    "quiet-reopen.json",
    "browser-result.json",
    "processes.json",
    "protected-before.json",
    "protected-after.json",
    "A.png",
    "B.png",
):
    shutil.copyfile(ROOT / name, OUTPUT / name)
(OUTPUT / "production-records.json").write_text(json.dumps(data, indent=2))
(OUTPUT / "native-redacted.json").write_text(json.dumps(native, indent=2))
runtime = load("m/copilot-runtime/process.json")
runtime["binary_sha256"] = sha(Path(runtime["binary"]))
assert (
    runtime["binary_sha256"] == "463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69"
)
for pid in (runtime["pid"], info["server"]["pid"], info["browser"]["pid"]):
    assert not Path(f"/proc/{pid}").exists()
assert not info["server"].get("forced") and not info["browser"].get("forced")
(OUTPUT / "runtime-process.json").write_text(json.dumps(runtime, indent=2))
identities = {
    "test_base": info["test_base"],
    "sdk": "github-copilot-sdk==1.0.34",
    "cli": "1.0.112",
    "model": "gpt-6.1-sol",
    "root": str(ROOT),
    "compiled_sha256": {
        str(p.relative_to(REPO)): sha(p)
        for p in (REPO / "apps/frontend/dist").rglob("*")
        if p.is_file()
    },
    "executed_driver_sha256": info["driver_sha256"],
}
if (OUTPUT / "identities.json").exists():
    identities = json.loads((OUTPUT / "identities.json").read_text())
(OUTPUT / "identities.json").write_text(json.dumps(identities, indent=2))
summary = {
    "R1": "PASS",
    "strict_M02": "PASS",
    "native_session_ids": {k: v["session_id"] for k, v in native.items()},
    "original_requests": {k: data[k]["requests"][0]["request_id"] for k in data},
    "cloud_roots": 2,
    "native_children": 0,
    "resumes": 0,
    "jobs": 0,
    "renders_or_native_inspections": 0,
    "actual_shutdown_before_B_request": True,
    "cross_project_event_attribution": False,
    "quiet_reopen_unchanged": True,
    "prior_evidence_unchanged": True,
    "whole_proof_seconds": info["elapsed_seconds"],
    "inherited": [
        "M01 accepted",
        "original M02 criteria 2/3/4/6 and atomic publication",
        "R2 accepted from a0c15ba7d95193095718a730aabf6b833b77c496; not rerun",
    ],
}
(OUTPUT / "verification.json").write_text(json.dumps(summary, indent=2))
(OUTPUT / "manifest.json").write_text(
    json.dumps(
        {
            p.name: {"sha256": sha(p), "bytes": p.stat().st_size}
            for p in OUTPUT.iterdir()
            if p.is_file() and p.name not in {"README.md", "manifest.json"}
        },
        indent=2,
    )
)
print(json.dumps(summary, indent=2))
