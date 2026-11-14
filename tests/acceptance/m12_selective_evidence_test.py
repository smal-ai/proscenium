"""Retained failed live slice only. No new native execution or completion claim."""

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "docs/evidence/m12-selective-ack/result.json"


def evidence():
    return json.loads(PATH.read_text())


def test_native_acceptance_is_retained_without_claiming_missing_cut_or_reconcile():
    value = evidence()
    assert value["send_reservations"] == 1
    users = value["matching_native_user_messages_after_run"]
    assert len(users) == 1 and users[0]["client_turn_id"] == value["request_id"]
    assert users[0]["safe_public_content_matches"]
    assert (
        value["original_request_receipt_after_cleanup"]["native_message_id"]
        == users[0]["native_message_id"]
    )
    assert value["original_request_receipt_after_cleanup"]["state"] == "outcome_uncertain"
    assert not value["application_send_ack_delivered"]
    assert value["failed_operator_receipts"][0]["error_type"] == "AttributeError"
    assert not value["explicit_descriptor_cut_proved"]
    assert not value["quiet_restart_performed"] and not value["explicit_reconcile_performed"]
    assert (
        not value["full_criterion_closed"]
        and not value["m12_complete"]
        and not value["m14_complete"]
    )


def test_all_target_processes_including_browser_and_sdk_have_reaped_identity_receipts():
    value = evidence()
    cleanup = value["cleanup"]
    assert not cleanup["remaining_children"]
    assert cleanup["all_recorded_kernel_identities_gone"]
    records = cleanup["recorded_processes"]
    known = {(p["pid"], p["start"]) for p in records}
    assert any(p["kind"].startswith("chromium:") for p in records)
    assert all(p["original_kernel_identity_gone"] and p["start"] for p in records)
    for p in value["host_audit"]["native_processes"]:
        assert (p["pid"], p["start"]) in known
    assert value["host_audit"]["provider_attempts"] == 0
    assert value["jobs"] == value["invocations"] == 0
    for options in value["host_audit"]["options"]:
        assert options["memory"] == {"enabled": False} and options["enable_session_store"] is False
        assert options["custom_agents"] == options["available_tools"] == []


def test_no_native_private_content_or_auth_payload_is_exported():
    forbidden = {
        "content",
        "transformedContent",
        "responsesReasoning",
        "prompt",
        "input",
        "cookie",
        "csrf_token",
        "env",
        "authorization",
        "pairing_code",
        "system_message",
    }

    def check(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(evidence())
