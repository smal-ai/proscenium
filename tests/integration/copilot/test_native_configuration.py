import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from proscenium.contracts import SCHEMA
from proscenium.copilot import native as native_module
from proscenium.copilot.native import Configuration, Native
from proscenium.copilot.tools import TOOLS, tool_schema
from test_delivery import PROJECT
from test_delivery import runtime as runtime


def test_wire_create_and_resume_both_memory_controls_and_owned_exec_launcher(runtime, monkeypatch):
    settings = dict(
        worker_model="configured-economical-worker",
        skill_directories=[str(Path("agent/skills").resolve())],
        mcp_servers={
            "blender": {
                "type": "stdio",
                "command": "configured-mcp-for-blender-2.0.0",
                "args": [],
                "tools": ["*"],
            }
        },
    )
    runtime.services.settings = replace(runtime.services.settings, runtime=settings)
    calls = []

    class Client:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))

        async def start(self):
            pass

        async def create_session(self, **kwargs):
            calls.append(("create", kwargs))
            return SimpleNamespace(session_id="native-session")

        async def resume_session(self, identity, **kwargs):
            calls.append(("resume", {**kwargs, "identity": identity}))
            return SimpleNamespace(session_id=identity)

        async def stop(self):
            pass

    monkeypatch.setattr(native_module, "CopilotClient", Client)

    async def run():
        native = Native(runtime.services)
        await native.open(PROJECT, None, {})
        await native.open(PROJECT, "native-session", {})
        await native.close()

    asyncio.run(run())
    launch = calls[0][1]
    assert launch["use_logged_in_user"] is True
    assert "github_token" not in launch
    assert "GITHUB_TOKEN" not in launch["env"]
    assert "launcher.py" in launch["connection"].args[0]
    assert (
        str(Path("node_modules/@github/copilot-linux-x64/copilot").resolve())
        in launch["connection"].args
    )
    for _, config in calls[1:]:
        assert config["memory"] == {"enabled": False}
        assert config["enable_session_store"] is False
        assert config["sandbox_config"].enabled is False
        assert config["infinite_sessions"]["enabled"]
        assert config["autopilot"]["max_continues"] >= 5
        assert config["large_output"]["output_directory"].endswith(".proscenium/tool-output")
        assert config["custom_agents"][0]["model_policy"] == "required"
        assert config["custom_agents"][0]["model"] == "configured-economical-worker"
        assert config["mcp_servers"] == settings["mcp_servers"]
        assert "excluded_tools" not in config  # Shell is not removed.
    assert calls[-1][1]["continue_pending_work"] is False


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"worker_model": "worker", "max_continues": 4},
        {"worker_model": "worker", "github_token": "never"},
        {"worker_model": "worker", "cli_path": "copilot"},
    ],
)
def test_invalid_or_unpinned_private_configuration_rejected(settings):
    with pytest.raises(ValueError):
        Configuration.load(settings)


def test_tool_schemas_are_normative_transitive_closures_not_full_catalogue():
    for _, _, name, _, _ in TOOLS.values():
        schema = tool_schema(name)
        assert {k: v for k, v in schema.items() if k != "$defs"} == SCHEMA["$defs"][name]
        assert len(schema.get("$defs", {})) < len(SCHEMA["$defs"])
        Draft202012Validator.check_schema(schema)
    example = json.loads(Path("docs/contracts/examples/checkpoint-request.json").read_text())
    validator = Draft202012Validator(tool_schema("CheckpointRequest"))
    assert validator.is_valid(example)
    assert not validator.is_valid({**example, "invented_field": "forbidden"})
    assert len(json.dumps(tool_schema("CheckpointRequest"))) < len(json.dumps(SCHEMA)) // 10
