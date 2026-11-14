from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fixtures.doubles import FakeProvider, FakeRuntime, FakeWorker
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import validate
from proscenium.db.outbox import dispatch_one
from proscenium.services import Services


def admit(services, caller, admission):
    services.gate.resume("project-test", reconciliation_evidence="synthetic explicit Resume")
    job, replayed = services.jobs.admit(caller, admission)
    assert not replayed
    return job


def worker(job):
    return Caller(
        "worker",
        "worker-test",
        "project-test",
        job_id=job["job_id"],
        attempt_id=job["attempt_id"],
        input_digest=job["input_digest"],
        operations=("updateJobState",),
    )


def update(job, state, operation, **extra):
    return dict(
        schema_version="1.0",
        operation_id=operation,
        project_id=job["project_id"],
        job_id=job["job_id"],
        attempt_id=job["attempt_id"],
        expected_revision=job["state_revision"],
        state=state,
        availability=job["availability"],
        **extra,
    )


def test_job_admission_http_atomic_idempotency(services, client, caller, admission):
    services.gate.resume("project-test", reconciliation_evidence="synthetic explicit Resume")
    token = services.auth.issue_capability(caller)
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": admission["operation_id"]}
    first = client.post("/internal/v1/jobs", json=admission, headers=headers)
    again = client.post("/internal/v1/jobs", json=admission, headers=headers)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json() == again.json()
    validate("JobRecord", first.json())
    conflict = client.post(
        "/internal/v1/jobs", json={**admission, "input_digest": "b" * 64}, headers=headers
    )
    assert conflict.status_code == 409
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        assert len(tx.all("jobs")) == len(tx.all("attempts")) == tx.watermark() == 1
        event = tx.events()["events"][0]
        assert event["payload"] == first.json()
        assert event["work_id"] == "work-test"
        assert "Authorization" not in str(event)


def test_cancellation_race_preserves_observed_success(services, caller, admission):
    job = admit(services, caller, admission)
    job, _ = services.jobs.update(worker(job), update(job, "starting", "operation-start"))
    services.jobs.cancellers["unity"] = lambda job: None
    body = dict(
        schema_version="1.0",
        operation_id="operation-cancel",
        project_id="project-test",
        expected_revision=job["state_revision"],
    )
    receipt, replayed = services.jobs.cancel(Caller("owner", "owner-test"), "job-test", body)
    assert not replayed and receipt["state"] == "cancel_requested"
    job = services.jobs.get("project-test", "job-test")
    observed = FakeWorker().cancellation_observation()
    job, _ = services.jobs.update(
        worker(job), update(job, observed, "operation-finished", native_exit_code=0)
    )
    assert job["state"] == "succeeded"
    receipt, replayed = services.jobs.cancel(Caller("owner", "owner-test"), "job-test", body)
    assert (
        replayed and receipt["state"] == "cancel_requested"
    )  # original acknowledgement, not new effect
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        assert tx.require("attempts", job["attempt_id"])["state"] == "succeeded"
    retry = dict(
        schema_version="1.0",
        operation_id="retry-success",
        project_id="project-test",
        job_id="job-test",
        expected_revision=job["state_revision"],
    )
    with pytest.raises(DomainError):
        services.jobs.retry(caller, retry)


def test_stale_revisions_retries_and_old_attempt_rejected(services, caller, admission):
    first = admit(services, caller, admission)
    failed, _ = services.jobs.update(
        worker(first), update(first, "failed", "operation-failed", native_exit_code=1)
    )
    retry = dict(
        schema_version="1.0",
        operation_id="operation-retry",
        project_id="project-test",
        job_id="job-test",
        expected_revision=failed["state_revision"],
    )
    second, replayed = services.jobs.retry(caller, retry)
    assert (
        not replayed
        and second["attempt_number"] == 2
        and second["attempt_id"] != first["attempt_id"]
    )
    again, replayed = services.jobs.retry(caller, retry)
    assert replayed and again == second
    with pytest.raises(DomainError) as error:
        services.jobs.update(worker(first), update(first, "starting", "late-old-attempt"))
    assert error.value.code == "revision_conflict"
    with pytest.raises(DomainError) as error:
        services.jobs.update(worker(first), update(second, "starting", "forged-new-attempt"))
    assert error.value.code == "scope_denied"
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        attempts = tx.all("attempts")
        assert len(attempts) == 2
        assert {a["state"] for a in attempts} == {"failed", "planned"}


def test_restart_is_read_only_until_explicit_resume(services, caller, admission):
    job = admit(services, caller, admission)
    settings = services.settings
    services.close()
    restored = Services.create(settings)
    try:
        assert restored.jobs.get("project-test", job["job_id"]) == job
        with pytest.raises(DomainError) as error:
            restored.jobs.admit(
                replace(caller, owner_epoch=restored.ownership.epoch),
                {**admission, "operation_id": "new-operation", "job_id": "new-job"},
            )
        assert error.value.code == "project_busy"
        # A detached exact-attempt worker can report an already admitted outcome after restart.
        job, _ = restored.jobs.update(
            worker(job), update(job, "failed", "surviving-worker", native_exit_code=2)
        )
        assert job["state"] == "failed"
    finally:
        restored.close()


