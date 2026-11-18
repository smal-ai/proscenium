"""Unit and HTTP/tool tests for VLM 16K/medium critique, work-scoped self-review, escalation and Critic inbox."""

import io
import json
import threading
import time
from dataclasses import replace

import httpx
import pytest
import test_worker_capabilities_and_publication as shared
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
from proscenium.api.dependencies import owner
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.providers.router import router as provider_router
from proscenium.providers.service import ProviderService
from proscenium.providers.vlm import (
    DEFAULT_VLM_MAX_TOKENS,
    DEFAULT_VLM_REASONING_EFFORT,
    RUBRIC_REVISION,
    VLM_SYSTEM_PROMPT,
    VLMClient,
    select_reasoning,
)
from proscenium.reviews.routes import router as review_router
from proscenium.reviews.store import install as install_reviews
from proscenium.sources.common import sha
from test_worker_capabilities_and_publication import PROJECT, SESSION, TASK, WORK, invoke


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    shared.offline.__wrapped__(monkeypatch)


def make_png(colour: tuple[int, int, int] = (40, 90, 140)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (64, 64), colour)
    for x in range(0, 64, 8):
        img.putpixel((x, x), (220, 180, 90))
    img.save(buf, format="PNG")
    return buf.getvalue()


def register_png(
    runtime, artifact_id: str, purpose: str = "keyframe", colour=(40, 90, 140)
) -> dict:
    raw = make_png(colour)
    artifacts = runtime.services.extension("artifacts")
    artifact = artifacts.promote(
        PROJECT,
        artifact_id,
        raw,
        kind="image",
        purpose=purpose,
        media_type="image/png",
    )
    with runtime.store.transaction() as tx:
        artifacts.put(tx, artifact)
    return {
        "origin": "reference" if purpose == "reference" else "concept",
        "item_id": None,
        "version_id": None,
        "shot_id": None,
        "render_job_id": None,
        "sequence_frame": None,
        "source_frame": None,
        "artifact_id": artifact["artifact_id"],
        "media_artifact_id": None,
        "displayed_sha256": artifact["sha256"],
        "dimensions": {"width": 64, "height": 64},
    }


@pytest.fixture
def vlm_runtime(tmp_path):
    for rt in shared.runtime.__wrapped__(tmp_path):
        reviews = install_reviews(rt.services)
        rt.services.settings = replace(
            rt.services.settings,
            services=(
                dict(
                    provider_id="comfy",
                    endpoint="http://comfy.invalid",
                    capabilities=["image"],
                    model_id=None,
                    available=None,
                ),
                dict(
                    provider_id="local-vlm",
                    endpoint="http://vlm.invalid",
                    capabilities=["vlm"],
                    model_id="qwen/qwen3.8-27b",
                    available=True,
                ),
            ),
            provider_options={
                "review_provider_id": "local-vlm",
                "vlm_max_tokens": DEFAULT_VLM_MAX_TOKENS,
                "vlm_reasoning_effort": DEFAULT_VLM_REASONING_EFFORT,
            },
        )
        vlm_state = {
            "allowed_options": ["off", "low", "medium", "xhigh", "on"],
            "requests": [],
            "response": {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"findings": []})},
                    }
                ],
                "usage": {
                    "prompt_tokens": 1240,
                    "completion_tokens": 310,
                    "completion_tokens_details": {"reasoning_tokens": 210},
                },
            },
            "raise_exc": None,
        }

        def transport(req: httpx.Request) -> httpx.Response:
            vlm_state["requests"].append(req)
            if req.url.path == "/api/v1/models":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "key": "qwen/qwen3.8-27b",
                                "capabilities": {
                                    "vision": True,
                                    "reasoning": {"allowed_options": vlm_state["allowed_options"]},
                                },
                                "loaded_instances": [{"id": "qwen/qwen3.8-27b"}],
                            }
                        ]
                    },
                )
            if req.url.path == "/v1/chat/completions":
                if vlm_state["raise_exc"] is not None:
                    raise vlm_state["raise_exc"]
                return httpx.Response(200, json=vlm_state["response"])
            return httpx.Response(200, json={"data": []})

        rt.provider.client.close()
        vlm_client = httpx.Client(transport=httpx.MockTransport(transport))
        provider = ProviderService(rt.services, client=vlm_client)
        rt.services.extensions["providers"] = provider
        rt.services.extensions["vlm"] = SimpleNamespaceReviewAlias(provider)
        rt.provider = provider
        rt.reviews = reviews
        rt.vlm_state = vlm_state
        try:
            yield rt
        finally:
            vlm_client.close()


