"""One actual owner/native parent-child admission proof. Provider execution DISABLED.

No lifecycle/provider pump is started. Any pump or provider HTTP attempt fails the
allocation independently. The production W01 callbacks, Tools and ProviderService.submit
remain unchanged. Fixture publication authority is explicitly labelled, never a child.
"""

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from copilot.session import CopilotSession
from proscenium.api.composition import configure_services
from proscenium.api.errors import DomainError
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.copilot.native import Native
from proscenium.copilot.service import wire
from proscenium.copilot.tools import Tools
from proscenium.providers.sd_turbo_fp16 import FILENAME, REVISION, graph
from proscenium.services import Services
from proscenium.sources import closure_digest
from proscenium.sources.common import git

REPO = Path(__file__).resolve().parents[3]
ROOT = Path.home() / ".cache/proscenium-development/milestones/m07-native-budget-20261114"
ORIGIN = "http://127.0.0.1:18961"
MODEL = "gpt-6.1-sol"
PNG = "docs/evidence/m07-fp16/execution/m07-fp16-initial.png"
PNG_SHA = "07673e7a7cea364e56b52bb4bc255722678e0cbf20b1b2c714e819c5ab84806d"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, indent=2, default=str) + "\n")


def append(name, value):
    with (ROOT / name).open("a") as stream:
        stream.write(json.dumps({"at": time.time(), **value}, default=str) + "\n")


def memory():
    fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(fields["MemAvailable"].split()[0]) * 1024


class ZeroHTTP(httpx.BaseTransport):
    def __init__(self):
        self.attempts = []

    def handle_request(self, request):
        self.attempts.append({"method": request.method, "url": str(request.url)})
        append("forbidden-provider-http.jsonl", self.attempts[-1])
        raise RuntimeError("Admission-only allocation forbids ALL provider HTTP")


class Bounds:
    def __init__(self):
        self.sends = 0
        self.delegations = set()
        self.child_started = None
        self.children = set()
        self.violations = []
        self.session = None
        self.tasks = []

    def pre_tool(self, data, context):
        name, args = data["toolName"], data.get("toolArgs", {})
        denied = None
        if name == "task":
            call = data.get("toolCallId")
            if (
                data.get("agentId")
                or not call
                or args.get("agent_type") != "authoring-specialist"
                or (self.delegations and call not in self.delegations)
            ):
                denied = "One root-owned authoring-specialist child only"
            else:
                self.delegations.add(call)
        elif name not in {"submit_generation", "get_artifact", "get_version"}:
            denied = "Admission-only allocation: no shell, reads outside named metadata, execution or other tools"
        if denied:
            self.violations.append({"tool": name, "reason": denied})
            append("bounds-violations.jsonl", self.violations[-1])
            return {"permissionDecision": "deny", "permissionDecisionReason": denied}
        return None  # Never manufacture a permission approval.

    async def deadline(self, seconds, reason):
        await asyncio.sleep(seconds)
        self.violations.append({"deadline": reason})
        append("bounds-violations.jsonl", {"deadline": reason})
        assert self.session is not None
        await self.session.abort()

    def observe_event(self, event):
        value = wire(event)
        kind = value["type"]
        if kind not in {
            "assistant.message_delta",
            "assistant.reasoning_delta",
            "assistant.reasoning",
        }:
            append("native-events.jsonl", {"event": value})
        data = value.get("data", {})
        if kind == "subagent.started":
            agent = value.get("agent_id") or value.get("agentId") or data.get("agentId")
            self.children.add(agent)
            self.child_started = time.monotonic()
            self.tasks.append(asyncio.create_task(self.deadline(120, "child-120s")))
            if len(self.children) > 1:
                self.violations.append({"extra_child": agent})
                assert self.session is not None
                asyncio.create_task(self.session.abort())
        if kind in {"subagent.completed", "subagent.failed"}:
            for task in self.tasks[1:]:
                task.cancel()


