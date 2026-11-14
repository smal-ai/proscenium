"""Read and verify a retained live proof. Never submits inference or calls any service."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from PIL import Image
from proscenium.contracts import canonical, validate


@pytest.mark.native
def test_real_comfy_conditioning_and_immutable_png_receipts():
    value = os.environ.get("PROSCENIUM_W07_GENERATION_RECEIPT")
    if not value:
        pytest.skip("Supply an actual retained proof report, not a synthetic fixture")
    report_path = Path(value)
    report = json.loads(report_path.read_bytes())
    job = report["job"]
    validate("JobRecord", job)
    if job["state"] != "succeeded":
        pytest.skip(
            "Retained proof did not execute successfully. Do not invent an output PNG receipt."
        )
    assert job["state"] == "succeeded" and job["availability"] == "complete"
    assert report["command"]["model_ids"] == [
        "flux-2-klein-4b.safetensors",
        "qwen_3_4b.safetensors",
        "flux2-vae.safetensors",
    ]
    graph = report["execution"]["graph"]
    assert graph["latent"]["inputs"]["width"] == graph["latent"]["inputs"]["height"] == 64
    assert graph["sigmas"]["inputs"]["steps"] == 4
    assert graph["reference"]["class_type"] == "LoadImage"
    assert not graph["reference"]["inputs"]["image"].startswith("artifact:")
    assert graph["reference_encode"]["inputs"]["pixels"] == ["reference", 0]
    assert graph["reference_positive"]["inputs"]["latent"] == ["reference_encode", 0]
    assert graph["guider"]["inputs"]["positive"] == ["reference_positive", 0]
    assert report["execution"]["prompt_id"] == job["external_job_id"]
    assert hashlib.sha256(canonical(graph)).hexdigest() == report["execution"]["graph_sha256"]
    assert len(report["reservations"]) <= 2
    assert any(
        r["id"] == job["job_id"] and r["state"] == "consumed" for r in report["reservations"]
    )
    images = []
    for artifact in report["artifacts"]:
        validate("Artifact", artifact)
        path = (
            report_path.parent
            / "project/.proscenium/artefacts/sha256"
            / artifact["sha256"]
            / "content"
        )
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
        assert path.stat().st_mode & 0o222 == 0
        if artifact["kind"] == "image":
            with Image.open(path) as image:
                assert image.format == "PNG" and image.size == (64, 64)
                image.verify()
            images.append(artifact)
    assert images
    assert report["execution"]["derived_images"]


@pytest.mark.native
def test_retained_memory_blocker_has_no_inference_or_fabricated_output():
    value = os.environ.get("PROSCENIUM_W07_GENERATION_RECEIPT")
    if not value:
        pytest.skip("Supply an actual retained proof report")
    report_path = Path(value)
    report = json.loads(report_path.read_bytes())
    if report["job"]["state"] == "succeeded":
        pytest.skip("A successful proof has no pre-submit resource blocker")
    assert report["job"]["state"] == "planned"
    assert report["job"]["external_job_id"] is None
    assert report["execution"] == {} and report["artifacts"] == []
    assert report["reference"]["dimensions"] == {"width": 64, "height": 64}
    assert len(report["reservations"]) == 1 and report["reservations"][0]["state"] == "reserved"
    assert report["observations"]
    assert all(
        o["mem_available_bytes"] < 16_132_301_316 + 4 * 1024**3 for o in report["observations"]
    )
    reference = report["reference"]
    path = (
        report_path.parent
        / "project/.proscenium/artefacts/sha256"
        / reference["sha256"]
        / "content"
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"]
    with Image.open(path) as image:
        assert image.size == (64, 64) and image.format == "PNG"
        image.verify()
