"""Hermetic worker tools, immutable preparation and retained publication diagnostics."""

import asyncio
import base64
import copy
import hashlib
import importlib.util
import io
import json
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from copilot import CopilotClient
from copilot.generated.rpc import (
    ExternalToolTextResultForLlmBinaryResultsForLlm,
    ExternalToolTextResultForLlmBinaryResultsForLlmType,
)
from copilot.tools import (
    ToolBinaryResult,
    ToolInvocation,
    tool_result_to_external_tool_text_result_for_llm,
)
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.catalogue.artifacts import Artifacts
from proscenium.catalogue.service import Catalogue
from proscenium.configuration import Settings
from proscenium.contracts import canonical, validate
from proscenium.copilot.capabilities import (
    compare,
    get_configuration_status,
    get_project_capabilities,
    validate_authored_document,
)
from proscenium.copilot.native import Configuration
from proscenium.copilot.outcomes import get_tool_outcome, record_tool_outcome
from proscenium.copilot.service import Copilot
from proscenium.copilot.state import State, key
from proscenium.copilot.tools import Tools
from proscenium.db import Store
from proscenium.db.pagination import Cursors
from proscenium.db.registry import Registry
from proscenium.jobs import JobService
from proscenium.jobs.gate import ExecutionGate
from proscenium.previews.config import REVISION
from proscenium.previews.journal import UNSUPPORTED_BLENDER_FEATURE_REASON
from proscenium.providers import klein, sd_turbo, sd_turbo_fp16
from proscenium.providers.preparation import Preparation
from proscenium.providers.service import ProviderService
from proscenium.sources.resolver import closure_digest

ROOT = Path(__file__).resolve().parents[1]
PROJECT, WORK, TASK, SESSION = "project-one", "work-one", "task-one", "session-one"
RECIPE = dict(
    profile_revision="comfy-image-1",
    unet="flux-2-klein-4b.safetensors",
    clip="qwen_3_4b.safetensors",
    vae="flux2-vae.safetensors",
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Worker capability tests must remain offline")

    async def blocked_async(*args, **kwargs):
        blocked()

    monkeypatch.setattr(CopilotClient, "start", blocked_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)


def plan():
    return dict(
        schema_version="1.0",
        project_id=PROJECT,
        plan_id="plan-one",
        brief_artifact_ids=[],
        asset_dependencies=[],
        unresolved=[],
        shots=[
            dict(
                shot_id="shot-one",
                intent="Synthetic intent",
                camera_intent="Static",
                motion_beats=[],
                scene_version=None,
                source_range=None,
                fps=None,
                frames=[
                    dict(
                        frame_id="frame-one",
                        role="start",
                        composition="Wide",
                        artifact_id=None,
                        state="missing",
                    )
                ],
            )
        ],
    )


def catalog():
    """Synthetic Comfy node schemas. All requests still run the real preflight/binder."""
    graphs = [
        klein.graph(
            **{k: RECIPE[k] for k in ("unet", "clip", "vae")},
            width=512,
            height=512,
            reference_artifact_id="reference",
        ),
        sd_turbo.graph("reference"),
        sd_turbo_fp16.graph("reference"),
    ]
    result = {}
    choices = {
        "unet_name": [RECIPE["unet"]],
        "clip_name": [RECIPE["clip"]],
        "vae_name": [RECIPE["vae"]],
        "ckpt_name": [sd_turbo.FILENAME, sd_turbo_fp16.FILENAME],
        "upscale_method": ["nearest-exact", "bilinear", "area", "bicubic", "lanczos"],
    }
    for graph in graphs:
        for node in graph.values():
            inputs = {}
            for field, value in node["inputs"].items():
                if field in choices:
                    inputs[field] = [choices[field]]
                elif isinstance(value, list):
                    inputs[field] = ["*"]
                else:
                    inputs[field] = [
                        "INT"
                        if type(value) is int or value == "$seed"
                        else "FLOAT"
                        if type(value) is float
                        else "STRING"
                    ]
            result[node["class_type"]] = dict(
                input={"required": inputs},
                output=["*", "*", "*"],
                output_node=node["class_type"] == "SaveImage",
            )
    return result


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    store = Store(root / ".proscenium/state.sqlite3", PROJECT)
    project = dict(path=str(root), state="active", state_revision=0, repository_id="repository-one")
    gate = ExecutionGate()
    gate.resume(PROJECT, reconciliation_evidence="Offline fixture")
    registry = SimpleNamespace(
        project_store=lambda project_id: store,
        get=lambda project_id: project,
        active_id=lambda: PROJECT,
    )
    settings = Settings(
        data_directory=tmp_path / "machine",
        runtime={"worker_model": "gpt-6.1-sol"},
        free_space_reserve_bytes=0,
        services=(
            dict(
                provider_id="comfy",
                endpoint="http://comfy.invalid",
                capabilities=["image"],
                model_id=None,
                available=None,
            ),
            dict(
                provider_id="vlm",
                endpoint="http://vlm.invalid",
                capabilities=["vlm"],
                model_id=None,
                available=False,
            ),
        ),
    )
    services = SimpleNamespace(
        registry=registry,
        settings=settings,
        gate=gate,
        ownership=SimpleNamespace(epoch="epoch"),
        cursors=Cursors(b"x" * 32),
        extensions={},
    )
    services.extension = lambda name: services.extensions[name]
    services.jobs = JobService(registry, gate, settings, "epoch")
    artifacts = Artifacts(services)
    services.extensions["artifacts"] = artifacts
    calls = []
    response_catalog = catalog()

    def transport(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path in {"/object_info", "/v1/models"}
        assert request.extensions["timeout"]["read"] == 3
        return httpx.Response(
            200, json=response_catalog if request.url.path == "/object_info" else {"data": []}
        )

    client = httpx.Client(transport=httpx.MockTransport(transport))
    provider = ProviderService(services, client=client)
    services.extensions["providers"] = provider
    services.extensions["generation"] = SimpleNamespace(submit=provider.submit)
    services.extensions["image_preparation"] = Preparation(services, provider)
    services.extensions["catalogue"] = Catalogue(services, SimpleNamespace(), artifacts)
    with store.transaction() as tx:
        tx.put(
            "work",
            WORK,
            dict(
                schema_version="1.0",
                project_id=PROJECT,
                work_id=WORK,
                originating_request_id="request-one",
                directive_request_ids=[],
                state="running",
                state_revision=0,
                native_session_id=SESSION,
                job_ids=[],
                generation_task_id=TASK,
            ),
            schema="WorkRecord",
        )
        tx.put("copilot.effective", "request-one", {"generation_attempt_limit": 3})
        tx.put("requests", "request-one", dict(project_id=PROJECT, submission={"context_id": None}))
    state = SimpleNamespace(control=lambda tx: {"fenced": False})
    tools = Tools(services, state, PROJECT)
    yield SimpleNamespace(
        services=services,
        store=store,
        root=root,
        provider=provider,
        catalog=response_catalog,
        calls=calls,
        tools=tools,
        state=state,
    )
    client.close()


def invocation(runtime, name, body, identity="call-one"):
    invocation_id = key(SESSION, "tool", identity)
    with runtime.store.transaction() as tx:
        if not tx.get("invocations", invocation_id, required=False):
            tx.put(
                "invocations",
                invocation_id,
                dict(
                    invocation_id=invocation_id,
                    agent_id=None,
                    session_id=SESSION,
                    name=name,
                    arguments_sha256=hashlib.sha256(canonical(body)).hexdigest(),
                    request_id="request-one",
                    work_id=WORK,
                    owner_epoch="epoch",
                    state="running",
                ),
            )
    return ToolInvocation(
        session_id=SESSION, tool_call_id=identity, tool_name=name, arguments=body, agent_id=None
    )


def invoke(runtime, name, body, identity="call-one"):
    result = asyncio.run(runtime.tools.invoke(name, invocation(runtime, name, body, identity)))
    return json.loads(result.text_result_for_llm), result.result_type


def validate_plan(runtime, **changes):
    return validate_authored_document(
        runtime.services,
        PROJECT,
        {
            **dict(
                schema_version="1.0",
                project_id=PROJECT,
                document_kind="authored_shot_plan",
                document=plan(),
            ),
            **changes,
        },
    )


def test_owner_status_shares_bounded_live_provider_probes(runtime, tmp_path):
    services = runtime.services
    editor = tmp_path / "Unity"
    editor.touch()
    services.settings = replace(
        services.settings,
        execution_profiles=frozenset({"blender-cpu", "unity-local"}),
        runtime={
            "worker_model": "fixture",
            "unity_editor": str(editor),
            "mcp_servers": {
                "blender": {},
                "assets": {"type": "http", "url": "https://blendswap.com/api/mcp"},
            },
        },
        services=(
            services.settings.services[0],
            {**services.settings.services[1], "available": True, "model_id": "qwen/qwen3.8-27b"},
        ),
        provider_options={"image_recipes": {"comfy": RECIPE}},
    )
    runtime.provider.options = services.settings.provider_options
    calls = []

    def transport(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.extensions["timeout"]["read"] == 3
        return httpx.Response(
            200,
            json=runtime.catalog
            if request.url.path == "/object_info"
            else {"data": [{"id": "qwen/qwen3.8-27b"}]},
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        runtime.provider.client = client
        status = get_configuration_status(services)
        assert status["rendering_ready"] and status["blender_mcp_configured"]
        assert status["blendswap_mcp_configured"] and status["unity_configured"]
        assert status["providers"][0]["status"] == "ready"
        assert RECIPE["unet"] in status["providers"][0]["selected_models"]
        assert status["providers"][1]["status"] == "configured"
        assert get_project_capabilities(services, PROJECT)["providers"] == status["providers"]
        assert len(calls) == 4
        services.settings = replace(services.settings, runtime={})
        assert get_configuration_status(services)["blender_mcp_configured"] is False


def test_configuration_and_recipe_validation(tmp_path):
    settings = Settings.load(ROOT / "config.example.json")
    assert "skill" in Configuration.load(settings.runtime).worker_tools
    assert Settings(provider_options={}).provider_options == {}
    assert Settings(provider_options={"image_recipes": {"comfy": RECIPE}})
    for recipe in (
        {**RECIPE, "profile_revision": sd_turbo.REVISION},
        {**RECIPE, "width": True},
        {**RECIPE, "height": 513},
        {**RECIPE, "unet": None},
        {**RECIPE, "steps": 0},
        {**RECIPE, "extra": "value"},
    ):
        with pytest.raises((ValueError, DomainError)):
            Settings(provider_options={"image_recipes": {"comfy": recipe}})
    with pytest.raises(ValueError):
        Settings(
            provider_options={
                "image_profiles": {"comfy": sd_turbo.REVISION},
                "image_recipes": {
                    "comfy": {
                        "profile_revision": sd_turbo.REVISION,
                        "checkpoint": "wrong.safetensors",
                    }
                },
            }
        )


@pytest.mark.parametrize("blender", [False, True])
@pytest.mark.parametrize("token", [None, "synthetic-token"])
def test_capabilities_distinguish_blender_from_optional_blendswap(
    runtime, monkeypatch, blender, token
):
    monkeypatch.delenv("BLENDSWAP_KEY", raising=False)
    if token:
        monkeypatch.setenv("BLENDSWAP_KEY", token)
    servers = {
        "blendswap": {
            "type": "http",
            "url": "https://blendswap.com/api/mcp",
            "bearer_token_env": "BLENDSWAP_KEY",
        }
    }
    if blender:
        servers["blender"] = {"type": "stdio", "command": "uvx", "args": ["mcp-for-blender==2.3.0"]}
    runtime.services.settings = replace(
        runtime.services.settings,
        services=(),
        runtime={"worker_model": "fixture", "mcp_servers": servers},
    )
    capabilities = get_project_capabilities(runtime.services, PROJECT)
    assert capabilities["blender_mcp_configured"] is blender
    assert "synthetic-token" not in json.dumps(capabilities)
    assert runtime.calls == []


def test_capabilities_skills_override_ready_ambiguous_unavailable(runtime):
    runtime.services.settings.runtime["worker_tools"] = ["view"]
    result, _ = invoke(runtime, "get_project_capabilities", {})
    assert result["exposed_tools"] == ["view"] and not result["skill_loader_exposed"]
    assert "skill" in result["missing_worker_tools"] and "storyboard" in result["available_skills"]
    assert "hero-assets" in result["available_skills"]
    assert runtime.services.settings.runtime["worker_tools"] == ["view"]
    assert result["providers"][0]["status"] == "ready"
    assert result["providers"][1]["reason_code"] == "configured_unavailable"
    runtime.catalog["UNETLoader"]["input"]["required"]["unet_name"][0].append("second.safetensors")
    result = get_project_capabilities(runtime.services, PROJECT)
    assert result["providers"][0]["status"] == "ambiguous"
    runtime.provider.options["image_recipes"] = {"comfy": RECIPE}
    assert get_project_capabilities(runtime.services, PROJECT)["providers"][0][
        "selected_models"
    ] == [RECIPE[k] for k in ("unet", "clip", "vae")]
    del runtime.catalog["VAELoader"]
    assert (
        get_project_capabilities(runtime.services, PROJECT)["providers"][0]["status"]
        == "unavailable"
    )
    runtime.services.settings.services[1]["available"] = None
    assert (
        get_project_capabilities(runtime.services, PROJECT)["providers"][1]["reason_code"]
        == "model_not_configured"
    )


def test_capabilities_transport_failure_and_model_listing(runtime):
    runtime.provider.client.close()
    runtime.provider.client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(503))
    )
    assert (
        get_project_capabilities(runtime.services, PROJECT)["providers"][0]["reason_code"]
        == "provider_unreachable"
    )
    runtime.services.settings.services[1].update(available=None, model_id="vision")
    runtime.provider.client.close()
    runtime.provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"data": [{"id": "vision"}]})
        )
    )
    vlm = get_project_capabilities(runtime.services, PROJECT)["providers"][1]
    assert vlm["status"] == "configured" and "does not prove vision inference" in vlm["summary"]
    runtime.provider.client.close()


