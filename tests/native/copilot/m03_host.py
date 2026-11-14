"""Real composed M03 host with bounded observational allocation instrumentation.

No synthetic successful callbacks, alternate planner or provider/model-service startup.
Fixture helpers and the independent FFmpeg worker operate only in this test root.
"""

import asyncio
import fcntl
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from PIL import Image, ImageDraw
from proscenium.api.composition import configure_services
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.copilot.launcher import identity
from proscenium.copilot.native import Native
from proscenium.copilot.service import wire
from proscenium.db.store import now
from proscenium.services import Services
from proscenium.sources import closure_digest
from proscenium.sources.common import git

REPO = Path(__file__).resolve().parents[3]
OWNER = Caller("owner", "m03-scripted-test-owner")
ALLOCATION = Path(
    "/home/quintin/.cache/proscenium-development/m03-runtime-allocation-4e8b233.jsonl"
)


def append(path, body):
    with path.open("a") as output:
        output.write(json.dumps(body) + "\n")
        output.flush()
        os.fsync(output.fileno())


def reserve(root, kind, value, **details):
    with ALLOCATION.open("a+") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        output.seek(0)
        rows = [json.loads(line) for line in output if line.strip()]
        if any(r["kind"] == kind and r["identity"] == value for r in rows):
            if kind == "child":
                return
            raise RuntimeError("An actual send identity is already reserved; never resubmit")
        if sum(r["kind"] == kind for r in rows) >= {"send": 6, "child": 3}[kind]:
            raise RuntimeError("M03 execution allocation exhausted")
        body = dict(at=now(), root=str(root), kind=kind, identity=value, **details)
        output.write(json.dumps(body) + "\n")
        output.flush()
        os.fsync(output.fileno())


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


HOLD = """import json,os,time
from pathlib import Path
root=Path(__file__).resolve().parent
directory=root/'.proscenium/tool-output/m03'
directory.mkdir(parents=True,exist_ok=True)
marker=directory/'child.jsonl'
with marker.open('a') as out:
 out.write(json.dumps({'event':'started','pid':os.getpid(),'ppid':os.getppid(),'at':time.time()})+'\\n');out.flush()
 deadline=time.monotonic()+70
 while time.monotonic()<deadline:
  out.write(json.dumps({'event':'heartbeat','pid':os.getpid(),'at':time.time()})+'\\n');out.flush()
  time.sleep(0.5)
 out.write(json.dumps({'event':'natural-completion','pid':os.getpid(),'at':time.time()})+'\\n');out.flush()
print('Bounded helper naturally completed; no other files or services changed.')
"""
DIRECT = """from pathlib import Path
root=Path(__file__).resolve().parent
directory=root/'.proscenium/tool-output/m03'
directory.mkdir(parents=True,exist_ok=True)
(directory/'direct.txt').write_text('M03 direct lead task completed\\n')
print('M03 direct lead task completed')
"""
TRANSPORT = HOLD.replace("'child.jsonl'", "'transport.jsonl'").replace("+70", "+35")


