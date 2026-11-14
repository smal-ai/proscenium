"""Synthetic provider doubles. No live service, inference or GPU access."""

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.contracts import validate
from proscenium.providers import bind_generation_task, install
from proscenium.providers.klein import graph
from proscenium.providers.service import sha
from proscenium.reviews import install as install_reviews


def png(colour="red"):
    stream = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(stream, format="PNG")
    return stream.getvalue()


class Artifacts:
    def __init__(self, services):
        self.services = services
        self.root = Path(services.registry.get("project-test")["path"])
        self.paths = {}

    def add(self, identity, data, *, image=False):
        path = self.root / "fixtures" / identity
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        record = dict(
            schema_version="1.0",
            artifact_id=identity,
            project_id="project-test",
            kind="image" if image else "document",
            purpose="reference" if image else "metadata",
            sha256=sha(data),
            bytes=len(data),
            media_type="image/png" if image else "application/json",
            state="ready",
            derived_from=[],
        )
        if image:
            record["dimensions"] = {"width": 64, "height": 64}
        validate("Artifact", record)
        with self.services.registry.project_store("project-test").transaction() as tx:
            tx.put("artifacts", identity, record, schema="Artifact")
        self.paths[identity] = path
        return record

    def get(self, project_id, artifact_id):
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            return tx.require("artifacts", artifact_id)

    def ready_path(self, project_id, artifact_id):
        self.get(project_id, artifact_id)
        return self.paths[artifact_id]

    def register(self, caller, request):
        validate("ArtifactRegister", request)
        assert caller.channel == "worker"
        path = self.root / request["staging_path"]
        data = path.read_bytes()
        assert sha(data) == request["sha256"]
        record = dict(
            schema_version="1.0",
            project_id=request["project_id"],
            artifact_id=request["artifact_id"],
            kind=request["kind"],
            purpose=request["purpose"],
            sha256=sha(data),
            bytes=len(data),
            media_type=request["media_type"],
            state="ready",
            derived_from=request.get("derived_from", []),
        )
        if record["kind"] == "image":
            with Image.open(path) as image:
                record["dimensions"] = {"width": image.width, "height": image.height}
        with self.services.registry.project_store(request["project_id"]).transaction() as tx:
            previous = tx.get("artifacts", request["artifact_id"], required=False)
            if previous is not None:
                if previous != record:
                    raise DomainError("idempotency_conflict", 409)
            else:
                tx.put("artifacts", request["artifact_id"], record, schema="Artifact")
        self.paths[request["artifact_id"]] = path
        return record


class ProviderHTTP:
    def __init__(self, graph):
        self.calls: list[httpx.Request] = []
        self.graph = graph
        self.queue_busy = False
        self.timeout_submit = False
        self.accept_before_timeout = False
        self.reject_submit = False
        self.prompt_id = None
        self.client_id = None
        self.completed = True
        self.vlm_result: Any = {
            "findings": [
                {
                    "target_index": 0,
                    "observation": "A red square is visible.",
                    "recommendation": "",
                    "evidence": [
                        {"kind": "visual", "artifact_id": "image-test", "description": "Red pixels"}
                    ],
                }
            ]
        }
        self.vlm_timeout = False
        self.vision_loaded = True
        self.unavailable = False

    def catalog(self):
        # Schema double is constructed explicitly enough to catch wrong model and broken links.
        outputs = {
            "UNETLoader": ["MODEL"],
            "CLIPLoader": ["CLIP"],
            "VAELoader": ["VAE"],
            "CLIPTextEncode": ["CONDITIONING"],
            "ConditioningZeroOut": ["CONDITIONING"],
            "EmptyFlux2LatentImage": ["LATENT"],
            "Flux2Scheduler": ["SIGMAS"],
            "CFGGuider": ["GUIDER"],
            "KSamplerSelect": ["SAMPLER"],
            "RandomNoise": ["NOISE"],
            "SamplerCustomAdvanced": ["LATENT"],
            "VAEDecode": ["IMAGE"],
            "SaveImage": [],
            "LoadImage": ["IMAGE", "MASK"],
            "VAEEncode": ["LATENT"],
            "ReferenceLatent": ["CONDITIONING"],
        }
        result = {}
        for node in self.graph.values():
            required = {}
            for key, value in node["inputs"].items():
                if isinstance(value, list):
                    required[key] = [outputs[self.graph[value[0]]["class_type"]][value[1]], {}]
                elif key in {"unet_name", "clip_name", "vae_name"}:
                    required[key] = [[value], {}]
                elif key in {"seed", "noise_seed"}:
                    required[key] = ["INT", {}]
                elif key == "sampler_name":
                    required[key] = ["COMBO", {"options": ["euler"]}]
                else:
                    required[key] = [
                        "STRING"
                        if isinstance(value, str)
                        else "FLOAT"
                        if isinstance(value, float)
                        else "INT",
                        {},
                    ]
            result[node["class_type"]] = {
                "input": {"required": required},
                "output": outputs[node["class_type"]],
                "output_node": node["class_type"] == "SaveImage",
            }
        return result

    def __call__(self, request):
        self.calls.append(request)
        if self.unavailable:
            raise httpx.ConnectError("synthetic unavailable", request=request)
        path = request.url.path
        if path == "/object_info":
            return httpx.Response(200, json=self.catalog())
        if path == "/system_stats":
            return httpx.Response(200, json={"devices": [{"vram_free": 10**10}]})
        if path == "/queue":
            entries = (
                [[1, self.prompt_id, {}, {"client_id": self.client_id}, []]]
                if self.prompt_id
                else []
            )
            return httpx.Response(
                200,
                json={
                    "queue_running": entries if not self.completed else [],
                    "queue_pending": [[1]] if self.queue_busy else [],
                },
            )
        if path == "/upload/image":
            return httpx.Response(200, json={"name": "conditioning.png", "subfolder": "project"})
        if path == "/prompt":
            body = json.loads(request.content)
            self.client_id = body["client_id"]
            if self.reject_submit:
                return httpx.Response(400, json={"error": "synthetic rejection"})
            if not self.timeout_submit or self.accept_before_timeout:
                self.prompt_id = "prompt-test"
            if self.timeout_submit:
                raise httpx.ReadTimeout("synthetic secret must not escape", request=request)
            return httpx.Response(200, json={"prompt_id": self.prompt_id})
        if path.startswith("/history"):
            record: dict[str, Any] = {
                "prompt": [1, self.prompt_id, {}, {"client_id": self.client_id}, []],
                "status": {"completed": self.completed, "status_str": "success"},
                "outputs": {"save": {"images": [{"filename": "output.png"}]}},
            }
            return httpx.Response(200, json={self.prompt_id: record} if self.prompt_id else {})
        if path == "/view":
            return httpx.Response(200, content=png())
        if path == "/v1/chat/completions":
            if self.vlm_timeout:
                raise httpx.ReadTimeout("synthetic VLM timeout", request=request)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(self.vlm_result)},
                        }
                    ]
                },
            )
        if path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "capabilities": {"vision": self.vision_loaded},
                            "loaded_instances": [{"id": "synthetic-vision"}],
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected synthetic provider operation: {request.method} {path}")


