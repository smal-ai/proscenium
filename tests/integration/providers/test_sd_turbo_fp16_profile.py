"""CPU/offline admission checks for the owned FP16 precision derivative, no inference."""

import io
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.catalogue import install as install_catalogue
from proscenium.contracts import canonical
from proscenium.providers import sd_turbo, sd_turbo_fp16
from proscenium.providers.plugins import plugin
from proscenium.providers.service import ProviderService

EVIDENCE = Path(__file__).parents[3] / "docs/evidence/m07-fp16"


def test_old_profiles_remain_exact_and_new_profile_is_explicit_only():
    assert plugin("image").revision == plugin("image", "comfy-image-1").revision == "comfy-image-1"
    assert plugin("image", sd_turbo.REVISION).expected_model_hashes == {
        sd_turbo.FILENAME: sd_turbo.SHA256
    }
    assert plugin("image", sd_turbo_fp16.REVISION).expected_model_hashes == {
        sd_turbo_fp16.FILENAME: sd_turbo_fp16.SHA256
    }
    assert ProviderService._plugin({"request": {"capability": "image"}}).revision == "comfy-image-1"
    assert plugin("image_to_3d").revision == "hunyuan3d-native-2.0-1"


@pytest.mark.parametrize(
    "mutation", ["f32-file", "full-denoise", "wrong-latent", "wrong-prompt", "other-model-list"]
)
def test_fp16_binding_never_reinterprets_another_file_or_changes_conditioning(mutation):
    workflow = sd_turbo_fp16.graph("ref")
    models = [sd_turbo_fp16.FILENAME]
    if mutation == "f32-file":
        workflow["checkpoint"]["inputs"]["ckpt_name"] = sd_turbo.FILENAME
    if mutation == "full-denoise":
        workflow["sigmas"]["inputs"]["denoise"] = 1
    if mutation == "wrong-latent":
        workflow["sample"]["inputs"]["latent_image"] = ["empty", 0]
    if mutation == "wrong-prompt":
        workflow["text"]["inputs"]["text"] = "Unbound text"
    if mutation == "other-model-list":
        models = [sd_turbo.FILENAME]
    with pytest.raises(DomainError):
        sd_turbo_fp16.bind(workflow, "Owner prompt", "1", {"ref": "artifact:ref"}, models)


def test_new_profile_keeps_exact_img2img_method():
    bound = sd_turbo_fp16.bind(
        sd_turbo_fp16.graph("ref"),
        "Owner prompt",
        "42",
        {"ref": "uploaded.png"},
        [sd_turbo_fp16.FILENAME],
    )
    assert bound["checkpoint"]["inputs"]["ckpt_name"] == sd_turbo_fp16.FILENAME
    assert bound["reference"]["inputs"]["image"] == "uploaded.png"
    assert bound["encode"]["inputs"]["pixels"] == ["reference", 0]
    assert bound["sample"]["inputs"]["latent_image"] == ["encode", 0]
    assert bound["guider"]["inputs"]["positive"] == ["text", 0]
    assert bound["sigmas"]["inputs"]["steps"] == 4 and bound["sigmas"]["inputs"]["denoise"] == 0.5


def test_admission_freezes_actual_model_hash_and_conversion_provenance(services, caller):
    install_catalogue(services)
    services.settings = replace(
        services.settings,
        services=(
            dict(
                provider_id="fp16-offline",
                endpoint="http://offline.invalid:18589",
                capabilities=["image"],
                model_id=None,
                available=None,
            ),
        ),
        provider_options={"image_profiles": {"fp16-offline": sd_turbo_fp16.REVISION}},
    )
    services.gate.resume(
        "project-test", reconciliation_evidence="explicit offline test, not native execution"
    )
    caller = replace(caller, operations=caller.operations + ("submitGenerationJob",))
    artifacts = services.extension("artifacts")
    image = io.BytesIO()
    Image.new("RGB", (512, 512), "red").save(image, format="PNG")
    for identity, data, kind, media in [
        ("ref", image.getvalue(), "image", "image/png"),
        ("graph", canonical(sd_turbo_fp16.graph("ref")), "document", "application/json"),
        ("prompt", b"Owner prompt", "document", "text/plain"),
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
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("No network effect during admission")
        )
    ) as client:
        adapter = ProviderService(services, client=client)
        body = dict(
            schema_version="1.0",
            job_id="fp16-offline-job",
            project_id="project-test",
            generation_task_id="task-test",
            target_id="task-test",
            provider_id="fp16-offline",
            capability="image",
            workflow_artifact_id="graph",
            prompt_artifact_id="prompt",
            input_artifact_ids=["ref"],
            seed="42",
            model_ids=[sd_turbo_fp16.FILENAME],
            settings={},
        )
        job, replayed = adapter.submit(caller, body)
        assert not replayed and job["state"] == "planned"
        inputs = services.jobs.inputs("project-test", job["job_id"])
        assert inputs["plugin"]["revision"] == sd_turbo_fp16.REVISION
        assert inputs["plugin"]["expected_model_hashes"] == {
            sd_turbo_fp16.FILENAME: sd_turbo_fp16.SHA256
        }
        assert inputs["plugin"]["model_source"] == sd_turbo_fp16.MODEL_SOURCE
        assert (
            inputs["plugin"]["model_source"]["conversion"]["converter_sha256"]
            == sd_turbo_fp16.CONVERTER_SHA256
        )
        assert inputs["references"]["ref"]["dimensions"] == {"width": 512, "height": 512}
        services.settings = replace(services.settings, provider_options={})
        assert adapter._plugin(inputs).revision == sd_turbo_fp16.REVISION
        assert adapter.submit(caller, body)[1]


def test_actual_conversion_report_matches_frozen_profile():
    raw = (EVIDENCE / "conversion-report.json").read_bytes()
    import hashlib

    report = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == sd_turbo_fp16.CONVERSION_REPORT_SHA256
    assert (
        report["output_sha256"] == sd_turbo_fp16.SHA256
        and report["output_bytes"] == sd_turbo_fp16.BYTES
    )
    assert report["source_sha256"] == sd_turbo.SHA256 and report["original_hash_unchanged_after"]
    assert (
        report["metadata_preserved"]
        and report["complete_offset_coverage"]
        and report["full_streaming_readback_validated"]
    )
    assert report["peak_rss_bytes"] < 1024**3 and report["elapsed_seconds"] <= 300
    assert report["minimum_host_mem_available_bytes"] >= 4 * 1024**3
    assert (
        not report["torch_imported"]
        and report["model_initialisations"] == report["gpu_inferences"] == 0
    )
    assert (
        report["fit_floor_bytes"]
        == max(12 * 1024**3, report["output_bytes"] + 8 * 1024**3)
        == 12_884_901_888
    )