def prepare(services, root):
    projects = services.extension("projects")
    project, _ = projects.open(
        OWNER,
        dict(
            schema_version="1.0",
            operation_id="m03-open",
            path=str(root / "project"),
            create=True,
            name="M03 real discussion fixture",
        ),
    )
    pid = project["project_id"]
    projects.activate(
        OWNER,
        dict(
            schema_version="1.0", operation_id="m03-activate", project_id=pid, expected_revision=0
        ),
    )
    workspace = Path(project["path"])
    git(workspace, "config", "user.name", "M03 isolated fixture")
    git(workspace, "config", "user.email", "m03@example.invalid")
    services.gate.resume(
        pid, reconciliation_evidence="Authorised host fixture preparation, no native/model effects"
    )
    store = services.registry.project_store(pid)
    with store.transaction() as tx:
        tx.put(
            "work",
            "m03-fixture-work",
            dict(
                schema_version="1.0",
                work_id="m03-fixture-work",
                project_id=pid,
                originating_request_id="m03-fixture-command",
                directive_request_ids=[],
                state="running",
                state_revision=0,
                native_session_id="fixture-not-native",
                job_ids=[],
                generation_task_id="m03-fixture-task",
            ),
            schema="WorkRecord",
        )
        tx.put(
            "invocations",
            "m03-fixture-invocation",
            dict(work_id="m03-fixture-work", owner_epoch=services.ownership.epoch, state="running"),
        )
    caller = Caller(
        "tool",
        "m03-fixture",
        pid,
        "m03-fixture-work",
        services.ownership.epoch,
        invocation_id="m03-fixture-invocation",
        operations=("checkpoint_sources", "publishOutputs", "admitToolJob"),
    )
    items = []
    for label, colour, kind in (("A", "#e77932", "scene"), ("B", "#3985dc", "asset")):
        relative = f"assets/m03-{label}.txt"
        data = f"M03 labelled synthetic discussion source {label}. Not a Blender scene.\n".encode()
        (workspace / relative).write_bytes(data)
        head = git(workspace, "rev-parse", "HEAD", required=False).decode().strip() or None
        checkpoint = services.extension("sources").checkpoint(
            caller,
            dict(
                schema_version="1.0",
                operation_id=f"m03-checkpoint-{label}",
                project_id=pid,
                paths=[relative],
                message=f"Pin M03 discussion target {label}",
                expected_head=head,
            ),
        )
        image = Image.new("RGB", (320, 180), colour)
        ImageDraw.Draw(image).text((20, 70), f"M03 FIXTURE {label}", fill="white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        artifacts = services.extension("artifacts")
        artifact = artifacts.promote(
            pid,
            f"m03-image-{label}",
            output.getvalue(),
            kind="image",
            purpose="concept",
            media_type="image/png",
        )
        with store.transaction() as tx:
            artifacts.put(tx, artifact)
        source = dict(
            entry_path=relative,
            files=[
                dict(
                    destination=relative,
                    origin=dict(
                        kind="git",
                        repository_id=project["repository_id"],
                        commit=checkpoint["source_commit"],
                        path=relative,
                        sha256=hashlib.sha256(data).hexdigest(),
                    ),
                )
            ],
            dependencies=[],
            required_runtime=dict(application="fixture", version="1", features=[]),
            digest="0" * 64,
        )
        source["digest"] = closure_digest(source)
        item = dict(
            item_id=f"m03-item-{label}",
            version_id=f"m03-version-{label}",
            kind=kind,
            name=f"M03 labelled {kind} {label}",
            expected_current_version_id=None,
            source_commit=checkpoint["source_commit"],
            source=source,
            artifact_ids=[artifact["artifact_id"]],
            preview_requests=[],
        )
        services.extension("catalogue").publish(
            caller,
            dict(
                schema_version="1.0",
                publication_id=f"m03-publication-{label}",
                project_id=pid,
                items=[item],
            ),
        )
        items.append(item)
    (workspace / "m03_hold.py").write_text(HOLD)
    (workspace / "m03_direct.py").write_text(DIRECT)
    (workspace / "m03_transport.py").write_text(TRANSPORT)
    # Scope guard fixtures are test-only, nonsensitive bytes. No credential is read.
    (workspace / ".env").write_text("M03_NOT_A_CREDENTIAL=fixture\n")
    (root / "outside.png").write_bytes(output.getvalue())
    staging = workspace / ".proscenium/tool-output/staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "escape.png").symlink_to(root / "outside.png")
    (staging / "environment.png").symlink_to(workspace / ".env")
    admission = dict(
        schema_version="1.0",
        operation_id="m03-independent-admit",
        project_id=pid,
        job_id="m03-independent-encode",
        kind="encode",
        target=dict(
            target_id=items[1]["item_id"],
            version={"item_id": items[1]["item_id"], "version_id": items[1]["version_id"]},
            shot_id=None,
        ),
        input_digest=hashlib.sha256(output.getvalue()).hexdigest(),
        input_artifact_ids=["m03-image-B"],
        execution_profile_id="m03-test-encode",
    )
    independent, _ = services.jobs.admit(caller, admission)
    with store.transaction() as tx:
        work = tx.require("work", "m03-fixture-work")
        tx.put(
            "work",
            "m03-fixture-work",
            {**work, "state": "completed", "state_revision": 1},
            expected_revision=0,
            schema="WorkRecord",
        )
        inv = tx.require("invocations", "m03-fixture-invocation")
        tx.put(
            "invocations",
            "m03-fixture-invocation",
            {**inv, "state": "completed"},
            expected_revision=0,
        )
    services.gate.close(pid)
    return dict(
        project_id=pid,
        path=str(workspace),
        items=items,
        independent_job=independent,
        fixture_sha256={
            str(p.relative_to(workspace)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (workspace / "assets").glob("m03-*.txt")
        },
    )


class ObservedNative(Native):
    def __init__(self, services, root):
        super().__init__(services)
        self.root = root
        self.open_calls = 0
        self.deadlines = {}

    async def open(self, project_id, session_id, callbacks):
        self.open_calls += 1
        callbacks = dict(callbacks)
        original_event = callbacks["on_event"]

        def event(value):
            original_event(value)
            raw = wire(value)
            data = raw.get("data", {})
            allowed = {
                "messageId",
                "originatingMessageId",
                "clientTurnId",
                "toolCallId",
                "toolName",
                "turnId",
                "parentToolCallId",
                "agentName",
                "model",
                "modelSelectionSource",
                "firstDispatchedModel",
                "success",
                "status",
                "state",
                "requestId",
            }
            observed = {
                k: raw[k] for k in ("id", "type", "parentId", "agentId", "timestamp") if k in raw
            }
            observed["data"] = {k: data[k] for k in allowed if k in data}
            if raw["type"] == "tool.execution_start" and data.get("toolName") in {
                "task",
                "bash",
                "get_version",
            }:
                observed["data"]["arguments"] = data.get("arguments")
            if raw["type"] in {
                "subagent.started",
                "subagent.completed",
                "subagent.failed",
                "tool.execution_start",
                "tool.execution_complete",
                "user.message",
                "assistant.turn_end",
                "session.idle",
                "session.error",
                "user_input.requested",
                "user_input.completed",
            }:
                append(self.root / "native-events.jsonl", dict(observed_at=now(), **observed))

        callbacks["on_event"] = event

        def guard(data, context):
            name, args = data["toolName"], data["toolArgs"]
            if name == "task":
                if args.get("agent_type") != "authoring-specialist" or not data.get("toolCallId"):
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Only the bounded configured specialist is authorised.",
                    }
                try:
                    reserve(self.root, "child", f"{data['sessionId']}:{data['toolCallId']}")
                except RuntimeError:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Native child allocation exhausted. No further delegation.",
                    }
            elif name == "bash":
                if args.get("command", "").strip() not in {
                    "python3 m03_direct.py",
                    "python3 m03_hold.py",
                    "python3 m03_transport.py",
                }:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Only the three bounded in-project M03 helper commands are authorised.",
                    }
            elif name not in {
                "get_version",
                "get_job",
                "get_artifact",
                "ask_user",
                "task_complete",
                "tool_search_tool_regex",
            }:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "This M03 allocation allows only read/status/decision and bounded helper operations.",
                }
            return {"permissionDecision": "allow"}

        callbacks["hooks"] = {**callbacks["hooks"], "on_pre_tool_use": guard}
        session = await super().open(project_id, session_id, callbacks)
        original_send = session.send

        async def send(prompt, **kwargs):
            turn = kwargs.get("client_turn_id")
            with self.services.registry.project_store(project_id).transaction(write=False) as tx:
                request = tx.get("requests", str(turn), required=False)
            mode = request["submission"]["type"] if request else "continuation"
            reserve(
                self.root,
                "send",
                f"{project_id}:{turn}",
                project_id=project_id,
                request_id=turn,
                delivery_mode=mode,
                native_session_id=session.session_id,
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            )
            append(
                self.root / "sends.jsonl",
                dict(
                    at=now(),
                    request_id=turn,
                    mode=mode,
                    sdk_mode=kwargs.get("mode"),
                    session_id=session.session_id,
                ),
            )
            self.deadlines[str(turn)] = time.monotonic() + 150
            # No delayed/local acknowledgement fabrication: this awaits the actual SDK transport.
            result = await original_send(prompt, **kwargs)
            append(
                self.root / "send-acks.jsonl",
                dict(at=now(), request_id=turn, native_message_id=result),
            )
            return result

        session.send = send
        return session


