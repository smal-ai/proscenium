"""Cold-start packaging checks with disposable storage and no model/provider execution."""

import hashlib
import io
import json
import re
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from copilot import CopilotClient
from fastapi.testclient import TestClient
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.copilot.native import BINARY_SHA256, Configuration, unity_agent

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "apps/frontend/dist"


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    attempts = []

    def blocked(*args, **kwargs):
        attempts.append("unexpected external execution")
        raise AssertionError("Local runtime smoke checks must remain offline")

    async def blocked_async(*args, **kwargs):
        blocked()

    monkeypatch.setattr(CopilotClient, "start", blocked_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)
    yield
    assert attempts == []


def pair(browser):
    operation = str(uuid4())
    response = browser.post(
        "/api/v1/owner/session",
        json={
            "schema_version": "1.0",
            "operation_id": operation,
            "client_nonce": str(uuid4()),
            "pairing_code": browser.app.state.services.auth.issue_pairing(),
        },
        headers={"Origin": str(browser.base_url).rstrip("/"), "Idempotency-Key": operation},
    )
    assert response.status_code == 201
    browser.headers.update(
        {
            "Origin": str(browser.base_url).rstrip("/"),
            "X-CSRF-Token": response.json()["csrf_token"],
        }
    )


def command(browser, path, body):
    payload = {"schema_version": "1.0", "operation_id": str(uuid4()), **body}
    response = browser.post(
        path, json=payload, headers={"Idempotency-Key": payload["operation_id"]}
    )
    response.raise_for_status()
    return response.json()


def test_configuration_resolves_bundled_native_assets():
    settings = Settings.load(ROOT / "config.example.json")
    config = Configuration.load(settings.runtime)
    assert settings.frontend_directory == FRONTEND
    assert config.cli_path == ROOT / "node_modules/@github/copilot-linux-x64/copilot"
    with config.cli_path.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == BINARY_SHA256
    assert all(Path(path).is_dir() for path in config.skill_directories)
    assert "run_unity_workflow" in unity_agent(config)["tools"]
    for path in (
        "tools/unity/export_blender.py",
        "tools/unity/Editor/UnityWorkflow.cs",
        "tools/unity/Runtime/WalkthroughPlayer.cs",
        "tools/unity/Tests/PlayableTests.cs",
    ):
        assert (ROOT / path).is_file()
    lock = json.loads((ROOT / "docs/implementation/runtime-lock.json").read_text())
    assert lock["released_sdk"]["version"] == version("github-copilot-sdk")
    assert lock["released_cli"]["binary_sha256"] == BINARY_SHA256


def test_compiled_ui_and_owner_boundary(tmp_path):
    assert (FRONTEND / "index.html").is_file(), "Run make build before the smoke tests"
    settings = Settings(data_directory=tmp_path / "machine", frontend_directory=FRONTEND)
    with TestClient(create_app(settings), base_url=settings.origin) as browser:
        assert browser.get("/health").status_code == 200
        response = browser.get("/")
        assert response.status_code == 200
        assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', response.text)
        assert any(path.endswith(".js") for path in assets)
        assert any(path.endswith(".css") for path in assets)
        for path in assets:
            assert browser.get(path).content == (FRONTEND / path.lstrip("/")).read_bytes()
        assert browser.get("/api/v1/configuration").status_code == 401
        assert browser.get("/health", headers={"Host": "untrusted.invalid"}).status_code == 403
        assert (
            browser.get("/health", headers={"Origin": "https://untrusted.invalid"}).status_code
            == 403
        )
        pair(browser)
        assert browser.get("/api/v1/configuration").json() == settings.view()
        services = browser.app.state.services
        assert services.extension("copilot").native is None
        assert services.extension("lifecycle").errors == {}
    assert services.ownership.file.closed


def test_project_and_image_survive_quiet_restart(tmp_path):
    settings = Settings(data_directory=tmp_path / "machine", frontend_directory=FRONTEND)
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(image, format="PNG")
    data = image.getvalue()
    with TestClient(create_app(settings), base_url=settings.origin) as browser:
        pair(browser)
        project = command(
            browser,
            "/api/v1/projects",
            {"path": str(tmp_path / "project"), "create": True, "name": "Local smoke project"},
        )
        project_id = project["project_id"]
        prefix = f"/api/v1/projects/{project_id}"
        command(
            browser,
            prefix + "/activate",
            {"project_id": project_id, "expected_revision": project["state_revision"]},
        )
        response = browser.put(
            prefix + "/uploads/smoke-image",
            content=data,
            headers={"Content-Type": "image/png", "Idempotency-Key": "smoke-image"},
        )
        assert response.status_code == 201
        artifact = response.json()
        assert artifact["sha256"] == hashlib.sha256(data).hexdigest()
        assert artifact["dimensions"] == {"width": 16, "height": 16}

    with TestClient(create_app(settings), base_url=settings.origin) as browser:
        pair(browser)
        services = browser.app.state.services
        assert services.registry.active_id() == project_id
        snapshot = browser.get(prefix + "/snapshot").json()
        assert snapshot["project"]["state"] == "awaiting_resume"
        assert snapshot["active_work"] is None and snapshot["jobs"] == []
        with pytest.raises(DomainError, match="explicit Resume"):
            services.gate.require(project_id)
        content = prefix + "/artifacts/smoke-image/content"
        assert browser.get(content).content == data
        partial = browser.get(content, headers={"Range": "bytes=0-7"})
        assert partial.status_code == 206 and partial.content == data[:8]
        assert services.extension("copilot").native is None
        assert services.extension("lifecycle").errors == {}
    assert services.ownership.file.closed


@pytest.mark.parametrize(
    ("module", "arguments", "flag"),
    [
        ("proscenium", [], "--config"),
        ("tools.operations.recovery", ["backup"], "--project-id"),
        ("tools.blender", [], "doctor"),
        ("tools.unity.workflow", [], "--project"),
    ],
)
def test_runtime_cli_imports_without_development_history(module, arguments, flag):
    result = subprocess.run(
        [sys.executable, "-m", module, *arguments, "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert flag in result.stdout
