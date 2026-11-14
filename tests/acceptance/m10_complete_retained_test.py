"""Read retained actual M10 follow-up evidence. No model or native process is launched."""

import json
from pathlib import Path

from proscenium.contracts import validate

DIRECTORY = Path(__file__).resolve().parents[2] / "docs/evidence/m10-complete"


def result():
    return json.loads((DIRECTORY / "actual-run.json").read_text())


def test_one_new_owner_queue_same_session_old_completed_work_not_reopened():
    data = result()
    assert data["status"] == "actual_follow_up_v2_publication_and_review_pass"
    assert data["new_queue_count"] == 1
    new = data["new_requests"][0]
    validate("RequestRecord", new)
    assert new["submission"]["type"] == "queue"
    assert new["request_id"] != "927cf238-56c2-454e-a063-014e072f9010"
    assert new["receipt"]["work_id"] != "work-bc19a5da5fcd4aaf9ae552a13d06b20d"
    assert (
        data["new_work"]["native_session_id"]
        == data["same_session"]
        == "5971843f-f216-40f8-b8cb-995675f48161"
    )
    assert not data["old_work_reopened"] and not data["old_work_relabelled_natural_completion"]
    assert data["browser"]["final_old_work"]["state"] == "completed"
    assert data["browser"]["final_old_work"]["state_revision"] == 6
    assert data["browser"]["old_work_continue_commands"] == 0
    assert data["sdk_send_claim"]["sends"] == 1 and data["sdk_send_ack"]["native_message_id"]
    assert data["native_user_messages_after"] == data["native_user_messages_before"] + 1


