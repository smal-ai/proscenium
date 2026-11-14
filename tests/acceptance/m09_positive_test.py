"""Retained actual M09 positive journey. These tests execute no model/native inference."""

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from PIL import Image
from proscenium.providers.vlm import result_schema

EVIDENCE = Path(__file__).parents[2] / "docs/evidence/m09-positive"


def load(name):
    return json.loads((EVIDENCE / name).read_text())


def test_actual_two_positive_origins_and_attribution():
    owner, native = load("public-owner.json"), load("public-native.json")
    assert owner["settled"]["status"] == native["ticket"]["status"] == "ready"
    assert owner["state"]["requests"]["items"] == []
    for value, ticket in ((owner, owner["ticket"]), (native, native["ticket"])):
        state = value["state"]
        job = next(j for j in state["jobs"]["items"] if j["job_id"] == ticket["job_id"])
        assert job["state"] == "succeeded"
        attempt = state["attempts"][job["job_id"]]["items"][0]
        assert (
            attempt["attempt_id"] == job["attempt_id"]
            and attempt["input_digest"] == job["input_digest"]
        )
        if value is owner:
            assert job["work_id"] is None and job["owner_operation_id"] == ticket["review_id"]
        else:
            assert (
                job["work_id"] == native["request"]["receipt"]["work_id"]
                and job["owner_operation_id"] is None
            )
        assert len(state["findings"][ticket["review_id"]]["items"]) == 2


def test_actual_fixed_reasoning_and_evidence_schema_succeed_within_allocation():
    rows = load("vlm-observations.json")
    dispatched = [r for r in rows if r["phase"] == "dispatch"]
    responses = [r for r in rows if r["phase"] == "response"]
    assert len(dispatched) == len(responses) == 2
    for sent, response in zip(dispatched, responses, strict=True):
        assert sent["max_tokens"] == 512 and sent["reasoning_effort"] == "none"
        assert sent["mem_available"] >= 4 * 1024**3
        assert response["status"] == 200 and response["seconds"] <= 120
        choice = response["body"]["choices"][0]
        assert choice["finish_reason"] == "stop" and not choice["message"]["reasoning_present"]
        assert response["body"]["usage"]["completion_tokens_details"]["reasoning_tokens"] == 0
        assert Draft202012Validator(sent["schema"]).is_valid(
            json.loads(choice["message"]["content"])
        )
    native_schema = result_schema(load("fixture-identities.json")["native_review"])
    assert dispatched[1]["schema"] == native_schema


def test_actual_native_findings_distinguish_measured_and_visual_sources():
    rows = load("public-native.json")["state"]["findings"]["m09-positive-native-review"]["items"]
    kinds = {e["kind"] for f in rows for e in f["evidence"]}
    assert kinds == {"visual", "measured"}
    for finding in rows:
        assert finding["revision"] == 1 and finding["state"] == "unreviewed"
        assert finding["target"] == load("fixture-identities.json")["target"]
        for evidence in finding["evidence"]:
            assert evidence["artifact_id"] == (
                "m09-clean-target" if evidence["kind"] == "visual" else "m09-pixel-telemetry"
            )


def test_amended_subset_and_rejection_are_real_revisioned_owner_changes():
    approved = load("public-approve.json")
    selected, declined = approved["selected"], approved["declined"]
    rows = {
        f["finding_id"]: f
        for f in approved["state"]["findings"]["m09-positive-native-review"]["items"]
    }
    assert (
        rows[selected["finding_id"]]["state"] == "submitted"
        and rows[selected["finding_id"]]["revision"] == 2
    )
    assert (
        rows[declined["finding_id"]]["state"] == "rejected"
        and rows[declined["finding_id"]]["revision"] == 2
    )
    assert rows[selected["finding_id"]]["observation"] == selected["observation"]
    assert (
        approved["amended"] != selected["observation"]
        and approved["amended"] != selected["recommendation"]
    )
    assert approved["rejection"]["state_revision"] == 2


