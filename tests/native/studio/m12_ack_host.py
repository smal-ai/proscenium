"""Fresh composed host, one real send, observational callback-aware pipe cut."""
# ruff: noqa: E402 -- standalone host must expose maintained tools before app composition imports

import asyncio
import json
import logging
import signal
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import uvicorn
from copilot import CopilotClient
from copilot.session import CopilotSession
from m12_ack_gate import CallbackPipeGate, accepted_ack_loss
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.copilot import install
from proscenium.services import Services

from tools.operations.recovery import write_json  # noqa: E402 -- maintained repository tool


def atomic(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except FileNotFoundError:
        return None


def main():
    root = Path(sys.argv[1])
    generation = sys.argv[2]
    signal.alarm(280)
    logging.getLogger("copilot").setLevel(logging.CRITICAL)
    plan = json.loads((root / "plan-private.json").read_text())
    services = Services.create(Settings.load(root / "config.json"))
    runtime = install(services)
    audit = {
        "generation": generation,
        "native_processes": [],
        "options": [],
        "forbidden": [],
        "session_ids": [],
    }
    gate = None
    deadlines = []
    real_send, real_create, real_resume = (
        CopilotSession.send,
        CopilotClient.create_session,
        CopilotClient.resume_session,
    )

    async def watch(request, client):
        await asyncio.sleep(174)
        project = services.registry.active_id()
        with services.registry.project_store(project).transaction(write=False) as tx:
            state = tx.require("requests", request)["receipt"]["state"]
        if (
            state not in {"completed", "failed", "stopped"}
            and client._process is not None
            and client._process.poll() is None
        ):
            process = client._process
            start = identity(process.pid)
            audit["deadline_fence"] = {"pid": process.pid, "start": start}
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 3)
            except Exception:
                if identity(process.pid) == start:
                    process.kill()
                await asyncio.to_thread(process.wait, 2)

    async def send(session, prompt, **kwargs):
        nonlocal gate
        assert kwargs.get("client_turn_id") == plan["request_id"] and prompt == plan["prompt"]
        # Exclusive reservation persists across process restarts. Never replay it.
        write_json(
            root / "send-reservation.json",
            {
                "request_id": plan["request_id"],
                "session_id": session.session_id,
                "before_send_ns": time.monotonic_ns(),
                "count": 1,
            },
        )
        assert generation == "1"
        gate = CallbackPipeGate(
            runtime.native.client._client, session.session_id, plan["request_id"], prompt
        )
        gate.install()
        deadlines.append(asyncio.create_task(watch(plan["request_id"], runtime.native.client)))
        try:
            result = await real_send(session, prompt, **kwargs)
            audit["application_send_ack_delivered"] = True
            return result
        finally:
            audit["send_await_finished_ns"] = time.monotonic_ns()

    def options(kwargs):
        assert kwargs["memory"] == {"enabled": False} and kwargs["enable_session_store"] is False
        kwargs.update(available_tools=[], custom_agents=[])
        audit["options"].append(
            {
                key: kwargs[key]
                for key in (
                    "working_directory",
                    "memory",
                    "enable_session_store",
                    "available_tools",
                    "custom_agents",
                )
            }
        )

    def native_process(client, session):
        process = client._process
        assert (
            Path(f"/proc/{process.pid}/exe").resolve()
            == Path(services.settings.runtime["cli_path"]).resolve()
        )
        audit["native_processes"].append(
            {"pid": process.pid, "start": identity(process.pid), "direct_verified_binary": True}
        )
        audit["session_ids"].append(session.session_id)
        atomic(root / "session-private.json", {"session_id": session.session_id})

    async def create(client, **kwargs):
        assert generation == "1"
        options(kwargs)
        session = await real_create(client, **kwargs)
        native_process(client, session)
        return session

    async def resume(client, sid, **kwargs):
        assert generation == "2"
        assert sid == json.loads((root / "send-reservation.json").read_text())["session_id"]
        assert kwargs["continue_pending_work"] is False
        options(kwargs)
        audit["continue_pending_work"] = False
        session = await real_resume(client, sid, **kwargs)
        native_process(client, session)
        return session

    def forbidden(*_args, **_kwargs):
        audit["forbidden"].append("provider_http")
        raise RuntimeError("No local provider work")

    async def inspect(_command):
        project = services.registry.active_id()
        result = {
            "sdk_constructed": runtime.native is not None,
            "gate_open": bool(project and project in services.gate._open),
            "send_reservations": int((root / "send-reservation.json").exists()),
        }
        if project:
            with services.registry.project_store(project).transaction(write=False) as tx:
                record = tx.get("requests", plan["request_id"], required=False)
                result.update(
                    request_receipt=record["receipt"] if record else None,
                    resumes=[
                        {
                            "operation_id": r["body"]["operation_id"],
                            "state": r["state"],
                            "detail_code": r.get("detail_code"),
                        }
                        for r in tx.all("copilot.resumes")
                    ],
                    jobs=len(tx.all("jobs")),
                    invocations=len(tx.all("invocations")),
                )
        return result

    async def cut(_command):
        end = time.monotonic() + 14
        native = None
        observed = None
        while time.monotonic() < end:
            if gate is not None and gate.reply_written.is_set():
                path = (
                    root / "d/copilot-runtime/home/session-state" / gate.session_id / "events.jsonl"
                )
                if path.exists():
                    with path.open() as stream:
                        for line in stream:
                            try:
                                value = json.loads(line)
                            except ValueError:
                                continue
                            if value.get("type") != "user.message":
                                continue
                            data = value.get("data", {})
                            if (
                                data.get("clientTurnId") == plan["request_id"]
                                and data.get("content") == plan["prompt"]
                            ):
                                native = {
                                    "native_user_message_id": data.get("messageId"),
                                    "native_client_turn_id": data["clientTurnId"],
                                    "native_event_id": value["id"],
                                    "native_timestamp": value.get("timestamp"),
                                }
                                observed = time.monotonic_ns()
                    if native:
                        break
            await asyncio.sleep(0.01)
        if gate is None:
            raise RuntimeError("No send entered SDK")
        task = runtime.sends.get(plan["request_id"])
        facts = {
            "native_observed_before_cut_ns": observed,
            "send_await_pending_before_cut": task is not None and not task.done(),
            **(native or {}),
        }
        facts.update(gate.cut())
        if task:
            await asyncio.wait_for(asyncio.shield(task), 40)
        facts["sdk_transport_closed"] = gate.rpc._transport_closed
        facts["application_send_ack_delivered"] = audit.get("application_send_ack_delivered", False)
        facts["accepted_ack_loss"] = accepted_ack_loss(facts)
        atomic(root / "cut-private.json", facts)
        return facts

    for folder in ("commands", "receipts"):
        (root / folder).mkdir(mode=0o700, exist_ok=True)

    async def mailbox():
        while True:
            for path in (root / "commands").glob("*.json"):
                path.rename(path.with_suffix(".claimed"))
                command = json.loads(path.with_suffix(".claimed").read_text())
                try:
                    result = await {"inspect": inspect, "cut": cut}[command["operation"]](command)
                    value = {"status": "completed", "result": result}
                except Exception as error:
                    value = {
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "code": getattr(error, "code", None),
                    }
                atomic(root / "receipts" / path.name, value)
            await asyncio.sleep(0.02)

    app = create_app(services.settings, services=services, manage_features=True)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifecycle(application):
        task = None
        try:
            async with original(application):
                if generation == "1":
                    write_json(
                        root / "pairing-private.json", {"code": services.auth.issue_pairing()}
                    )
                task = asyncio.create_task(mailbox())
                yield
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if gate:
                gate.release.set()
            for task in deadlines:
                task.cancel()
            await asyncio.gather(*deadlines, return_exceptions=True)
            audit["native_identities_gone"] = [
                identity(p["pid"]) != p["start"] for p in audit["native_processes"]
            ]
            atomic(root / f"host-{generation}-audit.json", audit)
            services.close()

    app.router.lifespan_context = lifecycle
    with (
        patch.object(CopilotSession, "send", send),
        patch.object(CopilotClient, "create_session", create),
        patch.object(CopilotClient, "resume_session", resume),
        patch("httpx.Client.send", forbidden),
        patch("httpx.AsyncClient.send", forbidden),
    ):
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=int(services.settings.origin.rsplit(":", 1)[1]),
            workers=1,
            access_log=False,
            log_level="critical",
        )


if __name__ == "__main__":
    main()
