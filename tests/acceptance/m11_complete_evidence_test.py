"""Verify retained actual publication evidence. No SDK, browser or native application."""

import hashlib
import json
from pathlib import Path

from proscenium.contracts import canonical, validate

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m11-complete"


def load(name):
    return json.loads((EVIDENCE / name).read_text())


def test_exported_hashes():
    for name, digest in load("files.json").items():
        assert hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest() == digest


def test_real_publication_and_source_input_mapping():
    proof = load("publication.json")
    version = validate("VersionRecord", proof["version"])
    assert proof["item"]["current_version_id"] == "m11-playable-v1"
    assert proof["item"]["selected_version_id"] is None
    assert (
        version["kind"] == "unity_result" and version["publication_id"] == "m11-unity-publication"
    )
    assert version["source_commit"] == "8d49caaee980d18a140111e81b15a46c71e229ce"
    assert version["source"]["entry_path"] == version["unity"]["scene_path"]
    assert version["source"]["dependencies"] == [
        dict(item_id="m11-source", version_id="m11-source-v1")
    ]
    assert version["derived_from"] == version["source"]["dependencies"]
    assert len(version["source"]["files"]) == 77
    assert {f["destination"]: f["origin"]["sha256"] for f in version["source"]["files"]} == {
        f["path"]: f["sha256"] for f in load("checkpoint.json")["files"]
    }
    source = dict(version["source"])
    digest = source.pop("digest")
    assert hashlib.sha256(canonical(source)).hexdigest() == digest


def test_publication_is_real_native_tool_authority():
    proof = load("publication.json")
    invocation = proof["invocation"]
    assert invocation["native_id"] == "call_lCkKYkLWtfNHpdWXz9fGGxx2"
    assert invocation["model_id"] == "gpt-6.1-sol" and invocation["state"] == "completed"
    assert invocation["work_id"] == load("manifest.json")["work_id"]
    assert proof["version"]["actor"] == dict(kind="tool", id=invocation["native_id"])


def test_exact_original_authority_replay():
    replay = load("original-authority-replay.json")
    assert replay["caller"]["channel"] == "tool"
    assert (
        replay["caller"]["invocation_id"] == load("publication.json")["invocation"]["invocation_id"]
    )
    assert replay["records_unchanged"] and replay["replay"][0]["replayed"]
    assert replay["initial"][0]["replayed"] is False
    assert replay["body"]["publication_id"] == "m11-unity-publication"


def test_actual_final_then_turn_end_and_native_idle():
    proof = load("terminal-native-evidence.json")
    assert proof["final_message"]["data"]["content"].strip()
    assert proof["final_message"]["data"]["toolRequests"] == []
    assert proof["completion"]["public_terminal_message"] and proof["completion"]["candidate"]
    assert proof["turn_end"]["id"] == proof["completion"]["producer_event_id"]
    assert proof["turn_end"]["type"] in {"assistant.turn_end", "session.task_complete"}
    assert proof["idle_projection"]["idle"] and len(proof["actual_idle_callbacks"]) == 1
    assert proof["actual_idle_callbacks"][0]["type"] == "session.idle"
    assert proof["actual_idle_callbacks"][0]["attributed"]
    assert proof["work"]["state"] == "completed" and proof["work"]["state_revision"] == 12
    assert proof["root_elapsed_ms"] < 240000


def test_same_original_work_owner_ui_and_one_send():
    manifest = load("manifest.json")
    owner = load("owner-continue.json")
    assert owner["status"] == 202 and owner["command"]["expected_revision"] == 10
    assert owner["command"]["work_id"] == manifest["work_id"]
    assert owner["command"]["operation_id"] == manifest["continuation_operation"]
    assert manifest["sdk_sends"] == 1
    for key in (
        "new_queue_requests",
        "new_conversations",
        "new_children",
        "new_editors",
        "new_blender",
        "new_exports",
        "new_renders",
        "new_inference",
        "source_recommits",
    ):
        assert manifest[key] == 0
    allocation = load("session-allocation.json")
    assert (
        allocation["memory"] == dict(enabled=False) and allocation["enable_session_store"] is False
    )
    assert allocation["continue_pending_work"] is False


def test_owner_catalogue_current_version_and_no_render_claim():
    public = load("public-catalogue.json")
    assert public["viewport"] == dict(width=1920, height=1080, dpr=1)
    item = next(i for i in public["items"]["items"] if i["item_id"] == "m11-playable")
    version = next(
        v
        for v in public["versions"]["m11-playable"]["items"]
        if v["version_id"] == "m11-playable-v1"
    )
    assert item["current_version_id"] == version["version_id"]
    assert version == load("publication.json")["version"]
    assert version["preview_requests"] == [] and version["preview_job_ids"] == []
    assert (
        "M11 playable F01 Unity scene" in public["visible"]
        and "m11-playable-v1" in public["visible"]
    )


def test_honest_playmode_validation_association():
    proof = load("validation-separation.json")
    assert proof["generic_import"]["validation"]["status"] == "not_run"
    assert proof["operator"]["unity"]["validation"]["status"] == "passed"
    assert proof["published_unity"]["validation"]["status"] == "passed"
    assert (
        "generic import m11-import-job validation was not_run"
        in proof["published_unity"]["validation"]["notes"]
    )
    assert set(load("publication.json")["version"]["artifact_ids"]) == {
        "m11-playmode-attestation",
        "m11-playmode-xml",
        "m11-playmode-metrics",
    }


def test_preservation_and_truthful_historical_permission_transition():
    proof = load("preservation.json")
    for key in (
        "source_hashes_unchanged",
        "old_reports_unchanged",
        "git_index_unchanged",
        "old_bindings_unchanged",
        "old_decisions_unchanged",
        "old_versions_unchanged",
        "original_submissions_unchanged",
        "contexts_unchanged",
        "old_permission_reply_authority_delivery_preserved",
    ):
        assert proof[key]
    history = load("historical-permission-stage.json")
    assert history["owner_decisions_byte_identical"]
    assert len(history["only_cold_restoration_phase_changes"]) == 1
    change = history["only_cold_restoration_phase_changes"][0]
    assert (
        change["before"]["phase"] == "reply_prepared"
        and change["after"]["phase"] == "outcome_uncertain"
    )
    for row in (change["before"], change["after"]):
        assert (
            row["authority"] == "host"
            and row["reply"] == "reject"
            and row["delivery_state"] == "outcome_uncertain"
        )


def test_bounded_clean_shutdown_and_real_build():
    cleanup = load("cleanup.json")
    assert (
        cleanup["browser_reaped"]
        and cleanup["services_closed"]
        and cleanup["recorded_identities_gone"]
    )
    assert cleanup["sdk_sends"] == 1 and cleanup["stack_seconds"] < 600
    assert load("lifespan-closed.json") == dict(feature_lifespan_closed=True, errors={})
    assert load("send-headroom.json")["available_bytes"] >= 4 * 1024**3
    assert load("build-import-composition.json")["status"] == "passed"
    assert load("build-import-composition.json")["frontend_hashes"]
