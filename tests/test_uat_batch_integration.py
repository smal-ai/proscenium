"""Independent HTTP/native-adapter/storage journeys. No native or provider execution."""

import asyncio
import base64
import copy
import io
import json
import re
import subprocess
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import httpx
import pytest
import test_worker_capabilities_and_publication as worker_fixtures
from copilot import CopilotClient
from copilot.tools import ToolBinaryResult, ToolInvocation
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
from proscenium.api.dependencies import owner
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue.router import router as catalogue_router
from proscenium.contracts import canonical, validate
from proscenium.copilot.outcomes import get_tool_outcome
from proscenium.copilot.router import router as copilot_router
from proscenium.copilot.service import Copilot
from proscenium.copilot.state import key
from proscenium.db import Store
from proscenium.db.registry import Registry
from proscenium.jobs.resources import Resources
from proscenium.projects.service import Projects
from proscenium.providers.bridge import register_provider_artifact
from proscenium.providers.targets import admit_target, resolve_target
from proscenium.sources import Sources, closure_digest
from proscenium.sources.common import git, sha
from test_provider_klein_and_pump import add_tiled_nodes, add_vae_dtype_node, raster
from test_request_targeting import add_version, context, plan
from test_worker_capabilities_and_publication import PROJECT, SESSION
from test_worker_capabilities_and_publication import publication as source_publication

from tools.operations.recovery import backup, restore, verify


def test_canonical_documentation_links_and_agent_instructions_are_checkout_independent():
    root = Path(__file__).resolve().parents[1]
    files = [
        root / "README.md",
        *sorted((root / "docs").glob("*.md")),
        root / "docs/contracts/README.md",
        root / "agent/profiles/README.md",
        *sorted((root / "agent/skills").glob("*/SKILL.md")),
    ]
    for path in files:
        text = path.read_text()
        links = re.findall(r'\[[^\]]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)', text)
        links += re.findall(r"^\[[^\]]+\]:\s*(\S+)", text, re.MULTILINE)
        for target in links:
            if target.startswith(("https://", "http://", "#")):
                continue  # External liveness is audited separately, never during offline checks.
            assert (path.parent / target.split("#")[0]).resolve().exists(), (path, target)
        if path.name == "SKILL.md" or path == root / "agent/profiles/README.md":
            assert not any(
                helper in text
                for helper in ("plan_tools.py", "tools/comfy/graph.py", "python3 -m tools.blender")
            ), path
    tracked = (
        subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"]).decode().split("\0")
    )
    forbidden = re.compile(
        r"(^|/)(\.playwright-mcp|screenshots?|logs?|credentials?)(/|$)|\.(sqlite3?|db|log|png|jpe?g|mp4)$"
    )
    assert not [path for path in tracked if forbidden.search(path)]


class SyntheticNative:
    """Exercise real callback registration and reducer bindings, never an SDK process."""

    def __init__(self):
        self.sent = []
        self.session = SimpleNamespace(session_id=SESSION, send=self.send)

    async def open(self, project_id, session_id, callbacks):
        self.callbacks = callbacks
        return self.session

    async def send(self, text, **kwargs):
        self.sent.append((text, copy.deepcopy(kwargs)))
        return f"message-{len(self.sent)}"

    async def pending(self, session):
        return dict(items=[], steeringMessages=[], inFlightSteeringCount=0)

    def emit(self, identity, kind, parent, agent=None, **data):
        event = dict(id=identity, type=kind, parentId=parent, data=data)
        if agent:
            event["agentId"] = agent
        self.callbacks["on_event"](event)


@pytest.fixture
def tool_runtime(tmp_path):
    yield from worker_fixtures.runtime.__wrapped__(tmp_path)


