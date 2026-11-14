"""M09 actual-response regressions and retained real evidence, not a new inference run."""

import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator
from proscenium.api.errors import DomainError
from proscenium.providers.vlm import VLMClient, findings, result_schema

EVIDENCE = Path(__file__).parents[2] / "docs/evidence/m09"


def load(name):
    return json.loads((EVIDENCE / name).read_text())


def test_actual_native_candidate_is_rejected_not_relabelled_as_measured_telemetry():
    request = load("fixture-identities.json")["native_review"]
    responses = [r for r in load("vlm-responses-redacted.json") if r["phase"] == "response"]
    candidate = json.loads(responses[1]["choices"][0]["content"])
    assert candidate["findings"][1]["evidence"][0]["kind"] == "measured"
    assert (
        candidate["findings"][1]["evidence"][0]["artifact_id"]
        == request["targets"][0]["artifact_id"]
    )
    with pytest.raises(DomainError):
        findings(request, candidate)
    assert not Draft202012Validator(result_schema(request)).is_valid(candidate)


def test_refined_generation_schema_separates_visual_and_measured_evidence():
    request = load("fixture-identities.json")["native_review"]
    candidate = json.loads(
        [r for r in load("vlm-responses-redacted.json") if r["phase"] == "response"][1]["choices"][
            0
        ]["content"]
    )
    # Synthetic correction for schema regression only. Never submitted/stored as actual findings.
    synthetic = copy.deepcopy(candidate)
    synthetic["findings"][1]["evidence"][0]["artifact_id"] = request["telemetry_artifact_ids"][0]
    assert Draft202012Validator(result_schema(request)).is_valid(synthetic)
    assert len(findings(request, synthetic)) == 2
    no_telemetry = {**request, "telemetry_artifact_ids": []}
    assert not Draft202012Validator(result_schema(no_telemetry)).is_valid(synthetic)


@pytest.mark.parametrize("reasoning", [{"allowed_options": ["off", "on"]}, {}])
def test_reasoning_off_only_when_exact_loaded_model_advertises_support(reasoning):
    request = load("fixture-identities.json")["native_review"]
    calls = []

    def handle(incoming):
        calls.append(incoming)
        if incoming.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "capabilities": {"vision": True, "reasoning": reasoning},
                            "loaded_instances": [{"id": "fixture-vision"}],
                        }
                    ]
                },
            )
        body = json.loads(incoming.content)
        assert body["max_tokens"] == 512
        assert (body.get("reasoning_effort") == "none") is bool(reasoning)
        assert body["response_format"]["json_schema"]["schema"] == result_schema(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"findings":[]}'}}]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert (
            VLMClient("http://offline.invalid", client).review("fixture-vision", request, [], {})
            == []
        )
    assert len(calls) == 2


def test_actual_truncation_stays_failed_and_does_not_parse_reasoning():
    response = [r for r in load("vlm-responses-redacted.json") if r["phase"] == "response"][0]
    assert response["choices"][0]["finish_reason"] == "length"
    assert response["choices"][0]["content"] == ""
    assert response["usage"]["completion_tokens"] == 512
    assert response["usage"]["completion_tokens_details"]["reasoning_tokens"] == 509
    state = load("public-collect.json")
    assert all(not f["items"] for f in state["findings"].values())
    assert load("result.json")["result"] == "BLOCKED_NOT_COMPLETE"


def test_real_origins_exact_attribution_and_ticket_only_native_boundary():
    owner = load("public-owner.json")
    native = load("public-native.json")
    owner_job = next(j for j in owner["jobs"]["items"] if j["job_id"] == owner["ticket"]["job_id"])
    assert (
        owner_job["work_id"] is None
        and owner_job["owner_operation_id"] == owner["ticket"]["review_id"]
    )
    assert not owner["requests"]["items"]
    native_job = next(
        j for j in native["jobs"]["items"] if j["job_id"] == native["ticket"]["job_id"]
    )
    assert native_job["work_id"] == native["request"]["receipt"]["work_id"]
    assert native_job["owner_operation_id"] is None
    returned = load("native-tool-ticket.json")[0]
    ticket = json.loads(returned["result"])
    assert set(ticket) == {"schema_version", "job_id", "review_id", "status"}
    assert hashlib.sha256(returned["result"].encode()).hexdigest() == returned["sha256"]
    assert ticket["job_id"] == native_job["job_id"]
    assert len(load("native-delivery.json")) == 1
    commands = load("public-owner-commands.json")
    owner_command = next(
        c for c in commands if c["phase"] == "owner" and c["path"].endswith("/reviews")
    )
    assert (
        owner_command["request"]["targets"][0]["displayed_sha256"]
        == load("fixture-identities.json")["artifact"]["sha256"]
    )
    final = load("public-collect.json")
    for job in final["jobs"]["items"]:
        attempt = final["attempts"][job["job_id"]]["items"][0]
        assert (
            attempt["attempt_id"] == job["attempt_id"]
            and attempt["input_digest"] == job["input_digest"]
        )
        worker_events = [
            e
            for e in final["events"]
            if e["event_type"] == "job.state_changed"
            and e["payload"]["job_id"] == job["job_id"]
            and e["actor"]["kind"] == "worker"
        ]
        assert worker_events


def test_gemma_released_qwen_preserved_and_no_false_approval():
    cleanup = load("model-cleanup.json")
    assert cleanup["saved_profile_unchanged"] and cleanup["qwen_context_ttl_last_used_unchanged"]
    assert [m["identifier"] for m in cleanup["after"]] == ["qwen3.8-27b-uncensored"]
    assert load("result.json")["counts"]["approved_submissions"] == 0
    assert len([r for r in load("vlm-responses-redacted.json") if r["phase"] == "dispatch"]) == 2
    unavailable = load("public-unavailable.json")
    job = next(
        j for j in unavailable["jobs"]["items"] if j["job_id"] == unavailable["ticket"]["job_id"]
    )
    assert job["state"] == "failed" and job["error_code"] == "capability_unavailable"
    assert load("public-collect.json")["images"] == [
        {"complete": True, "width": 640, "height": 360}
    ]


def test_export_has_no_private_reasoning_and_full_hd_screenshots_are_actual_pngs():
    from PIL import Image

    assert "reasoning_content" not in (EVIDENCE / "vlm-responses-redacted.json").read_text()
    for name in ("owner", "native", "unavailable", "refresh"):
        assert load(f"public-{name}.json")["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
        with Image.open(EVIDENCE / f"browser-{name}.png") as image:
            assert image.size == (1920, 1080)
    for name, metadata in load("manifest.json").items():
        data = (EVIDENCE / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == metadata["sha256"]
