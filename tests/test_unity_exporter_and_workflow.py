import argparse
import json
import math
import random
import re
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from proscenium.copilot.native import UNITY_INSTRUCTIONS, Configuration, unity_agent

from tools.unity import export_blender as exporter
from tools.unity import workflow


@pytest.fixture
def unity_skill_and_prompt(tmp_path, monkeypatch):
    skill = (Path(__file__).resolve().parents[1] / "agent/skills/unity/SKILL.md").read_text()
    monkeypatch.chdir(tmp_path)  # Model the active workspace, not the app checkout.
    config = Configuration.load({"worker_model": "test-worker"})
    return skill, unity_agent(config)["prompt"]


def test_unity_prompt_starts_with_instructions_and_embeds_checkout_independent_skill(
    unity_skill_and_prompt,
):
    skill, prompt = unity_skill_and_prompt
    assert prompt.startswith(UNITY_INSTRUCTIONS)
    assert skill in prompt.removeprefix(UNITY_INSTRUCTIONS)
    assert not re.search(r"\]\(\s*<?\.\./", skill)
    assert not re.search(r"^\s*\[[^\]\n]+\]:\s*<?\.\./", skill, re.MULTILINE)
    for forbidden in (
        "plan_tools.py",
        "tools/comfy/graph.py",
        "python3 -m tools.blender",
        "tools/unity/",
        "tools.unity.",
    ):
        assert forbidden not in prompt


def _documented_unity_commands(skill):
    fences = re.compile(r"^[ \t]*```bash[ \t]*\n(.*?)^[ \t]*```[ \t]*$", re.MULTILINE | re.DOTALL)
    for block in fences.findall(skill):
        for line in block.replace("\\\n", " ").splitlines():
            if re.match(r"\s*unity\s", line):
                yield shlex.split(line, comments=True)
    for inline in re.findall(r"`(unity\s+[^`]+)`", fences.sub("", skill)):
        yield shlex.split(inline)


def test_unity_skill_cli_examples_scope_inspection_and_close_positionally(unity_skill_and_prompt):
    skill, _ = unity_skill_and_prompt
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--project-path")
    parser.add_argument("--format")
    editor_commands = []
    close_commands = []
    for _, verb, *args in _documented_unity_commands(skill):
        if verb == "command":
            separator = args.index("--") if "--" in args else len(args)
            options, command = parser.parse_known_args(args[:separator])
            assert options.project_path and not options.project_path.startswith("-")
            editor_commands.append((tuple(command), options, args[separator + 1 :]))
        elif verb == "close":
            assert not any(arg.split("=", 1)[0] == "--project-path" for arg in args)
            assert len(args) == 1 and not args[0].startswith("-")
            close_commands.append(args[0])

    project_paths = {options.project_path for _, options, _ in editor_commands}
    assert project_paths
    assert set(close_commands) & project_paths
    assert set(close_commands) <= project_paths | {"<project-path>"}

    console = [
        (options, args) for cmd, options, args in editor_commands if cmd == ("console", "get")
    ]
    assert console
    for options, args in console:
        assert options.format == "json"
        assert "--since-session" in args
        assert "--tail" not in args
        assert args[args.index("--level") + 1] == "error"

    auto_tick = [args for cmd, _, args in editor_commands if cmd == ("editor", "set_auto_tick")]
    assert auto_tick
    for args in auto_tick:
        assert args[args.index("--persist") + 1] == "false"

    capture_sources = {
        args[args.index("--source") + 1]
        for cmd, _, args in editor_commands
        if cmd == ("capture", "game_view")
    }
    assert {"camera", "screen"} <= capture_sources


def test_unity_skill_preserves_exact_runtime_apis_and_receipt_fields(unity_skill_and_prompt):
    skill, _ = unity_skill_and_prompt
    for identifier in (
        "PauseMenu",
        "Cursor.visible",
        "Cursor.lockState",
        "CursorLockMode.None",
        "CursorLockMode.Locked",
        "Time.timeScale",
        "VideoAudioOutputMode.Direct",
        "VideoAudioOutputMode.AudioSource",
        "VideoAudioOutputMode.None",
        "SetTargetAudioSource",
        "ColorSpace.Linear",
        "CameraClearFlags.SolidColor",
        "CameraClearFlags.Skybox",
        "k_MaxVisibleLightCountMobile",
        "validation.status",
        "build_output",
    ):
        assert re.search(rf"\b{re.escape(identifier)}\b", skill), identifier
    assert not re.search(r"\bAudioOutputMode\b", skill)
    assert re.search(r"\bVP8\b", skill) and re.search(r"\bVP9\b", skill) and ".webm" in skill
    for field, expected in (
        ("counts.error", "0"),
        ("groundTruth.consoleErrors", "0"),
        ("groundTruth.compilationFailed", "false"),
        ("dropped", "false"),
    ):
        assert re.search(rf"\b{re.escape(field)}\s*==\s*{expected}\b", skill), field


def _product(matrix, vector):
    return tuple(sum(a * b for a, b in zip(row, vector, strict=True)) for row in matrix)


def _axis_rotation(axis, angle):
    x, y, z = (component / math.hypot(*axis) for component in axis)
    c, s = math.cos(angle), math.sin(angle)
    t = 1 - c
    return (
        (t * x * x + c, t * x * y - s * z, t * x * z + s * y),
        (t * x * y + s * z, t * y * y + c, t * y * z - s * x),
        (t * x * z - s * y, t * y * z + s * x, t * z * z + c),
    )


def _matrix(rotation, scale=(1, 1, 1), translation=(0, 0, 0)):
    return tuple(
        tuple(r * s for r, s in zip(row, scale, strict=True)) + (t,)
        for row, t in zip(rotation, translation, strict=True)
    ) + ((0, 0, 0, 1),)