class SimpleNamespaceReviewAlias:
    def __init__(self, provider: ProviderService):
        self.provider = provider

    def request(self, caller: Caller, body: dict):
        return self.provider.request_review(caller, body)

    def escalate(self, caller: Caller, body: dict):
        return self.provider.reviews.escalate(caller, body)


def test_vlm_defaults_reasoning_selection_interleaved_labels_and_reference_cross_citation(
    vlm_runtime, tmp_path
):
    assert DEFAULT_VLM_MAX_TOKENS == 16384
    assert DEFAULT_VLM_REASONING_EFFORT == "medium"
    assert select_reasoning(
        {"reasoning": {"allowed_options": ["off", "low", "medium", "xhigh", "on"]}}
    ) == ({"reasoning_effort": "medium"}, "medium")
    assert select_reasoning(
        {"reasoning": {"allowed_options": ["off", "low", "on"]}}, preferred="off"
    ) == ({"reasoning_effort": "off"}, "off")
    assert select_reasoning(
        {"reasoning": {"allowed_options": ["none", "low"]}}, preferred="off"
    ) == ({"reasoning_effort": "none"}, "none")
    assert select_reasoning({"vision": True}) == ({}, None)

    for bad_opts in (
        {"vlm_max_tokens": 16385},
        {"vlm_reasoning_effort": "ultra"},
        {"vlm_timeout": 601},
    ):
        from types import SimpleNamespace

        bad_services = SimpleNamespace(
            **{
                **vlm_runtime.services.__dict__,
                "settings": replace(vlm_runtime.services.settings, provider_options=bad_opts),
            }
        )
        with pytest.raises(ValueError):
            ProviderService(bad_services, client=vlm_runtime.provider.client)

    ref_target = register_png(vlm_runtime, "art-ref", purpose="reference", colour=(30, 50, 70))
    cand_target = register_png(vlm_runtime, "art-cand", purpose="inspection", colour=(80, 40, 40))

    vlm_runtime.vlm_state["response"] = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "findings": [
                                {
                                    "target_index": 1,
                                    "observation": "Dish mount floats 1m above the dorsal spine compared with the reference anchor.",
                                    "recommendation": "Lower the dish collar until it seats flush against the dorsal spine while preserving camera framing.",
                                    "evidence": [
                                        {
                                            "kind": "visual",
                                            "artifact_id": "art-cand",
                                            "description": "Visible gap beneath dish collar in upper-centre silhouette.",
                                        },
                                        {
                                            "kind": "visual",
                                            "artifact_id": "art-ref",
                                            "description": "Reference anchor shows collar bolted directly onto the spine.",
                                        },
                                    ],
                                }
                            ]
                        }
                    )
                },
            }
        ],
        "usage": {
            "prompt_tokens": 2048,
            "completion_tokens": 512,
            "completion_tokens_details": {"reasoning_tokens": 390},
        },
    }

    ticket, status = invoke(
        vlm_runtime,
        "request_vlm_review",
        {
            "schema_version": "1.0",
            "review_id": "rev-cross-cite",
            "project_id": PROJECT,
            "title": "Relay collar continuity",
            "targets": [ref_target, cand_target],
            "criteria": "Verify dish collar seats flush against the dorsal spine matching the reference.",
            "telemetry_artifact_ids": [],
        },
        identity="call-req-1",
    )
    assert status == "success"
    assert ticket["awaiting_human"] is False
    assert ticket["human_review_state"] == "not_requested"
    assert ticket["title"] == "Relay collar continuity"

    pumped = vlm_runtime.provider.pump_reviews(PROJECT)
    assert len(pumped) == 1 and pumped[0]["state"] == "succeeded"

    chat_reqs = [
        r for r in vlm_runtime.vlm_state["requests"] if r.url.path == "/v1/chat/completions"
    ]
    assert len(chat_reqs) == 1
    sent = json.loads(chat_reqs[0].content)
    assert sent["max_tokens"] == 16384
    assert sent["reasoning_effort"] == "medium"
    assert sent["messages"][0] == {"role": "system", "content": VLM_SYSTEM_PROMPT}
    for lens in (
        "1. Subject & structural fidelity",
        "2. Multi-frame & reference continuity",
        "3. Framing & scale",
        "4. Lighting & materials",
        "5. Visible generation or render artefacts",
    ):
        assert lens in VLM_SYSTEM_PROMPT

    user_parts = sent["messages"][1]["content"]
    assert [part["type"] for part in user_parts] == [
        "text",
        "text",
        "image_url",
        "text",
        "image_url",
    ]
    assert json.loads(user_parts[1]["text"]) == {
        "target_index": 0,
        "role": "reference_anchor",
        "artifact_id": "art-ref",
        "origin": "reference",
        "purpose": "reference",
    }
    assert json.loads(user_parts[3]["text"]) == {
        "target_index": 1,
        "role": "candidate",
        "artifact_id": "art-cand",
        "origin": "concept",
        "purpose": "inspection",
    }
    schema_items = sent["response_format"]["json_schema"]["schema"]["properties"]["findings"][
        "items"
    ]
    assert schema_items["properties"]["target_index"]["enum"] == [1]

    detail, read_status = invoke(
        vlm_runtime,
        "get_vlm_review",
        {"review_id": "rev-cross-cite", "wait_seconds": 0},
        identity="call-get-1",
    )
    assert read_status == "success"
    assert detail["ticket"]["status"] == "ready"
    assert detail["ticket"]["finding_count"] == 1
    assert detail["ticket"]["awaiting_human"] is False
    assert len(detail["findings"]) == 1
    assert detail["findings"][0]["target"]["artifact_id"] == "art-cand"
    assert [e["artifact_id"] for e in detail["findings"][0]["evidence"]] == ["art-cand", "art-ref"]
    assert detail["diagnostics"] == {
        "finish_reason": "stop",
        "prompt_tokens": 2048,
        "completion_tokens": 512,
        "reasoning_tokens": 390,
        "reasoning_effort": "medium",
        "rubric_revision": RUBRIC_REVISION,
        "error_code": None,
    }

    # Placeholder "Pass" findings and truncated finish_reason="length" are rejected cleanly.
    vlm_client = VLMClient("http://vlm.invalid", vlm_runtime.provider.client)
    vlm_runtime.vlm_state["response"]["choices"][0] = {
        "finish_reason": "stop",
        "message": {
            "content": json.dumps(
                {
                    "findings": [
                        {
                            "target_index": 1,
                            "observation": "Looks good.",
                            "recommendation": "Pass",
                            "evidence": [
                                {
                                    "kind": "visual",
                                    "artifact_id": "art-cand",
                                    "description": "Clean.",
                                }
                            ],
                        }
                    ]
                }
            )
        },
    }
    with pytest.raises(DomainError) as pass_exc:
        vlm_client.review_with_diagnostics(
            "qwen/qwen3.8-27b",
            {
                "review_id": "rev-pass-reject",
                "project_id": PROJECT,
                "targets": [ref_target, cand_target],
                "criteria": "Check structure.",
                "telemetry_artifact_ids": [],
            },
            [make_png(), make_png()],
            {},
        )
    assert pass_exc.value.code == "invalid_request"

    vlm_runtime.vlm_state["response"]["choices"][0]["finish_reason"] = "length"
    with pytest.raises(DomainError) as trunc_exc:
        vlm_client.review_with_diagnostics(
            "qwen/qwen3.8-27b",
            {
                "review_id": "rev-trunc",
                "project_id": PROJECT,
                "targets": [cand_target],
                "criteria": "Check structure.",
                "telemetry_artifact_ids": [],
            },
            [make_png()],
            {},
        )
    assert trunc_exc.value.code == "invalid_request"
    assert trunc_exc.value.details["diagnostics"]["finish_reason"] == "length"