def test_validation_json_schema_and_no_materialisation(runtime):
    assert validate_plan(runtime)["valid"]
    path = runtime.root / "index.html"
    path.write_text("<html>not a plan</html>")
    result, _ = invoke(
        runtime,
        "validate_authored_document",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            document_kind="authored_shot_plan",
            path="index.html",
        ),
    )
    assert (
        not result["schema_valid"]
        and result["issues"][0]["message"] == "Storyboard source must be AuthoredShotPlan JSON"
    )
    document = plan()
    document["shots"][0]["camera_intent"] = {"secret": "never-echo-this"}
    result = validate_plan(runtime, document=document)
    assert result["issues"][0]["path"] == "shots[0].camera_intent"
    assert "never-echo-this" not in json.dumps(result) and len(result["issues"]) <= 25
    assert not (runtime.root / ".proscenium/snapshots").exists()
    with runtime.store.transaction(write=False) as tx:
        assert tx.all("jobs") == []


def test_validation_impact_matches_canonical_helper(runtime):
    before = plan()
    after = copy.deepcopy(before)
    after["shots"][0]["intent"] = "Changed"
    (runtime.root / "before.json").write_bytes(canonical(before))
    result = validate_plan(
        runtime, document=after, compare_to_path="before.json", changed_ids=["frame-one"]
    )
    assert result["valid"] and result["impact"] == compare(before, after)
    removed = {**after, "shots": []}
    assert compare(before, removed)["removed_frame_ids"] == ["frame-one"]
    assert validate_plan(runtime, changed_ids=["frame-one"])["impact"]["affected_shot_ids"] == [
        "shot-one"
    ]
    spec = importlib.util.spec_from_file_location(
        "canonical_plan_tools", ROOT / "agent/skills/storyboard/plan_tools.py"
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    assert compare(before, after) == helper.compare(before, after)
    assert compare(before, removed) == helper.compare(before, removed)


@pytest.mark.parametrize(
    "mutation", ["duplicate_frame", "roles", "fps", "range", "dependency", "image"]
)
def test_validation_semantic_failures(runtime, mutation):
    document = plan()
    shot = document["shots"][0]
    if mutation == "duplicate_frame":
        shot["frames"].append(copy.deepcopy(shot["frames"][0]))
    elif mutation == "roles":
        shot["frames"][0]["role"] = "end"
        shot["frames"].append({**shot["frames"][0], "frame_id": "frame-two", "role": "start"})
    elif mutation == "fps":
        shot["fps"] = {"numerator": 48, "denominator": 2}
    elif mutation == "range":
        shot["source_range"] = {"start": 10, "end": 1}
    elif mutation == "dependency":
        document["asset_dependencies"] = [{"item_id": "missing", "version_id": "missing"}]
    else:
        shot["frames"][0].update(artifact_id="missing", state="available")
    result = validate_plan(runtime, document=document)
    assert not result["valid"] and result["issues"]


@pytest.mark.parametrize("path", [".env", ".proscenium/state.sqlite3", "link.json", "large.json"])
def test_validation_path_boundaries(runtime, path):
    (runtime.root / ".env").write_text("SECRET=not-readable")
    (runtime.root / "link.json").symlink_to(runtime.root / ".env")
    (runtime.root / "large.json").write_bytes(b" " * (2 * 1024**2 + 1))
    result = validate_authored_document(
        runtime.services,
        PROJECT,
        dict(
            schema_version="1.0", project_id=PROJECT, document_kind="authored_shot_plan", path=path
        ),
    )
    assert not result["valid"] and "not-readable" not in json.dumps(result)


@pytest.mark.parametrize(
    "revision,reference",
    [
        ("comfy-image-1", False),
        ("comfy-image-1", True),
        (sd_turbo.REVISION, True),
        (sd_turbo_fp16.REVISION, True),
    ],
)
def test_preparation_submission_replay_budget_and_attribution(runtime, revision, reference):
    if revision != "comfy-image-1":
        runtime.provider.options["image_profiles"] = {"comfy": revision}
    image = io.BytesIO()
    Image.new("RGB", (512, 512), "blue").save(image, format="PNG")
    artifacts = runtime.services.extension("artifacts")
    artifact = artifacts.promote(
        PROJECT,
        "reference",
        image.getvalue(),
        kind="image",
        purpose="reference",
        media_type="image/png",
    )
    with runtime.store.transaction() as tx:
        artifacts.put(tx, artifact)
    body = dict(
        schema_version="1.0",
        operation_id="prepare-one",
        project_id=PROJECT,
        generation_task_id=TASK,
        prompt="Synthetic concept",
        seed="42",
        reference_artifact_ids=["reference"] if reference else [],
    )
    result, result_type = invoke(runtime, "prepare_image_generation", body)
    assert result_type != "failure", result
    assert result["profile_revision"] == revision and result["dimensions"] == {
        "width": 512,
        "height": 512,
    }
    with runtime.store.transaction(write=False) as tx:
        assert tx.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
        assert tx.all("jobs") == []
        assert (
            tx.connection.execute(
                "SELECT COUNT(*) FROM operations WHERE scope='providers.prepare_image'"
            ).fetchone()[0]
            == 1
        )
        outcome = get_tool_outcome(tx, key(SESSION, "tool", "call-one"))
        assert outcome["artifact_ids"] == sorted(
            [result["workflow_artifact_id"], result["prompt_artifact_id"]]
        )
    for identity in [result["workflow_artifact_id"], result["prompt_artifact_id"]]:
        assert artifacts.ready_path(PROJECT, identity).is_relative_to(
            runtime.root / ".proscenium/artefacts/sha256"
        )
    runtime.state.control = lambda tx: {"fenced": True}
    runtime.services.gate.close(PROJECT)
    previous_calls = len(runtime.calls)
    runtime.provider.options["image_recipes"] = {"comfy": {"profile_revision": "changed"}}
    replayed, _ = invoke(runtime, "prepare_image_generation", body)
    assert replayed == {**result, "replayed": True} and len(runtime.calls) == previous_calls
    conflict, _ = invoke(
        runtime, "prepare_image_generation", {**body, "prompt": "Different"}, "new-call"
    )
    assert conflict["error"] == "idempotency_conflict"
    runtime.state.control = lambda tx: {"fenced": False}
    runtime.services.gate.resume(PROJECT, reconciliation_evidence="Offline fixture")
    runtime.provider.options["image_profiles"] = {"comfy": "comfy-image-1"}
    job, result_type = invoke(runtime, "submit_generation", result["submit_request"], "submit-one")
    assert result_type != "failure", job
    with runtime.store.transaction(write=False) as tx:
        assert tx.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1
    again, _ = invoke(runtime, "submit_generation", result["submit_request"], "submit-one")
    assert again == job and len(runtime.calls) == previous_calls
    page = artifacts.list_retained(PROJECT, limit=1, kind="document", purpose="metadata")
    assert page["items"][0]["attribution_status"] == "verified" and page["next_cursor"]
    page2 = artifacts.list_retained(
        PROJECT, cursor=page["next_cursor"], limit=1, kind="document", purpose="metadata"
    )
    assert page2["items"][0]["artifact"] != page["items"][0]["artifact"]
    legacy = artifacts.list_retained(PROJECT, kind="image")["items"][0]
    assert legacy["attribution_status"] == "unrecorded" and legacy["request_id"] is None
    with pytest.raises(DomainError):
        artifacts.list_retained(PROJECT, kind="unknown")


def publication(runtime, data):
    root = runtime.root
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / "source.json").write_bytes(data)
    subprocess.run(["git", "-C", str(root), "add", "source.json"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        check=True,
    )
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"]).decode().strip()
    from proscenium.sources.common import sha

    closure = dict(
        entry_path="source.json",
        files=[
            dict(
                destination="source.json",
                origin=dict(
                    kind="git",
                    repository_id="repository-one",
                    commit=commit,
                    path="source.json",
                    sha256=sha(data),
                ),
            )
        ],
        dependencies=[],
        required_runtime=dict(application="proscenium", version="0.1.0", features=[]),
    )
    closure["digest"] = closure_digest(closure)
    runtime.services.extension("catalogue").sources = SimpleNamespace(
        resolve=lambda *args, **kwargs: root
    )
    return dict(
        schema_version="1.0",
        project_id=PROJECT,
        publication_id="publication-one",
        items=[
            dict(
                item_id="storyboard",
                version_id="version-one",
                kind="storyboard",
                name="Synthetic storyboard",
                expected_current_version_id=None,
                source_commit=commit,
                source=closure,
                artifact_ids=[],
                preview_requests=[],
                derived_from=[],
            )
        ],
    )


@pytest.mark.parametrize(
    "data,message",
    [
        (b"<html>not a plan</html>", "Storyboard source must be AuthoredShotPlan JSON"),
        (
            canonical({"schema_version": "1.0"}),
            "Storyboard source failed AuthoredShotPlan validation",
        ),
    ],
)
def test_publication_failure_trusted_outcomes(runtime, data, message):
    body = publication(runtime, data)
    result, result_type = invoke(runtime, "publish_outputs", body)
    assert result_type == "failure" and result["message"] == message
    with runtime.store.transaction(write=False) as tx:
        outcome = get_tool_outcome(tx, key(SESSION, "tool", "call-one"))
        assert outcome["status"] == "failed" and outcome["safe_summary"] == message
        if "failed" in message:
            assert outcome["issues"] and outcome["issues"][0]["path"] != "$"
            assert any(
                issue["message"] == "Missing required field plan_id" for issue in outcome["issues"]
            )
        assert not tx.all("versions") and not tx.all("jobs")


def test_publish_outputs_rejects_inline_preview_for_unsupported_blender_features(runtime):
    body = publication(runtime, b"synthetic scene")
    item = body["items"][0]
    item.update(kind="scene", item_id="scene-one", name="Synthetic scene")
    source = item["source"]
    source["required_runtime"] = dict(
        application=" Blender ", version="4.5.16 LTS", features=["optix"]
    )
    source["digest"] = closure_digest(source)
    item["preview_requests"] = [
        dict(
            intent_id="proxy-one",
            shot_id=None,
            purpose="proxy",
            source_closure_digest=source["digest"],
            scene_name="Scene",
            camera=None,
            frames={"start": 1, "end": 1},
            fps={"numerator": 24, "denominator": 1},
            dimensions={"height": 512, "width": 512},
            engine="CYCLES",
            device="CPU",
            samples=8,
            colour_management={"view_transform": "AgX", "look": "None", "exposure": 0, "gamma": 1},
            renderer_revision=REVISION,
        )
    ]
    call = invocation(runtime, "publish_outputs", body)
    catalogue = runtime.services.extension("catalogue")
    with pytest.raises(DomainError) as error:
        catalogue.publish(runtime.tools.caller(call, name="publish_outputs"), body)
    assert (error.value.code, error.value.status, error.value.message) == (
        "capability_unavailable",
        501,
        UNSUPPORTED_BLENDER_FEATURE_REASON,
    )
    result, kind = invoke(runtime, "publish_outputs", body)
    assert kind == "failure"
    assert result["error"] == "capability_unavailable"
    assert result["message"] == UNSUPPORTED_BLENDER_FEATURE_REASON
    with runtime.store.transaction(write=False) as tx:
        for namespace in ("publications", "items", "versions", "jobs", "preview_intents"):
            assert not tx.all(namespace), namespace


def test_publication_guidance_keeps_schema_keys_without_echoing_private_input(runtime):
    from proscenium.copilot.tools import tool_failure

    document = plan()
    document["password-private-field"] = "never-echo-this-value"
    body = publication(runtime, canonical(document))
    result, kind = invoke(runtime, "publish_outputs", body)
    assert kind == "failure"
    issues = result["details"]["issues"]
    assert any(
        issue["code"] == "additionalProperties"
        and issue["message"].startswith("Unexpected property. Allowed keys: ")
        and "plan_id" in issue["message"]
        for issue in issues
    )
    assert "password-private-field" not in json.dumps(result)
    assert "never-echo-this-value" not in json.dumps(result)
    forged = DomainError(
        "invalid_request",
        issues=[
            {
                "phase": "schema",
                "path": "$",
                "code": "additionalProperties",
                "message": "Unexpected property. Allowed keys: password-private-field",
            }
        ],
    )
    refused = tool_failure(forged, "publish_outputs")
    assert "password-private-field" not in refused.text_result_for_llm


def test_publication_preflight_reads_git_without_snapshots(runtime):
    body = publication(runtime, canonical(plan()))
    result = validate_authored_document(
        runtime.services,
        PROJECT,
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            document_kind="publication_submit",
            document=body,
        ),
    )
    assert result["valid"], result
    assert not (runtime.root / ".proscenium/snapshots").exists()
    body["items"][0]["expected_current_version_id"] = "wrong"
    assert not validate_authored_document(
        runtime.services,
        PROJECT,
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            document_kind="publication_submit",
            document=body,
        ),
    )["valid"]


