"""Production host over snapshotted original M03 state, no new Queue fixtures."""

import asyncio
import fcntl
import hashlib
import json
import os
import sys
import time
from contextlib import asynccontextmanager

import uvicorn
from m03_resume_control import BASE, REPO, ROOT, port
from proscenium.api.composition import configure_services
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.copilot.launcher import identity
from proscenium.copilot.native import Native
from proscenium.copilot.service import wire
from proscenium.db.store import now
from proscenium.services import Services


def append(path, body):
    with path.open("a") as output:
        output.write(json.dumps(body) + "\n")
        output.flush()
        os.fsync(output.fileno())


def reserve(kind, identity_value, **details):
    with (ROOT / "allocation.jsonl").open("a+") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        output.seek(0)
        rows = [json.loads(line) for line in output if line.strip()]
        if any(row["kind"] == kind and row["identity"] == identity_value for row in rows):
            raise RuntimeError("Do not repeat an allocated send/child identity")
        assert sum(row["kind"] == kind for row in rows) < 1, (
            "Renewed one-send/one-child allocation exhausted"
        )
        output.write(
            json.dumps(dict(kind=kind, identity=identity_value, at=now(), **details)) + "\n"
        )
        output.flush()
        os.fsync(output.fileno())


class ObservedNative(Native):
    def __init__(self, services, run, label):
        super().__init__(services)
        self.run, self.label = run, label
        self.deadline = None

    async def open(self, project_id, session_id, callbacks):
        assert session_id, "Only the original native session may be resumed"
        callbacks = dict(callbacks)
        original_event = callbacks["on_event"]

        def event(value):
            raw = wire(value)
            original_event(value)
            allowed = {
                "messageId",
                "clientTurnId",
                "originatingMessageId",
                "toolCallId",
                "parentToolCallId",
                "toolName",
                "turnId",
                "requestId",
                "model",
                "modelSelectionSource",
                "success",
                "status",
                "result",
            }
            observed = {
                k: raw[k] for k in ("id", "type", "parentId", "agentId", "timestamp") if k in raw
            }
            observed["data"] = {k: v for k, v in raw.get("data", {}).items() if k in allowed}
            append(self.run / "native-events.jsonl", dict(observed_at=now(), **observed))

        callbacks["on_event"] = event

        def guard(data, context):
            name, args = data["toolName"], data["toolArgs"]
            if self.label != "a":
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Reconcile-only allocation. No tool execution.",
                }
            if name == "task":
                if args.get("agent_type") != "authoring-specialist":
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Only the original required-model specialist is allocated.",
                    }
                try:
                    reserve("child", data["toolCallId"], native_session_id=session_id)
                except (AssertionError, RuntimeError):
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "One native child budget exhausted.",
                    }
            elif name == "bash":
                if args.get("command", "").strip() not in {
                    "python3 m03_direct.py",
                    "python3 m03_hold.py",
                }:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Only the original bounded 70-second helper/direct command is allocated.",
                    }
            elif name not in {
                "get_version",
                "get_job",
                "get_artifact",
                "task_complete",
                "tool_search_tool_regex",
            }:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "No new authoring, owner question, service, provider or shell scope is allocated.",
                }
            return {"permissionDecision": "allow"}

        callbacks["hooks"] = {**callbacks["hooks"], "on_pre_tool_use": guard}
        session = await super().open(project_id, session_id, callbacks)
        send_original = session.send

        async def send(prompt, **kwargs):
            assert self.label == "a", "Project B has no send allocation"
            turn = kwargs.get("client_turn_id")
            with self.services.registry.project_store(project_id).transaction(write=False) as tx:
                continuation = tx.require("copilot.continuations", turn)
                assert continuation["work_id"] == "work-6361ef13fbeb4f4884ff382bf96d8815"
                assert continuation["request_id"] == "3e9e382d-b2da-4f28-9ca3-91c86cac5014"
            reserve(
                "send",
                turn,
                project_id=project_id,
                native_session_id=session_id,
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            )
            self.deadline = time.monotonic() + 175
            append(
                self.run / "sends.jsonl",
                dict(
                    at=now(),
                    operation_id=turn,
                    original_binding=continuation,
                    sdk_mode=kwargs.get("mode"),
                ),
            )
            result = await send_original(prompt, **kwargs)
            append(
                self.run / "send-acks.jsonl",
                dict(at=now(), operation_id=turn, native_message_id=result),
            )
            return result

        session.send = send
        return session


