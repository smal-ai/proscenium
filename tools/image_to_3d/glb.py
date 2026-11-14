"""CPU-only inspection of embedded, indexed triangle GLBs. Not Blender proof."""

import hashlib
import json
import math
import struct
from pathlib import Path


def inspect_glb(path: Path) -> dict:
    if path.is_symlink() or not 20 <= path.stat().st_size <= 256 * 1024**2:
        raise ValueError("unsafe/oversized GLB")
    data = path.read_bytes()
    if struct.unpack_from("<4sII", data) != (b"glTF", 2, len(data)):
        raise ValueError("invalid GLB 2 header")
    chunks = []
    offset = 12
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("truncated GLB chunk header")
        size, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        if size % 4 or offset + size > len(data):
            raise ValueError("invalid GLB chunk extent")
        chunks.append((kind, data[offset : offset + size]))
        offset += size
    if [c[0] for c in chunks] != [0x4E4F534A, 0x004E4942] or len(chunks[0][1]) > 2 * 1024**2:
        raise ValueError("expected JSON and embedded BIN chunks")
    document = json.loads(chunks[0][1])
    binary = chunks[1][1]
    buffers = document.get("buffers", [])
    if (
        len(buffers) != 1
        or "uri" in buffers[0]
        or not len(binary) - 3 <= buffers[0]["byteLength"] <= len(binary)
    ):
        raise ValueError("external or invalid binary buffer")
    if any("uri" in image for image in document.get("images", [])):
        raise ValueError("external image dependencies need a separately verified closure")

    def values(index: int, shape: str, components: dict[int, tuple[str, int]]) -> list:
        accessor = document["accessors"][index]
        if accessor["type"] != shape or accessor.get("sparse") or accessor.get("normalized"):
            raise ValueError("unsupported accessor representation")
        if accessor["componentType"] not in components:
            raise ValueError("unsupported component type")
        fmt, width = components[accessor["componentType"]]
        view = document["bufferViews"][accessor["bufferView"]]
        count = accessor["count"]
        view_offset = view.get("byteOffset", 0)
        relative = accessor.get("byteOffset", 0)
        stride = view.get("byteStride", width)
        if (
            view.get("buffer", 0) != 0
            or count <= 0
            or stride < width
            or min(view_offset, relative, view["byteLength"]) < 0
            or view_offset + view["byteLength"] > buffers[0]["byteLength"]
            or relative + (count - 1) * stride + width > view["byteLength"]
        ):
            raise ValueError("accessor outside embedded buffer")
        return [
            struct.unpack_from(fmt, binary, view_offset + relative + i * stride)
            for i in range(count)
        ]

    vertices = triangles = 0
    minimum, maximum = [math.inf] * 3, [-math.inf] * 3
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            if primitive.get("mode", 4) != 4 or "indices" not in primitive:
                raise ValueError("expected indexed triangles")
            positions = values(primitive["attributes"]["POSITION"], "VEC3", {5126: ("<3f", 12)})
            indices = values(
                primitive["indices"],
                "SCALAR",
                {
                    5121: ("<B", 1),
                    5123: ("<H", 2),
                    5125: ("<I", 4),
                },
            )
            if len(indices) % 3 or any(index[0] >= len(positions) for index in indices):
                raise ValueError("invalid triangle indices")
            for position in positions:
                if not all(math.isfinite(x) for x in position):
                    raise ValueError("non-finite mesh positions")
                minimum = [min(a, b) for a, b in zip(minimum, position, strict=True)]
                maximum = [max(a, b) for a, b in zip(maximum, position, strict=True)]
            vertices += len(positions)
            triangles += len(indices) // 3
    if (
        not vertices
        or not triangles
        or not any(a < b for a, b in zip(minimum, maximum, strict=True))
    ):
        raise ValueError("empty or collapsed mesh")
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "vertices": vertices,
        "triangles": triangles,
        "local_bounds": [minimum, maximum],
        "materials": len(document.get("materials", [])),
        "images": len(document.get("images", [])),
        "blender_import_proven": False,
    }
