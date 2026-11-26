"""Unit and HTTP/tool tests for VLM 16K/medium critique, work-scoped self-review, escalation and Critic inbox."""

import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
from proscenium.api.lifecycle import FeatureLifecycle
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.db.outbox import has_unsettled_outbox, settled_uncertain_vlm_review
from proscenium.db.registry import Registry
from proscenium.db.store import Store
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


@pytest.fixture
def owner_review_runtime(vlm_runtime, tmp_path):
    rt = vlm_runtime
    registry = Registry(Store(tmp_path / "registry.sqlite3", write_barrier=threading.RLock()))
    other = tmp_path / "second-project"
    other.mkdir()
    for project_id, path in [(PROJECT, rt.root), ("second-project", other)]:
        registry.register(
            dict(
                schema_version="1.0",
                project_id=project_id,
                name=project_id,
                path=str(path),
                repository_id=f"repo-{project_id}",
                state="inactive",
                state_revision=0,
                native_session_id=None,
            )
        )
    registry.activate(PROJECT)
    rt.services.registry = rt.services.jobs.registry = registry
    rt.store = registry.project_store(PROJECT)
    with rt.store.transaction() as tx:
        work = tx.require("work", WORK)
        work.update(state="completed", state_revision=1)
        tx.put("work", WORK, work, expected_revision=0, schema="WorkRecord")
    return rt


def request_owner_review(rt):
    ticket, _ = rt.provider.request_review(
        Caller("owner", "owner"),
        {
            "schema_version": "1.0",
            "review_id": "rev-owner",
            "project_id": PROJECT,
            "targets": [register_png(rt, "art-owner")],
            "criteria": "Check the lighting.",
            "telemetry_artifact_ids": [],
            "human_review": True,
        },
    )
    return rt.services.jobs.get(PROJECT, ticket["job_id"])


def time_out_review(rt):
    rt.vlm_state["raise_exc"] = httpx.ReadTimeout("LM Studio timed out")
    (job,) = rt.provider.pump_reviews(PROJECT)
    assert job["state"] == "outcome_uncertain"
    assert rt.provider.execution(PROJECT, job["job_id"]) == {"phase": "submitting"}
    requests = len(rt.vlm_state["requests"])
    assert rt.provider.pump_reviews(PROJECT) == []
    assert len(rt.vlm_state["requests"]) == requests
    return job


def test_timed_out_vlm_review_allows_idle_and_project_switch(owner_review_runtime):
    rt = owner_review_runtime
    request_owner_review(rt)
    job = time_out_review(rt)
    with rt.store.transaction(write=False) as tx:
        assert settled_uncertain_vlm_review(tx, job)
        assert not has_unsettled_outbox(tx)
    assert FeatureLifecycle(rt.services)._activity() == (PROJECT, False)
    rt.services.registry.assert_settled(PROJECT)
    rt.services.registry.activate("second-project")
    assert rt.services.registry.active_id() == "second-project"


@pytest.mark.parametrize(
    ("kind", "state", "phase"),
    [
        ("vlm_review", "planned", None),
        ("vlm_review", "waiting_resource", "waiting"),
        ("vlm_review", "starting", "submitting"),
        ("vlm_review", "running", "submitting"),
        ("vlm_review", "cancel_requested", None),
        ("vlm_review", "outcome_uncertain", "review_complete"),
        ("vlm_review", "outcome_uncertain", "checking"),
        ("image_generation", "outcome_uncertain", "submitting"),
    ],
)
def test_live_jobs_still_block_switch_and_keep_workers_active(
    owner_review_runtime, kind, state, phase
):
    rt = owner_review_runtime
    if kind == "vlm_review":
        job = request_owner_review(rt)
        # Isolate the job check from any independently active outbox intent.
        with rt.store.transaction() as tx:
            tx.claim_intent(job["attempt_id"])
            tx.settle_intent(job["attempt_id"], "acknowledged", {"test": "job-only blocker"})
    else:
        with rt.store.transaction() as tx:
            job = rt.services.jobs.create(
                tx,
                job_id="job-image",
                kind=kind,
                target={"target_id": "concept", "version": None, "shot_id": None},
                input_digest="0" * 64,
                operation_id="generate-image",
                owner_operation_id="generate-image",
            )
    if state == "running":
        job = rt.provider.update(job, "starting")
    if state != "planned":
        job = rt.provider.update(job, state)
    if phase:
        rt.provider.execution(PROJECT, job["job_id"], {"phase": phase})
    with pytest.raises(DomainError, match=f"Project {PROJECT} has unsettled jobs") as error:
        rt.services.registry.assert_settled(PROJECT)
    assert (error.value.code, error.value.status) == ("project_busy", 409)
    assert FeatureLifecycle(rt.services)._activity() == (PROJECT, True)


