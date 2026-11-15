"""Cold-start packaging checks with disposable storage and no model/provider execution."""

import asyncio
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from copilot import CopilotClient
from fastapi.testclient import TestClient
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.app import create_app
from proscenium.configuration import Settings, _read_dotenv
from proscenium.copilot.native import (
    BINARY_SHA256,
    Configuration,
    Native,
    is_blender_mcp_configured,
    resolve_mcp_servers,
    unity_agent,
)

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


def test_secret_env_precedence_and_private_view(tmp_path, monkeypatch):
    app = tmp_path / "app"
    config_dir = tmp_path / "config"
    app.mkdir()
    config_dir.mkdir()
    monkeypatch.setattr("proscenium.configuration.APP_ROOT", app)
    monkeypatch.delenv("BLENDSWAP_KEY", raising=False)
    (app / ".env").write_text('BLENDSWAP_KEY="app-secret"\n')
    (config_dir / ".env").write_text("export BLENDSWAP_KEY = 'config-secret'\n")
    config = config_dir / "config.json"
    config.write_text("{}")
    settings = Settings.load(config)
    environment = dict(os.environ)
    assert settings._config_dir == config_dir.resolve()
    assert settings.secret_env("BLENDSWAP_KEY") == "config-secret"
    monkeypatch.setenv("BLENDSWAP_KEY", "process-secret")
    assert settings.secret_env("BLENDSWAP_KEY") == "process-secret"
    monkeypatch.setenv("BLENDSWAP_KEY", "")
    assert settings.secret_env("BLENDSWAP_KEY") == "config-secret"
    monkeypatch.delenv("BLENDSWAP_KEY")
    (config_dir / ".env").write_text("BLENDSWAP_KEY=\n")
    assert settings.secret_env("BLENDSWAP_KEY") == "app-secret"
    (config_dir / ".env").unlink()
    (config_dir / ".env").symlink_to(app / ".env")
    assert _read_dotenv(config_dir / ".env") == {}
    assert settings.secret_env("BLENDSWAP_KEY") == "app-secret"
    (app / ".env").unlink()
    assert settings.secret_env("BLENDSWAP_KEY") is None
    assert dict(os.environ) == environment
    assert all(
        secret not in json.dumps(settings.view())
        for secret in (
            "process-secret",
            "config-secret",
            "app-secret",
            "BLENDSWAP_KEY",
            "_config_dir",
        )
    )
    assert "_config_dir" not in repr(settings)
    assert Settings(_config_dir=config_dir) == Settings(_config_dir=app)


def test_dotenv_parser_and_bounds(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "\n # comment\nexport KEY = 'single quoted'\nDOUBLE=\"double quoted\"\n"
        "PLAIN = value=with=equals \n_lower=valid\n9INVALID=no\nBAD-KEY=no\n"
        "NO_EQUALS\nEMPTY=\nUNMATCHED='literal\n"
    )
    assert _read_dotenv(path) == {
        "KEY": "single quoted",
        "DOUBLE": "double quoted",
        "PLAIN": "value=with=equals",
        "_lower": "valid",
        "EMPTY": "",
        "UNMATCHED": "'literal",
    }
    path.write_text("KEY=" + "x" * 65536)
    assert _read_dotenv(path) == {}
    assert _read_dotenv(tmp_path) == {}
    assert _read_dotenv(tmp_path / "missing") == {}


def test_synthetic_settings_never_read_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("BLENDSWAP_KEY", raising=False)

    def forbidden(path):
        raise AssertionError("Synthetic settings must not read host dotenv files")

    monkeypatch.setattr("proscenium.configuration._read_dotenv", forbidden)
    assert Settings().secret_env("BLENDSWAP_KEY") is None
    assert Settings.load(tmp_path / "missing.json").secret_env("BLENDSWAP_KEY") is None
    for name in ("lowercase", "BAD-NAME", "9KEY", "", "KEY\n"):
        with pytest.raises(ValueError, match="Invalid environment variable name"):
            Settings().secret_env(name)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"_config_dir": str(tmp_path)}))
    with pytest.raises(ValueError, match="Unknown configuration fields"):
        Settings.load(config)


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_mcp_optional_bearer_wire_copy(transport):
    servers = {
        "blender": {"type": "stdio", "command": "uvx", "args": ["mcp-for-blender==2.3.0"]},
        "blendswap": {
            "type": transport,
            "url": "https://blendswap.com/api/mcp",
            "bearer_token_env": "BLENDSWAP_KEY",
            "headers": {"Accept": "application/json"},
        },
    }
    config = Configuration.load({"worker_model": "fixture", "mcp_servers": servers})
    wire = resolve_mcp_servers(config.mcp_servers, lambda name: "synthetic-token")
    assert wire["blendswap"]["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer synthetic-token",
    }
    assert "bearer_token_env" not in wire["blendswap"]
    wire["blender"]["args"].append("wire-only")
    assert config.mcp_servers == servers
    assert config.mcp_servers["blender"]["args"] == ["mcp-for-blender==2.3.0"]
    assert "Authorization" not in config.mcp_servers["blendswap"]["headers"]
    assert "synthetic-token" not in repr(config)
    for token in (None, ""):
        assert resolve_mcp_servers(config.mcp_servers, lambda name: token) == {
            "blender": servers["blender"]
        }


