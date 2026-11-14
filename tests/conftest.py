from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.services import Services


@pytest.fixture
def services(tmp_path: Path):
    settings = Settings(
        data_directory=tmp_path / "machine",
        execution_profiles=frozenset({"offline"}),
        free_space_reserve_bytes=0,
    )
    services = Services.create(settings)
    project = tmp_path / "project"
    project.mkdir()
    services.registry.register(
        dict(
            schema_version="1.0",
            project_id="project-test",
            repository_id="repo-test",
            name="Disposable synthetic project",
            path=str(project),
            native_session_id=None,
            state="inactive",
            state_revision=0,
        )
    )
    services.registry.activate("project-test")
    services.acquire_project("project-test")
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "work",
            "work-test",
            dict(
                schema_version="1.0",
                work_id="work-test",
                project_id="project-test",
                originating_request_id="request-test",
                directive_request_ids=[],
                state="running",
                state_revision=0,
                native_session_id="synthetic-session",
                job_ids=[],
                generation_task_id="task-test",
            ),
            schema="WorkRecord",
        )
    yield services
    services.close()


@pytest.fixture
def caller(services):
    return Caller(
        "tool",
        "tool-test",
        "project-test",
        "work-test",
        services.ownership.epoch,
        operations=("admitToolJob", "retryToolJob", "updateJobState"),
    )


@pytest.fixture
def admission():
    return dict(
        schema_version="1.0",
        operation_id="operation-admit",
        project_id="project-test",
        job_id="job-test",
        kind="unity",
        target=dict(target_id="target-test", version=None, shot_id=None),
        input_digest="a" * 64,
        input_artifact_ids=[],
        execution_profile_id="offline",
    )


@pytest.fixture
def client(services):
    with TestClient(
        create_app(services.settings, services=services), base_url=services.settings.origin
    ) as client:
        yield client


@pytest.fixture
def owner_headers(services, client):
    body = dict(
        schema_version="1.0",
        operation_id="operation-pair",
        client_nonce="client-test",
        pairing_code=services.auth.issue_pairing(),
    )
    response = client.post(
        "/api/v1/owner/session",
        json=body,
        headers={"Origin": services.settings.origin, "Idempotency-Key": body["operation_id"]},
    )
    assert response.status_code == 201
    return {"Origin": services.settings.origin, "X-CSRF-Token": response.json()["csrf_token"]}
