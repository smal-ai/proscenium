"""One clean production Stop/Continue proof. Explicit two-send allocation, no child."""

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from m03_control import stop
from proscenium.api.composition import configure_services
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.copilot.launcher import identity
from proscenium.copilot.native import Native
from proscenium.copilot.service import wire
from proscenium.db.store import now
from proscenium.services import Services

REPO = Path(__file__).resolve().parents[3]
ROOT = Path("/home/quintin/.cache/proscenium-development/runtime-recovery-normal-0b8e9ba")
HELPER = """import json,os,time
from pathlib import Path
path=Path(__file__).resolve().parent/'.proscenium/tool-output/normal-helper.jsonl'
path.parent.mkdir(parents=True,exist_ok=True)
def record(event):
 with path.open('a') as out:
  out.write(json.dumps(dict(event=event,pid=os.getpid(),start=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19],at=time.time()))+'\\n');out.flush()
record('started')
end=time.monotonic()+25
while time.monotonic()<end:
 record('heartbeat');time.sleep(0.25)
record('natural-completion')
print('The bounded 25-second helper actually completed.')
"""


def append(path, body, durable=False):
    with path.open("a") as output:
        output.write(json.dumps(body) + "\n")
        if durable:
            output.flush()
            os.fsync(output.fileno())


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ObservedNative(Native):
    def __init__(self, services):
        super().__init__(services)
        self.deadline = None
        self.bash_calls = 0

    async def open(self, project_id, session_id, callbacks):
        callbacks = dict(callbacks)
        original_event = callbacks["on_event"]

        def event(value):
            original_event(value)
            raw = wire(value)
            if raw["type"] not in {
                "user.message",
                "session.idle",
                "session.error",
                "tool.execution_start",
                "tool.execution_complete",
                "permission.requested",
                "permission.completed",
                "assistant.turn_end",
            }:
                return  # Do not synchronously journal/fsync per-token observers.
            data = raw.get("data", {})
            observed = {
                k: raw[k] for k in ["id", "type", "parentId", "agentId", "timestamp"] if k in raw
            }
            observed["data"] = {
                k: data[k]
                for k in [
                    "toolCallId",
                    "toolName",
                    "requestId",
                    "result",
                    "success",
                    "clientTurnId",
                    "messageId",
                ]
                if k in data
            }
            append(ROOT / "native-events.jsonl", dict(at=now(), **observed))

        def guard(data, context):
            if (
                data["toolName"] == "bash"
                and data["toolArgs"].get("command", "").strip()
                == "python3 runtime_recovery_hold.py"
                and self.bash_calls < 2
            ):
                self.bash_calls += 1
                return {"permissionDecision": "allow"}
            if data["toolName"] in {"task_complete", "tool_search_tool_regex"}:
                return {"permissionDecision": "allow"}
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": "Only two executions of the allocated 25-second project-local helper are allowed. No delegation or other operations.",
            }

        callbacks["on_event"] = event
        callbacks["hooks"] = {**callbacks["hooks"], "on_pre_tool_use": guard}
        session = await super().open(project_id, session_id, callbacks)
        original_send = session.send

        async def send(prompt, **kwargs):
            path = ROOT / "sends.jsonl"
            rows = (
                [json.loads(line) for line in path.read_text().splitlines()]
                if path.exists()
                else []
            )
            assert len(rows) < 2 and kwargs["client_turn_id"] not in {
                r["delivery_id"] for r in rows
            }
            append(
                path,
                dict(
                    at=now(),
                    delivery_id=kwargs["client_turn_id"],
                    session_id=session.session_id,
                    mode=kwargs.get("mode"),
                    prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                ),
                durable=True,
            )
            self.deadline = time.monotonic() + 175
            result = await original_send(prompt, **kwargs)
            append(
                ROOT / "acks.jsonl",
                dict(at=now(), delivery_id=kwargs["client_turn_id"], native_message_id=result),
                durable=True,
            )
            return result

        session.send = send
        return session

    async def health(self):
        started = time.monotonic()
        try:
            await super().health()
        except Exception as exc:
            append(
                ROOT / "health-exceptions.private.jsonl",
                dict(
                    at=now(),
                    type=type(exc).__qualname__,
                    module=type(exc).__module__,
                    elapsed=time.monotonic() - started,
                    frames=[
                        dict(file=f.filename, line=f.lineno, function=f.name)
                        for f in traceback.extract_tb(exc.__traceback__)
                    ],
                ),
            )
            raise