def test_pump_reviews_runs_outside_comfy_lock_and_records_timeout_uncertainty(vlm_runtime):
    cand = register_png(vlm_runtime, "art-lock-test", purpose="keyframe")
    invoke(
        vlm_runtime,
        "request_vlm_review",
        {
            "schema_version": "1.0",
            "review_id": "rev-nonblocking",
            "project_id": PROJECT,
            "targets": [cand],
            "criteria": "Verify silhouette readability.",
            "telemetry_artifact_ids": [],
        },
        identity="call-nb-1",
    )

    comfy_lock_held = threading.Event()
    release_comfy_lock = threading.Event()

    def hold_comfy_lock():
        with vlm_runtime.provider._lock:
            comfy_lock_held.set()
            release_comfy_lock.wait(timeout=5)

    holder = threading.Thread(target=hold_comfy_lock, daemon=True)
    holder.start()
    assert comfy_lock_held.wait(timeout=2)
    try:
        pumped = vlm_runtime.provider.pump_reviews(PROJECT)
        assert len(pumped) == 1
        assert pumped[0]["state"] == "succeeded"
    finally:
        release_comfy_lock.set()
        holder.join(timeout=2)

    # Timeout during VLM HTTP POST -> outcome_uncertain, never replayed on subsequent pump_reviews.
    vlm_runtime.vlm_state["raise_exc"] = httpx.ReadTimeout("LM Studio timed out")
    invoke(
        vlm_runtime,
        "request_vlm_review",
        {
            "schema_version": "1.0",
            "review_id": "rev-timeout",
            "project_id": PROJECT,
            "targets": [cand],
            "criteria": "Verify rim lighting.",
            "telemetry_artifact_ids": [],
        },
        identity="call-timeout-1",
    )
    uncertain = vlm_runtime.provider.pump_reviews(PROJECT)
    assert len(uncertain) == 1
    assert uncertain[0]["state"] == "outcome_uncertain"
    assert vlm_runtime.provider.pump_reviews(PROJECT) == []

    detail, _ = invoke(
        vlm_runtime,
        "get_vlm_review",
        {"review_id": "rev-timeout", "wait_seconds": 0},
        identity="call-timeout-read",
    )
    assert detail["ticket"]["error_code"] == "outcome_uncertain"
    assert detail["ticket"]["awaiting_human"] is False

    # Escalating an uncertain review with an explicit human question surfaces it in Pending.
    escalated, esc_status = invoke(
        vlm_runtime,
        "escalate_vlm_review",
        {
            "schema_version": "1.0",
            "operation_id": "op-esc-timeout",
            "project_id": PROJECT,
            "review_id": "rev-timeout",
            "question": "VLM timed out; does the rim light look too hot to your eye?",
        },
        identity="call-timeout-esc",
    )
    assert esc_status == "success"
    assert escalated["awaiting_human"] is True
    assert escalated["human_review_state"] == "pending"


