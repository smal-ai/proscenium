"""Read-only verification/export of the actual partial follow-up, never a new run."""

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(sys.argv[1])
REPO = Path(__file__).resolve().parents[3]
OUTPUT = REPO / "docs/evidence/m02-followup"
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


info = load("processes.json")
assert info["test_base"] == "8dd18b86526fda843a62fd0b2056611816308fd0"
assert load("protected-before.json") == load("protected-after.json")
assert load("browser-result.json")["result"] == "R2_completed_R1_incomplete"
plan = load("planned.json")
assert plan["status"] == "completed"
cancel = load("cancel-observed.json")
assert cancel["job"]["state"] == "cancelled" and not cancel["gate_is_open"]
assert cancel["resumes_after_close"] == []
assert load("owner-cancel.json")["status"] == 202
assert not cancel["job"].get("native_exit_code")  # Absence/null, never fabricated zero.
assert "native_exit_code" not in cancel["job"] or cancel["job"]["native_exit_code"] is None
assert not list((ROOT / "project-a/.proscenium/jobs").rglob("launch.claim"))
assert not list((ROOT / "project-a/.proscenium/jobs").rglob("process.json"))
assert not list((ROOT / "project-a/.proscenium/jobs").rglob("native-process.json"))
data = {}
for label in ("a", "b"):
    database = ROOT / f"project-{label}/.proscenium/state.sqlite3"
    data[label] = {
        n: records(database, n)
        for n in (
            "requests",
            "copilot.sends",
            "copilot.session",
            "copilot.control",
            "copilot.resumes",
            "jobs",
            "attempts",
            "frames",
            "versions",
            "invocations",
            "work",
        )
    }
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        events = [
            json.loads(row[0])
            for row in connection.execute("SELECT body FROM events ORDER BY sequence")
        ]
        intents = [
            dict(zip(("id", "kind", "state", "body", "evidence"), row))
            for row in connection.execute(
                "SELECT id,kind,state,body,evidence FROM outbox WHERE kind='job.cancel'"
            )
        ]
    data[label]["events"] = [
        e
        for e in events
        if e["event_type"]
        in {
            "project.state_changed",
            "assistant.completed",
            "request.accepted",
            "request.state_changed",
            "work.state_changed",
            "job.state_changed",
            "publication.created",
        }
    ]
    data[label]["cancellation_intents"] = intents
