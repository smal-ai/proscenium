"""Offline Stable Audio graphs, FLAC registration and provider lifecycle."""

import json
import subprocess
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import httpx
import pytest
from proscenium.api.errors import DomainError
from proscenium.copilot.capabilities import get_project_capabilities
from proscenium.jobs.resources import Resources
from proscenium.previews.media import verify_video
from proscenium.providers import register_provider_artifact, stable_audio
from proscenium.providers.plugins import StableAudio3Plugin
from proscenium.providers.preparation import AudioPreparation, validate_recipes
from test_worker_capabilities_and_publication import PROJECT, TASK, invoke
from test_worker_capabilities_and_publication import offline as offline
from test_worker_capabilities_and_publication import runtime as runtime

MODELS = [stable_audio.DEFAULT_MUSIC_CHECKPOINT, stable_audio.DEFAULT_CLIP]


def flac(path, *, frequency=440, seconds=1, channels=2):
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}",
            "-ac",
            str(channels),
            "-c:a",
            "flac",
            str(path),
        ],
        check=True,
        timeout=10,
    )
    return path.read_bytes()


def workflow(seconds=8):
    return stable_audio.graph(checkpoint=MODELS[0], clip=MODELS[1], seconds=seconds)


def test_stable_audio_graph_and_idempotent_binding():
    graph = workflow()
    assert graph["8"]["inputs"] == {"audio": ["7", 0], "filename_prefix": "proscenium_audio"}
    assert graph["7"]["inputs"] == {"samples": ["6", 0], "vae": ["1", 2]}
    assert graph["6"]["inputs"] == dict(
        model=["1", 0],
        positive=["3", 0],
        negative=["4", 0],
        latent_image=["5", 0],
        seed="$seed",
        steps=8,
        cfg=1.0,
        sampler_name="lcm",
        scheduler="simple",
        denoise=1.0,
    )
    bound = stable_audio.bind(graph, "Soft underscore", str(2**53 - 1), {}, MODELS)
    assert bound["3"]["inputs"]["text"] == "Soft underscore"
    assert bound["4"]["inputs"]["text"] == ""
    assert bound["6"]["inputs"]["seed"] == 2**53 - 1
    assert graph == workflow() and graph is not bound
    assert stable_audio.bind(bound, "Soft underscore", str(2**53 - 1), {}, MODELS) == bound
    broken = deepcopy(graph)
    broken["7"]["inputs"]["vae"] = ["1", 1]
    for value, refs, models in [
        (broken, {}, MODELS),
        (graph, {"ref": "x"}, MODELS),
        (graph, {}, [MODELS[0]]),
        (graph, {}, [MODELS[0], MODELS[0]]),
    ]:
        with pytest.raises(DomainError, match="Stable Audio graph|reference artefacts"):
            stable_audio.bind(value, "Soft underscore", "42", refs, models)


@pytest.mark.parametrize("seed", [None, "", "-1", "1.5", "1\n", str(2**53), "1" * 5000])
def test_invalid_audio_seed(seed):
    with pytest.raises(DomainError, match="exact JSON-safe decimal seed"):
        stable_audio.bind(workflow(), "prompt", seed, {}, MODELS)


@pytest.mark.parametrize("seconds", [0, 380.01, float("nan"), float("inf")])
def test_invalid_audio_duration(seconds):
    with pytest.raises(DomainError, match="Audio duration"):
        stable_audio.bind(workflow(seconds), "prompt", "42", {}, MODELS)


def test_flac_metadata_rejects_invalid_input(tmp_path):
    data = flac(tmp_path / "tone.flac", seconds=1.25, channels=2)
    assert stable_audio.inspect_flac_bytes(data) == dict(
        duration_seconds=1.25,
        sample_rate_hz=48000,
        channels=2,
        declared_total_samples=60000,
    )
    no_samples = bytearray(data)
    no_samples[21] &= 0xF0
    no_samples[22:26] = b"\0" * 4
    for bad, code, status in [
        (b"not FLAC", "unsupported_media_type", 415),
        (data[:41], "unsupported_media_type", 415),
        (data[:7] + b"\x21" + data[8:], "unsupported_media_type", 415),
        (bytes(no_samples), "corrupt_provider_output", 422),
        (data[:42], "corrupt_provider_output", 422),
        (data[:4] + b"\x80" + data[5:42] + b"xxxxxxxxxx", "corrupt_provider_output", 422),
    ]:
        with pytest.raises(DomainError) as caught:
            stable_audio.inspect_flac_bytes(bad)
        assert (caught.value.code, caught.value.status) == (code, status)