@pytest.mark.parametrize(
    "body", [{"job_id": "missing"}, [], {"job_id": 42}, {"job_id": "wrong-attempt"}]
)
def test_unmatched_uncertain_review_intent_still_blocks(owner_review_runtime, body):
    rt = owner_review_runtime
    request_owner_review(rt)
    job = time_out_review(rt)
    if body == {"job_id": "wrong-attempt"}:
        body = {"job_id": job["job_id"], "attempt_id": "orphan"}
    with rt.store.transaction() as tx:
        tx.connection.execute(
            "INSERT INTO outbox (id,kind,state,body) VALUES (?,?,?,?)",
            ("orphan", "providers.review", "outcome_uncertain", json.dumps(body)),
        )
        assert has_unsettled_outbox(tx)
    with pytest.raises(DomainError, match="unsettled background operations") as error:
        rt.services.registry.assert_settled(PROJECT)
    assert (error.value.code, error.value.status) == ("project_busy", 409)
    assert FeatureLifecycle(rt.services)._activity() == (PROJECT, True)


def test_uncertain_review_outbox_payload_must_name_exact_attempt(owner_review_runtime):
    rt = owner_review_runtime
    request_owner_review(rt)
    job = time_out_review(rt)
    with rt.store.transaction() as tx:
        tx.connection.execute(
            "UPDATE outbox SET body=json_set(body, '$.attempt_id', 'wrong') WHERE id=?",
            (job["attempt_id"],),
        )
        assert settled_uncertain_vlm_review(tx, job)
        assert has_unsettled_outbox(tx)
    with pytest.raises(DomainError, match="unsettled background operations") as error:
        rt.services.registry.assert_settled(PROJECT)
    assert (error.value.code, error.value.status) == ("project_busy", 409)
    assert FeatureLifecycle(rt.services)._activity() == (PROJECT, True)

    assert cancel_owner_review(rt, job)[0]["state"] == "cancel_requested"
    with pytest.raises(DomainError, match="Review outbox payload does not match attempt") as error:
        rt.services.jobs.dispatch_cancellations(PROJECT)
    assert (error.value.code, error.value.status) == ("source_conflict", 409)
    with rt.store.transaction(write=False) as tx:
        assert tx.require("jobs", job["job_id"])["state"] == "cancel_requested"
        assert tx.require("reviews", "rev-owner")["status"] == "pending"
        assert outbox_record(tx, job["attempt_id"])[0] == "outcome_uncertain"
        assert outbox_record(tx, "owner-cancel-review") == ("pending", None)
        assert has_unsettled_outbox(tx)
    with pytest.raises(DomainError) as error:
        rt.services.registry.assert_settled(PROJECT)
    assert (error.value.code, error.value.status) == ("project_busy", 409)


@pytest.mark.parametrize("mismatch_job", [True, False])
def test_review_completion_cannot_clear_bad_outbox_payload(owner_review_runtime, mismatch_job):
    rt = owner_review_runtime
    job = request_owner_review(rt)
    with rt.store.transaction() as tx:
        if mismatch_job:
            tx.connection.execute(
                "UPDATE outbox SET body=json_set(body, '$.job_id', 'wrong') WHERE id=?",
                (job["attempt_id"],),
            )
        else:
            tx.connection.execute("UPDATE outbox SET body='[]' WHERE id=?", (job["attempt_id"],))
    with pytest.raises(DomainError, match="Review outbox payload does not match attempt") as error:
        rt.provider.complete_review(rt.provider.worker(job), ready_review_request())
    assert (error.value.code, error.value.status) == ("source_conflict", 409)
    with rt.store.transaction(write=False) as tx:
        assert tx.require("jobs", job["job_id"])["state"] == "planned"
        assert tx.require("reviews", "rev-owner")["status"] == "pending"
        assert tx.require("providers.tickets", "rev-owner")["status"] == "pending"
        assert outbox_record(tx, job["attempt_id"]) == ("pending", None)


