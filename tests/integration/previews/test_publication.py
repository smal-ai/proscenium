import io
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.app import create_app
from proscenium.contracts import validate
from proscenium.previews import router
from proscenium.previews.journal import output, sha
from proscenium.previews.service import ranges, worker_caller

from .conftest import admit_synthetic


def frame(job, root, ordinal):
    data = io.BytesIO()
    Image.new("RGB", (640, 360), (ordinal * 15, 96, 128)).save(data, format="PNG")
    path = root / f"frames/{ordinal:08d}.png"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data.getvalue())
    return dict(
        schema_version="1.0",
        receipt_id=f"receipt-{ordinal}",
        project_id=job["project_id"],
        job_id=job["job_id"],
        attempt_id=job["attempt_id"],
        version_id="version-test",
        shot_id="shot-test",
        source_frame=ordinal,
        artifact_id=f"artifact-frame-{ordinal}",
        output_path=path.relative_to(root).as_posix(),
        sha256=sha(data.getvalue()),
        dimensions={"width": 640, "height": 360},
        input_digest=job["input_digest"],
    )


def test_request_replay_intent_uniqueness_and_frozen_inputs(previews, preview_caller, request_body):
    job, repeated = previews.request(preview_caller, request_body)
    assert not repeated and job["state"] == "planned"
    previews.services.gate.close("project-test")
    assert previews.request(preview_caller, request_body) == (job, True)
    previews.services.gate.resume("project-test", reconciliation_evidence="offline")
    another = {**request_body, "operation_id": "another-op"}
    assert previews.request(preview_caller, another)[0]["job_id"] == job["job_id"]
    assert previews.request(
        preview_caller, {**another, "operation_id": "same-intent-new-operation"}
    )[1]
    conflict = deepcopy(another)
    conflict["operation_id"] = "conflicting-op"
    conflict["spec"]["samples"] = 16
    with pytest.raises(DomainError, match="idempotency conflict"):
        previews.request(preview_caller, conflict)
    assert (
        previews.services.jobs.inputs("project-test", job["job_id"])["invocation_id"]
        == "invocation-test"
    )
    with previews.services.registry.project_store("project-test").transaction(write=False) as tx:
        assert len(tx.all("jobs")) == 1
        assert tx.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_out_of_order_holes_and_partial_failure(previews, preview_caller, request_body):
    job, root = admit_synthetic(previews, preview_caller, request_body)
    caller = worker_caller(job)
    for ordinal in (12, 1, 3):
        body = frame(job, root, ordinal)
        assert not previews.register(caller, body)[1]
        assert previews.register(caller, body)[1]
    availability = previews.availability("project-test", job["job_id"])
    assert availability["available_ranges"] == [
        {"start": 1, "end": 1},
        {"start": 3, "end": 3},
        {"start": 12, "end": 12},
    ]
    assert availability["playable_ranges"] == []
    failed = previews.state(job, "failed", evidence="Synthetic prelaunch terminal proof")
    assert failed.get("native_exit_code") is None
    availability = previews.availability("project-test", job["job_id"])
    assert availability["failed_ranges"] == [{"start": 2, "end": 2}, {"start": 4, "end": 11}]
    assert availability["pending_ranges"] == []
    validate("FrameAvailability", availability)


@pytest.mark.parametrize("corruption", ["hash", "dimensions", "attempt", "scope", "symlink"])
def test_receipt_rejects_bad_bytes_and_wrong_attempt(
    previews, preview_caller, request_body, corruption
):
    job, root = admit_synthetic(previews, preview_caller, request_body)
    body = frame(job, root, 1)
    caller = worker_caller(job)
    if corruption == "hash":
        body["sha256"] = "0" * 64
    elif corruption == "dimensions":
        body["dimensions"]["width"] = 320
    elif corruption == "attempt":
        body["attempt_id"] = "another-attempt"
    elif corruption == "scope":
        caller = replace(caller, channel="tool")
    else:
        original = root / body["output_path"]
        target = root / "elsewhere.png"
        original.rename(target)
        original.symlink_to(target)
    with pytest.raises((DomainError, ValueError)):
        previews.register(caller, body)
    assert previews.availability("project-test", job["job_id"])["available_ranges"] == []


