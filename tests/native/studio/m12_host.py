"""Bounded real product host, existing session only, with private operator mailbox.

No alternate application routes or SDK proxy. The optional loss seam gates the
pinned SDK's actual read loop and closes its real stdout file descriptor.
"""

import asyncio
import hashlib
import json
import logging
import os
import signal
import stat
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import uvicorn
from copilot import CopilotClient
from copilot.session import CopilotSession
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.copilot import install
from proscenium.services import Services

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.operations.recovery import write_json

SID = "d939999f-bff2-448c-9d24-028c1dcff527"
PID = "project-90687708-fe6d-465d-8e11-cd881eced35e"


def atomic(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def process_identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except FileNotFoundError:
        return None


def reserve_send(root, plan, request, session_id, prompt):
    ledger_path = root / "sends-private.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    if (
        request not in plan["request_ids"]
        or request in [entry["request_id"] for entry in ledger]
        or len(ledger) >= 2
    ):
        raise RuntimeError("Cloud send allocation exhausted or duplicate prevented")
    assert session_id == SID
    ledger.append(
        {
            "request_id": request,
            "session_id": session_id,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "before_real_send_ns": time.monotonic_ns(),
        }
    )
    atomic(ledger_path, ledger)
    return ledger


async def pause_actual_stdout(client):
    rpc = client._client
    original_read = rpc._read_message
    release, paused, armed, expired = (threading.Event() for _ in range(4))

    def gated_read():
        if armed.is_set():
            paused.set()
            if not release.wait(25):
                expired.set()  # No fabricated read error. An expired gate simply reads normally.
        return original_read()

    rpc._read_message = gated_read
    # The SDK's supported inline response seam arms only after THIS control ping
    # was read. No auth frames or application ACK contents are inspected.
    await rpc.request(
        "ping",
        {"message": "m12-read-boundary"},
        timeout=5,
        on_response_inline=lambda _result: armed.set(),
    )
    assert await asyncio.to_thread(paused.wait, 5)
    stdout = client._process.stdout
    descriptor = stdout.fileno()
    assert stat.S_ISFIFO(os.fstat(descriptor).st_mode)
    return dict(
        client=client,
        rpc=rpc,
        release=release,
        expired=expired,
        stdout=stdout,
        descriptor=descriptor,
        native_pid=client._process.pid,
        start=process_identity(client._process.pid),
    )


def close_actual_stdout(loss):
    pending = len(loss["rpc"].pending_requests)
    loss["stdout"].close()
    try:
        os.fstat(loss["descriptor"])
        closed = False
    except OSError:
        closed = True
    loss["release"].set()
    return {
        "actual_stdout_fd_closed": closed,
        "pending_rpc_count_before_release": pending,
        "reader_pause_expired": loss["expired"].is_set(),
    }


