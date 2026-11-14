"""Read retained native output, measure F02 edges and ray-pick actual GLB triangles.

No Blender process is started. Optional fractional encoding is bounded and repeat-safe.
"""

import argparse
import json
import math
import sqlite3
import statistics
import struct
from pathlib import Path

from PIL import Image
from proscenium.previews.journal import read, sha
from proscenium.previews.media import encode, verify_video


def camera_edges(job):
    root = Path(job["root"])
    spec = job["job"]["render_spec"]
    camera = spec["camera"]
    p = camera["projection"]
    actual = read(root / "native-profile.json")["camera_override"]["projection"]
    matrix_error = max(abs(a - b) for a, b in zip(p, [v for row in actual for v in row]))
    assert matrix_error < 1e-5
    divisor = 9 if camera["kind"] == "perspective" else 1
    shift_x = -p[2] if camera["kind"] == "perspective" else p[3]
    shift_y = -p[6] if camera["kind"] == "perspective" else p[7]
    x = round(320 * (1 + p[0] / divisor + shift_x))
    expected = [180 * (1 - (p[5] * y / divisor + shift_y)) for y in (0.5, -0.5)]
    image = Image.open(root / "frames/00000001.png")
    rows = []
    # Known blue cube at x=1,y=0,z=[0,1], sampled vertically through its top face.
    # Top edge: blue-channel rise from shadowed floor to blue cube. Bottom: red
    # rise from blue cube to grey floor. Interpolate the half-step between pixel centres.
    for channel, predicted in zip((2, 0), expected):
        centre = math.floor(predicted)
        before = statistics.median(
            image.getpixel((x, centre + delta))[channel] for delta in (-6, -5, -4, -3)
        )
        after = statistics.median(
            image.getpixel((x, centre + delta))[channel] for delta in (3, 4, 5, 6)
        )
        threshold = (before + after) / 2
        crossings = []
        for y in range(centre - 2, centre + 3):
            a, b = image.getpixel((x, y))[channel], image.getpixel((x, y + 1))[channel]
            if a <= threshold < b:
                crossings.append(y + 0.5 + (threshold - a) / (b - a))
        assert crossings
        measured = min(crossings, key=lambda value: abs(value - predicted))
        error = abs(measured - predicted)
        assert error <= 0.5, (camera["kind"], predicted, measured, error)
        rows.append(
            {
                "expected_pixel_y": predicted,
                "measured_pixel_y": measured,
                "absolute_error_pixels": error,
                "sample_column": x,
                "channel": channel,
                "half_step": threshold,
            }
        )
    return {"kind": camera["kind"], "matrix_max_error": matrix_error, "edges": rows}


def proxy_picks(job, mapping):
    data = (Path(job["root"]) / "proxy.glb").read_bytes()
    size = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20 : 20 + size])
    binary = data[20 + size + 8 :]

    def accessor(index):
        row = gltf["accessors"][index]
        view = gltf["bufferViews"][row["bufferView"]]
        kind = {5123: "H", 5125: "I", 5126: "f"}[row["componentType"]]
        count = {"VEC3": 3, "SCALAR": 1}[row["type"]]
        stride = view.get("byteStride", struct.calcsize("<" + kind) * count)
        offset = view.get("byteOffset", 0) + row.get("byteOffset", 0)
        return [
            struct.unpack_from("<" + kind * count, binary, offset + i * stride)
            for i in range(row["count"])
        ]

    def sub(a, b):
        return tuple(x - y for x, y in zip(a, b))

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

    triangles = []
    for record in mapping["nodes"]:
        indices = gltf["scenes"][gltf.get("scene", 0)]["nodes"]
        for step in record["node_path"]:
            node = gltf["nodes"][indices[step]]
            indices = node.get("children", [])
        assert node["extras"]["proscenium_object_id"] == record["object_id"]
        assert node["extras"]["proscenium_instance_id"] == record["instance_id"]
        matrix = record["world_matrix"]
        for primitive in gltf["meshes"][node["mesh"]]["primitives"]:
            vertices = accessor(primitive["attributes"]["POSITION"])
            positions = [
                tuple(
                    sum(matrix[row * 4 + col] * v[col] for col in range(3)) + matrix[row * 4 + 3]
                    for row in range(3)
                )
                for v in vertices
            ]
            indices = [value[0] for value in accessor(primitive["indices"])]
            for i in range(0, len(indices), 3):
                triangles.append((record, *(positions[indices[i + j]] for j in range(3))))
    results = []
    for x, expected in ((1, "instance-blue"), (-1, "instance-renamed")):
        origin, direction = (x, 2, 0), (0, -1, 0)
        hits = []
        for record, a, b, c in triangles:
            edge1, edge2 = sub(b, a), sub(c, a)
            h = cross(direction, edge2)
            det = dot(edge1, h)
            if abs(det) < 1e-9:
                continue
            s = sub(origin, a)
            u = dot(s, h) / det
            q = cross(s, edge1)
            v = dot(direction, q) / det
            distance = dot(edge2, q) / det
            if u >= 0 and v >= 0 and u + v <= 1 and distance > 1e-9:
                hits.append((distance, record))
        distance, selected = min(hits, key=lambda pair: pair[0])
        assert selected["instance_id"] == expected and selected["object_id"] == "object-cube-source"
        assert abs(distance - 1) <= 1e-6
        results.append(
            {
                "origin_proxy_m": origin,
                "direction_proxy": direction,
                "hit_distance_m": distance,
                "object_id": selected["object_id"],
                "instance_id": selected["instance_id"],
                "node_path": selected["node_path"],
            }
        )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proof", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fractional-output", type=Path)
    args = parser.parse_args()
    proof = read(args.proof)
    result = {
        "proof_sha256": sha(args.proof.read_bytes()),
        "cameras": [camera_edges(job) for job in proof["jobs"][1:3]],
        "proxy_picks": proxy_picks(proof["jobs"][3], proof["proxy_mapping"]),
    }
    project = Path(proof["output_project"])
    connection = sqlite3.connect(f"file:{project / '.proscenium/state.sqlite3'}?mode=ro", uri=True)
    result["events"] = [
        json.loads(row[0])
        for row in connection.execute("SELECT body FROM events ORDER BY sequence")
    ]
    connection.close()
    draft = proof["jobs"][0]
    root = Path(draft["root"])
    result["draft_timing"] = {
        "started_ns": draft["started_ns"],
        **draft["observations"],
        "last_frame": read(root / "timing-00000012.json"),
        "first_encode": read(root / "encode-00000001.json"),
    }
    if args.fractional_output:
        target = args.fractional_output
        assert target.parent.parent == Path("/tmp/opencode") and target.parent.name.startswith(
            "proscenium-w03-"
        )
        target.parent.mkdir(exist_ok=True)
        config = read(root / "task.json")["config"]
        fps = {"numerator": 24000, "denominator": 1001}
        if not target.exists():
            command = encode(root, 1, 4, fps, config, target)
        else:
            command = None
        result["fractional_encode"] = {
            "path": str(target),
            "sha256": sha(target.read_bytes()),
            "argv": command,
            "validation": verify_video(target, fps, 4, {"width": 640, "height": 360}, config),
        }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {"camera_edges": result["cameras"], "proxy_picks": result["proxy_picks"]}, indent=2
        )
    )


if __name__ == "__main__":
    main()