def test_dispatching_attempt_is_not_a_settled_uncertain_review(owner_review_runtime):
    rt = owner_review_runtime
    request_owner_review(rt)
    job = time_out_review(rt)
    with rt.store.transaction() as tx:
        tx.connection.execute(
            "UPDATE outbox SET state='dispatching' WHERE id=?", (job["attempt_id"],)
        )
        assert not settled_uncertain_vlm_review(tx, job)
        assert has_unsettled_outbox(tx)
    with pytest.raises(DomainError, match="unsettled jobs"):
        rt.services.registry.assert_settled(PROJECT)
    assert FeatureLifecycle(rt.services)._activity() == (PROJECT, True)


def cancel_owner_review(rt, job):
    return rt.services.jobs.cancel(
        Caller("owner", "owner"),
        job["job_id"],
        dict(
            schema_version="1.0",
            operation_id="owner-cancel-review",
            project_id=PROJECT,
            expected_revision=job["state_revision"],
        ),
    )


@pytest.mark.parametrize("initial_state", ["planned", "outcome_uncertain"])
def test_owner_cancels_non_inflight_vlm_review(owner_review_runtime, initial_state):
    rt = owner_review_runtime
    job = request_owner_review(rt)
    if initial_state == "outcome_uncertain":
        job = time_out_review(rt)
    requests = len(rt.vlm_state["requests"])
    receipt, replayed = cancel_owner_review(rt, job)
    assert not replayed
    assert receipt["state"] == "cancel_requested"
    # A worker tick between admission and cancellation delivery must not pick it up.
    assert rt.provider.pump_reviews(PROJECT) == []
    assert rt.services.jobs.dispatch_cancellations(PROJECT) == [
        {"operation_id": "owner-cancel-review"}
    ]
    assert rt.services.jobs.dispatch_cancellations(PROJECT) == []
    cancelled = rt.services.jobs.get(PROJECT, job["job_id"])
    assert (cancelled["state"], cancelled["error_code"]) == ("cancelled", "cancelled")
    with rt.store.transaction(write=False) as tx:
        ticket = tx.require("reviews", "rev-owner")
        view = rt.reviews.ticket_view(tx, ticket)
        assert (ticket["status"], view["error_code"], view["awaiting_human"]) == (
            "failed",
            "cancelled",
            False,
        )
        assert tx.require("providers.execution", job["job_id"]) == {"phase": "cancelled"}
        assert tx.get("providers.pending", job["job_id"], required=False) is None
        outbox = tx.connection.execute(
            "SELECT state,evidence FROM outbox WHERE id=?", (job["attempt_id"],)
        ).fetchone()
        assert outbox["state"] == "failed"
        assert json.loads(outbox["evidence"]) == {"job_id": job["job_id"], "state": "cancelled"}
        assert not has_unsettled_outbox(tx)
    assert rt.provider.pump_reviews(PROJECT) == []
    assert len(rt.vlm_state["requests"]) == requests
    assert FeatureLifecycle(rt.services)._activity() == (PROJECT, False)


def outbox_record(tx, identity):
    row = tx.connection.execute(
        "SELECT state,evidence FROM outbox WHERE id=?", (identity,)
    ).fetchone()
    return row["state"], json.loads(row["evidence"]) if row["evidence"] else None


def strand_cancel_intent(tx, state):
    if state != "pending":
        tx.claim_intent("owner-cancel-review")
    if state == "outcome_uncertain":
        tx.settle_intent("owner-cancel-review", state, {"reason": "legacy interruption"})


