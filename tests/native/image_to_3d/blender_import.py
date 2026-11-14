"""Fixed one-shot entry point for the explicitly allocated background Blender import."""

import hashlib
import json
import math
import sys
from pathlib import Path

import bpy


def main():
    source, destination, report_path = [Path(p) for p in sys.argv[sys.argv.index("--") + 1 :]]
    if destination.exists() or report_path.exists():
        raise ValueError("Never repeat an allocated import or overwrite retained evidence")
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0
    bpy.ops.import_scene.gltf(filepath=str(source))
    meshes = sorted(
        (obj for obj in bpy.context.scene.objects if obj.type == "MESH"), key=lambda o: o.name
    )
    if not meshes:
        raise ValueError("GLB import produced no meshes")
    records = []
    for index, obj in enumerate(meshes, 1):
        obj.name = f"E04_Mesh_{index:03d}"
        obj["proscenium_object_id"] = f"e04-object-{index:03d}"
        obj["proscenium_instance_id"] = f"e04-instance-{index:03d}"
        positions = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
        if (
            not positions
            or not obj.data.polygons
            or not all(math.isfinite(x) for v in positions for x in v)
        ):
            raise ValueError("Empty or non-finite imported mesh")
        low = [min(v[i] for v in positions) for i in range(3)]
        high = [max(v[i] for v in positions) for i in range(3)]
        if not any(a < b for a, b in zip(low, high, strict=True)):
            raise ValueError("Collapsed imported mesh")
        obj.data.calc_loop_triangles()
        records.append(
            {
                "name": obj.name,
                "object_id": obj["proscenium_object_id"],
                "instance_id": obj["proscenium_instance_id"],
                "vertices": len(positions),
                "polygons": len(obj.data.polygons),
                "triangles": len(obj.data.loop_triangles),
                "world_bounds": [low, high],
                "scale": list(obj.scale),
                "origin": list(obj.location),
                "materials": [
                    slot.material.name if slot.material else None for slot in obj.material_slots
                ],
                "uv_layers": [layer.name for layer in obj.data.uv_layers],
            }
        )
    report = {
        "blender_version": bpy.app.version_string,
        "build_hash": bpy.app.build_hash.decode(),
        "objects": records,
        "source_glb_sha256": original_hash,
        "materials": [material.name for material in bpy.data.materials],
        "images": [
            {
                "name": image.name,
                "source": image.source,
                "packed": bool(image.packed_file),
                "filepath": image.filepath,
            }
            for image in bpy.data.images
        ],
        "metres_per_blender_unit": 1.0,
        "real_world_scale_calibrated": False,
        "geometry_repaired": False,
        "textures_generated": False,
        "retopology_performed": False,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(destination), check_existing=False)
    if hashlib.sha256(source.read_bytes()).hexdigest() != original_hash:
        raise ValueError("Import modified retained input mesh")
    report["blend_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
