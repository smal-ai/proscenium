"""Offline Budget3D image_to_3d graph, capability preflight, GLB vertex-colour and pump tests."""

import io
import json
import struct
from dataclasses import replace

import httpx
import pytest
import test_worker_capabilities_and_publication as shared
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.copilot.capabilities import get_project_capabilities
from proscenium.jobs.resources import Resources
from proscenium.providers import budget3d, hunyuan
from proscenium.providers.mesh import inspect_glb_bytes
from proscenium.providers.plugins import plugin
from test_worker_capabilities_and_publication import PROJECT, ROOT, TASK, invoke


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    shared.offline.__wrapped__(monkeypatch)


@pytest.fixture
def runtime(tmp_path):
    for state in shared.runtime.__wrapped__(tmp_path):
        try:
            yield state
        finally:
            state.provider.client.close()


def add_budget3d_catalog(catalog):
    sample_graph = budget3d.graph("reference", turbo=True)
    choices = {
        "unet_name": [budget3d.TURBO_UNET, budget3d.IDOL_UNET, "flux-2-klein-4b.safetensors"],
        "clip_name": [budget3d.CLIP_VISION, "qwen_3_4b.safetensors"],
        "vae_name": [budget3d.FLUX_VAE, budget3d.SPLAT_VAE],
        "bg_removal_name": [budget3d.BG_REMOVAL],
        "mode": ["synthetic", "real"],
        "dtype": ["default", "fp32", "fp16", "bf16"],
    }
    for node in sample_graph.values():
        kind = node["class_type"]
        req = {}
        for field, value in node["inputs"].items():
            if field in choices:
                req[field] = ["COMBO", {"options": choices[field]}]
            elif isinstance(value, list):
                req[field] = ["*"]
            elif isinstance(value, bool):
                req[field] = ["BOOLEAN"]
            elif isinstance(value, int) or value == "$seed":
                req[field] = ["INT"]
            elif isinstance(value, float):
                req[field] = ["FLOAT"]
            else:
                req[field] = ["STRING"]
        existing = catalog.get(kind, {}).get("input", {}).get("required", {})
        if kind in {"UNETLoader", "CLIPLoader", "VAELoader"} and existing:
            for field in choices:
                if field in existing:
                    existing[field] = [sorted(set(existing[field][0]) | set(choices[field]))]
            continue
        catalog[kind] = {
            "input": {"required": req},
            "output": ["*", "*", "*"],
            "output_node": kind == "SaveGLB",
        }


def make_vertex_colored_glb() -> bytes:
    positions = struct.pack("<9f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.5)
    indices = struct.pack("<3I", 0, 1, 2)
    colors = struct.pack("<9f", 0.9, 0.2, 0.1, 0.1, 0.8, 0.3, 0.2, 0.4, 0.95)
    bin_body = positions + indices + colors
    while len(bin_body) % 4:
        bin_body += b"\x00"
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(bin_body)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(indices)},
            {
                "buffer": 0,
                "byteOffset": len(positions) + len(indices),
                "byteLength": len(colors),
            },
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5125, "count": 3, "type": "SCALAR"},
            {"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC3"},
        ],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "COLOR_0": 2},
                        "indices": 1,
                        "mode": 4,
                    }
                ]
            }
        ],
    }
    json_bytes = json.dumps(doc, separators=(",", ":")).encode()
    while len(json_bytes) % 4:
        json_bytes += b" "
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_body)
    return (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(json_bytes), 0x4E4F534A)
        + json_bytes
        + struct.pack("<II", len(bin_body), 0x004E4942)
        + bin_body
    )


def test_skill_uses_host_preparation_and_preserves_manual_compatibility():
    text = (ROOT / "agent/skills/image-to-3d/SKILL.md").read_text()
    assert text.index("`prepare_mesh_generation`") < text.index("`submit_generation`")
    assert "exact returned `submit_request`" in text
    assert "`settings: {}`" in text and f'"{budget3d.FILENAME_PREFIX}"' in text
    assert (
        "`selected_models` from `get_project_capabilities` into `submit_generation`'s `model_ids`"
        in text
    )
    assert "```json" not in text