@pytest.mark.parametrize("intent_state", ["pending", "dispatching", "outcome_uncertain"])
def test_review_cancel_rolls_back_as_one_unit_and_recovers(
    owner_review_runtime, monkeypatch, intent_state
):
    rt = owner_review_runtime
    job = request_owner_review(rt)
    cancel_owner_review(rt, job)
    with rt.store.transaction() as tx:
        strand_cancel_intent(tx, intent_state)
        old_intent = outbox_record(tx, "owner-cancel-review")
        old_ticket = tx.require("providers.tickets", "rev-owner")
        watermark = tx.watermark()
    complete = rt.reviews.complete_in_transaction

    def interrupt(*args, **kwargs):
        complete(*args, **kwargs)
        raise DomainError("storage_unavailable", 503)

    with monkeypatch.context() as fault:
        fault.setattr(rt.reviews, "complete_in_transaction", interrupt)
        with pytest.raises(DomainError) as error:
            rt.services.jobs.dispatch_cancellations(PROJECT)
        assert (error.value.code, error.value.status) == ("storage_unavailable", 503)
    with rt.store.transaction(write=False) as tx:
        assert tx.require("jobs", job["job_id"])["state"] == "cancel_requested"
        assert tx.require("attempts", job["attempt_id"])["state"] == "cancel_requested"
        assert tx.require("reviews", "rev-owner")["status"] == "pending"
        assert tx.require("providers.tickets", "rev-owner") == old_ticket
        assert tx.get("review.diagnostics", "rev-owner", required=False) is None
        assert tx.get("providers.execution", job["job_id"], required=False) is None
        assert tx.require("providers.pending", job["job_id"]) == {"kind": "review"}
        assert outbox_record(tx, job["attempt_id"]) == ("pending", None)
        assert outbox_record(tx, "owner-cancel-review") == old_intent
        assert tx.watermark() == watermark

    assert rt.services.jobs.dispatch_cancellations(PROJECT) == [
        {"operation_id": "owner-cancel-review"}
    ]
    with rt.store.transaction(write=False) as tx:
        assert tx.require("jobs", job["job_id"])["state"] == "cancelled"
        assert tx.require("reviews", "rev-owner")["status"] == "failed"
        assert tx.require("providers.tickets", "rev-owner")["error_code"] == "cancelled"
        assert outbox_record(tx, "owner-cancel-review") == (
            "acknowledged",
            dict(
                job_id=job["job_id"],
                attempt_id=job["attempt_id"],
                observed_state="cancelled",
                adapter_called=True,
            ),
        )
    rt.services.registry.assert_settled(PROJECT)


def test_stranded_cancel_repairs_legacy_cancelled_job_with_pending_ticket(owner_review_runtime):
    rt = owner_review_runtime
    job = request_owner_review(rt)
    cancel_owner_review(rt, job)
    job = rt.services.jobs.get(PROJECT, job["job_id"])
    rt.provider.update(job, "cancelled", error_code="cancelled")
    with rt.store.transaction() as tx:
        strand_cancel_intent(tx, "outcome_uncertain")
    rt.services.jobs.dispatch_cancellations(PROJECT)
    with rt.store.transaction(write=False) as tx:
        assert tx.require("reviews", "rev-owner")["status"] == "failed"
        assert tx.require("providers.tickets", "rev-owner")["error_code"] == "cancelled"
        state, evidence = outbox_record(tx, "owner-cancel-review")
        assert state == "acknowledged"
        assert evidence["observed_state"] == "cancelled"
        assert evidence["adapter_called"] is False
    rt.services.registry.assert_settled(PROJECT)