def host():
    os.umask(0o077)
    origin = f"http://127.0.0.1:{port()}"
    settings = Settings(
        data_directory=ROOT / "machine",
        origin=origin,
        frontend_directory=REPO / "apps/frontend/dist",
        free_space_reserve_bytes=0,
        profiles=(
            {
                "profile_id": "authoring",
                "name": "Bounded normal Resume",
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
    owner = Caller("owner", "runtime-recovery-scripted-owner")
    project, _ = services.extension("projects").open(
        owner,
        dict(
            schema_version="1.0",
            operation_id="normal-open",
            path=str(ROOT / "project"),
            create=True,
            name="Normal Stop and Continue",
        ),
    )
    services.extension("projects").activate(
        owner,
        dict(
            schema_version="1.0",
            operation_id="normal-activate",
            project_id=project["project_id"],
            expected_revision=0,
        ),
    )
    (ROOT / "project/runtime_recovery_hold.py").write_text(HELPER)
    runtime = services.extension("copilot")
    runtime.native = ObservedNative(services)
    app = create_app(settings, services=services, manage_features=True)
    original_lifespan = app.router.lifespan_context

    async def observe():
        while True:
            with services.registry.project_store(project["project_id"]).transaction(
                write=False
            ) as tx:
                status = {
                    n: tx.all(n)
                    for n in [
                        "work",
                        "requests",
                        "invocations",
                        "decisions",
                        "copilot.stops",
                        "copilot.resumes",
                        "copilot.continuation_sends",
                    ]
                }
            temp = ROOT / "status.tmp"
            temp.write_text(json.dumps(status))
            temp.replace(ROOT / "status.json")
            if runtime.native.deadline and time.monotonic() > runtime.native.deadline:
                runtime.native.deadline = None
                if any(w["state"] in {"running", "awaiting_user"} for w in status["work"]):
                    append(ROOT / "deadline.jsonl", dict(at=now(), reason="175-second root bound"))
                    await runtime.failed(project["project_id"], "bounded_test_deadline")
            await asyncio.sleep(0.1)

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            task = asyncio.create_task(observe())
            try:
                yield
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app.router.lifespan_context = lifespan
    (ROOT / "host.private.json").write_text(
        json.dumps(
            dict(
                origin=origin,
                project_id=project["project_id"],
                pair_url=f"{origin}/#pair={services.auth.issue_pairing()}",
            )
        )
    )
    try:
        uvicorn.run(app, host="127.0.0.1", port=int(origin.rsplit(":", 1)[1]), log_level="warning")
    finally:
        services.close()


def start():
    ROOT.mkdir(mode=0o700)  # No replacement fixture or retry over an existing run.
    with (ROOT / "server.private.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__)), "host"],
            env={**os.environ, "PYTHONPATH": str(REPO)},
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (ROOT / "server.json").write_text(
        json.dumps(dict(pid=process.pid, start=identity(process.pid)))
    )
    deadline = time.monotonic() + 30
    while not (ROOT / "host.private.json").exists():
        assert process.poll() is None and time.monotonic() < deadline, (
            "Inspect failed bootstrap; do not replace the run"
        )
        time.sleep(0.1)
    cdp = port()
    with (ROOT / "browser.private.log").open("wb") as log:
        browser = subprocess.Popen(
            [
                str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080",
                "--force-device-scale-factor=1",
                f"--remote-debugging-port={cdp}",
                f"--user-data-dir={ROOT}/browser-profile",
                "about:blank",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (ROOT / "browser.private.json").write_text(
        json.dumps(dict(pid=browser.pid, start=identity(browser.pid), cdp=cdp))
    )
    print(json.dumps(dict(root=str(ROOT), server=process.pid, browser=browser.pid)))


def close():
    import sqlite3

    db = sqlite3.connect(f"file:{ROOT}/project/.proscenium/state.sqlite3?mode=ro", uri=True)
    result = {"records": {}, "events": []}
    for ns, body in db.execute("select namespace,body from records order by namespace,id"):
        result["records"].setdefault(ns, []).append(json.loads(body))
    result["events"] = [
        json.loads(r[0]) for r in db.execute("select body from events order by sequence")
    ]
    db.close()
    (ROOT / "export.private.json").write_text(json.dumps(result, indent=2))
    server = json.loads((ROOT / "server.json").read_text())
    browser = json.loads((ROOT / "browser.private.json").read_text())
    stop(server)
    stop(browser, group=True)
    native = json.loads((ROOT / "machine/copilot-runtime/process.json").read_text())
    records = dict(
        server=server, browser=browser, native={"pid": native["pid"], "start": native["start"]}
    )
    for row in records.values():
        row["same_identity_alive"] = identity(row["pid"]) == row["start"]
    helpers = []
    marker = ROOT / "project/.proscenium/tool-output/normal-helper.jsonl"
    if marker.exists():
        for line in marker.read_text().splitlines():
            row = json.loads(line)
            if row["event"] == "started":
                helpers.append({**row, "same_identity_alive": identity(row["pid"]) == row["start"]})
    records["helpers"] = helpers
    assert not any(
        row["same_identity_alive"]
        for row in [records["server"], records["browser"], records["native"], *helpers]
    )
    (ROOT / "cleanup.json").write_text(json.dumps(records, indent=2))
    print(json.dumps(records))


if __name__ == "__main__":
    {"start": start, "host": host, "close": close}[sys.argv[1]]()