def install_observers(bounds):
    original_open, original_send, original_tool = Native.open, CopilotSession.send, Tools.invoke

    async def opened(native, project_id, session_id, callbacks):
        prompt = callbacks["hooks"]["on_user_prompt_submitted"]
        event = callbacks["on_event"]

        def hook(data, context):
            result = prompt(data, context)  # Actual W01 binding and additionalContext.
            append("prompt-hooks.jsonl", {"input": data, "result": result})
            return result

        def on_event(value):
            event(value)  # Real reducer first. No forged native event or authority.
            bounds.observe_event(value)

        callbacks = {
            **callbacks,
            "on_event": on_event,
            "hooks": {
                **callbacks["hooks"],
                "on_user_prompt_submitted": hook,
                "on_pre_tool_use": bounds.pre_tool,
            },
        }
        return await original_open(native, project_id, session_id, callbacks)

    async def send(session, prompt, **kwargs):
        if bounds.sends:
            raise RuntimeError("Exactly one SDK root send authorised; no continuation sends")
        if memory() < 4 * 1024**3:
            raise RuntimeError("Four-GiB headroom floor before only SDK send")
        bounds.sends += 1
        bounds.session = session
        append("sdk-send.jsonl", {"session_id": session.session_id, "prompt": prompt, **kwargs})
        bounds.tasks.append(asyncio.create_task(bounds.deadline(180, "root-180s")))
        return await original_send(session, prompt, **kwargs)

    async def invoke(tools, name, invocation):
        result = await original_tool(tools, name, invocation)
        append(
            "tool-results.jsonl",
            {
                "tool": name,
                "arguments": invocation.arguments,
                "invocation": {
                    key: getattr(invocation, key, None)
                    for key in ("tool_call_id", "session_id", "agent_id", "turn_id")
                },
                "result_type": result.result_type,
                "result": result.text_result_for_llm,
            },
        )
        return result

    setattr(Native, "open", opened)
    setattr(CopilotSession, "send", send)
    setattr(Tools, "invoke", invoke)

    def restore():
        setattr(Native, "open", original_open)
        setattr(CopilotSession, "send", original_send)
        setattr(Tools, "invoke", original_tool)

    return restore


def settings(root):
    return Settings(
        data_directory=root / "machine",
        origin=ORIGIN,
        generation_attempt_limit=3,
        profiles=(
            dict(
                profile_id="authoring",
                name="M07 actual native budget admission",
                lead_model_id=MODEL,
                worker_model_ids=[MODEL],
                scope_status="project_scoped",
            ),
        ),
        services=(
            dict(
                provider_id="m07-budget-unprobed",
                endpoint="http://127.0.0.1:18589",
                capabilities=["image"],
                model_id=None,
                available=None,
            ),
        ),
        provider_options={"image_profiles": {"m07-budget-unprobed": REVISION}},
        runtime=dict(
            cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            lead_model=MODEL,
            worker_model=MODEL,
            worker_tools=["submit_generation", "get_artifact", "get_version"],
            profile_id="authoring",
            skill_directories=[],
            mcp_servers={},
            max_continues=5,
        ),
    )