@pytest.mark.parametrize(
    ("job_state", "missing_projection"),
    [("cancelled", False), ("succeeded", True), ("failed", False)],
)
def test_stranded_cancel_repairs_terminal_review_projection(
    owner_review_runtime, job_state, missing_projection
):
    rt = owner_review_runtime
    job = request_owner_review(rt)
    cancel_owner_review(rt, job)
    job = rt.services.jobs.get(PROJECT, job["job_id"])
    ready = job_state == "succeeded"
    request = ready_review_request()
    if not ready:
        request.update(
            status="failed",
            error_code="cancelled" if job_state == "cancelled" else "provider_unavailable",
        )
    rt.reviews.complete(rt.provider.worker(job), request)
    rt.provider.update(
        job,
        job_state,
        **({"availability": "complete"} if ready else {"error_code": request["error_code"]}),
    )
    with rt.store.transaction() as tx:
        assert tx.require("reviews", "rev-owner")["status"] == request["status"]
        assert tx.require("providers.tickets", "rev-owner")["status"] == "pending"
        if missing_projection:
            tx.connection.execute(
                "DELETE FROM records WHERE namespace='providers.tickets' AND id='rev-owner'"
            )
        strand_cancel_intent(tx, "dispatching")

    assert rt.services.jobs.dispatch_cancellations(PROJECT) == [
        {"operation_id": "owner-cancel-review"}
    ]
    with rt.store.transaction(write=False) as tx:
        assert tx.require("jobs", job["job_id"])["state"] == job_state
        assert tx.require("providers.tickets", "rev-owner") == rt.reviews.ticket_view(
            tx, tx.require("reviews", "rev-owner")
        )
        assert tx.get("providers.pending", job["job_id"], required=False) is None
        assert outbox_record(tx, job["attempt_id"])[0] == (
            "failed" if job_state == "cancelled" else "acknowledged"
        )
        state, evidence = outbox_record(tx, "owner-cancel-review")
        assert state == "acknowledged"
        assert evidence["observed_state"] == job_state
    rt.services.registry.assert_settled(PROJECT)


def ready_review_request():
    return dict(
        schema_version="1.0",
        operation_id="receive-owner-review",
        project_id=PROJECT,
        review_id="rev-owner",
        status="ready",
        findings=[],
    )


@pytest.mark.parametrize("legacy_ticket_only", [False, True])
def test_ready_review_wins_against_admitted_cancel(owner_review_runtime, legacy_ticket_only):
    rt = owner_review_runtime
    job = request_owner_review(rt)
    cancel_owner_review(rt, job)
    if legacy_ticket_only:
        rt.reviews.complete(rt.provider.worker(job), ready_review_request())
        assert rt.services.jobs.get(PROJECT, job["job_id"])["state"] == "cancel_requested"
    else:
        ticket, replayed = rt.provider.complete_review(
            rt.provider.worker(job), ready_review_request()
        )
        assert ticket["status"] == "ready"
        assert not replayed
        assert rt.services.jobs.get(PROJECT, job["job_id"])["state"] == "succeeded"
    rt.services.jobs.dispatch_cancellations(PROJECT)
    with rt.store.transaction(write=False) as tx:
        completed = tx.require("jobs", job["job_id"])
        assert (completed["state"], completed["availability"]) == ("succeeded", "complete")
        assert tx.require("reviews", "rev-owner")["status"] == "ready"
        assert tx.require("providers.tickets", "rev-owner")["status"] == "ready"
        assert tx.get("providers.pending", job["job_id"], required=False) is None
        assert outbox_record(tx, job["attempt_id"]) == ("acknowledged", {"review_id": "rev-owner"})
        state, evidence = outbox_record(tx, "owner-cancel-review")
        assert state == "acknowledged"
        assert evidence["observed_state"] == "succeeded"
        assert evidence["adapter_called"] is legacy_ticket_only
    rt.services.registry.assert_settled(PROJECT)


def test_late_completion_cannot_replace_committed_cancel(owner_review_runtime):
    rt = owner_review_runtime
    job = request_owner_review(rt)
    cancel_owner_review(rt, job)
    rt.services.jobs.dispatch_cancellations(PROJECT)
    with pytest.raises(DomainError) as error:
        rt.provider.complete_review(rt.provider.worker(job), ready_review_request())
    assert (error.value.code, error.value.status) == ("invalid_state_transition", 409)
    with rt.store.transaction(write=False) as tx:
        assert tx.require("jobs", job["job_id"])["state"] == "cancelled"
        assert tx.require("reviews", "rev-owner")["status"] == "failed"