@pytest.fixture
def journey(tool_runtime, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Native execution and external network are forbidden")

    def forbidden_http(*args, **kwargs):
        raise AssertionError("External HTTP is forbidden")

    monkeypatch.setattr(CopilotClient, "start", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden_http)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    rt = tool_runtime
    registry = Registry(Store(rt.services.settings.data_directory / "registry.sqlite3"))
    registry.register(
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            repository_id="repository-one",
            name="Isolated UAT",
            path=str(rt.root),
            native_session_id=SESSION,
            state="active",
            state_revision=0,
        )
    )
    registry.activate(PROJECT)
    rt.services.registry = registry
    rt.services.activation_lock = RLock()
    rt.services.reconcile_project_projection = lambda project_id: None
    native = SyntheticNative()
    copilot = Copilot(rt.services, native=native)
    rt.services.extensions["copilot"] = copilot
    rt.services.extensions["projects"] = Projects(rt.services)
    rt.copilot, rt.native = copilot, native
    # Remove only the shared fixture's initial synthetic request/work placeholders.
    with rt.store.transaction() as tx:
        tx.connection.execute(
            "DELETE FROM records WHERE namespace IN ('work','requests','copilot.effective')"
        )
    app = FastAPI()
    app.state.services = rt.services
    app.dependency_overrides[owner] = lambda: Caller("owner", "synthetic-owner")

    @app.exception_handler(DomainError)
    async def error(request: Request, exc: DomainError):
        return JSONResponse(exc.envelope(), status_code=exc.status)

    app.include_router(copilot_router)
    app.include_router(catalogue_router)

    @app.get(f"/api/v1/projects/{PROJECT}/snapshot")
    def snapshot():
        return Projects(rt.services).snapshot(PROJECT)

    with TestClient(app) as client:
        rt.client = client
        yield rt


def post(rt, resource, body, status):
    response = rt.client.post(
        f"/api/v1/projects/{PROJECT}/{resource}",
        json=body,
        headers={
            "Idempotency-Key": body.get(
                "operation_id", body.get("request_id", body.get("context_id", "fixture"))
            )
        },
    )
    assert response.status_code == status, response.text
    return response.json()


def queue(rt, body=None):
    body = body or context(request_target={"kind": "project"}, execution_scope="default")
    post(rt, "contexts", body, 201)
    receipt = post(
        rt,
        "requests",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            request_id="R1",
            type="queue",
            text="Create a synthetic concept",
            context_id=body["context_id"],
            generation_attempt_limit=2,
        ),
        202,
    )
    asyncio.run(rt.copilot.send(PROJECT, "R1"))
    rt.work_id = receipt["work_id"]
    with rt.store.transaction(write=False) as tx:
        rt.task_id = tx.require("work", rt.work_id)["generation_task_id"]
    rt.native.emit(
        "root",
        "user.message",
        None,
        clientTurnId="R1",
        messageId="message-1",
        content="Create a synthetic concept",
    )
    return receipt


def invoke(rt, name, body, identity, parent="root", agent=None):
    rt.native.emit(
        identity + "-start",
        "tool.execution_start",
        parent,
        agent,
        toolCallId=identity,
        toolName=name,
        arguments=body,
    )
    tool = next(tool for tool in rt.native.callbacks["tools"] if tool.name == name)
    result = asyncio.run(
        tool.handler(
            ToolInvocation(
                session_id=SESSION,
                tool_call_id=identity,
                tool_name=name,
                arguments=body,
                agent_id=agent,
            )
        )
    )
    rt.native.emit(
        identity + "-done",
        "tool.execution_complete",
        identity + "-start",
        agent,
        toolCallId=identity,
        success=result.result_type != "failure",
        result={"content": result.text_result_for_llm},
    )
    return json.loads(result.text_result_for_llm), result.result_type


def versions(rt):
    fixture = (rt.services, rt.store, rt.root, None)
    old = add_version(fixture, item_id="asset-a", version_id="asset-a-v1")
    board = add_version(
        fixture, item_id="storyboard-b", version_id="board-b-v1", kind="storyboard", document=plan()
    )
    return old, board