def audio_catalog():
    specs = {
        "CheckpointLoaderSimple": (
            {"ckpt_name": [[MODELS[0], stable_audio.DEFAULT_SFX_CHECKPOINT]]},
            ["MODEL", "CLIP", "VAE"],
        ),
        "CLIPLoader": (
            {"clip_name": [[MODELS[1]]], "type": [["stable_audio"]], "device": [["default"]]},
            ["CLIP"],
        ),
        "CLIPTextEncode": ({"text": ["STRING"], "clip": ["CLIP"]}, ["CONDITIONING"]),
        "EmptyLatentAudio": (
            {"seconds": ["FLOAT", {"min": 1, "max": 380}], "batch_size": ["INT"]},
            ["LATENT"],
        ),
        "KSampler": (
            {
                "model": ["MODEL"],
                "positive": ["CONDITIONING"],
                "negative": ["CONDITIONING"],
                "latent_image": ["LATENT"],
                "seed": ["INT"],
                "steps": ["INT"],
                "cfg": ["FLOAT"],
                "sampler_name": [["lcm"]],
                "scheduler": [["simple"]],
                "denoise": ["FLOAT"],
            },
            ["LATENT"],
        ),
        "VAEDecodeAudio": ({"samples": ["LATENT"], "vae": ["VAE"]}, ["AUDIO"]),
        "SaveAudio": ({"audio": ["AUDIO"], "filename_prefix": ["STRING"]}, []),
    }
    return {
        kind: dict(input={"required": inputs}, output=outputs, output_node=kind == "SaveAudio")
        for kind, (inputs, outputs) in specs.items()
    }


