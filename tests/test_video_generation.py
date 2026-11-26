"""Offline MiniMax H3 API graphs, wire contracts and real MP4 verification."""

import io
import json
import math
import struct
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.catalogue.validation import sequence_map
from proscenium.contracts import canonical, validate
from proscenium.copilot.capabilities import get_project_capabilities
from proscenium.jobs.resources import Resources
from proscenium.providers import minimax_h3 as h3
from proscenium.providers import register_provider_artifact
from proscenium.providers.preparation import VideoPreparation, validate_recipes
from proscenium.sources.resolver import Resolver
from test_audio_generation import flac
from test_rc4_retry_and_context import DRAFT
from test_rc4_retry_and_context import sequence_runtime as sequence_runtime
from test_worker_capabilities_and_publication import PROJECT, invoke, publication
from test_worker_capabilities_and_publication import offline as offline
from test_worker_capabilities_and_publication import runtime as runtime


def workflow(mode="fl2va", **options):
    unet = h3.DEFAULT_FL2VA_MODEL if mode == "fl2va" else h3.DEFAULT_REF2VA_MODEL
    models = [unet, h3.DEFAULT_CLIP, h3.DEFAULT_VIDEO_VAE, h3.DEFAULT_AUDIO_VAE]
    if options.get("lora"):
        models.append(options["lora"])
    return h3.graph(
        mode=mode, unet=unet, clip=models[1], video_vae=models[2], audio_vae=models[3], **options
    ), models


@pytest.fixture
def catalog():
    # Relevant ComfyUI 0.45.0 object_info fields captured from the Strix Halo endpoint.
    # Unrelated model choices and the names of uploaded input files were removed.
    path = Path(__file__).parent / "fixtures" / "comfy-minimax-h3-object-info.json"
    return json.loads(path.read_text())


@pytest.mark.parametrize(
    "duration,count", [(0.2, 5), (0.9, 22), (4.4, 107), (5.0, 124), (10.0, 243), (15.0, 362)]
)
def test_align_frame_count(duration, count):
    assert h3.align_frame_count(duration) == count


@pytest.mark.parametrize("duration", [0, 0.199, 15.001, float("nan"), float("inf"), -float("inf")])
def test_align_frame_count_rejects_bad_duration(duration):
    with pytest.raises(DomainError, match="duration") as caught:
        h3.align_frame_count(duration)
    assert caught.value.code == "invalid_request"


def test_fl2va_prompt_and_keyframes(catalog):
    for anchors in (
        {},
        {"first_frame_artifact_id": "start"},
        {"first_frame_artifact_id": "start", "last_frame_artifact_id": "end"},
    ):
        graph, models = workflow(video_vae_dtype="bf16", **anchors)
        inputs = graph["conditioning"]["inputs"]
        assert graph["conditioning"]["class_type"] == "MiniMaxH3ImageToVideo"
        assert inputs["prompt"] == "$prompt"
        assert [inputs[k] for k in ("width", "height", "length")] == [864, 480, 124]
        assert graph["shift"]["inputs"] == {
            "model": ["model", 0],
            "shift_video": 12.0,
            "shift_audio": 3.0,
        }
        for key in ("first_frame", "last_frame"):
            if artifact_id := anchors.get(key + "_artifact_id"):
                assert inputs[key] == [key, 0]
                assert graph[key] == {
                    "class_type": "LoadImage",
                    "inputs": {"image": "artifact:" + artifact_id},
                }
            else:
                assert key not in graph and key not in inputs
        assert graph["guider"]["inputs"]["conditioning"] == ["conditioning", 0]
        assert graph["sample"]["inputs"]["latent_image"] == ["conditioning", 1]
        assert graph["decode_video"]["class_type"] == "VAEDecode"
        assert graph["decode_audio"]["inputs"] == {
            "samples": ["sample", 0],
            "vae": ["audio_vae", 0],
        }
        assert graph["create_video"]["inputs"] == dict(
            images=["decode_video", 0],
            audio=["decode_audio", 0],
            fps=24.0,
            bit_depth=8,
            color_space="sRGB",
        )
        assert graph["save"]["inputs"] == {
            "video": ["create_video", 0],
            "filename_prefix": "proscenium_video",
            "format": "mp4",
            "format.codec": "h264",
        }
        h3.preflight(graph, catalog, models)


@pytest.mark.parametrize(
    "steps,lora", [(4, h3.DEFAULT_FL2VA_LORA_4STEP), (8, h3.DEFAULT_FL2VA_LORA_8STEP)]
)
def test_fl2va_guides_lora_bf16_and_tiled_decode(catalog, steps, lora):
    assert not h3.requires_tiled_vae(864, 480, 124)
    assert h3.requires_tiled_vae(896, 480, 124)
    assert h3.requires_tiled_vae(864, 480, 141)
    graph, models = workflow(
        steps=steps,
        lora=lora,
        frame_count=141,
        tiled_vae=h3.requires_tiled_vae(864, 480, 141),
        video_vae_dtype="bf16",
        guide_frames=[
            {"artifact_id": "guide-a", "frame_index": 42},
            {"artifact_id": "guide-b", "frame_index": 140},
        ],
    )
    assert graph["shift"]["inputs"]["model"] == ["lora", 0]
    assert graph["lora"]["inputs"] == dict(model=["model", 0], lora_name=lora, strength_model=1.0)
    assert graph["sigmas"]["inputs"]["steps"] == steps
    assert graph["guide_0"]["inputs"] == dict(
        positive=["conditioning", 0],
        vae=["video_vae_dtype", 0],
        latent=["conditioning", 1],
        image=["guide_image_0", 0],
        frame_idx=42,
    )
    assert graph["guide_1"]["inputs"]["positive"] == ["guide_0", 0]
    assert graph["guide_1"]["inputs"]["frame_idx"] == 140
    assert graph["guider"]["inputs"]["conditioning"] == ["guide_1", 0]
    assert graph["video_vae_dtype"] == {
        "class_type": "VAEComputeDtype",
        "inputs": {"vae": ["video_vae", 0], "dtype": "bf16"},
    }
    assert graph["audio_vae"]["inputs"]["vae_name"] == h3.DEFAULT_AUDIO_VAE
    assert graph["decode_audio"]["inputs"]["vae"] == ["audio_vae", 0]
    assert graph["decode_video"] == {
        "class_type": "VAEDecodeTiled",
        "inputs": dict(
            samples=["sample", 0],
            vae=["video_vae_dtype", 0],
            tile_size=512,
            overlap=64,
            temporal_size=64,
            temporal_overlap=8,
        ),
    }
    h3.preflight(graph, catalog, models)


def test_ref2va_reference_slots_and_anchor(catalog):
    refs = [f"image-{i}" for i in range(9)]
    graph, models = workflow(
        "ref2va",
        first_frame_artifact_id="start",
        reference_artifact_ids=refs,
        ref_image_size="max",
        lora=h3.DEFAULT_REF2VA_LORA_4STEP,
        video_vae_dtype="bf16",
    )
    cond = graph["conditioning"]
    assert cond["class_type"] == "MiniMaxH3ReferenceToVideo"
    assert cond["inputs"]["ref_image_size"] == "max"
    assert cond["inputs"]["audio_vae"] == ["audio_vae", 0]
    assert "first_frame" not in cond["inputs"]
    for i, ref in enumerate(refs):
        assert cond["inputs"][f"ref_images.ref_image_{i}"] == [f"ref_{i}", 0]
        assert graph[f"ref_{i}"]["inputs"]["image"] == "artifact:" + ref
    assert graph["guide_start"]["inputs"] == dict(
        positive=["conditioning", 0],
        vae=["video_vae_dtype", 0],
        latent=["conditioning", 1],
        image=["first_frame", 0],
        frame_idx=0,
    )
    assert graph["guider"]["inputs"]["conditioning"] == ["guide_start", 0]
    h3.preflight(graph, catalog, models)


