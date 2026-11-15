"""Trusted Blender script: evaluated triangle meshes in metres, Blender XYZ -> Unity XZY.

Invoke with --background --factory-startup --disable-autoexec --threads 4, opening a
sealed SourceService materialisation, then --python this-file -- ROOT OUTPUT DIGEST.
No source saves, texture copies, add-ons or game assets. A usable base material is exported.
"""

import json
import math
import sys
from pathlib import Path


def main() -> None:
    import bpy  # type: ignore[import-not-found]

    root_arg, output_arg, digest = sys.argv[sys.argv.index("--") + 1 :]
    root = Path(root_arg).resolve(strict=True)
    Path(bpy.data.filepath).resolve(strict=True).relative_to(root)
    for library in bpy.data.libraries:
        Path(bpy.path.abspath(library.filepath)).resolve(strict=True).relative_to(root)
    for image in bpy.data.images:
        if image.source == "FILE" and not image.packed_file:
            Path(bpy.path.abspath(image.filepath, library=image.library)).resolve(
                strict=True
            ).relative_to(root)
    scene = bpy.context.scene
    unit = scene.unit_settings.scale_length
    graph = bpy.context.evaluated_depsgraph_get()
    rows = []
    seen = set()
    for instance in graph.object_instances:
        obj = instance.object
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
            vertices = []
            for vertex in mesh.vertices:
                point = instance.matrix_world @ vertex.co
                values = (point.x * unit, point.z * unit, point.y * unit)
                if not all(math.isfinite(value) for value in values):
                    raise RuntimeError("Non-finite mesh: " + obj.name)
                vertices.append(dict(zip(("x", "y", "z"), values, strict=True)))
            triangles: list[int] = []
            # XZY changes handedness. A negative source determinant changes it again.
            reverse = instance.matrix_world.determinant() >= 0
            for triangle in mesh.loop_triangles:
                a, b, c = triangle.vertices
                triangles.extend((a, c, b) if reverse else (a, b, c))
            if not vertices or not triangles:
                raise RuntimeError("Empty mesh: " + obj.name)
            colour = [0.55, 0.6, 0.65, 1]
            if obj.material_slots and obj.material_slots[0].material:
                colour = list(obj.material_slots[0].material.diffuse_color)
            rows.append(
                dict(
                    name=obj.name,
                    object_id=object_id,
                    instance_id=instance_id,
                    vertices=vertices,
                    triangles=triangles,
                    colour=dict(zip(("r", "g", "b", "a"), colour, strict=True)),
                )
            )
        finally:
            obj.to_mesh_clear()
    if not rows:
        raise RuntimeError("No evaluated meshes")
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
            ),
            allow_nan=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
