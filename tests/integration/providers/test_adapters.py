import concurrent.futures
import copy
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from proscenium.api.errors import DomainError
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.providers import (
    ProviderService,
    bind_generation_task,
    extend_generation_task,
    router,
)
from proscenium.providers.comfy import ComfyClient, preflight
from proscenium.providers.klein import graph
from proscenium.providers.vlm import findings


def posts(http, path):
    return [r for r in http.calls if r.method == "POST" and r.url.path == path]


def reservations(services):
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        return [
            tuple(r)
            for r in tx.connection.execute(
                "SELECT id,task_id,target_id,state FROM budget_reservations"
            )
        ]


def test_admission_is_durable_and_inert_then_immutable_outputs(rig, generation):
    services, caller, artifacts, http, adapter, _ = rig
    job, replayed = adapter.submit(caller, generation)
    assert not replayed and job["state"] == "planned" and not http.calls
    assert adapter.submit(caller, generation) == (job, True)
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        assert tx.require("attempts", job["attempt_id"])["job_id"] == job["job_id"]
        assert tx.connection.execute("SELECT state FROM outbox").fetchone()[0] == "pending"
    result = adapter.pump("project-test")[0]
    assert result["state"] == "succeeded" and result["availability"] == "complete"
    assert len(result["artifact_ids"]) == 2
    assert reservations(services)[0][3] == "consumed"
    assert adapter.pump("project-test") == []
    assert len(posts(http, "/prompt")) == 1
    for identity in result["artifact_ids"]:
        assert artifacts.ready_path("project-test", identity).stat().st_mode & 0o222 == 0
    metadata = json.loads(
        artifacts.ready_path("project-test", result["artifact_ids"][-1]).read_bytes()
    )
    assert metadata["inputs"]["request"]["seed"] == "42"
    assert metadata["graph"]["noise"]["inputs"]["noise_seed"] == 42
    assert metadata["prompt_id"] == "prompt-test"


@pytest.mark.parametrize("endpoint", ["http://provider-a.test:8188", "http://provider-b.test:9199"])
def test_exact_endpoint_frozen_across_changed_defaults(rig, generation, endpoint):
    services, caller, _, http, adapter, _ = rig
    config = list(services.settings.services)
    config[0] = {**config[0], "endpoint": endpoint}
    services.settings = replace(services.settings, services=tuple(config))
    job, _ = adapter.submit(caller, generation)
    config[0] = {**config[0], "endpoint": "http://wrong.test:1111"}
    services.settings = replace(services.settings, services=tuple(config))
    assert adapter.submit(caller, generation)[1]
    adapter.pump("project-test")
    assert all(str(r.url).startswith(endpoint + "/") for r in http.calls)
    assert services.jobs.inputs("project-test", job["job_id"])["provider"]["endpoint"] == endpoint


def test_budget_held_across_child_steer_resume_and_new_job_ids(rig, generation):
    services, caller, _, _, adapter, _ = rig
    for i in range(3):
        child = replace(caller, subject_id=f"child-{i}")
        adapter.submit(child, {**generation, "job_id": f"generation-{i}"})
    with pytest.raises(DomainError, match="generation budget exhausted"):
        adapter.submit(caller, {**generation, "job_id": "bypass-job"})
    with pytest.raises(DomainError, match="scope denied"):
        adapter.submit(caller, {**generation, "generation_task_id": "new-fake-task"})
    with pytest.raises(DomainError):
        adapter.submit(caller, {**generation, "target_id": "renamed-target"})
    with services.registry.project_store("project-test").transaction() as tx:
        with pytest.raises(DomainError, match="idempotency conflict"):
            bind_generation_task(tx, "task-test", 300, ["target-test"])
    assert len(reservations(services)) == 3


def test_concurrent_admission_is_one_reservation_and_job(rig, generation):
    services, caller, _, _, adapter, _ = rig
    with concurrent.futures.ThreadPoolExecutor(4) as executor:
        results = list(executor.map(lambda _: adapter.submit(caller, generation), range(8)))
    assert sum(not replayed for _, replayed in results) == 1
    assert len({j["attempt_id"] for j, _ in results}) == 1
    assert len(reservations(services)) == 1