@pytest.mark.parametrize(
    "mode,options",
    [
        ("fl2va", {"width": 321}),
        ("fl2va", {"height": 224}),
        ("fl2va", {"width": 1344, "height": 800}),
        ("fl2va", {"frame_count": 125}),
        ("fl2va", {"frame_count": 379}),
        ("fl2va", {"steps": True}),
        ("fl2va", {"steps": 31}),
        ("fl2va", {"video_vae_dtype": "fp8"}),
        ("fl2va", {"reference_artifact_ids": ["image"]}),
        ("ref2va", {"last_frame_artifact_id": "image"}),
        ("ref2va", {"ref_image_size": "stretched"}),
        ("ref2va", {"reference_artifact_ids": ["same", "same"]}),
        ("ref2va", {"reference_artifact_ids": [f"image-{i}" for i in range(10)]}),
        ("ref2va", {"guide_frames": [{"artifact_id": "image", "frame_index": 0}]}),
        ("fl2va", {"guide_frames": [{"artifact_id": "image", "frame_index": 124}]}),
        ("fl2va", {"guide_frames": [{"artifact_id": "image", "frame_index": -1}]}),
        (
            "fl2va",
            {"guide_frames": [{"artifact_id": f"image-{i}", "frame_index": 2} for i in range(2)]},
        ),
        (
            "fl2va",
            {"guide_frames": [{"artifact_id": f"image-{i}", "frame_index": i} for i in range(5)]},
        ),
        ("fl2va", {"first_frame_artifact_id": "a/b"}),
        ("fl2va", {"last_frame_artifact_id": "a\\b"}),
        ("ref2va", {"reference_artifact_ids": ["a\0b"]}),
        ("other", {}),
    ],
)
def test_graph_rejects_invalid_canvas_mode_or_refs(mode, options):
    with pytest.raises(DomainError) as caught:
        workflow(mode, **options)
    assert caught.value.code == "invalid_request"


def test_bind_and_preflight_draft_and_uploaded_workflows(catalog):
    graph, models = workflow(
        "ref2va",
        first_frame_artifact_id="first",
        reference_artifact_ids=["r1", "r2"],
        video_vae_dtype="bf16",
    )
    saved = deepcopy(graph)
    provisional = h3.bind(
        graph,
        "Camera drifts forward",
        str(2**53 - 1),
        {key: "artifact:" + key for key in ("first", "r1", "r2")},
        models,
    )
    refs = {"first": "duplicate.png", "r1": "duplicate.png", "r2": "uploads/third.png"}
    with_uploads = h3.bind(provisional, "Camera drifts forward", f"00{2**53 - 1}", refs, models)
    assert with_uploads["conditioning"]["inputs"]["prompt"] == "Camera drifts forward"
    assert with_uploads["noise"]["inputs"]["noise_seed"] == 2**53 - 1
    assert [
        with_uploads[key]["inputs"]["image"] for key in ("first_frame", "ref_0", "ref_1")
    ] == list(refs.values())
    assert graph == saved
    for value in (graph, provisional, with_uploads):
        h3.preflight(value, catalog, models)


@pytest.mark.parametrize("seed", [None, "", "-1", "+1", "1.5", "1\n", "１", str(2**53), "1" * 5000])
def test_bind_rejects_unsafe_seed(seed):
    graph, models = workflow()
    with pytest.raises(DomainError, match="JSON-safe decimal seed"):
        h3.bind(graph, "Prompt", seed, {}, models)


@pytest.mark.parametrize(
    "node,key,value",
    [
        ("conditioning", "prompt", "Different prompt"),
        ("noise", "noise_seed", 43),
        ("noise", "noise_seed", True),
        ("sample", "latent_image", ["conditioning", True]),
        ("decode_audio", "vae", ["video_vae_dtype", 0]),
        ("video_vae_dtype", "dtype", "fp8"),
        ("ref_1", "image", "artifact:r1"),
        ("ref_1", "image", "already-uploaded.png"),
        ("save", "format.codec", "av1"),
    ],
)
def test_bind_rejects_graph_tampering(node, key, value):
    graph, models = workflow("ref2va", reference_artifact_ids=["r1", "r2"], video_vae_dtype="bf16")
    graph[node]["inputs"][key] = value
    with pytest.raises(DomainError) as caught:
        h3.bind(graph, "Prompt", "42", {"r1": "first.png", "r2": "second.png"}, models)
    assert caught.value.code == "invalid_request"


def test_bind_rejects_extra_nodes_inputs_and_models():
    graph, models = workflow(first_frame_artifact_id="start")
    for refs in (
        {},
        {"start": "file.png", "extra": "other.png"},
        {"start": "../file.png"},
        {"start": "file.png [output]"},
    ):
        with pytest.raises(DomainError, match="binding mismatch"):
            h3.bind(graph, "Prompt", "0", refs, models)
    for invalid_models in (models[:-1], [*models, "extra"], [*models, models[0]]):
        with pytest.raises(DomainError, match="model manifest"):
            h3.bind(graph, "Prompt", "0", {"start": "file.png"}, invalid_models)
    graph["extra"] = {"class_type": "PreviewImage", "inputs": {"images": ["decode_video", 0]}}
    with pytest.raises(DomainError, match="graph"):
        h3.bind(graph, "Prompt", "0", {"start": "file.png"}, models)


def test_preflight_validates_live_options_schema_and_output(catalog):
    graph, models = workflow("ref2va", reference_artifact_ids=["r1", "r2"], video_vae_dtype="bf16")
    missing_node = deepcopy(catalog)
    del missing_node["MiniMaxH3ReferenceToVideo"]
    missing_model = deepcopy(catalog)
    missing_model["UNETLoader"]["input"]["required"]["unet_name"] = ["STRING"]
    wrong_dtype = deepcopy(catalog)
    wrong_dtype["VAEComputeDtype"]["input"]["required"]["dtype"][1]["options"].remove("bf16")
    wrong_slots = deepcopy(catalog)
    wrong_slots["MiniMaxH3ReferenceToVideo"]["input"]["optional"]["ref_images"][1]["template"][
        "max"
    ] = 1
    wrong_codec = deepcopy(catalog)
    formats = wrong_codec["SaveVideo"]["input"]["required"]["format"][1]["options"]
    mp4 = next(option for option in formats if option["key"] == "mp4")
    codecs = mp4["inputs"]["required"]["codec"][1]["options"]
    codecs[:] = [option for option in codecs if option["key"] != "h264"]
    wrong_output = deepcopy(catalog)
    wrong_output["LoadImage"]["output_node"] = True
    for bad, code in [
        (missing_node, "capability_unavailable"),
        (missing_model, "capability_unavailable"),
        (wrong_dtype, "invalid_request"),
        (wrong_slots, "invalid_request"),
        (wrong_codec, "capability_unavailable"),
        (wrong_output, "invalid_request"),
    ]:
        with pytest.raises(DomainError) as caught:
            h3.preflight(graph, bad, models)
        assert caught.value.code == code


def test_catalog_requires_exact_8_step_filename(catalog):
    graph, models = workflow(steps=8, lora=h3.DEFAULT_FL2VA_LORA_8STEP, video_vae_dtype="bf16")
    h3.preflight(graph, catalog, models)
    stale = "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"
    graph["lora"]["inputs"]["lora_name"] = models[-1] = stale
    with pytest.raises(DomainError) as caught:
        h3.preflight(graph, catalog, models)
    assert caught.value.code == "capability_unavailable"


