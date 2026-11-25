"""Offline Stable Audio graphs, FLAC registration and provider lifecycle."""

import json
import subprocess
from copy import deepcopy
from dataclasses import replace

import httpx
import pytest
from proscenium.api.errors import DomainError
from proscenium.copilot.capabilities import get_project_capabilities
from proscenium.jobs.resources import Resources
from proscenium.providers import register_provider_artifact, stable_audio
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
        sha256_verified_during_registration=True,
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


@pytest.mark.parametrize("malformed", [False, True])
def test_audio_preparation_submit_provider_pump_and_replay(runtime, tmp_path, malformed):
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
    data = b"Not FLAC" if malformed else flac(tmp_path / "synth.flac", seconds=1.25)

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
        job, kind = invoke(runtime, "submit_generation", request, "submit-audio")
        assert kind == "success" and job["kind"] == "audio_generation", job
        (finished,) = provider.pump(PROJECT, job["job_id"])
        if malformed:
            assert (
                finished["state"] == "failed" and finished["error_code"] == "unsupported_media_type"
            )
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
                inspection=stable_audio.inspect_flac_bytes(data),
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
