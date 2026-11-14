"""Read retained evidence only. No runtime, browser, model send or private repair."""

import json
from pathlib import Path

from proscenium.contracts import validate

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m10-continuation"


def load(name):
    return json.loads((EVIDENCE / name).read_text())


def test_original_control_ids_and_immutable_context_are_preserved():
    result = load("summary.json")
    ids = result["original_ids"]
    assert ids["request_id"] == "927cf238-56c2-454e-a063-014e072f9010"
    assert ids["work_id"] == "work-bc19a5da5fcd4aaf9ae552a13d06b20d"
    assert ids["session_id"] == "5971843f-f216-40f8-b8cb-995675f48161"
    assert ids["native_message_id"] == "aeb54bc0-32e1-409d-b80b-1b85b984cb54"
    assert result["immutable_request_submission_preserved"]
    assert result["immutable_original_context_preserved"]
    preserved = load("preservation.json")
    assert preserved["original_context_before"] == preserved["original_context_after"]
    assert (
        preserved["original_context_after"]["context_id"] == "83857a90-4bdc-4617-a558-a5a5739ad1be"
    )
    assert preserved["original_context_after"]["selected_objects"] == []


def test_real_picked_object_capture_is_now_accepted_without_queueing():
    result = load("summary.json")
    assert result["selected_object_capture"]["status"] == 201
    body = result["selected_object_capture"]["body"]
    validate("ContextSnapshot", body)
    assert body["context_id"] == "4b0a5a53-001d-464e-82f2-f7a1949eb593"
    assert body["version_id"] == "m10-asset-v1"
    assert body["selected_objects"] == [
        {"object_id": "e04-object-001", "instance_id": "e04-instance-001"}
    ]
    assert result["replacement_queue_requests"] == 0
    assert result["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    assert "SwiftShader" in result["software_renderer"]
    assert result["continuous_browser_connections"] == 1


def test_one_correctly_targeted_metadata_resume_stopped_before_continue():
    result = load("summary.json")
    assert result["metadata_reconcile_attempts"] == 1
    assert result["metadata_resume"]["status"] == 202
    command = result["metadata_resume"]["command"]
    validate("ResumeCommand", command)
    assert command["mode"] == "reconcile_only"
    assert command["work_id"] == result["original_ids"]["work_id"]
    assert command["expected_revision"] == 3
    assert command["operation_id"] == "f055275c-71c2-42e8-802a-c74d7516da31"
    assert result["metadata_operation"]["state"] == "outcome_uncertain"
    assert result["metadata_operation"]["detail_code"] == "attribution_unavailable"
    assert result["continue_owner_commands"] == result["sdk_continuation_sends"] == 0
    assert result["native_user_messages_before"] == result["native_user_messages_after"] == 1


def test_legacy_permission_ack_or_execution_was_not_invented():
    result = load("summary.json")
    stage = result["blocker_permission_stages"][0]
    assert stage["native_id"] == "1fbd60bd-5e63-4553-af72-8b6d50648dc1"
    assert stage["phase"] == "outcome_uncertain"
    assert stage["authority"] is None and stage["reply"] is None
    assert stage["delivery_state"] == "not_sent"
    assert stage["completions"][0]["matches"] is False
    invocation = result["blocker_invocations"][0]
    assert invocation["native_id"] == "call_Yo8Pyi1HvNYCI5E6CSQxCamx"
    assert (
        invocation["invocation_id"]
        == "3b55ae7e5f095af132fa663e93619f02d176c412b5d5fada875b69e7f18c23b5"
    )
    assert invocation["state"] == "running"
    assert result["original_binding_rows_preserved"]
    assert result["original_work_after"]["state"] == "awaiting_resume"
    assert result["original_request_receipt_after"]["state"] == "acknowledged"


def test_original_source_history_media_and_generation_counters_are_preserved():
    result, preservation = load("summary.json"), load("preservation.json")
    assert preservation["file_hashes_before"] == preservation["file_hashes_after"]
    assert preservation["git_refs_before"] == preservation["git_refs_after"]
    assert preservation["e04_file_hashes_before"] == preservation["e04_file_hashes_after"]
    assert result["native_history_prefix_preserved"]
    assert result["generation_reservations_before"] == result["generation_reservations_after"] == []
    assert (
        result["e04_generation_reservations_before"]
        == result["e04_generation_reservations_after"]
        == [["e04-mesh-job", "e04-generation-task", "e04-generation-task", "consumed"]]
    )


def test_owned_processes_closed_and_unrelated_blender_was_preserved():
    result = load("summary.json")
    checks = result["process_checks"]
    assert (
        not checks["server_remaining"]
        and not checks["driver_remaining"]
        and not checks["sdk_remaining"]
    )
    assert checks["browser_processes_remaining"] == result["remaining_owned_processes"] == []
    assert (
        checks["protected_blender_start_before"]
        == checks["protected_blender_start_after"]
        == "46620374"
    )
    assert result["unrelated_services_targeted"] is False
    assert result["mem_available_before_host"] > 4 * 1024**3


def test_no_missing_revision_or_milestone_is_claimed():
    result = load("summary.json")
    assert result["overall"] == "BLOCKED_BEFORE_CONTINUATION_SEND"
    assert result["authoring_processes"] == result["readbacks"] == 0
    assert (
        result["new_proxy_exports"]
        == result["new_cpu_stills"]
        == result["new_native_children"]
        == 0
    )
    assert result["v2_exists"] is False
    assert result["criteria"]["3"].startswith("REMAINING")
    assert result["criteria"]["5"] == "REMAINING"
