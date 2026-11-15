"""Offline Klein preparation, retained raster validation and resource-hold regressions."""

import copy
import io
import json
import math
import random

import httpx
import pytest
import test_worker_capabilities_and_publication as shared
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.jobs.resources import Resources
from proscenium.providers import klein, sd_turbo, sd_turbo_fp16
from proscenium.providers.comfy import bind_graph
from proscenium.providers.plugins import ImagePlugin
from proscenium.providers.preparation import validate_recipes, workflow
from test_worker_capabilities_and_publication import (
    PROJECT,
    RECIPE,
    TASK,
    WORK,
    invoke,
)

DIMENSIONS = "Image width and height must be multiples of 16 between 64 and 4096"
PIXELS = "Requested image dimensions exceed the configured provider pixel limit"
CORRUPT = "Provider returned a corrupted scanline or noise image"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    shared.offline.__wrapped__(monkeypatch)


@pytest.fixture
def runtime(tmp_path):
    for state in shared.runtime.__wrapped__(tmp_path):
        try:
            yield state
        finally:
            state.provider.client.close()


def graph(**changes):
    return klein.graph(
        **{k: RECIPE[k] for k in ("unet", "clip", "vae")},
        **{"width": 512, "height": 512, **changes},
    )


def add_tiled_nodes(catalog):
    for node in graph(reference_artifact_id="reference", tiled_vae=True).values():
        if not node["class_type"].endswith("Tiled"):
            continue
        catalog[node["class_type"]] = {
            "input": {
                "required": {
                    name: ["*" if isinstance(value, list) else "INT"]
                    for name, value in node["inputs"].items()
                }
            },
            "output": ["*"],
        }


@pytest.mark.parametrize("tiled", [False, True])
def test_reference_scaling_tiling_and_admitted_replay(tiled):
    original = graph(reference_artifact_id="reference", tiled_vae=tiled)
    assert original["reference_scale"] == {
        "class_type": "ImageScaleToTotalPixels",
        "inputs": {
            "image": ["reference", 0],
            "upscale_method": "lanczos",
            "megapixels": 1.0,
            "resolution_steps": 16,
        },
    }
    for key, kind in (("decode", "VAEDecode"), ("reference_encode", "VAEEncode")):
        assert original[key]["class_type"] == kind + ("Tiled" if tiled else "")
        if tiled:
            assert {
                k: original[key]["inputs"][k]
                for k in ("tile_size", "overlap", "temporal_size", "temporal_overlap")
            } == {"tile_size": 512, "overlap": 64, "temporal_size": 64, "temporal_overlap": 8}
    admitted = bind_graph(original, "Concept", "42", {"reference": "artifact:reference"})
    replayed = bind_graph(admitted, "Concept", "42", {"reference": "uploaded/reference.png"})
    assert replayed["reference"]["inputs"]["image"] == "uploaded/reference.png"
    assert original["noise"]["inputs"]["noise_seed"] == "$seed"
    for polarity in ("positive", "negative"):
        assert replayed["guider"]["inputs"][polarity] == ["reference_" + polarity, 0]


def test_historical_unscaled_untiled_graph_binds_and_replays():
    historical = graph(reference_artifact_id="reference")
    del historical["reference_scale"]
    historical["reference_encode"]["inputs"]["pixels"] = ["reference", 0]
    admitted = bind_graph(historical, "Concept", "42", {"reference": "artifact:reference"})
    replayed = bind_graph(admitted, "Concept", "42", {"reference": "uploaded.png"})
    assert replayed["reference_encode"]["inputs"]["pixels"] == ["reference", 0]
    assert replayed["decode"]["class_type"] == "VAEDecode"


def test_unreachable_reference_conditioning_is_rejected():
    invalid = graph(reference_artifact_id="reference", tiled_vae=True)
    invalid["guider"]["inputs"]["positive"] = ["text", 0]
    with pytest.raises(DomainError, match="Reference is not conditioning"):
        bind_graph(invalid, "Concept", "42", {"reference": "uploaded.png"})


@pytest.mark.parametrize(
    "dimensions", [(63, 512), (4112, 512), (512, 63), (512, 4112), (513, 512), (512, 513)]
)
def test_klein_alignment_validation(dimensions):
    with pytest.raises(DomainError, match=DIMENSIONS) as error:
        graph(width=dimensions[0], height=dimensions[1])
    assert error.value.code == "invalid_request"