def test_http_steer_resume_child_hook_and_generation_keep_latest_intent_original_budget(journey):
    rt = journey
    old, board = versions(rt)
    pin = dict(item_id=old["item_id"], version_id=old["version_id"])
    target = dict(
        kind="shot",
        storyboard_version=dict(item_id=board["item_id"], version_id=board["version_id"]),
        shot_id="shot-one",
    )
    queue(rt, context(request_target=dict(kind="item", version=pin)))
    add_version((rt.services, rt.store, rt.root, None), item_id="asset-a", version_id="asset-a-v2")
    latest = context(
        "context-r2", request_target=target, reference_versions=[pin], execution_scope="default"
    )
    post(rt, "contexts", latest, 201)
    post(
        rt,
        "requests",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            request_id="R2",
            type="steer",
            text="Use B shot one with A v1",
            context_id="context-r2",
            target_work_id=rt.work_id,
        ),
        202,
    )
    asyncio.run(rt.copilot.send(PROJECT, "R2"))
    with rt.store.transaction() as tx:
        rt.copilot.state.fence(tx, "native_turn_aborted")
        rt.copilot.state.work_state(tx, rt.work_id, "awaiting_resume")
        revision = tx.require("work", rt.work_id)["state_revision"]
    command = dict(
        schema_version="1.0",
        project_id=PROJECT,
        operation_id="resume-r1",
        mode="continue_work",
        work_id=rt.work_id,
        expected_revision=revision,
    )
    post(rt, "resume", command, 202)
    path = f"/api/v1/projects/{PROJECT}/resumes/resume-r1"
    assert rt.client.get(path).json()["state"] == "pending"
    asyncio.run(rt.copilot.resume(PROJECT, "resume-r1"))
    assert rt.client.get(path).json()["state"] == "completed"
    replayed = post(rt, "resume", command, 200)
    assert replayed["replayed"]
    asyncio.run(rt.copilot.resume(PROJECT, "resume-r1"))
    assert len(rt.native.sent) == 3  # Queue, Steer and one original continuation.
    payload = rt.native.sent[-1][1]["attachments"][0]["payload"]
    assert payload["request_target"] == target and payload["reference_versions"] == [pin]
    assert payload["generation_task_id"] == rt.task_id
    with rt.store.transaction(write=False) as tx:
        assert tx.require("copilot.effective", "R1")["generation_attempt_limit"] == 2
    rt.native.emit(
        "resume-root", "user.message", None, clientTurnId="resume-r1", messageId="message-3"
    )
    rt.native.emit(
        "child-start",
        "subagent.started",
        "resume-root",
        "child-one",
        agentName="authoring-specialist",
        agentDisplayName="B shot one",
        model="synthetic",
    )
    hook = rt.native.callbacks["hooks"]["on_user_prompt_submitted"]
    assert json.loads(hook({"agentId": "child-one"}, None)["additionalContext"]) == payload
    prepared, result_type = invoke(
        rt,
        "prepare_image_generation",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            generation_task_id=rt.task_id,
            operation_id="prepare-resumed",
            prompt="B shot one",
            seed="42",
            target_slot=dict(
                kind="storyboard_frame",
                storyboard_version=target["storyboard_version"],
                shot_id="shot-one",
                frame_id="frame-one",
            ),
        ),
        "prepare-resumed",
        "child-start",
        "child-one",
    )
    assert result_type != "failure", prepared
    assert (
        prepared["submit_request"]["settings"]["target_slot"]["storyboard_version"]
        == target["storyboard_version"]
    )
    caller = Caller(
        "tool",
        "fixture",
        project_id=PROJECT,
        work_id=rt.work_id,
        invocation_id=key(SESSION, "tool", "prepare-resumed"),
    )
    resolved = resolve_target(rt.services, caller, prepared["submit_request"])
    assert resolved["context"] == latest and resolved["target"]["shot_id"] == "shot-one"
    asset = resolve_target(
        rt.services,
        caller,
        dict(project_id=PROJECT, generation_task_id=rt.task_id, target_id="asset-a", settings={}),
    )
    assert asset["target"]["version"] == pin and asset["item"]["current_version_id"] == "asset-a-v2"
    with rt.store.transaction() as tx:
        assert admit_target(tx, tx.require("work", rt.work_id), resolved)["limit"] == 2
    job, result_type = invoke(
        rt,
        "submit_generation",
        prepared["submit_request"],
        "submit-resumed",
        "child-start",
        "child-one",
    )
    assert result_type != "failure", job
    with rt.store.transaction(write=False) as tx:
        assert tx.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1
    rt.native.emit("child-end", "subagent.completed", "child-start", "child-one", success=True)
    rt.native.emit("turn-start", "assistant.turn_start", "resume-root")
    rt.native.emit(
        "reply",
        "assistant.message",
        "turn-start",
        messageId="reply",
        turnId="turn",
        content="Synthetic continuation finished",
    )
    rt.native.emit("turn-end", "assistant.turn_end", "reply")
    rt.native.emit("idle", "session.idle", "turn-end")
    asyncio.run(rt.copilot.settle(PROJECT, rt.native.session))
    with rt.store.transaction(write=False) as tx:
        assert [tx.require("requests", r)["receipt"]["state"] for r in ("R1", "R2")] == [
            "completed",
            "completed",
        ]
        assert tx.require("work", rt.work_id)["state"] == "completed"
        assert tx.require("work", rt.work_id)["generation_task_id"] == rt.task_id


