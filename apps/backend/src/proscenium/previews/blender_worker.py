"""Installed presentation entry point, executed in Blender, never an authoring tool."""

import json
import math
import os
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from journal import atomic, identity, output, read, sha  # type: ignore[import-not-found] # noqa: E402


def dependencies(bpy, source, closure, scene):
    declared = {row["destination"]: row["origin"]["sha256"] for row in closure["files"]}
    paths = list(bpy.utils.blend_paths(absolute=True, packed=False, local=False))
    for image in bpy.data.images:
        if image.packed_file or not image.filepath:
            continue
        if image.source == "TILED":
            raise ValueError("unsupported_dynamic_texture: UDIM tile closure is not configured")
        paths.append(bpy.path.abspath(image.filepath, library=image.library))
    trees = list(bpy.data.node_groups)
    trees += [
        owner.node_tree
        for owners in (bpy.data.materials, bpy.data.worlds, bpy.data.lights, bpy.data.scenes)
        for owner in owners
        if owner.node_tree is not None
    ]
    for tree in trees:
        for node in tree.nodes:
            if node.type in {"TEX_IMAGE", "IMAGE"} and node.image and not node.image.packed_file:
                user, image = getattr(node, "image_user", None), node.image
                if user is None:
                    if image.source == "SEQUENCE":
                        raise ValueError("unsupported compositor image sequence")
                    continue  # Static files are already checked through bpy.data.images.
                if image.source == "SEQUENCE":
                    frame = scene.frame_current - user.frame_start + 1 + user.frame_offset
                    user.frame_current = (
                        (frame - 1) % user.frame_duration + 1
                        if user.use_cyclic
                        else max(1, min(user.frame_duration, frame))
                    )
                paths.append(
                    bpy.path.abspath(
                        image.filepath_from_user(image_user=user), library=image.library
                    )
                )
    missing = []
    for raw in paths:
        path = Path(bpy.path.abspath(raw)).resolve()
        if not path.is_relative_to(source):
            missing.append("outside_materialisation")
            continue
        name = path.relative_to(source).as_posix()
        if name not in declared or not path.is_file() or sha(path.read_bytes()) != declared[name]:
            missing.append(name)
    for lib in bpy.data.libraries:
        if getattr(lib, "is_missing", False):
            missing.append("missing_linked_library")
    for obj in scene.objects:
        for modifier in obj.modifiers:
            # V1 refuses simulation evaluation rather than silently rendering unbaked dynamics.
            if modifier.type in {
                "CLOTH",
                "FLUID",
                "DYNAMIC_PAINT",
                "PARTICLE_SYSTEM",
                "SOFT_BODY",
                "NODES",
            }:
                missing.append("unsupported_simulation_cache")
    if bpy.data.cache_files or bpy.data.volumes:
        missing.append("unsupported_dynamic_cache")
    if missing:
        raise ValueError(f"dependency_missing: {sorted(set(missing))}")
    for name, digest in declared.items():
        if sha((source / name).read_bytes()) != digest:
            raise ValueError("Source bytes changed during presentation")


def camera_override(bpy, scene, record):
    from mathutils import Matrix  # type: ignore[import-not-found]

    width, height = scene.render.resolution_x, scene.render.resolution_y
    aspect = width / height
    if abs(record["aspect"] - aspect) > 1e-6:
        raise ValueError("Camera aspect does not match the output")
    pose = Matrix([record["camera_to_world"][i : i + 4] for i in range(0, 16, 4)])
    rotation = pose.to_3x3()
    if (
        max(
            abs((rotation.transposed() @ rotation)[i][j] - (1 if i == j else 0))
            for i in range(3)
            for j in range(3)
        )
        > 1e-6
        or abs(rotation.determinant() - 1) > 1e-6
        or max(abs(pose[3][i] - (1 if i == 3 else 0)) for i in range(4)) > 1e-6
    ):
        raise ValueError("Camera pose must be a rigid metre-space transform")
    data = bpy.data.cameras.new("Proscenium presentation override")
    obj = bpy.data.objects.new("Proscenium presentation override", data)
    scene.collection.objects.link(obj)
    obj.matrix_world = pose
    data.type = "PERSP" if record["kind"] == "perspective" else "ORTHO"
    data.sensor_fit = "VERTICAL"
    data.clip_start, data.clip_end = record["near_m"], record["far_m"]
    if data.clip_end <= data.clip_start:
        raise ValueError("Invalid camera clipping")
    if data.type == "PERSP":
        data.sensor_height = 32
        data.lens = 16 / math.tan(math.radians(record["vertical_fov_degrees"]) / 2)
    else:
        data.ortho_scale = record["orthographic_height_m"]
    scene.camera = obj
    target = [record["projection"][i : i + 4] for i in range(0, 16, 4)]

    def projection():
        return obj.calc_matrix_camera(
            bpy.context.evaluated_depsgraph_get(), x=width, y=height, scale_x=1, scale_y=1
        )

    # Solve shifts against the authoritative matrix, not raw Blender shift assumptions.
    p = projection()
    column = 2 if data.type == "PERSP" else 3
    for attr, row in (("shift_x", 0), ("shift_y", 1)):
        setattr(data, attr, 1)
        shifted = projection()
        setattr(
            data,
            attr,
            (target[row][column] - p[row][column]) / (shifted[row][column] - p[row][column]),
        )
    actual = projection()
    if max(abs(actual[i][j] - target[i][j]) for i in range(4) for j in range(4)) > 1e-5:
        raise ValueError("Camera optical parameters do not reproduce the authoritative projection")
    shift = (
        [-target[0][2] / 2, target[1][2] / 2]
        if data.type == "PERSP"
        else [target[0][3] / 2, -target[1][3] / 2]
    )
    if "shift" in record and max(abs(a - b) for a, b in zip(shift, record["shift"])) > 1e-6:
        raise ValueError("Camera shift contradicts its projection")
    return {
        "camera_to_world": [list(row) for row in obj.matrix_world],
        "projection": [list(row) for row in actual],
        "blender_shift": [data.shift_x, data.shift_y],
    }