def test_preflight_requires_video_bf16_when_advertised(catalog):
    graph, models = workflow()
    with pytest.raises(DomainError, match="requires BF16 video VAE"):
        h3.preflight(graph, catalog, models)
    del catalog["VAEComputeDtype"]
    h3.preflight(graph, catalog, models)


TILED_CASES = [(1344, 768, 5, 124), (864, 480, 5.8, 141)]


@pytest.mark.parametrize("width,height,seconds,count", TILED_CASES)
def test_preflight_requires_tiling_for_pixels_or_frames(catalog, width, height, seconds, count):
    assert h3.align_frame_count(seconds) == count
    options = dict(width=width, height=height, frame_count=count, video_vae_dtype="bf16")
    tiled, models = workflow(**options, tiled_vae=True)
    untiled, _ = workflow(**options)
    h3.preflight(tiled, catalog, models)
    with pytest.raises(DomainError, match="requires tiled video VAE") as caught:
        h3.preflight(untiled, catalog, models)
    assert caught.value.code == "invalid_request"
    del catalog["VAEDecodeTiled"]
    for graph in (tiled, untiled):
        with pytest.raises(DomainError, match=h3.UNAVAILABLE) as caught:
            h3.preflight(graph, catalog, models)
        assert caught.value.code == "capability_unavailable"


BAD_VIDEO_MODELS = [
    (4, "model", "fl2va_model", "minimax_h3_fl2va_pruned_bf16.safetensors"),
    (4, "clip", "clip", "unrelated_encoder.safetensors"),
    (4, "lora", "fl2va_lora_4step", h3.DEFAULT_FL2VA_LORA_8STEP),
    (8, "lora", "fl2va_lora_8step", h3.DEFAULT_FL2VA_LORA_4STEP),
]


@pytest.mark.parametrize("steps,role,slot,name", BAD_VIDEO_MODELS)
def test_config_and_preflight_reject_wrong_precision_and_turbo_variant(
    catalog, steps, role, slot, name
):
    with pytest.raises(ValueError, match=h3.INVALID_GRAPH):
        validate_recipes({"video_recipes": {"comfy": {slot: name}}})
    lora = h3.DEFAULT_FL2VA_LORA_8STEP if steps == 8 else h3.DEFAULT_FL2VA_LORA_4STEP
    graph, models = workflow(steps=steps, lora=lora, video_vae_dtype="bf16")
    node, key = graph[role], h3.MODEL_INPUTS[role]
    node["inputs"][key] = models[list(h3.MODEL_INPUTS).index(role)] = name
    # A provider advertising the wrong model must not weaken local verification.
    choices = catalog[node["class_type"]]["input"]["required"][key][0]
    if name not in choices:
        choices.append(name)
    with pytest.raises(DomainError, match=h3.INVALID_GRAPH) as caught:
        h3.preflight(graph, catalog, models)
    assert caught.value.code == "invalid_request"


@pytest.mark.parametrize(
    "mode,mutations",
    [
        ("fl2va", {"video_vae": h3.DEFAULT_AUDIO_VAE, "audio_vae": h3.DEFAULT_VIDEO_VAE}),
        ("fl2va", {"model": h3.DEFAULT_REF2VA_MODEL}),
        ("ref2va", {"lora": h3.DEFAULT_FL2VA_LORA_4STEP}),
        ("fl2va", {"clip": "qwen3vl_32b_minimax_h3_nvfp4.safetensors"}),
        ("fl2va", {"audio_vae": "minimax_h3_audio_vae_fp16.safetensors"}),
    ],
)
def test_bind_and_preflight_reject_incompatible_model_roles(catalog, mode, mutations):
    lora = h3.DEFAULT_REF2VA_LORA_4STEP if mode == "ref2va" else h3.DEFAULT_FL2VA_LORA_4STEP
    graph, models = workflow(mode, lora=lora, video_vae_dtype="bf16")
    for role, name in mutations.items():
        graph[role]["inputs"][h3.MODEL_INPUTS[role]] = name
        models[list(h3.MODEL_INPUTS).index(role)] = name
    for verify in (
        lambda: h3.bind(graph, "Prompt", "42", {}, models),
        lambda: h3.preflight(graph, catalog, models),
    ):
        with pytest.raises(DomainError) as caught:
            verify()
        assert caught.value.code == "invalid_request"


def test_bind_and_preflight_reject_unordered_manifest_and_audio_cast(catalog):
    graph, models = workflow(video_vae_dtype="bf16")
    for options, manifest in ((graph, models[::-1]), (deepcopy(graph), models)):
        if manifest == models:
            options["video_vae_dtype"]["inputs"]["vae"] = ["audio_vae", 0]
        with pytest.raises(DomainError, match="model manifest"):
            h3.bind(options, "Prompt", "42", {}, manifest)
        with pytest.raises(DomainError, match="model manifest"):
            h3.preflight(options, catalog, manifest)


def test_extract_saved_video_descriptors():
    item = {
        "filename": "proscenium_video_00001_.MP4",
        "subfolder": "renders/video",
        "type": "output",
    }
    preview = {"videos": [{**item, "filename": "preview.mp4"}]}
    for key in ("images", "videos"):
        assert h3.output_descriptors({"outputs": {"preview": preview, "save": {key: [item]}}}) == [
            item
        ]
    root = {"filename": "file.mp4", "type": "output"}
    assert h3.output_descriptors({"outputs": {"save": {"videos": [root]}}}) == [root]
    for outputs in (
        {"preview": preview},
        {"save": {}},
        {"save": {"images": [item, item]}},
        {"save": {"images": [item], "videos": [item]}},
    ):
        with pytest.raises(DomainError) as caught:
            h3.output_descriptors({"outputs": outputs})
        assert (caught.value.code, caught.value.status) == ("corrupt_provider_output", 422)


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "temp"},
        {"type": "input"},
        {"type": None},
        {"filename": "preview.jpg"},
        {"filename": "../file.mp4"},
        {"filename": "sub/file.mp4"},
        {"filename": "sub\\file.mp4"},
        {"filename": "fil\0e.mp4"},
        {"filename": "file..mp4"},
        {"subfolder": "renders/../outside"},
        {"subfolder": "/absolute"},
        {"subfolder": "sub\\folder"},
        {"subfolder": "bad\0path"},
        {"subfolder": False},
    ],
)
def test_reject_unsafe_save_descriptors(overrides):
    item = {"filename": "file.mp4", "type": "output", **overrides}
    with pytest.raises(DomainError) as caught:
        h3.output_descriptors({"outputs": {"save": {"images": [item]}}})
    assert (caught.value.code, caught.value.status) == ("corrupt_provider_output", 422)


def mp4(
    path,
    *,
    source="testsrc2",
    audio=True,
    audio_seconds=1,
    muxer="mp4",
    subtitle=None,
    video_frames=24,
    size="96x64",
    fps=24,
):
    separator = ":" if "=" in source else "="
    video = [
        "-f",
        "lavfi",
        "-i",
        f"{source}{separator}size={size}:rate={fps},trim=end_frame={video_frames}",
    ]
    sound = (
        [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={audio_seconds}",
        ]
        if audio
        else []
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            *video,
            *sound,
            *(["-i", str(subtitle)] if subtitle is not None else []),
            *(["-c:a", "aac", "-ac", "2"] if audio else []),
            *(["-c:s", "mov_text"] if subtitle is not None else []),
            "-threads",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-f",
            muxer,
            str(path),
        ],
        check=True,
        timeout=10,
    )
    return path.read_bytes()


