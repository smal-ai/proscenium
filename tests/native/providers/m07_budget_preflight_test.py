"""Offline preflight only. No native model send, provider network or process."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from proscenium.api.composition import configure_services
from proscenium.app import create_app
from proscenium.contracts import validate
from proscenium.providers.sd_turbo_fp16 import bind
from proscenium.services import Services


def load():
    spec = importlib.util.spec_from_file_location(
        "m07_budget_preflight", Path(__file__).with_name("m07_budget_host.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_fixture_standalone_import_graph_policy_and_owner_queue_without_execution(tmp_path):
    module = load()
    services = Services.create(module.settings(tmp_path))
    configure_services(services)
    try:
        provider = services.extension("providers")
        provider.client.close()
        transport = module.ZeroHTTP()
        provider.client = httpx.Client(transport=transport)
        provider.owns_client = True
        fixture = module.fixture(services, tmp_path)
        pid = fixture["project"]["project_id"]
        for body in fixture["envelopes"]:
            validate("GenerationSubmit", body)
        workflow, raw = provider.artifact_bytes(pid, "m07-budget-graph")
        bound = bind(
            json.loads(raw),
            "Admission only",
            "42",
            {fixture["image"]["artifact_id"]: "artifact:fixture"},
            [module.FILENAME],
        )
        assert bound["sample"]["inputs"]["latent_image"] == ["encode", 0]
        assert fixture["image"]["sha256"] == module.PNG_SHA
        assert provider.provider("m07-budget-unprobed", "image")["available"] is None
        assert services.settings.generation_attempt_limit == 3

        async def run():
            app = create_app(services=services, manage_features=False)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url=module.ORIGIN,
                    headers={"Origin": module.ORIGIN},
                ) as client:
                    result = await module.owner_queue(client, services, pid, fixture["prompt"])
                    assert result["status"] == 202
                    assert result["receipt"]["state"] == "held"

        asyncio.run(run())
        with services.registry.project_store(pid).transaction(write=False) as tx:
            assert len(tx.all("requests")) == 1 and not tx.all("jobs")
            assert not tx.all("providers.execution")
        assert not transport.attempts and services.extension("copilot").native is None
    finally:
        services.close()


def test_zero_http_independent_of_resource_claim(tmp_path, monkeypatch):
    module = load()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    guard = module.ZeroHTTP()
    with httpx.Client(transport=guard) as client:
        with pytest.raises(RuntimeError, match="forbids ALL"):
            client.get("http://127.0.0.1:18589/queue")
    assert len(guard.attempts) == 1


def test_one_child_and_no_execution_tools_guard(tmp_path, monkeypatch):
    module = load()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    bounds = module.Bounds()
    data = dict(
        toolName="task", toolCallId="first", toolArgs={"agent_type": "authoring-specialist"}
    )
    assert bounds.pre_tool(data, {}) is None
    assert bounds.pre_tool(data, {}) is None  # Hook replay, not an extra task.
    assert bounds.pre_tool({**data, "toolCallId": "second"}, {})["permissionDecision"] == "deny"
    assert bounds.pre_tool({**data, "agentId": "child"}, {})["permissionDecision"] == "deny"
    assert bounds.pre_tool(dict(toolName="bash", toolArgs={}), {})["permissionDecision"] == "deny"
    assert bounds.pre_tool(dict(toolName="submit_generation", toolArgs={}), {}) is None


def test_wrapped_callbacks_preserve_additionalcontext_and_exact_one_send(tmp_path, monkeypatch):
    module = load()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    seen = []
    payload = {"generation_task_id": "task-original", "work_id": "work-original"}

    async def opened(native, project, session, callbacks):
        seen.append(callbacks)
        return "opened"

    async def sent(session, prompt, **kwargs):
        return "actual-boundary-substitute-offline-only"

    monkeypatch.setattr(module.Native, "open", opened)
    monkeypatch.setattr(module.CopilotSession, "send", sent)
    bounds = module.Bounds()
    restore = module.install_observers(bounds)

    async def run():
        try:
            result = await module.Native.open(
                None,
                "project",
                None,
                dict(
                    on_event=lambda _: None,
                    hooks={
                        "on_user_prompt_submitted": lambda data, context: {
                            "additionalContext": json.dumps(payload)
                        }
                    },
                ),
            )
            assert result == "opened"
            assert (
                json.loads(
                    seen[0]["hooks"]["on_user_prompt_submitted"]({"agentId": "child"}, {})[
                        "additionalContext"
                    ]
                )
                == payload
            )
            session = SimpleNamespace(session_id="offline-session", abort=lambda: None)
            assert (
                await module.CopilotSession.send(session, "once")
                == "actual-boundary-substitute-offline-only"
            )
            with pytest.raises(RuntimeError, match="Exactly one"):
                await module.CopilotSession.send(session, "twice")
        finally:
            for task in bounds.tasks:
                task.cancel()
            await asyncio.gather(*bounds.tasks, return_exceptions=True)
            restore()

    asyncio.run(run())