def proxy(bpy, scene, root, task):
    from mathutils import Matrix, Quaternion, Vector  # type: ignore[import-not-found]

    scene.frame_set(task["spec"]["frames"]["start"])
    depsgraph = bpy.context.evaluated_depsgraph_get()
    if any(instance.is_instance for instance in depsgraph.object_instances):
        raise ValueError(
            "Collection/particle instances require an explicit authored instance mapping"
        )
    meshes = [obj for obj in scene.objects if obj.type == "MESH" and not obj.hide_render]
    pairs = [(obj.get("proscenium_object_id"), obj.get("proscenium_instance_id")) for obj in meshes]
    if (
        not meshes
        or any(not a or not b for a, b in pairs)
        or len({b for _, b in pairs}) != len(pairs)
    ):
        raise ValueError("Proxy needs pre-existing, unambiguous object and instance IDs")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    path = root / "proxy.glb"
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=True,
        export_extras=True,
        export_animations=False,
        export_yup=True,
        export_apply=True,
        export_cameras=False,
        export_lights=False,
    )
    data = path.read_bytes()
    size, kind = struct.unpack_from("<II", data, 12)
    if kind != 0x4E4F534A:
        raise ValueError("Invalid GLB JSON")
    gltf = json.loads(data[20 : 20 + size])
    nodes = []

    def visit(index, node_path, parent):
        node = gltf["nodes"][index]
        if "matrix" in node:
            local = Matrix([node["matrix"][i::4] for i in range(4)])
        else:
            q = node.get("rotation", [0, 0, 0, 1])
            local = Matrix.LocRotScale(
                Vector(node.get("translation", [0, 0, 0])),
                Quaternion((q[3], *q[:3])),
                Vector(node.get("scale", [1, 1, 1])),
            )
        world = parent @ local
        if "mesh" in node:
            extras = node.get("extras", {})
            pair = (extras.get("proscenium_object_id"), extras.get("proscenium_instance_id"))
            if pair not in pairs:
                raise ValueError("Exported proxy lost authored identities")
            nodes.append(
                dict(
                    node_path=node_path,
                    object_id=pair[0],
                    instance_id=pair[1],
                    display_name=node.get("name", ""),
                    world_matrix=[value for row in world for value in row],
                )
            )
        for child_index, child in enumerate(node.get("children", [])):
            visit(child, node_path + [child_index], world)

    for ordinal, index in enumerate(gltf["scenes"][gltf.get("scene", 0)]["nodes"]):
        visit(index, [ordinal], Matrix.Identity(4))
    if sorted((n["object_id"], n["instance_id"]) for n in nodes) != sorted(pairs):
        raise ValueError("Proxy mapping is incomplete or ambiguous")
    mapping = dict(
        schema_version="1.0",
        project_id=task["project_id"],
        version_id=task["version_id"],
        proxy_artifact_id=task["proxy_artifact_id"],
        source_closure_digest=task["closure"]["digest"],
        sample_frame=scene.frame_current,
        space="proxy_world_m",
        nodes=nodes,
    )
    atomic(root / "proxy-map.json", mapping)
    atomic(
        root / "proxy-ready.json",
        {"proxy_sha256": sha(data), "mapping_sha256": sha((root / "proxy-map.json").read_bytes())},
    )