@pytest.mark.parametrize(
    "revision,turbo,steps,cfg",
    [
        (budget3d.TURBO_REVISION, True, 5, 0.0),
        (budget3d.IDOL_REVISION, False, 50, 3.0),
    ],
)
def test_budget3d_graph_binding_and_replay(revision, turbo, steps, cfg):
    adapter = plugin("image_to_3d", revision)
    raw = budget3d.graph("ref-img", turbo=turbo, budget=0.5, mode="real")
    assert raw["10"]["inputs"]["steps"] == steps
    assert raw["10"]["inputs"]["cfg"] == cfg
    assert raw["10"]["inputs"]["budget"] == 0.5
    assert raw["4"]["inputs"]["mode"] == "real"
    models = budget3d.models(turbo=turbo)
    admitted = adapter.bind_graph(
        raw, "Hero prop", "12345", {"ref-img": "artifact:ref-img"}, list(reversed(models))
    )
    replayed = adapter.bind_graph(
        admitted, "Hero prop", "12345", {"ref-img": "sub/uploaded.png"}, models
    )
    assert replayed["1"]["inputs"]["image"] == "sub/uploaded.png"
    assert replayed["10"]["inputs"]["seed"] == 12345
    assert replayed["13"]["inputs"]["seed"] == 12345


def test_budget3d_rejects_tampered_graphs_and_bad_bounds():
    for kwargs in (
        {"budget": 0.05},
        {"budget": 1.1},
        {"budget": True},
        {"mode": "studio"},
        {"steps": 0},
        {"steps": 51},
        {"num_gaussians": 32769},
        {"mesh_resolution": 385},
    ):
        with pytest.raises(ValueError):
            budget3d.graph("ref", **kwargs)
    tampered = budget3d.graph("ref")
    tampered["15"]["inputs"]["filename_prefix"] = "../escape"
    adapter = plugin("image_to_3d")
    with pytest.raises(DomainError) as exc:
        adapter.bind_graph(tampered, "Prop", "7", {"ref": "uploaded.png"}, budget3d.models())
    assert exc.value.code == "invalid_request"


def test_mesh_recipe_configuration_and_legacy_hunyuan_compatibility():
    assert Settings(
        provider_options={
            "mesh_recipes": {
                "comfy": {
                    "profile_revision": budget3d.TURBO_REVISION,
                    "budget": 0.5,
                    "mode": "real",
                    "steps": 5,
                    "num_gaussians": 131072,
                    "mesh_resolution": 256,
                }
            }
        }
    )
    assert Settings(
        provider_options={
            "mesh_profiles": {"comfy": "hunyuan3d-native-2.0-1"},
            "mesh_recipes": {"comfy": {"profile_revision": "hunyuan3d-native-2.0-1"}},
        }
    )
    assert plugin("image_to_3d", "hunyuan3d-native-2.0-1").expected_model_hashes == {
        hunyuan.FILENAME: "360bc281fc956d4acac0c3d36d5ec0ebf8cdddbf4b8892e894d12419388d479b"
    }
    for bad in (
        {"mesh_profiles": {"comfy": "unknown-3d"}},
        {"mesh_recipes": {"comfy": {"profile_revision": budget3d.TURBO_REVISION, "budget": 2.0}}},
        {
            "mesh_profiles": {"comfy": budget3d.IDOL_REVISION},
            "mesh_recipes": {"comfy": {"profile_revision": budget3d.TURBO_REVISION}},
        },
    ):
        with pytest.raises((ValueError, DomainError)):
            Settings(provider_options=bad)