def fixture(services, root):
    owner = Caller("owner", "m07-budget-scripted-owner")
    projects = services.extension("projects")
    project, _ = projects.open(
        owner,
        dict(
            schema_version="1.0",
            operation_id="m07-budget-open",
            path=str(root / "project"),
            create=True,
            name="M07 native budget admission ONLY",
        ),
    )
    pid = project["project_id"]
    projects.activate(
        owner,
        dict(
            schema_version="1.0",
            operation_id="m07-budget-activate",
            project_id=pid,
            expected_revision=project["state_revision"],
        ),
    )
    services.gate.resume(pid, reconciliation_evidence="Explicit bounded admission-only allocation")
    with services.registry.project_store(pid).transaction() as tx:
        tx.put(
            "work",
            "fixture-publication-work",
            dict(
                schema_version="1.0",
                work_id="fixture-publication-work",
                project_id=pid,
                originating_request_id="fixture-not-a-native-request",
                directive_request_ids=[],
                state="running",
                state_revision=0,
                native_session_id="fixture-no-session",
                job_ids=[],
                generation_task_id="fixture-no-generation-task",
            ),
            schema="WorkRecord",
        )
        tx.put(
            "invocations",
            "fixture-publication-invocation",
            dict(
                work_id="fixture-publication-work",
                owner_epoch=services.ownership.epoch,
                state="running",
            ),
        )
    publisher = Caller(
        "tool",
        "labelled-input-fixture-publisher",
        pid,
        "fixture-publication-work",
        services.ownership.epoch,
        operations=("registerToolArtifact", "publishOutputs"),
        invocation_id="fixture-publication-invocation",
    )
    artifacts = services.extension("artifacts")
    raw = (REPO / PNG).read_bytes()
    assert sha(raw) == PNG_SHA
    image, _ = artifacts.upload(owner, pid, "m07-budget-real-png-input", raw, "image/png")
    staging = root / "project/.proscenium/tool-output/staging"
    staging.mkdir(parents=True, exist_ok=True)
    for name, data, media in [
        ("m07-budget-graph", canonical(graph(image["artifact_id"])), "application/json"),
        (
            "m07-budget-prompt",
            b"Admission-only frozen reference fixture. No execution authorised.",
            "text/plain",
        ),
    ]:
        (staging / name).write_bytes(data)
        artifacts.register(
            publisher,
            dict(
                schema_version="1.0",
                operation_id=name + "-register",
                project_id=pid,
                artifact_id=name,
                staging_path=".proscenium/tool-output/staging/" + name,
                kind="document",
                purpose="metadata",
                sha256=sha(data),
                media_type=media,
            ),
        )
    plan = dict(
        schema_version="1.0",
        plan_id="m07-budget-plan",
        project_id=pid,
        brief_artifact_ids=[],
        asset_dependencies=[],
        unresolved=[],
        shots=[
            dict(
                shot_id="shot-mug",
                intent="Admission-only canonical target",
                camera_intent="No execution",
                motion_beats=[],
                scene_version=None,
                source_range=None,
                fps=None,
                frames=[
                    dict(
                        frame_id="frame-mug",
                        role="start",
                        composition="Read-only prior actual M07 PNG",
                        artifact_id=image["artifact_id"],
                        state="available",
                    )
                ],
            )
        ],
    )
    project_root = root / "project"
    name = "storyboards/m07-budget-plan.json"
    (project_root / "storyboards").mkdir(exist_ok=True)
    data = canonical(plan)
    (project_root / name).write_bytes(data)
    git(project_root, "config", "user.name", "M07 budget labelled fixture")
    git(project_root, "config", "user.email", "m07-budget@example.invalid")
    git(project_root, "add", "--", name, ".gitattributes")
    git(
        project_root,
        "commit",
        "-m",
        "M07 admission-only reference and canonical storyboard fixture",
    )
    commit = git(project_root, "rev-parse", "HEAD").decode().strip()
    closure = dict(
        entry_path=name,
        files=[
            dict(
                destination=name,
                origin=dict(
                    kind="git",
                    repository_id=project["repository_id"],
                    commit=commit,
                    path=name,
                    sha256=sha(data),
                ),
            )
        ],
        dependencies=[],
        required_runtime=dict(application="fixture", version="1", features=[]),
        digest="0" * 64,
    )
    closure["digest"] = closure_digest(closure)
    publication, _ = services.extension("catalogue").publish(
        publisher,
        dict(
            schema_version="1.0",
            publication_id="m07-budget-publication",
            project_id=pid,
            items=[
                dict(
                    item_id="m07-budget-storyboard",
                    version_id="m07-budget-plan-v1",
                    kind="storyboard",
                    name="M07 admission-only target",
                    expected_current_version_id=None,
                    source_commit=commit,
                    source=closure,
                    artifact_ids=[image["artifact_id"]],
                    preview_requests=[],
                    description="Labelled previously generated M07 PNG input, no creative output or selection claimed",
                )
            ],
        ),
    )
    with services.registry.project_store(pid).transaction() as tx:
        w = tx.require("work", "fixture-publication-work")
        tx.put(
            "work",
            w["work_id"],
            {**w, "state": "completed", "state_revision": 1},
            expected_revision=0,
            schema="WorkRecord",
        )
        assert not tx.all("jobs") and not tx.all("requests")
    base = dict(
        schema_version="1.0",
        project_id=pid,
        generation_task_id="COPY_FROM_TRUSTED_HOST_CONTEXT",
        provider_id="m07-budget-unprobed",
        capability="image",
        workflow_artifact_id="m07-budget-graph",
        prompt_artifact_id="m07-budget-prompt",
        input_artifact_ids=[image["artifact_id"]],
        seed="42",
        model_ids=[FILENAME],
        settings={
            "target_slot": dict(
                kind="storyboard_frame",
                storyboard_version=dict(
                    item_id="m07-budget-storyboard", version_id="m07-budget-plan-v1"
                ),
                shot_id="shot-mug",
                frame_id="frame-mug",
            )
        },
    )
    bodies = [
        {**base, "job_id": f"m07-budget-{i}", "target_id": f"renamed-alias-{i}"}
        for i in range(1, 5)
    ]
    prompt = (
        "M07 ADMISSION-ONLY native parent/child budget test. Executor is deliberately DISABLED. Stop on admission receipts, never poll outputs. "
        "No files, shell, provider HTTP, services, GPU, generation execution, approvals or other work. "
        "Use submit_generation twice as lead with envelopes 1 and 2 below, replacing generation_task_id ONLY with the exact authoritative HOST extension-context generation_task_id. "
        "Then delegate EXACTLY ONE task to authoring-specialist (required gpt-6.1-sol), no other child. "
        "Give child envelopes 3 and 4 and tell it to read its own trusted additionalContext JSON inside system_reminder and use that ORIGINAL generation_task_id, not a new task. "
        "Child must call submit_generation for 3, then for 4 even though target_id is renamed. All four settings.target_slot values are identical. "
        "Expect jobs 1/2/3 planned and fourth generation_budget_exhausted. Do not retry, change allowance, or claim images. "
        "Child finishes <=120s, root <=180s. Named registered inputs are m07-budget-real-png-input (actual prior M07 PNG), m07-budget-graph and m07-budget-prompt; no discovery needed. "
        + json.dumps(bodies)
    )
    return dict(
        project=project,
        image=image,
        plan=plan,
        publication=publication,
        source_commit=commit,
        source_path=str(project_root / name),
        source_sha256=sha(data),
        envelopes=bodies,
        prompt=prompt,
        input_original_path=PNG,
        input_original_sha256=PNG_SHA,
    )


