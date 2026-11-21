"""Offline Klein preparation, retained raster validation and resource-hold regressions."""

import copy
import hashlib
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
from proscenium.contracts import canonical
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
ORDERED_REFS = ("z-form", "a-colour", "m-detail", "b-context")
SD_TURBO_REFERENCE = "Selected image profile requires one exact 512x512 reference"


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


def add_vae_dtype_node(catalog, *, legacy=False, options=("default", "fp32", "fp16", "bf16")):
    catalog["VAEComputeDtype"] = {
        "input": {
            "required": {
                "vae": ["VAE", {}],
                "dtype": [list(options)]
                if legacy
                else ["COMBO", {"default": "default", "options": list(options)}],
            }
        },
        "output": ["VAE"],
    }


@pytest.mark.parametrize("tiled", [False, True])
@pytest.mark.parametrize("vae_dtype", [None, "bf16"])
def test_reference_scaling_tiling_vae_dtype_and_admitted_replay(tiled, vae_dtype):
    original = graph(reference_artifact_id="reference", tiled_vae=tiled, vae_dtype=vae_dtype)
    assert original["reference_scale"] == {
        "class_type": "ImageScaleToTotalPixels",
        "inputs": {
            "image": ["reference", 0],
            "upscale_method": "lanczos",
            "megapixels": 1.0,
            "resolution_steps": 16,
        },
    }
    expected_vae = ["vae_dtype", 0] if vae_dtype else ["vae", 0]
    assert ("vae_dtype" in original) is bool(vae_dtype)
    if vae_dtype:
        assert original["vae_dtype"] == {
            "class_type": "VAEComputeDtype",
            "inputs": {"vae": ["vae", 0], "dtype": vae_dtype},
        }
    for key, kind in (("decode", "VAEDecode"), ("reference_encode", "VAEEncode")):
        assert original[key]["class_type"] == kind + ("Tiled" if tiled else "")
        assert original[key]["inputs"]["vae"] == expected_vae
        if tiled:
            assert {
                k: original[key]["inputs"][k]
                for k in ("tile_size", "overlap", "temporal_size", "temporal_overlap")
            } == {"tile_size": 512, "overlap": 64, "temporal_size": 64, "temporal_overlap": 8}
    admitted = bind_graph(original, "Concept", "42", {"reference": "artifact:reference"})
    replayed = bind_graph(admitted, "Concept", "42", {"reference": "uploaded/reference.png"})
    assert replayed["reference"]["inputs"]["image"] == "uploaded/reference.png"
    assert replayed["decode"]["inputs"]["vae"] == expected_vae
    assert replayed["reference_encode"]["inputs"]["vae"] == expected_vae
    assert original["noise"]["inputs"]["noise_seed"] == "$seed"
    for polarity in ("positive", "negative"):
        assert replayed["guider"]["inputs"][polarity] == ["reference_" + polarity, 0]


def test_historical_unscaled_and_tiled_graphs_bind_and_replay():
    historical = graph(reference_artifact_id="reference")
    del historical["reference_scale"]
    historical["reference_encode"]["inputs"]["pixels"] = ["reference", 0]
    admitted = bind_graph(historical, "Concept", "42", {"reference": "artifact:reference"})
    replayed = bind_graph(admitted, "Concept", "42", {"reference": "uploaded.png"})
    assert replayed["reference_encode"]["inputs"]["pixels"] == ["reference", 0]
    assert replayed["decode"]["class_type"] == "VAEDecode"
    assert replayed["decode"]["inputs"]["vae"] == ["vae", 0]

    historical_tiled = graph(reference_artifact_id="reference", tiled_vae=True)
    admitted_tiled = bind_graph(
        historical_tiled, "Concept", "42", {"reference": "artifact:reference"}
    )
    replayed_tiled = bind_graph(admitted_tiled, "Concept", "42", {"reference": "uploaded.png"})
    assert replayed_tiled["decode"]["class_type"] == "VAEDecodeTiled"
    assert replayed_tiled["reference_encode"]["class_type"] == "VAEEncodeTiled"
    assert replayed_tiled["decode"]["inputs"]["vae"] == ["vae", 0]


