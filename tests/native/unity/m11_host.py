"""Allocated real M11 host and explicit operator-fixture mailbox. No model/tool doubles."""

import asyncio
import hashlib
import importlib
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import uvicorn
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.copilot.native import Native
from proscenium.copilot.service import wire
from proscenium.services import Services

m11_operator = importlib.import_module("m11_operator")
ROOT = Path(sys.argv[1]).resolve()
REPO = Path(__file__).resolve().parents[3]
services = Services.create(Settings.load(ROOT / "config.json"))
app = create_app(services.settings, services=services, manage_features=True)
original_open = Native.open
task_ids: set[str] = set()
original_popen = subprocess.Popen


def append(name, value):
    with (ROOT / name).open("a") as output:
        output.write(json.dumps(value, default=str) + "\n")
        output.flush()
        os.fsync(output.fileno())


async def allocated_open(self, project_id, session_id, callbacks):
    callbacks = dict(callbacks)
    original_event = callbacks["on_event"]

    def event(value):
        value = wire(value)
        if not value.get("ephemeral"):
            append("native-events.private.jsonl", value)
        original_event(value)

    def guard(data, context):
        name, args = data["toolName"], data.get("toolArgs") or {}
        append("allocation-tools.jsonl", data)
        if name == "task":
            identity = data.get("toolCallId")
            if args.get("agent_type") != "unity-specialist" or not identity:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Only the allocated product unity-specialist is authorised.",
                }
            if identity not in task_ids:
                if len(task_ids) >= 2:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Two native children exhausted. Finish this root without another child.",
                    }
                task_ids.add(identity)
                append(
                    "child-allocation.jsonl",
                    dict(tool_call_id=identity, agent_type=args["agent_type"], at=time.time()),
                )
        if name.endswith("run_unity_workflow"):
            if args != m11_operator.IMPORT_BODY:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Only the declared single M11 import operation/job/source is allocated. Do not start another export or Editor.",
                }
        if name in {
            "submit_generation",
            "request_vlm_review",
            "request_preview",
            "assemble_sequence",
        }:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": "No generation, rendering, VLM or assembly allocation in M11.",
            }
        return {}  # Preserve production permission/admission policy.

    callbacks["on_event"] = event
    callbacks["hooks"] = {**callbacks["hooks"], "on_pre_tool_use": guard}
    return await original_open(self, project_id, session_id, callbacks)


class ObservedPopen(original_popen):
    def __init__(self, argv, *args, **kwargs):
        observed = (
            isinstance(argv, list)
            and len(argv) > 2
            and str(argv[1]) == str(REPO / "tools/unity/worker.py")
        )
        if observed:
            request = json.loads(Path(argv[2]).read_text())
            project_id = services.registry.active_id()
            assert project_id is not None
            job = services.jobs.get(project_id, request["job_id"])
            assert job["state"] == "starting" and job["attempt_id"] == request["attempt_id"]
            mem = {
                line.split(":")[0]: int(line.split()[1]) * 1024
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            }
            assert mem["MemAvailable"] >= 4 * 1024**3, "Keep 4 GiB host headroom before Editor"
            import shutil

            assert shutil.disk_usage(ROOT).free >= 5 * 1024**3
            claims = list(ROOT.glob("unity-*.claim"))
            assert len(claims) < 3, "Three Editor invocation allocation exhausted"
            claim = ROOT / f"unity-{len(claims) + 1}.claim"
            claim.write_text(
                json.dumps(
                    dict(
                        job_before_supervisor_spawn=job, request=request, at=time.time(), memory=mem
                    ),
                    indent=2,
                )
            )
        super().__init__(argv, *args, **kwargs)
        if observed:
            append(
                "unity-supervisor-observations.jsonl",
                dict(pid=self.pid, argv=argv, job_id=request["job_id"], at=time.time()),
            )


patch.object(Native, "open", allocated_open).start()
patch.object(subprocess, "Popen", ObservedPopen).start()
(ROOT / "pairing.url").write_text(
    f"{services.settings.origin}/#pair={services.auth.issue_pairing()}"
)
stop = threading.Event()
loop_holder = {}


original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def remember_loop(app):
    async with original_lifespan(app):
        loop_holder["loop"] = asyncio.get_running_loop()
        yield


app.router.lifespan_context = remember_loop


def mailbox():
    while not stop.wait(0.1):
        project_id = services.registry.active_id()
        if project_id:
            with services.registry.project_store(project_id).transaction(write=False) as tx:
                requests = tx.all("requests")
                works = {r["work_id"]: r for r in tx.all("work")}
            for path in ROOT.glob("cloud-*.claim"):
                budget = json.loads(path.read_text())
                if time.time() * 1000 - budget["at"] <= 300000:
                    continue
                request = next(
                    (
                        r
                        for r in requests
                        if hashlib.sha256(r["submission"].get("text", "").encode()).hexdigest()
                        == budget["prompt_sha256"]
                    ),
                    None,
                )
                marker = path.with_suffix(".deadline")
                if (
                    request
                    and works.get(request["receipt"].get("work_id"), {}).get("state")
                    in {"running", "awaiting_user"}
                    and request["receipt"]["state"]
                    not in {"completed", "failed", "stopped", "cancelled", "outcome_uncertain"}
                    and not marker.exists()
                ):
                    from proscenium.auth import Caller

                    runtime = services.extension("copilot")
                    value, _ = runtime.state.submit(
                        Caller("owner", "m11-scripted-allocation-deadline"),
                        dict(
                            schema_version="1.0",
                            request_id="m11-budget-stop-" + request["request_id"],
                            project_id=project_id,
                            type="stop",
                            target_work_id=request["receipt"]["work_id"],
                            reason="Allocated 300-second root deadline",
                        ),
                    )
                    marker.write_text(json.dumps(value))
                    runtime.notify()
        for path in sorted((ROOT / "commands").glob("*.json")):
            receipt = ROOT / "receipts" / path.name
            claim = path.with_suffix(".claimed")
            if receipt.exists() or claim.exists() or "loop" not in loop_holder:
                continue
            path.rename(claim)
            command = json.loads(claim.read_text())
            try:
                result = importlib.reload(m11_operator).apply(
                    services, ROOT, command, loop_holder["loop"]
                )
                receipt.write_text(
                    json.dumps(dict(status="completed", command=command, result=result), indent=2)
                )
            except Exception as error:
                receipt.write_text(
                    json.dumps(
                        dict(
                            status="failed",
                            command=command,
                            type=type(error).__name__,
                            code=getattr(error, "code", None),
                            message=str(error),
                        ),
                        indent=2,
                    )
                )


thread = threading.Thread(target=mailbox, name="m11-operator-fixture", daemon=True)
thread.start()
url = urlsplit(services.settings.origin)
assert url.hostname is not None and url.port is not None
try:
    uvicorn.run(app, host=url.hostname, port=url.port, workers=1)
finally:
    stop.set()
    thread.join()
    services.close()