def _rotate(quaternion, vector):
    x, y, z, w = quaternion
    return _product(
        (
            (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
        ),
        vector,
    )


def _unity_axes(vector, unit):
    x, y, z = vector
    return x * unit, z * unit, y * unit


def _apply_unity_trs(trs, vector):
    position, quaternion, scale = trs
    rotated = _rotate(quaternion, tuple(a * b for a, b in zip(scale, vector, strict=True)))
    return tuple(a + b for a, b in zip(position, rotated, strict=True))


@pytest.mark.parametrize(
    ("linear", "srgb"),
    [
        (-1, 0),
        (0, 0),
        (0.0031308, 0.040449936),
        (0.024, 0.168018287683),
        (0.5, 0.735356983052),
        (1, 1),
        (2, 1),
        (float("nan"), 0),
    ],
)
def test_linear_to_srgb(linear, srgb):
    assert exporter.linear_to_srgb(linear) == pytest.approx(srgb, abs=1e-9)


@pytest.mark.parametrize("signs", [(1, 1, 1), (-1, 1, 1), (1, -1, 1), (-1, -1, 1), (-1, -1, -1)])
def test_trs_preserves_points_and_has_proper_unit_rotation(signs):
    rng = random.Random(635)
    for _ in range(25):
        axis = tuple(rng.uniform(-1, 1) for _ in range(3))
        angle = rng.uniform(-math.pi, math.pi)
        source_scale = tuple(rng.uniform(0.1, 7) * sign for sign in signs)
        translation = tuple(rng.uniform(-60, 60) for _ in range(3))
        unit = rng.uniform(0.012, 12)
        matrix = _matrix(_axis_rotation(axis, angle), source_scale, translation)
        trs = exporter.blender_matrix_to_unity_trs(matrix, unit)
        assert trs is not None
        _, rotation, scale = trs
        assert math.hypot(*rotation) == pytest.approx(1.0, abs=1e-12)
        assert scale == pytest.approx(
            (math.prod(signs) * abs(source_scale[0]), abs(source_scale[2]), abs(source_scale[1]))
        )
        basis = tuple(_rotate(rotation, a) for a in [(1, 0, 0), (0, 1, 0), (0, 0, 1)])
        a, b, c = basis
        determinant = (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        )
        assert determinant == pytest.approx(1.0, abs=1e-12)
        for _ in range(10):
            vertex = tuple(rng.uniform(-3, 3) for _ in range(3))
            point = _product(matrix, (*vertex, 1))[:3]
            assert _apply_unity_trs(trs, _unity_axes(vertex, unit)) == pytest.approx(
                _unity_axes(point, unit), abs=1e-5, rel=0
            )


@pytest.mark.parametrize(
    "matrix",
    [
        ((1, 0.2, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)),
        _matrix(_axis_rotation((1, 2, 3), 0.4), (2, 1, 0)),
        _matrix(_axis_rotation((1, 2, 3), 0.4), (2, 1, 1e-8)),
    ],
)
def test_trs_declines_shear_or_degenerate_scale(matrix):
    assert exporter.blender_matrix_to_unity_trs(matrix, 1) is None


@pytest.mark.parametrize(
    ("axis", "angle"), [((0, 0, 1), 0), ((2, 1, 3), 0.9), ((1, 0, 0), math.pi / 2)]
)
def test_light_forward_and_up_match_blender(axis, angle):
    matrix = _matrix(_axis_rotation(axis, angle), (1, 2, 3), (2, 4, 1))
    quaternion = exporter.blender_light_rotation_to_unity(matrix)
    assert quaternion is not None
    for source, target in [((0, 0, -1, 0), (0, 0, 1)), ((0, 1, 0, 0), (0, 1, 0))]:
        vector = _product(matrix, source)[:3]
        assert _rotate(quaternion, target) == pytest.approx(
            _unity_axes(vector, 1 / math.hypot(*vector)), abs=1e-12
        )


def _link(node, output=None):
    return {"links": [{"from_node": node, "from_socket": output}]}


def _material(shader, name="Slate"):
    output = {
        "type": "OUTPUT_MATERIAL",
        "is_active_output": True,
        "inputs": {"Surface": _link(shader)},
    }
    return {
        "name": name,
        "diffuse_color": (1, 0, 1, 1),
        "use_nodes": True,
        "node_tree": {"nodes": [{"type": "OUTPUT_MATERIAL", "is_active_output": False}, output]},
    }


def _principled():
    return {
        "type": "BSDF_PRINCIPLED",
        "inputs": {
            "Base Color": _link({"type": "RGB"}, {"default_value": (0.024, 0.04, 0.06, 0.75)}),
            "Metallic": _link({"type": "VALUE", "value": 0.6}),
            "Roughness": {"default_value": 0.65},
            "Emission Color": (0.2, 0.1, 0.0, 1),
            "Emission Strength": 3,
        },
    }


def test_principled_material_reads_active_output_and_linked_inputs():
    source = _material(_principled())
    row = exporter.extract_material(source)
    assert row["base_colour"] == pytest.approx(
        {"r": 0.168018287683, "g": 0.220916362548, "b": 0.271699758892, "a": 0.75}
    )
    assert row["metallic"] == 0.6
    assert row["smoothness"] == pytest.approx(0.35)
    assert row["emissive"]
    assert row["emission_colour"] == pytest.approx({"r": 0.6, "g": 0.3, "b": 0, "a": 1})
    assert re.fullmatch("[a-f0-9]{16}", row["material_id"])
    assert row == exporter.extract_material(source)
    source["name"] = "Another slate"
    assert row["material_id"] != exporter.extract_material(source)["material_id"]


def test_emission_mix_shader_blends_in_linear_space_and_scales_hdr_output():
    emission: dict[str, Any] = {
        "type": "EMISSION",
        "inputs": {"Color": (0.5, 0.2, 0.0, 1), "Strength": 150},
    }
    mixed: dict[str, Any] = {
        "type": "MIX_SHADER",
        "inputs": {"Fac": 0.25, "Shader1": _link(_principled()), "Shader2": _link(emission)},
    }
    row = exporter.extract_material(_material(mixed))
    assert row["metallic"] == pytest.approx(0.45)
    assert row["base_colour"]["r"] == pytest.approx(
        exporter.linear_to_srgb(0.024 * 0.75 + 0.5 * 0.25)
    )
    assert row["emission_colour"] == pytest.approx(
        {"r": 2.0, "g": 2.0 * 7.725 / 19.2, "b": 0.0, "a": 1.0}
    )
    mixed["inputs"]["Fac"] = 1.0
    row = exporter.extract_material(_material(mixed))
    assert row["emissive"] is True
    assert row["emission_colour"] == pytest.approx({"r": 2.0, "g": 0.8, "b": 0.0, "a": 1.0})
    assert row["base_colour"]["r"] == pytest.approx(0.735356983052)
    emission["inputs"]["Strength"] = 0
    assert exporter.extract_material(_material(mixed))["emissive"] is False


def test_hdr_emission_caps_violet_peak_without_losing_rgb_ratios():
    emission = {"type": "EMISSION", "inputs": {"Color": (0.28, 0.36, 1.0, 1.0), "Strength": 7}}
    colour = exporter.extract_material(_material(emission))["emission_colour"]
    assert max(colour[c] for c in "rgb") == 2.0
    assert colour["r"] < 1.0 and colour["g"] < 1.0
    assert tuple(colour[c] / colour["b"] for c in "rgb") == pytest.approx((0.28, 0.36, 1.0))


@pytest.mark.parametrize("node_type", ["MIX_RGB", "MIX"])
def test_colour_links_mix_texture_average_with_ramp(node_type):
    image = {"source": "GENERATED", "channels": 4, "pixels": [0.8, 0, 0, 1, 0.2, 0, 0, 1]}
    ramp = {
        "type": "VALTORGB",
        "color_ramp": {"evaluate": lambda fac: (0, fac, 0, 1)},
        "inputs": {"Fac": 0.4},
    }
    mix = {
        "type": node_type,
        "inputs": {
            "Factor": 0.25,
            "A": _link({"type": "TEX_IMAGE", "image": image}),
            "B": _link(ramp),
        },
    }
    shader = {"type": "BSDF_PRINCIPLED", "inputs": {"Base Color": _link(mix)}}
    base = exporter.extract_material(_material(shader))["base_colour"]
    assert base == pytest.approx(
        {"r": exporter.linear_to_srgb(0.375), "g": exporter.linear_to_srgb(0.10), "b": 0, "a": 1}
    )


def _rna_socket(name, socket_type, value=None, *, node=None, output=None, enabled=True):
    return SimpleNamespace(
        name=name,
        type=socket_type,
        enabled=enabled,
        default_value=value,
        links=[] if node is None else [SimpleNamespace(from_node=node, from_socket=output)],
    )


def _rna_material_with_roughness(node, output):
    shader = SimpleNamespace(
        type="BSDF_PRINCIPLED",
        inputs=[_rna_socket("Roughness", "VALUE", 0.5, node=node, output=output)],
    )
    material_output = SimpleNamespace(
        type="OUTPUT_MATERIAL",
        is_active_output=True,
        inputs=[_rna_socket("Surface", "SHADER", node=shader)],
    )
    return SimpleNamespace(
        name="RNA Material", use_nodes=True, node_tree=SimpleNamespace(nodes=[material_output])
    )


def test_float_mix_reads_rna_value_sockets():
    mix = SimpleNamespace(
        type="MIX",
        data_type="FLOAT",
        inputs=[
            _rna_socket("Factor", "VALUE", 0.5),
            _rna_socket("A", "RGBA", (1, 1, 1, 1)),
            _rna_socket("A", "VALUE", 0.3),
            _rna_socket("B", "VALUE", 0.9),
        ],
    )
    material = _rna_material_with_roughness(mix, _rna_socket("Result", "VALUE"))
    assert exporter.extract_material(material)["smoothness"] == pytest.approx(0.4)


def test_colour_ramp_alpha_link_reads_alpha_from_rna_output():
    ramp = SimpleNamespace(
        type="VALTORGB",
        inputs=[_rna_socket("Fac", "VALUE", 0.4)],
        color_ramp=SimpleNamespace(evaluate=lambda factor: (0.1, 0.1, 0.1, 2 * factor)),
    )
    material = _rna_material_with_roughness(ramp, _rna_socket("Alpha", "VALUE"))
    assert exporter.extract_material(material)["smoothness"] == pytest.approx(0.2)


def _texture_material(image):
    input_socket = _link({"type": "TEX_IMAGE", "image": image})
    input_socket["default_value"] = (0.0, 0.0, 0.0, 1.0)
    return _material({"type": "BSDF_PRINCIPLED", "inputs": {"Base Color": input_socket}})


@pytest.mark.parametrize("source", ["SEQUENCE", "MOVIE", "TILED", "FILE", "VIEWER"])
def test_material_never_reads_unsafe_image_pixels(source):
    class Image:
        packed_file = None

        def __init__(self, source: str):
            self.source = source

        @property
        def pixels(self):
            raise AssertionError("must not read pixels")

    image = Image(source)
    material = _texture_material(image)
    black = {"r": 0, "g": 0, "b": 0, "a": 1}
    assert exporter.extract_material(material)["base_colour"] == black
    assert exporter.extract_material(material, verified_images={0})["base_colour"] == black
    if source != "FILE":
        assert (
            exporter.extract_material(material, verified_images={id(image)})["base_colour"] == black
        )


def test_material_samples_packed_data_or_a_verified_static_file():
    packed = SimpleNamespace(source="FILE", packed_file=object(), pixels=[1, 0, 0, 1])
    verified = SimpleNamespace(
        source="FILE", packed_file=None, pixels=[0, 1, 0, 1], as_pointer=lambda: 42
    )
    assert exporter.extract_material(_texture_material(packed))["base_colour"] == {
        "r": 1,
        "g": 0,
        "b": 0,
        "a": 1,
    }
    assert exporter.extract_material(_texture_material(verified), verified_images={42})[
        "base_colour"
    ] == {
        "r": 0,
        "g": 1,
        "b": 0,
        "a": 1,
    }


@pytest.mark.parametrize("source", ["FILE", "SEQUENCE", "MOVIE", "TILED"])
def test_export_validates_every_unpacked_external_image_path(monkeypatch, tmp_path, source):
    root = tmp_path / "sealed"
    root.mkdir()
    source_file = root / "source.blend"
    source_file.touch()
    outside = tmp_path / "external.png"
    outside.touch()
    in_root_link = root / "external.png"
    in_root_link.symlink_to(outside)
    image = SimpleNamespace(
        source=source, packed_file=None, filepath=str(in_root_link), library=None
    )
    bpy = SimpleNamespace(
        data=SimpleNamespace(filepath=str(source_file), libraries=[], images=[image]),
        path=SimpleNamespace(abspath=lambda filepath, library: filepath),
    )
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setattr(
        sys, "argv", ["blender", "--", str(root), str(root / "export.json"), "closure"]
    )
    with pytest.raises(ValueError, match="not in the subpath"):
        exporter.main()


@pytest.mark.parametrize("token", ["<UDIM>", "<udim>"])
def test_tiled_image_validates_concrete_udim_paths_without_authorising_pixels(tmp_path, token):
    (tmp_path / "albedo.1001.png").touch()
    image = SimpleNamespace(
        source="TILED", filepath=f"albedo.{token}.png", tiles=[SimpleNamespace(number=1001)]
    )
    assert (
        exporter.validate_external_images([image], tmp_path, lambda item: tmp_path / item.filepath)
        == set()
    )


@pytest.mark.parametrize("tiles", [[SimpleNamespace(number=1001)], []])
def test_tiled_image_rejects_out_of_root_udim_paths(tmp_path, tiles):
    root = tmp_path / "sealed"
    root.mkdir()
    (tmp_path / "outside.1001.png").touch()
    image = SimpleNamespace(source="TILED", filepath="../outside.<UDIM>.png", tiles=tiles)
    with pytest.raises(ValueError, match="not in the subpath"):
        exporter.validate_external_images([image], root, lambda item: root / item.filepath)


def test_empty_udim_tile_list_checks_only_the_parent(tmp_path):
    (tmp_path / "textures").mkdir()
    image = {"source": "TILED", "filepath": "textures/albedo.<UDIM>.png", "tiles": []}
    assert (
        exporter.validate_external_images(
            [image], tmp_path, lambda item: tmp_path / item["filepath"]
        )
        == set()
    )


def test_material_uses_diffuse_fallback_only_for_unreadable_or_disabled_shader():
    viewport = SimpleNamespace(use_nodes=False, diffuse_color=(0.2, -1, 2, 3), name="Viewport")
    assert exporter.extract_material(viewport)["base_colour"] == pytest.approx(
        {"r": 0.484529204482, "g": 0, "b": 1, "a": 1}
    )
    assert exporter.extract_material(_material({"type": "UNKNOWN"}))["base_colour"] == {
        "r": 1,
        "g": 0,
        "b": 1,
        "a": 1,
    }


@pytest.mark.parametrize(
    ("name", "size", "count", "collider"),
    [
        ("LED trim", (2.0, 0.045, 0.01), 1, "none"),
        ("Bolt", (0.08, 0.04, 0.02), 20, "none"),
        ("Chair back", (0.4, 0.7, 0.05), 280, "box"),
        ("Chair back", (0.4, 0.7, 0.05), 11, "mesh"),
        ("Stage floor", (10, 0.06, 5), 1, "mesh"),
        ("Wall", (8, 4, 0.25), 9, "mesh"),
        ("Stair tread", (1.2, 0.05, 0.3), 20, "mesh"),
        ("Hero plinth", (0.4, 1, 0.2), 12, "mesh"),
    ],
)
def test_collider_policy(name, size, count, collider):
    assert exporter.classify_collider(name, size, count) == collider


def test_geometry_splits_uv_seams_and_preserves_submesh_order_and_winding():
    mesh = SimpleNamespace(
        uv_layers=SimpleNamespace(
            active=SimpleNamespace(
                data=[
                    SimpleNamespace(uv=uv)
                    for uv in [(0, 0), (1, 0), (1, 1), (0.5, 0.5), (1, 1), (0, 1)]
                ]
            )
        ),
        loop_triangles=[
            SimpleNamespace(vertices=(0, 1, 2), loops=(0, 1, 2), material_index=1),
            SimpleNamespace(vertices=(0, 2, 3), loops=(3, 4, 5), material_index=0),
        ],
    )
    points = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]
    geometry = exporter._geometry(mesh, points, ["ring", "centre"], reverse=True)
    assert geometry["vertices"][0] == geometry["vertices"][3]
    assert geometry["uvs"][0] != geometry["uvs"][3]
    assert len(geometry["vertices"]) == len(geometry["uvs"]) == 5
    assert geometry["submeshes"] == [
        {"material_id": "ring", "triangles": [3, 4, 2]},
        {"material_id": "centre", "triangles": [0, 2, 1]},
    ]
    baked_mirror = exporter._geometry(mesh, points, ["ring", "centre"], reverse=False)
    assert baked_mirror["submeshes"][0]["triangles"] == [3, 2, 4]
    assert exporter._hash(geometry) != exporter._hash(baked_mirror)


