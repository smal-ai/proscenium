"""Recorded owner cancellation remains live while new execution is held."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from proscenium.api.composition import configure_services
from proscenium.api.errors import DomainError
from proscenium.app import create_app
from proscenium.auth import Caller


class IdleRuntime:
    async def start(self):
        pass

    async def close(self):
        pass


def admitted_render(services, *, launched=False):
    configure_services(services)
    services.extensions["copilot"] = IdleRuntime()
    services.gate.resume("project-test", reconciliation_evidence="Synthetic explicit admission")
    with services.registry.project_store("project-test").transaction() as tx:
        job = services.jobs.create(
            tx,
            job_id="cancel-preview",
            kind="render",
            target={"target_id": "cancel-target", "version": None, "shot_id": None},
            input_digest="a" * 64,
            operation_id="preview-intent",
            work_id="work-test",
            enqueue_kind="preview.render",
        )
    services.gate.close("project-test")
    previews = services.extension("previews")
    if launched:
        root = previews.root(job)
        root.mkdir(parents=True)
        (root / "launch.claim").touch()
    return job


def pair(browser, services):
    body = {
        "schema_version": "1.0",
        "operation_id": "cancel-proof-pair",
        "client_nonce": "cancel-proof-browser",
        "pairing_code": services.auth.issue_pairing(),
    }
    response = browser.post(
        "/api/v1/owner/session",
        json=body,
        headers={"Origin": services.settings.origin, "Idempotency-Key": body["operation_id"]},
    )
    assert response.status_code == 201
    return {"Origin": services.settings.origin, "X-CSRF-Token": response.json()["csrf_token"]}


@pytest.mark.parametrize("launched", [False, True])
def test_owner_http_cancel_reaches_actual_adapter_with_closed_gate(services, monkeypatch, launched):
    job = admitted_render(services, launched=launched)
    previews = services.extension("previews")

    def forbidden(*args, **kwargs):
        pytest.fail("A closed cancellation path must not dispatch a renderer or provider")

    monkeypatch.setattr(previews, "pump", forbidden)
    monkeypatch.setattr(services.extension("providers"), "pump", forbidden)
    with TestClient(
        create_app(services=services, manage_features=True), base_url=services.settings.origin
    ) as browser:
        headers = pair(browser, services)
        command = {
            "schema_version": "1.0",
            "operation_id": "owner-cancel-preview",
            "project_id": "project-test",
            "expected_revision": job["state_revision"],
        }
        response = browser.post(
            "/api/v1/projects/project-test/jobs/cancel-preview/cancel",
            json=command,
            headers={**headers, "Idempotency-Key": command["operation_id"]},
        )
        assert response.status_code == 202
        deadline = time.monotonic() + 3
        marker = previews.root(job) / "cancel.json"
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert marker.exists()
        expected = "cancel_requested" if launched else "cancelled"
        while (
            services.jobs.get("project-test", job["job_id"])["state"] != expected
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        current = services.jobs.get("project-test", job["job_id"])
        assert current["state"] == expected
        assert current.get("native_exit_code") is None
        assert (previews.root(job) / "launch.claim").exists() == launched
        assert not (previews.root(job) / "process.json").exists()
        with pytest.raises(DomainError) as error:
            services.gate.require("project-test")
        assert error.value.code == "project_busy"


def test_uncertain_cancellation_delivery_is_not_blindly_retried(services):
    job = admitted_render(services)
    calls = []

    def uncertain(value):
        calls.append(value["attempt_id"])
        raise TimeoutError("Synthetic cancellation acknowledgement loss")

    services.jobs.cancellers["render"] = uncertain
    services.jobs.cancel(
        Caller("owner", "owner-test"),
        job["job_id"],
        {
            "schema_version": "1.0",
            "operation_id": "uncertain-cancel",
            "project_id": "project-test",
            "expected_revision": job["state_revision"],
        },
    )
    with pytest.raises(TimeoutError):
        services.jobs.dispatch_cancellations("project-test")
    assert services.jobs.dispatch_cancellations("project-test") == []
    assert calls == [job["attempt_id"]]
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        assert (
            tx.connection.execute(
                "SELECT state FROM outbox WHERE id='uncertain-cancel'"
            ).fetchone()[0]
            == "outcome_uncertain"
        )
        assert tx.require("jobs", job["job_id"])["state"] == "cancel_requested"
    asyncio.run(services.extension("providers").close())


def test_old_terminal_cancellation_does_not_cancel_a_new_attempt(services, caller):
    job = admitted_render(services)
    services.jobs.cancel(
        Caller("owner", "owner-test"),
        job["job_id"],
        {
            "schema_version": "1.0",
            "operation_id": "old-cancel",
            "project_id": "project-test",
            "expected_revision": job["state_revision"],
        },
    )
    # The real pre-launch adapter proves there is no launch claim. Its original
    # cancellation outbox acknowledgement has not yet been recorded.
    services.jobs.cancellers["render"](services.jobs.get("project-test", job["job_id"]))
    terminal = services.jobs.get("project-test", job["job_id"])
    assert terminal["state"] == "cancelled"
    services.gate.resume("project-test", reconciliation_evidence="Synthetic explicit retry")
    retried, _ = services.jobs.retry(
        caller,
        {
            "schema_version": "1.0",
            "operation_id": "retry-after-cancel",
            "project_id": "project-test",
            "job_id": job["job_id"],
            "expected_revision": terminal["state_revision"],
        },
    )
    services.gate.close("project-test")
    calls = []
    services.jobs.cancellers["render"] = lambda value: calls.append(value["attempt_id"])
    services.jobs.dispatch_cancellations("project-test")
    assert calls == []
    assert services.jobs.get("project-test", job["job_id"]) == retried
    assert retried["attempt_id"] != job["attempt_id"] and retried["state"] == "planned"
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        assert (
            tx.connection.execute("SELECT state FROM outbox WHERE id='old-cancel'").fetchone()[0]
            == "acknowledged"
        )
    asyncio.run(services.extension("providers").close())
