"""Trusted Blender script: evaluated triangle meshes in metres, Blender XYZ -> Unity XZY.

Invoke with --background --factory-startup --disable-autoexec --threads 4, opening a
sealed SourceService materialisation, then --python this-file -- ROOT OUTPUT DIGEST.
No source saves, texture copies, add-ons or game assets.
"""

import hashlib
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Vector = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]
RGBA = tuple[float, float, float, float]
Matrix = Sequence[Sequence[float]]
AXES = (0, 2, 1)
DEFAULT_COLOUR: RGBA = (0.55, 0.6, 0.65, 1.0)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value)) if not math.isnan(value) else low


def linear_to_srgb(channel: float) -> float:
    c = clamp(channel)
    if c == 1.0:
        return c
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vector, b: Vector) -> Vector:
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def _unit_vector(v: Vector) -> Vector | None:
    length = math.hypot(*v)
    if not math.isfinite(length) or length <= 1e-12:
        return None
    return v[0] / length, v[1] / length, v[2] / length


def _quaternion(x: Vector, y: Vector, z: Vector) -> Quaternion:
    # The basis vectors are the columns of the rotation matrix.
    trace = x[0] + y[1] + z[2]
    if trace > 0:
        s = 2 * math.sqrt(1 + trace)
        q = ((y[2] - z[1]) / s, (z[0] - x[2]) / s, (x[1] - y[0]) / s, s / 4)
    elif x[0] >= max(y[1], z[2]):
        s = 2 * math.sqrt(1 + x[0] - y[1] - z[2])
        q = (s / 4, (y[0] + x[1]) / s, (z[0] + x[2]) / s, (y[2] - z[1]) / s)
    elif y[1] >= z[2]:
        s = 2 * math.sqrt(1 + y[1] - x[0] - z[2])
        q = ((y[0] + x[1]) / s, s / 4, (z[1] + y[2]) / s, (z[0] - x[2]) / s)
    else:
        s = 2 * math.sqrt(1 + z[2] - x[0] - y[1])
        q = ((z[0] + x[2]) / s, (z[1] + y[2]) / s, s / 4, (x[1] - y[0]) / s)
    length = math.hypot(*q)
    return q[0] / length, q[1] / length, q[2] / length, q[3] / length


def _xzy(point: Sequence[float], unit: float) -> Vector:
    return point[0] * unit, point[2] * unit, point[1] * unit


def blender_matrix_to_unity_trs(
    matrix_4x4: Matrix, unit: float
) -> tuple[Vector, Quaternion, Vector] | None:
    """Decompose S M S, putting any reflection in the X scale, not in the quaternion."""
    m = matrix_4x4
    columns: tuple[Vector, ...] = tuple((m[0][j], m[2][j], m[1][j]) for j in AXES)
    sx, sy, sz = (math.hypot(*column) for column in columns)
    translation = _xzy((m[0][3], m[1][3], m[2][3]), unit)
    if not all(math.isfinite(s) and s > 1e-6 for s in (sx, sy, sz)) or not all(
        math.isfinite(v) for v in translation
    ):
        return None
    x, y, z = (_unit_vector(column) for column in columns)
    if x is None or y is None or z is None:
        return None
    if max(abs(_dot(x, y)), abs(_dot(x, z)), abs(_dot(y, z))) >= 1e-4:
        return None
    if _dot(x, _cross(y, z)) < 0:
        x = (-x[0], -x[1], -x[2])
        sx = -sx
    return translation, _quaternion(x, y, z), (sx, sy, sz)


def blender_light_rotation_to_unity(matrix_world: Matrix) -> Quaternion | None:
    """Map Blender's local -Z to Unity's +Z, orthogonalising the light's up vector."""
    m = matrix_world
    forward = _unit_vector((-m[0][2], -m[2][2], -m[1][2]))
    up = _unit_vector((m[0][1], m[2][1], m[1][1]))
    if forward is None or up is None:
        return None
    right = _unit_vector(_cross(up, forward))
    return None if right is None else _quaternion(right, _cross(forward, right), forward)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def _socket(node: Any, *names: str, socket_type: str | None = None) -> Any:
    inputs = _get(node, "inputs", {})
    for name in names:
        if isinstance(inputs, Mapping):
            if name in inputs:
                return inputs[name]
        else:
            for socket in inputs:
                if (
                    socket.name == name
                    and _get(socket, "enabled", True)
                    and (socket_type is None or _get(socket, "type") == socket_type)
                ):
                    return socket
    return None