@pytest.mark.parametrize("checkpoint", [False, True])
@pytest.mark.parametrize(
    "features,supported",
    [(["optix", "secret-custom-tag"], False), (["native-mesh", "cycles-cpu"], True)],
)
def test_publication_preflight_checks_resolved_blender_features(
    runtime, checkpoint, features, supported
):
    body = publication(runtime, canonical(plan()))
    item = body["items"][0]
    closure = item["source"]
    closure["required_runtime"] = dict(
        application=" Blender ", version="4.5.16 LTS", features=features
    )
    closure["digest"] = closure_digest(closure)
    if checkpoint:
        item["checkpoint_operation_id"] = "checkpoint-one"
        del item["source"]
        with runtime.store.transaction() as tx:
            tx.put(
                "checkpoint_intents",
                item["checkpoint_operation_id"],
                dict(
                    state="completed",
                    receipt=dict(source_commit=item["source_commit"], source_closure=closure),
                ),
            )

    result, kind = invoke(
        runtime,
        "validate_authored_document",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            document_kind="publication_submit",
            document=body,
        ),
    )
    assert kind == "success", result
    validate("AuthoredDocumentValidationResult", result)
    assert (
        result["schema_valid"]
        and result["semantic_valid"] is supported
        and result["valid"] is supported
    )
    assert result["issues"] == (
        []
        if supported
        else [
            dict(
                phase="preflight",
                code="capability_unavailable",
                path="items[0].source.required_runtime.features",
                message=UNSUPPORTED_BLENDER_FEATURE_REASON,
            )
        ]
    )
    assert "secret-custom-tag" not in json.dumps(result)
    assert not (runtime.root / ".proscenium/snapshots").exists()
    with runtime.store.transaction(write=False) as tx:
        assert not tx.all("versions") and not tx.all("jobs")