def test_review_completion_rolls_back_and_replays_saved_response(owner_review_runtime, monkeypatch):
    rt = owner_review_runtime
    job = request_owner_review(rt)
    update = rt.services.jobs.update_in_transaction

    def interrupt(tx, caller, body, **kwargs):
        result = update(tx, caller, body, **kwargs)
        if body["state"] == "succeeded":
            raise DomainError("storage_unavailable", 503)
        return result

    with monkeypatch.context() as fault:
        fault.setattr(rt.services.jobs, "update_in_transaction", interrupt)
        with pytest.raises(DomainError) as error:
            rt.provider.pump_reviews(PROJECT)
        assert error.value.code == "storage_unavailable"

    with rt.store.transaction(write=False) as tx:
        assert tx.require("jobs", job["job_id"])["state"] == "starting"
        assert tx.require("reviews", "rev-owner")["status"] == "pending"
        assert tx.require("providers.tickets", "rev-owner")["status"] == "pending"
        assert tx.require("providers.execution", job["job_id"])["phase"] == "review_complete"
        assert tx.require("providers.pending", job["job_id"]) == {"kind": "review"}
        assert outbox_record(tx, job["attempt_id"]) == ("dispatching", None)
    assert FeatureLifecycle(rt.services)._activity() == (PROJECT, True)
    requests = len(rt.vlm_state["requests"])
    assert rt.provider.pump_reviews(PROJECT)[0]["state"] == "succeeded"
    assert len(rt.vlm_state["requests"]) == requests
    rt.services.registry.assert_settled(PROJECT)


@pytest.mark.parametrize("kind", ["render", "encode", "unity"])
def test_uncertain_native_job_cancellation_still_requires_evidence(owner_review_runtime, kind):
    rt = owner_review_runtime
    with rt.store.transaction() as tx:
        job = rt.services.jobs.create(
            tx,
            job_id="job-native",
            kind=kind,
            target={"target_id": "concept", "version": None, "shot_id": None},
            input_digest="0" * 64,
            operation_id="run-native",
            owner_operation_id="run-native",
        )
    rt.services.jobs.cancellers[kind] = lambda _: pytest.fail("Must not cancel a native job")
    job = rt.provider.update(job, "outcome_uncertain")
    with pytest.raises(DomainError, match="Reconciliation requires evidence") as error:
        cancel_owner_review(rt, job)
    assert error.value.code == "invalid_state_transition"
    with rt.store.transaction(write=False) as tx:
        assert tx.require("jobs", job["job_id"])["state"] == "outcome_uncertain"
        assert (
            tx.connection.execute("SELECT id FROM outbox WHERE kind='job.cancel'").fetchone()
            is None
        )
    request_owner_review(rt)
    review_job = time_out_review(rt)
    with rt.store.transaction(write=False) as tx:
        assert rt.provider.review_cancellation_evidence(tx, review_job) == (
            f"provider-execution:{review_job['job_id']}:{review_job['attempt_id']}:submitting"
        )
    assert cancel_owner_review(rt, review_job)[0]["state"] == "cancel_requested"


@pytest.mark.parametrize("intent_state", ["dispatching", "outcome_uncertain"])
def test_stranded_nontransactional_cancel_intent_is_not_replayed(
    owner_review_runtime, intent_state
):
    rt = owner_review_runtime
    with rt.store.transaction() as tx:
        job = rt.services.jobs.create(
            tx,
            job_id="job-native",
            kind="unity",
            target={"target_id": "concept", "version": None, "shot_id": None},
            input_digest="0" * 64,
            operation_id="run-native",
            owner_operation_id="run-native",
        )
    rt.services.jobs.cancellers["unity"] = lambda _: pytest.fail(
        "Must not replay a native cancellation"
    )
    cancel_owner_review(rt, job)
    with rt.store.transaction() as tx:
        strand_cancel_intent(tx, intent_state)
        original = outbox_record(tx, "owner-cancel-review")
    assert rt.services.jobs.dispatch_cancellations(PROJECT) == []
    with rt.store.transaction(write=False) as tx:
        assert outbox_record(tx, "owner-cancel-review") == original
        assert tx.require("jobs", job["job_id"])["state"] == "cancel_requested"


