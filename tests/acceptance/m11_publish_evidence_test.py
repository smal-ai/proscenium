"""Integrity checks of the real blocked publication continuation, not a milestone pass."""

import json
from pathlib import Path

from proscenium.contracts import validate

ROOT = Path(__file__).resolve().parents[2] / "docs/evidence/m11-publication"


def read(name):
    return json.loads((ROOT / name).read_text())


def test_original_ui_continuation_did_not_create_replacement_work_or_session():
    manifest = read("manifest.json")
    send = read("continuation-send.json")
    assert manifest["sdk_continuation_sends"] == 1 and manifest["new_queue_requests"] == 0
    assert send["session_id"] == manifest["session_id"] == "0cbee1a2-b5ed-4ab8-962b-ccf1d9f8a454"
    state = read("runtime-state.json")
    assert len(state["requests"]) == 2
    work = next(w for w in state["work"] if w["work_id"] == manifest["work_id"])
    assert (
        work["originating_request_id"]
        == manifest["request_id"]
        == "1a914aa8-2bbc-461a-a87d-1bb7431de02c"
    )
    assert (
        read("owner-reconcile.json")["status"] == 202
        and read("owner-continue.json")["status"] == 202
    )


def test_scene_meta_source_reports_git_and_native_results_were_not_changed_to_publish():
    data = read("preservation.json")
    assert data["source_files_identical"] and data["old_reports_identical"]
    assert data["jobs_before"] == data["jobs_after"]
    assert data["head_before"] == data["head_after"] == "3297ae647200270ebdc102630e7cfbfce2f4bfd1"
    assert (
        data["refs_before"] == data["refs_after"]
        and data["git_index_before"] == data["git_index_after"]
    )
    manifest = read("manifest.json")
    assert all(
        manifest[key] == 0
        for key in ("new_product_children", "new_unity", "new_blender", "new_renderers")
    )


def test_precise_permission_false_positive_is_path_data_not_installation():
    blocker = read("permission-blocker.json")
    request = blocker["request"]
    assert request["kind"] == "custom-tool" and request["toolName"] == "checkpoint_sources"
    validate("CheckpointRequest", request["args"])
    assert (
        blocker["matched_text"] == "install" and "tool-install.json" in blocker["matched_context"]
    )
    assert all(
        p.startswith("unity/M11/")
        and not any(
            part in p.split("/")
            for part in ("Library", "Logs", "Temp", "UserSettings", ".proscenium")
        )
        for p in request["args"]["paths"]
    )
    decision = blocker["decision"]
    assert (
        decision["state"] == "pending"
        and decision["response"] is None
        and decision["resolved_by"] is None
    )


def test_no_manual_publication_or_false_completion_is_claimed():
    manifest = read("manifest.json")
    assert manifest["status"] == "blocked_permission_false_positive"
    assert manifest["unity_publication"] is False and manifest["checkpoint_effect"] is False
    assert not any(v["kind"] == "unity_result" for v in read("runtime-state.json")["versions"])
    assert read("continue-result.json")["record"]["receipt"]["state"] != "completed"
    assert all(not row["original_alive"] for row in read("process-identities.json").values())
    assert read("cleanup.json")["browser_reaped"] and read("cleanup.json")["services_closed"]
