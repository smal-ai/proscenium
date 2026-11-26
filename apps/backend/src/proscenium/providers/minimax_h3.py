"""MiniMax H3 ComfyUI 0.45 API graphs and bounded MP4 verification."""

import errno
import json
import math
import re
import resource
import subprocess
import time
from collections.abc import Callable, Sequence
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.providers import comfy
from proscenium.providers.stable_audio import _bounded_output, resolve_audio_verifier

REVISION = "minimax-h3-1"
DEFAULT_FL2VA_MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
DEFAULT_REF2VA_MODEL = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
DEFAULT_CLIP = "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
DEFAULT_VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
DEFAULT_AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
DEFAULT_FL2VA_LORA_4STEP = "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
DEFAULT_FL2VA_LORA_8STEP = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
DEFAULT_REF2VA_LORA_4STEP = "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
DEFAULT_WIDTH = 864
DEFAULT_HEIGHT = 480
DEFAULT_STEPS = 4
DEFAULT_DURATION_SECONDS = 5.0
MAX_WIDTH = 1344
MAX_HEIGHT = 1344
MAX_PIXELS = 1344 * 768
CANVAS_MULTIPLE = 32
FPS = 24
MIN_FRAMES = 5
MAX_FRAMES = 362
MAX_DURATION_SECONDS = 15.0
MAX_DURATION_DRIFT_SECONDS = 0.25
TILE_THRESHOLD_PIXELS = 864 * 480
TILE_THRESHOLD_FRAMES = 124
MAX_REFERENCE_IMAGES = 9
MAX_GUIDE_FRAMES = 4
MAX_VIDEO_BYTES = 512 * 1024**2
DEFAULT_MAX_SUBPROCESS_MEMORY_BYTES = 1024**3
MAX_SUBPROCESS_CPU_SECONDS = 30
DEFAULT_SHIFT_VIDEO = 12.0
DEFAULT_SHIFT_AUDIO = 3.0

