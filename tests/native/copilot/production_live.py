"""Opt-in bounded production integration. No Blender/Unity/GPU/service changes.

W01_NATIVE_ROOT must be a new /tmp/opencode/proscenium-w01-* directory.
Budget reservations persist before every root send. Never rerun an existing label.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from copilot.tools import Tool, ToolResult
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.copilot import install
from proscenium.copilot.native import Native
from proscenium.copilot.service import Copilot
from proscenium.copilot.tools import Tools
from proscenium.db.store import now
from proscenium.services import Services

pytestmark = pytest.mark.native


class RecordingNative(Native):
    def __init__(self, services):
        super().__init__(services)
        self.runtime = None

    async def open(self, project_id, session_id, callbacks):
        callbacks = dict(callbacks)
        tools = Tools(self.services, self.runtime.state, project_id)

        def record(invocation):
            caller = tools.caller(invocation)
            with self.services.registry.project_store(project_id).transaction() as tx:
                native = tx.require("invocations", caller.invocation_id)
                tx.put(
                    "copilot.proof",
                    invocation.tool_call_id,
                    {
                        "request_id": native["request_id"],
                        "work_id": caller.work_id,
                        "tool_call_id": invocation.tool_call_id,
                        "agent_id": invocation.agent_id,
                        "context_id": invocation.arguments["context_id"],
                        "committed_before_reply": True,
                    },
                )
            return ToolResult(
                text_result_for_llm="Recorded after durable SQLite attribution. Continue only the explicitly requested integration checks."
            )

        callbacks["tools"] = [
            *callbacks["tools"],
            Tool(
                name="record_w01",
                description="Read-only W01 context recording",
                parameters={
                    "type": "object",
                    "properties": {"context_id": {"type": "string"}},
                    "required": ["context_id"],
                    "additionalProperties": False,
                },
                handler=record,
            ),
        ]
        return await super().open(project_id, session_id, callbacks)


def configured(root):
    settings = Settings(
        data_directory=root / "machine",
        free_space_reserve_bytes=0,
        runtime=dict(
            worker_model="gpt-6.1-sol",
            worker_tools=["record_w01"],
            cli_path=str(Path("node_modules/@github/copilot-linux-x64/copilot").resolve()),
            skill_directories=[str(Path("agent/skills").resolve())],
        ),
    )
    services = Services.create(settings)
    project = root / "project"
    project.mkdir(exist_ok=True)
    if services.registry.active_id() is None:
        services.registry.register(
            dict(
                schema_version="1.0",
                project_id="w01-proof",
                repository_id="w01-proof-repo",
                name="Disposable W01 native proof",
                path=str(project),
                native_session_id=None,
                state="inactive",
                state_revision=0,
            )
        )
        services.registry.activate("w01-proof")
        services.acquire_project("w01-proof")
    return services


async def wait_record(runtime, identity, terminal=True):
    for _ in range(1100):
        with runtime.services.registry.project_store("w01-proof").transaction(write=False) as tx:
            record = tx.require("requests", identity)
        if record["receipt"]["state"] in (
            {"completed", "failed", "stopped", "outcome_uncertain"}
            if terminal
            else {"acknowledged", "running"}
        ):
            return record
        await asyncio.sleep(0.1)
    raise TimeoutError("Bounded native request did not settle")


def reserve(root, label):
    import fcntl

    with (root / "allocation.jsonl").open("a+") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        output.seek(0)
        rows = [json.loads(line) for line in output if line.strip()]
        assert len(rows) < 6 and all(row["label"] != label for row in rows), (
            "Never retry a reserved native send"
        )
        output.write(json.dumps({"label": label, "reserved_at": now()}) + "\n")
        output.flush()
        os.fsync(output.fileno())


def test_production_sqlite_shell_child_stop_and_cold_read():
    value = os.environ.get("W01_NATIVE_ROOT")
    if not value:
        pytest.skip("Requires explicit bounded W01 native allocation")
    root = Path(value).resolve()
    assert str(root).startswith("/tmp/opencode/proscenium-w01-")
    root.mkdir(exist_ok=True)

    async def run():
        services = configured(root)
        native = RecordingNative(services)
        runtime = install(services, native=native)
        native.runtime = runtime
        owner = Caller("owner", "w01-proof-owner")
        evidence = {
            "evidence_kind": "live production SDK/SQLite integration",
            "recorded_at": now(),
            "sdk": "1.0.34",
            "cli": "1.0.112",
            "model": "gpt-6.1-sol",
            "root_sends": 0,
        }
        try:
            await runtime.start()
            context = dict(
                schema_version="1.0",
                context_id="w01-native-context",
                project_id="w01-proof",
                captured_at=now(),
                item_id=None,
                version_id=None,
                view_mode="none",
                target=None,
                camera=None,
                range=None,
                selected_objects=[],
                attachments=[],
                annotations=[],
                approved_findings=[],
            )
            runtime.contexts.capture(context)
            for label, prompt in [
                (
                    "w01-native-root-child",
                    "Small integration check. Call record_w01 with the exact context_id from the host extension context. Then delegate exactly once to authoring-specialist. Tell it to call record_w01 with the context_id it receives from trusted additionalContext, without giving it the value yourself. No file reads, no other tools. Finish briefly.",
                ),
                (
                    "w01-native-shell",
                    "Small integration check. Run only pwd with bash, then reply with its output and finish. No file reads or other tools.",
                ),
            ]:
                reserve(root, label)
                evidence["root_sends"] += 1
                runtime.state.submit(
                    owner,
                    dict(
                        schema_version="1.0",
                        request_id=label,
                        project_id="w01-proof",
                        type="queue",
                        text=prompt,
                        context_id=context["context_id"],
                    ),
                )
                runtime.notify()
                result = await asyncio.wait_for(wait_record(runtime, label), 119)
                evidence[label] = result["receipt"]
                (root / "evidence.json").write_text(json.dumps(evidence, indent=2))
                assert result["receipt"]["state"] == "completed", result
            with services.registry.project_store("w01-proof").transaction(write=False) as tx:
                evidence["recordings"] = tx.all("copilot.proof")
                evidence["invocations"] = tx.all("invocations")
                shell = tx.require("requests", "w01-native-shell")
                evidence["native_event_counts"] = {}
                for event in tx.all("copilot.native"):
                    name = event["type"]
                    evidence["native_event_counts"][name] = (
                        evidence["native_event_counts"].get(name, 0) + 1
                    )
            assert len(evidence["recordings"]) == 2
            assert any(row["agent_id"] for row in evidence["recordings"])
            assert all(row["context_id"] == context["context_id"] for row in evidence["recordings"])
            runtime.state.submit(
                owner,
                dict(
                    schema_version="1.0",
                    request_id="w01-native-stop",
                    project_id="w01-proof",
                    type="stop",
                    target_work_id=shell["receipt"]["work_id"],
                ),
            )
            runtime.notify()
            evidence["stop"] = (
                await asyncio.wait_for(wait_record(runtime, "w01-native-stop"), 119)
            )["receipt"]
            (root / "evidence.json").write_text(json.dumps(evidence, indent=2))
            assert evidence["stop"]["state"] == "completed"
            await runtime.close()
            other_native = RecordingNative(services)
            other = Copilot(services, native=other_native)
            other_native.runtime = other
            try:
                await other.start()
                assert other.sessions == {}
                read = await other.reconcile("w01-proof")
                evidence["cold_reconcile"] = read
                assert read["pending_empty"]
                assert not read["uncertain_request_ids"]
            finally:
                await other.close()
            evidence["owned_runtime_closed"] = True
        finally:
            await runtime.close()
            services.close()
            (root / "evidence.json").write_text(json.dumps(evidence, indent=2))

    asyncio.run(run())
