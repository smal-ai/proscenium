"""Verify retained real M12 evidence. Never starts a runtime or recertifies a run."""

import json
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m12/native-recovery.json"


def evidence():
    return json.loads(EVIDENCE.read_text())


def test_real_same_session_continuity_keeps_images_feedback_and_new_scope():
    value = evidence()
    assert value["native_session_id"] == "d939999f-bff2-448c-9d24-028c1dcff527"
    proof = value["native_continuity"]
    assert proof["old_public_message_ids_restored"]
    assert all(proof["checks"].values())
    assert proof["native_prompt_hook_cwd_matches_restored_root"]
    assert proof["held_restart"]["same_submission"] and proof["held_restart"]["sends"] == 0
    assert proof["request_receipt"]["state"] == "completed"
    assert all(value["restoration"]["namespace_hashes_unchanged"].values())
    assert value["restoration"]["backed_up_working_source_bytes_unchanged"]
    assert value["restoration"]["original_request_submissions_and_native_ids_unchanged"]
    assert value["restoration"]["actual_sdk_annotation_extension_matches_captured_context"]
    assert len(value["restoration"]["actual_sdk_image_files"]) == 2
    assert all(
        check["status"] == 200 and check["matches"]
        for check in value["restoration"]["media_http_checks"]
    )
    for options in value["native_resume_options"]:
        assert options["memory"] == {"enabled": False}
        assert options["enable_session_store"] is False
        assert options["continue_pending_work"] is False
        assert options["custom_agents"] == []


def test_original_auxiliary_deviation_is_not_hidden_by_corrected_read_only_pass():
    value = evidence()["original_preservation"]
    assert value["initial_whole_project_tree_unchanged"] is False
    assert value["only_initial_auxiliary_sidecars_removed"]
    assert value["durable_project_bytes_unchanged"]
    assert value["initial_registry_main_unchanged"] and value["initial_native_history_unchanged"]
    assert value["corrected_backup_through_final_all_measured_files_unchanged"]


def test_unjoined_pre_admission_pipe_loss_is_not_called_success_or_retried():
    value = evidence()
    assert value["total_new_sdk_send_calls"] == 2
    assert value["new_native_user_messages_confirmed"] == 1
    assert (
        value["children_observed"]
        == value["fresh_session_attempts"]
        == value["provider_http_attempts"]
        == 0
    )
    loss = value["transport_loss"]
    assert loss["physical_cut"]["send_pending_before_cut"]
    assert loss["physical_cut"]["actual_stdout_fd_closed"]
    assert loss["physical_cut"]["sdk_transport_closed"]
    assert not loss["physical_cut"]["reader_pause_expired"]
    assert loss["native_match_count"] == 0
    assert loss["matching_user_prompt_hooks"]
    assert loss["original_request_receipt"]["state"] == "outcome_uncertain"
    assert loss["original_request_receipt"]["native_message_id"] is None
    assert not loss["accepted_native_admission_ack_loss_proved"]
    assert not loss["request_reposted"] and not loss["sdk_send_retried"]
    assert not value["m12_complete"]
    assert all(p["original_identity_gone"] for p in value["cleanup"]["server_and_sdk_identities"])


def test_export_contains_no_native_private_content_or_auth_payload_fields():
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

    check(evidence())