class FixtureMonitor:
    def __init__(self, services, root, fixture):
        self.services, self.root, self.fixture = services, root, fixture
        self.encoder = None
        self.task = None
        self.seen_flags = set()

    def worker(self):
        job = self.services.jobs.get(self.fixture["project_id"], "m03-independent-encode")
        caller = Caller(
            "worker",
            "m03-owned-ffmpeg",
            self.fixture["project_id"],
            owner_epoch=self.services.ownership.epoch,
            job_id=job["job_id"],
            attempt_id=job["attempt_id"],
            input_digest=job["input_digest"],
            operations=("updateJobState",),
        )
        return job, caller

    def update(self, state, exit_code=None):
        job, caller = self.worker()
        body = dict(
            schema_version="1.0",
            operation_id="m03-worker-" + state,
            project_id=self.fixture["project_id"],
            job_id=job["job_id"],
            attempt_id=job["attempt_id"],
            expected_revision=job["state_revision"],
            state=state,
            availability=job["availability"],
        )
        if exit_code is not None:
            body["native_exit_code"] = exit_code
        self.services.jobs.update(
            caller,
            body,
            evidence="Observed exact owned FFmpeg process/exit; fixture worker, not a render",
        )

    async def run(self):
        runtime = self.services.extension("copilot")
        while True:
            for turn, deadline in list(runtime.native.deadlines.items()):
                if time.monotonic() > deadline:
                    with self.services.registry.project_store(
                        self.fixture["project_id"]
                    ).transaction(write=False) as tx:
                        request = tx.get("requests", turn, required=False)
                    runtime.native.deadlines.pop(turn, None)
                    if request and request["receipt"]["state"] not in {
                        "completed",
                        "failed",
                        "stopped",
                        "cancelled",
                        "outcome_uncertain",
                    }:
                        append(
                            self.root / "watchdog.jsonl",
                            dict(
                                at=now(), request_id=turn, reason="150-second bounded test deadline"
                            ),
                        )
                        await runtime.failed(self.fixture["project_id"], "m03_test_deadline")
            if (self.root / "start-independent").exists() and self.encoder is None:
                self.update("starting")
                image = self.services.extension("artifacts").ready_path(
                    self.fixture["project_id"], "m03-image-B"
                )
                self.log = (self.root / "encoder.private.log").open("wb")
                self.encoder = subprocess.Popen(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-re",
                        "-loop",
                        "1",
                        "-framerate",
                        "1",
                        "-i",
                        str(image),
                        "-t",
                        "80",
                        "-vf",
                        "scale=32:32",
                        "-c:v",
                        "libx264",
                        "-threads",
                        "1",
                        "-pix_fmt",
                        "yuv420p",
                        "-an",
                        "-y",
                        str(
                            Path(self.fixture["path"])
                            / ".proscenium/tool-output/m03/independent.mp4"
                        ),
                    ],
                    stdout=self.log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                append(
                    self.root / "process-observations.jsonl",
                    dict(
                        at=now(),
                        kind="independent-encoder",
                        pid=self.encoder.pid,
                        start=identity(self.encoder.pid),
                        state="running",
                    ),
                )
                self.update("running")
            if (
                self.encoder
                and self.encoder.poll() is not None
                and "encoder-exit" not in self.seen_flags
            ):
                self.seen_flags.add("encoder-exit")
                self.encoder.wait()
                self.update(
                    "succeeded" if self.encoder.returncode == 0 else "failed",
                    self.encoder.returncode,
                )
                self.log.close()
                append(
                    self.root / "process-observations.jsonl",
                    dict(
                        at=now(),
                        kind="independent-encoder",
                        pid=self.encoder.pid,
                        exit_code=self.encoder.returncode,
                        reaped=True,
                    ),
                )
            for session in list(runtime.sessions.values()):
                try:
                    pending = await runtime.native.pending(session)
                    append(
                        self.root / "queues.jsonl",
                        dict(at=now(), session_id=session.session_id, pending=pending),
                    )
                except Exception:
                    pass
            if (
                self.root / "cut-owned-transport"
            ).exists() and "transport-cut" not in self.seen_flags:
                self.seen_flags.add("transport-cut")
                client = runtime.native.client if runtime.native else None
                process = getattr(client, "_process", None)
                if process is not None and process.stdin is not None:
                    append(
                        self.root / "transport.jsonl",
                        dict(
                            at=now(),
                            operation="close actual SDK-owned stdin pipe",
                            pid=process.pid,
                            start=identity(process.pid),
                            native_send_ack_loss_claimed=False,
                        ),
                    )
                    process.stdin.close()
            # Host-only capability fixtures are never placed in the active project/model context.
            if not (self.root / "boundary.private.json").exists():
                with self.services.registry.project_store(self.fixture["project_id"]).transaction(
                    write=False
                ) as tx:
                    pending = [d for d in tx.all("decisions") if d["state"] == "pending"]
                    if pending:
                        decision = pending[0]
                        caller = Caller(
                            "tool",
                            "m03-boundary-test",
                            self.fixture["project_id"],
                            decision["work_id"],
                            self.services.ownership.epoch,
                            invocation_id=decision["invocation_id"],
                            operations=("registerToolArtifact",),
                        )
                    else:
                        caller = None
                if caller:
                    private = dict(
                        token=self.services.auth.issue_capability(caller),
                        decision_id=decision["decision_id"],
                        fingerprint=decision["fingerprint"],
                        revision=decision["state_revision"],
                    )
                    (self.root / "boundary.private.json").write_text(json.dumps(private))
                    (self.root / "boundary.private.json").chmod(0o600)
            (self.root / "monitor.json").write_text(
                json.dumps(
                    dict(
                        at=now(),
                        native_open_calls=runtime.native.open_calls,
                        encoder_pid=self.encoder.pid if self.encoder else None,
                        encoder_running=self.encoder is not None and self.encoder.poll() is None,
                    )
                )
            )
            await asyncio.sleep(0.25)

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.encoder and self.encoder.poll() is None:
            # Exact own fixture worker, never a global/native/provider process group.
            self.encoder.terminate()
            self.encoder.wait(timeout=10)
            self.update("failed", self.encoder.returncode)
            self.log.close()