def test_actual_second_sdk_send_is_exactly_approved_text_target_and_only_clean_image():
    approved, native = load("public-approve.json"), load("native-delivery.json")
    assert len(native) == 2
    second = native[1]
    assert second["prompt"] == second["display_prompt"] == approved["amended"]
    assert second["client_turn_id"] == approved["admission"]["request_id"]
    payload = next(a["payload"] for a in second["attachments"] if a["type"] == "extension_context")
    context = approved["state"]["contexts"][payload["context_id"]]
    assert len(payload["approved_findings"]) == 1
    assert payload["approved_findings"] == context["approved_findings"]
    value = payload["approved_findings"][0]
    assert value == dict(
        finding_id=approved["selected"]["finding_id"],
        finding_revision=1,
        approved_text=approved["amended"],
        target=approved["selected"]["target"],
    )
    assert payload["selection"]["target"] == value["target"] == context["target"]
    assert approved["declined"]["observation"] not in json.dumps(second)
    assert approved["declined"]["finding_id"] not in json.dumps(second)
    assert context["attachments"] == [{"artifact_id": "m09-clean-target", "role": "render"}]
    assert (
        len([a for a in second["attachments"] if a["type"] == "file"]) == 2
    )  # Disclosed role duplication, identical bytes.
    assert {a["sha256"] for a in payload["attached_files"]} == {value["target"]["displayed_sha256"]}


def test_only_ticket_crosses_triggering_tool_boundary_and_queue_replays_once():
    ticket = load("native-tool-ticket.json")
    assert len(ticket) == 1
    body = json.loads(ticket[0]["result"])
    assert set(body) == {"schema_version", "job_id", "review_id", "status"}
    assert hashlib.sha256(ticket[0]["result"].encode()).hexdigest() == ticket[0]["sha256"]
    approved = load("public-approve.json")
    assert approved["replay"]["status"] == 200 and approved["replay"]["receipt"]["replayed"]
    assert (
        approved["replay"]["receipt"]["native_message_id"]
        == approved["request"]["receipt"]["native_message_id"]
    )
    assert len(load("public-collect.json")["state"]["requests"]["items"]) == 2
    assert len(load("native-delivery.json")) == 2


def test_native_acknowledged_selected_revision_and_no_source_or_geometry_edits():
    approved = load("public-approve.json")
    messages = [
        e["payload"]["text"]
        for e in load("public-collect.json")["state"]["events"]
        if e["event_type"] == "assistant.completed"
        and e["work_id"] == approved["request"]["receipt"]["work_id"]
    ]
    ack = next(m for m in messages if "APPROVED_FINDING_RECEIVED" in m)
    assert approved["selected"]["finding_id"] in ack and "finding_revision: 1" in ack
    assert approved["selected"]["target"]["displayed_sha256"] in ack
    assert load("verification.json")["source_unchanged"]
    tools = load("native-tool-arguments.json")
    assert {t["data"]["toolName"] for t in tools} <= {
        "request_vlm_review",
        "get_artifact",
        "view",
        "bash",
    }
    for value in tools:
        if value["data"]["toolName"] == "bash":
            command = value["data"]["arguments"]["command"]
            assert command.startswith(("sha256sum ", "python - <<'PY'"))
            assert "open(" not in command or "Image.open(" in command
            assert "write" not in command and "bpy" not in command and "blender" not in command


def test_owned_model_cleanup_unavailable_review_and_normal_feedback_save():
    cleanup, final = load("model-cleanup.json"), load("public-collect.json")
    assert (
        cleanup["last_use_correlated_with_owned_response"]
        and cleanup["qwen_context_ttl_last_used_unchanged"]
        and cleanup["saved_profile_unchanged"]
    )
    assert not any(m["identifier"] == "google/gemma-4-e4b" for m in cleanup["after"])
    unavailable = load("public-unavailable.json")
    job = next(
        j for j in final["state"]["jobs"]["items"] if j["job_id"] == unavailable["ticket"]["job_id"]
    )
    assert job["state"] == "failed" and job["error_code"] == "capability_unavailable"
    assert (
        unavailable["annotation_ack"]["state"] == "saved"
        and unavailable["annotation_ack"]["state_revision"] == 1
    )
    assert len(final["state"]["annotations"]["items"]) == 1
    assert final["images"] == [{"complete": True, "width": 640, "height": 360}]
    host = load("host-cleanup.json")
    assert (
        host["children_records"] == 0
        and host["resource_claims"] == []
        and host["gemma_not_reloaded"]
    )


def test_actual_full_hd_screenshots_and_untouched_failed_run_manifest():
    for name in ("owner", "native", "approve", "unavailable", "collect"):
        assert load(f"public-{name}.json")["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
        with Image.open(EVIDENCE / f"browser-{name}.png") as image:
            assert image.size == (1920, 1080)
    runtime = load("runtime-identities.json")
    assert runtime["old_failed_manifest_sha256"] == runtime["old_failed_manifest_base_sha256"]
    for name, metadata in load("manifest.json").items():
        raw = (EVIDENCE / name).read_bytes()
        assert (
            hashlib.sha256(raw).hexdigest() == metadata["sha256"] and len(raw) == metadata["bytes"]
        )
