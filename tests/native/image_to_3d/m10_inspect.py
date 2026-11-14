"""One read-only Blender process compares retained v1 with the model-authored revision."""

import hashlib
import json
import math
import sys
from array import array
from pathlib import Path

import bpy


def inspect(path):
    bpy.ops.wm.open_mainfile(filepath=str(path))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if len(meshes) != 1:
        raise ValueError("The revision must retain the one actual Hunyuan mesh")
    obj = meshes[0]
    mesh = obj.data
    positions = array("f")
    for vertex in mesh.vertices:
        positions.extend(obj.matrix_world @ vertex.co)
    if not positions or not all(math.isfinite(value) for value in positions):
        raise ValueError("Non-finite or empty geometry")
    low = [min(positions[i::3]) for i in range(3)]
    high = [max(positions[i::3]) for i in range(3)]
    topology = hashlib.sha256()
    for collection, attribute, width in (
        (mesh.edges, "vertices", 2),
        (mesh.loops, "vertex_index", 1),
        (mesh.polygons, "loop_total", 1),
    ):
        values = array("i", [0]) * (len(collection) * width)
        collection.foreach_get(attribute, values)
        topology.update(values.tobytes())
    mesh.calc_loop_triangles()
    record = {
        "name": obj.name,
        "object_id": obj.get("proscenium_object_id"),
        "instance_id": obj.get("proscenium_instance_id"),
        "vertices": len(mesh.vertices),
        "polygons": len(mesh.polygons),
        "triangles": len(mesh.loop_triangles),
        "world_bounds": [low, high],
        "height_m": high[2] - low[2],
        "base_z_m": low[2],
        "object_scale": list(obj.scale),
        "object_location": list(obj.location),
        "topology_sha256": topology.hexdigest(),
        "world_positions_sha256": hashlib.sha256(positions.tobytes()).hexdigest(),
        "materials": [slot.material.name if slot.material else None for slot in obj.material_slots],
        "uv_layers": [layer.name for layer in mesh.uv_layers],
        "images": [image.name for image in bpy.data.images],
        "camera": bpy.context.scene.camera.name if bpy.context.scene.camera else None,
        "lights": [light.name for light in bpy.data.lights],
        "metres_per_blender_unit": bpy.context.scene.unit_settings.scale_length,
    }
    return record, positions


def main():
    v1, v2, output = [Path(p) for p in sys.argv[sys.argv.index("--") + 1 :]]
    if output.exists():
        raise ValueError("An original inspection receipt exists. Never repeat or overwrite it")
    before_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (v1, v2)}
    first, a = inspect(v1)
    second, b = inspect(v2)
    scale = second["height_m"] / first["height_m"]
    offset = [second["world_bounds"][0][i] - first["world_bounds"][0][i] * scale for i in range(3)]
    maximum_error = max(
        abs(y - (x * scale + offset[i % 3])) for i, (x, y) in enumerate(zip(a, b, strict=True))
    )
    report = {
        "blender_version": bpy.app.version_string,
        "build_hash": bpy.app.build_hash.decode(),
        "v1": first,
        "v2": second,
        "uniform_scale": scale,
        "translation": offset,
        "maximum_uniform_shape_error_m": maximum_error,
        "topology_preserved": first["topology_sha256"] == second["topology_sha256"],
        "source_hashes_before": before_hashes,
        "source_hashes_after": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (v1, v2)
        },
        "source_saved_by_inspector": False,
        "rendered_by_inspector": False,
    }
    output.write_text(json.dumps(report, indent=2) + "\n")
    assert first["vertices"] == second["vertices"] == 350259
    assert first["triangles"] == second["triangles"] == 1304618
    assert first["object_id"] == second["object_id"] == "e04-object-001"
    assert first["instance_id"] == second["instance_id"] == "e04-instance-001"
    assert report["topology_preserved"] and maximum_error < 2e-6
    assert abs(second["height_m"] - 0.12) < 1e-5 and abs(second["base_z_m"]) < 1e-5
    assert second["camera"] and second["lights"] and second["materials"]
    assert report["source_hashes_before"] == report["source_hashes_after"]


if __name__ == "__main__":
    main()
