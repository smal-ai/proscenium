from dataclasses import replace

import pytest
from fastapi import APIRouter, Depends, WebSocket
from fastapi.testclient import TestClient
from proscenium.api.dependencies import owner_websocket
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.contracts import validate
from starlette.websockets import WebSocketDisconnect


def pair_body(services):
    return dict(
        schema_version="1.0",
        operation_id="operation-pair-test",
        client_nonce="client-test",
        pairing_code=services.auth.issue_pairing(),
    )


def test_pairing_retry_bound_to_original_client(services, client):
    body = pair_body(services)
    headers = {"Origin": services.settings.origin, "Idempotency-Key": body["operation_id"]}
    first = client.post("/api/v1/owner/session", json=body, headers=headers)
    second = client.post("/api/v1/owner/session", json=body, headers=headers)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json() == second.json()
    validate("OwnerSession", first.json())
    assert "HttpOnly" in first.headers["set-cookie"]
    assert "SameSite=strict" in first.headers["set-cookie"]
    denied = client.post(
        "/api/v1/owner/session", json={**body, "client_nonce": "other-client"}, headers=headers
    )
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "invalid_pairing"


def test_browser_tool_and_worker_channel_separation(
    services, client, caller, admission, owner_headers
):
    assert client.get("/api/v1/configuration").status_code == 200
    tool_token = services.auth.issue_capability(caller)
    # Even a valid owner session must never be accepted as tool authority.
    response = client.post(
        "/internal/v1/jobs", json=admission, headers={"Idempotency-Key": admission["operation_id"]}
    )
    assert response.status_code == 401
    client.cookies.clear()
    assert (
        client.get(
            "/api/v1/configuration", headers={"Authorization": f"Bearer {tool_token}"}
        ).status_code
        == 401
    )
    wrong = services.auth.issue_capability(
        Caller(
            "worker",
            "worker-test",
            "project-test",
            job_id="job-test",
            attempt_id="attempt-test",
            input_digest="a" * 64,
            operations=("admitToolJob",),
        )
    )
    response = client.post(
        "/internal/v1/jobs",
        json=admission,
        headers={"Authorization": f"Bearer {wrong}", "Idempotency-Key": admission["operation_id"]},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example"},
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
    ],
)
def test_host_and_origin_rejected(services, client, headers):
    response = client.get("/health", headers=headers)
    assert response.status_code == 403
    validate("Error", response.json())


def test_csrf_origin_and_idempotency_required(services, client, owner_headers):
    body = dict(
        schema_version="1.0",
        operation_id="cancel-test",
        project_id="project-test",
        expected_revision=0,
    )
    url = "/api/v1/projects/project-test/jobs/job-test/cancel"
    assert client.post(url, json=body).status_code == 403
    assert (
        client.post(url, json=body, headers={"Origin": services.settings.origin}).status_code == 403
    )
    response = client.post(url, json=body, headers=owner_headers)
    assert response.status_code == 400  # Missing key, not a successful no-op.
    assert response.json()["error"]["code"] == "invalid_request"


def test_stale_tool_epoch_and_active_project_scope(services, client, caller, admission):
    services.gate.resume("project-test", reconciliation_evidence="synthetic explicit Resume")
    token = services.auth.issue_capability(replace(caller, owner_epoch="epoch-old"))
    response = client.post(
        "/internal/v1/jobs",
        json=admission,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": admission["operation_id"]},
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "owner_conflict"
    token = services.auth.issue_capability(replace(caller, project_id="other-project"))
    response = client.post(
        "/internal/v1/jobs",
        json=admission,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": admission["operation_id"]},
    )
    assert response.status_code == 403


def test_json_required_and_unimplemented_feature_not_success(services, client, caller, admission):
    token = services.auth.issue_capability(caller)
    response = client.post(
        "/internal/v1/jobs",
        content="{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain",
            "Idempotency-Key": admission["operation_id"],
        },
    )
    assert response.status_code == 415
    response = client.get("/api/v1/projects")
    assert response.status_code == 404
    validate("Error", response.json())


def test_websocket_origin_and_owner_cookie(services, client, owner_headers):
    router = APIRouter()

    @router.websocket("/synthetic-socket")
    async def synthetic_socket(socket: WebSocket, caller=Depends(owner_websocket)):
        await socket.accept()
        await socket.send_json({"channel": caller.channel})
        await socket.close()

    with TestClient(
        create_app(services.settings, routers=[router], services=services),
        base_url=services.settings.origin,
    ) as browser:
        browser.cookies.update(client.cookies)
        with browser.websocket_connect(
            services.settings.origin.replace("http", "ws") + "/synthetic-socket",
            headers={"Origin": services.settings.origin},
        ) as socket:
            assert socket.receive_json() == {"channel": "owner"}
        with pytest.raises(WebSocketDisconnect):
            with browser.websocket_connect(
                services.settings.origin.replace("http", "ws") + "/synthetic-socket",
                headers={"Origin": "http://evil.example"},
            ):
                pytest.fail("Cross-origin upgrade accepted")
        browser.cookies.clear()
        with pytest.raises(WebSocketDisconnect):
            with browser.websocket_connect(
                services.settings.origin.replace("http", "ws") + "/synthetic-socket",
                headers={"Origin": services.settings.origin},
            ):
                pytest.fail("Unauthenticated upgrade accepted")


def test_pairing_code_expires(services, client, monkeypatch):
    import time

    body = pair_body(services)
    expires = time.time() + 901
    monkeypatch.setattr("proscenium.auth.time.time", lambda: expires)
    response = client.post(
        "/api/v1/owner/session",
        json=body,
        headers={"Origin": services.settings.origin, "Idempotency-Key": body["operation_id"]},
    )
    assert response.status_code == 401