def test_get_vlm_review_work_scoping_stop_interrupted_wait_and_idempotent_escalation(vlm_runtime):
    cand = register_png(vlm_runtime, "art-scope", purpose="keyframe")
    owner_caller = Caller(channel="owner", subject_id="owner")
    owner_ticket, _ = vlm_runtime.provider.request_review(
        owner_caller,
        {
            "schema_version": "1.0",
            "review_id": "rev-owner-private",
            "project_id": PROJECT,
            "title": "Owner check",
            "targets": [cand],
            "criteria": "Owner private review.",
            "telemetry_artifact_ids": [],
        },
    )
    assert owner_ticket["awaiting_human"] is True

    denied_payload, denied_type = invoke(
        vlm_runtime,
        "get_vlm_review",
        {"review_id": "rev-owner-private", "wait_seconds": 0},
        identity="call-deny-owner",
    )
    assert denied_type == "failure"
    assert denied_payload["error"] == "scope_denied"

    with vlm_runtime.store.transaction() as tx:
        tx.put(
            "work",
            "work-two",
            dict(
                schema_version="1.0",
                project_id=PROJECT,
                work_id="work-two",
                originating_request_id="request-one",
                directive_request_ids=[],
                state="running",
                state_revision=0,
                native_session_id=SESSION,
                job_ids=[],
                generation_task_id=TASK,
            ),
            schema="WorkRecord",
        )
    other_caller = Caller(
        channel="tool",
        subject_id="tool",
        project_id=PROJECT,
        work_id="work-two",
        owner_epoch="epoch",
        operations=("requestToolReview",),
    )
    vlm_runtime.provider.request_review(
        other_caller,
        {
            "schema_version": "1.0",
            "review_id": "rev-other-work",
            "project_id": PROJECT,
            "targets": [cand],
            "criteria": "Other work review.",
            "telemetry_artifact_ids": [],
        },
    )
    denied_other, denied_other_type = invoke(
        vlm_runtime,
        "get_vlm_review",
        {"review_id": "rev-other-work", "wait_seconds": 0},
        identity="call-deny-other",
    )
    assert denied_other_type == "failure"
    assert denied_other["error"] == "scope_denied"

    # Active work bounded wait interrupted promptly by Stop.
    invoke(
        vlm_runtime,
        "request_vlm_review",
        {
            "schema_version": "1.0",
            "review_id": "rev-stop-wait",
            "project_id": PROJECT,
            "targets": [cand],
            "criteria": "Wait interruption check.",
            "telemetry_artifact_ids": [],
        },
        identity="call-stop-req",
    )

    def trigger_stop():
        time.sleep(0.15)
        with vlm_runtime.store.transaction() as tx:
            work = tx.require("work", WORK)
            tx.put(
                "work",
                WORK,
                {**work, "state": "stop_requested", "state_revision": work["state_revision"] + 1},
                expected_revision=tx.revision("work", WORK),
                schema="WorkRecord",
            )

    stopper = threading.Thread(target=trigger_stop, daemon=True)
    t0 = time.monotonic()
    stopper.start()
    waited_detail, waited_type = invoke(
        vlm_runtime,
        "get_vlm_review",
        {"review_id": "rev-stop-wait", "wait_seconds": 20},
        identity="call-stop-wait",
    )
    stopper.join(timeout=2)
    elapsed = time.monotonic() - t0
    assert waited_type == "success"
    assert waited_detail["ticket"]["status"] == "pending"
    assert elapsed < 3.0

    # Restore running state and test escalation idempotency + conflict guard.
    with vlm_runtime.store.transaction() as tx:
        work = tx.require("work", WORK)
        tx.put(
            "work",
            WORK,
            {**work, "state": "running", "state_revision": work["state_revision"] + 1},
            expected_revision=tx.revision("work", WORK),
            schema="WorkRecord",
        )

    esc1, esc1_type = invoke(
        vlm_runtime,
        "escalate_vlm_review",
        {
            "schema_version": "1.0",
            "operation_id": "op-esc-1",
            "project_id": PROJECT,
            "review_id": "rev-stop-wait",
            "title": "Framing choice",
            "question": "Keep the wide 24mm framing or push into 50mm?",
        },
        identity="call-esc-1",
    )
    assert esc1_type == "success"
    assert esc1["awaiting_human"] is True
    assert esc1["title"] == "Framing choice"
    assert esc1["question"] == "Keep the wide 24mm framing or push into 50mm?"

    # Same question under a second operation_id returns the same pending ticket without duplicating.
    esc2, esc2_type = invoke(
        vlm_runtime,
        "escalate_vlm_review",
        {
            "schema_version": "1.0",
            "operation_id": "op-esc-2",
            "project_id": PROJECT,
            "review_id": "rev-stop-wait",
            "question": "Keep the wide 24mm framing or push into 50mm?",
        },
        identity="call-esc-2",
    )
    assert esc2_type == "success"
    assert esc2["revision"] == esc1["revision"]

    # Conflicting question while unresolved is rejected.
    esc_conflict, esc_conflict_type = invoke(
        vlm_runtime,
        "escalate_vlm_review",
        {
            "schema_version": "1.0",
            "operation_id": "op-esc-3",
            "project_id": PROJECT,
            "review_id": "rev-stop-wait",
            "question": "Completely different question?",
        },
        identity="call-esc-3",
    )
    assert esc_conflict_type == "failure"
    assert esc_conflict["error"] == "idempotency_conflict"