def test_dispatch_ack_loss_not_replayed(services):
    store = services.registry.project_store("project-test")
    runtime = FakeRuntime(outcome="ack_lost")
    with store.transaction() as tx:
        tx.enqueue("intent-test", "native.send", {"request_id": "request-test"})
    with pytest.raises(DomainError):
        dispatch_one(store, "intent-test", services.gate, {"native.send": runtime.send})
    assert runtime.sent == []
    services.gate.resume("project-test", reconciliation_evidence="synthetic explicit Resume")
    with pytest.raises(TimeoutError):
        dispatch_one(store, "intent-test", services.gate, {"native.send": runtime.send})
    with pytest.raises(DomainError):
        dispatch_one(store, "intent-test", services.gate, {"native.send": runtime.send})
    assert len(runtime.sent) == 1
    with store.transaction(write=False) as tx:
        assert (
            tx.connection.execute("SELECT state FROM outbox").fetchone()[0] == "outcome_uncertain"
        )


def test_native_event_dedup_attributed_before_ack(services):
    store = services.registry.project_store("project-test")
    payload = dict(
        invocation_id="invocation-original",
        name="synthetic.tool",
        state="running",
        summary="Synthetic activity",
        artifact_ids=[],
        model_id=None,
    )
    with store.transaction() as tx:
        first = tx.emit(
            "tool.started",
            payload,
            actor={"kind": "tool", "id": "tool-test"},
            work_id="work-test",
            native_session_id="session-a",
            native_event_id="event-1",
        )
        duplicate = tx.emit(
            "tool.started",
            payload,
            actor={"kind": "tool", "id": "tool-test"},
            work_id="work-test",
            native_session_id="session-a",
            native_event_id="event-1",
        )
        distinct = tx.emit(
            "tool.started",
            payload,
            actor={"kind": "tool", "id": "tool-test"},
            work_id="work-test",
            native_session_id="session-b",
            native_event_id="event-1",
        )
        assert first == duplicate and distinct["sequence"] == first["sequence"] + 1
        with pytest.raises(DomainError):
            tx.events(after=99)
        assert len(tx.events(after=0)["events"]) == 2


def test_resource_claim_not_stolen_and_project_switch_blocked(
    services, caller, admission, tmp_path: Path
):
    job = admit(services, caller, admission)
    assert services.resources.acquire("heavy-gpu", "project-test", job["job_id"], job["attempt_id"])
    assert not services.resources.acquire("heavy-gpu", "project-test", "other-job", "other-attempt")
    assert not services.resources.acquire(
        "heavy-gpu", "other-project", job["job_id"], job["attempt_id"]
    )
    with pytest.raises(DomainError):
        services.resources.release(
            "heavy-gpu", "project-test", "other-job", "other-attempt", terminal_evidence="not ours"
        )
    with pytest.raises(DomainError):
        services.registry.assert_settled("project-test")
    services.resources.release(
        "heavy-gpu",
        "project-test",
        job["job_id"],
        job["attempt_id"],
        terminal_evidence="synthetic observed terminal",
    )
    assert services.resources.acquire("heavy-gpu", "project-test", "other-job", "other-attempt")


def test_provider_double_reports_uncertainty():
    provider = FakeProvider(outcome="uncertain")
    with pytest.raises(TimeoutError):
        provider.submit({"input": "synthetic"})
    assert provider.submissions == 1


def test_concurrent_cancel_and_completion_use_cas(services, caller, admission):
    job = admit(services, caller, admission)
    job, _ = services.jobs.update(worker(job), update(job, "starting", "race-start"))
    services.jobs.cancellers["unity"] = lambda job: None
    cancel = dict(
        schema_version="1.0",
        operation_id="race-cancel",
        project_id="project-test",
        expected_revision=job["state_revision"],
    )
    complete = update(job, "succeeded", "race-complete", native_exit_code=0)

    def run(effect):
        try:
            effect()
            return "committed"
        except DomainError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                run,
                [
                    lambda: services.jobs.cancel(Caller("owner", "owner-test"), "job-test", cancel),
                    lambda: services.jobs.update(worker(job), complete),
                ],
            )
        )
    assert sorted(results) == ["committed", "revision_conflict"]
    observed = services.jobs.get("project-test", "job-test")
    if observed["state"] == "cancel_requested":
        observed, _ = services.jobs.update(
            worker(observed), update(observed, "succeeded", "race-reconciled", native_exit_code=0)
        )
    assert observed["state"] == "succeeded"


def test_matching_admission_replay_does_not_require_open_execution(services, caller, admission):
    original = admit(services, caller, admission)
    services.gate.close("project-test")
    replayed, was_replayed = services.jobs.admit(caller, admission)
    assert was_replayed and replayed == original


def test_worker_cannot_replay_another_attempts_operation(services, caller, admission):
    job = admit(services, caller, admission)
    body = update(job, "starting", "private-worker-operation")
    services.jobs.update(worker(job), body)
    with pytest.raises(DomainError) as error:
        services.jobs.update(replace(worker(job), job_id="other-job"), body)
    assert error.value.code == "scope_denied"
