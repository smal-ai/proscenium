"""Read retained final-attempt facts. No model, native launch, retry or private repair."""

import json
from pathlib import Path

from proscenium.contracts import validate

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m10-continuation/final"


def load(name):
    return json.loads((EVIDENCE / name).read_text())


def test_corrected_metadata_and_exact_original_continue_were_acknowledged():
    result = load("summary.json")
    assert result["metadata_operation"]["state"] == "completed"
    assert (
        result["metadata_resume"]["command"]["operation_id"]
        == "0f39989b-9bce-437c-a04e-ad5aab17acc4"
    )
    command = result["owner_continue"]["command"]
    validate("ResumeCommand", command)
    assert command["mode"] == "continue_work"
    assert command["operation_id"] == "dae26d99-3ef2-49ff-a5d0-78b2ee82f739"
    assert command["work_id"] == "work-bc19a5da5fcd4aaf9ae552a13d06b20d"
    assert command["expected_revision"] == 4
    assert result["sdk_continuation_sends"] == 1
    send = result["continuation_sends"][0]
    assert send["state"] == "acknowledged"
    assert send["message_id"] == "0616732c-cddd-44b1-9875-4e59ff93a202"
    assert send["session_id"] == "5971843f-f216-40f8-b8cb-995675f48161"
    assert result["replacement_queue_requests"] == 0


def test_no_legacy_reply_authority_or_ack_was_manufactured():
    result = load("summary.json")
    assert (
        result["legacy_reply_authority_ack_before"]
        == result["legacy_reply_authority_ack_after"]
        == {
            "authority": None,
            "reply": None,
            "delivery_state": "not_sent",
        }
    )
    assert result["original_ids"]["request"] == "927cf238-56c2-454e-a063-014e072f9010"
    assert result["original_ids"]["generation_task"] == "task-248f9ca287ec4d0a81d15444dca62d8f"


def test_missing_file_read_was_automatic_rejection_not_a_human_answer():
    result, stage = load("summary.json"), load("read-permission.json")
    assert stage["native_id"] == "67539b84-6fdc-4a6e-89e2-4c802647f66b"
    assert stage["tool_call_id"] == "call_pKoAp26w293LCPhPJ3iV6uz1"
    assert stage["authority"] == "automatic_policy" and stage["reply"] == "reject"
    assert stage["phase"] == "completed" and stage["delivery_state"] == "acknowledged"
    assert stage["completions"][-1]["matches"] is True
    assert result["rejected_read"]["path"] == "assets/e04/revision-measurements.json"
    assert result["rejected_read"]["file_exists"] is False
    assert result["rejected_read"]["human_denial_inferred"] is False
    assert result["owner_decision_rows"] == {}
    assert result["permission_decisions_reported"] == []


def test_runtime_completion_is_not_m10_or_v2_success():
    result = load("summary.json")
    assert result["product_work_after"]["state"] == "completed"
    assert result["overall"] == "BLOCKED_AFTER_ORIGINAL_CONTINUE_NO_V2"
    assert result["actual_guard"] == "Error: No actual v2 publication"
    assert result["v2_exists"] is False
    assert result["native_final_nonempty_message_observed"] is False
    assert result["native_task_complete_observed"] is False
    assert all(value["before"] == value["after"] for value in result["identities"].values())
    assert result["authoring_processes"] == result["readbacks"] == 0
    assert result["new_proxy_exports"] == result["new_cpu_stills"] == 0
    assert result["criteria"]["3"].startswith("REMAINING") and result["criteria"]["5"].startswith(
        "REMAINING"
    )


def test_original_source_history_context_and_failed_reports_are_preserved():
    result, data = load("summary.json"), load("preservation.json")
    assert data["source_hashes_before"] == data["source_hashes_after"]
    assert data["failed_report_hashes_before"] == data["failed_report_hashes_after"]
    assert data["e04_hashes_before"] == data["e04_hashes_after"]
    assert data["original_context_before"] == data["original_context_after"]
    assert data["original_context_after"]["context_id"] == "83857a90-4bdc-4617-a558-a5a5739ad1be"
    assert result["original_git_refs_preserved"] and result["native_history_prefix_preserved"]
    assert result["native_user_messages_before"] == 1 and result["native_user_messages_after"] == 2
    assert result["new_native_children"] == 0
    assert result["generation_reservations_after"] == []
    assert result["e04_generation_reservations_after"] == [
        ["e04-mesh-job", "e04-generation-task", "e04-generation-task", "consumed"]
    ]


def test_owned_processes_are_closed_and_no_native_readback_is_claimed():
    result = load("summary.json")
    assert result["remaining_owned_processes"] == result["browser_processes_remaining"] == []
    assert result["driver_exit"] == 0
    assert result["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    assert result["browser_connections"] == 1 and result["software_webgl_requested"]
    assert result["mem_available_before_host"] > 4 * 1024**3
    assert result["mem_available_before_continue"] > 4 * 1024**3
    assert result["readback_helper_prepared_but_not_executed"]
    assert not result["shared_implementation_edits"]
