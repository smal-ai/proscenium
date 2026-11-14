"""Retained one-send admission evidence only. No live model/runtime execution."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "native/studio"))
from m12_selective_filter import selective_proof

PATH = (
    Path(__file__).resolve().parents[2]
    / "docs/evidence/m12-selective-ack/final-verified-result.json"
)


def evidence():
    return json.loads(PATH.read_text())


def test_durable_exact_response_acceptance_and_physical_cut_order():
    value = evidence()
    stages = value["durable_checkpoints"]
    cut = value["actual_cut"]
    assert value["status"] == "selective_accepted_ack_recovery_pass"
    assert value["send_reservations"] == 1 and len(value["matching_native_user_messages"]) == 1
    held = stages["01-target-response-withheld"]
    assert held["rpc_future_pending"] and held["facts"]["withheld_response_count"] == 1
    assert (
        held["send_rpc_id"] == cut["send_rpc_id"]
        and held["request_id"] == value["original_request_id"]
    )
    assert held["session_id"] == value["native_session_id"]
    assert held["facts"]["withheld_response_sha256"] == cut["withheld_response_sha256"]
    accepted = stages["03-native-acceptance-before-cut"]
    assert accepted["rpc_future_pending"] and accepted["facts"]["send_await_pending_before_cut"]
    assert accepted["facts"]["native_user_message_id"] == cut["withheld_native_message_id"]
    assert (
        cut["withheld_response_ns"]
        < cut["hook_received_ns"]
        < cut["genuine_reply_written_ns"]
        < cut["native_observed_before_cut_ns"]
        < cut["cut_ns"]
        < cut["closed_ns"]
        < cut["owned_writer_fence_ns"]
    )
    assert cut["withheld_response_count"] == cut["hook_matches"] == cut["genuine_reply_writes"] == 1
    assert (
        cut["other_responses_forwarded"] > 0
        and cut["events_forwarded"] > 0
        and cut["server_requests_forwarded"] > 0
    )
    assert not cut["application_send_ack_delivered"]
    assert (
        cut["send_rpc_pending"]
        and cut["sdk_transport_closed"]
        and cut["os_descriptor_closed"]
        and cut["raw_file_closed"]
    )
    assert (
        stages["00-actual-sdk-transport-preflight"]["transport_preflight"]["stdout_type"]
        == "FileIO"
    )
    assert stages["05-real-descriptor-closed"]["facts"]["os_descriptor_closed"]
    # Persisted before the verdict was returned to the original caller.
    assert selective_proof(stages["08-sdk-transport-result"])


def test_quiet_restart_reconciles_original_without_second_send_or_work_completion_claim():
    value = evidence()
    cut = value["actual_cut"]
    restart = value["quiet_restart"]
    assert (
        not restart["sdk_constructed"]
        and not restart["gate_open"]
        and restart["send_reservations"] == 1
    )
    assert value["explicit_owner_reconcile"]["state"] == "completed"
    assert value["final_request_receipt"]["request_id"] == value["original_request_id"]
    assert value["final_request_receipt"]["native_message_id"] == cut["native_user_message_id"]
    assert value["final_request_receipt"]["state"] == "acknowledged"
    assert value["final_work_state"] == "awaiting_resume" and value["execution_gate_remains_closed"]
    assert value["identity_was_already_projected_from_forwarded_events_before_cut"]
    assert (
        not value["model_or_work_completion_claimed"]
        and not value["m12_complete"]
        and not value["m14_complete"]
    )
    assert value["jobs"] == value["tool_invocations"] == 0
    for audit in value["host_audits"]:
        assert (
            audit["session_ids"] == [value["native_session_id"]]
            and audit["provider_http_attempts"] == 0
        )
        for option in audit["options"]:
            assert (
                option["memory"] == {"enabled": False} and option["enable_session_store"] is False
            )
            assert option["available_tools"] == option["custom_agents"] == []
    assert value["host_audits"][1]["continue_pending_work"] is False


def test_browser_sdk_host_pid_start_cleanup_and_export_privacy():
    value = evidence()
    cleanup = value["cleanup"]
    known = {(p["pid"], p["start"]) for p in cleanup["recorded_processes"]}
    assert cleanup["all_recorded_kernel_identities_gone"] and not cleanup["remaining_children"]
    assert cleanup["controller_exit_code"] == 0 and cleanup["browser_context_close_awaited"]
    assert any(p["kind"].startswith("chromium:") for p in cleanup["recorded_processes"])
    for audit in value["host_audits"]:
        assert all((p["pid"], p["start"]) in known for p in audit["native_processes"])
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

    def check(item):
        if isinstance(item, dict):
            assert not forbidden.intersection(item)
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)

    check(value)