def test_http_journal_publication_failure_retained_pngs_and_corrective_publication(journey):
    rt = journey
    queue(rt)
    image = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(image, "PNG")
    uploaded = rt.client.put(
        f"/api/v1/projects/{PROJECT}/uploads/upload-reference",
        content=image.getvalue(),
        headers={"Idempotency-Key": "upload-reference", "Content-Type": "image/png"},
    )
    assert uploaded.status_code == 201, uploaded.text
    upload = uploaded.json()
    assert upload["state"] == "ready" and upload["purpose"] == "reference"
    content = rt.client.get(f"/api/v1/projects/{PROJECT}/artifacts/{upload['artifact_id']}/content")
    assert content.status_code == 200 and content.content == image.getvalue()
    artifacts = rt.services.extension("artifacts")
    with rt.store.transaction() as tx:
        artifacts.put(
            tx,
            artifacts.promote(
                PROJECT,
                "legacy-png",
                image.getvalue(),
                kind="image",
                purpose="concept",
                media_type="image/png",
            ),
        )
    staging = rt.root / ".proscenium/tool-output/staging/concept.png"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(image.getvalue())
    from proscenium.sources.common import sha

    registered, kind = invoke(
        rt,
        "register_artifact",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            operation_id="register-png",
            artifact_id="verified-png",
            staging_path=".proscenium/tool-output/staging/concept.png",
            kind="image",
            purpose="concept",
            media_type="image/png",
            sha256=sha(image.getvalue()),
        ),
        "register-png",
    )
    assert kind != "failure", registered
    for identity, data in (
        ("html", b"<html>not a plan</html>"),
        ("malformed", canonical({"schema_version": "1.0"})),
        ("valid", canonical(plan())),
    ):
        body = source_publication(rt, data)
        rt.services.extension("catalogue").sources = Sources(rt.services)
        if identity == "html":
            source = body["items"][0]["source"]
            source["entry_path"] = source["files"][0]["destination"] = "index.html"
            source["digest"] = closure_digest(source)
        body["publication_id"] = "publication-" + identity
        body["items"][0]["artifact_ids"] = ["verified-png"]
        result, kind = invoke(rt, "publish_outputs", body, identity)
        if identity == "valid":
            assert kind != "failure", result
            expected = "published"
        else:
            assert kind == "failure", result
            expected = "publication_blocked"
        with rt.store.transaction(write=False) as tx:
            outcome = get_tool_outcome(tx, key(SESSION, "tool", identity))
            assert outcome["status"] == ("succeeded" if identity == "valid" else "failed")
        events = rt.client.get(f"/api/v1/projects/{PROJECT}/events", params={"limit": 100}).json()[
            "events"
        ]
        completed = next(
            e["payload"]
            for e in events
            if e["event_type"] == "tool.completed"
            and e["payload"]["invocation_id"] == key(SESSION, "tool", identity)
        )
        assert completed["output_status"] == expected
        if identity == "html":
            assert completed["summary"] == "Storyboard source must be AuthoredShotPlan JSON"
            assert "(failure)" not in completed["summary"]
        snapshot = rt.client.get(f"/api/v1/projects/{PROJECT}/snapshot").json()
        validate("ProjectSnapshot", snapshot)
        assert snapshot["execution"]["work_outputs"][0]["status"] == expected
    path = f"/api/v1/projects/{PROJECT}/artifacts"
    first = rt.client.get(path, params=dict(limit=1, kind="image", purpose="concept")).json()
    second = rt.client.get(
        path, params=dict(limit=1, kind="image", purpose="concept", cursor=first["next_cursor"])
    ).json()
    rows = first["items"] + second["items"]
    assert {r["artifact"]["artifact_id"]: r["attribution_status"] for r in rows} == {
        "legacy-png": "unrecorded",
        "verified-png": "verified",
    }
    assert next(r for r in rows if r["attribution_status"] == "unrecorded")["request_id"] is None
    assert rt.client.get(path, params=dict(kind="document")).json()["items"] == []


