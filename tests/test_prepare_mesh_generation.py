"""Offline mesh preparation, immutable submission and ComfyUI pump coverage."""

import io
import json
from dataclasses import replace

import httpx
import pytest
from PIL import Image
from proscenium.copilot.capabilities import get_project_capabilities
from proscenium.guidance import REVIEWED_SAFE_REASONS
from proscenium.jobs.resources import Resources
from proscenium.providers import budget3d, hunyuan
from proscenium.providers.bridge import register_provider_artifact
from proscenium.providers.preparation import HUNYUAN_REVISION, MeshPreparation
from test_provider_budget3d import add_budget3d_catalog, make_vertex_colored_glb
from test_worker_capabilities_and_publication import PROJECT, TASK, invoke
from test_worker_capabilities_and_publication import offline as offline  # noqa: F401
from test_worker_capabilities_and_publication import runtime as base_runtime  # noqa: F401

REFERENCE = "mesh-reference"
TOOL = "prepare_mesh_generation"
OVERRIDES = dict(mode="real", budget=0.5, steps=5, num_gaussians=131072, mesh_resolution=256)


@pytest.fixture
def runtime(base_runtime):  # noqa: F811
    state = base_runtime
    services = state.services
    services.settings = replace(
        services.settings,
        services=({**services.settings.services[0], "capabilities": ["image_to_3d"]},),
    )
    services.extensions["mesh_preparation"] = MeshPreparation(services, state.provider)
    add_budget3d_catalog(state.catalog)
    stream = io.BytesIO()
    Image.new("RGB", (64, 64), (180, 90, 40)).save(stream, format="PNG")
    register(state, REFERENCE, stream.getvalue(), "image", "reference", "image/png")
    return state


def register(runtime, identity, data, kind, purpose, media_type, **changes):
    artifacts = runtime.services.extension("artifacts")
    record = artifacts.promote(
        PROJECT, identity, data, kind=kind, purpose=purpose, media_type=media_type
    )
    with runtime.store.transaction() as tx:
        artifacts.put(tx, {**record, **changes})


def mesh_request(**overrides):
    return {
        **dict(
            schema_version="1.0",
            operation_id="prepare-prop",
            reference_artifact_id=REFERENCE,
            prompt="Isometric lantern prop",
            seed="42",
        ),
        **overrides,
    }


def retained_workflow(runtime, prepared):
    artifact, data = runtime.provider.artifact_bytes(PROJECT, prepared["workflow_artifact_id"])
    assert artifact["kind"] == "document" and artifact["purpose"] == "metadata"
    assert artifact["media_type"] == "application/json"
    return json.loads(data)


@pytest.mark.parametrize("overrides", [{}, OVERRIDES], ids=["defaults", "overrides"])
def test_turbo_preparation_and_exact_submission_pump(runtime, overrides):
    body = mesh_request(**overrides)
    services, provider = runtime.services, runtime.provider
    if overrides:
        provider.options["mesh_recipes"] = {
            "comfy": {"profile_revision": budget3d.TURBO_REVISION, "steps": 6, "budget": 0.75}
        }
    expected_graph = budget3d.recipe_graph(REFERENCE, budget3d.TURBO_REVISION, overrides)
    model_ids = budget3d.models()
    prepared, kind = invoke(runtime, TOOL, body, "prepare")
    assert kind == "success", prepared
    assert prepared["profile_revision"] == budget3d.TURBO_REVISION
    assert prepared["replayed"] is False
    assert prepared["model_ids"] == model_ids
    assert prepared["input_artifact_ids"] == [REFERENCE]
    assert prepared["project_id"] == PROJECT and prepared["operation_id"] == body["operation_id"]
    assert [call.url.path for call in runtime.calls] == ["/object_info"]
    assert retained_workflow(runtime, prepared) == expected_graph
    artifact, prompt = provider.artifact_bytes(PROJECT, prepared["prompt_artifact_id"])
    assert artifact["kind"] == "document" and artifact["purpose"] == "metadata"
    assert artifact["media_type"] == "text/plain; charset=utf-8"
    assert prompt == body["prompt"].encode()

    request = prepared["submit_request"]
    assert request == dict(
        schema_version="1.0",
        job_id=request["job_id"],
        project_id=PROJECT,
        generation_task_id=TASK,
        target_id=TASK,
        provider_id="comfy",
        capability="image_to_3d",
        workflow_artifact_id=prepared["workflow_artifact_id"],
        prompt_artifact_id=prepared["prompt_artifact_id"],
        input_artifact_ids=[REFERENCE],
        seed=body["seed"],
        model_ids=model_ids,
        settings={},
    )
    with runtime.store.transaction(write=False) as tx:
        assert tx.require("providers.prepared", request["workflow_artifact_id"]) == prepared
        assert tx.all("jobs") == []
        assert tx.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0

    # A prepared workflow pins the original profile even when live configuration changes.
    provider.options["mesh_profiles"] = {"comfy": HUNYUAN_REVISION}
    changed, kind = invoke(runtime, "submit_generation", {**request, "seed": "43"}, "changed")
    assert kind == "failure" and changed["error"] == "idempotency_conflict"
    job, kind = invoke(runtime, "submit_generation", request, "submit")
    assert kind == "success" and job["kind"] == "mesh_generation", job
    assert [call.url.path for call in runtime.calls] == ["/object_info"]

    glb = make_vertex_colored_glb()
    calls = []

    def respond(call):
        path = call.url.path
        calls.append(path)
        if path == "/upload/image":
            _, pixels = provider.image_bytes(PROJECT, REFERENCE)
            assert call.method == "POST" and pixels in call.content
            return httpx.Response(200, json={"name": "conditioning.png", "subfolder": "fixture"})
        if path == "/prompt":
            assert call.method == "POST"
            sent = json.loads(call.content)["prompt"]
            assert sent == budget3d.bind(
                expected_graph, REFERENCE, "fixture/conditioning.png", "42"
            )
            return httpx.Response(200, json={"prompt_id": "mesh-prompt"})
        if path == "/view":
            assert call.url.params["filename"] == "mesh.glb"
            return httpx.Response(200, content=glb)
        return httpx.Response(
            200,
            json={
                "/object_info": runtime.catalog,
                "/queue": {"queue_running": [], "queue_pending": []},
                "/system_stats": {"devices": [{"vram_free": 2**32}]},
                "/history/mesh-prompt": {
                    "mesh-prompt": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {"15": {"3d": [{"filename": "mesh.glb", "type": "output"}]}},
                    }
                },
            }[path],
        )

    services.resources = Resources(runtime.store)
    provider.registrar = lambda caller, body: register_provider_artifact(services, caller, body)
    with httpx.Client(transport=httpx.MockTransport(respond)) as provider.client:
        (settled,) = provider.pump(PROJECT, job["job_id"])
    assert settled["state"] == "succeeded", settled
    assert calls.count("/prompt") == calls.count("/upload/image") == 1
    _, provenance = provider.artifact_bytes(PROJECT, settled["artifact_ids"][1])
    evidence = json.loads(provenance)
    assert evidence["inputs"]["request"] == request
    assert evidence["output_inspections"][0]["inspection"]["vertex_colors"] is True


