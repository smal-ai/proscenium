"""Offline MiniMax H3 API graphs, wire contracts and real MP4 verification."""

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from proscenium.api.errors import DomainError
from proscenium.catalogue.validation import sequence_map
from proscenium.contracts import validate
from proscenium.copilot.capabilities import probe_provider_capabilities
from proscenium.providers import minimax_h3 as h3


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
):
    video = ["-f", "lavfi", "-i", f"{source}=size=96x64:rate=24,trim=end_frame={video_frames}"]
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
            "target_slot": "custom-slot",
        },
    )
    for options in (
        {"duration_seconds": 0.9},
        {"guide_frames": [{"artifact_id": "image", "frame_index": 362}]},
        {"seed": 12},
        {"provider_id": None},
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
    for options in (
        {"entries": [rendered]},
        {"silent": True},
        {"audio_tracks": music},
        {"preserve_clip_audio": False},
    ):
        with pytest.raises(DomainError):
            validate("SequenceMap", {**native, **options})
    with pytest.raises(DomainError):
        validate("SequenceMap", dict(**sequence, entries=[rendered], silent=False, audio_tracks=[]))


@pytest.mark.parametrize("audio", [False, True])
def test_clip_sequence_semantic_validation_is_unavailable_in_wave_one(audio):
    frames = {"start": 1, "end": 24}
    clip = dict(
        entry_id="entry",
        shot_id=None,
        clip_artifact_id="video",
        source_frames=frames,
        sequence_frames=frames,
        transform="trim",
        mapping_artifact_id=None,
        include_audio=audio,
    )
    document = dict(
        schema_version="1.0",
        sequence_version_id="sequence",
        project_id="p",
        fps={"numerator": 24, "denominator": 1},
        frame_count=24,
        entries=[clip],
        silent=not audio,
    )
    if audio:
        document.update(preserve_clip_audio=True, audio_tracks=[])
    validate("SequenceMap", document)
    with pytest.raises(
        DomainError, match="Clip-based sequence assembly is not yet enabled"
    ) as caught:
        sequence_map(SimpleNamespace(project_id="p"), document, "sequence", {})
    assert (caught.value.code, caught.value.status) == ("capability_unavailable", 501)


def test_configured_video_reports_unavailable_until_runtime_integration():
    provider = SimpleNamespace(options={})
    settings = SimpleNamespace(
        services=[{"provider_id": "comfy", "available": None, "capabilities": ["video"]}]
    )
    (row,) = probe_provider_capabilities(
        SimpleNamespace(settings=settings, extension=lambda _: provider)
    )
    validate("ProviderCapabilitySummary", row)
    assert row["status"] == "unavailable"
    assert row["reason_code"] == "capability_unavailable"