@pytest.mark.parametrize("audio", [True, False])
def test_verify_video_bytes_real_mp4(tmp_path, audio):
    data = mp4(tmp_path / "sample.mp4", audio=audio)
    audio_metadata = {"sample_rate_hz": 48000, "channels": 2} if audio else {}
    assert h3.verify_video_bytes(data) == dict(
        dimensions={"width": 96, "height": 64},
        fps={"numerator": 24, "denominator": 1},
        frame_count=24,
        duration_seconds=1.0,
        **audio_metadata,
    )


def test_verify_video_bytes_rejects_bad_media(tmp_path, monkeypatch):
    data = mp4(tmp_path / "sample.mp4")
    black = mp4(tmp_path / "black.mp4", source="color", audio=False)
    for bad, code, status in [
        (b"", "unsupported_media_type", 415),
        (b"not MP4", "unsupported_media_type", 415),
        (b"\0\0\0\x10ftypisom", "unsupported_media_type", 415),
        (b"\0\0\0\x0fftyp" + data[8:], "unsupported_media_type", 415),
        (b"\0\0\x04\x01ftyp" + data[8:], "unsupported_media_type", 415),
        (data[:8] + b"qt  " + data[12:], "unsupported_media_type", 415),
        (data[:32], "corrupt_provider_output", 422),
        (data[: len(data) // 2], "corrupt_provider_output", 422),
        (black, "corrupt_provider_output", 422),
    ]:
        with pytest.raises(DomainError) as caught:
            h3.verify_video_bytes(bad)
        assert (caught.value.code, caught.value.status) == (code, status)
    monkeypatch.setattr(h3, "MAX_VIDEO_BYTES", 8)
    with pytest.raises(DomainError) as caught:
        h3.verify_video_bytes(data)
    assert (caught.value.code, caught.value.status) == ("upload_too_large", 413)


def test_reject_quicktime_and_mp4_with_subtitle(tmp_path):
    quicktime = mp4(tmp_path / "sample.mov", muxer="mov")
    assert quicktime[4:12] == b"ftypqt  "
    subtitle = tmp_path / "sample.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:00,500\nSubtitles\n")
    extra_stream = mp4(tmp_path / "extra.mp4", subtitle=subtitle)
    for data in (quicktime, extra_stream):
        with pytest.raises(DomainError) as caught:
            h3.verify_video_bytes(data)
        assert (caught.value.code, caught.value.status) == ("unsupported_media_type", 415)


def test_subprocess_address_space_ceiling_fails_closed(tmp_path):
    data = mp4(tmp_path / "sample.mp4")
    with pytest.raises(DomainError) as caught:
        h3.verify_video_bytes(data, _max_memory_bytes=8 * 1024**2)
    assert (caught.value.code, caught.value.status) == ("corrupt_provider_output", 422)


def test_reject_mismatched_soundtrack_and_container_duration(tmp_path):
    data = mp4(tmp_path / "long-audio.mp4", audio_seconds=2)
    with pytest.raises(DomainError, match="Video stream and container durations diverge") as caught:
        h3.verify_video_bytes(data)
    assert (caught.value.code, caught.value.status) == ("corrupt_provider_output", 422)
    data = mp4(tmp_path / "short-audio.mp4", audio_seconds=0.25)
    with pytest.raises(DomainError, match="Video and audio stream durations diverge") as caught:
        h3.verify_video_bytes(data)
    assert (caught.value.code, caught.value.status) == ("corrupt_provider_output", 422)


def test_max_frame_video_rejects_long_soundtrack(tmp_path):
    data = mp4(tmp_path / "long-audio.mp4", video_frames=362, audio_seconds=17.0)
    with pytest.raises(DomainError, match="Video stream and container durations diverge") as caught:
        h3.verify_video_bytes(data)
    assert (caught.value.code, caught.value.status) == ("corrupt_provider_output", 422)


def test_max_frame_video_accepts_matched_soundtrack(tmp_path):
    data = mp4(tmp_path / "matched-audio.mp4", video_frames=362, audio_seconds=15.1)
    metadata = h3.verify_video_bytes(data)
    assert metadata["frame_count"] == 362
    assert metadata["duration_seconds"] == 15.083333
    assert metadata["sample_rate_hz"] == 48000


def test_reject_exact_quarter_second_drift(tmp_path):
    data = mp4(tmp_path / "boundary.mp4", audio_seconds=0.75)
    with pytest.raises(DomainError, match="Video and audio stream durations diverge") as caught:
        h3.verify_video_bytes(data)
    assert (caught.value.code, caught.value.status) == ("corrupt_provider_output", 422)


def test_video_verification_fails_closed_when_missing_or_out_of_time(tmp_path):
    data = mp4(tmp_path / "sample.mp4")
    with pytest.raises(DomainError) as missing:
        h3.verify_video_bytes(data, ffprobe=str(tmp_path / "not-installed"))
    assert (missing.value.code, missing.value.status) == ("capability_unavailable", 501)
    with pytest.raises(DomainError) as timeout:
        h3.verify_video_bytes(data, timeout=0)
    assert (timeout.value.code, timeout.value.status) == ("media_unavailable", 503)


@pytest.mark.parametrize(
    "seed,accepted",
    [
        ("0", True),
        ("42", True),
        ("00042", True),
        ("9007199254740991", True),
        ("9007199254740992", False),
        ("9999999999999999", False),
        ("1\n", False),
        ("-1", False),
        ("", False),
        ("00000000000000000", False),
    ],
)
def test_video_prepare_seed_contract(seed, accepted):
    request = dict(schema_version="1.0", operation_id="op", prompt="Prompt", seed=seed)
    if accepted:
        assert validate("VideoGenerationPrepare", request) == request
    else:
        with pytest.raises(DomainError):
            validate("VideoGenerationPrepare", request)


def test_video_prepare_and_prepared_contracts():
    prepare = {"schema_version": "1.0", "operation_id": "op", "prompt": "Rack focus", "seed": "12"}
    validate("VideoGenerationPrepare", prepare)
    validate(
        "VideoGenerationPrepare",
        {
            **prepare,
            "guide_frames": [{"artifact_id": "image", "frame_index": 361}],
            "mode": "fl2va",
            "duration_seconds": 15,
            "target_slot": {"kind": "item", "item_id": "custom-item"},
        },
    )
    for options in (
        {"duration_seconds": 0.9},
        {"guide_frames": [{"artifact_id": "image", "frame_index": 362}]},
        {"seed": 12},
        {"provider_id": None},
        {"target_slot": "custom-slot"},
        {"mode": "other"},
        {"extra": False},
    ):
        with pytest.raises(DomainError):
            validate("VideoGenerationPrepare", {**prepare, **options})
    provider = dict(
        schema_version="1.0",
        project_id="p",
        provider_id="comfy",
        model_ids=["model"],
        input_artifact_ids=[],
        workflow_artifact_id="workflow",
        prompt_artifact_id="prompt",
    )
    submit = dict(
        **provider,
        job_id="job",
        generation_task_id="task",
        target_id="target",
        capability="video",
        seed="12",
        settings={},
    )
    prepared = dict(
        **provider,
        operation_id="op",
        replayed=False,
        profile_revision=h3.REVISION,
        mode="fl2va",
        dimensions={"width": 864, "height": 480},
        duration_seconds=124 / 24,
        frame_count=124,
        fps={"numerator": 24, "denominator": 1},
        submit_request=submit,
    )
    validate("VideoGenerationPrepared", prepared)
    validate("VideoGenerationPrepared", {**prepared, "frame_count": 362, "duration_seconds": 15.5})
    for options in (
        {"duration_seconds": 0},
        {"duration_seconds": 15.51},
        {"frame_count": 4},
        {"frame_count": 363},
        {"fps": 24},
        {"submit_request": {**submit, "capability": "unsupported"}},
        {"submit_request": {**submit, "capability": "image"}},
    ):
        with pytest.raises(DomainError):
            validate("VideoGenerationPrepared", {**prepared, **options})


def test_sequence_clip_entry_and_audio_modes():
    frames = {"start": 1, "end": 24}
    base = dict(
        entry_id="entry",
        shot_id=None,
        source_frames=frames,
        sequence_frames=frames,
        transform="trim",
        mapping_artifact_id=None,
    )
    rendered = dict(**base, version_id="version", job_id="job")
    clip = dict(**base, clip_artifact_id="video", include_audio=True, choice="agent")
    validate("SequenceEntry", rendered)
    validate("SequenceClipEntry", clip)
    for options in ({"job_id": "job"}, {"transform": "retime"}, {"mapping_artifact_id": "map"}):
        with pytest.raises(DomainError):
            validate("SequenceClipEntry", {**clip, **options})
    sequence = dict(
        schema_version="1.0",
        sequence_version_id="sequence",
        project_id="p",
        fps={"numerator": 24, "denominator": 1},
        frame_count=24,
    )
    music = [{"track_id": "music", "artifact_id": "audio", "start_seconds": 0}]
    for entries in ([rendered], [clip], [rendered, clip]):
        validate("SequenceMap", dict(**sequence, entries=entries, silent=True))
        validate("SequenceMap", dict(**sequence, entries=entries, silent=False, audio_tracks=music))
    native = dict(**sequence, entries=[clip], silent=False, preserve_clip_audio=True)
    validate("SequenceMap", native)
    validate("SequenceMap", dict(**native, audio_tracks=[]))
    validate("SequenceMap", dict(**native, audio_tracks=music))
    validate("SequenceMap", {**native, "preserve_clip_audio": False, "audio_tracks": music})
    for options in (
        {"entries": [rendered]},
        {"silent": True},
        {"preserve_clip_audio": False},
    ):
        with pytest.raises(DomainError):
            validate("SequenceMap", {**native, **options})
    with pytest.raises(DomainError):
        validate("SequenceMap", dict(**sequence, entries=[rendered], silent=False, audio_tracks=[]))


def configure_video(runtime):
    services = runtime.services
    provider = runtime.provider
    services.settings = replace(
        services.settings, services=({**services.settings.services[0], "capabilities": ["video"]},)
    )
    services.extensions["video_preparation"] = VideoPreparation(services, provider)
    services.resources = Resources(runtime.store)
    provider.registrar = lambda caller, request: register_provider_artifact(
        services, caller, request
    )
    return services, provider


def video_request(operation, **fields):
    return {
        "schema_version": "1.0",
        "operation_id": operation,
        "prompt": "A mechanical owl",
        "seed": "42",
        **fields,
    }


@pytest.mark.parametrize("reason", [None, "nodes_or_models_missing", "video_verifier_missing"])
def test_video_discovery_and_fail_closed(runtime, catalog, tmp_path, reason):
    services, provider = configure_video(runtime)
    calls = []

    def respond(request):
        calls.append(request.url.path)
        assert request.url.path == "/object_info"
        return httpx.Response(200, json={} if reason == "nodes_or_models_missing" else catalog)

    if reason == "video_verifier_missing":
        services.settings = replace(
            services.settings, rendering={"ffmpeg": str(tmp_path / "not-installed")}
        )
    provider.client.close()
    with httpx.Client(transport=httpx.MockTransport(respond)) as provider.client:
        capabilities = get_project_capabilities(services, PROJECT)
        assert "prepare_video_generation" in capabilities["exposed_tools"]
        (row,) = capabilities["providers"]
        assert row["status"] == ("unavailable" if reason else "ready")
        assert row["reason_code"] == reason
        assert row["profile_revision"] == h3.REVISION
        assert row["default_dimensions"] == {"width": 864, "height": 480}
        assert row["default_steps"] == 4 and row["max_pixels"] == 1032192
        if not reason:
            assert "fl2va, ref2va" in row["summary"]
            assert h3.DEFAULT_FL2VA_LORA_4STEP in row["selected_models"]
            assert h3.DEFAULT_FL2VA_LORA_8STEP in row["compatible_models"]
            assert h3.DEFAULT_REF2VA_LORA_4STEP in row["compatible_models"]
        else:
            response, kind = invoke(
                runtime, "prepare_video_generation", video_request("unavailable"), "unavailable"
            )
            assert kind == "failure" and response["error"] == "capability_unavailable"
            if reason == "video_verifier_missing":
                assert response["message"] == h3.VIDEO_VERIFY_UNAVAILABLE
                assert not calls


@pytest.mark.parametrize(
    "overrides",
    [
        {"width": 865},
        {"width": 1344, "height": 800},
        {"steps": 31},
        {"steps": True},
        {"clip": "nvfp4.safetensors"},
        {"tiled_vae": 1},
        {"shift_audio": float("nan")},
        {"minimax": "unexpected"},
        {"profile_revision": "unknown"},
    ],
)
def test_invalid_video_recipe(overrides):
    with pytest.raises(ValueError):
        validate_recipes({"video_recipes": {"comfy": overrides}})


@pytest.mark.parametrize("width,height,seconds,count", TILED_CASES)
def test_video_discovery_and_preparation_cannot_fall_back_to_untiled(
    runtime, catalog, width, height, seconds, count
):
    services, provider = configure_video(runtime)
    del catalog["VAEDecodeTiled"]
    provider.options["video_recipes"] = {"comfy": {"width": width, "height": height}}
    assert h3.align_frame_count(seconds) == count

    def respond(request):
        assert request.url.path == "/object_info"
        return httpx.Response(200, json=catalog)

    provider.client.close()
    with httpx.Client(transport=httpx.MockTransport(respond)) as provider.client:
        (row,) = get_project_capabilities(services, PROJECT)["providers"]
        # Discovery probes the default duration, which is exactly at the frame threshold.
        assert row["status"] == ("unavailable" if width == 1344 else "ready")
        error, status = invoke(
            runtime,
            "prepare_video_generation",
            video_request("needs-tiling", duration_seconds=seconds),
            "needs-tiling",
        )
        assert status == "failure" and error["error"] == "capability_unavailable"
        assert error["message"] == h3.UNAVAILABLE
        with runtime.store.transaction(write=False) as tx:
            assert not tx.all("providers.prepared")


def test_video_discovered_eight_step_fallback_is_the_prepared_default(runtime, catalog):
    services, provider = configure_video(runtime)
    catalog["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0].remove(
        h3.DEFAULT_FL2VA_LORA_4STEP
    )

    def respond(request):
        assert request.url.path == "/object_info"
        return httpx.Response(200, json=catalog)

    provider.client.close()
    with httpx.Client(transport=httpx.MockTransport(respond)) as provider.client:
        (row,) = get_project_capabilities(services, PROJECT)["providers"]
        assert row["status"] == "ready" and row["default_steps"] == 8
        prepared, status = invoke(
            runtime, "prepare_video_generation", video_request("fallback"), "fallback"
        )
        assert status == "success" and prepared["model_ids"] == row["selected_models"]
        prepared, status = invoke(
            runtime, "prepare_video_generation", video_request("base-model", steps=15), "base-model"
        )
        assert status == "success" and prepared["model_ids"] == row["selected_models"][:-1]


def image_inputs(runtime, names):
    artifacts = runtime.services.extension("artifacts")
    with runtime.store.transaction() as tx:
        for i, name in enumerate(names):
            stream = io.BytesIO()
            Image.new("RGB", (64, 64), color=(i * 50, 80, 120)).save(stream, format="PNG")
            artifacts.put(
                tx,
                artifacts.promote(
                    PROJECT,
                    name,
                    stream.getvalue(),
                    kind="image",
                    purpose="keyframe",
                    media_type="image/png",
                ),
            )


@pytest.mark.parametrize("mode", ["fl2va", "ref2va"])
def test_video_prepare_submit_and_pump(runtime, catalog, tmp_path, mode):
    services, provider = configure_video(runtime)
    image_inputs(runtime, ["anchor", "last", "guide", "picture-1", "picture-2"])
    width, height = (1344, 768) if mode == "fl2va" else (864, 480)
    fps = {"numerator": 24, "denominator": 1}
    frame_count = h3.align_frame_count(1)
    duration = frame_count / 24
    data = mp4(
        tmp_path / "output.mp4",
        size=f"{width}x{height}",
        video_frames=frame_count,
        audio_seconds=duration,
    )
    if mode == "fl2va":
        fields = dict(
            width=width,
            height=height,
            steps=8,
            last_frame_artifact_id="last",
            guide_frames=[
                {"artifact_id": "guide", "frame_index": 9},
                {"artifact_id": "anchor", "frame_index": 15},
            ],
        )
        expected = ["anchor", "last", "guide"]
    else:
        fields = dict(
            mode=mode,
            prompt="<Picture 1> meets <Picture 2>",
            reference_artifact_ids=["picture-1", "picture-2"],
        )
        expected = ["anchor", "picture-1", "picture-2"]

    calls, posts, uploads = [], [], []

    def respond(request):
        path = request.url.path
        calls.append(path)
        if path == "/upload/image":
            identity = expected[len(uploads)]
            _, pixels = provider.image_bytes(PROJECT, identity)
            assert pixels in request.content
            uploads.append(identity)
            return httpx.Response(200, json={"name": identity + ".png", "subfolder": "fixture"})
        if path == "/prompt":
            posts.append(json.loads(request.content)["prompt"])
            return httpx.Response(200, json={"prompt_id": "video-prompt"})
        if path == "/view":
            assert request.url.params["filename"] == "output.mp4"
            return httpx.Response(200, content=data)
        return httpx.Response(
            200,
            json={
                "/object_info": catalog,
                "/queue": {"queue_running": [], "queue_pending": []},
                "/system_stats": {"devices": [{"vram_free": 2**30}]},
                "/history/video-prompt": {
                    "video-prompt": {
                        "status": {"completed": True},
                        "outputs": {
                            "save": {"videos": [{"filename": "output.mp4", "type": "output"}]}
                        },
                    }
                },
            }[path],
        )

    body = video_request(
        "prepare-video", **fields, duration_seconds=1, first_frame_artifact_id="anchor"
    )
    provider.client.close()
    with httpx.Client(transport=httpx.MockTransport(respond)) as provider.client:
        preparation, kind = invoke(runtime, "prepare_video_generation", body, "prepare-video")
        assert kind == "success", preparation
        assert preparation["mode"] == mode
        assert preparation["input_artifact_ids"] == expected
        assert preparation["mode"] == mode and preparation["fps"] == fps
        assert preparation["duration_seconds"] == round(duration, 6)
        assert preparation["frame_count"] == frame_count
        assert preparation["dimensions"] == {"width": width, "height": height}
        repeated, kind = invoke(runtime, "prepare_video_generation", body, "replay-video")
        assert kind == "success" and repeated == {**preparation, "replayed": True}
        assert calls == ["/object_info"]
        submit = preparation["submit_request"]
        job, kind = invoke(runtime, "submit_generation", submit, "submit-video")
        assert kind == "success" and job["kind"] == "video_generation", job
        (finished,) = provider.pump(PROJECT, job["job_id"])
        assert finished["state"] == "succeeded", finished
        assert uploads == expected
        (graph,) = posts
        assert graph["video_vae_dtype"]["inputs"] == {"dtype": "bf16", "vae": ["video_vae", 0]}
        assert graph["audio_vae"]["inputs"]["vae_name"] == h3.DEFAULT_AUDIO_VAE
        assert graph["decode_audio"]["inputs"]["vae"] == ["audio_vae", 0]
        assert graph["first_frame"]["inputs"]["image"] == "fixture/anchor.png"
        if mode == "fl2va":
            assert graph["decode_video"]["class_type"] == "VAEDecodeTiled"
            assert graph["lora"]["inputs"]["lora_name"] == h3.DEFAULT_FL2VA_LORA_8STEP
            assert graph["conditioning"]["inputs"]["last_frame"] == ["last_frame", 0]
            assert graph["guide_0"]["inputs"]["frame_idx"] == 9
            assert graph["guide_image_1"]["inputs"]["image"] == "fixture/anchor.png"
        else:
            conditioning = graph["conditioning"]["inputs"]
            assert conditioning["prompt"] == "<Picture 1> meets <Picture 2>"
            assert conditioning["ref_images.ref_image_0"] == ["ref_0", 0]
            assert conditioning["ref_images.ref_image_1"] == ["ref_1", 0]
            assert graph["ref_0"]["inputs"]["image"] == "fixture/picture-1.png"
            assert graph["ref_1"]["inputs"]["image"] == "fixture/picture-2.png"
            assert graph["guide_start"]["class_type"] == "MiniMaxH3AddGuide"
            assert graph["guide_start"]["inputs"]["frame_idx"] == 0
        artifacts = services.extension("artifacts")
        video, provenance = finished["artifact_ids"]
        record = artifacts.get(PROJECT, video)
        inspection = h3.verify_video_bytes(data)
        assert all(record[k] == v for k, v in inspection.items())
        assert record["sample_rate_hz"] == 48000 and record["channels"] == 2
        assert (record["kind"], record["state"], record["purpose"]) == ("video", "ready", "concept")
        evidence = json.loads(artifacts.ready_path(PROJECT, provenance).read_bytes())
        assert evidence["output_inspections"][0]["inspection"] == inspection
        assert len(evidence["derived_images"]) == len(expected)
        for derivative_id, original_id in zip(evidence["derived_images"], expected, strict=True):
            derivative, png = artifacts.ready_bytes(PROJECT, derivative_id)
            assert png == provider.image_bytes(PROJECT, original_id)[1]
            assert derivative["media_type"] == "image/png"
            assert derivative["derived_from"] == [
                {
                    "kind": "encode",
                    "input_artifact_ids": [original_id],
                    "parameters": {"encoding": "RGB PNG", "exif_transposed": True},
                }
            ]
        with runtime.store.transaction(write=False) as tx:
            assert tx.connection.execute("SELECT COUNT(*) FROM resource_claims").fetchone()[0] == 0


def test_video_bad_media_consumes_three_attempts_and_releases_gpu(runtime, catalog, tmp_path):
    _, provider = configure_video(runtime)
    data = mp4(tmp_path / "black.mp4", source="color", audio=False)
    responses = [data[:-100], data, b"not an MP4"]
    posts = []

    def respond(request):
        path = request.url.path
        if path == "/prompt":
            posts.append(path)
            return httpx.Response(200, json={"prompt_id": "video-prompt"})
        if path == "/view":
            return httpx.Response(200, content=responses[len(posts) - 1])
        return httpx.Response(
            200,
            json={
                "/object_info": catalog,
                "/queue": {},
                "/system_stats": {"devices": [{"vram_free": 2**30}]},
                "/history/video-prompt": {
                    "video-prompt": {
                        "status": {"completed": True},
                        "outputs": {
                            "save": {"images": [{"filename": "output.mp4", "type": "output"}]}
                        },
                    }
                },
            }[path],
        )

    provider.client.close()
    with httpx.Client(transport=httpx.MockTransport(respond)) as provider.client:
        for i, error in enumerate(
            [
                "corrupt_provider_output",
                "corrupt_provider_output",
                "unsupported_media_type",
                "generation_budget_exhausted",
            ]
        ):
            body = video_request(f"prepare-{i}")
            prepared, kind = invoke(runtime, "prepare_video_generation", body, f"prepare-{i}")
            assert kind == "success", prepared
            job, kind = invoke(
                runtime, "submit_generation", prepared["submit_request"], f"submit-{i}"
            )
            if i == 3:
                assert kind == "failure" and job["error"] == error
                break
            assert kind == "success", job
            (finished,) = provider.pump(PROJECT, job["job_id"])
            assert finished["state"] == "failed" and finished["error_code"] == error, finished
            assert not finished["artifact_ids"]
            with runtime.store.transaction(write=False) as tx:
                assert (
                    tx.connection.execute("SELECT COUNT(*) FROM resource_claims").fetchone()[0] == 0
                )
                assert (
                    tx.connection.execute(
                        "SELECT COUNT(*) FROM budget_reservations WHERE state='consumed'"
                    ).fetchone()[0]
                    == i + 1
                )
        assert len(posts) == 3


@pytest.mark.parametrize(
    "size,count,fps", [("320x256", 22, 24), ("864x480", 24, 24), ("864x480", 22, 30)]
)
def test_video_rejects_decodable_output_that_does_not_match_admitted_graph(
    runtime, catalog, tmp_path, size, count, fps
):
    _, provider = configure_video(runtime)
    data = mp4(
        tmp_path / "different.mp4",
        size=size,
        fps=fps,
        video_frames=count,
        audio_seconds=count / fps,
    )
    # This is valid playable media. Only the match against the frozen graph should fail.
    inspection = h3.verify_video_bytes(data)
    assert inspection["frame_count"] == count and inspection["channels"] == 2

    def respond(request):
        path = request.url.path
        if path == "/view":
            return httpx.Response(200, content=data)
        return httpx.Response(
            200,
            json={
                "/object_info": catalog,
                "/queue": {},
                "/system_stats": {"devices": [{"vram_free": 2**30}]},
                "/prompt": {"prompt_id": "mismatched-video"},
                "/history/mismatched-video": {
                    "mismatched-video": {
                        "status": {"completed": True},
                        "outputs": {
                            "save": {"videos": [{"filename": "different.mp4", "type": "output"}]}
                        },
                    }
                },
            }[path],
        )

    provider.client.close()
    with httpx.Client(transport=httpx.MockTransport(respond)) as provider.client:
        prepared, status = invoke(
            runtime,
            "prepare_video_generation",
            video_request("prepare-mismatch", duration_seconds=1),
            "prepare-mismatch",
        )
        assert status == "success", prepared
        job, status = invoke(
            runtime, "submit_generation", prepared["submit_request"], "submit-mismatch"
        )
        assert status == "success", job
        (finished,) = provider.pump(PROJECT, job["job_id"])
        assert (finished["state"], finished["error_code"], finished["artifact_ids"]) == (
            "failed",
            "corrupt_provider_output",
            [],
        )
        assert provider.execution(PROJECT, job["job_id"])["phase"] == "failed"
        with runtime.store.transaction(write=False) as tx:
            assert {a["artifact_id"] for a in tx.all("artifacts")} == {
                prepared["workflow_artifact_id"],
                prepared["prompt_artifact_id"],
            }
            assert not tx.connection.execute(
                "SELECT 1 FROM resource_claims WHERE resource='heavy-gpu'"
            ).fetchone()


def store_clip(runtime, identity, data):
    artifacts = runtime.services.extension("artifacts")
    artifact = artifacts.promote(
        PROJECT,
        identity,
        data,
        kind="video",
        purpose="concept",
        media_type="video/mp4",
        metadata=h3.verify_video_bytes(data),
    )
    with runtime.store.transaction() as tx:
        artifacts.put(tx, artifact)
    return artifact


def clip_sequence():
    return dict(
        schema_version="1.0",
        project_id=PROJECT,
        sequence_version_id=DRAFT["version_id"],
        fps={"numerator": 24, "denominator": 1},
        frame_count=24,
        silent=True,
        entries=[
            dict(
                entry_id=f"entry-{i}",
                shot_id=None,
                clip_artifact_id=identity,
                source_frames={"start": i + 2, "end": i + 13},
                sequence_frames={"start": i * 12 + 1, "end": i * 12 + 12},
                transform="trim",
                mapping_artifact_id=None,
            )
            for i, identity in enumerate(("clip-z", "clip-a"))
        ],
    )


def publish_clip_sequence(runtime, sequence):
    request = publication(runtime, canonical(sequence))
    request["items"][0].update(**DRAFT, kind="sequence", name="Generated storyboard reel")
    catalogue = runtime.services.extension("catalogue")
    catalogue.sources = Resolver(runtime.services)
    receipt, status = invoke(runtime, "publish_outputs", request, "publish-clips")
    assert status == "success", receipt
    return catalogue


def admit_clip_assembly(runtime, job_id):
    body = dict(
        operation_id=f"assemble-{job_id}",
        job_id=job_id,
        execution_profile_id="blender-cpu",
        version=DRAFT,
    )
    job, status = invoke(runtime, "assemble_sequence", body, body["operation_id"])
    assert status == "success" and job["state"] == "planned", job
    return job


def run_clip_worker(runtime, job, monkeypatch):
    spawn = subprocess.Popen
    processes = []
    attempt_root = runtime.previews.root(job)

    def owned(argv, **kwargs):
        assert argv == [sys.executable, "-m", "proscenium.previews.worker", str(attempt_root)]
        process = spawn(argv, **kwargs)
        processes.append(process)
        return process

    try:
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, "Popen", owned)
            runtime.previews.pump(PROJECT)
        assert len(processes) == 1 and processes[0].wait(timeout=45) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    return attempt_root