def snapshot(services, pid):
    with services.registry.project_store(pid).transaction(write=False) as tx:
        namespaces = [
            r[0]
            for r in tx.connection.execute(
                "SELECT DISTINCT namespace FROM records ORDER BY namespace"
            )
        ]
        records = {ns: tx.all(ns) for ns in namespaces}
        events = []
        after = 0
        while True:
            page = tx.events(after, 100)
            events.extend(page["events"])
            if page["next_after"] >= page["last_available"]:
                break
            after = page["next_after"]
        reservations = [dict(r) for r in tx.connection.execute("SELECT * FROM budget_reservations")]
    with services.resources.store.transaction(write=False) as tx:
        claims = [dict(r) for r in tx.connection.execute("SELECT * FROM resource_claims")]
    return dict(records=records, events=events, reservations=reservations, resource_claims=claims)


async def owner_queue(client, services, pid, prompt):
    operation = str(uuid4())
    response = await client.post(
        "/api/v1/owner/session",
        json=dict(
            schema_version="1.0",
            operation_id=operation,
            client_nonce="m07-budget-owner",
            pairing_code=services.auth.issue_pairing(),
        ),
        headers={"Idempotency-Key": operation},
    )
    response.raise_for_status()
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    image = services.extension("artifacts").get(pid, "m07-budget-real-png-input")
    context_id = str(uuid4())
    context = dict(
        schema_version="1.0",
        context_id=context_id,
        project_id=pid,
        captured_at=datetime.now().astimezone().isoformat(),
        item_id="m07-budget-storyboard",
        version_id="m07-budget-plan-v1",
        view_mode="keyframes",
        camera=None,
        range=None,
        selected_objects=[],
        annotations=[],
        approved_findings=[],
        attachments=[dict(artifact_id=image["artifact_id"], role="reference")],
        target=dict(
            origin="concept",
            artifact_id=image["artifact_id"],
            displayed_sha256=image["sha256"],
            dimensions=image["dimensions"],
            item_id="m07-budget-storyboard",
            version_id="m07-budget-plan-v1",
            shot_id="shot-mug",
            render_job_id=None,
            source_frame=None,
            media_artifact_id=None,
        ),
    )
    response = await client.post(
        f"/api/v1/projects/{pid}/contexts", json=context, headers={"Idempotency-Key": context_id}
    )
    response.raise_for_status()
    request_id = str(uuid4())
    body = dict(
        schema_version="1.0",
        request_id=request_id,
        project_id=pid,
        type="queue",
        text=prompt,
        context_id=context_id,
        profile_id="authoring",
    )
    response = await client.post(
        f"/api/v1/projects/{pid}/requests", json=body, headers={"Idempotency-Key": request_id}
    )
    result = {"body": body, "status": response.status_code, "receipt": response.json()}
    response.raise_for_status()
    return result


