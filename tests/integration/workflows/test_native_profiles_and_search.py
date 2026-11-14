import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from copilot.tools import ToolInvocation
from jsonschema import Draft202012Validator
from proscenium.contracts import validate
from proscenium.copilot import native as native_module
from proscenium.copilot.native import UNITY_TOOLS, Configuration, unity_agent
from proscenium.copilot.tools import read_parameters
from proscenium.copilot.workflows import WORKFLOW_TOOLS, parameters


def test_required_unity_model_scope_and_native_envelopes():
    config = Configuration.load(
        {
            "worker_model": "configured-worker",
            "unity_worker_model": "configured-unity",
            "worker_tools": ["get_job"],
        }
    )
    profile = unity_agent(config)
    assert profile["model"] == "configured-unity" and profile["model_policy"] == "required"
    assert profile["name"] == "unity-specialist" and profile["tools"] == UNITY_TOOLS
    assert "run_unity_workflow" in profile["prompt"] and "# Unity specialist" in profile["prompt"]
    assert config.worker_tools == ["get_job"]  # Never widen an explicit authoring scope.
    assert (
        unity_agent(Configuration.load({"worker_model": "required-worker"}))["model"]
        == "required-worker"
    )
    assert {"list_items", "assemble_sequence", "run_unity_workflow", "get_unity_result"} <= set(
        Configuration.load({"worker_model": "required-worker"}).worker_tools
    )
    for bad in (
        {"worker_model": "worker", "unity_worker_model": ""},
        {"worker_model": "worker", "unity_editor": "Unity"},
        {},
    ):
        with pytest.raises(ValueError):
            Configuration.load(bad)
    for name in WORKFLOW_TOOLS:
        Draft202012Validator.check_schema(parameters(name))
    Draft202012Validator.check_schema(read_parameters("list_items"))


def test_list_items_provenance_search_pagination_read_only_and_scope(environment):
    env = environment
    with env.services.registry.project_store("project-test").transaction() as tx:
        for number, name, tags, creator in [
            (1, "Stone arch", ["Ancient"], "Fixture Studio"),
            (2, "Metal railing", ["Industrial"], "Other"),
            (3, "Stone wall", ["Grey"], "Fixture Studio"),
        ]:
            item = dict(
                schema_version="1.0",
                project_id="project-test",
                item_id=f"asset-{number}",
                kind="asset",
                name=name,
                current_version_id=None,
                selected_version_id=None,
                description="Recorded reusable geometry",
                tags=tags,
                provenance=dict(
                    source_url="https://example.invalid/fixture",
                    creator=creator,
                    licence="CC0",
                    note="Known fixture metadata",
                ),
                state_revision=0,
            )
            validate("ItemRecord", item)
            tx.put("items", item["item_id"], item, schema="ItemRecord")

    def snapshot():
        with env.services.registry.project_store("project-test").transaction(write=False) as tx:
            return {
                name: [
                    tuple(row)
                    for row in tx.connection.execute(f"SELECT * FROM {name} ORDER BY 1,2")
                ]
                for name in ("records", "operations", "events", "outbox")
            }

    async def call(body):
        result = await env.tools.invoke("list_items", env.invocation("list_items", body))
        return json.loads(result.text_result_for_llm)

    async def run():
        before = snapshot()
        names = {tool.name for tool in env.tools.definitions()}
        assert {
            "list_items",
            "run_unity_workflow",
            "assemble_sequence",
            "get_unity_result",
        } <= names
        first = await call({"query": "sToNe", "limit": 1})
        validate("ItemPage", first)
        assert first["items"][0]["item_id"] == "asset-1" and first["next_cursor"]
        last = await call({"query": "STONE", "limit": 1, "cursor": first["next_cursor"]})
        assert last["items"][0]["item_id"] == "asset-3"
        invalid = await call({"query": "metal", "cursor": first["next_cursor"]})
        assert invalid["error"] == "invalid_cursor"
        assert [row["item_id"] for row in (await call({"query": "fixture STUDIO"}))["items"]] == [
            "asset-1",
            "asset-3",
        ]
        assert [row["item_id"] for row in (await call({"query": "ANCIENT"}))["items"]] == [
            "asset-1"
        ]
        assert len((await call({"query": "reusable"}))["items"]) == 3
        env.services.gate.close("project-test")
        assert len((await call({}))["items"]) == 3
        assert (await call({"project_id": "another-project"}))["error"] == "invalid_request"
        bad = ToolInvocation(
            session_id="session-workflows",
            tool_call_id="workflow-call",
            agent_id="not-the-original-agent",
            tool_name="list_items",
            arguments={},
        )
        denied = await env.tools.invoke("list_items", bad)
        assert json.loads(denied.text_result_for_llm)["error"] == "attribution_unavailable"
        assert snapshot() == before
        await env.workflows.close()

    asyncio.run(run())


def test_sdk_create_and_resume_keep_security_and_register_both_profiles(
    environment, monkeypatch, tmp_path
):
    env = environment
    binary = tmp_path / "copilot"
    binary.write_bytes(b"OFFLINE SDK transport metadata fixture, never executed")
    monkeypatch.setattr(
        native_module, "BINARY_SHA256", hashlib.sha256(binary.read_bytes()).hexdigest()
    )
    env.services.settings = replace(
        env.services.settings,
        runtime=dict(
            worker_model="authoring-model",
            unity_worker_model="unity-model",
            cli_path=str(binary),
            worker_tools=["view", "bash", "get_job"],
        ),
    )
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            pass

        async def create_session(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(session_id="offline-session")

        async def resume_session(self, session_id, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(session_id=session_id)

        async def stop(self):
            pass

    monkeypatch.setattr(native_module, "CopilotClient", Client)

    async def run():
        native = native_module.Native(env.services)
        await native.open("project-test", None, {})
        await native.open("project-test", "offline-session", {})
        await native.close()
        await env.workflows.close()

    asyncio.run(run())
    assert len(calls) == 2
    for config in calls:
        assert config["memory"] == {"enabled": False} and config["enable_session_store"] is False
        assert (
            config["autopilot"]["continuation_enabled"]
            and config["autopilot"]["max_continues"] >= 5
        )
        assert config["sandbox_config"].enabled is False and "excluded_tools" not in config
        authoring, unity = config["custom_agents"]
        assert (
            authoring["tools"] == ["view", "bash", "get_job"]
            and authoring["model"] == "authoring-model"
        )
        assert unity["name"] == "unity-specialist" and unity["model"] == "unity-model"
        assert authoring["model_policy"] == unity["model_policy"] == "required"
        assert "run_unity_workflow" in unity["tools"] and "# Unity specialist" in unity["prompt"]