def test_queue_busy_and_shared_claim_do_not_submit_or_reserve_twice(rig, generation):
    services, caller, _, http, adapter, _ = rig
    adapter.submit(caller, generation)
    http.queue_busy = True
    assert adapter.pump("project-test")[0]["state"] == "waiting_resource"
    assert not posts(http, "/prompt")
    http.queue_busy = False
    services.resources.acquire("heavy-gpu", "project-test", "other-job", "other-attempt")
    assert adapter.pump("project-test")[0]["state"] == "waiting_resource"
    services.resources.release(
        "heavy-gpu", "project-test", "other-job", "other-attempt", terminal_evidence="synthetic"
    )
    assert adapter.pump("project-test")[0]["state"] == "succeeded"
    assert len(reservations(services)) == len(posts(http, "/prompt")) == 1


@pytest.mark.parametrize("accepted", [False, True])
def test_submission_timeout_never_blind_retries(rig, generation, accepted):
    services, caller, _, http, adapter, _ = rig
    http.timeout_submit, http.accept_before_timeout = True, accepted
    job, _ = adapter.submit(caller, generation)
    result = adapter.pump("project-test")[0]
    assert result["state"] == "outcome_uncertain"
    assert "synthetic secret" not in json.dumps(result)
    assert reservations(services)[0][3] == "reserved"
    result = adapter.pump("project-test")[0]
    assert result["state"] == ("succeeded" if accepted else "outcome_uncertain")
    assert len(posts(http, "/prompt")) == 1
    assert reservations(services)[0][3] == ("consumed" if accepted else "reserved")
    with pytest.raises(DomainError, match="invalid state transition"):
        services.jobs.retry(
            caller,
            dict(
                schema_version="1.0",
                operation_id="retry-bypass",
                project_id="project-test",
                job_id=job["job_id"],
                expected_revision=result["state_revision"],
            ),
        )


@pytest.mark.parametrize("failure", ["unavailable", "rejected", "bad-model"])
def test_known_pre_execution_failure_releases_budget(rig, generation, failure):
    services, caller, _, http, adapter, _ = rig
    if failure == "unavailable":
        http.unavailable = True
    elif failure == "rejected":
        http.reject_submit = True
    else:
        generation["model_ids"] = ["missing.safetensors"]
    adapter.submit(caller, generation)
    assert adapter.pump("project-test")[0]["state"] == "failed"
    assert reservations(services)[0][3] == "released"


@pytest.mark.parametrize("endpoint", ["http://provider-a.test:8188", "http://provider-b.test:9199"])
def test_actual_reference_upload_graph_and_derivative_provenance(rig, generation, endpoint):
    services, caller, artifacts, http, adapter, _ = rig
    config = list(services.settings.services)
    config[0] = {**config[0], "endpoint": endpoint}
    services.settings = replace(services.settings, services=tuple(config))
    conditioned = graph(
        unet="klein.safetensors",
        clip="qwen.safetensors",
        vae="flux2.safetensors",
        width=64,
        height=64,
        reference_artifact_id="image-test",
    )
    artifacts.add("conditioned-workflow", json.dumps(conditioned).encode())
    http.graph = conditioned
    generation.update(
        workflow_artifact_id="conditioned-workflow", input_artifact_ids=["image-test"]
    )
    adapter.submit(caller, generation)
    result = adapter.pump("project-test")[0]
    assert result["state"] == "succeeded"
    assert len(posts(http, "/upload/image")) == 1
    submitted = json.loads(posts(http, "/prompt")[0].content)["prompt"]
    assert submitted["reference"]["inputs"]["image"] == "project/conditioning.png"
    assert submitted["guider"]["inputs"]["positive"] == ["reference_positive", 0]
    provenance = json.loads(
        artifacts.ready_path("project-test", result["artifact_ids"][-1]).read_bytes()
    )
    assert provenance["derived_images"]
    assert all(str(r.url).startswith(endpoint + "/") for r in http.calls)


