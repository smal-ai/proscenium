"""Pinned native Comfy SD-Turbo img2img family. Not FLUX ReferenceLatent conditioning.

Official card: stabilityai/sd-turbo, b261bac6fd2cf515557d5d0707481eafa0485ec2.
Uses one 512px image, real VAE latents, prompt conditioning and reduced-noise sampling.
"""

import copy
from typing import Any

from proscenium.api.errors import DomainError

FILENAME = "sd_turbo.safetensors"
SHA256 = "3f067a1b943cf162f2b8f8588f6cf5824bd5b4c7d1d88d87164b9ca123616549"
REVISION = "comfy-sd-turbo-img2img-1"


def graph(reference_artifact_id: str) -> dict[str, Any]:
    return {
        "checkpoint": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": FILENAME}},
        "reference": {
            "class_type": "LoadImage",
            "inputs": {"image": "artifact:" + reference_artifact_id},
        },
        "encode": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["reference", 0], "vae": ["checkpoint", 2]},
        },
        "text": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["checkpoint", 1], "text": "$prompt"},
        },
        "zero": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["text", 0]}},
        "guider": {
            "class_type": "CFGGuider",
            "inputs": {
                "model": ["checkpoint", 0],
                "positive": ["text", 0],
                "negative": ["zero", 0],
                "cfg": 1.0,
            },
        },
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": "$seed"}},
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}},
        "sigmas": {
            "class_type": "SDTurboScheduler",
            "inputs": {"model": ["checkpoint", 0], "steps": 4, "denoise": 0.5},
        },
        "sample": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["noise", 0],
                "guider": ["guider", 0],
                "sampler": ["sampler", 0],
                "sigmas": ["sigmas", 0],
                "latent_image": ["encode", 0],
            },
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sample", 0], "vae": ["checkpoint", 2]},
        },
        "save": {
            "class_type": "SaveImage",
            "inputs": {"images": ["decode", 0], "filename_prefix": "proscenium/sd-turbo-img2img"},
        },
    }


def bind(workflow, prompt, seed, references, models):
    if (
        len(references) != 1
        or models != [FILENAME]
        or seed is None
        or not 0 <= int(seed) <= 2**53 - 1
    ):
        raise DomainError(
            "invalid_request",
            message="SD-Turbo profile requires one reference, exact checkpoint and JSON-safe seed",
            safe_for_model=True,
        )
    identity = next(iter(references))
    expected = graph(identity)
    candidate = copy.deepcopy(workflow)
    # Accept only original tokens or the exact host-bound values. Everything else is
    # the fixed versioned profile, including actual conditioning edges and denoise < 1.
    for node, field, token, value in (
        ("text", "text", "$prompt", prompt),
        ("noise", "noise_seed", "$seed", int(seed)),
        ("reference", "image", "artifact:" + identity, references[identity]),
    ):
        if candidate[node]["inputs"][field] not in (token, value):
            raise DomainError(
                "invalid_request", message="Unbound SD-Turbo input", safe_for_model=True
            )
        candidate[node]["inputs"][field] = expected[node]["inputs"][field]
    if candidate != expected:
        raise DomainError(
            "invalid_request",
            message="Graph differs from host-selected SD-Turbo img2img revision",
            safe_for_model=True,
        )
    expected["text"]["inputs"]["text"] = prompt
    expected["noise"]["inputs"]["noise_seed"] = int(seed)
    expected["reference"]["inputs"]["image"] = references[identity]
    return expected