@pytest.mark.parametrize("failure", [None, "unsupported_media_type", "corrupt_provider_output"])
def test_audio_preparation_submit_provider_pump_and_replay(runtime, tmp_path, failure):
    services, provider = runtime.services, runtime.provider
    services.settings = replace(
        services.settings, services=({**services.settings.services[0], "capabilities": ["audio"]},)
    )
    services.extensions["audio_preparation"] = AudioPreparation(services, provider)
    services.resources = Resources(runtime.store)
    provider.registrar = lambda caller, request: register_provider_artifact(
        services, caller, request
    )
    artifacts = services.extension("artifacts")
    calls = []
    catalogue = audio_catalog()
    data = (
        b"Not FLAC"
        if failure == "unsupported_media_type"
        else flac(tmp_path / "synth.flac", seconds=1.25)
    )
    if failure == "corrupt_provider_output":
        data = data[:-100]

    def respond(request):
        path = request.url.path
        calls.append(path)
        if path == "/prompt":
            submitted = json.loads(request.content)["prompt"]
            assert submitted == stable_audio.bind(
                workflow(1.25), "Piano underscore", "42", {}, MODELS
            )
            return httpx.Response(200, json={"prompt_id": "audio-prompt"})
        if path == "/view":
            assert request.url.params["filename"] == "output.flac"
            return httpx.Response(200, content=data)
        return httpx.Response(
            200,
            json={
                "/object_info": catalogue,
                "/queue": {"queue_running": [], "queue_pending": []},
                "/system_stats": {"devices": [{"vram_free": 2**30}]},
                "/history/audio-prompt": {
                    "audio-prompt": {
                        "status": {"completed": True},
                        "outputs": {
                            "8": {
                                "audio": [
                                    {"filename": "output.flac", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            }[path],
        )

    provider.client.close()
    with httpx.Client(transport=httpx.MockTransport(respond)) as provider.client:
        (row,) = get_project_capabilities(services, PROJECT)["providers"]
        assert row["status"] == "ready" and row["profile_revision"] == stable_audio.REVISION
        assert row["default_steps"] == 8 and "music, sfx" in row["summary"]
        assert (
            set(row["selected_models"])
            == set(row["compatible_models"])
            == {*MODELS, stable_audio.DEFAULT_SFX_CHECKPOINT}
        )
        body = dict(
            schema_version="1.0",
            operation_id="prepare-audio",
            prompt="Piano underscore",
            seed="42",
            duration_seconds=1.25,
        )
        prepared, kind = invoke(runtime, "prepare_audio_generation", body, "prepare-audio")
        assert kind == "success", prepared
        request = prepared["submit_request"]
        assert prepared["mode"] == "music" and prepared["model_ids"] == MODELS
        assert (
            request["generation_task_id"] == TASK
            and request["project_id"] == PROJECT
            and request["input_artifact_ids"] == []
        )
        replayed, kind = invoke(runtime, "prepare_audio_generation", body, "prepare-audio-replay")
        assert kind == "success" and replayed == {**prepared, "replayed": True}
        assert calls == ["/object_info", "/object_info"]
        invalid, kind = invoke(
            runtime,
            "prepare_audio_generation",
            {**body, "operation_id": "too-long", "mode": "sfx", "duration_seconds": 121},
            "too-long",
        )
        assert kind == "failure" and invalid["message"] == stable_audio.INVALID_DURATION
        mismatch, kind = invoke(
            runtime, "submit_generation", {**request, "seed": "43"}, "changed-submit"
        )
        assert kind == "failure" and mismatch["error"] == "idempotency_conflict"
        job, kind = invoke(runtime, "submit_generation", request, "submit-audio")
        assert kind == "success" and job["kind"] == "audio_generation", job
        (finished,) = provider.pump(PROJECT, job["job_id"])
        if failure:
            assert finished["state"] == "failed" and finished["error_code"] == failure
            assert finished["artifact_ids"] == []
            repeated, kind = invoke(runtime, "submit_generation", request, "submit-audio-replay")
            assert kind == "success" and repeated == finished and calls.count("/prompt") == 1
            return
        assert finished["state"] == "succeeded", finished
        assert calls[3:] == [
            "/object_info",
            "/queue",
            "/system_stats",
            "/prompt",
            "/history/audio-prompt",
            "/view",
        ]
        outcome, provenance = finished["artifact_ids"]
        artifact = artifacts.get(PROJECT, outcome)
        assert {
            k: artifact[k]
            for k in ("kind", "media_type", "duration_seconds", "sample_rate_hz", "channels")
        } == dict(
            kind="audio",
            media_type="audio/flac",
            duration_seconds=1.25,
            sample_rate_hz=48000,
            channels=2,
        )
        evidence = json.loads(artifacts.ready_path(PROJECT, provenance).read_bytes())
        assert evidence["inputs"]["request"] == request and evidence["prompt_id"] == "audio-prompt"
        assert evidence["output_inspections"] == [
            dict(
                artifact_id=outcome,
                sha256=artifact["sha256"],
                inspection=stable_audio.verify_audio_bytes(data, "audio/flac"),
            )
        ]
        repeated, kind = invoke(runtime, "submit_generation", request, "submit-audio-replay")
        assert kind == "success" and repeated == finished and calls.count("/prompt") == 1
        with pytest.raises(DomainError, match="hash mismatch"):
            artifacts.promote(
                PROJECT,
                "mismatch",
                data,
                kind="audio",
                purpose="concept",
                media_type="audio/flac",
                metadata={"channels": 1},
            )


@pytest.mark.parametrize(
    "changes",
    [
        {"steps": 0},
        {"max_duration_seconds": float("nan")},
        {"clip": ""},
        {"music_checkpoint": None},
    ],
)
def test_invalid_audio_recipe(changes):
    with pytest.raises(ValueError):
        validate_recipes(
            {
                "audio_recipes": {
                    "comfy": {
                        "profile_revision": stable_audio.REVISION,
                        "music_checkpoint": MODELS[0],
                        "clip": MODELS[1],
                        **changes,
                    }
                }
            }
        )


def configure_audio(runtime, *, image=False):
    services, provider = runtime.services, runtime.provider
    services.settings = replace(
        services.settings,
        services=({**services.settings.services[0], "capabilities": ["image", "audio"]},),
    )
    services.extensions["audio_preparation"] = AudioPreparation(services, provider)
    provider.client.close()
    provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=runtime.catalog if image else audio_catalog())
        )
    )
    return provider.client


@pytest.mark.parametrize("capability", ["image", "audio"])
def test_planning_scope_denies_new_generation_without_reserving_and_preserves_replay(
    runtime, capability
):
    def set_scope(scope):
        identity = f"context-{scope}"
        with runtime.store.transaction() as tx:
            if tx.get("contexts", identity, required=False) is None:
                tx.put(
                    "contexts",
                    identity,
                    dict(context_id=identity, project_id=PROJECT, execution_scope=scope),
                )
            request = tx.require("requests", "request-one")
            tx.put(
                "requests",
                "request-one",
                {**request, "submission": {**request["submission"], "context_id": identity}},
                expected_revision=tx.revision("requests", "request-one"),
            )

    def prepare(operation):
        body = dict(
            schema_version="1.0", operation_id=operation, prompt="Prepared prompt", seed="42"
        )
        if capability == "audio":
            body["duration_seconds"] = 2
        result, status = invoke(runtime, f"prepare_{capability}_generation", body, operation)
        assert status == "success", result
        return result["submit_request"]

    with configure_audio(runtime, image=capability == "image"):
        set_scope("planning_only")
        request = prepare("planning-preparation")
        blocked, status = invoke(runtime, "submit_generation", request, "blocked-planning")
        assert status == "failure" and blocked["error"] == "scope_denied"
        assert (
            blocked["message"] == "Generation is unavailable while execution_scope is planning_only"
        )
        with runtime.store.transaction(write=False) as tx:
            assert not tx.all("jobs")
            assert (
                tx.connection.execute("SELECT count(*) FROM budget_reservations").fetchone()[0] == 0
            )
            assert (
                tx.connection.execute(
                    "SELECT count(*) FROM outbox WHERE kind='providers.generation'"
                ).fetchone()[0]
                == 0
            )
        set_scope("default")
        admitted, status = invoke(runtime, "submit_generation", request, "allowed-after-planning")
        assert status == "success", admitted
        set_scope("planning_only")
        replay, status = invoke(runtime, "submit_generation", request, "replay-in-planning")
        assert status == "success" and replay == admitted
        with runtime.store.transaction(write=False) as tx:
            assert (
                tx.connection.execute("SELECT count(*) FROM budget_reservations").fetchone()[0] == 1
            )


def test_direct_audio_submission_cannot_bypass_preparation(runtime):
    artifacts = runtime.services.extension("artifacts")
    graph = stable_audio.graph(
        checkpoint=stable_audio.DEFAULT_SFX_CHECKPOINT, clip=MODELS[1], seconds=200
    )
    with configure_audio(runtime), runtime.store.transaction() as tx:
        for identity, data in (
            ("direct-graph", json.dumps(graph).encode()),
            ("direct-prompt", b"Isolated SFX"),
        ):
            artifacts.put(
                tx,
                artifacts.promote(
                    PROJECT,
                    identity,
                    data,
                    kind="document",
                    purpose="metadata",
                    media_type="application/json",
                ),
            )
    body = dict(
        schema_version="1.0",
        job_id="direct-job",
        project_id=PROJECT,
        generation_task_id=TASK,
        target_id=TASK,
        provider_id="comfy",
        capability="audio",
        workflow_artifact_id="direct-graph",
        prompt_artifact_id="direct-prompt",
        input_artifact_ids=[],
        seed="42",
        model_ids=[stable_audio.DEFAULT_SFX_CHECKPOINT, MODELS[1]],
        settings={},
    )
    blocked, status = invoke(runtime, "submit_generation", body, "direct-submit")
    assert status == "failure" and blocked["error"] == "invalid_request"
    assert blocked["message"] == "Audio generation requires prepare_audio_generation"
    with runtime.store.transaction(write=False) as tx:
        assert not tx.all("jobs")
        assert tx.connection.execute("SELECT count(*) FROM budget_reservations").fetchone()[0] == 0


@pytest.mark.parametrize("boundary", [False, True], ids=["inside-first-frame", "between-frames"])
def test_truncated_flac_never_promotes(runtime, tmp_path, boundary):
    path = tmp_path / "complete.flac"
    data = flac(path, seconds=1.25)
    packets = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-of",
                "json",
                "-show_packets",
                "-show_entries",
                "packet=pos,size",
                str(path),
            ],
            timeout=10,
        )
    )["packets"]
    first = packets[0]
    end = int(packets[1]["pos"]) if boundary else int(first["pos"]) + int(first["size"]) // 2
    truncated = data[:end]
    assert stable_audio.inspect_flac_bytes(truncated)["declared_total_samples"] == 60_000
    if boundary:
        path.write_bytes(truncated)
        # FFmpeg alone accepts complete frames even though the file ends early.
        pcm = subprocess.check_output(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-f",
                "flac",
                "-i",
                str(path),
                "-f",
                "s16le",
                "-",
            ],
            timeout=10,
        )
        assert 0 < len(pcm) < 60_000 * 2 * 2
    with pytest.raises(DomainError, match="Provider returned empty or corrupted audio") as error:
        StableAudio3Plugin().inspect_output(truncated, 0)
    assert error.value.code == "corrupt_provider_output"
    artifacts = runtime.services.extension("artifacts")
    with pytest.raises(
        DomainError, match="Audio bytes failed bounded decode verification"
    ) as error:
        artifacts.promote(
            PROJECT,
            "truncated",
            truncated,
            kind="audio",
            purpose="concept",
            media_type="audio/flac",
        )
    assert error.value.code == "media_unavailable"
    with runtime.store.transaction(write=False) as tx:
        assert tx.get("artifacts", "truncated", required=False) is None
    assert not artifacts.path(PROJECT, sha256(truncated).hexdigest()).exists()


