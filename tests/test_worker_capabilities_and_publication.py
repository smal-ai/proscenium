"""Hermetic worker tools, immutable preparation and retained publication diagnostics."""

import asyncio
import copy
import importlib.util
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from copilot import CopilotClient
from copilot.tools import ToolInvocation
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.catalogue.artifacts import Artifacts
from proscenium.catalogue.service import Catalogue
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.copilot.capabilities import (
    compare,
    get_project_capabilities,
    validate_authored_document,
)
from proscenium.copilot.native import Configuration
from proscenium.copilot.outcomes import get_tool_outcome
from proscenium.copilot.state import key
from proscenium.copilot.tools import Tools
from proscenium.db import Store
from proscenium.db.pagination import Cursors
from proscenium.db.registry import Registry
from proscenium.jobs import JobService
from proscenium.jobs.gate import ExecutionGate
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


def invoke(runtime, name, body, identity="call-one"):
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
                    request_id="request-one",
                    work_id=WORK,
                    owner_epoch="epoch",
                    state="running",
                ),
            )
    invocation = ToolInvocation(
        session_id=SESSION, tool_call_id=identity, tool_name=name, arguments=body, agent_id=None
    )
    result = asyncio.run(runtime.tools.invoke(name, invocation))
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


def test_capabilities_skills_override_ready_ambiguous_unavailable(runtime):
    runtime.services.settings.runtime["worker_tools"] = ["view"]
    result, _ = invoke(runtime, "get_project_capabilities", {})
    assert result["exposed_tools"] == ["view"] and not result["skill_loader_exposed"]
    assert "skill" in result["missing_worker_tools"] and "storyboard" in result["available_skills"]
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
    conflict, _ = invoke(runtime, "prepare_image_generation", {**body, "prompt": "Different"})
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
    "case", ["ambiguous", "unavailable", "references", "dimension", "seed", "wrong_task"]
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
        body["reference_artifact_ids"] = ["one", "two"]
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


def test_register_artifact_records_verified_output(runtime):
    from proscenium.sources.common import sha

    data = b"Synthetic retained metadata"
    path = ".proscenium/tool-output/staging/metadata.txt"
    staging = runtime.root / path
    staging.parent.mkdir(parents=True)
    staging.write_bytes(data)
    body = dict(
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
    result, kind = invoke(runtime, "register_artifact", body)
    assert kind != "failure", result
    assert (
        runtime.services.extension("artifacts").list_retained(PROJECT)["items"][0][
            "attribution_status"
        ]
        == "verified"
    )