def test_publication_preflight_caps_blender_feature_issues(runtime):
    body = publication(runtime, canonical(plan()))
    item = body["items"][0]
    closure = item["source"]
    closure["required_runtime"] = dict(
        application="blender", version="4.5.16 LTS", features=["secret-custom-tag"]
    )
    closure["digest"] = closure_digest(closure)
    body["items"] = [dict(item, item_id=f"item-{i}", version_id=f"version-{i}") for i in range(26)]
    result, kind = invoke(
        runtime,
        "validate_authored_document",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            document_kind="publication_submit",
            document=body,
        ),
    )
    assert kind == "success" and result["schema_valid"] and not result["valid"]
    validate("AuthoredDocumentValidationResult", result)
    assert result["issues"] == [
        dict(
            phase="preflight",
            code="capability_unavailable",
            path=f"items[{index}].source.required_runtime.features",
            message=UNSUPPORTED_BLENDER_FEATURE_REASON,
        )
        for index in range(25)
    ]
    assert "secret-custom-tag" not in json.dumps(result)


def test_sequence_validation_and_invalid_schema_bounds(runtime):
    with runtime.store.transaction() as tx:
        tx.put(
            "versions",
            "scene-version",
            dict(project_id=PROJECT, version_id="scene-version", item_id="scene", kind="scene"),
        )
        tx.put(
            "jobs",
            "render-job",
            dict(
                project_id=PROJECT,
                job_id="render-job",
                kind="render",
                target={
                    "version": {"item_id": "scene", "version_id": "scene-version"},
                    "shot_id": "shot-one",
                },
                render_spec={
                    "frames": {"start": 1, "end": 1},
                    "fps": {"numerator": 24, "denominator": 1},
                },
            ),
        )
    sequence = dict(
        schema_version="1.0",
        project_id=PROJECT,
        sequence_version_id="sequence-version",
        fps={"numerator": 24, "denominator": 1},
        frame_count=1,
        silent=True,
        entries=[
            dict(
                entry_id="entry-one",
                shot_id="shot-one",
                version_id="scene-version",
                job_id="render-job",
                source_frames={"start": 1, "end": 1},
                sequence_frames={"start": 1, "end": 1},
                transform="identity",
                mapping_artifact_id=None,
            )
        ],
    )
    request = dict(
        schema_version="1.0", project_id=PROJECT, document_kind="sequence_map", document=sequence
    )
    assert validate_authored_document(runtime.services, PROJECT, request)["valid"]
    sequence["frame_count"] = 2
    result = validate_authored_document(runtime.services, PROJECT, request)
    assert result["schema_valid"] and not result["semantic_valid"]
    document = plan()
    document["shots"] = [
        {"shot_id": f"shot-{i}", "intent": {"private": "never-echo"}} for i in range(50)
    ]
    result = validate_plan(runtime, document=document)
    assert len(result["issues"]) == 25 and "never-echo" not in json.dumps(result)
    assert len({(i["path"], i["code"]) for i in result["issues"]}) == 25


def test_successful_publication_outcome_and_retained_artifact(runtime):
    artifacts = runtime.services.extension("artifacts")
    artifact = artifacts.promote(
        PROJECT,
        "published-document",
        b"metadata",
        kind="document",
        purpose="metadata",
        media_type="text/plain",
    )
    with runtime.store.transaction() as tx:
        artifacts.put(tx, artifact)
    body = publication(runtime, canonical(plan()))
    body["items"][0]["artifact_ids"] = ["published-document"]
    result, kind = invoke(runtime, "publish_outputs", body)
    assert kind != "failure", result
    page = artifacts.list_retained(PROJECT)
    assert page["items"][0]["published_versions"] == result["versions"]
    assert page["items"][0]["attribution_status"] == "verified"
    with runtime.store.transaction(write=False) as tx:
        outcome = get_tool_outcome(tx, key(SESSION, "tool", "call-one"))
        assert outcome["publication_id"] == result["publication_id"] and outcome[
            "artifact_ids"
        ] == ["published-document"]


def test_preparation_backup_and_verify(runtime, tmp_path):
    from tools.operations.recovery import backup, verify

    publication(runtime, canonical(plan()))
    (runtime.root / ".git/info/exclude").write_text(".proscenium/\n")
    result, kind = invoke(
        runtime,
        "prepare_image_generation",
        dict(
            schema_version="1.0",
            operation_id="prepare-backup",
            project_id=PROJECT,
            generation_task_id=TASK,
            prompt="Backup fixture",
            seed="42",
        ),
    )
    assert kind != "failure", result
    machine = runtime.services.settings.data_directory
    registry = Registry(Store(machine / "registry.sqlite3"))
    registry.register(
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            repository_id="repository-one",
            name="Offline fixture",
            path=str(runtime.root),
            native_session_id=SESSION,
            state="active",
            state_revision=0,
        )
    )
    registry.activate(PROJECT)
    # Synthetic settled native history, not a native process or inference request.
    history = machine / "copilot-runtime/home/session-state" / SESSION
    history.mkdir(parents=True)
    (history / "events.jsonl").write_text("")
    with runtime.store.transaction() as tx:
        work = tx.require("work", WORK)
        tx.put(
            "work",
            WORK,
            {**work, "state": "completed", "state_revision": tx.revision("work", WORK) + 1},
            expected_revision=tx.revision("work", WORK),
        )
        identity = key(SESSION, "tool", "call-one")
        record = tx.require("invocations", identity)
        tx.put(
            "invocations",
            identity,
            {**record, "state": "completed"},
            expected_revision=tx.revision("invocations", identity),
        )
    registry.assert_settled(PROJECT)
    bundle = tmp_path / "backup"
    manifest = backup(runtime.services.settings, PROJECT, bundle)
    assert not manifest["limits"]
    assert verify(bundle)["format"] == "proscenium-private-backup-1"
    assert len(manifest["files"]) > 2