def main(label):
    assert label in "ab" and len(label) == 1
    os.umask(0o077)
    run = ROOT / label
    original = BASE / f"m03-runtime-proof-{label}"
    assert (ROOT / f"before-{label}.private.json").exists()
    fixture = json.loads((original / "fixture.json").read_text())
    origin = f"http://127.0.0.1:{port()}"
    settings = Settings(
        data_directory=original / "machine",
        origin=origin,
        frontend_directory=REPO / "apps/frontend/dist",
        free_space_reserve_bytes=0,
        execution_profiles=frozenset({"m03-test-encode"}),
        profiles=(
            {
                "profile_id": "authoring",
                "name": "M03 bounded native",
                "lead_model_id": "gpt-6.1-sol",
                "worker_model_ids": ["gpt-6-luna"],
                "scope_status": "project_scoped",
            },
        ),
        runtime=dict(
            cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            worker_model="gpt-6-luna",
            worker_tools=["bash"],
            skill_directories=[str(REPO / "agent/skills")],
            mcp_servers={},
        ),
    )
    services = Services.create(settings)
    configure_services(services)
    assert services.registry.active_id() == fixture["project_id"]
    runtime = services.extension("copilot")
    runtime.native = ObservedNative(services, run, label)
    original_replay = runtime.state.replay_event

    def replay(project_id, session_id, event):
        try:
            return original_replay(project_id, session_id, event)
        except Exception as exc:
            append(
                run / "replay-diagnostic.jsonl",
                dict(
                    at=now(),
                    native_session_id=session_id,
                    native_event_id=event.get("id"),
                    type=event.get("type"),
                    native_request_id=event.get("data", {}).get("requestId"),
                    code=getattr(exc, "code", type(exc).__name__),
                ),
            )
            raise

    runtime.state.replay_event = replay
    original_reconcile = runtime.reconcile

    async def reconcile(project_id):
        evidence = await original_reconcile(project_id)
        append(
            run / "reconciliation-evidence.jsonl", dict(at=now(), project_id=project_id, **evidence)
        )
        return evidence

    runtime.reconcile = reconcile
    app = create_app(settings, services=services, manage_features=True)
    original_lifespan = app.router.lifespan_context

    async def watchdog():
        while True:
            with services.registry.project_store(fixture["project_id"]).transaction(
                write=False
            ) as tx:
                status = {
                    namespace: tx.all(namespace)
                    for namespace in (
                        "work",
                        "requests",
                        "decisions",
                        "copilot.resumes",
                        "copilot.stops",
                        "copilot.control",
                        "copilot.continuation_sends",
                    )
                }
            temporary = run / "status.tmp"
            temporary.write_text(json.dumps(status))
            temporary.replace(run / "status.json")
            if runtime.native.deadline and time.monotonic() >= runtime.native.deadline:
                runtime.native.deadline = None
                target = next(
                    w
                    for w in status["work"]
                    if w["work_id"] == "work-6361ef13fbeb4f4884ff382bf96d8815"
                )
                if target["state"] not in {"completed", "failed", "stopped"}:
                    append(run / "watchdog.jsonl", dict(at=now(), reason="175-second root cap"))
                    await runtime.failed(fixture["project_id"], "m03_resume_deadline")
            await asyncio.sleep(0.25)

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            task = asyncio.create_task(watchdog())
            try:
                yield
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app.router.lifespan_context = lifespan
    info = dict(
        origin=origin,
        project_id=fixture["project_id"],
        original=str(original),
        pair_url=f"{origin}/#pair={services.auth.issue_pairing()}",
        server_pid=os.getpid(),
        server_start=identity(os.getpid()),
    )
    (run / "host.private.json").write_text(json.dumps(info, indent=2))
    print(json.dumps({k: info[k] for k in ("origin", "project_id", "server_pid")}), flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=int(origin.rsplit(":", 1)[1]), log_level="warning")
    finally:
        services.close()


if __name__ == "__main__":
    main(sys.argv[1])
