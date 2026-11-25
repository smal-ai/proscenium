"""Stable Audio 3 ComfyUI graph and bounded FLAC metadata inspection."""

import math
import re
from copy import deepcopy
from typing import Any

from proscenium.api.errors import DomainError

REVISION = "stable-audio-3-1"
DEFAULT_MUSIC_CHECKPOINT = "stable_audio_3_medium.safetensors"
DEFAULT_SFX_CHECKPOINT = "stable_audio_3_small_sfx.safetensors"
DEFAULT_CLIP = "t5gemma_b_b_ul2.safetensors"
DEFAULT_STEPS = 8
MAX_MUSIC_SECONDS = 380.0
MAX_SFX_SECONDS = 120.0
NO_REFERENCES = "Audio generation does not accept reference artefacts"
INVALID_SEED = "An exact JSON-safe decimal seed is required"
INVALID_DURATION = "Audio duration is outside the supported bounds for this mode"
INVALID_GRAPH = "Invalid Stable Audio graph or model manifest"
UNAVAILABLE = "Stable Audio recipe nodes or models are unavailable"
MODE_LIMITS = {"music": MAX_MUSIC_SECONDS, "sfx": MAX_SFX_SECONDS}


def graph(
    *, checkpoint: str, clip: str, seconds: float, steps: int = DEFAULT_STEPS
) -> dict[str, Any]:
    nodes = (
        ("CheckpointLoaderSimple", dict(ckpt_name=checkpoint)),
        ("CLIPLoader", dict(clip_name=clip, type="stable_audio", device="default")),
        ("CLIPTextEncode", dict(text="$prompt", clip=["2", 0])),
        ("CLIPTextEncode", dict(text="", clip=["2", 0])),
        ("EmptyLatentAudio", dict(seconds=float(seconds), batch_size=1)),
        (
            "KSampler",
            dict(
                model=["1", 0],
                positive=["3", 0],
                negative=["4", 0],
                latent_image=["5", 0],
                seed="$seed",
                steps=steps,
                cfg=1.0,
                sampler_name="lcm",
                scheduler="simple",
                denoise=1.0,
            ),
        ),
        ("VAEDecodeAudio", dict(samples=["6", 0], vae=["1", 2])),
        ("SaveAudio", dict(audio=["7", 0], filename_prefix="proscenium_audio")),
    )
    return {
        str(i): dict(class_type=kind, inputs=inputs) for i, (kind, inputs) in enumerate(nodes, 1)
    }


def bind(
    workflow: dict[str, Any],
    prompt: str,
    seed: str | None,
    references: dict[str, str],
    models: list[str],
) -> dict[str, Any]:
    if references:
        raise DomainError("invalid_request", message=NO_REFERENCES)
    if not isinstance(seed, str) or re.fullmatch(r"[0-9]+", seed) is None:
        raise DomainError("invalid_request", message=INVALID_SEED)
    significant_seed = seed.lstrip("0") or "0"
    if len(significant_seed) > 16 or int(significant_seed) > 2**53 - 1:
        raise DomainError("invalid_request", message=INVALID_SEED)

    bound_seed = int(significant_seed)

    def inputs(identity: str) -> dict[str, Any]:
        return workflow[identity]["inputs"]

    try:
        # Resolve from the sole saved output so equivalent node IDs are accepted.
        (save,) = (
            identity for identity, node in workflow.items() if node["class_type"] == "SaveAudio"
        )
        decode = inputs(save)["audio"][0]
        sampler = inputs(decode)["samples"][0]
        sample_inputs = inputs(sampler)
        checkpoint_node = sample_inputs["model"][0]
        pos, neg, latent = (sample_inputs[k][0] for k in ("positive", "negative", "latent_image"))
        clip_node = inputs(pos)["clip"][0]
        seconds, steps = inputs(latent)["seconds"], sample_inputs["steps"]
        checkpoint, clip = inputs(checkpoint_node)["ckpt_name"], inputs(clip_node)["clip_name"]
        if type(seconds) not in (int, float) or not 1 <= seconds <= MAX_MUSIC_SECONDS:
            raise DomainError("invalid_request", message=INVALID_DURATION)
        if (
            type(steps) is not int
            or not 1 <= steps <= 50
            or len(models) != 2
            or len({checkpoint, clip}) != 2
            or set(models) != {checkpoint, clip}
            or not all(isinstance(name, str) and name for name in models)
        ):
            raise ValueError
        ids = dict(
            zip(
                map(str, range(1, 9)),
                (checkpoint_node, clip_node, pos, neg, latent, sampler, decode, save),
                strict=True,
            )
        )
        if len(workflow) != 8 or len(set(ids.values())) != 8:
            raise ValueError
        expected = graph(checkpoint=checkpoint, clip=clip, seconds=seconds, steps=steps)
        for identity, node in expected.items():
            actual = workflow[ids[identity]]
            for k, value in node["inputs"].items():
                supplied = actual["inputs"][k]
                if value == "$prompt" and supplied == prompt:
                    node["inputs"][k] = prompt
                elif value == "$seed" and type(supplied) is int and supplied == bound_seed:
                    node["inputs"][k] = bound_seed
                elif isinstance(value, list):
                    link = supplied
                    if not isinstance(link, list) or len(link) != 2 or type(link[1]) is not int:
                        raise ValueError
                    node["inputs"][k] = [ids[value[0]], value[1]]
            if actual["class_type"] != node["class_type"] or actual["inputs"] != node["inputs"]:
                raise ValueError
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise DomainError("invalid_request", message=INVALID_GRAPH) from exc
    bound = deepcopy(workflow)
    bound[pos]["inputs"]["text"] = prompt
    bound[sampler]["inputs"]["seed"] = bound_seed
    return bound


def inspect_flac_bytes(data: bytes) -> dict[str, Any]:
    if len(data) < 42 or data[:4] != b"fLaC" or data[4] & 0x7F or data[5:8] != b"\0\0\x22":
        raise DomainError("unsupported_media_type", 415)
    stream = int.from_bytes(data[18:26], "big")
    sample_rate = stream >> 44
    channels = ((stream >> 41) & 7) + 1
    bits_per_sample = ((stream >> 36) & 31) + 1
    total_samples = stream & ((1 << 36) - 1)
    if sample_rate < 8000 or not 1 <= channels <= 8 or bits_per_sample < 4:
        raise DomainError("unsupported_media_type", 415)

    def corrupted() -> DomainError:
        return DomainError(
            "corrupt_provider_output", 422, "Provider returned empty or corrupted audio"
        )

    duration = round(total_samples / sample_rate, 6)
    if not math.isfinite(duration) or duration <= 0:
        raise corrupted()
    # Walk metadata until the last block and check the first frame sync word.
    header, end = 4, 42
    while not data[header] & 0x80:
        header = end
        if header + 4 > len(data):
            raise corrupted()
        if data[header] & 0x7F in {0, 127}:
            raise DomainError("unsupported_media_type", 415)
        end = header + 4 + int.from_bytes(data[header + 1 : header + 4], "big")
        if end > len(data):
            raise corrupted()
    if end + 10 > len(data) or data[end] != 0xFF or data[end + 1] & 0xFE != 0xF8:
        raise corrupted()
    return dict(
        duration_seconds=duration,
        sample_rate_hz=sample_rate,
        channels=channels,
        sha256_verified_during_registration=True,
    )