def test_owner_review_stays_independent_of_active_work_and_findings_private(rig, review):
    services, _, _, http, adapter, _ = rig
    ticket, replayed = adapter.request_review(Caller("owner", "owner-test"), review)
    assert not replayed and set(ticket) == {"schema_version", "review_id", "job_id", "status"}
    job = services.jobs.get("project-test", ticket["job_id"])
    assert job["work_id"] is None and job["owner_operation_id"] == review["review_id"]
    assert not http.calls
    assert adapter.pump("project-test")[0]["state"] == "succeeded"
    retry, replayed = adapter.request_review(Caller("owner", "owner-test"), review)
    assert replayed and retry["status"] == "ready" and "findings" not in retry
    assert len(posts(http, "/v1/chat/completions")) == 1
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        finding = tx.require("findings", "review-test-finding-0")
        assert finding["state"] == "unreviewed" and finding["project_id"] == "project-test"
        assert not tx.all("requests")
        assert all("Red pixels" not in json.dumps(e) for e in tx.events()["events"])


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"findings": [{"target_index": 200}]},
        {
            "findings": [
                {
                    "target_index": 0,
                    "observation": "measured",
                    "recommendation": "repair",
                    "evidence": [
                        {
                            "kind": "measured",
                            "artifact_id": "image-test",
                            "description": "not telemetry",
                        }
                    ],
                }
            ]
        },
    ],
)
def test_malformed_or_fabricated_findings_fail_safely(rig, review, bad):
    services, caller, _, http, adapter, _ = rig
    http.vlm_result = bad
    ticket, _ = adapter.request_review(caller, review)
    assert services.jobs.get("project-test", ticket["job_id"])["work_id"] == caller.work_id
    assert adapter.pump("project-test")[0]["state"] == "failed"
    assert adapter.request_review(caller, review)[0]["status"] == "failed"
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        assert tx.all("findings") == []


def test_vlm_timeout_is_uncertain_and_never_resubmitted(rig, review):
    _, caller, _, http, adapter, _ = rig
    http.vlm_timeout = True
    adapter.request_review(caller, review)
    assert adapter.pump("project-test")[0]["state"] == "outcome_uncertain"
    adapter.pump("project-test")
    assert len(posts(http, "/v1/chat/completions")) == 1


def test_no_startup_or_read_execution_and_reset_gate(rig, generation):
    services, caller, artifacts, http, adapter, _ = rig
    job, _ = adapter.submit(caller, generation)
    services.gate.close("project-test")
    restarted = ProviderService(services, registrar=artifacts.register, client=adapter.client)
    assert services.jobs.get("project-test", job["job_id"])["state"] == "planned"
    assert not http.calls
    with pytest.raises(DomainError):
        restarted.pump("project-test")
    assert not http.calls
    services.gate.resume("project-test", reconciliation_evidence="synthetic explicit Resume")
    assert restarted.pump("project-test")[0]["state"] == "succeeded"


def test_missing_review_store_or_registrar_reports_unavailable(rig, review, generation):
    services, caller, _, _, adapter, _ = rig
    services.extensions.pop("reviews")
    with pytest.raises(DomainError) as error:
        adapter.request_review(caller, review)
    assert error.value.code == "capability_unavailable"
    adapter.registrar = None
    adapter.submit(caller, generation)
    assert adapter.pump("project-test")[0]["error_code"] == "capability_unavailable"


def test_tool_cannot_complete_idle_owner_review(rig, review):
    _, caller, _, _, adapter, _ = rig
    adapter.request_review(Caller("owner", "owner-test"), review)
    with pytest.raises(DomainError, match="scope denied"):
        adapter.complete_review(
            caller,
            dict(
                schema_version="1.0",
                operation_id="fake-complete",
                project_id="project-test",
                review_id=review["review_id"],
                status="ready",
                findings=[],
            ),
        )


def test_evidence_kinds_and_host_stamping(rig, review):
    raw = copy.deepcopy(rig[3].vlm_result)
    finding = findings(review, raw)[0]
    assert finding["finding_id"] == "review-test-finding-0"
    raw["findings"][0]["finding_id"] = "model-chosen"
    with pytest.raises(DomainError):
        findings(review, raw)


def test_preflight_invalid_model_and_dangling_link(rig):
    http = rig[3]
    with pytest.raises(DomainError):
        preflight(http.graph, http.catalog(), ["not-installed"])
    broken = copy.deepcopy(http.graph)
    broken["save"]["inputs"]["images"] = ["unknown", 0]
    with pytest.raises(DomainError):
        preflight(
            broken, http.catalog(), ["klein.safetensors", "qwen.safetensors", "flux2.safetensors"]
        )


def test_http_channels_status_and_only_ticket_native_response(rig, generation, review):
    services, caller, _, _, adapter, _ = rig
    token = services.auth.issue_capability(caller)
    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": generation["job_id"]}
    with TestClient(
        create_app(services.settings, services=services, routers=[router]),
        base_url=services.settings.origin,
    ) as client:
        assert (
            client.post(
                "/internal/v1/generation-jobs", json=generation, headers=headers
            ).status_code
            == 202
        )
        assert (
            client.post(
                "/internal/v1/generation-jobs", json=generation, headers=headers
            ).status_code
            == 200
        )
        headers["Idempotency-Key"] = review["review_id"]
        response = client.post("/internal/v1/reviews", json=review, headers=headers)
        assert response.status_code == 202
        assert "findings" not in response.json()
        assert client.post("/internal/v1/reviews", json=review, headers=headers).status_code == 200
        headers["Origin"] = services.settings.origin
        assert (
            client.post(
                "/api/v1/projects/project-test/reviews", json=review, headers=headers
            ).status_code
            == 401
        )


