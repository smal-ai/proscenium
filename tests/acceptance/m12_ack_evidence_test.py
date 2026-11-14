"""Retained observations only. Never retries or recertifies native execution."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "native/studio"))
from m12_ack_gate import accepted_ack_loss

PATH = Path(__file__).resolve().parents[2] / "docs/evidence/m12-accepted-ack/result.json"


def test_recorded_missed_boundary_cannot_be_called_accepted_ack_loss():
    value = json.loads(PATH.read_text())
    facts = value["actual_cut_facts"]
    assert value["status"] == "not_proved_one_send_exhausted"
    assert value["send_reservations"] == 1
    assert facts["hook_matches"] == facts["genuine_reply_writes"] == 1
    assert (
        facts["reader_paused"] and facts["genuine_reply_written"] and facts["os_descriptor_closed"]
    )
    assert not facts["reader_expired"]
    assert facts["application_send_ack_delivered"]
    assert not facts["send_await_pending_before_cut"] and not facts["send_rpc_pending"]
    assert not accepted_ack_loss(facts)
    assert not value["accepted_native_user_before_cut"] and not value["send_ack_was_lost"]
    assert not value["matching_native_user_messages_after_run"]
    assert value["final_request_receipt"]["state"] == "outcome_uncertain"
    assert value["final_gate_open"] is False
    assert (
        not value["completion_claimed"] and not value["m12_complete"] and not value["m14_complete"]
    )
    assert value["quiet_restart"]["sdk_constructed"] is False
    assert value["quiet_restart"]["send_reservations"] == 1


def test_browser_sdk_server_processes_have_pid_start_and_are_reaped():
    value = json.loads(PATH.read_text())
    cleanup = value["cleanup"]
    assert not cleanup["remaining_children"]
    assert cleanup["all_recorded_kernel_identities_gone"]
    records = cleanup["recorded_processes"]
    identities = {(p["pid"], p["start"]) for p in records}
    assert any(p["kind"].startswith("chromium:") for p in records)
    assert all(p["start"] and p["original_kernel_identity_gone"] for p in records)
    sid = value["native_session_id"]
    for audit in value["host_audits"]:
        assert audit["session_ids"] == [sid]
        assert audit["provider_http_attempts"] == 0
        assert all(audit["native_identities_gone"])
        assert all((p["pid"], p["start"]) in identities for p in audit["native_processes"])
        for option in audit["options"]:
            assert (
                option["memory"] == {"enabled": False} and option["enable_session_store"] is False
            )
            assert option["available_tools"] == option["custom_agents"] == []
    assert value["host_audits"][1]["continue_pending_work"] is False


def test_already_delivered_ack_is_an_explicit_failure_even_with_other_flags_true():
    facts = dict(
        native_user_message_id="id",
        native_client_turn_id="turn",
        native_observed_before_cut_ns=1,
        cut_ns=2,
        send_await_pending_before_cut=True,
        send_rpc_pending=True,
        hook_matches=1,
        genuine_reply_writes=1,
        genuine_reply_written=True,
        reader_paused=True,
        reader_expired=False,
        os_descriptor_closed=True,
        sdk_transport_closed=True,
        application_send_ack_delivered=True,
    )
    assert not accepted_ack_loss(facts)
    facts["application_send_ack_delivered"] = False
    facts["reader_expired"] = True
    assert not accepted_ack_loss(facts)


def test_no_native_private_content_or_auth_payload_fields_are_exported():
    prohibited = {
        "content",
        "transformedContent",
        "responsesReasoning",
        "input",
        "prompt",
        "system_message",
        "authorization",
        "cookie",
        "csrf_token",
        "env",
        "pairing_code",
    }

    def check(value):
        if isinstance(value, dict):
            assert not prohibited.intersection(value)
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(json.loads(PATH.read_text()))
