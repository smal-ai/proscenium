"""Checks retained real partial evidence, not a substitute for missing R1 execution."""

import hashlib
import json
from pathlib import Path

from PIL import Image

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m02-followup"


def load(name):
    return json.loads((EVIDENCE / name).read_text())


def test_manifest_integrity():
    for path, record in load("manifest.json").items():
        content = (EVIDENCE / path).read_bytes()
        assert hashlib.sha256(content).hexdigest() == record["sha256"]
        assert len(content) == record["bytes"]


def test_actual_closed_gate_cancellation_completed_without_a_fixture_dispatch():
    observed = load("cancel-observed.json")
    assert observed["job"]["state"] == "cancelled"
    assert observed["gate_is_open"] is False and observed["resumes_after_close"] == []
    assert observed["job"].get("native_exit_code") is None
    assert observed["job"]["artifact_ids"] == [] and observed["job"]["completed_frames"] == 0
    assert load("owner-cancel.json")["status"] == 202
    data = load("production-records.json")["a"]
    states = [
        e["payload"]["state"] for e in data["events"] if e["event_type"] == "job.state_changed"
    ]
    assert states == ["planned", "cancel_requested", "cancelled"]
    assert data["cancellation_intents"][0]["state"] == "acknowledged"


def test_r1_is_not_fabricated_from_an_offline_test_or_old_session():
    result = load("verification.json")
    assert result["strict_m02"] == "FAIL" and result["R1"] == "NOT_PROVEN"
    assert result["R2"] == "PASS" and result["root_requests"] == 1
    data = load("production-records.json")
    assert len(data["a"]["requests"]) == len(data["a"]["copilot.sends"]) == 1
    assert data["a"]["requests"][0]["receipt"]["state"] == "completed"
    assert not data["b"]["requests"] and not data["b"]["copilot.session"]
    assert data["a"]["copilot.resumes"][0]["state"] == "outcome_uncertain"
    native = load("native-redacted.json")
    assert len(native) == 1
    assert sum(e["type"] == "user.message" for e in native[0]["events"]) == 1
    assert any(e["type"] == "session.shutdown" for e in native[0]["events"])


def test_prior_evidence_preserved_and_processes_bounded():
    assert load("protected-before.json") == load("protected-after.json")
    for name in (
        "processes.json",
        "bootstrap-failure.json",
        "interrupted-before-b-send.json",
        "r1-reattach-timeout.json",
    ):
        info = load(name)
        for role in ("server", "browser"):
            if role in info:
                assert info[role]["elapsed_seconds"] <= 300
    assert load("processes.json")["prior_evidence_and_private_sources_unchanged"]


def test_real_ui_cancel_was_full_hd():
    for name in ("r1-a", "r2-planned-closed", "r2-cancelled-closed"):
        assert Image.open(EVIDENCE / f"{name}.png").size == (1920, 1080)
        assert load(f"{name}.json")["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