def test_owner_review_inbox_badge_lifecycle_and_http_routes(vlm_runtime):
    app = FastAPI()
    app.state.services = vlm_runtime.services
    app.add_exception_handler(
        DomainError, lambda _, exc: JSONResponse(exc.envelope(), status_code=exc.status)
    )
    app.include_router(review_router)
    app.include_router(provider_router)
    app.dependency_overrides[owner] = lambda: Caller(channel="owner", subject_id="owner")
    client = TestClient(app)

    cand = register_png(vlm_runtime, "art-inbox", purpose="keyframe")

    # 1. Routine agent self-check with 1 finding -> NOT in pending, IS in all.
    vlm_runtime.vlm_state["response"]["choices"][0]["message"]["content"] = json.dumps(
        {
            "findings": [
                {
                    "target_index": 0,
                    "observation": "Minor horizon tilt.",
                    "recommendation": "Level the camera roll to 0 degrees.",
                    "evidence": [
                        {
                            "kind": "visual",
                            "artifact_id": "art-inbox",
                            "description": "Horizon slopes 3 degrees right.",
                        }
                    ],
                }
            ]
        }
    )
    invoke(
        vlm_runtime,
        "request_vlm_review",
        {
            "schema_version": "1.0",
            "review_id": "rev-self-check",
            "project_id": PROJECT,
            "title": "Agent self check",
            "targets": [cand],
            "criteria": "Check camera roll.",
            "telemetry_artifact_ids": [],
        },
        identity="call-inbox-self",
    )
    vlm_runtime.provider.pump_reviews(PROJECT)

    pending_res = client.get(f"/api/v1/projects/{PROJECT}/reviews", params={"filter": "pending"})
    assert pending_res.status_code == 200
    assert pending_res.json()["items"] == []

    all_res = client.get(f"/api/v1/projects/{PROJECT}/reviews", params={"filter": "all"})
    assert [r["review_id"] for r in all_res.json()["items"]] == ["rev-self-check"]

    # 2. Owner-requested review with 0 findings -> pending before pump, auto-resolved on ready!
    vlm_runtime.vlm_state["response"]["choices"][0]["message"]["content"] = json.dumps(
        {"findings": []}
    )
    owner_zero = client.post(
        f"/api/v1/projects/{PROJECT}/reviews",
        headers={"Idempotency-Key": "rev-owner-zero"},
        json={
            "schema_version": "1.0",
            "review_id": "rev-owner-zero",
            "project_id": PROJECT,
            "title": "Zero defect check",
            "targets": [cand],
            "criteria": "Verify clean frame.",
            "telemetry_artifact_ids": [],
            "human_review": True,
        },
    )
    assert owner_zero.status_code == 202
    assert owner_zero.json()["awaiting_human"] is True
    vlm_runtime.provider.pump_reviews(PROJECT)

    detail_zero = client.get(f"/api/v1/projects/{PROJECT}/reviews/rev-owner-zero").json()
    assert detail_zero["ticket"]["status"] == "ready"
    assert detail_zero["ticket"]["finding_count"] == 0
    assert detail_zero["ticket"]["awaiting_human"] is False
    assert detail_zero["ticket"]["human_review_state"] == "resolved"

    # 3. Owner-requested review with 2 findings -> stays pending (count 1) until both handled or resolved.
    vlm_runtime.vlm_state["response"]["choices"][0]["message"]["content"] = json.dumps(
        {
            "findings": [
                {
                    "target_index": 0,
                    "observation": "Cable clips through railing.",
                    "recommendation": "Route the cable above the top stanchion.",
                    "evidence": [
                        {
                            "kind": "visual",
                            "artifact_id": "art-inbox",
                            "description": "Lower-left foreground railing.",
                        }
                    ],
                },
                {
                    "target_index": 0,
                    "observation": "Fill light clips highlight on dish rim.",
                    "recommendation": "Reduce key-to-fill exposure by 1 stop on dish rim.",
                    "evidence": [
                        {
                            "kind": "visual",
                            "artifact_id": "art-inbox",
                            "description": "Upper-right dish rim.",
                        }
                    ],
                },
            ]
        }
    )
    owner_two = client.post(
        f"/api/v1/projects/{PROJECT}/reviews",
        headers={"Idempotency-Key": "rev-owner-two"},
        json={
            "schema_version": "1.0",
            "review_id": "rev-owner-two",
            "project_id": PROJECT,
            "title": "Shot 2 lighting and cable",
            "targets": [cand],
            "criteria": "Check cable clearance and rim exposure.",
            "telemetry_artifact_ids": [],
        },
    )
    assert owner_two.status_code == 202
    vlm_runtime.provider.pump_reviews(PROJECT)

    pending_items = client.get(
        f"/api/v1/projects/{PROJECT}/reviews", params={"filter": "pending"}
    ).json()["items"]
    assert [r["review_id"] for r in pending_items] == ["rev-owner-two"]
    assert pending_items[0]["finding_count"] == 2
    assert pending_items[0]["unreviewed_count"] == 2

    # Reject first finding -> still 1 unreviewed finding, still awaiting_human == True.
    rej = client.post(
        f"/api/v1/projects/{PROJECT}/reviews/rev-owner-two/findings/rev-owner-two-finding-0/reject",
        headers={"Idempotency-Key": "op-rej-0"},
        json={
            "schema_version": "1.0",
            "operation_id": "op-rej-0",
            "project_id": PROJECT,
            "expected_revision": 1,
        },
    )
    assert rej.status_code == 200
    after_one_rej = client.get(f"/api/v1/projects/{PROJECT}/reviews/rev-owner-two").json()
    assert after_one_rej["ticket"]["unreviewed_count"] == 1
    assert after_one_rej["ticket"]["awaiting_human"] is True

    # Owner clicks "Done without changes" (resolveOwnerReview) -> clears immediately!
    res = client.post(
        f"/api/v1/projects/{PROJECT}/reviews/rev-owner-two/resolve",
        headers={"Idempotency-Key": "op-resolve-two"},
        json={
            "schema_version": "1.0",
            "operation_id": "op-resolve-two",
            "project_id": PROJECT,
            "expected_revision": after_one_rej["ticket"]["revision"],
        },
    )
    assert res.status_code == 200
    after_resolve = client.get(f"/api/v1/projects/{PROJECT}/reviews/rev-owner-two").json()
    assert after_resolve["ticket"]["awaiting_human"] is False
    assert after_resolve["ticket"]["human_review_state"] == "resolved"
    assert (
        client.get(f"/api/v1/projects/{PROJECT}/reviews", params={"filter": "pending"}).json()[
            "items"
        ]
        == []
    )

    # Filter-bound pagination cursor cannot be replayed across different filters.
    paged_all = client.get(
        f"/api/v1/projects/{PROJECT}/reviews", params={"filter": "all", "limit": 1}
    ).json()
    assert paged_all["next_cursor"] is not None
    wrong_scope = client.get(
        f"/api/v1/projects/{PROJECT}/reviews",
        params={"filter": "pending", "cursor": paged_all["next_cursor"]},
    )
    assert wrong_scope.status_code == 400
    assert wrong_scope.json()["error"]["code"] == "invalid_cursor"

    # Legacy 4-field ReviewTicket row without review.meta projects cleanly.
    with vlm_runtime.store.transaction() as tx:
        legacy_job = vlm_runtime.services.jobs.create(
            tx,
            job_id="review-job-legacy",
            kind="vlm_review",
            target={"target_id": "rev-legacy", "version": None, "shot_id": None},
            input_digest=sha(canonical({"legacy": True})),
            operation_id="rev-legacy",
            work_id=WORK,
            owner_operation_id=None,
            provider_id="local-vlm",
            enqueue_kind="providers.review",
        )
        vlm_runtime.services.jobs.emit(tx, legacy_job, Caller(channel="tool", subject_id="tool"))
        tx.put(
            "reviews",
            "rev-legacy",
            {
                "schema_version": "1.0",
                "review_id": "rev-legacy",
                "job_id": legacy_job["job_id"],
                "status": "ready",
            },
            schema="ReviewTicket",
        )
        tx.put(
            "review.requests",
            "rev-legacy",
            {
                "schema_version": "1.0",
                "review_id": "rev-legacy",
                "project_id": PROJECT,
                "targets": [{**cand, "shot_id": "shot-04"}],
                "criteria": "Read-only assessment of hull panel alignment; ignore draft noise.",
                "telemetry_artifact_ids": [],
            },
            schema="ReviewRequest",
        )
    legacy_detail = client.get(f"/api/v1/projects/{PROJECT}/reviews/rev-legacy").json()
    assert legacy_detail["ticket"]["title"] == "shot-04 · hull panel alignment"
    assert legacy_detail["ticket"]["created_at"] is not None
    assert validate("ReviewDetail", legacy_detail)["ticket"]["awaiting_human"] is False