@pytest.mark.parametrize("session_id", [None, "session-one"])
@pytest.mark.parametrize("token", [None, "synthetic-token"])
def test_native_session_uses_resolved_optional_mcp_copy(tmp_path, monkeypatch, session_id, token):
    monkeypatch.delenv("BLENDSWAP_KEY", raising=False)
    if token:
        monkeypatch.setenv("BLENDSWAP_KEY", token)
    binary = tmp_path / "copilot"
    binary.write_bytes(b"offline fixture, never executed")
    monkeypatch.setattr(
        "proscenium.copilot.native.BINARY_SHA256", hashlib.sha256(binary.read_bytes()).hexdigest()
    )
    servers = {
        "blender": {"type": "stdio", "command": "uvx", "args": ["mcp-for-blender==2.3.0"]},
        "blendswap": {
            "type": "http",
            "url": "https://blendswap.com/api/mcp",
            "bearer_token_env": "BLENDSWAP_KEY",
        },
    }
    settings = Settings(
        data_directory=tmp_path / "machine",
        runtime={"worker_model": "fixture", "cli_path": str(binary), "mcp_servers": servers},
    )
    native = Native(
        SimpleNamespace(
            settings=settings,
            registry=SimpleNamespace(get=lambda project_id: {"path": str(tmp_path)}),
        )
    )
    calls = []

    async def create_session(**kwargs):
        calls.append(("create", None, kwargs))
        return "offline-session"

    async def resume_session(identity, **kwargs):
        calls.append(("resume", identity, kwargs))
        return "offline-session"

    native.client = SimpleNamespace(create_session=create_session, resume_session=resume_session)
    assert asyncio.run(native.open("project-one", session_id, {})) == "offline-session"
    method, identity, wire = calls[0]
    assert (method, identity) == (("resume", session_id) if session_id else ("create", None))
    if token:
        assert wire["mcp_servers"]["blendswap"]["headers"] == {"Authorization": f"Bearer {token}"}
        assert "bearer_token_env" not in wire["mcp_servers"]["blendswap"]
    else:
        assert "blendswap" not in wire["mcp_servers"]
    assert native.config.mcp_servers == servers
    assert "Authorization" not in servers["blendswap"]
    assert "synthetic-token" not in repr(native.config)


@pytest.mark.parametrize(
    "server",
    [
        {"type": "stdio", "bearer_token_env": "BLENDSWAP_KEY"},
        {"type": "local", "bearer_token_env": "BLENDSWAP_KEY"},
        {"type": "http", "bearer_token_env": "bad-name"},
        {"type": "http", "bearer_token_env": "KEY\n"},
        {"type": "http", "bearer_token_env": 42},
        {
            "type": "http",
            "bearer_token_env": "BLENDSWAP_KEY",
            "headers": {"aUtHoRiZaTiOn": "inline"},
        },
        {"type": "http", "headers": {"Authorization": "Bearer inline"}},
        {"type": "http", "headers": None},
        {"type": "http", "headers": []},
        {"type": "http", "headers": {"Accept": 42}},
        {"type": "http", "headers": {42: "value"}},
    ],
)
def test_invalid_mcp_authentication_rejected(server):
    with pytest.raises(ValueError):
        Configuration.load({"worker_model": "fixture", "mcp_servers": {"blendswap": server}})


@pytest.mark.parametrize("servers", [{"": {}}, {" ": {}}, {1: {}}, {"blender": []}, []])
def test_invalid_mcp_entries_rejected(servers):
    with pytest.raises(ValueError):
        Configuration.load({"worker_model": "fixture", "mcp_servers": servers})


@pytest.mark.parametrize(
    ("servers", "expected"),
    [
        ({"blendswap": {"type": "http", "url": "https://blendswap.com/api/mcp"}}, False),
        ({"BLENDER": {}}, True),
        ({"blender-mcp": {}}, True),
        ({"mcp-for-blender": {}}, True),
        ({"authoring": {"type": "stdio", "command": "blender-mcp"}}, True),
        ({"authoring": {"type": "local", "args": ["blender_mcp"]}}, True),
        ({"authoring": {"command": "uvx", "args": ["mcp-for-blender==2.3.0"]}}, True),
        ({"other": {"type": "http", "args": ["mcp-for-blender"]}}, False),
        ({}, False),
    ],
)
def test_blender_mcp_detection(servers, expected):
    assert is_blender_mcp_configured(servers) is expected


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