@pytest.fixture
def rig(services, caller):
    services.settings = replace(
        services.settings,
        services=(
            dict(
                provider_id="comfy-test",
                endpoint="http://provider-a.test:8188",
                capabilities=["image"],
                model_id=None,
                available=None,
            ),
            dict(
                provider_id="vlm-test",
                endpoint="http://vision.test:1234",
                capabilities=["vlm"],
                model_id="synthetic-vision",
                available=None,
            ),
        ),
    )
    caller = replace(
        caller,
        operations=caller.operations
        + ("submitGenerationJob", "requestToolReview", "completeToolReview"),
    )
    services.gate.resume("project-test", reconciliation_evidence="synthetic fixture")
    artifacts = Artifacts(services)
    services.extensions["artifacts"] = artifacts
    install_reviews(services)
    workflow = graph(
        unet="klein.safetensors",
        clip="qwen.safetensors",
        vae="flux2.safetensors",
        width=64,
        height=64,
    )
    artifacts.add("workflow-test", json.dumps(workflow).encode())
    artifacts.add("prompt-test", b"One red square")
    image = artifacts.add("image-test", png(), image=True)
    with services.registry.project_store("project-test").transaction() as tx:
        bind_generation_task(tx, "task-test", 3, ["target-test"])
    http = ProviderHTTP(workflow)
    transport = httpx.Client(transport=httpx.MockTransport(http), trust_env=False)
    adapter = install(services, registrar=artifacts.register, client=transport)
    yield services, caller, artifacts, http, adapter, image
    transport.close()


@pytest.fixture
def generation():
    return dict(
        schema_version="1.0",
        job_id="generation-test",
        project_id="project-test",
        generation_task_id="task-test",
        target_id="target-test",
        provider_id="comfy-test",
        capability="image",
        workflow_artifact_id="workflow-test",
        prompt_artifact_id="prompt-test",
        input_artifact_ids=[],
        seed="42",
        model_ids=["klein.safetensors", "qwen.safetensors", "flux2.safetensors"],
        settings={},
    )


@pytest.fixture
def review(rig):
    image = rig[-1]
    return dict(
        schema_version="1.0",
        review_id="review-test",
        project_id="project-test",
        targets=[
            dict(
                origin="reference",
                artifact_id="image-test",
                displayed_sha256=image["sha256"],
                dimensions=image["dimensions"],
                item_id=None,
                version_id=None,
                shot_id=None,
                render_job_id=None,
                source_frame=None,
                media_artifact_id=None,
            )
        ],
        criteria="Describe visible colour only",
        telemetry_artifact_ids=[],
    )