def test_provider_does_not_offer_global_cancellation(rig, generation):
    services, caller, _, _, adapter, _ = rig
    job, _ = adapter.submit(caller, generation)
    with pytest.raises(DomainError, match="cancel unsupported"):
        services.jobs.cancel(
            Caller("owner", "owner-test"),
            job["job_id"],
            dict(
                schema_version="1.0",
                operation_id="cancel-test",
                project_id="project-test",
                expected_revision=0,
            ),
        )
    assert services.jobs.get("project-test", job["job_id"])["state"] == "planned"


def test_separate_submit_status_wait_download_use_same_endpoint(rig):
    http = rig[3]
    with httpx.Client(transport=httpx.MockTransport(http)) as transport:
        client = ComfyClient("http://separate.test:9999", transport)
        prompt_id = client.submit(http.graph, "client-known")
        assert client.status(prompt_id)
        result = client.wait(prompt_id, timeout=1, poll=0)
        assert client.download(result["outputs"]["save"]["images"][0])
        assert all(r.url.host == "separate.test" for r in http.calls)


def test_measured_evidence_requires_explicit_telemetry_and_clean_images_only(rig, review):
    services, caller, artifacts, http, adapter, _ = rig
    artifacts.add("telemetry-test", b'{"samples":16}')
    review["telemetry_artifact_ids"] = ["telemetry-test"]
    http.vlm_result["findings"][0]["evidence"] = [
        {
            "kind": "measured",
            "artifact_id": "telemetry-test",
            "description": "Telemetry reports 16 samples.",
        }
    ]
    adapter.request_review(caller, review)
    assert adapter.pump("project-test")[0]["state"] == "succeeded"
    request = json.loads(posts(http, "/v1/chat/completions")[0].content)
    user_content = request["messages"][1]["content"]
    assert len(user_content) == 2  # Specified image plus criteria/explicit telemetry only.
    assert json.loads(user_content[0]["text"])["telemetry"] == {"telemetry-test": {"samples": 16}}
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        assert tx.require("findings", "review-test-finding-0")["evidence"][0]["kind"] == "measured"


def test_completion_route_exact_attempt_scope_idempotency_and_no_findings_in_result(rig, review):
    services, caller, _, http, adapter, _ = rig
    ticket, _ = adapter.request_review(caller, review)
    job = services.jobs.get("project-test", ticket["job_id"])
    completion = dict(
        schema_version="1.0",
        operation_id="complete-once",
        project_id="project-test",
        review_id=review["review_id"],
        status="ready",
        findings=findings(review, http.vlm_result),
    )
    wrong = replace(adapter.worker(job), attempt_id="unrelated-attempt")
    with pytest.raises(DomainError):
        adapter.complete_review(wrong, completion)
    token = services.auth.issue_capability(adapter.worker(job))
    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "complete-once"}
    with TestClient(
        create_app(services.settings, services=services, routers=[router]),
        base_url=services.settings.origin,
    ) as client:
        response = client.post(
            "/internal/v1/reviews/review-test/complete", json=completion, headers=headers
        )
        assert response.status_code == 200 and response.json()["status"] == "ready"
        assert "findings" not in response.json()
        assert (
            client.post(
                "/internal/v1/reviews/review-test/complete", json=completion, headers=headers
            ).json()
            == response.json()
        )
        assert (
            client.post(
                "/internal/v1/reviews/wrong-review/complete", json=completion, headers=headers
            ).status_code
            == 403
        )
    assert services.jobs.get("project-test", job["job_id"])["state"] == "succeeded"
    assert adapter.pump("project-test") == [] and not http.calls


def test_frozen_budget_is_not_reset_by_default_change_or_reuse_of_job_id(rig, generation):
    services, caller, _, _, adapter, _ = rig
    adapter.submit(caller, generation)
    services.settings = replace(services.settings, generation_attempt_limit=300)
    with pytest.raises(DomainError) as error:
        adapter.submit(caller, {**generation, "seed": "43"})
    assert error.value.code == "idempotency_conflict"
    for i in range(2):
        adapter.submit(caller, {**generation, "job_id": f"correction-{i}"})
    with pytest.raises(DomainError) as error:
        adapter.submit(caller, {**generation, "job_id": "new-default-bypass"})
    assert error.value.code == "generation_budget_exhausted"
    assert len(reservations(services)) == 3