def test_klein_zero_and_one_reference_graphs_keep_legacy_bytes():
    # Ordered JSON digests from the original single-reference implementation.
    for refs, digest in (
        ([], "5f82ceee055e17a92fa26dcbfebb8c74ad9c5d40904e0d17a932c438a9ba4855"),
        (["reference"], "6e0eb29216a8f9086b8b1b996c9e2c077a6ece3d87566f296a683ed4397cfdce"),
    ):
        legacy = graph(reference_artifact_id=refs[0] if refs else None)
        ordered = graph(reference_artifact_ids=refs)
        assert json.dumps(legacy) == json.dumps(ordered)
        assert hashlib.sha256(json.dumps(ordered).encode()).hexdigest() == digest
    assert graph(reference_artifact_id="reference", reference_artifact_ids=("reference",)) == graph(
        reference_artifact_id="reference"
    )


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"reference_artifact_id": "one", "reference_artifact_ids": []}, "invalid request"),
        ({"reference_artifact_id": "one", "reference_artifact_ids": ["two"]}, "invalid request"),
        (
            {"reference_artifact_id": "one", "reference_artifact_ids": ["one", "two"]},
            "invalid request",
        ),
        ({"reference_artifact_id": ""}, "invalid request"),
        ({"reference_artifact_ids": [""]}, "invalid request"),
        ({"reference_artifact_ids": [None]}, "invalid request"),
        ({"reference_artifact_ids": ["../escape"]}, "invalid request"),
        ({"reference_artifact_ids": ["path\\image"]}, "invalid request"),
        ({"reference_artifact_ids": ["nul\0id"]}, "invalid request"),
        ({"reference_artifact_ids": ["one", "one"]}, "Duplicate conditioning image"),
        (
            {"reference_artifact_ids": [*ORDERED_REFS, "extra"]},
            "Image preparation accepts at most four references",
        ),
    ],
)
def test_klein_rejects_invalid_reference_ids(kwargs, message):
    with pytest.raises(DomainError, match=message) as error:
        graph(**kwargs)
    assert error.value.code == "invalid_request"


def ref_node(index):
    return "reference" if index == 0 else f"reference_{index + 1}"


def assert_reference_branches(built, images, *, tiled=False, bf16=False):
    positive, negative = ["text", 0], ["zero", 0]
    for i, image in enumerate(images):
        ref = ref_node(i)
        assert built[ref] == {"class_type": "LoadImage", "inputs": {"image": image}}
        assert built[f"{ref}_scale"] == {
            "class_type": "ImageScaleToTotalPixels",
            "inputs": {
                "image": [ref, 0],
                "upscale_method": "lanczos",
                "megapixels": 1.0,
                "resolution_steps": 16,
            },
        }
        tiles = {"tile_size": 512, "overlap": 64, "temporal_size": 64, "temporal_overlap": 8}
        assert built[f"{ref}_encode"] == {
            "class_type": "VAEEncodeTiled" if tiled else "VAEEncode",
            "inputs": {
                "pixels": [f"{ref}_scale", 0],
                "vae": ["vae_dtype" if bf16 else "vae", 0],
                **(tiles if tiled else {}),
            },
        }
        for polarity, previous in (("positive", positive), ("negative", negative)):
            assert built[f"{ref}_{polarity}"] == {
                "class_type": "ReferenceLatent",
                "inputs": {"conditioning": previous, "latent": [f"{ref}_encode", 0]},
            }
        positive, negative = [f"{ref}_positive", 0], [f"{ref}_negative", 0]
    assert built["guider"]["inputs"]["positive"] == positive
    assert built["guider"]["inputs"]["negative"] == negative


@pytest.mark.parametrize("count", [2, 3, 4])
def test_klein_workflow_and_bind_graph_chain_references_in_caller_order(count):
    ids = ORDERED_REFS[:count]
    built = workflow(RECIPE, ids)
    assert built == graph(reference_artifact_ids=ids)
    assert_reference_branches(built, [f"artifact:{identity}" for identity in ids])
    uploads = {identity: f"comfy/{identity}.png" for identity in ids}
    bound = bind_graph(built, "Concept", "42", uploads)
    assert_reference_branches(bound, [uploads[identity] for identity in ids])