def test_retained_owner_route_pagination_and_filters(runtime, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from proscenium.api.dependencies import owner
    from proscenium.app import create_app
    from proscenium.auth import Caller
    from proscenium.catalogue.router import router

    app = FastAPI()
    app.state.services = runtime.services
    app.include_router(router)
    app.dependency_overrides[owner] = lambda: Caller("owner", "offline-owner")
    artifacts = runtime.services.extension("artifacts")
    with runtime.store.transaction() as tx:
        for identity in ("legacy-a", "legacy-b"):
            artifacts.put(
                tx,
                artifacts.promote(
                    PROJECT,
                    identity,
                    b"legacy metadata",
                    kind="document",
                    purpose="metadata",
                    media_type="text/plain",
                ),
            )
    with TestClient(app) as browser:
        base = f"/api/v1/projects/{PROJECT}/artifacts"
        page = browser.get(
            base, params={"limit": 1, "kind": "document", "purpose": "metadata"}
        ).json()
        assert page["items"][0]["attribution_status"] == "unrecorded"
        next_page = browser.get(
            base,
            params={
                "limit": 1,
                "kind": "document",
                "purpose": "metadata",
                "cursor": page["next_cursor"],
            },
        ).json()
        assert (
            next_page["items"][0]["artifact"]["artifact_id"]
            != page["items"][0]["artifact"]["artifact_id"]
        )
    settings = Settings(data_directory=tmp_path / "auth-machine")
    with TestClient(create_app(settings), base_url=settings.origin) as browser:
        assert browser.get(f"/api/v1/projects/{PROJECT}/artifacts").status_code == 401


@pytest.mark.parametrize(
    "case",
    [
        "ambiguous",
        "unavailable",
        "references",
        "duplicate_references",
        "dimension",
        "seed",
        "wrong_task",
    ],
)
def test_preparation_rejects_before_budget_admission(runtime, case):
    body = dict(
        schema_version="1.0",
        operation_id="prepare-invalid",
        project_id=PROJECT,
        generation_task_id=TASK,
        prompt="Fixture",
        seed="42",
    )
    if case == "ambiguous":
        runtime.catalog["UNETLoader"]["input"]["required"]["unet_name"][0].append(
            "other.safetensors"
        )
    elif case == "unavailable":
        del runtime.catalog["VAELoader"]
    elif case == "references":
        body["reference_artifact_ids"] = ["one", "two", "three", "four", "five"]
    elif case == "duplicate_references":
        body["reference_artifact_ids"] = ["one", "one"]
    elif case == "dimension":
        body["width"] = 513
    elif case == "seed":
        body["seed"] = str(2**53)
    else:
        body["generation_task_id"] = "wrong-task"
    result, kind = invoke(runtime, "prepare_image_generation", body)
    assert kind == "failure", result
    if case == "ambiguous":
        assert "configure provider_options.image_recipes" in result["message"]
    elif case in {"references", "duplicate_references"}:
        assert result["error"] == "invalid_request"
        assert (
            result["message"]
            == {
                "references": "Image preparation accepts at most four references",
                "duplicate_references": "Duplicate conditioning image",
            }[case]
        )
    with runtime.store.transaction(write=False) as tx:
        assert not tx.all("jobs") and not tx.all("artifacts")
        assert tx.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_asset_first_preparation_uses_frozen_creation_intent(runtime):
    with runtime.store.transaction() as tx:
        tx.put(
            "contexts",
            "context-one",
            dict(
                project_id=PROJECT,
                context_id="context-one",
                item_id=None,
                version_id=None,
                request_target={
                    "kind": "new_item",
                    "item_kind": "asset",
                    "intent_id": "asset-intent",
                },
            ),
        )
        tx.put(
            "requests",
            "request-one",
            dict(project_id=PROJECT, submission={"context_id": "context-one"}),
            expected_revision=tx.revision("requests", "request-one"),
        )
    result, kind = invoke(
        runtime,
        "prepare_image_generation",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            generation_task_id=TASK,
            operation_id="asset-first",
            prompt="Standalone asset concept",
            seed="42",
        ),
    )
    assert kind != "failure", result
    assert result["submit_request"]["target_id"] == "asset-intent"
    assert result["submit_request"]["settings"] == {}
    with runtime.store.transaction(write=False) as tx:
        assert not tx.all("items") and not tx.all("versions")


def register_body(runtime):
    from proscenium.sources.common import sha

    data = b"Synthetic retained metadata"
    path = ".proscenium/tool-output/staging/metadata.txt"
    staging = runtime.root / path
    staging.parent.mkdir(parents=True)
    staging.write_bytes(data)
    return dict(
        schema_version="1.0",
        project_id=PROJECT,
        operation_id="register-one",
        artifact_id="registered-one",
        staging_path=path,
        kind="document",
        purpose="metadata",
        media_type="text/plain",
        sha256=sha(data),
    )


def test_register_artifact_records_verified_output(runtime):
    body = register_body(runtime)
    result, kind = invoke(runtime, "register_artifact", body)
    assert kind != "failure", result
    assert (
        runtime.services.extension("artifacts").list_retained(PROJECT)["items"][0][
            "attribution_status"
        ]
        == "verified"
    )


@pytest.mark.parametrize("has_outcome", [False, True])
@pytest.mark.parametrize(
    "mismatch", ["recorded-name", "callback-name", "active-args", "replayable-args"]
)
def test_mismatched_tool_handler_cannot_execute_or_write_outcome(
    runtime, monkeypatch, mismatch, has_outcome
):
    if mismatch == "active-args":
        name = "retry_job"
        body = dict(
            schema_version="1.0",
            project_id=PROJECT,
            job_id="job-one",
            operation_id="retry-one",
            expected_revision=1,
        )
        args = {**body, "expected_revision": 2}
        service, method = runtime.services.jobs, "retry"
    else:
        name = "register_artifact"
        body = register_body(runtime)
        args = (
            {**body, "artifact_id": "different-artifact"} if mismatch == "replayable-args" else body
        )
        service, method = runtime.services.extension("artifacts"), "register"
    recorded_name = "publish_outputs" if mismatch == "recorded-name" else name
    call = invocation(runtime, recorded_name, body)
    callback = replace(
        call, tool_name="publish_outputs" if mismatch == "callback-name" else name, arguments=args
    )
    inv_id = key(SESSION, "tool", call.tool_call_id)
    prior = None
    if has_outcome:
        prior = record_tool_outcome(
            runtime.store,
            invocation_id=inv_id,
            session_id=SESSION,
            project_id=PROJECT,
            work_id=WORK,
            request_id="request-one",
            tool_name=recorded_name,
            status="succeeded",
        )

    effects = []
    monkeypatch.setattr(service, method, lambda *args: effects.append(args))
    result = asyncio.run(runtime.tools.invoke(name, callback))
    assert result.result_type == "failure"
    assert json.loads(result.text_result_for_llm)["error"] == "attribution_unavailable"
    assert not effects
    with runtime.store.transaction(write=False) as tx:
        assert get_tool_outcome(tx, inv_id) == prior
        assert tx.all("copilot.tool_outcomes") == ([prior] if has_outcome else [])
        assert tx.all("artifacts") == tx.all("jobs") == []


@pytest.fixture
def copilot(runtime):
    copilot = Copilot.__new__(Copilot)
    state = State(runtime.services, None)
    copilot.services, copilot.state, copilot.closing = runtime.services, state, False
    runtime.tools.state = state
    with runtime.store.transaction() as tx:
        tx.put(
            "copilot.control",
            "gate",
            dict(fenced=False, epoch="epoch", reconciled_epoch="epoch", revision=0),
        )
    return copilot


def custom_event(copilot, event_id, kind, **data):
    copilot.state.reduce(PROJECT, SESSION, dict(id=event_id, type=kind, parentId="root", data=data))


def custom_permission(runtime, copilot, name, body):
    call_id, permission_id = "custom-call", "custom-permission"
    call = ToolInvocation(
        session_id=SESSION, tool_call_id=call_id, tool_name=name, arguments=body, agent_id=None
    )
    stage_id = key(SESSION, permission_id)
    request = dict(kind="custom-tool", toolName=name, args=body, toolCallId=call_id)
    joined = dict(work_id=WORK, request_id="request-one", delivery_id="delivery-one")
    with runtime.store.transaction() as tx:
        copilot.state.bind(tx, SESSION, "event", "root", joined)
    custom_event(
        copilot,
        "initial-start",
        "tool.execution_start",
        toolCallId=call_id,
        toolName=name,
        arguments=body,
    )
    custom_event(
        copilot,
        "initial-permission",
        "permission.requested",
        requestId=permission_id,
        permissionRequest=request,
    )
    decision = asyncio.run(copilot.permission(PROJECT, request, {"session_id": SESSION}))
    with runtime.store.transaction(write=False) as tx:
        stage = tx.require("copilot.permissions", stage_id)
        assert not copilot.state.control(tx)["fenced"]
        assert tx.require("work", WORK)["state"] == "running"
    return decision, stage, call


@pytest.mark.parametrize("has_outcome", [False, True])
@pytest.mark.parametrize("conflict", ["name", "arguments", "noncanonical"])
def test_conflicting_native_start_cannot_rebind_approved_handler(
    runtime, copilot, monkeypatch, conflict, has_outcome
):
    body = register_body(runtime)
    decision, stage, call = custom_permission(runtime, copilot, "register_artifact", body)
    assert (decision.kind, stage["authority"]) == ("approve-once", "automatic_policy")
    inv_id = key(SESSION, "tool", call.tool_call_id)
    if has_outcome:
        result = asyncio.run(runtime.tools.invoke(call.tool_name, call))
        assert result.result_type == "success"
    with runtime.store.transaction(write=False) as tx:
        original = tx.require("invocations", inv_id)
        assert original["name"] == call.tool_name
        assert original["arguments_sha256"] == hashlib.sha256(canonical(body)).hexdigest()
        outcomes = tx.all("copilot.tool_outcomes")
        assert bool(outcomes) == has_outcome

    name = "publish_outputs" if conflict == "name" else call.tool_name
    args = (
        body
        if conflict == "name"
        else {
            **body,
            "artifact_id": float("nan") if conflict == "noncanonical" else "different-artifact",
        }
    )
    custom_event(
        copilot,
        "conflicting-start",
        "tool.execution_start",
        toolCallId=call.tool_call_id,
        toolName=name,
        arguments=args,
    )

    effects = []
    for extension, method in [("artifacts", "register"), ("catalogue", "publish")]:
        monkeypatch.setattr(
            runtime.services.extension(extension), method, lambda *args: effects.append(args)
        )
    conflicting = replace(call, tool_name=name, arguments=args)
    with pytest.raises(DomainError) as caught:
        runtime.tools.caller(conflicting, name=name, admit=False)
    assert (caught.value.code, caught.value.status) == ("attribution_unavailable", 409)
    result = asyncio.run(runtime.tools.invoke(name, conflicting))
    assert result.result_type == "failure"
    assert json.loads(result.text_result_for_llm)["error"] == "attribution_unavailable"
    assert not effects
    with runtime.store.transaction(write=False) as tx:
        assert tx.require("invocations", inv_id) == original
        control = copilot.state.control(tx)
        assert control["fenced"] and control["reason"] == "attribution_unavailable"
        assert tx.all("copilot.tool_outcomes") == outcomes


