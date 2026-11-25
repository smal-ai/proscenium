"""Offline Stable Audio graphs, FLAC registration and provider lifecycle."""

import errno
import json
import os
import subprocess
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from proscenium.api.errors import DomainError
from proscenium.copilot.capabilities import get_project_capabilities
from proscenium.jobs.resources import Resources
from proscenium.previews import media as preview_media
from proscenium.previews.media import verify_video
from proscenium.providers import register_provider_artifact, stable_audio
from proscenium.providers.plugins import ImagePlugin, StableAudio3Plugin
from proscenium.providers.preparation import AudioPreparation, validate_recipes
from test_worker_capabilities_and_publication import PROJECT, TASK, invocation, invoke
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


@pytest.mark.parametrize(
    "outcome",
    [
        None,
        "unsupported_media_type",
        "corrupt_provider_output",
        "media_unavailable",
        "resume_verification",
        "legacy_verification_no_history",
        "malformed_completed_outputs",
    ],
)
def test_audio_preparation_submit_provider_pump_and_replay(runtime, tmp_path, monkeypatch, outcome):
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
    history_available = True
    descriptor = {"filename": "output.flac", "subfolder": "scores"}
    catalogue = audio_catalog()
    data = (
        b"Not FLAC"
        if outcome == "unsupported_media_type"
        else flac(tmp_path / "synth.flac", seconds=1.25)
    )
    if outcome == "corrupt_provider_output":
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
        if path == "/history/audio-prompt":
            if not history_available:
                return httpx.Response(200, json={})
            if outcome == "malformed_completed_outputs":
                return httpx.Response(
                    200, json={"audio-prompt": {"status": {"completed": True}, "outputs": []}}
                )
        if path == "/view":
            assert request.url.params == httpx.QueryParams({**descriptor, "type": "output"})
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
                                    {**descriptor, "type": 42, "unused": "not durable evidence"},
                                    "malformed",
                                    {"filename": None, "type": "output"},
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
        simulate_crash = outcome in {"resume_verification", "legacy_verification_no_history"}
        if outcome == "media_unavailable" or simulate_crash:
            original_write = Path.write_bytes

            def fail_verifier_write(path, contents):
                if path.name == "audio" and path.parent.name.startswith("proscenium-audio-verify-"):
                    execution = provider.execution(PROJECT, job["job_id"])
                    assert execution["phase"] == "verifying"
                    assert execution["output_descriptors"] == [descriptor]
                    if simulate_crash:
                        raise SystemExit("Simulated crash while verifying")
                    raise OSError(errno.ENOSPC, "Simulated full local temp filesystem")
                return original_write(path, contents)

            with monkeypatch.context() as patch:
                patch.setattr(Path, "write_bytes", fail_verifier_write)
                if simulate_crash:
                    with pytest.raises(SystemExit, match="Simulated crash while verifying"):
                        provider.pump(PROJECT, job["job_id"])
                else:
                    (finished,) = provider.pump(PROJECT, job["job_id"])
        if simulate_crash:
            history_available = False
        if outcome == "legacy_verification_no_history":
            execution = provider.execution(PROJECT, job["job_id"])
            execution.pop("output_descriptors")
            provider.execution(PROJECT, job["job_id"], execution)
        if outcome != "media_unavailable":
            (finished,) = provider.pump(PROJECT, job["job_id"])
        with runtime.store.transaction(write=False) as tx:
            assert (
                tx.connection.execute(
                    "SELECT state FROM budget_reservations WHERE id=?", (job["job_id"],)
                ).fetchone()[0]
                == "consumed"
            )
        assert calls.count("/prompt") == 1
        if outcome not in {None, "resume_verification"}:
            expected = (
                "media_unavailable"
                if outcome in {"legacy_verification_no_history", "malformed_completed_outputs"}
                else outcome
            )
            assert finished["state"] == "failed" and finished["error_code"] == expected
            if outcome == "legacy_verification_no_history":
                assert calls.count("/history/audio-prompt") == 2 and calls.count("/view") == 1
            if outcome == "malformed_completed_outputs":
                assert "/view" not in calls
                assert provider.execution(PROJECT, job["job_id"])["output_descriptors"] == []
            assert finished["artifact_ids"] == []
            repeated, kind = invoke(runtime, "submit_generation", request, "submit-audio-replay")
            assert kind == "success" and repeated == finished
            return
        assert finished["state"] == "succeeded", finished
        assert calls[3:] == [
            "/object_info",
            "/queue",
            "/system_stats",
            "/prompt",
            "/history/audio-prompt",
            "/view",
            *(["/view"] if outcome == "resume_verification" else []),
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


@pytest.mark.parametrize("image_output", [False, True], ids=["audio-only", "mixed"])
def test_image_submission_cannot_run_audio_outputs(runtime, image_output):
    artifacts = runtime.services.extension("artifacts")
    graph = workflow()
    if image_output:
        graph["image-output"] = dict(class_type="SaveImage", inputs={"images": ["7", 0]})
    with configure_audio(runtime), runtime.store.transaction() as tx:
        for identity, data in (
            ("cross-graph", json.dumps(graph).encode()),
            ("cross-prompt", b"An audio prompt"),
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
        job_id="cross-job",
        project_id=PROJECT,
        generation_task_id=TASK,
        target_id=TASK,
        provider_id="comfy",
        capability="image",
        workflow_artifact_id="cross-graph",
        prompt_artifact_id="cross-prompt",
        input_artifact_ids=[],
        seed="42",
        model_ids=MODELS,
        settings={},
    )
    blocked, result = invoke(runtime, "submit_generation", body, "cross-submit")
    assert result == "failure" and blocked["error"] == "invalid_request"
    assert blocked["message"] == "Graph output node does not match capability"
    with runtime.store.transaction(write=False) as tx:
        assert not tx.all("jobs")
        assert not tx.connection.execute(
            "SELECT 1 FROM outbox WHERE kind='providers.generation'"
        ).fetchone()
        assert not tx.connection.execute("SELECT 1 FROM budget_reservations").fetchone()


def test_capability_preflight_rejects_other_catalogue_output_nodes():
    catalog = {
        name: {"output_node": True} for name in ("PreviewImage", "SaveAudio", "UnknownOutput")
    }

    def node(name):
        return {"class_type": name, "inputs": {}}

    ImagePlugin().preflight({"1": node("PreviewImage")}, catalog, [])
    StableAudio3Plugin().preflight({"1": node("SaveAudio")}, catalog, [])
    for plugin, output in [(ImagePlugin(), "PreviewImage"), (StableAudio3Plugin(), "SaveAudio")]:
        with pytest.raises(DomainError, match="Graph output node does not match capability"):
            plugin.preflight({"1": node(output), "2": node("UnknownOutput")}, catalog, [])


@pytest.mark.parametrize("binary", ["ffmpeg", "ffprobe"])
def test_missing_audio_verifier_rejects_discovery_preparation_and_submit(runtime, tmp_path, binary):
    body = dict(
        schema_version="1.0",
        operation_id="original-prep",
        prompt="Prepared score",
        seed="42",
        duration_seconds=1,
    )
    with configure_audio(runtime):
        prepared, result = invoke(runtime, "prepare_audio_generation", body, "initial-prep")
        assert result == "success", prepared
    runtime.services.settings = replace(
        runtime.services.settings,
        services=({**runtime.services.settings.services[0], "capabilities": ["audio"]},),
        rendering={binary: str(tmp_path / f"missing-{binary}")},
    )
    with runtime.store.transaction(write=False) as tx:
        existing_artifacts = tx.all("artifacts")

    def no_provider_call(request):
        pytest.fail(f"Called ComfyUI with a missing verifier: {request.url.path}")

    with httpx.Client(transport=httpx.MockTransport(no_provider_call)) as runtime.provider.client:
        (row,) = get_project_capabilities(runtime.services, PROJECT)["providers"]
        assert (row["status"], row["reason_code"], row["summary"]) == (
            "unavailable",
            "audio_verifier_missing",
            stable_audio.AUDIO_VERIFY_UNAVAILABLE,
        )
        commands = [
            ("prepare_audio_generation", {**body, "operation_id": "missing-verifier-prep"}),
            ("submit_generation", prepared["submit_request"]),
        ]
        for tool, request in commands:
            blocked, result = invoke(runtime, tool, request, tool)
            assert result == "failure" and blocked["error"] == "capability_unavailable"
            assert blocked["message"] == stable_audio.AUDIO_VERIFY_UNAVAILABLE
    with runtime.store.transaction(write=False) as tx:
        assert tx.all("artifacts") == existing_artifacts
        assert not tx.all("jobs")
        assert not tx.connection.execute("SELECT 1 FROM budget_reservations").fetchone()


@pytest.mark.parametrize("grows", [False, True], ids=["oversize-stat", "grows-after-stat"])
def test_oversized_staged_audio_has_bounded_read(runtime, monkeypatch, grows):
    relative = ".proscenium/tool-output/staging/oversized.flac"
    path = runtime.root / relative
    path.parent.mkdir(parents=True)
    data = b"bytes beyond the cap"
    path.write_bytes(data)
    monkeypatch.setattr(stable_audio, "MAX_AUDIO_BYTES", 4)

    def no_decode(*_args, **_kwargs):
        pytest.fail("Oversized encoded audio reached verification")

    monkeypatch.setattr(stable_audio, "verify_audio_bytes", no_decode)
    original_fstat, original_fdopen = os.fstat, os.fdopen
    file_info = path.stat()
    reads = []

    def is_staged_file(info):
        return (info.st_dev, info.st_ino) == (file_info.st_dev, file_info.st_ino)

    def fstat(fd):
        info = original_fstat(fd)
        if grows and is_staged_file(info):
            values = list(info)
            values[6] = 4
            return os.stat_result(values)
        return info

    @contextmanager
    def capture_read(stream):
        class ReadSpy:
            def read(self, size=-1):
                reads.append(size)
                assert size == 5
                return stream.read(size)

        with stream:
            yield ReadSpy()

    def fdopen(fd, *args, **kwargs):
        if is_staged_file(original_fstat(fd)):
            assert grows, "Oversized staged file was read"
            return capture_read(original_fdopen(fd, *args, **kwargs))
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fstat", fstat)
    monkeypatch.setattr(os, "fdopen", fdopen)
    body = dict(
        schema_version="1.0",
        operation_id="oversized",
        project_id=PROJECT,
        artifact_id="oversized",
        kind="audio",
        purpose="concept",
        media_type="audio/flac",
        staging_path=relative,
        sha256=sha256(data).hexdigest(),
    )
    call = invocation(runtime, "register_artifact", body, "oversized-register")
    caller = runtime.tools.caller(call, name="register_artifact")
    artifacts = runtime.services.extension("artifacts")
    with pytest.raises(DomainError) as captured:
        artifacts.register(caller, body)
    assert (captured.value.code, captured.value.status) == ("upload_too_large", 413)
    assert reads == ([5] if grows else [])
    with pytest.raises(DomainError) as captured:
        artifacts.promote(
            PROJECT, "oversized", data, kind="audio", purpose="concept", media_type="audio/flac"
        )
    assert (captured.value.code, captured.value.status) == ("upload_too_large", 413)


@pytest.fixture
def short_aac_cut(tmp_path):
    def encode_cut(audio_length="0.125", offset="0"):
        path = tmp_path / "short-aac.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x64:r=24:d=0.125",
                "-itsoffset",
                offset,
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:sample_rate=44100:duration={audio_length}",
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
        return path

    return encode_cut


@pytest.mark.parametrize(
    ("length", "offset", "accepted"),
    [
        ("0.125", "0", True),
        ("0.02322", "0", False),
        ("0.045", "0", False),
        ("0.125", "0.1", False),
    ],
    ids=["matching", "one-aac-frame", "0.08s-shortfall", "late-start"],
)
def test_short_cut_verifies_aac_endpoints(short_aac_cut, length, offset, accepted):
    path = short_aac_cut(length, offset)
    arguments = (
        path,
        dict(numerator=24, denominator=1),
        3,
        dict(width=64, height=64),
        dict(ffmpeg="ffmpeg", ffprobe="ffprobe", encode_timeout=10),
    )
    if accepted:
        assert verify_video(*arguments, expect_audio=True)["pts"] == ["0", "1/24", "1/12"]
    else:
        with pytest.raises(DomainError, match="Audio timeline does not match the video") as error:
            verify_video(*arguments, expect_audio=True)
        assert (error.value.code, error.value.status) == ("media_unavailable", 422)


@pytest.mark.parametrize(
    ("field", "value"),
    [("start_time", None), ("duration", None), ("start_time", "NaN"), ("duration", "Infinity")],
)
def test_short_cut_requires_explicit_finite_audio_timing(short_aac_cut, monkeypatch, field, value):
    path = short_aac_cut()
    original = preview_media.run

    def probe_without_timing(argv, timeout):
        assert argv[0] == "ffprobe", "Invalid timing should fail before final decode"
        data = json.loads(original(argv, timeout))
        (audio,) = [stream for stream in data["streams"] if stream["codec_type"] == "audio"]
        if value is None:
            del audio[field]
        else:
            audio[field] = value
        return json.dumps(data).encode()

    monkeypatch.setattr(preview_media, "run", probe_without_timing)
    with pytest.raises(DomainError, match="Incomplete MP4 audio stream metadata") as error:
        verify_video(
            path,
            dict(numerator=24, denominator=1),
            3,
            dict(width=64, height=64),
            dict(ffmpeg="ffmpeg", ffprobe="ffprobe", encode_timeout=10),
            expect_audio=True,
        )
    assert (error.value.code, error.value.status) == ("media_unavailable", 422)


def test_catalogue_rejects_staged_fifo_without_blocking(runtime, monkeypatch):
    relative = ".proscenium/tool-output/staging/audio.fifo"
    path = runtime.root / relative
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    opened = []
    original_open = os.open

    def guarded_open(candidate, flags, *args, **kwargs):
        if candidate in {path, str(path), path.name}:
            # Check before opening, so the regression fails instead of hanging without a writer.
            opened.append(flags)
            assert flags & os.O_NONBLOCK
            assert flags & os.O_NOFOLLOW
        return original_open(candidate, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    body = dict(
        schema_version="1.0",
        operation_id="fifo",
        project_id=PROJECT,
        artifact_id="fifo",
        kind="audio",
        purpose="concept",
        media_type="audio/flac",
        staging_path=relative,
        sha256=sha256(b"").hexdigest(),
    )
    call = invocation(runtime, "register_artifact", body, "fifo-register")
    caller = runtime.tools.caller(call, name="register_artifact")
    with pytest.raises(DomainError, match="Expected a regular file") as captured:
        runtime.services.extension("artifacts").register(caller, body)
    assert (captured.value.code, captured.value.status) == ("scope_denied", 403)
    assert len(opened) == 1


def test_provider_bridge_rejects_oversized_stage_before_promotion(runtime, monkeypatch):
    body = dict(
        schema_version="1.0",
        operation_id="bridge-prepare",
        prompt="Bridge test",
        seed="42",
        duration_seconds=1,
    )
    with configure_audio(runtime):
        prepared, status = invoke(runtime, "prepare_audio_generation", body, "bridge-prepare")
        assert status == "success", prepared
        job, status = invoke(
            runtime, "submit_generation", prepared["submit_request"], "bridge-submit"
        )
        assert status == "success", job
    relative = f".proscenium/jobs/{job['job_id']}/oversized.flac"
    path = runtime.root / relative
    path.parent.mkdir(parents=True)
    data = b"over the cap"
    path.write_bytes(data)
    monkeypatch.setattr(stable_audio, "MAX_AUDIO_BYTES", 4)
    monkeypatch.setattr(
        stable_audio,
        "verify_audio_bytes",
        lambda *_a, **_kw: pytest.fail("Oversized audio reached verification"),
    )
    body = dict(
        schema_version="1.0",
        operation_id="oversized-bridge",
        project_id=PROJECT,
        artifact_id="oversized-bridge",
        kind="audio",
        purpose="concept",
        media_type="audio/flac",
        staging_path=relative,
        sha256=sha256(data).hexdigest(),
    )
    caller = runtime.provider.worker(job)
    with pytest.raises(DomainError) as captured:
        register_provider_artifact(runtime.services, caller, body)
    assert (captured.value.code, captured.value.status) == ("upload_too_large", 413)
    with runtime.store.transaction(write=False) as tx:
        assert tx.get("artifacts", body["artifact_id"], required=False) is None


def test_provider_existing_stage_is_bounded_before_hashing(runtime, monkeypatch):
    data = b"small"
    digest = sha256(data).hexdigest()
    job = dict(job_id="oversized-existing-stage", project_id=PROJECT)
    path = runtime.root / f".proscenium/jobs/{job['job_id']}/{digest}.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"Larger than the shared staged-output cap")
    monkeypatch.setattr(stable_audio, "MAX_AUDIO_BYTES", len(data))
    runtime.provider.registrar = lambda *_args: pytest.fail(
        "Oversized audio recovery reached registration"
    )
    with pytest.raises(DomainError) as captured:
        runtime.provider._register(job, data, "0")
    assert (captured.value.code, captured.value.status) == ("upload_too_large", 413)