def test_frame_conflict_and_event_transaction_rollback(
    previews, preview_caller, request_body, monkeypatch
):
    from proscenium.db.store import Transaction

    job, root = admit_synthetic(previews, preview_caller, request_body)
    body = frame(job, root, 1)
    original = Transaction.emit

    def fail(tx, name, *args, **kwargs):
        if name == "frame.available":
            raise RuntimeError("Injected event failure")
        return original(tx, name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Transaction, "emit", fail)
        with pytest.raises(RuntimeError):
            previews.register(worker_caller(job), body)
    with previews.services.registry.project_store("project-test").transaction(write=False) as tx:
        assert not tx.all("frames") and not tx.all("artifacts")
    previews.register(worker_caller(job), body)
    with pytest.raises(DomainError, match="frame conflict"):
        previews.register(worker_caller(job), {**body, "sha256": "0" * 64})


def test_http_owner_tool_worker_channels_and_order(previews, preview_caller, request_body):
    services = previews.services
    with TestClient(
        create_app(services.settings, services=services, routers=[router]),
        base_url=services.settings.origin,
    ) as client:
        token = services.auth.issue_capability(preview_caller)
        headers = {
            "Authorization": "Bearer " + token,
            "Idempotency-Key": request_body["operation_id"],
        }
        response = client.post("/internal/v1/previews", json=request_body, headers=headers)
        assert response.status_code == 202, response.text
        assert (
            client.post("/internal/v1/previews", json=request_body, headers=headers).status_code
            == 200
        )
        job = response.json()
        root = previews.root(job)
        root.mkdir(parents=True)
        from proscenium.previews.journal import atomic

        atomic(
            root / "task.json", {key: job[key] for key in ("job_id", "attempt_id", "input_digest")}
        )
        body = frame(job, root, 1)
        headers["Idempotency-Key"] = body["receipt_id"]
        assert (
            client.post("/internal/v1/frame-receipts", json=body, headers=headers).status_code
            == 401
        )
        worker_token = services.auth.issue_capability(worker_caller(job))
        headers["Authorization"] = "Bearer " + worker_token
        assert (
            client.post("/internal/v1/frame-receipts", json=body, headers=headers).status_code
            == 201
        )
        assert (
            client.get(
                f"/api/v1/projects/project-test/jobs/{job['job_id']}/availability", headers=headers
            ).status_code
            == 401
        )


def test_interval_and_root_paths(tmp_path):
    assert ranges({1, 2, 4, 8, 9}) == [
        {"start": 1, "end": 2},
        {"start": 4, "end": 4},
        {"start": 8, "end": 9},
    ]
    for bad in ("../x", "/etc/passwd", "a//b", "a/./b"):
        with pytest.raises(ValueError):
            output(tmp_path, bad)


def test_parent_atomic_job_update_seam_shares_receipt_transaction(
    previews, preview_caller, request_body, monkeypatch
):
    job, root = admit_synthetic(previews, preview_caller, request_body)
    body = frame(job, root, 1)

    def update_tx(tx, caller, update, **kwargs):
        assert tx.require("frames", body["receipt_id"]) == body
        assert tx.require("artifacts", body["artifact_id"])["sha256"] == body["sha256"]
        assert update["completed_frames"] == 1 and update["availability"] == "partial"
        raise RuntimeError("Injected parent job update failure")

    monkeypatch.setattr(previews.services.jobs, "update_in_transaction", update_tx, raising=False)
    with pytest.raises(RuntimeError):
        previews.register(worker_caller(job), body)
    with previews.services.registry.project_store("project-test").transaction(write=False) as tx:
        assert not tx.all("frames") and not tx.all("artifacts")