@pytest.mark.parametrize("case", ["empty-name", "missing-invocation"])
def test_tool_callback_fails_closed_without_exact_invocation(runtime, case):
    name = "register_artifact"
    call = invocation(runtime, name, register_body(runtime))
    call = (
        replace(call, tool_name="")
        if case == "empty-name"
        else replace(call, tool_call_id="unknown")
    )
    with pytest.raises(DomainError) as caught:
        runtime.tools.caller(call, name=name, admit=False)
    assert (caught.value.code, caught.value.status) == ("attribution_unavailable", 409)
    result = asyncio.run(runtime.tools.invoke(name, call))
    assert result.result_type == "failure"
    assert json.loads(result.text_result_for_llm)["error"] == "attribution_unavailable"
    with runtime.store.transaction(write=False) as tx:
        assert tx.all("copilot.tool_outcomes") == tx.all("artifacts") == []


def test_staged_tool_start_preserves_argument_digest_for_handler_and_duplicates(runtime, copilot):
    body = register_body(runtime)
    name, call_id = "register_artifact", "early-call"
    start = dict(toolCallId=call_id, toolName=name, arguments=body)
    digest = hashlib.sha256(canonical(body)).hexdigest()
    event_id = key(SESSION, "early-start")
    inv_id = key(SESSION, "tool", call_id)
    custom_event(copilot, "early-start", "tool.execution_start", **start)
    with runtime.store.transaction() as tx:
        pending = tx.require("copilot.pending_events", event_id)
        assert not pending["resolved"]
        assert pending["arguments_sha256"] == digest
        assert "arguments" not in pending["event"]["data"]
        assert tx.get("invocations", inv_id, required=False) is None
        copilot.state.bind(
            tx,
            SESSION,
            "event",
            "origin",
            dict(work_id=WORK, request_id="request-one", delivery_id="delivery-one"),
        )
    copilot.state.reduce(
        PROJECT, SESSION, dict(id="root", type="assistant.turn_start", parentId="origin")
    )
    copilot.state.drain_pending(PROJECT, SESSION)
    with runtime.store.transaction(write=False) as tx:
        assert tx.require("copilot.pending_events", event_id)["resolved"]
        original = tx.require("invocations", inv_id)
        assert original["arguments_sha256"] == digest

    call = ToolInvocation(
        session_id=SESSION, tool_call_id=call_id, tool_name=name, arguments=body, agent_id=None
    )
    result = asyncio.run(runtime.tools.invoke(name, call))
    assert result.result_type == "success", result.text_result_for_llm
    assert json.loads(result.text_result_for_llm)["artifact_id"] == body["artifact_id"]

    custom_event(copilot, "duplicate-early-start", "tool.execution_start", **start)
    with runtime.store.transaction(write=False) as tx:
        assert tx.require("invocations", inv_id) == original
        assert not copilot.state.control(tx)["fenced"]


@pytest.mark.parametrize("child", [False, True], ids=["tool", "subagent"])
def test_conflicting_native_parent_tool_call_preserves_original_invocation(runtime, copilot, child):
    parent_ids = ("task-call-1", "task-call-2")
    joined = dict(work_id=WORK, request_id="request-one", delivery_id="delivery-one")
    with runtime.store.transaction() as tx:
        for parent in parent_ids:
            copilot.state.bind(tx, SESSION, "tool", parent, joined)
    name = "authoring-specialist" if child else "register_artifact"
    inv_id = key(SESSION, "agent", "child-one") if child else key(SESSION, "tool", "custom-call")
    body = register_body(runtime)

    def emit(event_id, parent, *, starting):
        if child:
            event = dict(
                type="subagent.started" if starting else "subagent.completed",
                agentId="child-one",
                data=dict(agentName=name, toolCallId=parent),
            )
        else:
            event = dict(
                type="tool.execution_start" if starting else "tool.execution_complete",
                data=dict(
                    toolName=name, toolCallId="custom-call", parentToolCallId=parent, arguments=body
                ),
            )
        copilot.state.reduce(PROJECT, SESSION, dict(id=event_id, **event))

    emit("initial-start", parent_ids[0], starting=True)
    with runtime.store.transaction(write=False) as tx:
        original = tx.require("invocations", inv_id)
        assert original["parent_tool_call_id"] == parent_ids[0]
        assert not copilot.state.control(tx)["fenced"]
    emit("conflicting-start", parent_ids[1], starting=True)
    with runtime.store.transaction(write=False) as tx:
        assert tx.require("invocations", inv_id) == original
        control = copilot.state.control(tx)
        assert control["fenced"] and control["reason"] == "attribution_unavailable"

    emit("completion", parent_ids[1], starting=False)
    with runtime.store.transaction(write=False) as tx:
        completed = tx.require("invocations", inv_id)
        assert (completed["state"], completed["parent_tool_call_id"]) == (
            "completed",
            parent_ids[0],
        )


def test_duplicate_tool_start_is_a_noop_before_and_after_completion(runtime, copilot):
    _, _, call = custom_permission(runtime, copilot, "register_artifact", register_body(runtime))
    inv_id = key(SESSION, "tool", call.tool_call_id)
    data = dict(toolCallId=call.tool_call_id, toolName=call.tool_name, arguments=call.arguments)
    with runtime.store.transaction(write=False) as tx:
        original = tx.require("invocations", inv_id)
        revision, watermark = tx.revision("invocations", inv_id), tx.watermark()
    custom_event(copilot, "redundant-start", "tool.execution_start", **data)
    with runtime.store.transaction(write=False) as tx:
        assert tx.require("invocations", inv_id) == original
        assert (tx.revision("invocations", inv_id), tx.watermark()) == (revision, watermark)
    custom_event(
        copilot,
        "completion",
        "tool.execution_complete",
        **dict(data, toolName="wrong-name", arguments={}, success=True),
    )
    with runtime.store.transaction(write=False) as tx:
        completed = tx.require("invocations", inv_id)
        assert completed["state"] == "completed"
        assert all(completed[k] == original[k] for k in ("name", "arguments_sha256"))
        revision, watermark = tx.revision("invocations", inv_id), tx.watermark()
    custom_event(copilot, "late-start", "tool.execution_start", **data)
    with runtime.store.transaction(write=False) as tx:
        assert tx.require("invocations", inv_id) == completed
        assert (tx.revision("invocations", inv_id), tx.watermark()) == (revision, watermark)
        assert not copilot.state.control(tx)["fenced"]


def test_subagent_completion_and_conflicting_start_keep_original_identity(runtime, copilot):
    custom_permission(runtime, copilot, "register_artifact", register_body(runtime))
    agent = "specialist-one"
    inv_id = key(SESSION, "agent", agent)

    def child_event(identity, kind, name):
        copilot.state.reduce(
            PROJECT,
            SESSION,
            dict(id=identity, type=kind, parentId="root", agentId=agent, data=dict(agentName=name)),
        )

    child_event("child-start", "subagent.started", "authoring-specialist")
    child_event("child-done", "subagent.completed", "wrong-name")
    with runtime.store.transaction(write=False) as tx:
        completed = tx.require("invocations", inv_id)
        assert (completed["state"], completed["name"], completed["arguments_sha256"]) == (
            "completed",
            "authoring-specialist",
            None,
        )
    child_event("child-conflict", "subagent.started", "wrong-name")
    with runtime.store.transaction(write=False) as tx:
        assert tx.require("invocations", inv_id) == completed
        control = copilot.state.control(tx)
        assert control["fenced"] and control["reason"] == "attribution_unavailable"


@pytest.mark.parametrize("name", ["register_artifact", "validate_authored_document"])
def test_schema_invalid_custom_tools_enter_the_handler_without_fencing(runtime, copilot, name):
    if name == "register_artifact":
        body = dict(register_body(runtime), derived_from=["bad-string"])
        issue_path = "derived_from[0]"
    else:
        body = dict(schema_version="1.0", project_id=PROJECT, document=plan())
        issue_path = "document_kind"
    assert copilot.custom_tool_arguments(PROJECT, dict(toolName=name, args=body)) is True
    decision, stage, call = custom_permission(runtime, copilot, name, body)
    assert decision.kind == "approve-once"
    assert (stage["authority"], stage["reply"]) == ("automatic_policy", "approve")

    result = asyncio.run(runtime.tools.invoke(name, call))
    error = json.loads(result.text_result_for_llm)
    assert result.result_type == "failure" and error["error"] == "invalid_request"
    assert any(issue["path"] == issue_path for issue in error["details"]["issues"])
    assert "bad-string" not in result.text_result_for_llm
    with runtime.store.transaction(write=False) as tx:
        assert tx.all("artifacts") == []
        assert not copilot.state.control(tx)["fenced"]
        assert tx.require("work", WORK)["state"] == "running"
    runtime.services.gate.require(PROJECT)