def test_budget3d_capability_probe_and_end_to_end_pump(runtime):
    runtime.services.settings = replace(
        runtime.services.settings,
        services=(
            {**runtime.services.settings.services[0], "capabilities": ["image", "image_to_3d"]},
            runtime.services.settings.services[1],
        ),
    )
    runtime.provider.options["mesh_recipes"] = {
        "comfy": {"profile_revision": budget3d.TURBO_REVISION, "budget": 0.5, "steps": 5}
    }
    # Missing Budget3D nodes/models initially -> unavailable
    caps_missing = get_project_capabilities(runtime.services, PROJECT)["providers"]
    mesh_row = next(r for r in caps_missing if r["capability"] == "image_to_3d")
    assert mesh_row["status"] == "unavailable"
    assert mesh_row["default_steps"] == 5

    # Populate Budget3D nodes + models -> ready
    add_budget3d_catalog(runtime.catalog)
    caps_ready = get_project_capabilities(runtime.services, PROJECT)["providers"]
    mesh_row = next(r for r in caps_ready if r["capability"] == "image_to_3d")
    assert mesh_row["status"] == "ready"
    assert mesh_row["profile_revision"] == budget3d.TURBO_REVISION
    assert set(mesh_row["selected_models"]) == set(budget3d.models(turbo=True))
    assert "budget=0.5, 5 steps" in mesh_row["summary"]

    # Register conditioning image, workflow and prompt artefacts, then submit & pump
    artifacts = runtime.services.extension("artifacts")
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (180, 90, 40)).save(buf, format="PNG")
    wf = budget3d.recipe_graph(
        "ref-prop", budget3d.TURBO_REVISION, runtime.provider.options["mesh_recipes"]["comfy"]
    )
    promoted = [
        artifacts.promote(
            PROJECT,
            "ref-prop",
            buf.getvalue(),
            kind="image",
            purpose="reference",
            media_type="image/png",
        ),
        artifacts.promote(
            PROJECT,
            "wf-prop",
            canonical(wf),
            kind="document",
            purpose="metadata",
            media_type="application/json",
        ),
        artifacts.promote(
            PROJECT,
            "pr-prop",
            b"Isometric lantern prop",
            kind="document",
            purpose="metadata",
            media_type="text/plain; charset=utf-8",
        ),
    ]
    with runtime.store.transaction() as tx:
        for art in promoted:
            artifacts.put(tx, art)

    submitted, kind = invoke(
        runtime,
        "submit_generation",
        {
            "schema_version": "1.0",
            "job_id": "mesh-job-one",
            "project_id": PROJECT,
            "generation_task_id": TASK,
            "target_id": TASK,
            "provider_id": "comfy",
            "capability": "image_to_3d",
            "workflow_artifact_id": "wf-prop",
            "prompt_artifact_id": "pr-prop",
            "input_artifact_ids": ["ref-prop"],
            "seed": "42",
            "model_ids": mesh_row["selected_models"],
            "settings": {},
        },
        "submit-mesh-one",
    )
    assert kind == "success" and submitted["kind"] == "mesh_generation", submitted

    glb_bytes = make_vertex_colored_glb()
    inspected = inspect_glb_bytes(glb_bytes)
    assert (
        inspected["vertices"] == 3
        and inspected["triangles"] == 1
        and inspected["vertex_colors"] is True
    )

    runtime.services.resources = Resources(runtime.store)

    def registrar(caller, request):
        data = (runtime.root / request["staging_path"]).read_bytes()
        art = artifacts.promote(
            PROJECT,
            request["artifact_id"],
            data,
            kind=request["kind"],
            purpose=request["purpose"],
            media_type=request["media_type"],
        )
        with runtime.store.transaction() as tx:
            artifacts.put(tx, art)
        return art

    runtime.provider.registrar = registrar

    def transport(request):
        path = request.url.path
        if path == "/object_info":
            return httpx.Response(200, json=runtime.catalog)
        if path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if path == "/system_stats":
            return httpx.Response(200, json={"devices": [{"vram_free": 2**32}]})
        if path == "/upload/image":
            return httpx.Response(200, json={"name": "ref-prop.png", "subfolder": ""})
        if path == "/prompt":
            sent = json.loads(request.content)["prompt"]
            assert sent["10"]["inputs"]["budget"] == 0.5
            assert sent["10"]["inputs"]["seed"] == 42
            assert sent["13"]["inputs"]["seed"] == 42
            assert sent["1"]["inputs"]["image"] == "ref-prop.png"
            return httpx.Response(200, json={"prompt_id": "mesh-prompt-1"})
        if path == "/history/mesh-prompt-1":
            return httpx.Response(
                200,
                json={
                    "mesh-prompt-1": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {
                            "15": {
                                "3d": [
                                    {
                                        "filename": "proscenium-budget3d_00001_.glb",
                                        "subfolder": "mesh",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=glb_bytes)
        raise AssertionError(f"Unexpected call: {path}")

    runtime.provider.client.close()
    runtime.provider.client = httpx.Client(transport=httpx.MockTransport(transport))
    settled = runtime.provider.pump(PROJECT, "mesh-job-one")[0]
    assert settled["state"] == "succeeded", settled
    assert len(settled["artifact_ids"]) == 2
    provenance = json.loads(artifacts.ready_path(PROJECT, settled["artifact_ids"][1]).read_bytes())
    assert provenance["output_inspections"][0]["inspection"]["vertex_colors"] is True