def test_owner_cannot_cancel_inflight_vlm_http_request(owner_review_runtime, monkeypatch):
    rt = owner_review_runtime
    job = request_owner_review(rt)
    request_entered = threading.Event()
    release_request = threading.Event()
    send = rt.provider.client.send

    def blocked_send(request, *args, **kwargs):
        if request.url.path == "/v1/chat/completions":
            request_entered.set()
            assert release_request.wait(timeout=5)
        return send(request, *args, **kwargs)

    monkeypatch.setattr(rt.provider.client, "send", blocked_send)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pumped = pool.submit(rt.provider.pump_reviews, PROJECT)
        try:
            assert request_entered.wait(timeout=5)
            starting = rt.services.jobs.get(PROJECT, job["job_id"])
            assert starting["state"] == "starting"
            with pytest.raises(DomainError) as error:
                cancel_owner_review(rt, starting)
            assert (error.value.code, error.value.status) == ("cancel_unsupported", 501)
            running = rt.provider.update(starting, "running")
            with pytest.raises(DomainError) as error:
                cancel_owner_review(rt, running)
            assert (error.value.code, error.value.status) == ("cancel_unsupported", 501)
            assert rt.services.jobs.dispatch_cancellations(PROJECT) == []
        finally:
            release_request.set()
        assert pumped.result(timeout=5)[0]["state"] == "succeeded"


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


def test_subagent_permission_requested_and_rpc_before_tool_execution_start_never_false_fences(
    tmp_path, monkeypatch
):
    import asyncio
    import hashlib

    import test_runtime_controls_and_diagnostics as rcd
    from proscenium.copilot.service import Copilot

    state, store, _, _ = rcd.runtime.__wrapped__(tmp_path, monkeypatch)
    rcd.start(state)

    sub_agent = "subagent-race-1"
    sub_start = rcd.event("ev-sub-start", "subagent.started", agentName="authoring-specialist")
    sub_start["agentId"] = sub_agent
    state.reduce(rcd.PROJECT, rcd.SESSION, sub_start)

    tool_call_id = "call_Ep8TOn3RqkObbMPtujJOk0hL"
    perm_req = {
        "kind": "custom-tool",
        "toolCallId": tool_call_id,
        "toolName": "get_job",
        "args": {"job_id": "job-race-1", "wait_seconds": 15},
    }
    # 1. permission.requested arrives BEFORE tool.execution_start!
    perm_ev = rcd.event(
        "ev-perm-req",
        "permission.requested",
        requestId="perm-race-1",
        permissionRequest=perm_req,
    )
    perm_ev["agentId"] = sub_agent
    state.reduce(rcd.PROJECT, rcd.SESSION, perm_ev)

    with store.transaction(write=False) as tx:
        assert state.control(tx)["fenced"] is False

    copilot = Copilot.__new__(Copilot)
    copilot.services = state.services
    copilot.state = state
    copilot.closing = False

    async def run_race():
        # 2. RPC permission callback starts BEFORE tool.execution_start lands!
        perm_task = asyncio.create_task(
            copilot.permission(rcd.PROJECT, perm_req, {"session_id": rcd.SESSION})
        )
        await asyncio.sleep(0.06)
        start_ev = rcd.event(
            "ev-tool-start",
            "tool.execution_start",
            toolCallId=tool_call_id,
            toolName="get_job",
            arguments={"job_id": "job-race-1", "wait_seconds": 15},
        )
        start_ev["agentId"] = sub_agent
        state.reduce(rcd.PROJECT, rcd.SESSION, start_ev)
        decision = await asyncio.wait_for(perm_task, 2.0)
        done_ev = rcd.event(
            "ev-perm-done",
            "permission.completed",
            requestId="perm-race-1",
            toolCallId=tool_call_id,
            result={"kind": "approved"},
        )
        done_ev["agentId"] = sub_agent
        state.reduce(rcd.PROJECT, rcd.SESSION, done_ev)
        return decision

    decision = asyncio.run(run_race())
    assert decision.kind == "approve-once"
    with store.transaction(write=False) as tx:
        assert state.control(tx)["fenced"] is False
        stage = tx.require("copilot.permissions", rcd.key(rcd.SESSION, "perm-race-1"))
        assert stage["phase"] == "completed"
        assert stage["authority"] == "automatic_policy"
        assert hashlib.sha256(canonical(perm_req)).hexdigest() == stage["fingerprint"]