@pytest.mark.parametrize("bypassed", [0, 1, 2], ids=["first", "middle", "last"])
def test_bind_graph_rejects_each_bypassed_positive_reference(bypassed):
    ids = ORDERED_REFS[:3]
    built = graph(reference_artifact_ids=ids)
    bypassed_node = built[f"{ref_node(bypassed)}_positive"]
    previous = bypassed_node["inputs"]["conditioning"]
    if bypassed == len(ids) - 1:
        built["guider"]["inputs"]["positive"] = previous
    else:
        built[f"{ref_node(bypassed + 1)}_positive"]["inputs"]["conditioning"] = previous
    # The bypassed image still contributes to the negative branch.
    with pytest.raises(DomainError, match="Reference is not conditioning"):
        bind_graph(built, "Concept", "42", {identity: f"{identity}.png" for identity in ids})


def test_bind_graph_rejects_duplicate_artifact_bindings():
    built = graph(reference_artifact_ids=("one", "two"))
    built["reference_2"]["inputs"]["image"] = "artifact:one"
    with pytest.raises(DomainError, match="Duplicate conditioning image"):
        bind_graph(built, "Concept", "42", {"one": "one.png"})


@pytest.mark.parametrize("cycle", ["positive", "negative", "vae_encode"])
def test_bind_graph_rejects_reachable_cycles(cycle):
    built = graph(reference_artifact_ids=("one", "two"))
    if cycle == "vae_encode":
        built["reference_encode"]["inputs"]["pixels"] = ["reference_encode", 0]
    else:
        built[f"reference_{cycle}"]["inputs"]["conditioning"] = [f"reference_2_{cycle}", 0]
    with pytest.raises(DomainError, match="Invalid graph link"):
        bind_graph(built, "Concept", "42", {"one": "one.png", "two": "two.png"})


def test_bind_graph_rejects_reference_order_different_from_request():
    built = graph(reference_artifact_ids=("b", "a"))
    with pytest.raises(DomainError, match="Reference is not conditioning"):
        bind_graph(built, "Concept", "42", {"a": "a.png", "b": "b.png"})


@pytest.mark.parametrize("negative", ["omitted", "swapped", "wrong_base"])
def test_bind_graph_rejects_invalid_negative_reference_chain(negative):
    built = graph(reference_artifact_ids=("a", "b"))
    if negative == "omitted":
        built["guider"]["inputs"]["negative"] = ["zero", 0]
    elif negative == "swapped":
        built["reference_negative"]["inputs"]["latent"] = ["reference_2_encode", 0]
        built["reference_2_negative"]["inputs"]["latent"] = ["reference_encode", 0]
    else:
        built["other_text"] = copy.deepcopy(built["text"])
        built["zero"]["inputs"]["conditioning"] = ["other_text", 0]
    with pytest.raises(DomainError, match="Reference is not conditioning"):
        bind_graph(built, "Concept", "42", {"a": "a.png", "b": "b.png"})


@pytest.mark.parametrize("guiders", ["none_reachable", "second_reachable_omits_refs"])
def test_bind_graph_validates_every_reachable_guider(guiders):
    built = graph(reference_artifact_id="reference")
    if guiders == "none_reachable":
        built["save"]["inputs"]["images"] = ["reference", 0]
    else:
        for identity in ("guider", "sample", "decode", "save"):
            built[f"second_{identity}"] = copy.deepcopy(built[identity])
        built["second_guider"]["inputs"]["positive"] = ["text", 0]
        built["second_sample"]["inputs"]["guider"] = ["second_guider", 0]
        built["second_decode"]["inputs"]["samples"] = ["second_sample", 0]
        built["second_save"]["inputs"]["images"] = ["second_decode", 0]
    with pytest.raises(DomainError, match="Reference is not conditioning"):
        bind_graph(built, "Concept", "42", {"reference": "uploaded.png"})


def test_bind_graph_rejects_reachable_reference_latent_without_requested_references():
    built = graph()
    built["hidden_ref"] = {
        "class_type": "ReferenceLatent",
        "inputs": {"conditioning": ["text", 0], "latent": ["latent", 0]},
    }
    built["zero"]["inputs"]["conditioning"] = ["hidden_ref", 0]
    with pytest.raises(DomainError, match="Reference is not conditioning"):
        bind_graph(built, "Concept", "42", {})


@pytest.mark.parametrize("image", [None, "", "extra.png", ["latent", 0], "artifact:other"])
def test_bind_graph_requires_a_declared_token_in_every_load_image(image):
    built = graph()
    built["extra"] = {"class_type": "LoadImage", "inputs": {"image": image}}
    with pytest.raises(DomainError, match="Unbound conditioning image"):
        bind_graph(built, "Concept", "42", {})


