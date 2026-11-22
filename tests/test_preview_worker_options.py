from types import SimpleNamespace

import pytest
from proscenium.api.errors import DomainError
from proscenium.contracts import validate
from proscenium.previews.blender_worker import _is_unmapped_collection_instance, configure_denoise
from proscenium.previews.geometry_nodes import modifier_kind
from proscenium.previews.journal import unsupported_blender_features


@pytest.mark.parametrize(
    "features",
    [
        [],
        ["native-mesh", "cycles-cpu"],
        ("CYCLES", "python"),
        {" geometry_nodes ", "linked_libraries"},
    ],
)
def test_supported_blender_features(features):
    assert unsupported_blender_features(features) == []


@pytest.mark.parametrize(
    "feature",
    [
        "eevee",
        "workbench",
        "cuda",
        "optix",
        "hip",
        "metal",
        "gpu",
        "cloth",
        "fluid",
        "smoke",
        "alembic",
        "usd",
        "secret-custom-tag",
        "",
        "  ",
    ],
)
def test_unsupported_blender_features(feature):
    assert unsupported_blender_features(["cpu", feature, feature]) == [feature]


def test_malformed_native_features_still_report_unsupported():
    assert unsupported_blender_features(["optix", None, {}]) == ["None", "optix", "{}"]


@pytest.mark.parametrize(
    "spec,enabled", [({}, True), ({"denoise": True}, True), ({"denoise": False}, False)]
)
@pytest.mark.parametrize("gpu_property", [True, False])
def test_cycles_denoise_default_and_override(spec, enabled, gpu_property):
    cycles = SimpleNamespace(**({"denoising_use_gpu": True} if gpu_property else {}))
    configure_denoise(cycles, spec)
    assert cycles.use_denoising is enabled
    if enabled:
        assert cycles.denoiser == "OPENIMAGEDENOISE"
        assert cycles.denoising_input_passes == "RGB_ALBEDO_NORMAL"
        assert cycles.denoising_prefilter == "ACCURATE"
    else:
        assert not hasattr(cycles, "denoiser")
    if gpu_property:
        assert cycles.denoising_use_gpu is not enabled
    else:
        assert not hasattr(cycles, "denoising_use_gpu")


@pytest.mark.parametrize(
    "kind,instancing,ids,allowed",
    [
        ("EMPTY", "COLLECTION", {}, True),
        ("EMPTY", "COLLECTION", {"proscenium_object_id": "o"}, False),
        ("EMPTY", "COLLECTION", {"proscenium_instance_id": "i"}, False),
        ("EMPTY", "NONE", {}, False),
        ("MESH", "COLLECTION", {}, False),
    ],
)
def test_collection_instance_exception_only_accepts_untagged_collection_empties(
    kind, instancing, ids, allowed
):
    original = SimpleNamespace(type=kind, instance_type=instancing, get=ids.get)
    assert (
        _is_unmapped_collection_instance(SimpleNamespace(parent=SimpleNamespace(original=original)))
        is allowed
    )
    assert _is_unmapped_collection_instance(SimpleNamespace(parent=original)) is allowed
    assert not _is_unmapped_collection_instance(SimpleNamespace())


def test_render_spec_accepts_optional_boolean_denoise():
    spec = dict(
        intent_id="proxy",
        shot_id=None,
        purpose="proxy",
        source_closure_digest="a" * 64,
        scene_name="Scene",
        camera=None,
        frames={"start": 1, "end": 1},
        fps={"numerator": 24, "denominator": 1},
        dimensions={"height": 512, "width": 512},
        engine="CYCLES",
        device="CPU",
        samples=8,
        colour_management={"view_transform": "AgX", "look": "None", "exposure": 0, "gamma": 1},
        renderer_revision="fixture",
    )
    validate("RenderSpec", spec)
    for value in (True, False):
        validate("RenderSpec", {**spec, "denoise": value})
    for value in (None, 0, "true"):
        with pytest.raises(DomainError, match="Invalid RenderSpec"):
            validate("RenderSpec", {**spec, "denoise": value})


@pytest.mark.parametrize("key", ["proscenium_object_id", "proscenium_instance_id"])
@pytest.mark.parametrize("value", ["", 0, False, None])
def test_collection_instance_rejects_present_falsy_identity_keys(key, value):
    properties = {key: value}
    original = SimpleNamespace(type="EMPTY", instance_type="COLLECTION", get=properties.get)
    assert not _is_unmapped_collection_instance(SimpleNamespace(parent=original))
    original.keys = properties.keys
    # A native property getter may report its default even when the key exists.
    original.get = lambda _key, default: default
    assert not _is_unmapped_collection_instance(SimpleNamespace(parent=original))


@pytest.mark.parametrize(
    "change", [None, "object", "dynamics", "disk_cache", "external", "baked", "emitter"]
)
def test_only_static_cache_free_path_hair_bypasses_simulation_cache_guard(change):
    particle = SimpleNamespace(
        settings=SimpleNamespace(type="HAIR", render_type="PATH"),
        point_cache=SimpleNamespace(use_disk_cache=False, use_external=False, is_baked=False),
        use_hair_dynamics=False,
    )
    if change == "object":
        particle.settings.render_type = "OBJECT"
    elif change == "dynamics":
        particle.use_hair_dynamics = True
    elif change == "disk_cache":
        particle.point_cache.use_disk_cache = True
    elif change == "external":
        particle.point_cache.use_external = True
    elif change == "baked":
        particle.point_cache.is_baked = True
    elif change == "emitter":
        particle.settings.type = "EMITTER"
    assert modifier_kind(SimpleNamespace(type="PARTICLE_SYSTEM", particle_system=particle)) == (
        "ordinary" if change is None else "unsupported_simulation_cache"
    )