def test_preparation_accepts_legacy_explicit_null_request_target(journey):
    rt = journey
    queue(rt, context(request_target=None))
    prepared, kind = invoke(
        rt,
        "prepare_image_generation",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            generation_task_id=rt.task_id,
            operation_id="prepare-legacy",
            prompt="Legacy concept",
            seed="42",
        ),
        "prepare-legacy",
    )
    assert kind != "failure", prepared


def test_backup_verify_restore_preserves_new_context_outcomes_preparation_and_cursors(
    journey, tmp_path
):
    rt = journey
    source = source_publication(rt, canonical(plan()))
    old, _ = versions(rt)
    # The shared retained-source fixture uses a synthetic commit. Recovery also
    # requires every publication's source_commit to be locally reachable.
    with rt.store.transaction() as tx:
        for version in tx.all("versions"):
            tx.put(
                "versions",
                version["version_id"],
                {**version, "source_commit": source["items"][0]["source_commit"]},
                expected_revision=tx.revision("versions", version["version_id"]),
            )
    queue(
        rt,
        context(
            request_target=dict(kind="new_item", item_kind="asset", intent_id="asset-intent"),
            reference_versions=[dict(item_id=old["item_id"], version_id=old["version_id"])],
            execution_scope="default",
        ),
    )
    # A local source checkpoint and ignored runtime directories are required by recovery.
    (rt.root / "project.json").write_bytes(
        canonical(
            dict(
                schema_version="1.0",
                project_id=PROJECT,
                repository_id="repository-one",
                name="Isolated UAT",
                paths={
                    name: name for name in ("brief", "assets", "scenes", "storyboards", "sequences")
                },
            )
        )
    )
    subprocess.run(["git", "-C", str(rt.root), "add", "project.json"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(rt.root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--quiet",
            "-m",
            "Project identity fixture",
        ],
        check=True,
    )
    (rt.root / ".git/info/exclude").write_text(".proscenium/\n")
    prepared, kind = invoke(
        rt,
        "prepare_image_generation",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            generation_task_id=rt.task_id,
            operation_id="prepare-backup",
            prompt="Backup synthetic concept",
            seed="42",
        ),
        "prepare-backup",
    )
    assert kind != "failure", prepared
    rt.native.emit("turn-start", "assistant.turn_start", "root")
    rt.native.emit(
        "reply",
        "assistant.message",
        "turn-start",
        messageId="reply",
        turnId="turn",
        content="Prepared only, no inference",
    )
    rt.native.emit("turn-end", "assistant.turn_end", "reply")
    rt.native.emit("idle", "session.idle", "turn-end")
    asyncio.run(rt.copilot.settle(PROJECT, rt.native.session))
    rt.services.registry.assert_settled(PROJECT)
    history = rt.services.settings.data_directory / "copilot-runtime/home/session-state" / SESSION
    history.mkdir(parents=True)
    (history / "events.jsonl").write_text("")
    requests = rt.client.get(f"/api/v1/projects/{PROJECT}/requests", params={"limit": 1}).json()
    assert requests["items"][0]["receipt"]["state"] == "completed"
    old_cursor = rt.services.cursors.encode(f"{PROJECT}:requests", "R1")
    assert (
        rt.client.get(f"/api/v1/projects/{PROJECT}/requests", params={"cursor": old_cursor}).json()[
            "error"
        ]["code"]
        == "invalid_cursor"
    )
    retained = rt.client.get(f"/api/v1/projects/{PROJECT}/artifacts").json()
    assert len(retained["items"]) == 2 and all(
        row["attribution_status"] == "verified" for row in retained["items"]
    )
    bundle = tmp_path / "backup"
    assert not backup(rt.services.settings, PROJECT, bundle)["limits"]
    assert verify(bundle)["format"] == "proscenium-private-backup-1"
    restored = tmp_path / "restored"
    restore(bundle, restored, rebind=True)
    restored_registry = Registry(Store(restored / "machine/registry.sqlite3"))
    restored_registry.assert_settled(PROJECT)
    databases = list(restored.rglob("state.sqlite3"))
    assert len(databases) == 1
    restored_store = Store(databases[0], PROJECT)
    with (
        rt.store.transaction(write=False) as source,
        restored_store.transaction(write=False) as target,
    ):
        for namespace in (
            "contexts",
            "requests",
            "copilot.tool_outcomes",
            "providers.prepared",
            "artifacts",
        ):
            assert source.all(namespace) and source.all(namespace) == target.all(namespace)


