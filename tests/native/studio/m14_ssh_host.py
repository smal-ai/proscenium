"""One read-only SDK send, quiet unavailable restart, lifespan-persisted audit."""
# ruff: noqa: E402 -- standalone entry point exposes maintained repository tools

import asyncio
import hashlib
import json
import signal
import subprocess
import sys
import time
from contextlib import ExitStack
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import uvicorn
from copilot import CopilotClient
from copilot.session import CopilotSession
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.copilot import install
from proscenium.services import Services

from tools.operations.recovery import write_json
from tools.operations.ssh_proof import identity
from tools.operations.ssh_service import witness_lifespan


def reserve_send(root, value):
    # O_EXCL reservation across restart. Buffer flushing is irrelevant to its existence.
    with (root / "send-reservation.json").open("x") as output:
        json.dump(value, output)
    (root / "send-reservation.json").chmod(0o600)


class Audit:
    def __init__(self, app, records):
        self.app, self.records = app, records

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        request_hash, response_hash = hashlib.sha256(), hashlib.sha256()
        item = {
            "type": scope["type"],
            "path": scope["path"],
            "method": scope.get("method"),
            "request_bytes": 0,
            "response_bytes": 0,
        }
        # Observe every header, retain only non-secret transport headers.
        safe = {
            b"host",
            b"origin",
            b"range",
            b"content-type",
            b"content-length",
            b"content-range",
            b"accept-ranges",
            b"etag",
            b"cache-control",
        }
        item["request_headers"] = {k.decode(): v.decode() for k, v in scope["headers"] if k in safe}

        async def observed_receive():
            message = await receive()
            data = message.get("body", message.get("bytes") or message.get("text", "").encode())
            request_hash.update(data)
            item["request_bytes"] += len(data)
            return message

        async def observed_send(message):
            if message["type"] in {"http.response.start", "websocket.accept"}:
                item["status"] = message.get("status", 101)
                item["response_headers"] = {
                    k.decode(): v.decode() for k, v in message.get("headers", []) if k in safe
                }
            data = message.get("body", message.get("bytes") or message.get("text", "").encode())
            response_hash.update(data)
            item["response_bytes"] += len(data)
            await send(message)

        try:
            await self.app(scope, observed_receive, observed_send)
        finally:
            item.update(
                request_sha256=request_hash.hexdigest(), response_sha256=response_hash.hexdigest()
            )
            self.records.append(item)


