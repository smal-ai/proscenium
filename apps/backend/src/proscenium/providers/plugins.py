"""Host-owned, versioned pure capability plugins. ProviderService alone executes jobs."""

import io
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from PIL import Image

from proscenium.api.errors import DomainError
from proscenium.providers import hunyuan, sd_turbo, sd_turbo_fp16
from proscenium.providers.comfy import bind_graph, preflight
from proscenium.providers.mesh import inspect_glb_bytes

JSON = dict[str, Any]


@dataclass(frozen=True)
class ArtifactFormat:
    kind: str
    media_type: str
    suffix: str
    purpose: str


IMAGE = ArtifactFormat("image", "image/png", "png", "concept")
GLB = ArtifactFormat("source_blob", "model/gltf-binary", "glb", "concept")
METADATA = ArtifactFormat("document", "application/json", "json", "metadata")


class GenerationCapabilityPlugin(Protocol):
    capability: str
    revision: str
    job_kind: str
    output_format: ArtifactFormat
    maximum_output_bytes: int
    expected_model_hashes: dict[str, str]

    def bind_graph(
        self,
        workflow: JSON,
        prompt: str,
        seed: str | None,
        references: dict[str, str],
        models: list[str],
    ) -> JSON: ...
    def preflight(self, graph: JSON, catalog: JSON, models: list[str]) -> None: ...
    def output_descriptors(self, history: JSON) -> list[JSON]: ...
    def inspect_output(self, data: bytes, maximum_pixels: int) -> JSON: ...


class ImagePlugin:
    capability = "image"
    revision = "comfy-image-1"
    job_kind = "image_generation"
    output_format = IMAGE
    maximum_output_bytes = 256 * 1024**2
    expected_model_hashes: dict[str, str] = {}

    def bind_graph(self, workflow, prompt, seed, references, models):
        return bind_graph(workflow, prompt, seed, references)

    def preflight(self, graph, catalog, models):
        preflight(graph, catalog, models)

    def output_descriptors(self, history):
        return [
            item
            for output in history.get("outputs", {}).values()
            for item in output.get("images", [])
        ]

    def inspect_output(self, data, maximum_pixels):
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.width * image.height > maximum_pixels:
                raise DomainError("unsupported_media_type", 415)
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            if width >= 64 and height >= 64:
                px = rgb.tobytes()
                stride = width * 3
                step_y = max(1, height // 128)
                step_x = max(1, width // 128)
                h_diff = v_diff = count = 0
                for y in range(0, height - 1, step_y):
                    row = y * stride
                    next_row = (y + 1) * stride
                    for x in range(0, width - 1, step_x):
                        i = row + x * 3
                        j = i + 3
                        k = next_row + x * 3
                        h_diff += sum(abs(px[i + c] - px[j + c]) for c in range(3))
                        v_diff += sum(abs(px[i + c] - px[k + c]) for c in range(3))
                        count += 3
                if count:
                    mean_h, mean_v = h_diff / count, v_diff / count
                    if mean_v >= 20.0 or (mean_v >= 10.0 and mean_v / max(mean_h, 0.01) >= 3.0):
                        raise DomainError(
                            "corrupt_provider_output",
                            422,
                            "Provider returned a corrupted scanline or noise image",
                        )
        return {"sha256_verified_during_registration": True}


class HunyuanPlugin:
    capability = "image_to_3d"
    revision = "hunyuan3d-native-2.0-1"
    job_kind = "mesh_generation"
    output_format = GLB
    maximum_output_bytes = 256 * 1024**2
    expected_model_hashes = {
        hunyuan.FILENAME: "360bc281fc956d4acac0c3d36d5ec0ebf8cdddbf4b8892e894d12419388d479b",
    }

    def bind_graph(self, workflow, prompt, seed, references, models):
        if len(references) != 1 or models != [hunyuan.FILENAME] or seed is None:
            raise DomainError(
                "invalid_request", message="One Hunyuan image and exact model required"
            )
        identity = next(iter(references))
        # Text is deliberately not a conditioning input to this shape model.
        return hunyuan.bind(workflow, identity, references[identity], seed)

    def preflight(self, graph, catalog, models):
        preflight(graph, catalog, models)

    def output_descriptors(self, history):
        return hunyuan.output_files(history)

    def inspect_output(self, data, maximum_pixels):
        return inspect_glb_bytes(data)


class SDTurboImg2ImgPlugin(ImagePlugin):
    revision = sd_turbo.REVISION
    expected_model_hashes = {sd_turbo.FILENAME: sd_turbo.SHA256}
    model_source: dict[str, Any] = {
        "repository": "stabilityai/sd-turbo",
        "revision": "b261bac6fd2cf515557d5d0707481eafa0485ec2",
        "filename": sd_turbo.FILENAME,
        "sha256": sd_turbo.SHA256,
        "licence": "Stability AI Community License, 2024-07-05",
    }
    reference_dimensions = {"width": 512, "height": 512}
    conditioning_method = (
        "VAE-encoded latent img2img, SDTurboScheduler denoise=0.5, 4 steps, cfg=1, no resize"
    )

    def bind_graph(self, workflow, prompt, seed, references, models):
        return sd_turbo.bind(workflow, prompt, seed, references, models)

    def inspect_output(self, data, maximum_pixels):
        result = super().inspect_output(data, maximum_pixels)
        with Image.open(io.BytesIO(data)) as image:
            if image.size != (512, 512):
                raise DomainError("image_dimensions_exceeded", 413)
        return {
            **result,
            "dimensions": self.reference_dimensions,
            "conditioning_method": self.conditioning_method,
        }


class SDTurboFP16Img2ImgPlugin(SDTurboImg2ImgPlugin):
    revision = sd_turbo_fp16.REVISION
    expected_model_hashes = {sd_turbo_fp16.FILENAME: sd_turbo_fp16.SHA256}
    model_source = sd_turbo_fp16.MODEL_SOURCE

    def bind_graph(self, workflow, prompt, seed, references, models):
        return sd_turbo_fp16.bind(workflow, prompt, seed, references, models)


# No model/request/graph field can register a plugin, nominate Python or choose a revision.
PLUGINS: dict[str, GenerationCapabilityPlugin] = {
    "image": ImagePlugin(),
    "image_to_3d": HunyuanPlugin(),
}
REGISTRY = MappingProxyType(PLUGINS)
IMAGE_REVISIONS = MappingProxyType(
    {
        "comfy-image-1": PLUGINS["image"],
        sd_turbo.REVISION: SDTurboImg2ImgPlugin(),
        sd_turbo_fp16.REVISION: SDTurboFP16Img2ImgPlugin(),
    }
)


def plugin(capability: str, revision: str | None = None) -> GenerationCapabilityPlugin:
    result = (
        IMAGE_REVISIONS.get(revision)
        if capability == "image" and revision is not None
        else REGISTRY.get(capability)
    )
    if result is None or (revision is not None and revision != result.revision):
        raise DomainError("capability_unavailable", 501, "Frozen capability plugin is unavailable")
    return result