async def execute():
    ROOT.mkdir(parents=True, mode=0o700, exist_ok=False)
    started = time.monotonic()
    before = memory()
    if before < 4 * 1024**3:
        save("blocked.json", {"reason": "4GiB initial headroom", "mem_available": before})
        return
    services = Services.create(settings(ROOT))
    configure_services(services)
    provider = services.extension("providers")
    provider.client.close()
    transport = ZeroHTTP()
    provider.client = httpx.Client(transport=transport, trust_env=False)
    provider.owns_client = True
    pump_attempts = []

    def disabled_pump(*args, **kwargs):
        pump_attempts.append(time.time())
        raise RuntimeError("Provider execution is DISABLED in this admission-only fixture")

    provider.pump = disabled_pump
    bounds = Bounds()
    restore = install_observers(bounds)
    runtime = services.extension("copilot")
    samples = []

    async def monitor():
        while True:
            available = memory()
            samples.append({"at": time.time(), "mem_available": available})
            if available < 4 * 1024**3:
                bounds.violations.append({"headroom": available})
                if bounds.session:
                    await bounds.session.abort()
                return
            await asyncio.sleep(0.5)

    monitor_task = asyncio.create_task(monitor())
    pid = None
    report: dict[str, Any] = {
        "started_at": datetime.now().astimezone().isoformat(),
        "pid": os.getpid(),
        "base": "dd3abd2a9d3a6d7365e824c089276c1ccc1a64b6",
        "execution_disabled": True,
        "application_transport": "in-process ASGI, no listener/service process",
        "before_mem_available": before,
    }
    try:
        f = fixture(services, ROOT)
        save("fixture.json", f)
        pid = f["project"]["project_id"]
        app = create_app(services=services, manage_features=False)
        async with app.router.lifespan_context(app):
            await runtime.start()  # W01 only. NO FeatureLifecycle/provider/preview pump.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}
            ) as client:
                queued = await owner_queue(client, services, pid, f["prompt"])
                save("owner-queue.json", queued)
                request_id = queued["body"]["request_id"]
                end = time.monotonic() + 185
                while time.monotonic() < end:
                    with services.registry.project_store(pid).transaction(write=False) as tx:
                        decisions = [d for d in tx.all("decisions") if d["state"] == "pending"]
                        request = tx.require("requests", request_id)
                    if decisions:
                        save("unexpected-decision.json", decisions)
                        raise RuntimeError(
                            "Unexpected genuine decision; exact question retained, no self-approval"
                        )
                    if bounds.violations or transport.attempts or pump_attempts:
                        raise RuntimeError(
                            "Allocation guard failed; retain original evidence, no second send"
                        )
                    if request["receipt"]["state"] in {
                        "completed",
                        "failed",
                        "stopped",
                        "outcome_uncertain",
                    }:
                        report["request_final"] = request
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise TimeoutError("Only original owner Queue exceeded deadline")
                save("settled.json", snapshot(services, pid))
    except Exception as error:
        report["blocker"] = type(error).__name__ + ": " + str(error)
    finally:
        if pid:
            services.gate.close(pid)
        await runtime.close()
        for task in bounds.tasks:
            task.cancel()
        monitor_task.cancel()
        await asyncio.gather(monitor_task, *bounds.tasks, return_exceptions=True)
        await provider.close()
        if pid:
            save("post-cleanup.json", snapshot(services, pid))
            try:
                services.gate.require(pid)
                report["gate_closed"] = False
            except DomainError:
                report["gate_closed"] = True
        report.update(
            elapsed_seconds=time.monotonic() - started,
            sdk_sends=bounds.sends,
            guarded_task_calls=len(bounds.delegations),
            children_observed=list(bounds.children),
            violations=bounds.violations,
            provider_http_attempts=transport.attempts,
            provider_pump_attempts=pump_attempts,
            input_original_unchanged=sha((REPO / PNG).read_bytes()) == PNG_SHA,
            minimum_mem_available=min((v["mem_available"] for v in samples), default=before),
        )
        process = ROOT / "machine/copilot-runtime/process.json"
        if process.exists():
            native = json.loads(process.read_bytes())
            report["native_process"] = native
            report["native_process_exited"] = not Path(f"/proc/{native['pid']}").exists()
        save("resources.json", samples)
        save("report.json", report)
        services.close()
        restore()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(execute(), 270))