def test_actual_pairing_activation_new_context_and_no_delegation():
    data = result()
    ui = data["ui_preflight"]
    assert ui["pairing_status"] == 201 and ui["sdk_sends"] == 0
    assert ui["activation"]["selector"] == "button.project-trigger"
    assert ui["activation"]["selected_project"] == data["project_id"]
    assert ui["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    assert ui["source_preserved"] and ui["old_work_unchanged"] and ui["old_request_unchanged"]
    assert data["new_context"]["context_id"] == data["new_requests"][0]["submission"]["context_id"]
    validate("ContextSnapshot", data["new_context"])
    allocated = data["session_allocation"]
    assert allocated["creates"] == data["native_children"] == 0
    assert allocated["excluded_tools"] == ["task"] and allocated["custom_agents"] == []
    assert allocated["permission_handler_preserved"] and allocated["continue_pending_work"] is False
    assert allocated["model"] == "gpt-6.1-sol"
    assert allocated["enable_session_store"] is False and allocated["memory"] == {"enabled": False}


def test_actual_model_authoring_scale_material_and_optional_independent_readback():
    data = result()
    assert 1 <= data["authoring_claims"] == len(data["authoring"]) <= 2
    authors = data["authoring"]
    assert authors[-1]["exit_code"] == 0
    assert all(row["pid"] > 0 and int(row["start_ticks"]) > 0 for row in authors)
    if len(authors) == 2:
        assert authors[0]["exit_code"] != 0
        assert not authors[0].get("uncertain") and not authors[0].get("timed_out")
        assert authors[0]["script_sha256"] != authors[1]["script_sha256"]
    measured = data["measurements"]
    assert abs(measured["after"]["height_m"] - 0.12) < 1e-5
    assert abs(measured["after"]["lowest_world_z_m"]) < 1e-5
    assert measured["before"]["counts"] == measured["after"]["counts"]
    assert measured["after"]["counts"]["vertices"] == 350259
    assert measured["after"]["counts"]["triangles"] == 1304618
    assert measured["mesh_coordinates_and_connectivity_unchanged"]
    assert measured["proscenium_object_id"] == "e04-object-001"
    assert measured["proscenium_instance_id"] == "e04-instance-001"
    assert measured["material"] and measured["execution"]["inference_operations"] == 0
    if data["readback"]:
        readback = data["readback"]
        assert data["readback_process"]["exit_code"] == 0
        assert (
            abs(readback["v2"]["height_m"] - 0.12) < 1e-5 and abs(readback["v2"]["base_z_m"]) < 1e-5
        )
        assert readback["topology_preserved"] and readback["maximum_uniform_shape_error_m"] < 2e-6
        assert readback["source_hashes_before"] == readback["source_hashes_after"]
        assert not readback["source_saved_by_inspector"] and not readback["rendered_by_inspector"]


def test_actual_checkpoint_publication_cas_and_original_authority_replay():
    data = result()
    version = data["version"]
    validate("VersionRecord", version)
    assert version["item_id"] == "m10-generated-asset" and version["version_id"] == "m10-asset-v2"
    assert version["source"]["entry_path"] == "assets/e04/asset-v2.blend"
    assert {"item_id": "m10-generated-asset", "version_id": "m10-asset-v1"} in version[
        "derived_from"
    ]
    replay = data["publication_replay"]
    assert (
        replay["caller"]["channel"] == "tool"
        and replay["caller"]["work_id"] == data["new_work"]["work_id"]
    )
    assert replay["caller"]["invocation_id"]
    assert replay["initial"][1] is False and replay["replay"][1] is True and replay["no_mutation"]
    item = next(row for row in replay["body"]["items"] if row["version_id"] == "m10-asset-v2")
    assert item["expected_current_version_id"] == "m10-asset-v1"
    assert item["source_commit"] == version["source_commit"]
    completed = [
        row["receipt"]
        for _, row in data["checkpoints"]
        if row.get("state") == "completed"
        and row.get("receipt", {}).get("source_commit") == version["source_commit"]
    ]
    assert completed
    validate("CheckpointReceipt", completed[0])
    origins = {row["destination"]: row["origin"] for row in version["source"]["files"]}
    assert origins["assets/e04/asset-v2.blend"]["sha256"] == data["measurements"]["output_sha256"]


def test_two_actual_production_previews_and_correct_v2_ui():
    data = result()
    assert len(data["previews"]) == 2
    assert {row["job"]["render_spec"]["purpose"] for row in data["previews"]} == {
        "proxy",
        "keyframe",
    }
    for row in data["previews"]:
        job, journals = row["job"], row["journals"]
        validate("JobRecord", job)
        assert job["state"] == "succeeded" and job["native_exit_code"] == 0
        assert (
            job["attempt_number"] == 1 and job["target"]["version"]["version_id"] == "m10-asset-v2"
        )
        assert job["render_spec"]["dimensions"] == {"width": 640, "height": 360}
        assert job["render_spec"]["samples"] == 8 and job["render_spec"]["device"] == "CPU"
        assert (
            journals["terminal.json"]["task_sha256"]
            == journals["renderer-verified.json"]["task_sha256"]
        )
        assert (
            journals["task.json"]["renderer_files_sha256"]
            == journals["renderer-verified.json"]["renderer_files_sha256"]
        )
        assert journals["task.json"]["config"]["threads"] == 4
    browser = data["browser"]
    assert browser["phase"] == "v2_visible"
    assert browser["v2_keyframe"]["width"] == 640 and browser["v2_keyframe"]["height"] == 360
    assert "SwiftShader" in browser["v2_renderer"]
    assert browser["v2_mapping"]["version_id"] == "m10-asset-v2"
    assert browser["v2_orbit"]["before"] != browser["v2_orbit"]["after"]


def test_originals_history_selection_budgets_and_cleanup_are_preserved():
    data = result()
    assert all(data["historical_records_preserved"].values())
    assert all(data["immutable_files_preserved"].values())
    assert (
        data["old_reports_preserved"]
        and data["e04_hashes_preserved"]
        and data["native_history_prefix_preserved"]
    )
    assert data["generation_reservations"] == []
    assert data["e04_generation_reservations"] == [
        ["e04-mesh-job", "e04-generation-task", "e04-generation-task", "consumed"]
    ]
    assert data["stack_seconds"] <= 600
    assert (
        data["headroom_samples"]
        and min(row["available_bytes"] for row in data["headroom_samples"]) >= 4 * 1024**3
    )
    assert data["cleanup"] and all(not row["alive"] for row in data["cleanup"])
    assert data["remaining_observed_owned_descendants"] == []
    assert data["resource_claims_after"] == []
    assert (
        data["old_frozen_job_inputs_preserved"] and data["old_source_closure_files_retained_in_v2"]
    )
    assert data["old_checkpoint_refs_and_git_ancestry_preserved"] and data["author_guard_unchanged"]


def test_nonempty_final_not_empty_tool_batch_settled_the_new_work_within_root_bound():
    data = result()
    assert data["new_work"]["state"] == "completed"
    assert data["nonempty_final_without_tools"]
    assert data["owner_stop_requested"] is False
    assert data["root_to_nonempty_final_seconds"] < 240
    completed = data["completion_record"]
    assert (
        completed["candidate"] and completed["request_id"] == data["new_requests"][0]["request_id"]
    )
    assert completed["work_id"] == data["new_work"]["work_id"]
    assert (
        data["durable_idle"]["idle"]
        and data["durable_idle"]["delivery_id"] == completed["delivery_id"]
    )
    measured = data["measurements"]["material_parameters"]
    assert measured["base_color"][2] > measured["base_color"][1] > measured["base_color"][0]
    assert measured["roughness"] > 0 and not measured["image_textures"]