@pytest.mark.parametrize("megapixels", [0.0, 0.0099, 4.0001, float("nan"), float("inf")])
def test_klein_reference_pixel_bounds(megapixels):
    with pytest.raises(DomainError):
        graph(reference_megapixels=megapixels)


@pytest.mark.parametrize("megapixels", [0.01, 4.0])
def test_klein_reference_pixel_boundary_values(megapixels):
    assert (
        graph(reference_artifact_id="reference", reference_megapixels=megapixels)[
            "reference_scale"
        ]["inputs"]["megapixels"]
        == megapixels
    )


@pytest.mark.parametrize("steps", [0, 51])
def test_klein_step_bounds(steps):
    with pytest.raises(DomainError):
        graph(steps=steps)


@pytest.mark.parametrize("maximum", [4096, 262144, 1_048_576, 16_777_216])
def test_recipe_pixel_limits_accept_inclusive_bounds(maximum):
    validate_recipes({"image_recipes": {"comfy": {**RECIPE, "max_pixels": maximum}}})
    scaled = workflow({**RECIPE, "max_pixels": maximum}, "reference")
    assert scaled["reference_scale"]["inputs"]["megapixels"] == min(
        1.0, max(0.01, round(maximum / 1_000_000, 4))
    )


@pytest.mark.parametrize("maximum", [True, False, 4095, 16_777_217, 1.0, "1048576", None])
def test_recipe_pixel_limits_reject_invalid_values(maximum):
    with pytest.raises(ValueError):
        validate_recipes({"image_recipes": {"comfy": {**RECIPE, "max_pixels": maximum}}})


@pytest.mark.parametrize("profile", [sd_turbo, sd_turbo_fp16])
def test_sd_turbo_pixel_limit_is_fixed(profile):
    recipe = {"profile_revision": profile.REVISION, "checkpoint": profile.FILENAME}
    options = {"image_profiles": {"comfy": profile.REVISION}, "image_recipes": {"comfy": recipe}}
    validate_recipes(options)
    recipe["max_pixels"] = 512 * 512
    validate_recipes(options)
    recipe["max_pixels"] += 1
    with pytest.raises(ValueError):
        validate_recipes(options)


def prepare_body(**changes):
    return {
        "schema_version": "1.0",
        "operation_id": "prepare-one",
        "prompt": "Concept",
        "seed": "42",
        **changes,
    }


def prepare(runtime, body):
    """Exercise provider authority defaults independently of the upstream tool guard."""
    caller = Caller(
        "tool",
        "provider-test",
        project_id=PROJECT,
        work_id=WORK,
        owner_epoch="epoch",
        invocation_id="prepare-invocation",
        operations=("prepareImageGeneration",),
    )
    with runtime.store.transaction() as tx:
        if not tx.get("invocations", caller.invocation_id, required=False):
            tx.put(
                "invocations",
                caller.invocation_id,
                {
                    "work_id": WORK,
                    "owner_epoch": "epoch",
                    "state": "running",
                    "request_id": "request-one",
                },
            )
    try:
        result, _ = runtime.services.extension("image_preparation").prepare(caller, body)
        return result, "success"
    except DomainError as exc:
        return {"error": exc.code, "message": str(exc)}, "failure"


def promote_reference(runtime):
    image = Image.new("RGB", (1600, 900), "blue")
    data = io.BytesIO()
    image.save(data, format="PNG")
    artifacts = runtime.services.extension("artifacts")
    artifact = artifacts.promote(
        PROJECT,
        "reference",
        data.getvalue(),
        kind="image",
        purpose="reference",
        media_type="image/png",
    )
    with runtime.store.transaction() as tx:
        artifacts.put(tx, artifact)


