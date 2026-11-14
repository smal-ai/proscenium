"""Create or inspect the bounded F01 native fixture inside Blender, never an owner scene."""

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def identify(obj, object_id, instance_id):
    obj["proscenium_object_id"] = object_id
    obj["proscenium_instance_id"] = instance_id


def save(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)


def asset(root, height):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, height / 2))
    obj = bpy.context.object
    obj.name = "Fixture cube"
    obj.dimensions = (1, 1, height)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    # Keep the canonical mesh origin at the ground plane for library reuse.
    for vertex in obj.data.vertices:
        vertex.co.z += height / 2
    obj.location.z = 0
    identify(obj, "object-cube-source", "instance-asset")
    material = bpy.data.materials.new("Fixture texture")
    material.use_nodes = True
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = bpy.data.images.load(str(root / "assets/cube/colour.png"))
    texture.image.filepath = "//colour.png"
    material.node_tree.links.new(
        texture.outputs["Color"], material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"]
    )
    obj.data.materials.append(material)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1
    save(root / "assets/cube/asset.blend")


def box(name, location, dimensions, object_id, instance_id, colour):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    identify(obj, object_id, instance_id)
    material = bpy.data.materials.new(name)
    material.diffuse_color = (*colour, 1)
    material.use_nodes = True
    material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = (*colour, 1)
    obj.data.materials.append(material)
    return obj


def scene(root):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    current = bpy.context.scene
    current.name = "Fixture"
    current.unit_settings.system = "METRIC"
    current.unit_settings.scale_length = 1
    with bpy.data.libraries.load(str(root / "assets/cube/asset.blend"), link=True) as (source, target):
        target.meshes = source.meshes
    mesh = target.meshes[0]
    for instance, position, name in (
        ("instance-blue", (1, 0, 0), "Blue cube"),
        ("instance-renamed", (-1, 0, 0), "Renamed cube"),
    ):
        obj = bpy.data.objects.new(name, mesh)
        current.collection.objects.link(obj)
        obj.location = position
        identify(obj, "object-cube-source", instance)
        if instance == "instance-renamed":
            obj.keyframe_insert(data_path="location", frame=1)
            obj.location.y = 1
            obj.keyframe_insert(data_path="location", frame=12)
            for curve in obj.animation_data.action.fcurves:
                for key in curve.keyframe_points:
                    key.interpolation = "LINEAR"
    box("Floor", (0, 0, -0.1), (6, 6, 0.2), "object-floor", "instance-floor", (0.18, 0.2, 0.23))
    box("Wall", (0, 2.5, 1.25), (6, 0.2, 2.5), "object-wall", "instance-wall", (0.3, 0.32, 0.35))
    camera_data = bpy.data.cameras.new("Fixture camera")
    camera = bpy.data.objects.new("Fixture camera", camera_data)
    current.collection.objects.link(camera)
    camera.location = (5, -7, 4)
    camera.rotation_euler = (Vector((0, 0, 0.7)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.lens = 45
    camera.keyframe_insert(data_path="location", frame=1)
    camera.location.x = 4.5
    camera.keyframe_insert(data_path="location", frame=12)
    current.camera = camera
    light_data = bpy.data.lights.new("Fixture area", type="AREA")
    light_data.energy = 1200
    light_data.shape = "DISK"
    light_data.size = 5
    light = bpy.data.objects.new("Fixture area", light_data)
    current.collection.objects.link(light)
    light.location = (1, -3, 6)
    light.rotation_euler = (Vector((0, 0, 0)) - light.location).to_track_quat("-Z", "Y").to_euler()
    current.world = bpy.data.worlds.new("Fixture world")
    current.world.use_nodes = True
    current.world.node_tree.nodes.get("Background").inputs["Color"].default_value = (0.15, 0.15, 0.15, 1)
    current.render.engine = "CYCLES"
    current.cycles.device = "CPU"
    current.cycles.samples = 4
    current.render.threads_mode = "FIXED"
    current.render.threads = 4
    current.render.resolution_x = 640
    current.render.resolution_y = 360
    current.render.resolution_percentage = 100
    current.render.fps = 24
    current.frame_start = 1
    current.frame_end = 12
    current.view_settings.view_transform = "Standard"
    current.frame_set(1)
    for library in bpy.data.libraries:
        library.filepath = "//../../assets/cube/asset.blend"
    save(root / "scenes/fixture/scene.blend")


def inspect(root, output):
    bpy.ops.wm.open_mainfile(filepath=str(root / "scenes/fixture/scene.blend"), load_ui=False)
    current = bpy.context.scene
    current.frame_set(1)
    records = []
    for obj in current.objects:
        if obj.type != "MESH":
            continue
        corners = [obj.matrix_world @ Vector(vertex) for vertex in obj.bound_box]
        if not all(math.isfinite(value) for point in corners for value in point):
            raise ValueError("Non-finite fixture geometry")
        records.append({
            "name": obj.name,
            "object_id": obj["proscenium_object_id"],
            "instance_id": obj["proscenium_instance_id"],
            "bounds_min": [min(p[i] for p in corners) for i in range(3)],
            "bounds_max": [max(p[i] for p in corners) for i in range(3)],
        })
    libraries = [str(Path(bpy.path.abspath(library.filepath)).resolve()) for library in bpy.data.libraries]
    images = [str(Path(bpy.path.abspath(image.filepath, library=image.library)).resolve())
              for image in bpy.data.images if image.filepath and not image.packed_file]
    for path in libraries + images:
        if not Path(path).is_relative_to(root) or not Path(path).is_file():
            raise ValueError(f"Fixture dependency outside materialisation or missing: {path}")
    report = {
        "blender": bpy.app.version_string,
        "build_hash": bpy.app.build_hash.decode(),
        "source": "scenes/fixture/scene.blend",
        "objects": sorted(records, key=lambda item: item["instance_id"]),
        "libraries": [str(Path(p).relative_to(root)) for p in libraries],
        "images": [str(Path(p).relative_to(root)) for p in images],
        "camera": current.camera.name,
        "frame_range": [current.frame_start, current.frame_end],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["asset-v1", "asset-v2", "scene", "inspect"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parsed = parser.parse_args(args)
    root = parsed.root.resolve()
    if not (root / ".proscenium-fixture").is_file():
        raise ValueError("Refusing to operate outside an explicitly marked disposable fixture")
    if parsed.operation.startswith("asset"):
        asset(root, 1 if parsed.operation == "asset-v1" else 1.5)
    elif parsed.operation == "scene":
        scene(root)
    elif parsed.report is None:
        raise ValueError("Inspection requires --report")
    else:
        inspect(root, parsed.report.resolve())


if __name__ == "__main__":
    main()