def main():
    root = Path(sys.argv[1])
    generation = sys.argv[2]
    signal.alarm(580)
    logging.getLogger("copilot").setLevel(logging.CRITICAL)
    plan = json.loads((root / "plan-private.json").read_text())
    audit = {
        "generation": generation,
        "resumes": [],
        "create_session_attempts": 0,
        "native_processes": [],
        "forbidden_effects": [],
    }
    services = Services.create(Settings.load(root / "config.json"))
    runtime = install(services)
    original_send = CopilotSession.send
    original_resume = CopilotClient.resume_session
    deadlines = []

    async def enforce_deadline(request, client):
        await asyncio.sleep(174)
        with services.registry.project_store(PID).transaction(write=False) as tx:
            state = tx.require("requests", request)["receipt"]["state"]
        if state in {"completed", "failed", "stopped"} or client is None:
            return
        process = client._process
        if process is not None and process.poll() is None:
            start = process_identity(process.pid)
            audit.setdefault("deadline_fences", []).append(
                {"request_id": request, "pid": process.pid, "start": start}
            )
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 3)
            except Exception:
                if process_identity(process.pid) == start:
                    process.kill()
                await asyncio.to_thread(process.wait, 2)

    async def send(session, prompt, **kwargs):
        request = kwargs.get("client_turn_id")
        reserve_send(root, plan, request, session.session_id, prompt)
        deadlines.append(asyncio.create_task(enforce_deadline(request, runtime.native.client)))
        return await original_send(session, prompt, **kwargs)

    async def resume(client, session_id, **kwargs):
        assert session_id == SID
        assert kwargs["continue_pending_work"] is False
        assert kwargs["working_directory"] == str(root / "restored/project")
        assert kwargs["memory"] == {"enabled": False} and kwargs["enable_session_store"] is False
        assert kwargs["mcp_servers"] == {}
        # Test allocation: retain real native read tools, but remove every delegate
        # and authoring/provider custom tool. No child is admitted in this proof.
        kwargs.update(available_tools=["view", "bash"], custom_agents=[])
        audit["resumes"].append(
            {
                key: kwargs[key]
                for key in (
                    "working_directory",
                    "memory",
                    "enable_session_store",
                    "continue_pending_work",
                    "available_tools",
                    "custom_agents",
                )
            }
        )
        session = await original_resume(client, session_id, **kwargs)
        process = client._process
        actual = Path(f"/proc/{process.pid}/exe").resolve()
        assert actual == Path(services.settings.runtime["cli_path"]).resolve()
        audit["native_processes"].append(
            {
                "pid": process.pid,
                "start": process_identity(process.pid),
                "direct_verified_executable": True,
            }
        )
        return session

    async def create(*_args, **_kwargs):
        audit["create_session_attempts"] += 1
        raise RuntimeError("Fresh session fallback forbidden")

    def forbidden(*_args, **_kwargs):
        audit["forbidden_effects"].append("provider_http")
        raise RuntimeError("No local provider work in M12")

    async def inspect(_command):
        with services.registry.project_store(PID).transaction(write=False) as tx:
            return {
                "native_constructed": runtime.native is not None,
                "sessions": len(runtime.sessions),
                "gate_open": PID in services.gate._open,
                "requests": [
                    {
                        key: value
                        for key, value in record.items()
                        if key in ("request_id", "submission", "receipt")
                    }
                    for record in tx.all("requests")
                ],
                "resumes": [
                    {"operation_id": r["body"]["operation_id"], "state": r["state"]}
                    for r in tx.all("copilot.resumes")
                ],
                "annotations": tx.all("annotations"),
                "contexts": tx.all("contexts"),
                "jobs": [{"job_id": j["job_id"], "state": j["state"]} for j in tx.all("jobs")],
                "sends": json.loads((root / "sends-private.json").read_text())
                if (root / "sends-private.json").exists()
                else [],
            }

    async def history(_command):
        session = runtime.sessions[PID]
        events = await session.get_events()
        users, outputs, tools = [], [], []
        for event in events:
            value = event.to_dict()
            data = value.get("data", {})
            if value["type"] == "user.message" and not data.get("isAutopilotContinuation"):
                users.append(
                    {
                        "event_id": value["id"],
                        "message_id": data.get("messageId"),
                        "client_turn_id": data.get("clientTurnId"),
                    }
                )
            elif value["type"] == "assistant.message":
                # Private receipt only. Exporter emits checks, never raw native content.
                outputs.append(
                    {
                        "event_id": value["id"],
                        "message_id": data.get("messageId"),
                        "content": data.get("content", ""),
                    }
                )
            elif value["type"] in ("tool.execution_start", "tool.execution_complete"):
                tools.append(
                    {
                        "event_id": value["id"],
                        "type": value["type"],
                        "tool_name": data.get("toolName"),
                    }
                )
        return {
            "session_id": session.session_id,
            "users": users,
            "public_assistant_outputs_private": outputs,
            "tools": tools,
            "audit": audit,
        }

    loss = {}

    async def arm_loss(_command):
        client = runtime.native.client
        loss.update(await pause_actual_stdout(client))
        return {"reader_paused": True, "real_stdout_is_pipe": True, "session_id": SID}

    async def cut_loss(command):
        target = command["request_id"]
        source = root / "restored/machine/copilot-runtime/home/session-state" / SID / "events.jsonl"
        observed = []
        until = time.monotonic() + 8
        while time.monotonic() < until:
            observed = []
            with source.open() as stream:
                for line in stream:
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    data = value.get("data", {})
                    if value.get("type") == "user.message" and data.get("clientTurnId") == target:
                        observed.append(
                            {
                                "event_id": value["id"],
                                "message_id": data.get("messageId"),
                                "client_turn_id": target,
                            }
                        )
            if observed:
                break
            await asyncio.sleep(0.05)
        send_task = runtime.sends.get(target)
        pending_before = send_task is not None and not send_task.done()
        physical = close_actual_stdout(loss)
        if send_task:
            await asyncio.wait_for(asyncio.shield(send_task), 45)
        result = {
            "session_id": SID,
            "native_users_observed_before_cut": observed,
            "send_pending_before_cut": pending_before,
            **physical,
            "native_pid_before_cut": loss["native_pid"],
            "native_start_before_cut": loss["start"],
            "sdk_transport_closed": loss["rpc"]._transport_closed,
        }
        atomic(root / "transport-cut-private.json", result)
        return result

    actions = {"inspect": inspect, "history": history, "arm_loss": arm_loss, "cut_loss": cut_loss}
    for folder in ("commands", "receipts"):
        (root / folder).mkdir(mode=0o700, exist_ok=True)

    async def mailbox():
        while True:
            for path in sorted((root / "commands").glob("*.json")):
                receipt = root / "receipts" / path.name
                if receipt.exists():
                    continue
                claimed = path.with_suffix(".claimed")
                path.rename(claimed)
                command = json.loads(claimed.read_text())
                try:
                    result = await actions[command["operation"]](command)
                    atomic(receipt, {"status": "completed", "result": result})
                except Exception as error:
                    atomic(
                        receipt,
                        {
                            "status": "failed",
                            "error_type": type(error).__name__,
                            "code": getattr(error, "code", None),
                        },
                    )
            await asyncio.sleep(0.05)

    app = create_app(services.settings, services=services, manage_features=True)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        task = None
        try:
            async with original_lifespan(application):
                if not (root / "pairing-private.json").exists():
                    write_json(
                        root / "pairing-private.json", {"code": services.auth.issue_pairing()}
                    )
                task = asyncio.create_task(mailbox())
                yield
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if loss:
                loss["release"].set()
            for deadline in deadlines:
                deadline.cancel()
            await asyncio.gather(*deadlines, return_exceptions=True)
            audit["owned_native_still_live"] = [
                p for p in audit["native_processes"] if process_identity(p["pid"]) == p["start"]
            ]
            atomic(root / f"host-{generation}-audit.json", audit)
            services.close()

    app.router.lifespan_context = lifespan
    with (
        patch.object(CopilotSession, "send", send),
        patch.object(CopilotClient, "resume_session", resume),
        patch.object(CopilotClient, "create_session", create),
        patch("httpx.Client.send", forbidden),
        patch("httpx.AsyncClient.send", forbidden),
    ):
        port = int(services.settings.origin.rsplit(":", 1)[1])
        uvicorn.run(
            app, host="127.0.0.1", port=port, workers=1, access_log=False, log_level="critical"
        )


if __name__ == "__main__":
    main()