def test_guided_idol_merges_configured_defaults_with_partial_request(runtime):
    runtime.provider.options["mesh_recipes"] = {
        "comfy": {
            "profile_revision": budget3d.IDOL_REVISION,
            "budget": 0.75,
            "mesh_resolution": 256,
        }
    }
    caps = get_project_capabilities(runtime.services, PROJECT)
    assert TOOL in caps["exposed_tools"] and TOOL not in caps["missing_worker_tools"]
    (mesh,) = caps["providers"]
    assert mesh["status"] == "ready" and mesh["default_steps"] == 50

    prepared, kind = invoke(runtime, TOOL, mesh_request(mode="real"))
    assert kind == "success", prepared
    assert prepared["profile_revision"] == budget3d.IDOL_REVISION
    assert prepared["model_ids"] == mesh["selected_models"] == budget3d.models(turbo=False)
    graph = retained_workflow(runtime, prepared)
    assert graph == budget3d.recipe_graph(
        REFERENCE, budget3d.IDOL_REVISION, dict(mode="real", budget=0.75, mesh_resolution=256)
    )
    assert graph["10"]["inputs"]["steps"] == 50 and graph["10"]["inputs"]["cfg"] == 3.0


def test_legacy_hunyuan_pinned_workflow_and_reviewed_override_denial(runtime):
    runtime.provider.options["mesh_profiles"] = {"comfy": HUNYUAN_REVISION}
    for node in hunyuan.graph(REFERENCE).values():
        inputs = {}
        for field, value in node["inputs"].items():
            inputs[field] = (
                [[hunyuan.FILENAME]]
                if field == "ckpt_name"
                else ["*"]
                if isinstance(value, list)
                else ["INT"]
                if type(value) is int or value == "$seed"
                else ["FLOAT"]
                if type(value) is float
                else ["STRING"]
            )
        runtime.catalog[node["class_type"]] = dict(
            input={"required": inputs},
            output=["*", "*", "*"],
            output_node=node["class_type"] == "SaveGLB",
        )
    prepared, kind = invoke(runtime, TOOL, mesh_request())
    assert kind == "success", prepared
    assert prepared["profile_revision"] == HUNYUAN_REVISION
    assert prepared["model_ids"] == [hunyuan.FILENAME]
    assert retained_workflow(runtime, prepared) == hunyuan.graph(REFERENCE)
    assert [call.url.path for call in runtime.calls] == ["/object_info"]
    for field, value in OVERRIDES.items():
        result, kind = invoke(
            runtime, TOOL, mesh_request(operation_id=field, **{field: value}), field
        )
        assert kind == "failure" and result["error"] == "invalid_request", result
        assert result["message"] in REVIEWED_SAFE_REASONS
        assert result["message"].startswith("Hunyuan3D uses a pinned recipe; omit ")