def assert_clip_audio_windows(path, mode):
    rate = 48000
    pcm = subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "f32le",
            "-",
        ],
        timeout=10,
    )
    samples = [v for (v,) in struct.iter_unpack("<f", pcm)]
    assert len(samples) >= rate

    def check(start, end, *, native=False, external=False):
        window = samples[round(start * rate) : round(end * rate)]
        rms = math.sqrt(sum(x * x for x in window) / len(window))
        assert (rms > 0.02) if native or external else (rms < 0.003)
        # A non-zero RMS is not enough: isolate the amplitude at each fixture tone.
        for hz, expected in ((440, native), (880, external)):
            omega = math.tau * hz / rate
            sin = sum(x * math.sin(i * omega) for i, x in enumerate(window))
            cos = sum(x * math.cos(i * omega) for i, x in enumerate(window))
            amplitude = 2 * math.hypot(sin, cos) / len(window)
            assert (amplitude > 0.02) if expected else (amplitude < 0.005)

    native = mode in {"native", "native-plus-external"}
    external = mode in {"external", "native-plus-external"}
    check(0.05, 0.15, native=native)
    check(0.40, 0.475, native=native, external=external)
    check(0.60, 0.70, external=external)
    check(0.875, 0.975)


@pytest.mark.parametrize("mode", ["native", "silent", "external", "native-plus-external"])
def test_storyboard_clip_publication_and_assembly(sequence_runtime, tmp_path, monkeypatch, mode):
    runtime = sequence_runtime
    services = runtime.services
    artifacts = services.extension("artifacts")
    sequence = clip_sequence()
    input_ids = {"clip-z", "clip-a"}
    store_clip(runtime, "clip-z", mp4(tmp_path / "z.mp4", source="color=c=red"))
    store_clip(
        runtime,
        "clip-a",
        mp4(
            tmp_path / "a.mp4",
            source="color=c=blue",
            size="64x96",
            fps=30,
            video_frames=30,
            audio=False,
        ),
    )
    if mode != "silent":
        sequence.update(silent=False, preserve_clip_audio=mode != "external")
    if mode in {"external", "native-plus-external"}:
        soundtrack = artifacts.promote(
            PROJECT,
            "underscoring",
            flac(tmp_path / "track.flac", frequency=880, seconds=1),
            kind="audio",
            purpose="concept",
            media_type="audio/flac",
        )
        with runtime.store.transaction() as tx:
            artifacts.put(tx, soundtrack)
        sequence["audio_tracks"] = [
            {
                "track_id": "music",
                "artifact_id": "underscoring",
                "start_seconds": 0.375,
                "duration_seconds": 0.4375,
                "gain_db": -3,
            }
        ]
        input_ids.add("underscoring")

    catalogue = publish_clip_sequence(runtime, sequence)
    job = admit_clip_assembly(runtime, "storyboard-reel")
    with runtime.store.transaction(write=False) as tx:
        snapshot = tx.require("preview_assemblies", job["job_id"])
        admission = tx.require("job_inputs", job["job_id"])["admission"]
    assert not snapshot["input_frames"] and snapshot["frame_map"] is None
    assert sorted(row["artifact_id"] for row in snapshot["input_clips"]) == ["clip-a", "clip-z"]
    assert admission["input_artifact_ids"] == sorted(input_ids)

    attempt_root = run_clip_worker(runtime, job, monkeypatch)
    runtime.previews.reconcile(PROJECT)
    finished = services.jobs.get(PROJECT, job["job_id"])
    assert finished["state"] == "succeeded", finished
    video_id, manifest_id = finished["artifact_ids"]
    record = artifacts.get(PROJECT, video_id)
    assert record["dimensions"] == {"width": 96, "height": 64}
    assert record["frame_count"] == 24 and record["duration_seconds"] == 1.0
    assert record["derived_from"][0]["input_artifact_ids"] == sorted(input_ids)
    assert set(finished["artifact_ids"]) <= set(
        catalogue.version(PROJECT, DRAFT["version_id"])["artifact_ids"]
    )
    path = artifacts.ready_path(PROJECT, video_id)
    report = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)], timeout=10
        )
    )
    assert [s["codec_name"] for s in report["streams"]] == (
        ["h264"] if mode == "silent" else ["h264", "aac"]
    )
    if mode != "silent":
        assert int(report["streams"][1]["sample_rate"]) == 48000
        assert report["streams"][1]["channels"] == 2
        assert_clip_audio_windows(path, mode)
    pixels = subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-an",
            "-vf",
            "crop=2:2,scale=1:1,format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        timeout=10,
    )
    primaries = [
        max(range(3), key=lambda channel: pixels[i + channel]) for i in range(0, len(pixels), 3)
    ]
    assert primaries == [0] * 12 + [2] * 12
    manifest = json.loads(artifacts.ready_path(PROJECT, manifest_id).read_bytes())
    assert manifest["sequence"] == sequence and manifest["input_clips"] == snapshot["input_clips"]
    assert not (attempt_root / "frame-map.json").exists()