def test_lights_export_photometric_approximations():
    spot = SimpleNamespace(
        name="Key",
        data=SimpleNamespace(
            type="SPOT",
            energy=2200,
            color=(1, 0.5, 0),
            spot_size=math.pi / 3,
            spot_blend=0.25,
            use_shadow=True,
        ),
    )
    matrix = _matrix(_axis_rotation((0, 0, 1), 0), translation=(1, 4, 2))
    row = exporter._light(spot, matrix, 0.5)
    assert row is not None
    assert row["type"] == "spot" and row["cast_shadows"]
    assert row["intensity"] == pytest.approx(2200 / (12 * math.pi))
    assert row["range"] == 28
    assert row["spot_angle"] == pytest.approx(60)
    assert row["inner_spot_angle"] == pytest.approx(45)
    assert row["position"] == {"x": 0.5, "y": 1, "z": 2}
    for energy, limit in ((0, 0.2), (10000, 120.0)):
        spot.data.energy = energy
        clamped = exporter._light(spot, matrix, 0.5)
        assert clamped is not None
        assert clamped["intensity"] == limit
    spot.data.energy = float("nan")
    assert exporter._light(spot, matrix, 0.5) is None


def test_active_output_prefers_active_node_and_falls_back_to_first_matching_type():
    unrelated = {"type": "OUTPUT_MATERIAL", "is_active_output": True}
    first = {"type": "OUTPUT_WORLD", "is_active_output": False}
    second = {"type": "OUTPUT_WORLD", "is_active_output": True}
    tree = {"nodes": [unrelated, first, second]}
    assert exporter._active_output(tree, "OUTPUT_WORLD") is second
    second["is_active_output"] = False
    assert exporter._active_output(tree, "OUTPUT_WORLD") is first
    assert exporter._active_output(tree, "NOT_AN_OUTPUT") is None