def _link(socket: Any) -> tuple[Any, Any]:
    for link in _get(socket, "links", ()):
        if _get(link, "is_valid", True) and not _get(link, "is_muted", False):
            return _get(link, "from_node"), _get(link, "from_socket")
    return None, None


def _scalar(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else default
    try:
        return _luminance(_rgba(value, (default, default, default, 1.0)))
    except (TypeError, ValueError, OverflowError):
        return default


def _rgba(value: Any, default: RGBA = DEFAULT_COLOUR) -> RGBA:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        v = float(value) if math.isfinite(value) else 0.0
        return v, v, v, 1.0
    try:
        r, g, b, *alpha = (float(v) for v in value)
        return (
            r if math.isfinite(r) else 0.0,
            g if math.isfinite(g) else 0.0,
            b if math.isfinite(b) else 0.0,
            alpha[0] if alpha and math.isfinite(alpha[0]) else 1.0,
        )
    except (TypeError, ValueError, OverflowError):
        return default


def _luminance(rgb: Sequence[float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _image_identity(image: Any) -> int:
    as_pointer = _get(image, "as_pointer")
    return int(as_pointer()) if callable(as_pointer) else id(image)


def validate_external_images(
    images: Iterable[Any], root: Path, resolve_filepath: Callable[[Any], str | Path]
) -> set[int]:
    """Check external image paths and authorise pixel sampling only for static files."""
    verified_images = set()
    udim = re.compile(r"<UDIM>", re.IGNORECASE)
    for image in images:
        source = _get(image, "source")
        if _get(image, "packed_file") or source in {"GENERATED", "VIEWER"}:
            continue
        raw_path = str(resolve_filepath(image))
        if source == "TILED" and udim.search(raw_path):
            tiles = tuple(_get(image, "tiles", ()))
            if not tiles:
                Path(raw_path).parent.resolve(strict=True).relative_to(root)
            for tile in tiles:
                path = Path(udim.sub(str(int(_get(tile, "number"))), raw_path))
                path.resolve(strict=True).relative_to(root)
        else:
            Path(raw_path).resolve(strict=True).relative_to(root)
            if source == "FILE":
                verified_images.add(_image_identity(image))
    return verified_images


def _image_average(image: Any, *, verified_external: bool = False) -> RGBA | None:
    source = _get(image, "source")
    if _get(image, "packed_file") is None and not (
        source == "GENERATED" or (source == "FILE" and verified_external)
    ):
        # Reading pixels may lazily open the file or resolve a sequence or tile.
        return None
    try:
        pixels = _get(image, "pixels", ())
        channels = _get(image, "channels", 4)
        if channels not in (3, 4):
            return None
        count = len(pixels) // channels
        if not count:
            return None
        # Stratified, bounded sampling. Do not copy the texture into a Python list.
        samples = min(count, 1024)
        totals = [0.0, 0.0, 0.0, 0.0]
        srgb_bytes = not _get(image, "is_float", False) and (
            _get(_get(image, "colorspace_settings"), "name") == "sRGB"
        )
        for i in range(samples):
            offset = ((2 * i + 1) * count // (2 * samples)) * channels
            colour = _rgba(pixels[offset : offset + channels])
            for channel, value in enumerate(colour):
                if srgb_bytes and channel < 3:
                    value = value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                totals[channel] += value / samples
        return totals[0], totals[1], totals[2], totals[3]
    except (RuntimeError, ValueError, IndexError, TypeError):
        return None


def _blend(a: float, b: float, factor: float) -> float:
    return a * (1 - factor) + b * factor


def _blend_rgba(a: RGBA, b: RGBA, factor: float) -> RGBA:
    return (
        _blend(a[0], b[0], factor),
        _blend(a[1], b[1], factor),
        _blend(a[2], b[2], factor),
        _blend(a[3], b[3], factor),
    )


def _value(socket: Any, verified_images: set[int] | None, depth: int = 0) -> Any:
    default = _get(socket, "default_value", socket)
    if depth >= 24:
        return default
    node, output = _link(socket)
    kind = _get(node, "type")
    if kind in ("RGB", "VALUE"):
        return (
            _get(output, "default_value", output)
            if output is not None
            else _get(node, "value", default)
        )
    if kind == "REROUTE":
        return _value(_socket(node, "Input"), verified_images, depth + 1)
    if kind == "TEX_IMAGE":
        image = _get(node, "image")
        average = _image_average(
            image,
            verified_external=verified_images is not None
            and _image_identity(image) in verified_images,
        )
        return average[3] if average and _get(output, "name") == "Alpha" else average or default
    if kind == "VALTORGB":
        factor = clamp(_scalar(_value(_socket(node, "Fac"), verified_images, depth + 1), 0.5))
        evaluate = _get(_get(node, "color_ramp"), "evaluate")
        if not callable(evaluate):
            return default
        result = evaluate(factor)
        return _rgba(result)[3] if _get(output, "name") == "Alpha" else result
    if kind in ("MIX", "MIX_RGB"):
        data_type = _get(node, "data_type", "RGBA")
        mix_socket_type = "VALUE" if data_type == "FLOAT" else data_type
        socket_type = mix_socket_type if kind == "MIX" else None
        factor = clamp(
            _scalar(_value(_socket(node, "Fac", "Factor"), verified_images, depth + 1), 0.5)
        )
        a = _value(
            _socket(node, "A", "Color1", socket_type=socket_type),
            verified_images,
            depth + 1,
        )
        b = _value(
            _socket(node, "B", "Color2", socket_type=socket_type),
            verified_images,
            depth + 1,
        )
        if data_type == "FLOAT":
            return _blend(_scalar(a, 0.0), _scalar(b, 0.0), factor)
        a = _rgba(a)
        b = _rgba(b)
        mode = _get(node, "blend_type", "MIX")
        if mode == "MULTIPLY":
            b = (a[0] * b[0], a[1] * b[1], a[2] * b[2], b[3])
        elif mode == "ADD":
            b = (a[0] + b[0], a[1] + b[1], a[2] + b[2], b[3])
        elif mode == "SUBTRACT":
            b = (a[0] - b[0], a[1] - b[1], a[2] - b[2], b[3])
        return _blend_rgba(a, b, factor)
    return default


def _active_output(tree: Any, kind: str) -> Any:
    outputs = [node for node in _get(tree, "nodes", ()) if _get(node, "type") == kind]
    return next(
        (node for node in outputs if _get(node, "is_active_output", False)),
        outputs[0] if outputs else None,
    )


@dataclass(frozen=True)
class _Surface:
    base: RGBA
    metallic: float = 0.0
    roughness: float = 0.5
    emission: RGBA = (0.0, 0.0, 0.0, 1.0)
    emissive: bool = False


def _surface(socket: Any, verified_images: set[int] | None, depth: int = 0) -> _Surface | None:
    if depth >= 24:
        return None
    node, _ = _link(socket)
    kind = _get(node, "type")
    if kind == "REROUTE":
        return _surface(_socket(node, "Input"), verified_images, depth + 1)
    if kind in ("BSDF_PRINCIPLED", "EMISSION"):
        pure_emission = kind == "EMISSION"
        base = _rgba(
            _value(_socket(node, "Color" if pure_emission else "Base Color"), verified_images)
        )
        colour = (
            base
            if pure_emission
            else _rgba(
                _value(_socket(node, "Emission Color", "Emission"), verified_images),
                (0.0, 0.0, 0.0, 1.0),
            )
        )
        strength = max(
            0.0,
            _scalar(
                _value(
                    _socket(node, "Strength" if pure_emission else "Emission Strength"),
                    verified_images,
                ),
                1.0,
            ),
        )
        red, green, blue, _ = colour
        emission = (
            max(0.0, red) * strength,
            max(0.0, green) * strength,
            max(0.0, blue) * strength,
            1.0,
        )
        return _Surface(
            base,
            clamp(_scalar(_value(_socket(node, "Metallic"), verified_images), 0.0)),
            clamp(_scalar(_value(_socket(node, "Roughness"), verified_images), 0.5)),
            emission,
            strength > 0 if pure_emission else any(emission[:3]),
        )
    if kind == "MIX_SHADER":
        inputs = _get(node, "inputs", ())
        slots = (
            (inputs.get("Shader1"), inputs.get("Shader2"))
            if isinstance(inputs, Mapping)
            else (inputs[1], inputs[2])
        )
        factor = clamp(_scalar(_value(_socket(node, "Fac"), verified_images), 0.5))
        if factor in (0.0, 1.0):
            return _surface(slots[int(factor)], verified_images, depth + 1)
        a, b = (_surface(slot, verified_images, depth + 1) for slot in slots)
        if a is None or b is None:
            return a or b
        return _Surface(
            _blend_rgba(a.base, b.base, factor),
            _blend(a.metallic, b.metallic, factor),
            _blend(a.roughness, b.roughness, factor),
            _blend_rgba(a.emission, b.emission, factor),
            a.emissive or b.emissive,
        )
    return None


def _colour(colour: RGBA, *, srgb: bool = True) -> dict[str, float]:
    r, g, b, a = colour
    if srgb:
        r, g, b = linear_to_srgb(r), linear_to_srgb(g), linear_to_srgb(b)
    else:
        r, g, b = clamp(r, 0.0, 64.0), clamp(g, 0.0, 64.0), clamp(b, 0.0, 64.0)
    return {"r": r, "g": g, "b": b, "a": clamp(a)}


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def extract_material(material: Any, *, verified_images: set[int] | None = None) -> dict[str, Any]:
    output = None
    if _get(material, "use_nodes", False):
        output = _active_output(_get(material, "node_tree"), "OUTPUT_MATERIAL")
    surface = _surface(_socket(output, "Surface"), verified_images) or _Surface(
        _rgba(_get(material, "diffuse_color"))
    )
    row = dict(
        name=_get(material, "name", "Default"),
        base_colour=_colour(surface.base),
        metallic=surface.metallic,
        smoothness=1 - surface.roughness,
        emissive=surface.emissive,
        emission_colour=_colour(surface.emission, srgb=False),
    )
    return dict(material_id=_hash(row)[:16], **row)


def classify_collider(name: str, world_size: Sequence[float], instance_count: int) -> str:
    dimensions = sorted(world_size)
    words = re.findall(r"[a-z]+", name.casefold())
    led = any(word in ("led", "leds", "emissive") or word.startswith("ledstrip") for word in words)
    if dimensions[-1] < 0.12 or (led and dimensions[1] < 0.08):
        return "none"
    structure = ("floor", "wall", "stair", "stage", "plinth", "structur", "architectur", "hero")
    if (
        instance_count >= 12
        and dimensions[-1] < 1.5
        and not any(word.startswith(structure) for word in words)
    ):
        return "box"
    return "mesh"


def _xyz(vector: Vector) -> dict[str, float]:
    # Canonicalise signed zero so it does not break mesh deduplication.
    return {axis: value or 0.0 for axis, value in zip("xyz", vector, strict=True)}


def _rotation(quaternion: Quaternion) -> dict[str, float]:
    return dict(zip("xyzw", quaternion, strict=True))


def _bounds_size(points: Sequence[Vector]) -> Vector:
    x, y, z = zip(*points, strict=True)
    return max(x) - min(x), max(y) - min(y), max(z) - min(z)


def _geometry(
    mesh: Any, points: Sequence[Vector], materials: Sequence[str], reverse: bool
) -> dict[str, Any]:
    layer = mesh.uv_layers.active
    uv_data = layer.data if layer is not None else None
    vertices: list[dict[str, float]] = []
    uvs: list[dict[str, float]] = []
    indices: dict[tuple[int, float, float], int] = {}
    submeshes: dict[int, list[int]] = {}
    for triangle in mesh.loop_triangles:
        corners = []
        for vertex, loop in zip(triangle.vertices, triangle.loops, strict=True):
            u, v = uv_data[loop].uv if uv_data is not None else (0.0, 0.0)
            if not math.isfinite(u) or not math.isfinite(v):
                raise RuntimeError("Non-finite mesh UV")
            key = (vertex, u, v)
            if key not in indices:
                indices[key] = len(vertices)
                vertices.append(_xyz(points[vertex]))
                uvs.append({"x": u or 0.0, "y": v or 0.0})
            corners.append(indices[key])
        a, b, c = corners
        submeshes.setdefault(triangle.material_index, []).extend(
            (a, c, b) if reverse else (a, b, c)
        )
    if not vertices:
        raise RuntimeError("Empty evaluated mesh")
    return dict(
        vertices=vertices,
        uvs=uvs,
        submeshes=[
            dict(
                material_id=materials[index] if 0 <= index < len(materials) else materials[-1],
                triangles=triangles,
            )
            for index, triangles in sorted(submeshes.items())
        ],
    )


def _light(obj: Any, matrix: Matrix, unit: float) -> dict[str, Any] | None:
    light = obj.data
    kind = {"SUN": "directional", "SPOT": "spot", "POINT": "point", "AREA": "point"}.get(light.type)
    rotation = blender_light_rotation_to_unity(matrix)
    position = _xzy((matrix[0][3], matrix[1][3], matrix[2][3]), unit)
    energy = light.energy
    colour = tuple(light.color)
    if (
        kind is None
        or rotation is None
        or not all(math.isfinite(v) for v in (*position, energy, *colour))
    ):
        return None
    directional = kind == "directional"
    angle = clamp(math.degrees(_get(light, "spot_size", math.pi / 4)), 5.0, 175.0)
    return dict(
        name=obj.name,
        type=kind,
        position=_xyz(position),
        rotation=_rotation(rotation),
        colour=_colour(_rgba(colour)),
        intensity=clamp(energy, 0.1, 5.0)
        if directional
        else clamp(energy / (4 * math.pi), 0.2, 350.0),
        range=clamp(math.sqrt(max(0.0, energy)) * 0.65, 3.0, 28.0),
        spot_angle=angle,
        inner_spot_angle=angle * (1 - clamp(_get(light, "spot_blend", 0.15))),
        cast_shadows=directional or _get(light, "use_shadow", True),
    )


def _environment(
    world: Any, has_lights: bool, *, verified_images: set[int] | None = None
) -> dict[str, Any]:
    colour: RGBA = (0.0, 0.0, 0.0, 1.0)
    strength = 0.0
    if _get(world, "use_nodes", False):
        output = _active_output(_get(world, "node_tree"), "OUTPUT_WORLD")
        background, _ = _link(_socket(output, "Surface"))
        if _get(background, "type") == "BACKGROUND":
            colour = _rgba(_value(_socket(background, "Color"), verified_images), colour)
            strength = max(
                0.0, _scalar(_value(_socket(background, "Strength"), verified_images), 1.0)
            )
    elif world is not None:
        colour = _rgba(_get(world, "color"), colour)
        strength = 1.0
    lit_linear = (
        max(0.0, colour[0]) * strength,
        max(0.0, colour[1]) * strength,
        max(0.0, colour[2]) * strength,
        1.0,
    )
    world_colour = _colour(lit_linear, srgb=True)
    use_skybox = not has_lights and _luminance(lit_linear[:3]) >= 0.15
    ambient = tuple(
        max(world_colour[c], floor) for c, floor in zip("rgb", (0.08, 0.09, 0.11), strict=True)
    )
    return dict(
        has_environment=True,
        world_colour=world_colour,
        world_strength=strength,
        ambient_colour=_colour((ambient[0], ambient[1], ambient[2], 1.0), srgb=False),
        use_skybox=use_skybox,
    )


def main() -> None:
    import bpy  # type: ignore[import-not-found]

    root_arg, output_arg, digest = sys.argv[sys.argv.index("--") + 1 :]
    root = Path(root_arg).resolve(strict=True)
    Path(bpy.data.filepath).resolve(strict=True).relative_to(root)
    for library in bpy.data.libraries:
        Path(bpy.path.abspath(library.filepath)).resolve(strict=True).relative_to(root)
    verified_images = validate_external_images(
        bpy.data.images, root, lambda image: bpy.path.abspath(image.filepath, library=image.library)
    )
    scene = bpy.context.scene
    unit = scene.unit_settings.scale_length
    graph = bpy.context.evaluated_depsgraph_get()
    rows: list[dict[str, Any]] = []
    collider_inputs: list[tuple[str, Vector]] = []
    lights: list[dict[str, Any]] = []
    meshes: dict[str, dict[str, Any]] = {}
    materials: dict[str, dict[str, Any]] = {}
    material_cache: dict[int, dict[str, Any]] = {}
    seen: set[str] = set()
    identity = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))

    def material_row(material: Any) -> dict[str, Any]:
        pointer = 0 if material is None else material.as_pointer()
        if pointer not in material_cache:
            material_cache[pointer] = extract_material(material, verified_images=verified_images)
        return material_cache[pointer]

    for instance in graph.object_instances:
        obj = instance.object
        if obj.type == "LIGHT" and not obj.hide_render:
            light = _light(obj, instance.matrix_world, unit)
            if light is not None:
                lights.append(light)
            continue
        if obj.type != "MESH":
            continue
        original = obj.original
        object_id = original.get("proscenium_object_id")
        instance_id = original.get("proscenium_instance_id")
        if not object_id or not instance_id or instance_id in seen:
            raise RuntimeError("Missing/ambiguous persistent source instance ID: " + obj.name)
        seen.add(instance_id)
        mesh = obj.to_mesh()
        try:
            mesh.calc_loop_triangles()
            matrix = instance.matrix_world
            trs = blender_matrix_to_unity_trs(matrix, unit)
            world_points = [_xzy(matrix @ vertex.co, unit) for vertex in mesh.vertices]
            points = [_xzy(vertex.co, unit) for vertex in mesh.vertices] if trs else world_points
            if not world_points or not all(
                math.isfinite(value) for point in world_points for value in point
            ):
                raise RuntimeError("Empty or non-finite mesh: " + obj.name)
            slots = [material_row(slot.material) for slot in obj.material_slots]
            fallback = material_row(None)
            material_ids = [row["material_id"] for row in (*slots, fallback)]
            # Only baked geometry applies the world determinant to its winding.
            geometry = _geometry(
                mesh, points, material_ids, trs is not None or matrix.determinant() >= 0
            )
            mesh_key = _hash(geometry if trs else (instance_id, geometry))
            if mesh_key not in meshes:
                exported_points = [(v["x"], v["y"], v["z"]) for v in geometry["vertices"]]
                meshes[mesh_key] = dict(
                    mesh_key=mesh_key,
                    local_bounds_size=_xyz(_bounds_size(exported_points)),
                    **geometry,
                )
            used_ids = {submesh["material_id"] for submesh in geometry["submeshes"]}
            for row in (*slots, fallback):
                if row["material_id"] in used_ids:
                    materials[row["material_id"]] = row
            position, rotation, scale = trs or identity
            rows.append(
                dict(
                    name=obj.name,
                    object_id=object_id,
                    instance_id=instance_id,
                    mesh_key=mesh_key,
                    has_transform=True,
                    position=_xyz(position),
                    rotation=_rotation(rotation),
                    scale=_xyz(scale),
                    colour=(slots[0] if slots else fallback)["base_colour"],
                )
            )
            emissive = all(materials[material_id]["emissive"] for material_id in used_ids)
            collider_inputs.append(
                (f"{obj.name} emissive" if emissive else obj.name, _bounds_size(world_points))
            )
        finally:
            obj.to_mesh_clear()
    if not rows:
        raise RuntimeError("No evaluated meshes")
    counts = Counter(row["mesh_key"] for row in rows)
    for row, (name, size) in zip(rows, collider_inputs, strict=True):
        row["collider"] = classify_collider(name, size, counts[row["mesh_key"]])
    output = Path(output_arg)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            dict(
                format="proscenium-triangles-1",
                closure_digest=digest,
                blender_version=bpy.app.version_string,
                units="metres",
                axes="XZY",
                objects=sorted(rows, key=lambda row: row["instance_id"]),
                materials=[materials[key] for key in sorted(materials)],
                shared_meshes=[meshes[key] for key in sorted(meshes)],
                lights=sorted(lights, key=lambda row: (row["name"], _hash(row))),
                environment=_environment(
                    scene.world, bool(lights), verified_images=verified_images
                ),
            ),
            allow_nan=False,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