def main():
    root, generation = Path(sys.argv[1]), sys.argv[2]
    signal.alarm(580)
    spec = json.loads((root / "allocation.json").read_text())
    settings = Settings.load(root / ("config.json" if generation == "1" else "unavailable.json"))
    services = Services.create(settings)
    runtime = install(services)
    audit = {
        "generation": generation,
        "sdk": version("github-copilot-sdk"),
        "sends": 0,
        "forbidden": [],
        "native_processes": [],
        "options": [],
        "transport": [],
    }
    real_create, real_send = CopilotClient.create_session, CopilotSession.send
    tasks = []

    def forbidden(kind):
        def fail(*args, **kwargs):
            audit["forbidden"].append(kind)
            raise RuntimeError("M14 allocation boundary: " + kind)

        return fail

    async def create(client, **kwargs):
        assert generation == "1" and not (root / "send-reservation.json").exists()
        assert kwargs["memory"] == {"enabled": False} and kwargs["enable_session_store"] is False
        kwargs.update(available_tools=["view"], custom_agents=[], tools=[])
        audit["options"].append(
            {
                k: kwargs[k]
                for k in (
                    "memory",
                    "enable_session_store",
                    "available_tools",
                    "custom_agents",
                    "working_directory",
                    "model",
                )
            }
        )
        session = await real_create(client, **kwargs)
        process = client._process
        audit["native_processes"].append(
            {
                "pid": process.pid,
                "start": identity(process.pid),
                "binary_verified": Path(f"/proc/{process.pid}/exe").resolve()
                == Path(settings.runtime["cli_path"]).resolve(),
                "session_id": session.session_id,
            }
        )
        return session

    async def deadline():
        await asyncio.sleep(179)
        if not (root / "completed.json").exists():
            write_json(root / "send-deadline.json", {"expired": True})
            await runtime.native.close()

    async def send(session, prompt, **kwargs):
        assert generation == "1" and prompt == spec["prompt"]
        reserve_send(
            root,
            {
                "session_id": session.session_id,
                "request_id": kwargs["client_turn_id"],
                "count": 1,
                "before_send_ns": time.monotonic_ns(),
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            },
        )
        audit["sends"] += 1
        tasks.append(asyncio.create_task(deadline()))
        result = await real_send(session, prompt, **kwargs)
        write_json(
            root / "admitted.json",
            {
                "request_id": kwargs["client_turn_id"],
                "session_id": session.session_id,
                "native_message_id": result,
                "ack_observed_ns": time.monotonic_ns(),
            },
        )
        return result

    original_popen = subprocess.Popen

    def scoped_process(command, *args, **kwargs):
        allowed = isinstance(command, (list, tuple)) and (
            Path(command[0]).name == "git"
            or (
                generation == "1"
                and len(command) > 1
                and Path(command[0]).resolve() == Path(sys.executable).resolve()
                and Path(command[1]).resolve()
                == REPO / "apps/backend/src/proscenium/copilot/launcher.py"
            )
        )
        if not allowed:
            return forbidden("non_git_or_exact_native_process")()
        return original_popen(command, *args, **kwargs)

    async def observe():
        prior = None
        while True:
            with services.registry.project_store(spec["project_id"]).transaction(write=False) as tx:
                rows = tx.all("requests")
                if rows:
                    assert len(rows) == 1
                    request = rows[0]
                    state = request["receipt"]["state"]
                    if prior != state:
                        with (root / f"host-{generation}-journal.jsonl").open("a") as output:
                            output.write(
                                json.dumps(
                                    {"ns": time.monotonic_ns(), "receipt": request["receipt"]}
                                )
                                + "\n"
                            )
                        prior = state
                    if generation == "1" and state in {
                        "completed",
                        "failed",
                        "stopped",
                        "outcome_uncertain",
                    }:
                        if not (root / "completed.json").exists():
                            write_json(
                                root / "completed.json",
                                {
                                    "observed_ns": time.monotonic_ns(),
                                    "request": request,
                                    "contexts": tx.all("contexts"),
                                    "events": [
                                        json.loads(row[0])
                                        for row in tx.connection.execute(
                                            "SELECT body FROM events ORDER BY sequence"
                                        )
                                    ],
                                    "work": tx.all("work"),
                                    "sessions": tx.all("copilot.session"),
                                },
                            )
            await asyncio.sleep(0.05)

    app = create_app(settings, services=services, manage_features=True)
    app.add_middleware(Audit, records=audit["transport"])
    original_lifespan = app.router.lifespan_context
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def monitored(application):
        async with original_lifespan(application):
            write_json(
                root / f"ready-{generation}.json",
                {"pid": __import__("os").getpid(), "start": identity(__import__("os").getpid())},
            )
            if generation == "1":
                write_json(root / "pairing-private.json", {"code": services.auth.issue_pairing()})
            monitor = asyncio.create_task(observe())
            try:
                yield
            finally:
                monitor.cancel()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(monitor, *tasks, return_exceptions=True)

    app.router.lifespan_context = monitored
    written = False

    def persist():
        nonlocal written
        if written:
            return
        with services.registry.project_store(spec["project_id"]).transaction(write=False) as tx:
            audit["records"] = {
                name: tx.all(name)
                for name in (
                    "requests",
                    "contexts",
                    "copilot.session",
                    "work",
                    "annotations",
                    "invocations",
                )
            }
            audit["jobs"] = [{"job_id": j["job_id"], "state": j["state"]} for j in tx.all("jobs")]
        audit["gate_closed"] = spec["project_id"] not in services.gate._open
        audit["native_identities_gone"] = [
            identity(p["pid"]) != p["start"] for p in audit["native_processes"]
        ]
        services.close()
        write_json(root / f"host-{generation}-audit.json", audit)
        written = True

    witness_lifespan(app, persist)
    with ExitStack() as stack:
        for target, replacement in (
            ("httpx.Client.send", forbidden("provider_http")),
            ("httpx.AsyncClient.send", forbidden("provider_http")),
            ("subprocess.Popen", scoped_process),
        ):
            stack.enter_context(patch(target, replacement))
        stack.enter_context(patch.object(CopilotClient, "create_session", create))
        stack.enter_context(
            patch.object(CopilotClient, "resume_session", forbidden("native_resume"))
        )
        stack.enter_context(patch.object(CopilotSession, "send", send))
        if generation != "1":
            stack.enter_context(
                patch(
                    "proscenium.copilot.native.CopilotClient", forbidden("sdk_factory_on_restart")
                )
            )
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=spec["backend_port"],
            workers=1,
            access_log=False,
            log_level="critical",
        )


if __name__ == "__main__":
    main()
