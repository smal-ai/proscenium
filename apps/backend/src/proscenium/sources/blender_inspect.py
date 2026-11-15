"""Fixed Blender entry point. Executed only by an explicitly allocated CPU inspection."""

import hashlib
import json
import sys
from pathlib import Path


def inspect() -> None:
    import bpy  # type: ignore[import-not-found]

    arguments = sys.argv[sys.argv.index("--") + 1 :]
    root = Path(arguments[0]).resolve(strict=True)
    receipt = json.loads((root / ".receipt.json").read_bytes())
    declared = receipt["files"]
    missing = []
    dependencies = []
    for raw in bpy.utils.blend_paths(absolute=True, packed=False, local=False):
        path = Path(bpy.path.abspath(raw)).resolve()
        try:
            name = path.relative_to(root).as_posix()
        except ValueError:
            missing.append("outside_materialisation")
            continue
        if name not in declared or not path.is_file():
            missing.append(name)
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != declared[name]:
            missing.append(name)
        dependencies.append(name)
    # Missing linked datablocks must not be accepted just because Blender printed a warning.
    for library in bpy.data.libraries:
        if getattr(library, "is_missing", False):
            missing.append("missing_linked_library")
    result = dict(
        application="blender",
        version=bpy.app.version_string,
        build_hash=bpy.app.build_hash.decode(),
        entry_path=receipt["closure"]["entry_path"],
        dependencies=sorted(set(dependencies)),
        missing=sorted(set(missing)),
        objects=len(bpy.data.objects),
        object_dimensions={
            obj.name: list(obj.dimensions)
            for obj in bpy.context.scene.objects
            if obj.type == "MESH"
        },
        images=[
            dict(
                name=image.name,
                packed=bool(image.packed_file),
                width=image.size[0],
                height=image.size[1],
            )
            for image in bpy.data.images
        ],
        allocation_id=arguments[1],
    )
    print("PROSCENIUM_SOURCE_INSPECTION=" + json.dumps(result, sort_keys=True))
    if missing:
        raise SystemExit(2)


if __name__ == "__main__":
    inspect()