def test_missing_host_policy_cannot_fall_back_to_agent_claim(rig, generation):
    services, caller, _, _, adapter, _ = rig
    with services.registry.project_store("project-test").transaction() as tx:
        work = tx.require("work", caller.work_id)
        work["generation_task_id"] = "host-task-with-no-policy"
        work["state_revision"] += 1
        tx.put("work", caller.work_id, work, schema="WorkRecord", expected_revision=0)
    generation["generation_task_id"] = "host-task-with-no-policy"
    generation["target_id"] = "host-task-with-no-policy"
    with pytest.raises(DomainError) as error:
        adapter.submit(caller, generation)
    assert error.value.code == "capability_unavailable"
    assert reservations(services) == []


def test_waiting_dispatch_race_across_adapters_submits_once(rig, generation):
    services, caller, artifacts, http, adapter, _ = rig
    adapter.submit(caller, generation)
    http.queue_busy = True
    adapter.pump("project-test")
    http.queue_busy = False
    restarted = ProviderService(services, registrar=artifacts.register, client=adapter.client)
    with concurrent.futures.ThreadPoolExecutor(2) as executor:
        futures = [executor.submit(a.pump, "project-test") for a in (adapter, restarted)]
        for future in futures:
            future.result()
    assert len(posts(http, "/prompt")) == 1
    assert services.jobs.get("project-test", generation["job_id"])["state"] == "succeeded"


@pytest.mark.parametrize("seed", [None, "9007199254740992"])
def test_seed_requires_exact_json_safe_value(rig, generation, seed):
    _, caller, _, http, adapter, _ = rig
    with pytest.raises(DomainError):
        adapter.submit(caller, {**generation, "seed": seed})
    assert not http.calls


def test_three_findings_only_one_owner_amendment_submitted_once(rig, review):
    services, caller, _, http, adapter, _ = rig
    original = http.vlm_result["findings"][0]
    http.vlm_result = {
        "findings": [dict(original, observation=f"Observation {i}") for i in range(3)]
    }
    ticket, _ = adapter.request_review(caller, review)
    assert adapter.pump("project-test")[0]["state"] == "succeeded"
    assert set(ticket) == {"schema_version", "review_id", "job_id", "status"}
    approved = dict(
        finding_id="review-test-finding-1",
        finding_revision=1,
        approved_text="Owner's amended instruction",
        target=review["targets"][0],
    )
    store = services.registry.project_store("project-test")
    for _ in range(2):
        # This is the trusted owner-Queue transaction seam, not a native/tool approval call.
        with store.transaction() as tx:
            services.extensions["reviews"].validate_and_submit_findings(
                tx, "owner-queue-once", [approved]
            )
    with store.transaction(write=False) as tx:
        rows = tx.all("findings")
        assert len(rows) == 3
        assert sum(f["state"] == "submitted" for f in rows) == 1
        saved = tx.require("findings", approved["finding_id"])
        assert saved["observation"] == "Observation 1" and saved["revision"] == 2
        assert tx.require("review.submissions", approved["finding_id"])["approved"] == approved
        assert len(tx.all("review.submissions")) == 1
    assert len(posts(http, "/v1/chat/completions")) == 1


def test_vlm_does_not_jit_load_or_use_text_only_model(rig, review):
    _, caller, _, http, adapter, _ = rig
    http.vision_loaded = False
    adapter.request_review(caller, review)
    result = adapter.pump("project-test")[0]
    assert result["state"] == "failed" and result["error_code"] == "capability_unavailable"
    assert not posts(http, "/v1/chat/completions")


def test_only_recorded_owner_extension_can_raise_same_task_allowance(rig, generation):
    services, caller, _, _, adapter, _ = rig
    for i in range(3):
        adapter.submit(caller, {**generation, "job_id": f"initial-{i}"})
    store = services.registry.project_store("project-test")
    with store.transaction() as tx:
        with pytest.raises(DomainError):
            extend_generation_task(tx, caller, "task-test", 4, "tool-bypass", 0)
    owner = Caller("owner", "owner-test")
    for replay in (False, True):
        with store.transaction() as tx:
            result, replayed = extend_generation_task(
                tx, owner, "task-test", 4, "owner-extension", 0
            )
            assert replayed is replay and result["limit"] == 4
    adapter.submit(caller, {**generation, "job_id": "owner-authorised-fourth"})
    assert len(reservations(services)) == 4
