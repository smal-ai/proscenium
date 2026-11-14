"""Allocation helpers only. These tests never start SSH, a browser or an SDK runtime."""
# ruff: noqa: E402 -- tests explicitly expose maintained repository helpers

import asyncio
import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from proscenium.configuration import Settings

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/native/studio"))

from tools.operations.ssh_service import witness_lifespan


def host_module():
    spec = importlib.util.spec_from_file_location(
        "m14_ssh_host", ROOT / "tests/native/studio/m14_ssh_host.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_send_reservation_survives_reload_and_refuses_second_send(tmp_path):
    host_module().reserve_send(tmp_path, {"count": 1})
    with pytest.raises(FileExistsError):
        host_module().reserve_send(tmp_path, {"count": 2})
    assert json.loads((tmp_path / "send-reservation.json").read_text()) == {"count": 1}


def test_lifespan_receipt_precedes_termination(tmp_path):
    from types import SimpleNamespace

    order = []

    @asynccontextmanager
    async def lifetime(app):
        try:
            yield
        finally:
            order.append("closed")

    app = SimpleNamespace(router=SimpleNamespace(lifespan_context=lifetime))

    def persist():
        order.append("receipt")
        (tmp_path / "audit.json").write_text(json.dumps(order))

    witness_lifespan(app, persist)

    async def exercise():
        with pytest.raises(SystemExit):
            async with app.router.lifespan_context(app):
                raise SystemExit(143)

    asyncio.run(exercise())
    assert json.loads((tmp_path / "audit.json").read_text()) == ["closed", "receipt"]


def test_transport_observes_full_body_without_retaining_auth():
    observed = []

    async def app(scope, receive, send):
        assert (await receive())["body"] == b"request bytes"
        await send(
            {
                "type": "http.response.start",
                "status": 206,
                "headers": [(b"content-range", b"bytes 0-3/4"), (b"set-cookie", b"secret")],
            }
        )
        await send({"type": "http.response.body", "body": b"data"})

    async def exercise():
        await host_module().Audit(app, observed)(
            {
                "type": "http",
                "path": "/media",
                "method": "GET",
                "headers": [
                    (b"cookie", b"secret"),
                    (b"authorization", b"secret"),
                    (b"range", b"bytes=0-3"),
                ],
            },
            lambda: asyncio.sleep(0, result={"type": "http.request", "body": b"request bytes"}),
            lambda message: asyncio.sleep(0),
        )

    asyncio.run(exercise())
    assert observed[0]["response_bytes"] == 4
    assert observed[0]["request_bytes"] == 13
    assert observed[0]["status"] == 206
    assert "secret" not in json.dumps(observed)
    assert observed[0]["response_headers"] == {"content-range": "bytes 0-3/4"}


def test_unavailable_configuration_is_declarative_not_health():
    settings = Settings(
        profiles=(
            {
                "profile_id": "authoring",
                "name": "Missing executable",
                "lead_model_id": "gpt-6.1-sol",
                "worker_model_ids": [],
                "scope_status": "unavailable",
            },
        ),
        services=(
            {
                "provider_id": "closed",
                "endpoint": "http://127.0.0.1:65534",
                "capabilities": ["vlm"],
                "model_id": None,
                "available": False,
            },
        ),
    )
    assert settings.view()["profiles"][0]["scope_status"] == "unavailable"
    assert settings.view()["services"][0]["available"] is False
