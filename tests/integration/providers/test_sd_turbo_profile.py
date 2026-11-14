"""Offline version/admission/conditioning checks. All HTTP output is explicitly synthetic."""

import io
import json
from dataclasses import replace

import httpx
import pytest
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.catalogue import install as install_catalogue
from proscenium.contracts import canonical
from proscenium.providers import install, register_provider_artifact
from proscenium.providers.plugins import plugin
from proscenium.providers.sd_turbo import FILENAME, REVISION, SHA256, bind, graph
from proscenium.providers.service import ProviderService


def test_versioned_selection_does_not_reinterpret_existing_or_legacy_image_jobs():
    assert plugin("image").revision == plugin("image", "comfy-image-1").revision == "comfy-image-1"
    assert plugin("image", REVISION).revision == REVISION
    assert ProviderService._plugin({"request": {"capability": "image"}}).revision == "comfy-image-1"
    assert (
        ProviderService._plugin(
            {
                "request": {"capability": "image"},
                "plugin": {"capability": "image", "revision": "comfy-image-1"},
            }
        ).revision
        == "comfy-image-1"
    )
    with pytest.raises(DomainError):
        plugin("image", "unknown-img2img-profile")


@pytest.mark.parametrize(
    "mutation",
    [
        "filename-only",
        "empty-latent",
        "no-conditioning",
        "full-denoise",
        "wrong-model",
        "extra-node",
    ],
)
def test_fixed_profile_refuses_invented_or_unconditioned_graphs(mutation):
    workflow = graph("reference")
    if mutation == "filename-only":
        workflow["reference"]["inputs"]["image"] = "reference.png"
    if mutation == "empty-latent":
        workflow["sample"]["inputs"]["latent_image"] = ["empty", 0]
    if mutation == "no-conditioning":
        workflow["guider"]["inputs"]["positive"] = ["zero", 0]
    if mutation == "full-denoise":
        workflow["sigmas"]["inputs"]["denoise"] = 1.0
    if mutation == "wrong-model":
        workflow["checkpoint"]["inputs"]["ckpt_name"] = "unconfigured.safetensors"
    if mutation == "extra-node":
        workflow["cloud"] = {"class_type": "Unknown", "inputs": {}}
    with pytest.raises(DomainError):
        bind(
            workflow,
            "An explicit current prompt",
            "1",
            {"reference": "artifact:reference"},
            [FILENAME],
        )


def test_actual_img2img_wiring_and_second_host_binding():
    bound = bind(
        graph("reference"), "Red ceramic mug", "42", {"reference": "artifact:reference"}, [FILENAME]
    )
    uploaded = bind(bound, "Red ceramic mug", "42", {"reference": "own-reference.png"}, [FILENAME])
    assert uploaded["reference"]["inputs"]["image"] == "own-reference.png"
    assert uploaded["encode"]["inputs"]["pixels"] == ["reference", 0]
    assert uploaded["sample"]["inputs"]["latent_image"] == ["encode", 0]
    assert uploaded["guider"]["inputs"]["positive"] == ["text", 0]
    assert uploaded["sigmas"]["inputs"] == {"model": ["checkpoint", 0], "steps": 4, "denoise": 0.5}
    assert not any(n["class_type"] == "ReferenceLatent" for n in uploaded.values())