@pytest.mark.parametrize("node,key", [("reference", "other"), ("save", "filename_prefix")])
def test_bind_graph_disallows_artifact_tokens_outside_load_image_input(node, key):
    built = graph(reference_artifact_id="reference")
    built[node]["inputs"][key] = "artifact:reference"
    with pytest.raises(DomainError, match="Unbound conditioning image"):
        bind_graph(built, "Concept", "42", {"reference": "uploaded.png"})


def test_klein_vae_dtype_validation():
    with pytest.raises(DomainError):
        graph(vae_dtype="fp8")


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
@pytest.mark.parametrize("tiled_catalog", [False, True])
@pytest.mark.parametrize("bf16_catalog", [False, True])
def test_preparation_authority_recipe_defaults_and_fenced_replay(
    runtime, explicit_project, explicit_task, tiled_catalog, bf16_catalog
):
    runtime.provider.options["image_recipes"] = {
        "comfy": {**RECIPE, "width": 768, "height": 512, "steps": 7, "max_pixels": 524288}
    }
    if tiled_catalog:
        add_tiled_nodes(runtime.catalog)
    if bf16_catalog:
        add_vae_dtype_node(runtime.catalog)
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
    assert saved["reference_encode"]["class_type"] == "VAEEncode"
    assert saved["decode"]["class_type"] == "VAEDecode"
    expected_vae = ["vae_dtype", 0] if bf16_catalog else ["vae", 0]
    assert ("vae_dtype" in saved) is bf16_catalog
    assert saved["reference_encode"]["inputs"]["vae"] == expected_vae
    assert saved["decode"]["inputs"]["vae"] == expected_vae
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


@pytest.mark.parametrize(
    "width,height,expected_tiled",
    [
        (512, 512, False),
        (1024, 576, False),
        (1024, 1024, False),
        (1024, 1040, True),
        (1920, 1088, True),
    ],
)
def test_tiling_activates_only_above_one_megapixel_when_advertised(width, height, expected_tiled):
    cat = {}
    add_tiled_nodes(cat)
    add_vae_dtype_node(cat, legacy=True)
    built = workflow(
        {**RECIPE, "width": width, "height": height, "max_pixels": 2_088_960},
        "reference",
        catalog=cat,
    )
    assert built["decode"]["class_type"] == ("VAEDecodeTiled" if expected_tiled else "VAEDecode")
    assert built["reference_encode"]["class_type"] == (
        "VAEEncodeTiled" if expected_tiled else "VAEEncode"
    )
    assert built["vae_dtype"] == {
        "class_type": "VAEComputeDtype",
        "inputs": {"vae": ["vae", 0], "dtype": "bf16"},
    }


@pytest.mark.parametrize(
    "nodes",
    [
        {},
        {"VAEDecodeTiled": {}},
        {"VAEEncodeTiled": {}},
        {
            "VAEComputeDtype": {
                "input": {"required": {"vae": ["VAE"], "dtype": [["fp16"]]}},
                "output": ["VAE"],
            }
        },
    ],
)
def test_partial_or_unsupported_catalog_keeps_untiled_default_vae_graph(nodes):
    built = workflow(
        {**RECIPE, "width": 1920, "height": 1088, "max_pixels": 2_088_960}, catalog=nodes
    )
    assert built["decode"]["class_type"] == "VAEDecode"
    assert "vae_dtype" not in built
    assert built["decode"]["inputs"]["vae"] == ["vae", 0]


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


def direct_generation_request(runtime, built, reference_ids, *, models=None):
    artifacts = runtime.services.extension("artifacts")
    with runtime.store.transaction() as tx:
        for identity, data, media_type in (
            ("workflow-direct", canonical(built), "application/json"),
            ("prompt-direct", b"Concept", "text/plain"),
        ):
            artifacts.put(
                tx,
                artifacts.promote(
                    PROJECT,
                    identity,
                    data,
                    kind="document",
                    purpose="metadata",
                    media_type=media_type,
                ),
            )
    return {
        "schema_version": "1.0",
        "job_id": "generation-direct",
        "project_id": PROJECT,
        "generation_task_id": TASK,
        "target_id": TASK,
        "provider_id": "comfy",
        "capability": "image",
        "workflow_artifact_id": "workflow-direct",
        "prompt_artifact_id": "prompt-direct",
        "input_artifact_ids": reference_ids,
        "seed": "42",
        "model_ids": models if models is not None else [RECIPE[k] for k in ("unet", "clip", "vae")],
        "settings": {},
    }