@pytest.mark.parametrize("name", ["register_artifact", "validate_authored_document"])
def test_custom_tool_permission_still_rejects_cross_project_arguments(runtime, copilot, name):
    if name == "register_artifact":
        body = dict(register_body(runtime), project_id="other-project")
    else:
        body = dict(
            schema_version="1.0",
            project_id=PROJECT,
            document_kind="authored_shot_plan",
            document=dict(plan(), project_id="other-project"),
        )
    with pytest.raises(DomainError) as caught:
        copilot.custom_tool_arguments(PROJECT, dict(toolName=name, args=body))
    assert (caught.value.code, caught.value.status) == ("scope_denied", 403)
    decision, stage, _ = custom_permission(runtime, copilot, name, body)
    assert decision.kind == "reject"
    assert (stage["authority"], stage["reply"]) == ("host", "reject")


def test_custom_tool_permission_does_not_grant_unknown_or_non_json_arguments(copilot):
    assert copilot.custom_tool_arguments(PROJECT, dict(toolName="unknown", args={})) is False
    request = dict(kind="custom-tool", toolName="register_artifact", toolCallId="bad-call")
    with pytest.raises(DomainError, match="invalid request"):
        copilot.custom_tool_arguments(PROJECT, dict(request, args=[]))
    decision = asyncio.run(
        copilot.permission(
            PROJECT, dict(request, args={"value": float("nan")}), {"session_id": SESSION}
        )
    )
    assert decision.kind == "reject"


def retained_image(runtime, format="PNG", media_type="image/png"):
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(image, format=format)
    raw = image.getvalue()
    artifacts = runtime.services.extension("artifacts")
    artifact = artifacts.promote(
        PROJECT, "inline-image", raw, kind="image", purpose="concept", media_type=media_type
    )
    with runtime.store.transaction() as tx:
        artifacts.put(tx, artifact)
    return artifact, raw