@pytest.mark.parametrize("explicit_project", [False, True])
@pytest.mark.parametrize("explicit_task", [False, True])
@pytest.mark.parametrize("tiled", [False, True])
def test_preparation_authority_recipe_defaults_and_fenced_replay(
    runtime, explicit_project, explicit_task, tiled
):
    runtime.provider.options["image_recipes"] = {
        "comfy": {**RECIPE, "width": 768, "height": 512, "steps": 7, "max_pixels": 524288}
    }
    if tiled:
        add_tiled_nodes(runtime.catalog)
    promote_reference(runtime)
    body = prepare_body(reference_artifact_ids=["reference"])
    if explicit_project:
        body["project_id"] = PROJECT
    if explicit_task:
        body["generation_task_id"] = TASK
    prepared, kind = prepare(runtime, body)
    assert kind != "failure", prepared
    assert prepared["project_id"] == PROJECT
    assert prepared["submit_request"]["generation_task_id"] == TASK
    assert prepared["submit_request"]["target_id"] == TASK
    assert prepared["dimensions"] == {"width": 768, "height": 512}
    artifacts = runtime.services.extension("artifacts")
    saved = json.loads(artifacts.ready_path(PROJECT, prepared["workflow_artifact_id"]).read_bytes())
    assert saved["sigmas"]["inputs"]["steps"] == 7
    assert saved["reference_scale"]["inputs"]["megapixels"] == 0.5243
    assert saved["reference_encode"]["class_type"] == ("VAEEncodeTiled" if tiled else "VAEEncode")
    assert saved["decode"]["class_type"] == ("VAEDecodeTiled" if tiled else "VAEDecode")
    with runtime.store.transaction(write=False) as tx:
        assert not tx.all("jobs")
        assert tx.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    runtime.state.control = lambda tx: {"fenced": True}
    runtime.services.gate.close(PROJECT)
    previous_calls = len(runtime.calls)
    runtime.provider.options["image_recipes"] = {"comfy": {"profile_revision": "changed"}}
    replayed, kind = prepare(runtime, body)
    assert kind != "failure", replayed
    assert replayed == {**prepared, "replayed": True}
    conflict, _ = prepare(runtime, {**body, "prompt": "Changed"})
    assert conflict["error"] == "idempotency_conflict"
    assert len(runtime.calls) == previous_calls
    runtime.state.control = lambda tx: {"fenced": False}
    runtime.services.gate.resume(PROJECT, reconciliation_evidence="Offline fixture")
    submitted, kind = invoke(runtime, "submit_generation", prepared["submit_request"], "submit-one")
    assert kind != "failure", submitted
    again, _ = invoke(runtime, "submit_generation", prepared["submit_request"], "submit-one")
    assert again == submitted
    with runtime.store.transaction(write=False) as tx:
        assert tx.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1


@pytest.mark.parametrize("nodes", [{}, {"VAEDecodeTiled": {}}, {"VAEEncodeTiled": {}}])
def test_partial_tiled_catalog_keeps_untiled_graph(nodes):
    assert workflow(RECIPE, catalog=nodes)["decode"]["class_type"] == "VAEDecode"


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"width": 513}, DIMENSIONS),
        ({"height": 513}, DIMENSIONS),
        ({"width": 2048, "height": 1024}, PIXELS),
    ],
)
def test_preparation_rejects_invalid_dimensions_before_materialisation(runtime, changes, message):
    result, kind = prepare(runtime, prepare_body(**changes))
    assert kind == "failure" and result["error"] == "invalid_request"
    assert result["message"] == message
    with runtime.store.transaction(write=False) as tx:
        assert not tx.all("jobs") and not tx.all("artifacts")


def test_preparation_enforces_configured_pixel_limit(runtime):
    runtime.provider.options["image_recipes"] = {"comfy": {**RECIPE, "max_pixels": 262144}}
    result, kind = prepare(runtime, prepare_body(width=768))
    assert kind == "failure" and result["message"] == PIXELS


def test_preparation_explicit_task_mismatch_is_scope_denied(runtime):
    result, kind = prepare(runtime, prepare_body(generation_task_id="wrong-task"))
    assert kind == "failure" and result["error"] == "scope_denied"
    assert (
        result["message"]
        == "generation_task_id does not match the active work; omit generation_task_id to use the active task"
    )


def test_preparation_requires_a_bound_project(runtime):
    preparation = runtime.services.extension("image_preparation")
    caller = Caller("tool", "unbound")
    for action in (preparation.replayable, preparation.prepare):
        with pytest.raises(DomainError) as error:
            action(caller, prepare_body())
        assert error.value.code == "invalid_request"


