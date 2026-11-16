"""Explicit FLUX.2 Klein API graph, optionally conditioned on one project image.

Adapted from the pinned reference template. Treatment, model filenames and size are supplied
by the current task, not inherited from the reference project. Live preflight is mandatory.
"""

from typing import Any

from proscenium.api.errors import DomainError

VALID_VAE_DTYPES = {"default", "fp32", "fp16", "bf16"}


def graph(
    *,
    unet: str,
    clip: str,
    vae: str,
    width: int,
    height: int,
    reference_artifact_id: str | None = None,
    steps: int = 4,
    tiled_vae: bool = False,
    vae_dtype: str | None = None,
    reference_megapixels: float = 1.0,
) -> dict[str, Any]:
    if not (64 <= width <= 4096 and 64 <= height <= 4096 and width % 16 == height % 16 == 0):
        raise DomainError(
            "invalid_request",
            message="Image width and height must be multiples of 16 between 64 and 4096",
        )
    if (
        not 1 <= steps <= 50
        or not 0.01 <= reference_megapixels <= 4.0
        or (vae_dtype is not None and vae_dtype not in VALID_VAE_DTYPES)
    ):
        raise DomainError("invalid_request")
    tiles = {"tile_size": 512, "overlap": 64, "temporal_size": 64, "temporal_overlap": 8}
    vae_source = ["vae_dtype", 0] if vae_dtype else ["vae", 0]
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
            "class_type": "VAEDecodeTiled" if tiled_vae else "VAEDecode",
            "inputs": {"samples": ["sample", 0], "vae": vae_source, **(tiles if tiled_vae else {})},
        },
        "save": {
            "class_type": "SaveImage",
            "inputs": {"images": ["decode", 0], "filename_prefix": "proscenium/generated"},
        },
    }
    if vae_dtype:
        result["vae_dtype"] = {
            "class_type": "VAEComputeDtype",
            "inputs": {"vae": ["vae", 0], "dtype": vae_dtype},
        }
    if reference_artifact_id:
        result.update(
            {
                "reference": {
                    "class_type": "LoadImage",
                    "inputs": {"image": "artifact:" + reference_artifact_id},
                },
                "reference_scale": {
                    "class_type": "ImageScaleToTotalPixels",
                    "inputs": {
                        "image": ["reference", 0],
                        "upscale_method": "lanczos",
                        "megapixels": reference_megapixels,
                        "resolution_steps": 16,
                    },
                },
                "reference_encode": {
                    "class_type": "VAEEncodeTiled" if tiled_vae else "VAEEncode",
                    "inputs": {
                        "pixels": ["reference_scale", 0],
                        "vae": vae_source,
                        **(tiles if tiled_vae else {}),
                    },
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