assert len(data["a"]["requests"]) == len(data["a"]["copilot.sends"]) == 1
assert data["a"]["requests"][0]["receipt"]["state"] == "completed"
assert (
    not data["b"]["requests"]
    and not data["b"]["copilot.sends"]
    and not data["b"]["copilot.session"]
)
assert not data["a"]["frames"] and not data["b"]["frames"]
assert len(data["a"]["jobs"]) == 1 and data["a"]["jobs"][0]["state"] == "cancelled"
assert data["a"]["copilot.resumes"][0]["state"] == "outcome_uncertain"
assert data["a"]["copilot.resumes"][0]["body"]["mode"] == "reconcile_only"
assert len(data["a"]["cancellation_intents"]) == 1
assert data["a"]["cancellation_intents"][0]["state"] == "acknowledged"
native = []
for path in (ROOT / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"):
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert sum(e["type"] == "user.message" for e in events) == 1
    assert not any(e["type"].startswith("subagent.") for e in events)
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
    assert any(e["type"] == "session.shutdown" for e in safe)
    assert (
        next(e for e in safe if e["type"] == "session.start")["data"]["selectedModel"]
        == "gpt-6.1-sol"
    )
    user_time = next(e["timestamp"] for e in events if e["type"] == "user.message")
    last_turn_time = max(e["timestamp"] for e in events if e["type"] == "assistant.turn_end")
    cloud_seconds = (
        datetime.fromisoformat(last_turn_time) - datetime.fromisoformat(user_time)
    ).total_seconds()
    assert 0 < cloud_seconds <= 120
    native.append(
        {
            "session_id": path.parent.name,
            "events": safe,
            "private_history_sha256": sha(path),
            "cloud_execution_seconds": cloud_seconds,
        }
    )
assert len(native) == 1
a_ids = {e["id"] for e in native[0]["events"]}
assert not a_ids.intersection(e.get("native_event_id") for e in data["b"]["events"])
for name in (
    "r1-a.json",
    "r2-only-initial.json",
    "r2-planned-closed.json",
    "r2-cancelled-closed.json",
):
    assert load(name)["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    shutil.copyfile(ROOT / name, OUTPUT / name)
for name in (
    "r1-a.png",
    "r2-planned-closed.png",
    "r2-cancelled-closed.png",
    "planned.json",
    "cancel-observed.json",
    "owner-cancel.json",
    "processes.json",
    "bootstrap-failure.json",
    "interrupted-before-b-send.json",
    "r1-reattach-timeout.json",
    "protected-before.json",
    "protected-after.json",
):
    shutil.copyfile(ROOT / name, OUTPUT / name)
(OUTPUT / "production-records.json").write_text(json.dumps(data, indent=2))
(OUTPUT / "native-redacted.json").write_text(json.dumps(native, indent=2))
runtime = load("machine/copilot-runtime/process.json")
assert not Path(f"/proc/{runtime['pid']}").exists()
runtime["binary_sha256"] = sha(Path(runtime["binary"]))
(OUTPUT / "runtime-process.json").write_text(json.dumps(runtime, indent=2))
for process_info in (
    info,
    load("bootstrap-failure.json"),
    load("interrupted-before-b-send.json"),
    load("r1-reattach-timeout.json"),
):
    for role in ("server", "browser"):
        if role in process_info:
            record = process_info[role]
            assert record["elapsed_seconds"] <= 300 and not Path(f"/proc/{record['pid']}").exists()
identities = {
    "test_base": info["test_base"],
    "accepted_fixes": ["f96e78b", "c1b2181"],
    "sdk": "github-copilot-sdk==1.0.34",
    "cli": "1.0.112",
    "model": "gpt-6.1-sol",
    "root": str(ROOT),
    "source_sha256": {
        name: sha(REPO / name)
        for name in subprocess.check_output(
            [
                "git",
                "ls-files",
                "apps/backend/src",
                "apps/frontend/src",
                "uv.lock",
                "package-lock.json",
            ],
            cwd=REPO,
            text=True,
        ).splitlines()
    },
    "compiled_sha256": {
        str(p.relative_to(REPO)): sha(p)
        for p in (REPO / "apps/frontend/dist").rglob("*")
        if p.is_file()
    },
}
if (OUTPUT / "identities.json").exists():
    identities = json.loads((OUTPUT / "identities.json").read_text())
(OUTPUT / "identities.json").write_text(json.dumps(identities, indent=2))
summary = {
    "strict_m02": "FAIL",
    "R1": "NOT_PROVEN",
    "R2": "PASS",
    "reason": "Driver interruption before B's first Send; subsequent read-only A reattach became outcome_uncertain. No retry or fabricated B execution.",
    "root_requests": 1,
    "native_children": 0,
    "native_inspections": 0,
    "rendered_frames": 0,
    "manual_cancellation_dispatch": False,
    "gate_opened_after_planned_close": False,
    "prior_evidence_unchanged": True,
    "cancel_latency_from_gate_close_seconds": (
        cancel["observed_monotonic_ns"] - plan["result"]["closed_monotonic_ns"]
    )
    / 1e9,
    "reused_dependencies": ["milestone/m01", "docs/evidence/m02 positive criteria 2/3/4/6"],
    "new_legacy_failure_claim": False,
}
(OUTPUT / "verification.json").write_text(json.dumps(summary, indent=2))
manifest = {
    p.name: {"sha256": sha(p), "bytes": p.stat().st_size}
    for p in OUTPUT.iterdir()
    if p.is_file() and p.name not in {"manifest.json", "README.md"}
}
(OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
print(json.dumps(summary, indent=2))