def assert_direct_submit_rejected(runtime, request, message):
    result, kind = invoke(runtime, "submit_generation", request)
    assert kind == "failure" and result["error"] == "invalid_request", result
    assert result["message"] == message
    assert not runtime.calls
    with runtime.store.transaction(write=False) as tx:
        assert not tx.all("jobs") and not tx.all("providers.tasks")
        assert tx.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_direct_klein_submit_rejects_over_limit_before_budget_admission(runtime):
    request = direct_generation_request(runtime, graph(), [*ORDERED_REFS, "extra"])
    assert_direct_submit_rejected(
        runtime, request, "Image preparation accepts at most four references"
    )


@pytest.mark.parametrize("profile", [sd_turbo, sd_turbo_fp16])
@pytest.mark.parametrize("reference_ids", [[], ["one", "two"]], ids=["missing", "too_many"])
def test_direct_sd_turbo_submit_requires_one_reference_before_image_reads(
    runtime, profile, reference_ids
):
    runtime.provider.options["image_profiles"] = {"comfy": profile.REVISION}
    request = direct_generation_request(
        runtime, profile.graph("one"), reference_ids, models=[profile.FILENAME]
    )
    assert_direct_submit_rejected(runtime, request, SD_TURBO_REFERENCE)


@pytest.mark.parametrize("connected", [False, True], ids=["disconnected", "conditioning"])
def test_direct_klein_submit_rejects_extra_literal_load_image(runtime, connected):
    promote_reference(runtime)
    literal = {"class_type": "LoadImage", "inputs": {"image": "sneaky.png"}}
    if connected:
        built = graph(reference_artifact_ids=("reference", "sneaky"))
        built["reference_2"] = literal
    else:
        built = graph(reference_artifact_id="reference")
        built["extra"] = literal
    request = direct_generation_request(runtime, built, ["reference"])
    assert_direct_submit_rejected(runtime, request, "Unbound conditioning image")


def test_direct_klein_submit_rejects_missing_negative_reference(runtime):
    promote_reference(runtime)
    built = graph(reference_artifact_id="reference")
    built["guider"]["inputs"]["negative"] = ["zero", 0]
    request = direct_generation_request(runtime, built, ["reference"])
    assert_direct_submit_rejected(runtime, request, "Reference is not conditioning")


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