def main(root):
    root = root.resolve()
    assert root.parent == Path(
        "/home/quintin/.cache/proscenium-development"
    ) and root.name.startswith("m03-runtime-proof-")
    root.mkdir(mode=0o700, exist_ok=True)
    assert not (root / "fixture.json").exists(), "Never recreate an existing proof/admission"
    catalogue = json.loads((root / "catalogue/catalogue.json").read_text())
    models = {m["id"]: m for m in catalogue["models"]}
    worker = "gpt-6-luna"
    lead_price = models["gpt-6.1-sol"]["billing"]["tokenPrices"]
    worker_price = models[worker]["billing"]["tokenPrices"]
    assert (
        worker_price["inputPrice"] < lead_price["inputPrice"]
        and worker_price["outputPrice"] < lead_price["outputPrice"]
    )
    origin = f"http://127.0.0.1:{free_port()}"
    settings = Settings(
        data_directory=root / "machine",
        origin=origin,
        frontend_directory=REPO / "apps/frontend/dist",
        free_space_reserve_bytes=0,
        execution_profiles=frozenset({"m03-test-encode"}),
        profiles=(
            {
                "profile_id": "authoring",
                "name": "M03 bounded native",
                "lead_model_id": "gpt-6.1-sol",
                "worker_model_ids": [worker],
                "scope_status": "project_scoped",
            },
        ),
        runtime=dict(
            cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            worker_model=worker,
            worker_tools=["bash"],
            skill_directories=[str(REPO / "agent/skills")],
            mcp_servers={},
        ),
    )
    services = Services.create(settings)
    configure_services(services)
    fixture = prepare(services, root)
    (root / "fixture.json").write_text(json.dumps(fixture, indent=2))
    runtime = services.extension("copilot")
    runtime.native = ObservedNative(services, root)
    monitor = FixtureMonitor(services, root, fixture)
    app = create_app(settings, services=services, manage_features=True)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            monitor.task = asyncio.create_task(monitor.run())
            try:
                yield
            finally:
                await monitor.close()

    app.router.lifespan_context = lifespan
    private = dict(
        root=str(root),
        origin=origin,
        project_id=fixture["project_id"],
        pair_url=f"{origin}/#pair={services.auth.issue_pairing()}",
        worker_model=worker,
        model_prices={"lead": lead_price, "worker": worker_price},
        server_pid=os.getpid(),
        server_start=identity(os.getpid()),
    )
    (root / "host.private.json").write_text(json.dumps(private, indent=2))
    (root / "host.private.json").chmod(0o600)
    print(
        json.dumps(
            {k: private[k] for k in ("root", "origin", "project_id", "worker_model", "server_pid")}
        ),
        flush=True,
    )
    try:
        uvicorn.run(app, host="127.0.0.1", port=int(origin.rsplit(":", 1)[1]), log_level="warning")
    finally:
        services.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
