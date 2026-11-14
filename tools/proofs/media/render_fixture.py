"""E03-only Blender presentation worker. Never saves a source file."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import bpy


def main():
    root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    job = json.loads((root / "job.json").read_text())
    bpy.ops.wm.open_mainfile(filepath=str(root / "source/scenes/fixture/scene.blend"), load_ui=False)
    scene = bpy.data.scenes["Fixture"]
    assert job["camera"] is None  # Do not replace the authored, animated camera.
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    scene.cycles.use_denoising = False
    scene.cycles.seed = 0
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.resolution_x = 640
    scene.render.resolution_y = 360
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.display_settings.display_device = "sRGB"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    scene.render.fps = 24
    scene.render.fps_base = 1
    dependencies = [Path(bpy.path.abspath(lib.filepath)).resolve() for lib in bpy.data.libraries]
    dependencies += [Path(bpy.path.abspath(image.filepath, library=image.library)).resolve()
                     for image in bpy.data.images if image.filepath and not image.packed_file]
    assert all(p.is_relative_to(root / "source") and p.is_file() for p in dependencies)
    profile = {
        "blender": bpy.app.version_string, "build_hash": bpy.app.build_hash.decode(),
        "engine": scene.render.engine, "device": scene.cycles.device, "samples": 8,
        "threads": 4, "denoising": False, "seed": 0, "dimensions": [640, 360],
        "camera": None, "authored_camera": scene.camera.name, "fps": [24, 1],
        "review_output": "PNG RGB 8-bit, scene view transform baked by render(write_still=True)",
        "display_device": scene.display_settings.display_device,
        "view_transform": scene.view_settings.view_transform, "look": scene.view_settings.look,
        "exposure": scene.view_settings.exposure, "gamma": scene.view_settings.gamma,
        "dependencies": [str(p.relative_to(root)) for p in dependencies],
        "pid": os.getpid(),
    }
    (root / "native/profile.json").write_text(json.dumps(profile, indent=2) + "\n")
    for frame in range(1, 13):
        scene.frame_set(frame)
        started = time.monotonic_ns()
        target = root / f"native/frames/{frame:04d}.png"
        temporary = target.with_name(f".pending-{frame:04d}.png")
        scene.render.filepath = str(temporary)
        bpy.ops.render.render(write_still=True, scene=scene.name)
        temporary.replace(target)
        receipt = {
            "source_frame": frame, "path": str(target.relative_to(root)),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "ready_ns": time.monotonic_ns(), "render_started_ns": started,
            "camera_matrix_world": [list(row) for row in scene.camera.matrix_world],
            "moving_cube_location": list(scene.objects["Renamed cube"].location),
        }
        receipt_path = target.with_suffix(".json")
        pending = receipt_path.with_suffix(".pending")
        pending.write_text(json.dumps(receipt, indent=2) + "\n")
        pending.replace(receipt_path)
        print("E03_RECEIPT " + json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