@pytest.mark.parametrize("profile", [sd_turbo, sd_turbo_fp16])
@pytest.mark.parametrize(
    "ids,code", [([], "capability_unavailable"), (["one", "two"], "invalid_request")]
)
def test_sd_turbo_workflow_and_preparation_require_one_reference(runtime, profile, ids, code):
    runtime.provider.options["image_profiles"] = {"comfy": profile.REVISION}
    with pytest.raises(DomainError, match=SD_TURBO_REFERENCE) as error:
        workflow({"profile_revision": profile.REVISION}, ids)
    assert error.value.code == code
    if not ids:
        assert error.value.status == 501
    result, kind = invoke(
        runtime, "prepare_image_generation", prepare_body(reference_artifact_ids=ids)
    )
    assert kind == "failure"
    assert result["message"] == SD_TURBO_REFERENCE and result["error"] == code
    with runtime.store.transaction(write=False) as tx:
        assert not tx.all("jobs") and not tx.all("artifacts")
        assert tx.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


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
            elif kind == "black":
                colour = (0, 0, 0)
            elif kind == "quadrant_wipeout":
                # Matches 3-quadrant NaN->0 wipeout from tiled VAE strip failure.
                colour = (
                    (0, 0, 0)
                    if (y < height // 2 or x >= width // 2)
                    else (40 + (x % 32), 70 + (y % 32), 110)
                )
            elif kind == "half_wipeout":
                colour = (0, 0, 0) if y < height // 2 else (40 + (x % 32), 70 + (y % 32), 110)
            elif kind == "single_quadrant_zero":
                colour = (
                    (0, 0, 0)
                    if (x < width // 2 and y < height // 2)
                    else (40 + (x % 32), 70 + (y % 32), 110)
                )
            elif kind == "starfield":
                # Upper two-thirds dark starfield, lower third lit horizon silhouette.
                if y >= (height * 2) // 3:
                    colour = (25, 45, 75)
                elif (x * 17 + y * 31) % 29 == 0:
                    colour = (80, 95, 120)
                else:
                    colour = (0, 0, 0)
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


@pytest.mark.parametrize(
    "kind", ["solid", "gradient", "landscape", "single_quadrant_zero", "starfield"]
)
def test_clean_rasters_pass_inspection(kind):
    assert ImagePlugin().inspect_output(raster(kind), 16384)["sha256_verified_during_registration"]


@pytest.mark.parametrize("size", [(1, 1), (32, 32), (63, 128), (128, 63)])
def test_small_rasters_skip_noise_guard(size):
    assert ImagePlugin().inspect_output(raster("noise", size), 16384)


@pytest.mark.parametrize("kind", ["scanline", "noise", "black", "quadrant_wipeout", "half_wipeout"])
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


def admitted_job(runtime, output_kind="solid", *, prepared=None):
    if prepared is None:
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
            derived_from=request.get("derived_from"),
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
        if path == "/upload/image":
            assert request.method == "POST"
            number = sum(call.url.path == path for call in calls)
            return httpx.Response(200, json={"subfolder": "inputs", "name": f"{number}.png"})
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


@pytest.mark.parametrize("count", [2, 3, 4])
def test_prepare_replay_and_pump_preserve_ordered_reference_provenance(runtime, count):
    ids = list(ORDERED_REFS[:count])
    artifacts = runtime.services.extension("artifacts")
    for i, identity in enumerate(ids):
        data = raster("gradient", (64 + i * 16, 64))
        artifact = artifacts.promote(
            PROJECT, identity, data, kind="image", purpose="reference", media_type="image/png"
        )
        with runtime.store.transaction() as tx:
            artifacts.put(tx, artifact)
    recipe = {**RECIPE, "width": 1920, "height": 1088, "max_pixels": 2_088_960}
    runtime.provider.options["image_recipes"] = {"comfy": recipe}
    add_tiled_nodes(runtime.catalog)
    add_vae_dtype_node(runtime.catalog)
    body = prepare_body(reference_artifact_ids=ids)
    prepared, kind = prepare(runtime, body)
    assert kind == "success", prepared
    assert prepared["input_artifact_ids"] == prepared["submit_request"]["input_artifact_ids"] == ids
    saved = json.loads(artifacts.ready_path(PROJECT, prepared["workflow_artifact_id"]).read_bytes())
    assert saved == workflow(recipe, ids, catalog=runtime.catalog)
    assert saved["decode"]["class_type"] == "VAEDecodeTiled"
    assert saved["decode"]["inputs"]["vae"] == ["vae_dtype", 0]
    assert saved["vae_dtype"]["inputs"]["dtype"] == "bf16"
    assert_reference_branches(
        saved, [f"artifact:{identity}" for identity in ids], tiled=True, bf16=True
    )
    runtime.state.control = lambda tx: {"fenced": True}
    runtime.services.gate.close(PROJECT)
    calls = len(runtime.calls)
    replayed, kind = prepare(runtime, body)
    assert kind == "success" and replayed == {**prepared, "replayed": True}
    conflict, _ = prepare(runtime, {**body, "reference_artifact_ids": ids[::-1]})
    assert conflict["error"] == "idempotency_conflict" and len(runtime.calls) == calls
    runtime.state.control = lambda tx: {"fenced": False}
    runtime.services.gate.resume(PROJECT, reconciliation_evidence="Offline fixture")

    job, _, requests = admitted_job(runtime, prepared=prepared)
    completed = runtime.provider.pump(PROJECT, job["job_id"])[0]
    assert completed["state"] == "succeeded", completed
    assert [r.url.path for r in requests] == [
        "/object_info",
        "/queue",
        "/system_stats",
        *(["/upload/image"] * count),
        "/prompt",
        "/history/prompt-one",
        "/view",
    ]
    submitted_graph = json.loads(requests[3 + count].content)["prompt"]
    assert_reference_branches(
        submitted_graph, [f"inputs/{i + 1}.png" for i in range(count)], tiled=True, bf16=True
    )
    output_id, provenance_id = completed["artifact_ids"]
    provenance = json.loads(artifacts.ready_path(PROJECT, provenance_id).read_bytes())
    derived_ids = [f"{job['job_id']}-artifact-input-{i}" for i in range(count)]
    for upload, derived_id, original_id in zip(
        requests[3 : 3 + count], derived_ids, ids, strict=True
    ):
        derivative = artifacts.get(PROJECT, derived_id)
        assert derivative["derived_from"] == [
            {
                "kind": "encode",
                "input_artifact_ids": [original_id],
                "parameters": {"encoding": "RGB PNG", "exif_transposed": True},
            }
        ]
        assert (
            derivative["sha256"]
            == provenance["inputs"]["references"][original_id]["derived_sha256"]
        )
        assert f'filename="{derivative["sha256"]}.png"'.encode() in upload.content
        assert artifacts.ready_path(PROJECT, derived_id).read_bytes() in upload.content
    assert provenance["derived_images"] == derived_ids
    assert provenance["inputs"]["request"]["input_artifact_ids"] == ids
    assert provenance["prompt_id"] == "prompt-one"
    assert provenance["graph"] == submitted_graph
    assert provenance["graph_sha256"] == hashlib.sha256(canonical(submitted_graph)).hexdigest()
    assert provenance["output_artifact_ids"] == [output_id]
    assert provenance["output_inspections"] == [
        {
            "artifact_id": output_id,
            "sha256": artifacts.get(PROJECT, output_id)["sha256"],
            "inspection": {"sha256_verified_during_registration": True},
        }
    ]


@pytest.mark.parametrize(
    "kind",
    [
        "solid",
        "gradient",
        "landscape",
        "tiny",
        "scanline",
        "noise",
        "black",
        "quadrant_wipeout",
    ],
)
def test_pump_completed_raster_is_terminal_and_consumes_attempt(runtime, kind):
    job, _, calls = admitted_job(runtime, kind)
    completed = runtime.provider.pump(PROJECT, job["job_id"])[0]
    corrupt = kind in {"scanline", "noise", "black", "quadrant_wipeout"}
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


def test_prepare_canonicalises_unpublished_target_alias_onto_active_intent(runtime):
    with runtime.store.transaction() as tx:
        tx.put(
            "contexts",
            "ctx-new",
            dict(
                schema_version="1.0",
                context_id="ctx-new",
                project_id=PROJECT,
                captured_at="2026-11-15T10:00:00Z",
                item_id=None,
                version_id=None,
                view_mode="none",
                target=None,
                camera=None,
                range=None,
                selected_objects=[],
                attachments=[],
                annotations=[],
                approved_findings=[],
                request_target=dict(
                    kind="new_item", item_kind="storyboard", intent_id="intent-board"
                ),
                reference_versions=[],
                execution_scope="default",
            ),
            schema="ContextSnapshot",
        )
        tx.put(
            "requests",
            "request-one",
            dict(project_id=PROJECT, submission={"context_id": "ctx-new"}),
            expected_revision=tx.revision("requests", "request-one"),
        )
    first, kind1 = prepare(
        runtime,
        dict(
            schema_version="1.0",
            operation_id="prep-alias-1",
            prompt="Opening shot",
            seed="11",
            target_id="unpublished-panel-alias-1",
        ),
    )
    second, kind2 = prepare(
        runtime,
        dict(
            schema_version="1.0",
            operation_id="prep-alias-2",
            prompt="Opening shot v2",
            seed="12",
            target_id="unpublished-panel-alias-2",
        ),
    )
    assert kind1 != "failure" and kind2 != "failure"
    assert first["submit_request"]["target_id"] == "intent-board"
    assert second["submit_request"]["target_id"] == "intent-board"


def test_bounded_get_accepts_large_comfy_object_info_catalog(runtime):
    from types import SimpleNamespace

    from proscenium.providers.preparation import MAX_CATALOG_BYTES, bounded_get

    large_catalog = {
        **runtime.catalog,
        "PaddingNode": {"description": "x" * (3 * 1024**2)},
    }
    payload = json.dumps(large_catalog).encode()
    assert 2 * 1024**2 < len(payload) < MAX_CATALOG_BYTES
    client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=payload))
    )
    try:
        provider = SimpleNamespace(client=client)
        parsed = bounded_get(provider, {"endpoint": "http://127.0.0.1:8188"}, "/object_info")
        assert "UNETLoader" in parsed and "PaddingNode" in parsed
    finally:
        client.close()