@pytest.mark.parametrize(
    "format,media_type", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
def test_inline_image_sdk_binary_and_zero_files(runtime, format, media_type):
    artifact, raw = retained_image(runtime, format, media_type)
    call = invocation(
        runtime, "get_artifact", {"artifact_id": artifact["artifact_id"], "include_image": True}
    )
    before = {str(p.relative_to(runtime.root)): p.is_symlink() for p in runtime.root.rglob("*")}
    result = asyncio.run(runtime.tools.invoke("get_artifact", call))
    assert result.result_type == "success" and json.loads(result.text_result_for_llm) == artifact
    (binary,) = result.binary_results_for_llm
    assert isinstance(binary, ToolBinaryResult)
    assert binary.type == "image" and binary.mime_type == media_type
    assert binary.description == artifact["artifact_id"]
    assert base64.b64decode(binary.data, validate=True) == raw
    wire = tool_result_to_external_tool_text_result_for_llm(result)
    (rpc_image,) = wire.binary_results_for_llm
    assert isinstance(rpc_image, ExternalToolTextResultForLlmBinaryResultsForLlm)
    assert rpc_image.type is ExternalToolTextResultForLlmBinaryResultsForLlmType.IMAGE
    encoded = wire.to_dict()["binaryResultsForLlm"][0]
    assert encoded["type"] == "image" and encoded["mimeType"] == media_type
    assert base64.b64decode(encoded["data"], validate=True) == raw
    assert {
        str(p.relative_to(runtime.root)): p.is_symlink() for p in runtime.root.rglob("*")
    } == before
    for i, body in enumerate(
        (
            {"artifact_id": artifact["artifact_id"]},
            {"artifact_id": artifact["artifact_id"], "include_image": False},
        )
    ):
        metadata = asyncio.run(
            runtime.tools.invoke(
                "get_artifact", invocation(runtime, "get_artifact", body, f"metadata-{i}")
            )
        )
        assert metadata.binary_results_for_llm is None


@pytest.mark.parametrize(
    "case",
    ["document", "media_type", "unavailable", "corrupt", "bytes", "pixels", "hash", "length"],
)
def test_inline_image_safe_rejections(runtime, monkeypatch, case):
    artifact, raw = retained_image(runtime)
    expected = "invalid_request"
    if case == "document":
        artifact.update(kind="document", media_type="text/plain")
    elif case == "media_type":
        artifact["media_type"] = "image/gif"
    elif case in {"unavailable", "corrupt"}:
        artifact["state"] = case
        expected = "media_unavailable"
    elif case == "bytes":
        runtime.services.settings = replace(
            runtime.services.settings, upload_max_bytes=len(raw) - 1
        )
    elif case == "pixels":
        runtime.services.settings = replace(
            runtime.services.settings, upload_max_pixels=64 * 64 - 1
        )
    else:
        path = runtime.services.extension("artifacts").ready_path(PROJECT, artifact["artifact_id"])
        # Exercise the tool's own second hash/length check even if bytes change
        # after ready_path validation, without weakening the production resolver.
        monkeypatch.setattr(
            runtime.services.extension("artifacts"), "ready_path", lambda *args: path
        )
        path.chmod(0o600)
        path.write_bytes(raw[:-1] if case == "length" else b"x" * len(raw))
        expected = "hash_mismatch"
    with runtime.store.transaction() as tx:
        tx.put(
            "artifacts",
            artifact["artifact_id"],
            artifact,
            expected_revision=tx.revision("artifacts", artifact["artifact_id"]),
        )
    result, kind = invoke(
        runtime, "get_artifact", {"artifact_id": artifact["artifact_id"], "include_image": True}
    )
    assert kind == "failure" and result["error"] == expected
    if case in {"unavailable", "corrupt"}:
        assert result["message"] == "Image artefact is not ready for inline inspection"
    elif case in {"document", "media_type"}:
        assert (
            result["message"]
            == "Inline image inspection supports ready PNG, JPEG or WebP artefacts only"
        )
    elif case in {"bytes", "pixels"}:
        assert result["message"] == "Image artefact exceeds inline inspection bounds"


@pytest.mark.parametrize(
    "name,field,value",
    [
        ("get_artifact", "include_image", 1),
        ("get_artifact", "include_image", "true"),
        ("get_artifact", "include_image", None),
        ("get_job", "wait_seconds", True),
        ("get_job", "wait_seconds", -1),
        ("get_job", "wait_seconds", 31),
        ("get_job", "wait_seconds", 1.0),
        ("get_job", "wait_seconds", "15"),
        ("get_job", "wait_seconds", None),
    ],
)
def test_read_arguments_reject_inexact_optional_types(name, field, value):
    from proscenium.copilot.tools import read_arguments

    identity = "artifact_id" if name == "get_artifact" else "job_id"
    with pytest.raises(DomainError) as error:
        read_arguments(name, {identity: "identity", field: value})
    assert error.value.code == "invalid_request"


def test_read_parameter_defaults_and_boundaries():
    from proscenium.copilot.tools import read_arguments, read_parameters

    assert read_parameters("get_artifact")["properties"]["include_image"] == {
        "type": "boolean",
        "default": False,
    }
    assert read_parameters("get_job")["properties"]["wait_seconds"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 30,
        "default": 0,
    }
    for seconds in (0, 30):
        assert (
            read_arguments("get_job", {"job_id": "job", "wait_seconds": seconds})["wait_seconds"]
            == seconds
        )


def waiting_job(runtime, state="running"):
    job = dict(
        schema_version="1.0",
        project_id=PROJECT,
        job_id="wait-job",
        work_id=WORK,
        owner_operation_id=None,
        target={"target_id": TASK, "version": None, "shot_id": None},
        attempt_id="attempt-one",
        attempt_number=1,
        kind="image_generation",
        state=state,
        state_revision=0,
        availability="pending",
        input_digest="0" * 64,
        external_job_id=None,
        provider_id="comfy",
        owner_epoch="epoch",
        completed_frames=None,
        expected_frames=None,
        artifact_ids=[],
        render_spec=None,
    )
    with runtime.store.transaction() as tx:
        tx.put("jobs", job["job_id"], job, schema="JobRecord")
    return job


@pytest.mark.parametrize(
    "change",
    [
        "succeeded",
        "gate",
        "stop_requested",
        "stopped",
        "awaiting_resume",
        "outcome_uncertain",
        "fenced",
    ],
)
def test_job_wait_async_ticks_and_execution_holds(runtime, monkeypatch, change):
    job = waiting_job(runtime)
    call = invocation(runtime, "get_job", {"job_id": job["job_id"], "wait_seconds": 15})
    reads = []
    original_get = runtime.services.jobs.get

    def observed_get(*args):
        result = original_get(*args)
        reads.append(result["state"])
        return result

    monkeypatch.setattr(runtime.services.jobs, "get", observed_get)

    async def run():
        async def transition():
            await asyncio.sleep(0.01)
            if change == "gate":
                runtime.services.gate.close(PROJECT)
            elif change == "fenced":
                runtime.state.control = lambda tx: {"fenced": True}
            else:
                bucket, identity = (
                    ("jobs", job["job_id"]) if change == "succeeded" else ("work", WORK)
                )
                with runtime.store.transaction() as tx:
                    record = tx.require(bucket, identity)
                    tx.put(
                        bucket,
                        identity,
                        {
                            **record,
                            "state": change,
                            "state_revision": tx.revision(bucket, identity) + 1,
                        },
                        expected_revision=tx.revision(bucket, identity),
                    )

        background = asyncio.create_task(transition())
        result = await runtime.tools.invoke("get_job", call)
        await background
        return result

    started = time.monotonic()
    result = asyncio.run(run())
    assert time.monotonic() - started < 3
    assert result.result_type == "success"
    assert json.loads(result.text_result_for_llm)["state"] == (
        "succeeded" if change == "succeeded" else "running"
    )
    assert reads == ["running", "succeeded" if change == "succeeded" else "running"]
    with runtime.store.transaction(write=False) as tx:
        assert tx.revision("jobs", job["job_id"]) == (1 if change == "succeeded" else 0)


@pytest.mark.parametrize("state", ["succeeded", "failed", "cancelled", "outcome_uncertain"])
def test_job_wait_terminal_states_return_without_sleep(runtime, monkeypatch, state):
    job = waiting_job(runtime, state)

    async def forbidden_sleep(*args):
        raise AssertionError("Terminal reads must not wait")

    monkeypatch.setattr(asyncio, "sleep", forbidden_sleep)
    result, kind = invoke(runtime, "get_job", {"job_id": job["job_id"], "wait_seconds": 30})
    assert kind == "success" and result["state"] == state


def test_job_wait_deadline_and_default_read_only(runtime, monkeypatch):
    job = waiting_job(runtime)
    ticks = []
    clock = [0.0]

    async def tick(seconds):
        ticks.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("proscenium.copilot.tools.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("proscenium.copilot.tools.asyncio.sleep", tick)
    for i, body in enumerate(
        ({"job_id": job["job_id"]}, {"job_id": job["job_id"], "wait_seconds": 0})
    ):
        result, kind = invoke(runtime, "get_job", body, f"inspect-{i}")
        assert kind == "success" and result == job and not ticks
    result, kind = invoke(runtime, "get_job", {"job_id": job["job_id"], "wait_seconds": 1})
    assert kind == "success" and result == job and ticks == [0.5, 0.5]
    with runtime.store.transaction(write=False) as tx:
        assert tx.revision("jobs", job["job_id"]) == 0


@pytest.mark.parametrize("hold", ["gate", "fenced", "stop_requested", "awaiting_resume"])
def test_job_wait_already_held_returns_without_sleep(runtime, monkeypatch, hold):
    job = waiting_job(runtime)
    if hold == "gate":
        runtime.services.gate.close(PROJECT)
    elif hold == "fenced":
        runtime.state.control = lambda tx: {"fenced": True}
    else:
        with runtime.store.transaction() as tx:
            work = tx.require("work", WORK)
            tx.put(
                "work",
                WORK,
                {**work, "state": hold, "state_revision": tx.revision("work", WORK) + 1},
                expected_revision=tx.revision("work", WORK),
            )

    async def forbidden_sleep(*args):
        raise AssertionError("Held reads must not wait")

    monkeypatch.setattr(asyncio, "sleep", forbidden_sleep)
    result, kind = invoke(runtime, "get_job", {"job_id": job["job_id"], "wait_seconds": 30})
    assert kind == "success" and result == job


@pytest.mark.parametrize("revision", ["comfy-image-1", sd_turbo.REVISION, sd_turbo_fp16.REVISION])
def test_capability_recipe_defaults(runtime, revision):
    runtime.provider.options["image_profiles"] = {"comfy": revision}
    row = get_project_capabilities(runtime.services, PROJECT)["providers"][0]
    assert row["status"] == "ready"
    assert row["default_dimensions"] == {"width": 512, "height": 512}
    assert row["default_steps"] == 4
    assert row["max_pixels"] == (1_048_576 if revision == "comfy-image-1" else 512 * 512)
    assert "512x512, 4 steps" in row["summary"]
    assert "Observed recipe preflight passed" in row["summary"]
    assert "Inference remains untested" in row["summary"]


def test_explicit_recipe_defaults_retained_when_unavailable(runtime):
    runtime.provider.options["image_recipes"] = {
        "comfy": {**RECIPE, "width": 768, "height": 512, "steps": 8, "max_pixels": 786432}
    }
    row = get_project_capabilities(runtime.services, PROJECT)["providers"][0]
    assert row["status"] == "ready"
    assert row["default_dimensions"] == {"width": 768, "height": 512}
    assert row["default_steps"] == 8 and row["max_pixels"] == 786432
    runtime.services.settings.services[0]["available"] = False
    rows = get_project_capabilities(runtime.services, PROJECT)["providers"]
    assert rows[0]["default_dimensions"] == row["default_dimensions"]
    assert rows[0]["default_steps"] == 8 and rows[0]["max_pixels"] == 786432
    assert all(
        rows[1][field] is None for field in ("default_dimensions", "default_steps", "max_pixels")
    )


def test_prepare_omitted_project_and_task_bound_by_host(runtime, monkeypatch):
    from proscenium.copilot.service import Copilot

    body = dict(schema_version="1.0", operation_id="prepare-omitted", prompt="Fixture", seed="42")
    assert Copilot.custom_tool_arguments(
        None, PROJECT, {"toolName": "prepare_image_generation", "args": body}
    )
    service = runtime.services.extension("image_preparation")
    original_prepare, original_replayable = service.prepare, service.replayable
    seen = []

    def prepare(caller, request):
        seen.append(request["project_id"])
        return original_prepare(caller, request)

    def replayable(caller, request):
        seen.append(request["project_id"])
        return original_replayable(caller, request)

    monkeypatch.setattr(service, "prepare", prepare)
    monkeypatch.setattr(service, "replayable", replayable)
    result, kind = invoke(runtime, "prepare_image_generation", body)
    assert kind == "success", result
    assert seen == [PROJECT, PROJECT, PROJECT]
    assert result["submit_request"]["project_id"] == PROJECT
    assert result["submit_request"]["generation_task_id"] == TASK
    mismatched = {**body, "project_id": "other-project"}
    with pytest.raises(DomainError) as error:
        Copilot.custom_tool_arguments(
            None, PROJECT, {"toolName": "prepare_image_generation", "args": mismatched}
        )
    assert error.value.code == "scope_denied" and error.value.status == 403
    result, kind = invoke(runtime, "prepare_image_generation", mismatched, "mismatch")
    assert kind == "failure" and result["error"] == "scope_denied"
    assert seen == [PROJECT, PROJECT, PROJECT]


def test_preflight_diagnostics_never_echo_unknown_reasons():
    from proscenium.copilot.tools import ERROR_GUIDANCE, tool_failure

    for name in ("prepare_image_generation", "validate_authored_document"):
        result = tool_failure(DomainError("invalid_request", message="private-provider-body"), name)
        message = json.loads(result.text_result_for_llm)["message"]
        assert "before any external job or inference was submitted" in message
        assert "private-provider-body" not in message
    assert "corrupted raster image" in ERROR_GUIDANCE["corrupt_provider_output"]


def test_native_lead_specialist_prompt_wiring(runtime, monkeypatch):
    from proscenium.copilot.native import (
        INSTRUCTIONS,
        LEAD_INSTRUCTIONS,
        SHARED_INSTRUCTIONS,
        SPECIALIST_INSTRUCTIONS,
        UNITY_INSTRUCTIONS,
        Native,
    )

    assert INSTRUCTIONS is LEAD_INSTRUCTIONS
    assert LEAD_INSTRUCTIONS != SPECIALIST_INSTRUCTIONS
    assert (
        SHARED_INSTRUCTIONS in LEAD_INSTRUCTIONS and SHARED_INSTRUCTIONS in SPECIALIST_INSTRUCTIONS
    )
    assert "via the task tool" in LEAD_INSTRUCTIONS
    assert "Do not run long authoring or generation loops inline" in LEAD_INSTRUCTIONS
    assert "relay the full pitch/shortlist or single-item revision card" in LEAD_INSTRUCTIONS
    assert (
        "never submit image generation without explicit generation instruction" in LEAD_INSTRUCTIONS
    )
    assert "without unnecessary nested subagent delegation" in SPECIALIST_INSTRUCTIONS
    assert "storyboard, hero-assets, image-generation, image-to-3d" in SPECIALIST_INSTRUCTIONS
    assert "Nested delegation is optional" not in LEAD_INSTRUCTIONS + SPECIALIST_INSTRUCTIONS
    for rule in (
        "include_image=true",
        "wait_seconds=15",
        'receipt["source_closure"]',
        "omit project_id and generation_task_id",
        "request_vlm_review",
        "owner-approved findings",
    ):
        assert rule in LEAD_INSTRUCTIONS and rule in SPECIALIST_INSTRUCTIONS

    captured = []

    class Client:
        async def create_session(self, **settings):
            captured.append(settings)
            return "session"

        async def resume_session(self, session_id, **settings):
            captured.append(settings)
            return session_id

    native = Native(runtime.services)
    native.client = Client()
    monkeypatch.setattr(
        "proscenium.copilot.native.hashlib.file_digest",
        lambda *args: SimpleNamespace(
            hexdigest=lambda: "463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69"
        ),
    )
    assert asyncio.run(native.open(PROJECT, None, {})) == "session"
    assert asyncio.run(native.open(PROJECT, SESSION, {})) == SESSION
    for settings in captured:
        assert settings["system_message"]["content"] == LEAD_INSTRUCTIONS
        authoring, unity = settings["custom_agents"]
        assert authoring["prompt"] == SPECIALIST_INSTRUCTIONS
        assert unity["prompt"].startswith(UNITY_INSTRUCTIONS)
