"""Stable Audio 3 ComfyUI graph and bounded FLAC metadata inspection."""

import json
import math
import os
import re
import selectors
import shutil
import signal
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.audio_formats import AUDIO_DEMUXERS

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
MAX_PCM_BYTES = 400 * 1024**2
AUDIO_VERIFY_UNAVAILABLE = "Audio verification requires ffmpeg and ffprobe"
INVALID_AUDIO_BYTES = "Audio bytes failed bounded decode verification"
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


def corrupt_audio() -> DomainError:
    return DomainError("corrupt_provider_output", 422, "Provider returned empty or corrupted audio")


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

    duration = round(total_samples / sample_rate, 6)
    if not math.isfinite(duration) or duration <= 0:
        raise corrupt_audio()
    # Walk metadata until the last block and check the first frame sync word.
    header, end = 4, 42
    while not data[header] & 0x80:
        header = end
        if header + 4 > len(data):
            raise corrupt_audio()
        if data[header] & 0x7F in {0, 127}:
            raise DomainError("unsupported_media_type", 415)
        end = header + 4 + int.from_bytes(data[header + 1 : header + 4], "big")
        if end > len(data):
            raise corrupt_audio()
    if end + 10 > len(data) or data[end] != 0xFF or data[end + 1] & 0xFE != 0xF8:
        raise corrupt_audio()
    return dict(
        duration_seconds=duration,
        sample_rate_hz=sample_rate,
        channels=channels,
        declared_total_samples=total_samples,
    )


def _bounded_output(
    argv: list[str], deadline: float, maximum: int, *, capture: bool = False
) -> tuple[int, bytes]:
    """Drain both pipes without trusting EOF, stderr size or process duration."""
    with subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    ) as process:
        assert process.stdout is not None and process.stderr is not None
        result = bytearray()
        count = 0
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                selector.register(process.stderr, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise corrupt_audio()
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, 64 * 1024)
                        if not chunk:
                            selector.unregister(key.fd)
                        elif key.fileobj is process.stdout:
                            count += len(chunk)
                            if count > maximum:
                                raise corrupt_audio()
                            if capture:
                                result.extend(chunk)
            returncode = process.wait(timeout=max(0, deadline - time.monotonic()))
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        if returncode != 0:
            raise corrupt_audio()
        return count, bytes(result)


def verify_audio_bytes(
    data: bytes,
    media_type: str,
    *,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Verify isolated audio bytes by counting the samples produced by a full PCM decode."""
    if media_type not in AUDIO_DEMUXERS:
        raise DomainError("unsupported_media_type", 415)
    if not data:
        raise corrupt_audio()
    declared = inspect_flac_bytes(data) if media_type == "audio/flac" else None
    decoder, probe = shutil.which(ffmpeg or "ffmpeg"), shutil.which(ffprobe or "ffprobe")
    if not decoder or not probe:
        raise DomainError("capability_unavailable", 501, AUDIO_VERIFY_UNAVAILABLE)
    demuxer, codecs = AUDIO_DEMUXERS[media_type]
    input_options = ["-protocol_whitelist", "file", "-f", demuxer]
    try:
        with TemporaryDirectory(prefix="proscenium-audio-verify-") as directory:
            path = Path(directory) / "audio"
            path.write_bytes(data)
            deadline = time.monotonic() + timeout
            _, raw = _bounded_output(
                [
                    probe,
                    "-v",
                    "error",
                    *input_options,
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ],
                deadline,
                1024**2,
                capture=True,
            )
            (stream,) = json.loads(raw)["streams"]
            codec = stream["codec_name"]
            if stream["codec_type"] != "audio" or not (
                codec.startswith("pcm_") if codecs is None else codec in codecs
            ):
                raise DomainError("unsupported_media_type", 415)
            rate, channels = int(stream["sample_rate"]), int(stream["channels"])
            if rate < 8000 or not 1 <= channels <= 8:
                raise DomainError("unsupported_media_type", 415)
            if declared and (rate, channels) != (declared["sample_rate_hz"], declared["channels"]):
                raise corrupt_audio()
            pcm, _ = _bounded_output(
                [
                    decoder,
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    *input_options,
                    "-i",
                    str(path),
                    "-map",
                    "0:a:0",
                    "-f",
                    "s16le",
                    "-",
                ],
                deadline,
                MAX_PCM_BYTES,
            )
    except (
        OSError,
        subprocess.TimeoutExpired,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ) as exc:
        raise corrupt_audio() from exc
    samples, remainder = divmod(pcm, channels * 2)
    duration = round(samples / rate, 6)
    if remainder or duration <= 0 or (declared and samples != declared["declared_total_samples"]):
        raise corrupt_audio()
    return dict(
        duration_seconds=duration,
        sample_rate_hz=rate,
        channels=channels,
        sha256_verified_during_registration=True,
    )