@pytest.mark.parametrize(
    "filename,replacement,error",
    [
        ("delivery.mp4", b"MP4 changed after the worker exited", "hash_mismatch"),
        ("delivery.mp4", None, "media_unavailable"),
        ("assembly-manifest.json", None, "media_unavailable"),
        ("assembly-manifest.json", b"{invalid JSON", "corrupt_provider_output"),
        ("assembly-manifest.json", b"{}", "corrupt_provider_output"),
        (
            "assembly-manifest.json",
            b'{"input_digest": [], "video_sha256": "wrong-type-test"}',
            "corrupt_provider_output",
        ),
    ],
)
def test_rejected_encode_publication_settles_and_releases_writer(
    sequence_runtime, tmp_path, monkeypatch, filename, replacement, error
):
    runtime = sequence_runtime
    services = runtime.services
    store_clip(runtime, "clip-z", mp4(tmp_path / "z.mp4", audio=False))
    store_clip(runtime, "clip-a", mp4(tmp_path / "a.mp4", audio=False))
    publish_clip_sequence(runtime, clip_sequence())
    first = admit_clip_assembly(runtime, "rejected-reel")
    root = run_clip_worker(runtime, first, monkeypatch)
    assert (root / "assembly-ready.json").exists()
    assert json.loads((root / "terminal.json").read_bytes())["state"] == "succeeded"

    def claim():
        with services.resources.store.transaction(write=False) as tx:
            row = tx.connection.execute(
                "SELECT job_id, attempt_id FROM resource_claims WHERE resource='ffmpeg-encode-writer'"
            ).fetchone()
            return tuple(row) if row is not None else None

    assert claim() == (first["job_id"], first["attempt_id"])
    if replacement is None:
        (root / filename).unlink()
    else:
        (root / filename).write_bytes(replacement)
    runtime.previews.reconcile(PROJECT)
    failed = services.jobs.get(PROJECT, first["job_id"])
    assert (failed["state"], failed["error_code"], failed["artifact_ids"]) == (
        "failed",
        error,
        [],
    )
    with runtime.store.transaction(write=False) as tx:
        assert {row["artifact_id"] for row in tx.all("artifacts")} == {"clip-a", "clip-z"}
    assert claim() is None

    second = admit_clip_assembly(runtime, "recovered-reel")
    run_clip_worker(runtime, second, monkeypatch)
    assert claim() == (second["job_id"], second["attempt_id"])
    runtime.previews.reconcile(PROJECT)
    assert services.jobs.get(PROJECT, second["job_id"])["state"] == "succeeded"
    assert claim() is None