def main():
    import bpy  # type: ignore[import-not-found]

    root = Path(sys.argv[sys.argv.index("--") + 1]).resolve(strict=True)
    task = read(root / "task.json")
    spec, closure = task["spec"], task["closure"]
    source = Path(task["source_root"]).resolve(strict=True)
    if (
        bpy.app.version_string != closure["required_runtime"]["version"]
        or bpy.app.build_hash.decode() != task["config"]["build_hash"]
    ):
        raise ValueError("Required Blender build is not installed")
    if closure["required_runtime"]["application"].lower() != "blender":
        raise ValueError("Source runtime is not Blender")
    supported = {
        "cycles",
        "CYCLES",
        "linked_libraries",
        "linked_textures",
        "gltf",
        "animation",
        "linked-libraries",
        "external-textures",
    }
    if set(closure["required_runtime"]["features"]) - supported:
        raise ValueError("Required native feature is not supported by this renderer")
    bpy.ops.wm.open_mainfile(filepath=str(source / closure["entry_path"]), load_ui=False)
    scene = bpy.data.scenes[spec["scene_name"]]
    bpy.context.window.scene = scene
    if abs(scene.unit_settings.scale_length - 1) > 1e-8:
        raise ValueError("Presentation source must use metre world units")
    dependencies(bpy, source, closure, scene)
    scene.render.engine = spec["engine"]
    scene.cycles.device = spec["device"]
    scene.cycles.samples = spec["samples"]
    scene.cycles.use_denoising = False
    scene.cycles.seed = 0
    scene.render.threads_mode = "FIXED"
    scene.render.threads = task["config"]["threads"]
    scene.render.resolution_x = spec["dimensions"]["width"]
    scene.render.resolution_y = spec["dimensions"]["height"]
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scene.render.fps = spec["fps"]["numerator"]
    scene.render.fps_base = spec["fps"]["denominator"]
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.display_settings.display_device = "sRGB"
    for key, value in spec["colour_management"].items():
        setattr(scene.view_settings, key, value)
    override = camera_override(bpy, scene, spec["camera"]) if spec["camera"] else None
    static_camera = scene.camera if override else None
    atomic(
        root / "native-profile.json",
        {
            "build_hash": bpy.app.build_hash.decode(),
            "version": bpy.app.version_string,
            "camera_override": override,
            "spec": spec,
            "process": identity(os.getpid()),
            "review_encoding": "view_transformed_srgb PNG RGB8",
            "threads": scene.render.threads,
            "samples": scene.cycles.samples,
            "device": scene.cycles.device,
        },
    )
    if spec["purpose"] == "proxy":
        proxy(bpy, scene, root, task)
        dependencies(bpy, source, closure, scene)
        atomic(root / "native-complete.json", {"complete": True})
        return
    (root / "frames").mkdir(exist_ok=True)
    for frame in range(spec["frames"]["start"], spec["frames"]["end"] + 1):
        if (root / "cancel.json").exists():
            raise RuntimeError("Owned attempt cancellation requested")
        scene.frame_set(frame)
        if static_camera is not None:
            # Authored timeline camera markers may otherwise replace a static override.
            scene.camera = static_camera
        dependencies(bpy, source, closure, scene)
        receipt_path = root / f"frame-{frame:08d}.json"
        if receipt_path.exists():
            old = read(receipt_path)
            if sha(output(root, old["output_path"]).read_bytes()) != old["sha256"]:
                raise ValueError("Previously receipted frame changed")
            continue
        path = output(root, f"frames/{frame:08d}.png")
        if path.exists():
            raise ValueError("Unreceipted output requires reconciliation, not overwrite")
        temporary = path.with_name(f".pending-{frame:08d}.png")
        scene.render.filepath = str(temporary)
        started = time.monotonic_ns()
        bpy.ops.render.render(write_still=True, scene=scene.name)
        temporary.replace(path)
        receipt = dict(
            schema_version="1.0",
            receipt_id=f"{task['attempt_id']}-f{frame}",
            project_id=task["project_id"],
            job_id=task["job_id"],
            attempt_id=task["attempt_id"],
            version_id=task["version_id"],
            shot_id=spec["shot_id"],
            source_frame=frame,
            artifact_id=f"{task['job_id']}-f{frame}",
            output_path=str(path.relative_to(root)),
            sha256=sha(path.read_bytes()),
            dimensions=spec["dimensions"],
            input_digest=task["input_digest"],
        )
        atomic(receipt_path, receipt)
        atomic(
            root / f"timing-{frame:08d}.json",
            {
                "started_ns": started,
                "ready_ns": time.monotonic_ns(),
                "camera_matrix_world": [list(row) for row in scene.camera.matrix_world],
            },
        )
        print("PROSCENIUM_FRAME=" + str(frame), flush=True)
    dependencies(bpy, source, closure, scene)
    atomic(root / "native-complete.json", {"complete": True})


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        root = Path(sys.argv[sys.argv.index("--") + 1]).resolve(strict=True)
        message = str(error)
        code = (
            "capability_unavailable"
            if "unsupported" in message or "Required" in message
            else "dependency_missing"
            if "dependency_missing" in message
            else "hash_mismatch"
            if "changed" in message
            else "render_failed"
        )
        atomic(root / "native-error.json", {"error_code": code, "exception": type(error).__name__})
        raise