def test_blender_worker_ignores_local_asset_shelf_weak_refs_and_allows_curve_font_tessellation():
    from types import SimpleNamespace

    from proscenium.previews.blender_worker import (
        _is_tessellated_primitive_instance,
        runtime_blend_paths,
    )

    gp_brush = "//../../../../.local/opt/blender-4.5/datafiles/assets/brushes/essentials_brushes-gp_draw.blend"
    local_mat = SimpleNamespace(
        library=None,
        library_weak_reference=SimpleNamespace(filepath=gp_brush),
    )
    linked_mat = SimpleNamespace(
        library=SimpleNamespace(filepath="//linked_kit.blend"),
        library_weak_reference=SimpleNamespace(filepath="//linked_kit.blend"),
    )
    fake_bpy = SimpleNamespace(
        data=SimpleNamespace(
            bl_rna=SimpleNamespace(
                properties=[
                    SimpleNamespace(type="COLLECTION", identifier="materials"),
                    SimpleNamespace(type="POINTER", identifier="rna_type"),
                ]
            ),
            materials=[local_mat, linked_mat],
        ),
        utils=SimpleNamespace(
            blend_paths=lambda **_: [gp_brush, "//linked_kit.blend", "//textures/albedo.png"]
        ),
        path=SimpleNamespace(abspath=lambda p: f"/abs/{p.lstrip('/')}"),
    )
    # Local weak ref (gp_brush) is stripped; linked_mat (library != None) and unpacked texture remain!
    assert runtime_blend_paths(fake_bpy) == [
        "/abs/linked_kit.blend",
        "/abs/textures/albedo.png",
    ]

    curve_orig = SimpleNamespace(name="Cable", type="CURVE")
    curve_inst = SimpleNamespace(
        is_instance=True,
        parent=SimpleNamespace(original=curve_orig, type="CURVE"),
        object=SimpleNamespace(original=curve_orig, type="MESH"),
    )
    collection_inst = SimpleNamespace(
        is_instance=True,
        parent=SimpleNamespace(original=SimpleNamespace(name="Empty"), type="EMPTY"),
        object=SimpleNamespace(original=SimpleNamespace(name="Bolt"), type="MESH"),
    )
    assert _is_tessellated_primitive_instance(curve_inst) is True
    assert _is_tessellated_primitive_instance(collection_inst) is False
