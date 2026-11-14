"""Integrity/oracles over retained controls. Never runs a model or certifies continuation."""

import hashlib
import json
from pathlib import Path

from PIL import Image

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m03-resume"


def report():
    return json.loads((EVIDENCE / "run.json").read_text())


def test_retained_evidence_integrity_and_full_hd():
    manifest = json.loads((EVIDENCE / "manifest.json").read_text())
    for relative, expected in manifest.items():
        path = EVIDENCE / relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
        if path.suffix == ".png":
            with Image.open(path) as image:
                assert image.size == (1920, 1080)
    assert len([name for name in manifest if name.endswith(".png")]) == 5


def test_ui_continue_admits_exact_original_work_and_work_revision():
    data = report()
    original = "work-6361ef13fbeb4f4884ff382bf96d8815"
    run = data["runs"]["a"]
    assert data["application_revision"].startswith("e3cddf8")
    assert data["selector"]["selected"] == original
    assert {row["value"] for row in data["selector"]["options"]} >= {
        original,
        "work-6c566aaa39084a139da0b42fcff0b555",
    }
    assert run["command"]["work_id"] == run["target_before"]["work_id"] == original
    assert run["command"]["expected_revision"] == run["target_before"]["state_revision"] == 4
    assert run["project_revision"] == 5  # This actual Continue distinguishes the resources.
    assert run["http_status"] == 202 and run["receipt"]["state"] == "accepted"
    assert run["operation"]["state"] == "outcome_uncertain"
    assert not data["successful_continuation"]


def test_known_ack_reconcile_admitted_but_unfinished_tool_remains_unproven():
    data = report()
    run = data["runs"]["b"]
    assert run["command"]["work_id"] == "work-ea4ab25e133747dba8e1f74dad160d2a"
    assert run["command"]["expected_revision"] == run["target_before"]["state_revision"] == 3
    assert run["http_status"] == 202
    assert run["operation"]["state"] == "outcome_uncertain"
    assert run["operation"]["detail_code"] == "attribution_unavailable"
    assert run["reconciliation_evidence"][0]["pending_empty"]
    assert run["reconciliation_evidence"][0]["live_invocations"] == 1
    assert run["live_invocations"][0]["name"] == "bash"
    assert run["live_invocations"][0]["state"] == "running"
    target = next(w for w in run["work_after"] if w["work_id"] == run["command"]["work_id"])
    assert target["state"] == "awaiting_resume"
    before = next(
        r for r in run["requests_before"] if r["request_id"] == target["originating_request_id"]
    )
    after = next(r for r in run["requests_after"] if r["request_id"] == before["request_id"])
    assert before["receipt"]["state"] == "outcome_uncertain"
    assert after["receipt"]["state"] == "acknowledged"
    assert (
        before["receipt"]["native_message_id"]
        == after["receipt"]["native_message_id"]
        == "2fa74a64-fda2-49c9-abef-710a8cffb705"
    )
    assert not data["actual_missing_native_ack_proof"]


def test_exact_pending_b_stop_rejection_never_fabricates_cancellation_or_owner_answer():
    data = report()
    stop = data["exact_pending_b_stop"]
    assert stop["status"] == 400
    assert stop["receipt"]["error"]["code"] == "invalid_state_transition"
    assert stop["command"]["type"] == "stop"
    assert stop["command"]["target_work_id"] == "work-6c566aaa39084a139da0b42fcff0b555"
    decision = data["runs"]["a"]["decisions_after"][0]
    assert decision["state"] == "pending" and decision["response"] is None
    assert decision["resolved_by"] is None and decision["delivery_state"] == "not_sent"
    assert data["runs"]["a"]["live_invocations"][0]["name"] == "ask_user"


def test_original_request_ids_preserved_and_no_followup_send_or_helper():
    data = report()
    assert data["allocation"]["original_actual_sdk_sends"] == 5
    for field in [
        "followup_sdk_sends",
        "followup_children",
        "followup_helpers",
        "new_queue_requests",
    ]:
        assert data["allocation"][field] == 0
    for run in data["runs"].values():
        assert {r["request_id"] for r in run["requests_before"]} == {
            r["request_id"] for r in run["requests_after"]
        }
        assert run["held_before"] == run["held_after"] == []  # No held fixture is invented.
        assert run["viewport"] == dict(width=1920, height=1080, dpr=1)
    assert all(v["verified_exact_before_copy"] for v in data["snapshots"].values())
    assert all(
        "database-export.json" in v and "sends.jsonl" in v
        for v in data["original_evidence_unchanged_sha256"].values()
    )


def test_exact_owned_cleanup_and_controlled_ui_checks_do_not_certify_native_result():
    data = report()
    for run in data["runs"].values():
        for kind in ["server", "browser", "native"]:
            assert not run["cleanup"][kind]["same_identity_alive"]
        assert not any(row["proc_exists"] for row in run["cleanup"]["helpers"])
    assert data["ui_checks"]["passed"] == 4
    assert not data["ui_checks"]["server"]["same_identity_alive"]
    assert not any(row["same_identity_alive"] for row in data["ui_checks"]["browsers"])
    assert not data["owned_process_leftovers"]
    assert data["status"].startswith("BLOCKED")
    assert data["no_product_source_changes"] and not data["original_m10_accessed"]


def test_no_pairing_or_channel_secrets_in_exports():
    for path in EVIDENCE.glob("*.json"):
        for excluded in [
            '"pairing_code"',
            '"csrf_token"',
            '"pair_url"',
            '"token":',
            "Bearer ",
            "#pair=",
        ]:
            assert excluded not in path.read_text(), (path.name, excluded)