@pytest.mark.parametrize("profile", [sd_turbo, sd_turbo_fp16])
def test_sd_turbo_preparation_omitted_authority_replays_while_fenced(runtime, profile):
    runtime.provider.options["image_profiles"] = {"comfy": profile.REVISION}
    artifacts = runtime.services.extension("artifacts")
    artifact = artifacts.promote(
        PROJECT,
        "reference",
        raster("solid", (512, 512)),
        kind="image",
        purpose="reference",
        media_type="image/png",
    )
    with runtime.store.transaction() as tx:
        artifacts.put(tx, artifact)
    body = prepare_body(reference_artifact_ids=["reference"])
    prepared, kind = prepare(runtime, body)
    assert kind != "failure", prepared
    assert prepared["profile_revision"] == profile.REVISION
    assert prepared["dimensions"] == {"width": 512, "height": 512}
    assert prepared["submit_request"]["generation_task_id"] == TASK
    runtime.services.gate.close(PROJECT)
    calls = len(runtime.calls)
    replayed, kind = prepare(runtime, body)
    assert kind != "failure" and replayed == {**prepared, "replayed": True}
    assert len(runtime.calls) == calls


def raster(kind, size=(128, 128)):
    if kind == "tiny":
        kind, size = "noise", (32, 32)
    width, height = size
    image = Image.new("RGB", size)
    rng = random.Random(42)
    pixels = []
    for y in range(height):
        for x in range(width):
            if kind == "scanline":
                value = 100 + 16 * (y % 2)
                colour = (value, value, value)
            elif kind == "noise":
                colour = tuple(rng.randrange(256) for _ in range(3))
            elif kind == "gradient":
                colour = (x * 255 // width, y * 255 // height, (x + y) * 127 // (width + height))
            elif kind == "landscape":
                # Smooth natural-colour terrain/sky fixture, not a live photograph.
                colour = (
                    int(90 + 25 * math.sin(x / 20)),
                    int(130 + 30 * math.sin((x + y) / 30)),
                    int(160 + 20 * math.cos(y / 25)),
                )
            else:
                colour = (20, 80, 140)
            pixels.append(colour)
    image.putdata(pixels)
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


@pytest.mark.parametrize("kind", ["solid", "gradient", "landscape"])
def test_clean_rasters_pass_inspection(kind):
    assert ImagePlugin().inspect_output(raster(kind), 16384)["sha256_verified_during_registration"]


@pytest.mark.parametrize("size", [(1, 1), (32, 32), (63, 128), (128, 63)])
def test_small_rasters_skip_noise_guard(size):
    assert ImagePlugin().inspect_output(raster("noise", size), 16384)


@pytest.mark.parametrize("kind", ["scanline", "noise"])
def test_corrupt_rasters_are_rejected(kind):
    with pytest.raises(DomainError, match=CORRUPT) as error:
        ImagePlugin().inspect_output(raster(kind), 16384)
    assert error.value.code == "corrupt_provider_output" and error.value.status == 422


def test_raster_format_and_pixel_guards_remain():
    with pytest.raises(DomainError) as error:
        ImagePlugin().inspect_output(raster("solid"), 16383)
    assert error.value.code == "unsupported_media_type"
    data = io.BytesIO()
    Image.new("RGB", (64, 64)).save(data, format="JPEG")
    with pytest.raises(DomainError) as error:
        ImagePlugin().inspect_output(data.getvalue(), 16384)
    assert error.value.code == "unsupported_media_type"


def admitted_job(runtime, output_kind="solid"):
    prepared, kind = prepare(runtime, prepare_body())
    assert kind != "failure", prepared
    job, kind = invoke(runtime, "submit_generation", prepared["submit_request"], "submit-one")
    assert kind != "failure", job
    runtime.services.resources = Resources(runtime.store)
    artifacts = runtime.services.extension("artifacts")

    def registrar(caller, request):
        data = (runtime.root / request["staging_path"]).read_bytes()
        artifact = artifacts.promote(
            PROJECT,
            request["artifact_id"],
            data,
            kind=request["kind"],
            purpose=request["purpose"],
            media_type=request["media_type"],
        )
        with runtime.store.transaction() as tx:
            artifacts.put(tx, artifact)
        return artifact

    runtime.provider.registrar = registrar
    response = {"busy": False}
    calls = []

    def transport(request):
        calls.append(request)
        path = request.url.path
        if path == "/object_info":
            return httpx.Response(200, json=runtime.catalog)
        if path == "/queue":
            return httpx.Response(
                200,
                json={
                    "queue_running": ["external"] if response["busy"] else [],
                    "queue_pending": [],
                },
            )
        if path == "/system_stats":
            return httpx.Response(200, json={"devices": [{"vram_free": 2**30}]})
        if path == "/prompt":
            assert request.method == "POST"
            return httpx.Response(200, json={"prompt_id": "prompt-one"})
        if path == "/history/prompt-one":
            return httpx.Response(
                200,
                json={
                    "prompt-one": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {"save": {"images": [{"filename": "generated.png"}]}},
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=raster(output_kind))
        raise AssertionError(f"Unexpected provider call: {request.method} {path}")

    runtime.provider.client.close()
    runtime.provider.client = httpx.Client(transport=httpx.MockTransport(transport))
    return job, response, calls


@pytest.mark.parametrize("kind", ["solid", "gradient", "landscape", "tiny", "scanline", "noise"])
def test_pump_completed_raster_is_terminal_and_consumes_attempt(runtime, kind):
    job, _, calls = admitted_job(runtime, kind)
    completed = runtime.provider.pump(PROJECT, job["job_id"])[0]
    corrupt = kind in {"scanline", "noise"}
    assert completed["state"] == ("failed" if corrupt else "succeeded"), completed
    assert completed["error_code"] == ("corrupt_provider_output" if corrupt else None)
    assert bool(completed["artifact_ids"]) is not corrupt
    assert len([r for r in calls if r.url.path == "/prompt"]) == 1
    if corrupt:
        assert runtime.provider.execution(PROJECT, job["job_id"])["phase"] == "failed"
    with runtime.store.transaction(write=False) as tx:
        assert tx.connection.execute("SELECT COUNT(*) FROM resource_claims").fetchone()[0] == 0
        assert (
            tx.connection.execute(
                "SELECT state FROM budget_reservations WHERE id=?", (job["job_id"],)
            ).fetchone()[0]
            == "consumed"
        )
        assert (
            tx.connection.execute(
                "SELECT state FROM outbox WHERE id=?", (job["attempt_id"],)
            ).fetchone()[0]
            == "acknowledged"
        )
        if corrupt:
            assert not tx.all("artifacts")[2:]
    before = copy.deepcopy(completed)
    assert runtime.provider.pump(PROJECT, job["job_id"]) == []
    assert runtime.services.jobs.get(PROJECT, job["job_id"]) == before


@pytest.mark.parametrize("hold", ["busy", "resource"])
def test_pump_waiting_resource_does_not_duplicate_events(runtime, hold):
    job, response, calls = admitted_job(runtime)
    if hold == "busy":
        response["busy"] = True
    else:
        assert runtime.services.resources.acquire(
            "heavy-gpu", PROJECT, "other-job", "other-attempt"
        )
    with runtime.store.transaction(write=False) as tx:
        watermark = tx.watermark()
    for _ in range(6):
        waiting = runtime.provider.pump(PROJECT, job["job_id"])[0]
        assert waiting["state"] == "waiting_resource" and waiting["state_revision"] == 1
        assert runtime.provider.execution(PROJECT, job["job_id"]) == {"phase": "waiting"}
    assert not any(r.url.path == "/prompt" for r in calls)
    with runtime.store.transaction(write=False) as tx:
        events = tx.events(watermark)["events"]
        assert len(events) == 1 and events[0]["event_type"] == "job.state_changed"
        assert (
            tx.connection.execute(
                "SELECT state FROM budget_reservations WHERE id=?", (job["job_id"],)
            ).fetchone()[0]
            == "reserved"
        )
        if hold == "resource":
            assert tuple(
                tx.connection.execute("SELECT job_id,attempt_id FROM resource_claims").fetchone()
            ) == ("other-job", "other-attempt")
    response["busy"] = False
    if hold == "resource":
        runtime.services.resources.release(
            "heavy-gpu",
            PROJECT,
            "other-job",
            "other-attempt",
            terminal_evidence="Offline holder settled",
        )
    assert runtime.provider.pump(PROJECT, job["job_id"])[0]["state"] == "succeeded"
    assert len([r for r in calls if r.url.path == "/prompt"]) == 1
