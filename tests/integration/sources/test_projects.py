import json
from pathlib import Path

from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.sources.common import git

EXAMPLES = Path(__file__).resolve().parents[3] / "docs/contracts/examples"


def test_create_open_recent_and_settings_are_private(
    client, owner_headers, source_services, tmp_path
):
    path = tmp_path / "new-workspace"
    command = dict(
        schema_version="1.0",
        operation_id="open-test",
        path=str(path),
        create=True,
        name="Owner project",
    )
    headers = {**owner_headers, "Idempotency-Key": command["operation_id"]}
    response = client.post("/api/v1/projects", json=command, headers=headers)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["native_session_id"] is None
    assert record["state"] == "inactive"
    assert client.post("/api/v1/projects", json=command, headers=headers).status_code == 200
    assert len(client.get("/api/v1/projects").json()["items"]) == 2
    for directory in ("brief", "assets", "scenes", "storyboards", "sequences", ".proscenium"):
        assert (path / directory).is_dir()
    assert b"filter=lfs" in (path / ".gitattributes").read_bytes()
    assert b".proscenium/" in (path / ".gitignore").read_bytes()
    assert git(path, "config", "--local", "--get", "filter.lfs.required").strip() == b"true"
    config = json.loads((path / "project.json").read_bytes())
    assert config["project_id"] == record["project_id"]
    service = source_services.extension("projects")
    body = dict(
        schema_version="1.0",
        operation_id="settings-test",
        project_id=record["project_id"],
        expected_revision=0,
        generation_attempt_limit=7,
        default_profile_id="offline",
    )
    result, _ = service.update_settings(Caller("owner", "owner-test"), body)
    assert result["state_revision"] == 1
    assert service.update_settings(Caller("owner", "owner-test"), body)[1]
    # Arbitrary agent-writable source JSON cannot alter the private settings.
    config["generation_attempt_limit"] = 999
    (path / "project.json").write_bytes(canonical(config))
    assert service.settings(record["project_id"])["generation_attempt_limit"] == 7
    stale = client.put(
        f"/api/v1/projects/{record['project_id']}/settings",
        json={**body, "operation_id": "settings-stale"},
        headers={**owner_headers, "Idempotency-Key": "settings-stale"},
    )
    assert stale.status_code == 409


def test_snapshot_contains_full_held_request_and_work_at_watermark(
    client, owner_headers, source_services
):
    request = json.loads((EXAMPLES / "request-record.json").read_bytes())
    request["project_id"] = request["submission"]["project_id"] = request["receipt"][
        "project_id"
    ] = "project-test"
    with source_services.registry.project_store("project-test").transaction() as tx:
        tx.put("requests", request["request_id"], request, schema="RequestRecord")
        event = tx.emit("request.accepted", request, actor={"kind": "user", "id": "owner-test"})
    response = client.get("/api/v1/projects/project-test/snapshot")
    assert response.status_code == 200, response.text
    snapshot = response.json()
    validate("ProjectSnapshot", snapshot)
    assert snapshot["held_requests"] == [request]
    assert snapshot["active_work"]["work_id"] == "work-test"
    assert snapshot["event_sequence"] == event["sequence"]
    assert snapshot["items"] == snapshot["jobs"] == snapshot["reviews"] == []


def settle(source_services):
    with source_services.registry.project_store("project-test").transaction() as tx:
        work = tx.require("work", "work-test")
        work.update(state="completed", state_revision=1)
        tx.put("work", "work-test", work, expected_revision=0)
        invocation = tx.require("invocations", "invocation-test")
        invocation["state"] = "completed"
        tx.put("invocations", "invocation-test", invocation, expected_revision=0)


def test_activation_closed_gate_busy_switch_and_replay(
    client, owner_headers, source_services, tmp_path
):
    service = source_services.extension("projects")
    owner = Caller("owner", "owner-test")
    record, _ = service.open(
        owner,
        dict(
            schema_version="1.0",
            operation_id="create-second",
            create=True,
            path=str(tmp_path / "second"),
        ),
    )
    command = dict(
        schema_version="1.0",
        operation_id="activate-test",
        project_id=record["project_id"],
        expected_revision=0,
    )
    url = f"/api/v1/projects/{record['project_id']}/activate"
    headers = {**owner_headers, "Idempotency-Key": command["operation_id"]}
    assert client.post(url, json=command, headers=headers).status_code == 409
    settle(source_services)
    response = client.post(url, json=command, headers=headers)
    assert response.status_code == 200, response.text
    assert source_services.registry.active_id() == record["project_id"]
    assert source_services.registry.get("project-test")["state"] == "inactive"
    assert client.post(url, json=command, headers=headers).json()["replayed"]
    snapshot = client.get(f"/api/v1/projects/{record['project_id']}/snapshot").json()
    assert snapshot["event_sequence"] == 1
    assert snapshot["active_work"] is None
    assert snapshot["project"]["state"] == "awaiting_resume"


def test_rebind_moved_workspace_retains_identity_and_database(
    client, owner_headers, source_services, root, tmp_path
):
    settle(source_services)
    moved = tmp_path / "relocated"
    root.rename(moved)
    body = dict(
        schema_version="1.0",
        operation_id="rebind-test",
        project_id="project-test",
        expected_revision=0,
        new_path=str(moved),
    )
    headers = {**owner_headers, "Idempotency-Key": body["operation_id"]}
    response = client.post("/api/v1/projects/project-test/rebind", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert source_services.registry.get("project-test")["path"] == str(moved)
    assert client.post("/api/v1/projects/project-test/rebind", json=body, headers=headers).json()[
        "replayed"
    ]
    assert client.get("/api/v1/projects/project-test/snapshot").status_code == 200


def test_all_normative_w02_operations_are_exported():
    from proscenium.catalogue import projects_router, router

    owners = json.loads((EXAMPLES.parent / "operation-owners.json").read_bytes())
    expected = {
        operation
        for group in owners["groups"]
        if group["owner"] == "W02"
        for operation in group["operations"]
    }
    actual = [
        route.operation_id for feature in (projects_router, router) for route in feature.routes
    ]
    assert set(actual) == expected
    assert len(actual) == len(expected) == 17


def test_duplicate_project_location_requires_explicit_rebind(
    client, owner_headers, source_services, root, tmp_path
):
    import shutil

    duplicate = tmp_path / "duplicate-source-only"
    shutil.copytree(root, duplicate, ignore=shutil.ignore_patterns(".proscenium", ".env*"))
    body = dict(
        schema_version="1.0", operation_id="open-duplicate", path=str(duplicate), create=False
    )
    response = client.post(
        "/api/v1/projects",
        json=body,
        headers={**owner_headers, "Idempotency-Key": body["operation_id"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_location_conflict"
    assert source_services.registry.get("project-test")["path"] == str(root)
