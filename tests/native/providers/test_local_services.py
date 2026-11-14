"""Bounded W07 live checks. Explicit opt-in, no lifecycle/model-loading endpoints."""

import io
import os

import httpx
import pytest
from PIL import Image
from proscenium.providers.comfy import bind_graph, preflight
from proscenium.providers.klein import graph
from proscenium.providers.service import sha
from proscenium.providers.vlm import VLMClient

pytestmark = pytest.mark.native


def configured(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"Explicit {name} allocation is required")
    return value.rstrip("/")


def test_comfy_read_only_schema_preflight():
    base = configured("PROSCENIUM_W07_COMFY_ENDPOINT")
    models = ["flux-2-klein-4b.safetensors", "qwen_3_4b.safetensors", "flux2-vae.safetensors"]
    with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
        response = client.get(base + "/object_info")
        response.raise_for_status()
        catalog = response.json()
        for reference in (None, "fixture-reference"):
            workflow = graph(
                unet=models[0],
                clip=models[1],
                vae=models[2],
                width=64,
                height=64,
                reference_artifact_id=reference,
            )
            bound = bind_graph(
                workflow,
                "A red square",
                "42",
                {reference: "artifact:" + reference} if reference else {},
            )
            preflight(bound, catalog, models)
        queue = client.get(base + "/queue")
        queue.raise_for_status()
        assert {"queue_running", "queue_pending"} <= queue.json().keys()
    print("Measured live schema preflight only. No uploads, inference or downloads.")


def test_one_already_loaded_vlm_64px_max512_tokens():
    base = configured("PROSCENIUM_W07_VLM_ENDPOINT")
    with httpx.Client(timeout=120, trust_env=False, follow_redirects=False) as client:
        response = client.get(base + "/api/v1/models")
        response.raise_for_status()
        models = [
            m
            for m in response.json()["models"]
            if m.get("capabilities", {}).get("vision") and m.get("loaded_instances")
        ]
        if len(models) != 1:
            pytest.skip(
                "No single already-loaded vision-capable model. No model load is authorised."
            )
        model = models[0]["loaded_instances"][0]["id"]
        output = io.BytesIO()
        Image.new("RGB", (64, 64), "red").save(output, format="PNG")
        image = output.getvalue()
        request = dict(
            schema_version="1.0",
            review_id="live-review",
            project_id="live-fixture",
            targets=[
                dict(
                    origin="reference",
                    artifact_id="live-red-square",
                    displayed_sha256=sha(image),
                    dimensions={"width": 64, "height": 64},
                    item_id=None,
                    version_id=None,
                    shot_id=None,
                    render_job_id=None,
                    source_frame=None,
                    media_artifact_id=None,
                )
            ],
            criteria="Describe only the visible colour and shape. At most one finding.",
            telemetry_artifact_ids=[],
        )
        result = VLMClient(base, client).review(model, request, [image], {}, max_tokens=512)
        assert len(result) <= 1
        print(
            f"Measured structured VLM response: model={model}, findings={len(result)}, image_sha256={sha(image)}"
        )