def test_batch_two_klein_tool_loop_checkpoint_publication_and_private_recovery(
    tool_runtime, tmp_path, monkeypatch
):
    """Drive real tool authority, provider pump, closure and recovery with mock HTTP only."""
    worker_fixtures.offline.__wrapped__(monkeypatch)
    rt = tool_runtime
    artifacts = rt.services.extension("artifacts")
    rt.services.resources = Resources(rt.store)
    rt.provider.options["image_recipes"] = {
        "comfy": {
            **worker_fixtures.RECIPE,
            "width": 512,
            "height": 512,
            "steps": 4,
            "max_pixels": 1_048_576,
        }
    }
    add_tiled_nodes(rt.catalog)
    add_vae_dtype_node(rt.catalog)
    reference = artifacts.promote(
        PROJECT,
        "wide-reference",
        raster("solid", (1600, 900)),
        kind="image",
        purpose="reference",
        media_type="image/png",
    )
    with rt.store.transaction() as tx:
        artifacts.put(tx, reference)
    assert reference["dimensions"] == {"width": 1600, "height": 900}
    provider_calls = []
    attempts = []

    def transport(request):
        provider_calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/object_info":
            return httpx.Response(200, json=rt.catalog)
        if path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if path == "/system_stats":
            return httpx.Response(200, json={"devices": [{"vram_free": 2**30}]})
        if path == "/upload/image":
            assert request.method == "POST"
            return httpx.Response(200, json={"name": "reference.png", "subfolder": "uat"})
        if path == "/prompt":
            assert request.method == "POST"
            graph = json.loads(request.content)["prompt"]
            assert graph["reference_scale"]["inputs"]["resolution_steps"] == 16
            assert graph["reference_scale"]["inputs"]["megapixels"] == 1.0
            assert graph["vae_dtype"] == {
                "class_type": "VAEComputeDtype",
                "inputs": {"vae": ["vae", 0], "dtype": "bf16"},
            }
            assert graph["reference_encode"]["class_type"] == "VAEEncode"
            assert graph["decode"]["class_type"] == "VAEDecode"
            assert graph["reference_encode"]["inputs"]["vae"] == ["vae_dtype", 0]
            assert graph["decode"]["inputs"]["vae"] == ["vae_dtype", 0]
            attempts.append(f"prompt-{len(attempts) + 1}")
            return httpx.Response(200, json={"prompt_id": attempts[-1]})
        if path.startswith("/history/prompt-"):
            return httpx.Response(
                200,
                json={
                    path.rsplit("/", 1)[1]: {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {"save": {"images": [{"filename": "generated.png"}]}},
                    }
                },
            )
        if path == "/view":
            return httpx.Response(
                200, content=raster("scanline" if len(attempts) == 1 else "gradient", (512, 512))
            )
        raise AssertionError(f"Unexpected provider request: {request.method} {path}")

    rt.provider.client.close()
    rt.provider.client = httpx.Client(transport=httpx.MockTransport(transport))
    rt.provider.registrar = lambda caller, body: register_provider_artifact(
        rt.services, caller, body
    )

    def call(name, body, identity):
        result, kind = worker_fixtures.invoke(rt, name, body, identity)
        assert kind == "success", result
        return result

    capabilities = call("get_project_capabilities", {}, "capabilities")
    provider = next(row for row in capabilities["providers"] if row["provider_id"] == "comfy")
    assert provider["default_dimensions"] == {"width": 512, "height": 512}
    assert provider["default_steps"] == 4 and provider["max_pixels"] == 1_048_576
    generated = []
    for number in (1, 2):
        prepared = call(
            "prepare_image_generation",
            dict(
                schema_version="1.0",
                operation_id=f"prepare-{number}",
                prompt="Synthetic retained concept",
                seed="42",
                reference_artifact_ids=[reference["artifact_id"]],
            ),
            f"prepare-{number}",
        )
        assert prepared["project_id"] == PROJECT
        assert prepared["dimensions"] == {"width": 512, "height": 512}
        submission = prepared["submit_request"]
        assert submission["project_id"] == PROJECT
        assert submission["generation_task_id"] == worker_fixtures.TASK
        graph = json.loads(
            artifacts.ready_path(PROJECT, prepared["workflow_artifact_id"]).read_bytes()
        )
        assert graph["reference_scale"]["class_type"] == "ImageScaleToTotalPixels"
        assert graph["reference_scale"]["inputs"]["resolution_steps"] == 16
        assert graph["reference_scale"]["inputs"]["megapixels"] == 1.0
        assert graph["sigmas"]["inputs"]["steps"] == 4
        admitted = call("submit_generation", submission, f"submit-{number}")
        assert admitted["attempt_number"] == 1  # New job, same shared task/target budget.
        if number == 1:
            settled = rt.provider.pump(PROJECT, admitted["job_id"])[0]
            assert settled["state"] == "failed"
            assert settled["error_code"] == "corrupt_provider_output"
            assert settled["artifact_ids"] == []
            with rt.store.transaction(write=False) as tx:
                assert not [
                    a
                    for a in tx.all("artifacts")
                    if a["sha256"] == sha(raster("scanline", (512, 512)))
                ]
        else:
            invocation = worker_fixtures.invocation(
                rt, "get_job", {"job_id": admitted["job_id"], "wait_seconds": 5}, "wait-clean"
            )

            async def wait_and_pump():
                async def pump():
                    await asyncio.sleep(0.01)
                    return await asyncio.to_thread(rt.provider.pump, PROJECT, admitted["job_id"])

                pending = asyncio.create_task(pump())
                result = await rt.tools.invoke("get_job", invocation)
                await pending
                return result

            result = asyncio.run(wait_and_pump())
            assert result.result_type == "success"
            settled = json.loads(result.text_result_for_llm)
            assert settled["state"] == "succeeded" and settled["error_code"] is None
            generated = [
                identity
                for identity in settled["artifact_ids"]
                if artifacts.get(PROJECT, identity)["kind"] == "image"
            ]
    assert len(attempts) == 2 and len(generated) == 1
    image_call = worker_fixtures.invocation(
        rt, "get_artifact", {"artifact_id": generated[0], "include_image": True}, "inspect-clean"
    )
    private = rt.root / ".proscenium"
    before = {str(path.relative_to(private)): path.is_symlink() for path in private.rglob("*")}
    result = asyncio.run(rt.tools.invoke("get_artifact", image_call))
    assert result.result_type == "success"
    (binary,) = result.binary_results_for_llm
    assert isinstance(binary, ToolBinaryResult)
    assert binary.type == "image" and binary.mime_type == "image/png"
    assert base64.b64decode(binary.data, validate=True) == raster("gradient", (512, 512))
    assert {
        str(path.relative_to(private)): path.is_symlink() for path in private.rglob("*")
    } == before
    assert not any(before.values())

    git(rt.root, "init", "-b", "main")
    git(rt.root, "config", "user.name", "Offline UAT fixture")
    git(rt.root, "config", "user.email", "fixture@example.invalid")
    (rt.root / ".git/info/exclude").write_text(".proscenium/\n")
    (rt.root / "project.json").write_bytes(
        canonical(
            dict(
                schema_version="1.0",
                project_id=PROJECT,
                repository_id="repository-one",
                name="Batch 2 recovery fixture",
                paths={
                    name: name for name in ("brief", "assets", "scenes", "storyboards", "sequences")
                },
            )
        )
    )
    git(rt.root, "add", "project.json")
    git(rt.root, "-c", "core.hooksPath=/dev/null", "commit", "-m", "Initial isolated project")
    entry = "storyboards/plan-one/plan.json"
    path = rt.root / entry
    path.parent.mkdir(parents=True)
    document = worker_fixtures.plan()
    document["shots"][0]["frames"][0].update(artifact_id=generated[0], state="available")
    path.write_bytes(canonical(document))
    sources = Sources(rt.services)
    rt.services.extensions["sources"] = sources
    rt.services.extension("catalogue").sources = sources
    validation = call(
        "validate_authored_document",
        {
            "schema_version": "1.0",
            "project_id": PROJECT,
            "document_kind": "authored_shot_plan",
            "path": entry,
        },
        "validate-plan",
    )
    assert validation["valid"], validation
    receipt = call(
        "checkpoint_sources",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            operation_id="checkpoint-plan",
            expected_head=git(rt.root, "rev-parse", "HEAD").decode().strip(),
            paths=[entry],
            entry_path=entry,
            message="Checkpoint Batch 2 storyboard",
        ),
        "checkpoint-plan",
    )
    closure = receipt["source_closure"]
    validate("SourceClosure", closure)
    assert closure["entry_path"] == entry and closure["digest"] == closure_digest(closure)
    published = call(
        "publish_outputs",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            publication_id="batch-two-publication",
            items=[
                dict(
                    item_id="storyboard",
                    version_id="board-v1",
                    kind="storyboard",
                    name="Batch 2 storyboard",
                    expected_current_version_id=None,
                    source_commit=receipt["source_commit"],
                    source=closure,
                    artifact_ids=generated,
                    preview_requests=[],
                )
            ],
        ),
        "publish-plan",
    )
    assert published["versions"] == [{"item_id": "storyboard", "version_id": "board-v1"}]
    with rt.store.transaction(write=False) as tx:
        assert tx.require("versions", "board-v1")["source"] == closure
        reservations = tx.connection.execute("SELECT state FROM budget_reservations").fetchall()
        assert [row[0] for row in reservations] == ["consumed", "consumed"]

    # Settle only our synthetic work and invocations, never a native/model process.
    with rt.store.transaction() as tx:
        for namespace in ("work", "invocations"):
            for record in tx.all(namespace):
                identity = record["work_id"] if namespace == "work" else record["invocation_id"]
                revision = tx.revision(namespace, identity)
                tx.put(
                    namespace,
                    identity,
                    {**record, "state": "completed", "state_revision": revision + 1},
                    expected_revision=revision,
                )
    registry = Registry(Store(rt.services.settings.data_directory / "registry.sqlite3"))
    registry.register(
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            repository_id="repository-one",
            name="Batch 2 recovery fixture",
            path=str(rt.root),
            native_session_id=SESSION,
            state="active",
            state_revision=0,
        )
    )
    registry.activate(PROJECT)
    registry.assert_settled(PROJECT)
    history = rt.services.settings.data_directory / "copilot-runtime/home/session-state" / SESSION
    history.mkdir(parents=True)
    (history / "events.jsonl").write_text("")
    manifest = backup(rt.services.settings, PROJECT, tmp_path / "batch-two-backup")
    assert not manifest["limits"]
    assert verify(tmp_path / "batch-two-backup")["format"] == "proscenium-private-backup-1"
    assert manifest["files"]
    assert provider_calls.count(("POST", "/prompt")) == 2
    rt.provider.client.close()