def test_replay_conflict_and_planning_only_are_decided_before_provider_reads(runtime):
    body = mesh_request()
    first, kind = invoke(runtime, TOOL, body, "initial")
    assert kind == "success", first
    replay, kind = invoke(runtime, TOOL, body, "replayed")
    assert kind == "success" and replay == {**first, "replayed": True}
    conflict, kind = invoke(runtime, TOOL, {**body, "prompt": "Different"}, "conflicting")
    assert kind == "failure" and conflict["error"] == "idempotency_conflict"

    with runtime.store.transaction() as tx:
        tx.put("contexts", "planning", dict(project_id=PROJECT, execution_scope="planning_only"))
        tx.put(
            "requests",
            "request-one",
            dict(project_id=PROJECT, submission={"context_id": "planning"}),
            expected_revision=tx.revision("requests", "request-one"),
        )
    denied, kind = invoke(runtime, TOOL, mesh_request(operation_id="planning"), "planning")
    assert kind == "failure" and denied["error"] == "scope_denied"
    assert "planning_only" in denied["message"]
    assert [call.url.path for call in runtime.calls] == ["/object_info"]
    with runtime.store.transaction(write=False) as tx:
        assert tx.all("providers.prepared") == [first]


@pytest.mark.parametrize(
    "reference,error",
    [
        ("absent", "not_found"),
        ("document", "unsupported_media_type"),
        ("unready", "media_unavailable"),
    ],
)
def test_preparation_requires_one_ready_image(runtime, reference, error):
    if reference == "document":
        register(runtime, reference, b"Not an image", "document", "metadata", "text/plain")
    elif reference == "unready":
        _, pixels = runtime.provider.image_bytes(PROJECT, REFERENCE)
        register(runtime, reference, pixels, "image", "reference", "image/png", state="unavailable")
    result, kind = invoke(runtime, TOOL, mesh_request(reference_artifact_id=reference))
    assert kind == "failure" and result["error"] == error, result
    with runtime.store.transaction(write=False) as tx:
        assert tx.all("providers.prepared") == []


@pytest.mark.parametrize("missing", ["node", "model"])
def test_preparation_requires_installed_comfy_nodes_and_models(runtime, missing):
    if missing == "node":
        del runtime.catalog["SplatToMesh"]
        reason = "Graph node is not installed"
    else:
        runtime.catalog["Budget3DLoader"]["input"]["required"]["unet_name"] = [[budget3d.IDOL_UNET]]
        reason = "Model/choice is unavailable"
    result, kind = invoke(runtime, TOOL, mesh_request())
    assert kind == "failure" and result["error"] == "capability_unavailable", result
    assert result["message"] == reason
    assert [call.url.path for call in runtime.calls] == ["/object_info"]
    with runtime.store.transaction(write=False) as tx:
        assert tx.all("providers.prepared") == []


@pytest.mark.parametrize("seed", ["9007199254740992", "0" * 17, "123\n", "-1", 42])
def test_mesh_tool_schema_is_self_contained_and_rejects_bad_seeds(runtime, seed):
    (tool,) = [definition for definition in runtime.tools.definitions() if definition.name == TOOL]
    serialised = json.dumps(tool.parameters)
    assert not any(f'"{key}":' in serialised for key in ("$ref", "$defs", "const"))
    result, kind = invoke(runtime, TOOL, mesh_request(seed=seed))
    assert kind == "failure" and result["error"] == "invalid_request"
    assert any(issue["path"] == "seed" for issue in result["details"]["issues"])
    assert runtime.calls == []


@pytest.mark.parametrize(
    "field,value,code,message",
    [
        ("steps", 0, "minimum", "Must be >= 1"),
        ("steps", 1, None, None),
        ("steps", 50, None, None),
        ("steps", 51, "maximum", "Must be <= 50"),
        ("num_gaussians", 32767, "minimum", "Must be >= 32768"),
        ("num_gaussians", 32768, None, None),
        ("num_gaussians", 32769, "multipleOf", "Must be a multiple of 32"),
        ("num_gaussians", 1048576, None, None),
        ("num_gaussians", 1048577, "maximum", "Must be <= 1048576"),
        ("mesh_resolution", 63, "minimum", "Must be >= 64"),
        ("mesh_resolution", 64, None, None),
        ("mesh_resolution", 65, "multipleOf", "Must be a multiple of 16"),
        ("mesh_resolution", 768, None, None),
        ("mesh_resolution", 769, "maximum", "Must be <= 768"),
    ],
)
def test_mesh_recipe_boundaries_match_the_model_visible_schema(
    runtime, field, value, code, message
):
    overrides = {field: value}
    result, kind = invoke(runtime, TOOL, mesh_request(**overrides))
    if code is None:
        assert kind == "success", result
        assert retained_workflow(runtime, result) == budget3d.recipe_graph(
            REFERENCE, recipe=overrides
        )
        assert [call.url.path for call in runtime.calls] == ["/object_info"]
        return
    assert kind == "failure" and result["error"] == "invalid_request"
    assert (
        dict(phase="schema", code=code, path=field, message=message) in result["details"]["issues"]
    )
    assert runtime.calls == []