def test_world_attenuates_in_linear_space_and_uses_an_srgb_ambient_floor():
    background: dict[str, Any] = {
        "type": "BACKGROUND",
        "inputs": {"Color": (0.5, 0.025, -0.5, 1), "Strength": 0.1},
    }
    world = {
        "use_nodes": True,
        "node_tree": {
            "nodes": [
                {
                    "type": "OUTPUT_WORLD",
                    "is_active_output": True,
                    "inputs": {"Surface": _link(background)},
                }
            ]
        },
    }
    interior = exporter._environment(world, False)
    assert interior["world_strength"] == 0.1
    assert interior["use_skybox"] is False
    assert interior["world_colour"] == pytest.approx(
        {"r": 0.24780052799, "g": 0.0323, "b": 0, "a": 1}
    )
    assert interior["ambient_colour"] == {"r": 0.55, "g": 0.58, "b": 0.66, "a": 1}
    dark = exporter._environment(None, True)
    assert dark["ambient_colour"] == interior["ambient_colour"]
    assert dark["world_colour"] == {"r": 0, "g": 0, "b": 0, "a": 1}
    warm = exporter._environment({"color": (0.5, 0.025, 0)}, True)
    assert warm["ambient_colour"] == pytest.approx(
        {"r": 0.735356983052, "g": 0.58, "b": 0.66, "a": 1}
    )