INVALID_GRAPH = "Invalid MiniMax H3 graph or model manifest"
INVALID_SEED = "An exact JSON-safe decimal seed is required"
UNAVAILABLE = "MiniMax H3 recipe nodes or models are unavailable"
VIDEO_VERIFY_UNAVAILABLE = "Video verification requires ffmpeg and ffprobe"
VIDEO_OUTPUT_NODES = frozenset({"SaveVideo"})
VAE_DTYPES = ("default", "fp32", "fp16", "bf16")
MODEL_INPUTS = {
    "model": "unet_name",
    "clip": "clip_name",
    "video_vae": "vae_name",
    "audio_vae": "vae_name",
    "lora": "lora_name",
}
ARTEFACT_PREFIX = "artifact:"
MP4_BRANDS = {b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ", b"dash"}
JSON = dict[str, Any]


def align_frame_count(duration_seconds: float) -> int:
    if not _number(duration_seconds, 0.2, MAX_DURATION_SECONDS):
        raise DomainError("invalid_request", message="Video duration is outside supported bounds")
    count = max(MIN_FRAMES, round(duration_seconds * FPS))
    return min(MAX_FRAMES, count + (5 - count) % 17)


def requires_tiled_vae(width: int, height: int, frame_count: int) -> bool:
    return width * height > TILE_THRESHOLD_PIXELS or frame_count > TILE_THRESHOLD_FRAMES


def _exact_int(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _number(value: Any, minimum: float, maximum: float) -> bool:
    return type(value) in (float, int) and minimum <= value <= maximum


def _artifact_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is not None
    )


def _graph_error() -> DomainError:
    return DomainError("invalid_request", message=INVALID_GRAPH)


def _validate_model_roles(
    mode: str, unet: str, clip: str, video_vae: str, audio_vae: str, lora: str | None
) -> None:
    names = [unet, clip, video_vae, audio_vae] + ([lora] if lora is not None else [])
    if not all(isinstance(name, str) and name.strip() for name in names) or len(set(names)) != len(
        names
    ):
        raise _graph_error()
    if any("nvfp4" in name.lower() for name in names):
        raise DomainError("invalid_request", message="NVFP4 models are not supported on ROCm")
    required, excluded = ("fl2v", "ref2v") if mode == "fl2va" else ("ref2v", "fl2v")
    if (
        required + "a" not in unet.lower()
        or excluded + "a" in unet.lower()
        or (lora is not None and (required not in lora.lower() or excluded in lora.lower()))
        or "video" not in video_vae.lower()
        or "audio" in video_vae.lower()
        or "audio" not in audio_vae.lower()
        or "fp32" not in audio_vae.lower()
    ):
        raise _graph_error()


def graph(
    *,
    mode: str,
    unet: str,
    clip: str,
    video_vae: str,
    audio_vae: str,
    lora: str | None = None,
    lora_strength: float = 1.0,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    frame_count: int = TILE_THRESHOLD_FRAMES,
    steps: int = DEFAULT_STEPS,
    sampler_name: str = "res_multistep",
    scheduler: str = "simple",
    shift_video: float = DEFAULT_SHIFT_VIDEO,
    shift_audio: float = DEFAULT_SHIFT_AUDIO,
    first_frame_artifact_id: str | None = None,
    last_frame_artifact_id: str | None = None,
    guide_frames: Sequence[JSON] = (),
    reference_artifact_ids: Sequence[str] = (),
    ref_image_size: str = "match",
    video_vae_dtype: str | None = None,
    tiled_vae: bool = False,
) -> JSON:
    """Build the only supported workflow shape, keeping the audio VAE uncast."""
    if not (
        _exact_int(width, 320, MAX_WIDTH)
        and _exact_int(height, 256, MAX_HEIGHT)
        and width % CANVAS_MULTIPLE == height % CANVAS_MULTIPLE == 0
        and width * height <= MAX_PIXELS
        and _exact_int(frame_count, MIN_FRAMES, MAX_FRAMES)
        and frame_count % 17 == 5
        and _exact_int(steps, 1, 30)
    ):
        raise DomainError(
            "invalid_request", message="Invalid MiniMax H3 canvas, frame count or steps"
        )
    if mode not in ("fl2va", "ref2va"):
        raise _graph_error()
    _validate_model_roles(mode, unet, clip, video_vae, audio_vae, lora)
    if (
        not all(isinstance(name, str) and name.strip() for name in (sampler_name, scheduler))
        or not _number(lora_strength, -100, 100)
        or not _number(shift_video, 0.01, 100)
        or not _number(shift_audio, 0.01, 100)
        or (video_vae_dtype is not None and video_vae_dtype not in VAE_DTYPES)
        or type(tiled_vae) is not bool
    ):
        raise _graph_error()
    if (
        ref_image_size not in ("match", "max")
        or (mode == "fl2va" and reference_artifact_ids)
        or (mode == "ref2va" and (last_frame_artifact_id is not None or guide_frames))
        or isinstance(reference_artifact_ids, (str, bytes))
        or len(reference_artifact_ids) > MAX_REFERENCE_IMAGES
        or len(guide_frames) > MAX_GUIDE_FRAMES
        or not all(_artifact_id(ref) for ref in reference_artifact_ids)
        or len(set(reference_artifact_ids)) != len(reference_artifact_ids)
        or any(
            value is not None and not _artifact_id(value)
            for value in (first_frame_artifact_id, last_frame_artifact_id)
        )
    ):
        raise DomainError("invalid_request", message="Invalid MiniMax H3 conditioning artefacts")
    indices: set[int] = set()
    for guide in guide_frames:
        if (
            not isinstance(guide, dict)
            or set(guide) != {"artifact_id", "frame_index"}
            or not _artifact_id(guide["artifact_id"])
            or not _exact_int(guide["frame_index"], 0, frame_count - 1)
            or guide["frame_index"] in indices
        ):
            raise DomainError(
                "invalid_request", message="Invalid or duplicate MiniMax H3 guide frame"
            )
        indices.add(guide["frame_index"])

    result: JSON = {}

    def add(identity: str, kind: str, **inputs: Any) -> list[str | int]:
        result[identity] = {"class_type": kind, "inputs": inputs}
        return [identity, 0]

    def image(identity: str, artifact_id: str) -> list[str | int]:
        return add(identity, "LoadImage", image=ARTEFACT_PREFIX + artifact_id)

    model = add("model", "UNETLoader", unet_name=unet, weight_dtype="default")
    if lora is not None:
        model = add(
            "lora",
            "LoraLoaderModelOnly",
            model=model,
            lora_name=lora,
            strength_model=float(lora_strength),
        )
    shifted = add(
        "shift",
        "MiniMaxH3SigmaShift",
        model=model,
        shift_video=float(shift_video),
        shift_audio=float(shift_audio),
    )
    encoder = add("clip", "CLIPLoader", clip_name=clip, type="minimax", device="default")
    vae = add("video_vae", "VAELoader", vae_name=video_vae)
    if video_vae_dtype is not None:
        vae = add("video_vae_dtype", "VAEComputeDtype", vae=vae, dtype=video_vae_dtype)
    audio = add("audio_vae", "VAELoader", vae_name=audio_vae)

    conditioning = dict(
        clip=encoder, vae=vae, prompt="$prompt", width=width, height=height, length=frame_count
    )
    first = (
        image("first_frame", first_frame_artifact_id)
        if first_frame_artifact_id is not None
        else None
    )
    latent = ["conditioning", 1]
    if mode == "fl2va":
        if first is not None:
            conditioning["first_frame"] = first
        if last_frame_artifact_id is not None:
            conditioning["last_frame"] = image("last_frame", last_frame_artifact_id)
        positive = add("conditioning", "MiniMaxH3ImageToVideo", **conditioning)
        for i, guide in enumerate(guide_frames):
            guide_image = image(f"guide_image_{i}", guide["artifact_id"])
            positive = add(
                f"guide_{i}",
                "MiniMaxH3AddGuide",
                positive=positive,
                vae=vae,
                latent=latent,
                image=guide_image,
                frame_idx=guide["frame_index"],
            )
    else:
        conditioning.update(audio_vae=audio, ref_image_size=ref_image_size)
        for i, ref in enumerate(reference_artifact_ids):
            conditioning[f"ref_images.ref_image_{i}"] = image(f"ref_{i}", ref)
        positive = add("conditioning", "MiniMaxH3ReferenceToVideo", **conditioning)
        if first is not None:
            positive = add(
                "guide_start",
                "MiniMaxH3AddGuide",
                positive=positive,
                vae=vae,
                latent=latent,
                image=first,
                frame_idx=0,
            )

    guider = add("guider", "BasicGuider", model=shifted, conditioning=positive)
    sampler = add("sampler", "KSamplerSelect", sampler_name=sampler_name)
    sigmas = add(
        "sigmas", "BasicScheduler", model=shifted, scheduler=scheduler, steps=steps, denoise=1.0
    )
    noise = add("noise", "RandomNoise", noise_seed="$seed")
    sample = add(
        "sample",
        "SamplerCustomAdvanced",
        noise=noise,
        guider=guider,
        sampler=sampler,
        sigmas=sigmas,
        latent_image=latent,
    )
    if tiled_vae:
        frames = add(
            "decode_video",
            "VAEDecodeTiled",
            samples=sample,
            vae=vae,
            tile_size=512,
            overlap=64,
            temporal_size=64,
            temporal_overlap=8,
        )
    else:
        frames = add("decode_video", "VAEDecode", samples=sample, vae=vae)
    soundtrack = add("decode_audio", "VAEDecodeAudio", samples=sample, vae=audio)
    video = add(
        "create_video",
        "CreateVideo",
        images=frames,
        audio=soundtrack,
        fps=float(FPS),
        bit_depth=8,
        color_space="sRGB",
    )
    add(
        "save",
        "SaveVideo",
        video=video,
        filename_prefix="proscenium_video",
        format="mp4",
        **{"format.codec": "h264"},
    )
    return result


def _image_input(value: Any) -> bool:
    # Comfy returns a relative upload name (optionally with a subfolder), not a path
    # or an annotated filename selecting a different storage area.
    return (
        isinstance(value, str)
        and not any(c in value for c in "\\\0\r\n:[]")
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


def _exact_shape(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and actual.keys() == expected.keys()
            and all(_exact_shape(actual[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_exact_shape(a, b) for a, b in zip(actual, expected, strict=True))
        )
    if type(expected) is float:
        return type(actual) in (float, int) and actual == expected
    return type(actual) is type(expected) and actual == expected


def _validate_workflow(
    workflow: JSON, models: list[str], *, unbound_images: bool
) -> dict[str, str]:
    """Rebuild the template from the declared controls and reject any other topology."""
    images: dict[str, str] = {}

    def inputs(identity: str) -> JSON:
        return workflow[identity]["inputs"]

    def artifact(identity: str) -> str:
        value = inputs(identity)["image"]
        if isinstance(value, str) and value.startswith(ARTEFACT_PREFIX):
            artifact_id = value.removeprefix(ARTEFACT_PREFIX)
            if not _artifact_id(artifact_id):
                raise _graph_error()
        elif not unbound_images and _image_input(value):
            # After upload the original ID is no longer available. Identical bytes
            # from distinct artefacts may legitimately share the same upload name.
            artifact_id = identity
        else:
            raise _graph_error()
        images[identity] = value
        return artifact_id

    try:
        selected = {
            role: inputs(role)[name] for role, name in MODEL_INPUTS.items() if role in workflow
        }
        if models != list(selected.values()):
            raise _graph_error()
        cond = inputs("conditioning")
        noise = inputs("noise")["noise_seed"]
        if (
            not isinstance(cond["prompt"], str)
            or not 1 <= len(cond["prompt"]) <= 8000
            or (noise != "$seed" and not _exact_int(noise, 0, 2**53 - 1))
        ):
            raise _graph_error()
        mode = (
            "ref2va"
            if workflow["conditioning"]["class_type"] == "MiniMaxH3ReferenceToVideo"
            else "fl2va"
        )
        expected = graph(
            mode=mode,
            unet=selected["model"],
            clip=selected["clip"],
            video_vae=selected["video_vae"],
            audio_vae=selected["audio_vae"],
            lora=selected.get("lora"),
            lora_strength=inputs("lora")["strength_model"] if "lora" in workflow else 1.0,
            width=cond["width"],
            height=cond["height"],
            frame_count=cond["length"],
            steps=inputs("sigmas")["steps"],
            scheduler=inputs("sigmas")["scheduler"],
            sampler_name=inputs("sampler")["sampler_name"],
            shift_video=inputs("shift")["shift_video"],
            shift_audio=inputs("shift")["shift_audio"],
            first_frame_artifact_id=artifact("first_frame") if "first_frame" in workflow else None,
            last_frame_artifact_id=artifact("last_frame") if "last_frame" in workflow else None,
            guide_frames=[
                {
                    "artifact_id": artifact(f"guide_image_{i}"),
                    "frame_index": inputs(f"guide_{i}")["frame_idx"],
                }
                for i in range(MAX_GUIDE_FRAMES)
                if f"guide_{i}" in workflow
            ],
            reference_artifact_ids=[
                artifact(f"ref_{i}") for i in range(MAX_REFERENCE_IMAGES) if f"ref_{i}" in workflow
            ],
            ref_image_size=cond.get("ref_image_size", "match"),
            video_vae_dtype=inputs("video_vae_dtype")["dtype"]
            if "video_vae_dtype" in workflow
            else None,
            tiled_vae=workflow["decode_video"]["class_type"] == "VAEDecodeTiled",
        )
        for identity, value in images.items():
            expected[identity]["inputs"]["image"] = value
        expected["conditioning"]["inputs"]["prompt"] = cond["prompt"]
        expected["noise"]["inputs"]["noise_seed"] = noise
        if not _exact_shape(workflow, expected):
            raise _graph_error()
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise _graph_error() from exc
    return images


def bind(
    workflow: JSON, prompt: str, seed: str | None, references: dict[str, str], models: list[str]
) -> JSON:
    if not isinstance(seed, str) or re.fullmatch(r"[0-9]+", seed) is None:
        raise DomainError("invalid_request", message=INVALID_SEED)
    significant = seed.lstrip("0") or "0"
    if len(significant) > 16 or int(significant) > 2**53 - 1:
        raise DomainError("invalid_request", message=INVALID_SEED)
    bound_seed = int(significant)
    images = _validate_workflow(workflow, models, unbound_images=True)
    paths = {identity: value.removeprefix(ARTEFACT_PREFIX) for identity, value in images.items()}
    if (
        not isinstance(prompt, str)
        or not 1 <= len(prompt) <= 8000
        or workflow["conditioning"]["inputs"]["prompt"] not in ("$prompt", prompt)
        or workflow["noise"]["inputs"]["noise_seed"] not in ("$seed", bound_seed)
        or set(paths.values()) != references.keys()
        or not all(
            value == ARTEFACT_PREFIX + key or _image_input(value)
            for key, value in references.items()
        )
    ):
        raise DomainError(
            "invalid_request", message="MiniMax H3 prompt, seed or conditioning binding mismatch"
        )
    result = deepcopy(workflow)
    result["conditioning"]["inputs"]["prompt"] = prompt
    result["noise"]["inputs"]["noise_seed"] = bound_seed
    for identity, artifact_id in paths.items():
        result[identity]["inputs"]["image"] = references[artifact_id]
    return result


def _choices(spec: list[Any]) -> list[Any]:
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and isinstance(spec[1].get("options"), list):
        return spec[1]["options"]
    return []


def _expand_inputs(schema: JSON, inputs: JSON, prefix: str = "") -> JSON:
    """Translate exercised V3 inputs to flat object-info specs for generic preflight."""
    flat: dict[str, JSON] = {"required": {}, "optional": {}}
    for group, specs in flat.items():
        for local, spec in schema.get(group, {}).items():
            key = prefix + local
            kind = spec[0]
            if kind == "COMFY_DYNAMICCOMBO_V3":
                options = {entry["key"]: entry["inputs"] for entry in spec[1]["options"]}
                specs[key] = [list(options)]
                if key in inputs:
                    if inputs[key] not in options:
                        raise DomainError("capability_unavailable", 501, UNAVAILABLE)
                    nested = _expand_inputs(options[inputs[key]], inputs, key + ".")
                    for child_group, children in nested.items():
                        flat[child_group].update(children)
            elif kind == "COMFY_AUTOGROW_V3":
                template = spec[1]["template"]
                ((_, inner),) = template["input"]["required"].items()
                minimum, maximum = template["min"], template["max"]
                if not 0 <= minimum <= maximum <= MAX_REFERENCE_IMAGES:
                    raise ValueError("Invalid ComfyUI autogrow bounds")
                stem = key + "." + template["prefix"]
                active = group == "required" or any(name.startswith(stem) for name in inputs)
                for i in range(maximum):
                    target = "required" if active and i < minimum else "optional"
                    flat[target][stem + str(i)] = inner
            else:
                specs[key] = spec
    return flat


def preflight(graph: JSON, catalog: JSON, models: list[str]) -> None:
    _validate_workflow(graph, models, unbound_images=False)
    flat_catalog: JSON = {}
    try:
        if comfy.supports_bf16_vae(catalog) and graph.get("video_vae_dtype", {}).get("inputs") != {
            "vae": ["video_vae", 0],
            "dtype": "bf16",
        }:
            raise DomainError(
                "invalid_request",
                message="MiniMax H3 requires BF16 video VAE compute on this provider",
            )
        for identity, node in graph.items():
            kind = node["class_type"]
            schema = catalog[kind]
            if bool(schema.get("output_node")) != (identity == "save"):
                raise DomainError("invalid_request", message=comfy.OUTPUT_CAPABILITY_MISMATCH)
            flat = _expand_inputs(schema["input"], node["inputs"])
            if (model_key := MODEL_INPUTS.get(identity)) is not None:
                model_spec = flat["required"].get(model_key) or flat["optional"][model_key]
                if node["inputs"][model_key] not in _choices(model_spec):
                    raise DomainError("capability_unavailable", 501, UNAVAILABLE)
            flat_catalog[kind] = {**schema, "input": flat}
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise DomainError("capability_unavailable", 501, UNAVAILABLE) from exc
    if graph["noise"]["inputs"]["noise_seed"] == "$seed":
        graph = {**graph, "noise": {**graph["noise"], "inputs": {"noise_seed": 0}}}
    comfy.preflight(graph, flat_catalog, models, allowed_output_nodes=VIDEO_OUTPUT_NODES)


def output_descriptors(history: JSON) -> list[JSON]:
    outputs = history.get("outputs")
    output = outputs.get("save") if isinstance(outputs, dict) else None
    if not isinstance(output, dict):
        raise _corrupt_video()
    images, videos = output.get("images", []), output.get("videos", [])
    if (
        not isinstance(images, list)
        or not isinstance(videos, list)
        or len(images) + len(videos) != 1
    ):
        raise _corrupt_video()
    descriptor = (images or videos)[0]
    if not isinstance(descriptor, dict) or descriptor.get("type") != "output":
        raise _corrupt_video()
    filename, subfolder = descriptor.get("filename"), descriptor.get("subfolder", "")
    if (
        not isinstance(filename, str)
        or not filename.lower().endswith(".mp4")
        or any(part in filename for part in ("/", "\\", "..", "\0"))
        or Path(filename).name != filename
        or not isinstance(subfolder, str)
        or subfolder.startswith("/")
        or any(part in subfolder for part in ("\\", "\0"))
        or ".." in subfolder.split("/")
    ):
        raise _corrupt_video()
    return [descriptor]


def resolve_video_verifier(rendering: JSON | None = None) -> tuple[str, str]:
    try:
        return resolve_audio_verifier(rendering)
    except DomainError as exc:
        raise DomainError("capability_unavailable", 501, VIDEO_VERIFY_UNAVAILABLE) from exc


def _corrupt_video() -> DomainError:
    return DomainError("corrupt_provider_output", 422, "Provider returned empty or corrupted video")


def _ftyp_brands(data: bytes) -> tuple[bytes, set[bytes]]:
    box_size = int.from_bytes(data[:4], "big")
    if data[4:8] != b"ftyp" or not 16 <= box_size <= min(len(data), 1024):
        raise DomainError("unsupported_media_type", 415)
    major_brand = data[8:12]
    compatible = {data[i : i + 4] for i in range(16, box_size - box_size % 4, 4)}
    return major_brand, compatible


def _decoder_limits(max_memory: int) -> Callable[[], None]:
    def ceiling(kind: int, maximum: int) -> tuple[int, int]:
        soft, hard = resource.getrlimit(kind)
        limit = min(value for value in (maximum, soft, hard) if value != resource.RLIM_INFINITY)
        return limit, limit

    memory = ceiling(resource.RLIMIT_AS, max_memory)
    cpu = ceiling(resource.RLIMIT_CPU, MAX_SUBPROCESS_CPU_SECONDS)

    def apply() -> None:
        # Keep the post-fork hook to setrlimit calls only. No IO, imports or locks.
        resource.setrlimit(resource.RLIMIT_AS, memory)
        resource.setrlimit(resource.RLIMIT_CPU, cpu)

    return apply


def _duration(stream: JSON) -> float | None:
    if "duration" not in stream:
        return None
    raw = stream["duration"]
    if type(raw) not in (int, float, str):
        raise _corrupt_video()
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise _corrupt_video()
    return value


def _verify_staged_video(
    path: Path, decoder: str, probe: str, deadline: float, limits: Callable[[], None]
) -> JSON:
    # Force the ISO BMFF demuxer and disallow external track references and network IO.
    options = [
        "-protocol_whitelist",
        "file",
        "-f",
        "mov",
        "-enable_drefs",
        "0",
        "-max_pixels",
        str(MAX_PIXELS),
    ]

    def read(argv: list[str], maximum: int) -> tuple[int, bytes]:
        return _bounded_output(
            argv, deadline, maximum, capture=True, preexec_fn=limits, corrupt=_corrupt_video
        )

    _, raw = read(
        [
            probe,
            "-v",
            "error",
            *options,
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        1024**2,
    )
    report = json.loads(raw)
    container = report["format"]
    if not {"mp4", "mov"}.intersection(container["format_name"].split(",")):
        raise DomainError("unsupported_media_type", 415)
    streams = report["streams"]
    if any(stream.get("codec_type") not in ("video", "audio") for stream in streams):
        raise DomainError("unsupported_media_type", 415)
    videos = [s for s in streams if s["codec_type"] == "video"]
    audio = [s for s in streams if s["codec_type"] == "audio"]
    if len(videos) != 1 or len(audio) > 1 or videos[0].get("codec_name") not in ("h264", "av1"):
        raise DomainError("unsupported_media_type", 415)
    video = videos[0]
    width, height = video["width"], video["height"]
    video_duration = _duration(video)
    container_duration = _duration(container)
    audio_duration = _duration(audio[0]) if audio else None
    fps = Fraction(video["avg_frame_rate"])
    if (
        not _exact_int(width, 64, MAX_PIXELS)
        or not _exact_int(height, 64, MAX_PIXELS)
        or width * height > MAX_PIXELS
        or fps <= 0
        or not math.isfinite(float(fps))
        or not _exact_int(video["index"], 0, 1024)
        or video.get("disposition", {}).get("attached_pic")
    ):
        raise _corrupt_video()
    audio_metadata = {}
    if audio:
        stream = audio[0]
        rate, channels = int(stream["sample_rate"]), stream["channels"]
        if (
            rate < 8000
            or not _exact_int(channels, 1, 8)
            or not _exact_int(stream["index"], 0, 1024)
        ):
            raise _corrupt_video()
        audio_metadata = {"sample_rate_hz": rate, "channels": channels}

    # Fully decode the video, emitting only a small RGB raster per frame. This detects
    # black clips (including limited-range YUV black) and verifies the declared count.
    # Decode the optional soundtrack to a null sink in the same pass.
    sample_size = 64 * 64 * 3
    declared_count = video.get("nb_frames")
    expected_count = int(declared_count) if declared_count not in (None, "N/A") else None
    limit = min(16 * 1024**2, expected_count * sample_size) if expected_count else 16 * 1024**2
    audio_command = ["-map", f"0:{audio[0]['index']}", "-f", "null", "-"] if audio else []
    count, frames = read(
        [
            decoder,
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-err_detect",
            "explode",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            *options,
            "-i",
            str(path),
            "-map",
            f"0:{video['index']}",
            "-vf",
            "scale=64:64:flags=area,format=rgb24",
            "-fps_mode",
            "passthrough",
            "-threads",
            "1",
            "-f",
            "rawvideo",
            "-",
            *audio_command,
        ],
        limit,
    )
    frame_count, remainder = divmod(count, sample_size)
    if (
        remainder
        or not frame_count
        or (expected_count is not None and frame_count != expected_count)
        or not any(frames)
    ):
        raise _corrupt_video()
    if video_duration is None:
        video_duration = float(frame_count / fps)
        if not math.isfinite(video_duration) or video_duration <= 0:
            raise _corrupt_video()

    def duration_diverges(other: float | None) -> bool:
        # FFprobe reports microseconds. Compare at that precision so binary float
        # subtraction cannot turn exactly 250 ms into a passing value.
        return (
            other is not None
            and round(abs(other - video_duration), 6) >= MAX_DURATION_DRIFT_SECONDS
        )

    if duration_diverges(container_duration):
        raise DomainError(
            "corrupt_provider_output", 422, "Video stream and container durations diverge"
        )
    if duration_diverges(audio_duration):
        raise DomainError(
            "corrupt_provider_output", 422, "Video and audio stream durations diverge"
        )
    return {
        "dimensions": {"width": width, "height": height},
        "fps": {"numerator": fps.numerator, "denominator": fps.denominator},
        "frame_count": frame_count,
        "duration_seconds": round(video_duration, 6),
        **audio_metadata,
    }


def verify_video_bytes(
    data: bytes,
    *,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    timeout: float = 30,
    _max_memory_bytes: int | None = None,
) -> JSON:
    if len(data) > MAX_VIDEO_BYTES:
        raise DomainError("upload_too_large", 413)
    major_brand, _ = _ftyp_brands(data)
    # A compatible brand must not override a disallowed major brand (such as QuickTime).
    if major_brand not in MP4_BRANDS:
        raise DomainError("unsupported_media_type", 415)
    maximum = (
        DEFAULT_MAX_SUBPROCESS_MEMORY_BYTES if _max_memory_bytes is None else _max_memory_bytes
    )
    if type(maximum) is not int or maximum <= 0:
        raise DomainError("invalid_request", message="Invalid video verifier memory ceiling")
    limits = _decoder_limits(min(maximum, DEFAULT_MAX_SUBPROCESS_MEMORY_BYTES))
    decoder, probe = resolve_video_verifier({"ffmpeg": ffmpeg, "ffprobe": ffprobe})
    deadline = time.monotonic() + timeout
    try:
        with TemporaryDirectory(prefix="proscenium-video-verify-") as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(data)
            return _verify_staged_video(path, decoder, probe, deadline, limits)
    except OSError as exc:
        if exc.errno == errno.ENOMEM:
            raise _corrupt_video() from exc
        raise DomainError("media_unavailable", 503, "Local video verification unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise DomainError(
            "media_unavailable", 503, "Video verification exceeded its deadline"
        ) from exc
    except subprocess.SubprocessError as exc:
        raise DomainError("media_unavailable", 503, "Local video verification unavailable") from exc
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        ZeroDivisionError,
        OverflowError,
    ) as exc:
        raise _corrupt_video() from exc
