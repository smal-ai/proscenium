"""Explicit FLUX.2 Klein API graph, optionally conditioned on one project image.

Adapted from the pinned reference template. Treatment, model filenames and size are supplied
by the current task, not inherited from the reference project. Live preflight is mandatory.
"""

from typing import Any

from proscenium.api.errors import DomainError


def graph(
    *,
    unet: str,
    clip: str,
    vae: str,
    width: int,
    height: int,
    reference_artifact_id: str | None = None,
    steps: int = 4,
) -> dict[str, Any]:
    if not (
        64 <= width <= 4096
        and 64 <= height <= 4096
        and width % 16 == height % 16 == 0
        and 1 <= steps <= 50
    ):
        raise DomainError("invalid_request")
    result: dict[str, Any] = {
        "model": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet, "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": clip, "type": "flux2", "device": "default"},
        },
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "text": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["clip", 0], "text": "$prompt"},
        },
        "zero": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["text", 0]}},
        "latent": {
            "class_type": "EmptyFlux2LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "sigmas": {
            "class_type": "Flux2Scheduler",
            "inputs": {"width": width, "height": height, "steps": steps},
        },
        "guider": {
            "class_type": "CFGGuider",
            "inputs": {
                "model": ["model", 0],
                "positive": ["text", 0],
                "negative": ["zero", 0],
                "cfg": 1.0,
            },
        },
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": "$seed"}},
        "sample": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["noise", 0],
                "guider": ["guider", 0],
                "sampler": ["sampler", 0],
                "sigmas": ["sigmas", 0],
                "latent_image": ["latent", 0],
            },
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]},
        },
        "save": {
            "class_type": "SaveImage",
            "inputs": {"images": ["decode", 0], "filename_prefix": "proscenium/generated"},
        },
    }
    if reference_artifact_id:
        result.update(
            {
                "reference": {
                    "class_type": "LoadImage",
                    "inputs": {"image": "artifact:" + reference_artifact_id},
                },
                "reference_encode": {
                    "class_type": "VAEEncode",
                    "inputs": {"pixels": ["reference", 0], "vae": ["vae", 0]},
                },
                "reference_positive": {
                    "class_type": "ReferenceLatent",
                    "inputs": {"conditioning": ["text", 0], "latent": ["reference_encode", 0]},
                },
                "reference_negative": {
                    "class_type": "ReferenceLatent",
                    "inputs": {"conditioning": ["zero", 0], "latent": ["reference_encode", 0]},
                },
            }
        )
        result["guider"]["inputs"].update(
            positive=["reference_positive", 0], negative=["reference_negative", 0]
        )
    return result