@pytest.mark.parametrize(
    ("linear", "has_lights", "use_skybox"),
    [
        (0.149, False, False),
        (0.151, False, True),
        (0.151, True, False),
    ],
)
def test_skybox_requires_enough_linear_world_light_and_no_scene_lights(
    linear, has_lights, use_skybox
):
    assert exporter._environment({"color": (linear,) * 3}, has_lights)["use_skybox"] is use_skybox


def test_export_deduplicates_evaluated_instances_and_bakes_shear(monkeypatch, tmp_path):
    class Matrix(tuple):
        def __matmul__(self, point):
            return _product(self, (*point, 1))[:3]

        def determinant(self):
            return self[0][0] * self[1][1] * self[2][2]  # All fixture matrices are triangular.

    identity = _axis_rotation((0, 0, 1), 0)
    cleared = []
    texture_file = tmp_path / "texture.png"
    texture_file.touch()
    image = SimpleNamespace(
        source="FILE",
        packed_file=None,
        filepath=str(texture_file),
        library=None,
        as_pointer=lambda: 42,
        pixels=[0, 1, 0, 1],
    )
    material = SimpleNamespace(**_texture_material(image), as_pointer=lambda: 43)

    def mesh_instance(identifier, matrix, height=1):
        mesh = SimpleNamespace(
            vertices=[SimpleNamespace(co=co) for co in ((0, 0, 0), (1, 0, 0), (0, 1, height))],
            loop_triangles=[SimpleNamespace(vertices=(0, 1, 2), loops=(0, 1, 2), material_index=0)],
            uv_layers=SimpleNamespace(active=None),
            calc_loop_triangles=lambda: None,
        )
        obj = SimpleNamespace(
            type="MESH",
            name="Chair " + identifier,
            material_slots=[SimpleNamespace(material=material)],
            original={"proscenium_object_id": "chair", "proscenium_instance_id": identifier},
            to_mesh=lambda: mesh,
            to_mesh_clear=lambda: cleared.append(identifier),
        )
        return SimpleNamespace(object=obj, matrix_world=Matrix(matrix))

    repeated = [
        mesh_instance(f"chair{i:02}", _matrix(identity, translation=(i, 0, 0))) for i in range(12)
    ]
    mirrored = mesh_instance("mirror", _matrix(identity, (-1, 1, 1)))
    divergent = mesh_instance("divergent", _matrix(identity), height=1.1)
    shear = ((-1, 0.3, 0, 4), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
    baked = [mesh_instance(identifier, shear) for identifier in ("baked1", "baked2")]
    hidden = SimpleNamespace(object=SimpleNamespace(type="LIGHT", hide_render=True))
    scene_file = tmp_path / "source.blend"
    scene_file.touch()
    graph = SimpleNamespace(
        object_instances=[hidden, divergent, *baked, mirrored, *reversed(repeated)]
    )
    scene = SimpleNamespace(unit_settings=SimpleNamespace(scale_length=0.5), world=None)
    bpy = SimpleNamespace(
        data=SimpleNamespace(filepath=str(scene_file), libraries=[], images=[image]),
        context=SimpleNamespace(scene=scene, evaluated_depsgraph_get=lambda: graph),
        app=SimpleNamespace(version_string="4.5.16 LTS"),
        path=SimpleNamespace(abspath=lambda filepath, library: filepath),
    )
    export_path = tmp_path / "export.json"
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setattr(sys, "argv", ["blender", "--", str(tmp_path), str(export_path), "closure"])
    exporter.main()
    expected = export_path.read_bytes()
    exporter.main()
    assert export_path.read_bytes() == expected
    result = json.loads(expected)
    assert expected == json.dumps(result, separators=(",", ":")).encode()
    assert (result["format"], result["closure_digest"], result["units"], result["axes"]) == (
        "proscenium-triangles-1",
        "closure",
        "metres",
        "XZY",
    )
    objects = {row["instance_id"]: row for row in result["objects"]}
    assert list(objects) == sorted(objects)
    assert len(cleared) == 2 * len(objects)
    assert not result["lights"]
    assert len(result["materials"]) == 1
    assert result["materials"][0]["base_colour"] == {"r": 0, "g": 1, "b": 0, "a": 1}
    assert len(result["shared_meshes"]) == 4
    meshes = {row["mesh_key"]: row for row in result["shared_meshes"]}
    mesh_key = objects["mirror"]["mesh_key"]
    assert objects["mirror"]["scale"] == {"x": -1, "y": 1, "z": 1}
    assert objects["divergent"]["mesh_key"] != mesh_key
    assert meshes[mesh_key]["submeshes"][0]["triangles"] == [0, 2, 1]
    assert meshes[mesh_key]["local_bounds_size"] == {"x": 0.5, "y": 0.5, "z": 0.5}
    for row in (objects[f"chair{i:02}"] for i in range(12)):
        assert row["mesh_key"] == mesh_key
        assert row["collider"] == "box"
    assert objects["baked1"]["mesh_key"] != objects["baked2"]["mesh_key"]
    for row in (objects["baked1"], objects["baked2"]):
        assert row["has_transform"] is True
        assert row["position"] == {"x": 0, "y": 0, "z": 0}
        assert row["rotation"] == {"x": 0, "y": 0, "z": 0, "w": 1}
        assert row["scale"] == {"x": 1, "y": 1, "z": 1}
        mesh = meshes[row["mesh_key"]]
        assert mesh["vertices"][0] == {"x": 2, "y": 0, "z": 0}
        assert mesh["submeshes"][0]["triangles"] == [0, 1, 2]


def _unity_workflow_source():
    return (workflow.HERE / "Editor/UnityWorkflow.cs").read_text()


def test_prepare_installs_and_tracks_pause_runtime_scripts(tmp_path):
    project = "unity/gallery"
    destination = workflow.prepare(tmp_path, project)
    tool_root = destination / "Assets/Proscenium"
    inventory = tool_root / "tool-install.json"
    installed = json.loads(inventory.read_text())
    for name in ("Runtime/PauseMenu.cs", "Runtime/WalkthroughPlayer.cs"):
        script = tool_root / name
        assert script.read_bytes() == (workflow.HERE / name).read_bytes()
        assert installed[name] == workflow.sha256(script)

    meta = tool_root / "Runtime/PauseMenu.cs.meta"
    meta.write_text("guid: existing-unity-guid\n")
    workflow.prepare(tmp_path, project)
    assert json.loads(inventory.read_text()) == installed
    assert meta.read_text() == "guid: existing-unity-guid\n"


def test_bootstrap_declares_pause_audio_and_video_modules(tmp_path):
    destination = workflow.bootstrap_project(tmp_path, "unity/gallery")
    dependencies = json.loads((destination / "Packages/manifest.json").read_text())["dependencies"]
    for module in ("imgui", "audio", "video"):
        assert dependencies[f"com.unity.modules.{module}"] == "1.0.0"


def test_walkthrough_releases_cursor_and_restores_time_on_pause_exit():
    source = (workflow.HERE / "Runtime/WalkthroughPlayer.cs").read_text()
    assert re.search(
        r"void ReleaseCursor\(\)\s*\{\s*Cursor\.lockState = CursorLockMode\.None;\s*"
        r"Cursor\.visible = true;",
        source,
    )
    assert re.search(
        r"void CaptureCursor\(\)\s*\{\s*Cursor\.lockState = CursorLockMode\.Locked;\s*"
        r"Cursor\.visible = false;",
        source,
    )
    for pattern in (
        r"if \(pauseMenuEnabled\) SetPaused\(!IsPaused\);\s*else ReleaseCursor\(\);",
        r"if \(!hasFocus\) ReleaseCursor\(\);",
        r"OnDisable\(\) => ReleasePause\(\);",
        r"OnDestroy\(\) => ReleasePause\(\);",
        r"RestoreTimeScale\(\);\s*ReleaseCursor\(\);",
        r"previousTimeScale = Time\.timeScale;\s*Time\.timeScale = 0f;",
        r"Time\.timeScale = previousTimeScale;",
        r"LastPolledReset = false;\s*ReleaseCursor\(\);\s*return;",
        r"if \(IsPaused\) return;\s*if \(reset",
    ):
        assert re.search(pattern, source)


def test_pause_menu_actions_wired_on_import_without_tightening_validation():
    menu = (workflow.HERE / "Runtime/PauseMenu.cs").read_text()
    for label, handler in (
        ("Resume", "Resume"),
        ("Reset Position", "ResetAndResume"),
        ("Quit", "Quit"),
    ):
        assert f'GUI.Button(button, "{label}", buttonStyle)) {handler}();' in menu
    assert "public void Resume() => player?.SetPaused(false);" in menu
    assert re.search(r"player\?\.Respawn\(\);\s*player\?\.SetPaused\(false\);", menu)
    assert re.search(
        r"QuitRequested = true;\s*player\?\.SetPaused\(false\);\s*"
        r"Cursor\.lockState = CursorLockMode\.None;\s*Cursor\.visible = true;",
        menu,
    )
    assert "if (!IsVisible) return;" in menu
    assert '"WASD Move  ·  Mouse Look  ·  R Reset  ·  Esc Toggle Pause"' in menu
    hint_style = menu.split("hintStyle = new GUIStyle", 1)[1].split("buttonStyle =", 1)[0]
    assert "fontSize = 14" in hint_style
    assert "wordWrap = false" in hint_style
    assert "textColor = new Color(0.86f, 0.90f, 0.95f)" in hint_style

    source = _unity_workflow_source()
    importer = source.split("public static void Import()", 1)[1].split(
        "private static void ImportInlineMesh(", 1
    )[0]
    assert re.search(
        r"var movement = player\.AddComponent<WalkthroughPlayer>\(\);\s*"
        r"var pauseMenu = player\.AddComponent<PauseMenu>\(\);\s*pauseMenu\.player = movement;",
        importer,
    )
    validation = source.split("public static void Validate()", 1)[1].split(
        "public static void ImportRepeatProof()", 1
    )[0]
    assert "GetComponentsInChildren<WalkthroughPlayer>().Length != 1" in validation
    assert "PauseMenu" not in validation


def test_pause_playmode_test_uses_production_input_and_restores_settings():
    source = (workflow.HERE / "Tests/PlayableTests.cs").read_text()
    test_name = "public IEnumerator F06_EscapePauseMenu_TogglesPauseCursorAndMovement()"
    assert re.search(rf"\[UnityTest\]\s*{re.escape(test_name)}", source)
    pause_test = source.split(test_name, 1)[1]
    assert re.search(
        r"mouse\.MakeCurrent\(\);\s*(?://[^\n]*\n\s*)?yield return null;\s*Send\(Key\.Escape\);",
        pause_test,
    )
    for snippet in (
        "Send(Key.Escape);",
        "Send(Key.W, new Vector2",
        "AssertPoll(Vector2.zero, Vector2.zero);",
        "pauseMenu.Resume();",
        "pauseMenu.ResetAndResume();",
        "pauseMenu.suppressQuitForTests = true;",
        "pauseMenu.Quit();",
        "Time.timeScale = 0f;",
        "player.enabled = false;",
        "player.enabled = true;",
        "Is.EqualTo(initialTimeScale)",
    ):
        assert snippet in pause_test
    assert re.search(
        r"Send\(\);\s*yield return null;\s*Send\(Key\.Escape\);\s*yield return null;\s*"
        r"Assert\.That\(player\.IsPaused, Is\.False\);\s*"
        r"Assert\.That\(pauseMenu\.IsVisible, Is\.False\);",
        pause_test,
    )
    cleanup = pause_test.split("finally", 1)[1]
    for restoration in (
        "settings.updateMode = previousMode;",
        "settings.backgroundBehavior = previousBackground;",
        "settings.editorInputBehaviorInPlayMode = previousEditorInput;",
        "Time.captureDeltaTime = previousCaptureDelta;",
        "Time.timeScale = previousTimeScale;",
    ):
        assert restoration in cleanup


def test_unity_workflow_has_shared_and_legacy_import_paths():
    source = _unity_workflow_source()
    for dto in (
        "Export",
        "MeshRow",
        "MaterialRow",
        "SubmeshRow",
        "SharedMeshRow",
        "LightRow",
        "EnvironmentRow",
    ):
        assert re.search(rf"\[Serializable\]\s+public sealed class {dto}\b", source)
    for snippet in (
        'material.EnableKeyword("_EMISSION")',
        'material.DisableKeyword("_EMISSION")',
        "AddComponent<AudioListener>()",
        "LightShadows.Soft",
        '"m_AdditionalLightShadowsSupported"',
        '"m_AnyShadowsSupported"',
        '"m_AdditionalLightsPerObjectLimit"',
        'rendererSettings.FindProperty("m_RenderingMode")',
        "renderingMode.intValue = 2",
        'rendererSettings.FindProperty("postProcessData")',
        "postProcessData.objectReferenceValue == null",
        'GetMethod("GetDefaultPostProcessData"',
        "postProcessData.objectReferenceValue = defaultData",
        "GetComponentsInChildren<Collider>().Any(c => !(c is CharacterController))",
        "mesh.SetTriangles(sm.submeshes[s].triangles, s",
        "export.shared_meshes == null || export.shared_meshes.Length == 0",
        "if (legacy) ImportInlineMesh",
        "mesh.vertices = row.vertices;",
        "mesh.triangles = row.triangles;",
        "material.color = row.colour;",
        "Physics.CheckCapsule",
        "Physics.Raycast",
    ):
        assert snippet in source
    assert source.index("bool legacy = ValidateExport(export);") < source.index(
        "EditorSceneManager.OpenScene("
    )


def test_unity_urp_configuration_checks_ownership_before_writing_asset_properties():
    source = _unity_workflow_source()
    assert (
        'AssetDatabase.GetAssetPath(asset).StartsWith(directory + "/", StringComparison.Ordinal)'
        in source
    )
    enable_lighting = source.split("private static void EnableUrpLighting(", 1)[1].split(
        "private static void AddUrpVolume(", 1
    )[0]
    assert enable_lighting.index("!IsGeneratedAsset(asset, directory)") < enable_lighting.index(
        "new SerializedObject(asset)"
    )
    assert enable_lighting.index("!IsGeneratedAsset(renderer, directory)") < enable_lighting.index(
        "new SerializedObject(renderer)"
    )
    configure_pipeline = source.split("private static void ConfigurePipeline(", 1)[1].split(
        "public static void Validate()", 1
    )[0]
    assert (
        configure_pipeline.index("AssetDatabase.CreateAsset(asset, path)")
        < configure_pipeline.index("!IsGeneratedAsset(asset, directory)")
        < configure_pipeline.index("new SerializedObject(asset)")
    )
    assert 'directory + "/LightingVolume.asset"' in source
    assert "AddUrpVolume(root, camera, directory);" in source


def test_unity_uses_linear_colour_only_for_generated_urp_and_neutral_tonemapping():
    source = _unity_workflow_source()
    volume, pipeline = source.split("private static void AddUrpVolume(", 1)[1].split(
        "private static void ConfigurePipeline(", 1
    )
    pipeline = pipeline.split("public static void Validate()", 1)[0]
    assert source.count("PlayerSettings.colorSpace = ColorSpace.Linear;") == 1
    assert re.search(
        r"if \(assetType\.IsInstanceOfType\(asset\) && IsGeneratedAsset\(asset, directory\)\s*"
        r"&& PlayerSettings\.colorSpace != ColorSpace\.Linear\)\s*"
        r"PlayerSettings\.colorSpace = ColorSpace\.Linear;",
        pipeline,
    )
    assert re.search(
        r'SetParameter\(tonemapping, "mode", p => \{\s*'
        r'int neutral = Array\.IndexOf\(p\.enumNames, "Neutral"\);\s*'
        r"if \(neutral >= 0\) p\.enumValueIndex = neutral;",
        volume,
    )


def test_unity_validates_optional_uvs_and_legacy_colour_before_importing():
    source = _unity_workflow_source()
    validation = source.split("private static bool ValidateExport(", 1)[1].split(
        "public static void Import()", 1
    )[0]
    assert (
        "sm.uvs != null && sm.uvs.Length != 0 && (sm.uvs.Length != sm.vertices.Length" in validation
    )
    assert "|| sm.uvs.Any(uv => !float.IsFinite(uv.x) || !float.IsFinite(uv.y))" in validation
    assert "|| !Finite(row.colour)" in validation
    assert source.index("ValidateExport(export)") < source.index(
        "ConfigurePipeline(recipe, directory)"
    )


def test_unity_scene_import_restores_skybox_mode_and_avoids_duplicate_audio():
    source = _unity_workflow_source()
    assert re.search(
        r"if \(!environment\.use_skybox\)\s*\{\s*"
        r"var baseAmbient = environment\.ambient_colour;\s*"
        r"RenderSettings\.ambientMode = AmbientMode\.Trilight;\s*"
        r"RenderSettings\.ambientSkyColor = new Color\(baseAmbient\.r, baseAmbient\.g, "
        r"baseAmbient\.b, 1f\);\s*"
        r"RenderSettings\.ambientEquatorColor = new Color\(baseAmbient\.r \* 0\.85f, "
        r"baseAmbient\.g \* 0\.85f,\s*baseAmbient\.b \* 0\.85f, 1f\);\s*"
        r"RenderSettings\.ambientGroundColor = new Color\(baseAmbient\.r \* 0\.45f, "
        r"baseAmbient\.g \* 0\.45f,\s*baseAmbient\.b \* 0\.45f, 1f\);\s*"
        r"RenderSettings\.defaultReflectionMode = DefaultReflectionMode\.Custom;\s*"
        r"RenderSettings\.reflectionIntensity = 0f;\s*"
        r"camera\.clearFlags = CameraClearFlags\.SolidColor;",
        source,
    )
    assert re.search(
        r"else\s*\{\s*RenderSettings\.ambientMode = AmbientMode\.Skybox;\s*"
        r"RenderSettings\.defaultReflectionMode = DefaultReflectionMode\.Skybox;\s*"
        r"RenderSettings\.reflectionIntensity = 1f;\s*"
        r"camera\.clearFlags = CameraClearFlags\.Skybox;",
        source,
    )
    assert not re.search(r"RenderSettings\.skybox\s*=", source)
    assert (
        source.index("foreach (var old in previous) UnityEngine.Object.DestroyImmediate(old);")
        < source.index(
            "!UnityEngine.Object.FindObjectsByType<AudioListener>(FindObjectsSortMode.None).Any(l => l.enabled)"
        )
        < source.index("cameraObject.AddComponent<AudioListener>()")
    )


def test_unity_caps_active_lights_and_distributes_non_primary_lights():
    source = _unity_workflow_source()
    assert "const int MaxActiveLights = 32;" in source
    assert "const int PrimaryLightCount = 12;" in source
    selector = source.split("private static HashSet<LightRow> SelectActiveLights(", 1)[1].split(
        "private static void ImportLighting(", 1
    )[0]
    assert "if (lights.Length <= MaxActiveLights) return new HashSet<LightRow>(lights);" in selector
    assert re.search(
        r"lights\.OrderByDescending\(l => l\.intensity\)\s*"
        r"\.ThenBy\(l => l\.name, StringComparer\.Ordinal\)",
        selector,
    )
    assert re.search(
        r'ranked\.Where\(l => l\.type == "directional"\)\s*'
        r'\.Concat\(ranked\.Where\(l => l\.type != "directional"\)\.Take\(PrimaryLightCount\)\)\s*'
        r"\.Take\(MaxActiveLights\)",
        selector,
    )
    assert "while (active.Count < MaxActiveLights)" in selector
    assert re.search(
        r"ranked\.Where\(l => !active\.Contains\(l\)\)\s*"
        r"\.OrderByDescending\(l => active\.Min\(selected => "
        r"\(l\.position - selected\.position\)\.sqrMagnitude\)\)\s*\.First\(\)",
        selector,
    )
    assert "var active = SelectActiveLights(export.lights);" in source
    assert "light.enabled = active.Contains(row);" in source


def test_unity_prioritises_primary_lights_and_budgets_non_point_shadows():
    source = _unity_workflow_source()
    primary = source.split("var primary =", 1)[1].split(";", 1)[0]
    assert re.search(
        r"Where\(active\.Contains\)\s*"
        r'\.OrderByDescending\(l => l\.type == "directional"\)\s*'
        r"\.ThenByDescending\(l => l\.intensity\)\s*"
        r'\.ThenByDescending\(l => l\.type == "spot"\)\s*'
        r"\.ThenBy\(l => l\.name, StringComparer\.Ordinal\)\s*\.Take\(PrimaryLightCount\)",
        primary,
    )
    assert "if (primary.Contains(row)) light.renderMode = LightRenderMode.ForcePixel;" in source
    shadowed = source.split("var shadowed =", 1)[1].split(";", 1)[0]
    assert re.search(
        r'Where\(l => active\.Contains\(l\) && l\.cast_shadows && l\.type != "point"\)\s*'
        r'\.OrderByDescending\(l => l\.type == "directional"\)\s*'
        r'\.ThenByDescending\(l => l\.type == "spot"\)\s*'
        r"\.ThenByDescending\(l => l\.intensity\)\s*"
        r"\.ThenBy\(l => l\.name, StringComparer\.Ordinal\)\.Take\(5\)",
        shadowed,
    )
    assert (
        "light.shadows = shadowed.Contains(row) ? LightShadows.Soft : LightShadows.None;" in source
    )


def test_repeat_import_reenumerates_paths_and_compares_both_paths_and_guids():
    source = _unity_workflow_source()
    proof = source.split("public static void ImportRepeatProof()", 1)[1].split(
        "public static void Build()", 1
    )[0]
    assert re.search(
        r"var beforePaths = AssetPaths\(\);.*?Import\(\);\s*var afterPaths = AssetPaths\(\);",
        proof,
        re.DOTALL,
    )
    assert (
        "!beforePaths.SequenceEqual(afterPaths) || !beforeGuids.SequenceEqual(afterGuids)" in proof
    )
    assert "afterPaths.Select(AssetDatabase.AssetPathToGUID)" in proof
