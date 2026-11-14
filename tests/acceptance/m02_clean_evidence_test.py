"""Integrity checks of retained real R1 evidence, not a second cloud execution."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2] / "docs/evidence/m02-clean"


def load(name):
    return json.loads((ROOT / name).read_text())


def test_recorded_bytes_and_full_hd():
    for path, record in load("manifest.json").items():
        content = (ROOT / path).read_bytes()
        assert hashlib.sha256(content).hexdigest() == record["sha256"]
        assert len(content) == record["bytes"]
    for name in ("A", "B"):
        assert Image.open(ROOT / f"{name}.png").size == (1920, 1080)
        assert load(f"{name}.json")["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}


def test_two_actual_original_native_sends_without_resume_or_children():
    records, native = load("production-records.json"), load("native-redacted.json")
    assert native["A"]["session_id"] != native["B"]["session_id"]
    for label in ("A", "B"):
        data = records[label]
        assert len(data["requests"]) == len(data["copilot.sends"]) == 1
        assert data["requests"][0]["receipt"]["state"] == "completed"
        assert not data["copilot.resumes"] and not data["jobs"] and not data["items"]
        users = [e for e in native[label]["events"] if e["type"] == "user.message"]
        assert len(users) == 1
        assert users[0]["data"]["clientTurnId"] == data["requests"][0]["request_id"]
        assert users[0]["data"]["messageId"] == data["copilot.sends"][0]["message_id"]
        assert 0 < native[label]["cloud_execution_seconds"] <= 120


def test_real_a_shutdown_preceded_b_first_request_without_cross_attribution():
    browser, data, native = (
        load("browser-result.json"),
        load("production-records.json"),
        load("native-redacted.json"),
    )
    request_path = f"/api/v1/projects/{data['B']['requests'][0]['project_id']}/requests"
    sent = next(r["at"] for r in browser["trace"] if r["path"] == request_path)
    assert datetime.fromisoformat(browser["shutdown"]["timestamp"]) < datetime.fromisoformat(sent)
    assert browser["shutdown"]["id"] in {e["id"] for e in native["A"]["events"]}
    assert not {e["id"] for e in native["A"]["events"]}.intersection(
        e.get("native_event_id") for e in data["B"]["events"]
    )
    assert "attribution_unavailable" not in json.dumps(data["B"])
    assert browser["connections"] == browser["contexts"] == browser["pages"] == 1
    assert browser["resume_requests"] == 0


def test_quiet_reopen_resource_bound_and_old_failure_preservation():
    assert load("quiet-reopen.json")["requests"] == load("B.json")["requests"]
    assert load("protected-before.json") == load("protected-after.json")
    assert load("processes.json")["elapsed_seconds"] < 300
    assert load("processes.json")["prior_evidence_unchanged"]
    result = load("verification.json")
    assert result["R1"] == result["strict_M02"] == "PASS"
    assert result["cloud_roots"] == 2 and result["resumes"] == result["jobs"] == 0
