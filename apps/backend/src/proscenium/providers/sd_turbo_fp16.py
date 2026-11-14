"""Owned CPU-converted SD-Turbo derivative, NOT an official downloaded FP16 file.

Conversion report/precision limits are immutable profile provenance. No model loads here.
The original F32 graph/profile and frozen jobs retain their existing interpretation.
"""

import copy

from proscenium.api.errors import DomainError
from proscenium.providers import sd_turbo

FILENAME = "sd_turbo-proscenium-fp16-1.safetensors"
SHA256 = "c1a0a0af943c0c197215fb7cd5bb8b72e342b98461e1c3719d8ba28300ba015e"
BYTES = 2_607_364_064
REVISION = "comfy-sd-turbo-img2img-fp16-1"
CONVERTER_SHA256 = "42e5311f48e85d455b3ccf929da517468a489a21126808d7e4c7fcfca845feb3"
CONVERSION_REPORT_SHA256 = "b44b82b596d3c6caeed02ffd3cf432b11f53a401f5a270c0177e4b61368a824a"
PREFIX = "proscenium/sd-turbo-img2img-fp16-1"
MODEL_SOURCE = {
    "repository": "stabilityai/sd-turbo",
    "revision": "b261bac6fd2cf515557d5d0707481eafa0485ec2",
    "source_filename": sd_turbo.FILENAME,
    "source_sha256": sd_turbo.SHA256,
    "filename": FILENAME,
    "sha256": SHA256,
    "bytes": BYTES,
    "is_local_derivative_not_official_download": True,
    "conversion": {
        "operation": "CPU streaming F32-to-F16, no training or clamping",
        "converter_sha256": CONVERTER_SHA256,
        "report_sha256": CONVERSION_REPORT_SHA256,
        "numpy_version": "2.5.3",
        "rounding": "IEEE binary16 round-to-nearest ties-to-even",
        "relative_tolerance": 2.0**-11,
        "absolute_tolerance": 2.0**-25,
        "converted_tensor_count": 1229,
        "converted_elements": 1_303_598_228,
        "max_abs_error": 0.0038881301879882812,
        "max_normal_relative_error": 0.0004880429477794046,
        "rounded_nonzero_to_zero": 231210,
        "full_streaming_readback_validated": True,
        "original_hash_unchanged_after": True,
    },
    "licence": "Stability AI Community License, 2024-07-05; local precision derivative, no retraining",
    "fit_floor_formula": "max(12 GiB, actual derivative bytes + 8 GiB)",
    "fit_floor_bytes": max(12 * 1024**3, BYTES + 8 * 1024**3),
}


def graph(reference_artifact_id):
    result = sd_turbo.graph(reference_artifact_id)
    result["checkpoint"]["inputs"]["ckpt_name"] = FILENAME
    result["save"]["inputs"]["filename_prefix"] = PREFIX
    return result


def bind(workflow, prompt, seed, references, models):
    if (
        models != [FILENAME]
        or workflow["checkpoint"]["inputs"]["ckpt_name"] != FILENAME
        or workflow["save"]["inputs"]["filename_prefix"] != PREFIX
    ):
        raise DomainError(
            "invalid_request",
            message="The host-selected FP16 profile requires its exact owned derivative filename",
        )
    translated = copy.deepcopy(workflow)
    translated["checkpoint"]["inputs"]["ckpt_name"] = sd_turbo.FILENAME
    translated["save"]["inputs"]["filename_prefix"] = sd_turbo.graph("")["save"]["inputs"][
        "filename_prefix"
    ]
    result = sd_turbo.bind(translated, prompt, seed, references, [sd_turbo.FILENAME])
    result["checkpoint"]["inputs"]["ckpt_name"] = FILENAME
    result["save"]["inputs"]["filename_prefix"] = PREFIX
    return result