def test_pcm_wave_metadata_and_media_spoofing(runtime, tmp_path, monkeypatch):
    wave = tmp_path / "tone.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=0.25",
            "-c:a",
            "pcm_s16le",
            str(wave),
        ],
        check=True,
        timeout=10,
    )
    data = wave.read_bytes()
    artifacts = runtime.services.extension("artifacts")

    def promote(data, media):
        return artifacts.promote(
            PROJECT, "sound", data, kind="audio", purpose="concept", media_type=media
        )

    result = promote(data, "audio/wav")
    assert (result["duration_seconds"], result["sample_rate_hz"], result["channels"]) == (
        0.25,
        16000,
        1,
    )
    for payload, media in [
        (b"ffconcat version 1.0\nfile forbidden\n", "audio/wav"),
        (data, "audio/mp4"),
    ]:
        with pytest.raises(DomainError) as error:
            promote(payload, media)
        assert error.value.code in {"unsupported_media_type", "media_unavailable"}
    monkeypatch.setattr(stable_audio, "MAX_PCM_BYTES", 1024)
    with pytest.raises(DomainError, match="Audio bytes failed bounded decode verification"):
        promote(data, "audio/wav")


@pytest.mark.parametrize(
    ("length", "offset"), [("0.5", "0"), ("3", "0"), ("2", "0.5")], ids=["short", "long", "late"]
)
def test_verify_video_rejects_wrong_audio_timeline(tmp_path, length, offset):
    path = tmp_path / "audio-timeline.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:r=24:d=2",
            "-itsoffset",
            offset,
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=44100:duration={length}",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-bf",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-video_track_timescale",
            "24",
            "-c:a",
            "aac",
            "-ac",
            "2",
            str(path),
        ],
        check=True,
        timeout=10,
    )
    cfg = dict(ffmpeg="ffmpeg", ffprobe="ffprobe", encode_timeout=10)
    with pytest.raises(DomainError, match="Audio timeline does not match the video") as error:
        verify_video(
            path,
            dict(numerator=24, denominator=1),
            48,
            dict(width=64, height=64),
            cfg,
            expect_audio=True,
        )
    assert error.value.code == "media_unavailable"