def test_real_f00_budget_freeze_bridge_and_synthetic_http_execution(services, caller):
    install_catalogue(services)
    services.settings = replace(
        services.settings,
        services=(
            dict(
                provider_id="sd-offline",
                endpoint="http://sd-fixture.invalid:18589",
                capabilities=["image"],
                model_id=None,
                available=None,
            ),
        ),
        provider_options={"image_profiles": {"sd-offline": REVISION}},
    )
    services.gate.resume("project-test", reconciliation_evidence="explicit offline fixture")
    caller = replace(caller, operations=caller.operations + ("submitGenerationJob",))
    artifacts = services.extension("artifacts")
    image = io.BytesIO()
    Image.new("RGB", (512, 512), "red").save(image, format="PNG")
    workflow = graph("sd-reference")
    for identity, data, kind, media in [
        ("sd-reference", image.getvalue(), "image", "image/png"),
        ("sd-graph", canonical(workflow), "document", "application/json"),
        ("sd-prompt", b"Red ceramic mug", "document", "text/plain"),
    ]:
        artifact = artifacts.promote(
            "project-test",
            identity,
            data,
            kind=kind,
            purpose="reference" if kind == "image" else "metadata",
            media_type=media,
        )
        with services.registry.project_store("project-test").transaction() as tx:
            artifacts.put(tx, artifact)
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put("copilot.effective", "request-test", {"generation_attempt_limit": 2})
    outputs = {
        "CheckpointLoaderSimple": ["MODEL", "CLIP", "VAE"],
        "LoadImage": ["IMAGE", "MASK"],
        "VAEEncode": ["LATENT"],
        "CLIPTextEncode": ["CONDITIONING"],
        "ConditioningZeroOut": ["CONDITIONING"],
        "CFGGuider": ["GUIDER"],
        "RandomNoise": ["NOISE"],
        "KSamplerSelect": ["SAMPLER"],
        "SDTurboScheduler": ["SIGMAS"],
        "SamplerCustomAdvanced": ["LATENT", "LATENT"],
        "VAEDecode": ["IMAGE"],
        "SaveImage": [],
    }
    catalog = {}
    for node in workflow.values():
        required = {}
        for key, value in node["inputs"].items():
            if isinstance(value, list):
                spec = [outputs[workflow[value[0]]["class_type"]][value[1]], {}]
            elif key == "ckpt_name":
                spec = [[FILENAME]]
            elif key == "noise_seed":
                spec = ["INT", {}]
            else:
                spec = [
                    "STRING"
                    if isinstance(value, str)
                    else "FLOAT"
                    if isinstance(value, float)
                    else "INT",
                    {},
                ]
            required[key] = spec
        catalog[node["class_type"]] = {
            "input": {"required": required},
            "output": outputs[node["class_type"]],
            "output_node": node["class_type"] == "SaveImage",
        }
    requests = []

    def http(request):
        requests.append(request)
        path = request.url.path
        if path == "/object_info":
            return httpx.Response(200, json=catalog)
        if path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if path == "/system_stats":
            return httpx.Response(200, json={"devices": [{"vram_free": 10**10}]})
        if path == "/upload/image":
            return httpx.Response(200, json={"name": "own-reference.png"})
        if path == "/prompt":
            assert json.loads(request.content)["prompt"]["sample"]["inputs"]["latent_image"] == [
                "encode",
                0,
            ]
            return httpx.Response(200, json={"prompt_id": "synthetic-sd-prompt"})
        if path.startswith("/history/"):
            return httpx.Response(
                200,
                json={
                    "synthetic-sd-prompt": {
                        "status": {"completed": True},
                        "outputs": {"save": {"images": [{"filename": "synthetic.png"}]}},
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=image.getvalue())
        raise AssertionError(path)

    with httpx.Client(transport=httpx.MockTransport(http)) as client:
        adapter = install(
            services,
            client=client,
            registrar=lambda c, b: register_provider_artifact(services, c, b),
        )
        command = dict(
            schema_version="1.0",
            job_id="sd-offline-job",
            project_id="project-test",
            generation_task_id="task-test",
            target_id="task-test",
            provider_id="sd-offline",
            capability="image",
            workflow_artifact_id="sd-graph",
            prompt_artifact_id="sd-prompt",
            input_artifact_ids=["sd-reference"],
            seed="42",
            model_ids=[FILENAME],
            settings={},
        )
        job, _ = adapter.submit(caller, command)
        frozen = services.jobs.inputs("project-test", job["job_id"])
        assert frozen["plugin"]["revision"] == REVISION and frozen["plugin"][
            "expected_model_hashes"
        ] == {FILENAME: SHA256}
        services.settings = replace(services.settings, provider_options={})
        assert adapter._plugin(frozen).revision == REVISION
        result = adapter.pump("project-test")[0]
        assert result["state"] == "succeeded"
        assert adapter.submit(caller, command)[1]
        assert sum(r.method == "POST" and r.url.path == "/prompt" for r in requests) == 1
        assert all(r.url.host == "sd-fixture.invalid" for r in requests)
        provenance = json.loads(
            artifacts.ready_path("project-test", result["artifact_ids"][-1]).read_bytes()
        )
        assert "img2img" in provenance["inputs"]["plugin"]["conditioning_method"]
        assert provenance["derived_images"]