def test_storyboard_clip_semantics_reject_mixed_invalid_and_missing_inputs(runtime, tmp_path):
    store_clip(runtime, "clip-z", mp4(tmp_path / "z.mp4", audio=False))
    store_clip(runtime, "clip-a", mp4(tmp_path / "a.mp4", audio=False))
    document = clip_sequence()
    first, second = document["entries"]
    render = {k: v for k, v in second.items() if k != "clip_artifact_id"}
    render.update(version_id="scene-version", job_id="render-job")
    with runtime.store.transaction(write=False) as tx:
        sequence_map(tx, document, DRAFT["version_id"], {})
        for entries, error in (
            ([first, render], "invalid_frame_mapping"),
            (
                [first, {**second, "source_frames": {"start": 14, "end": 25}}],
                "invalid_frame_mapping",
            ),
            ([first, {**second, "entry_id": first["entry_id"]}], "invalid_frame_mapping"),
            ([first, {**second, "clip_artifact_id": "absent"}], "media_unavailable"),
        ):
            with pytest.raises(DomainError) as caught:
                sequence_map(tx, {**document, "entries": entries}, DRAFT["version_id"], {})
            assert (caught.value.code, caught.value.status) == (error, 422)
        with pytest.raises(DomainError, match="require at least one audio track"):
            sequence_map(
                tx,
                {**document, "silent": False, "preserve_clip_audio": False},
                DRAFT["version_id"],
                {},
            )
