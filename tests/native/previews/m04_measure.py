"""Read retained M04/W05 media and measure projected mesh edges. Starts no services."""

import argparse
import json
import math
import statistics
import struct
from pathlib import Path

from PIL import Image
from proscenium.previews.journal import identity, read, sha

REPO = Path(__file__).resolve().parents[3]


def project(camera, point, dimensions):
    # Actual GLB vertices are proxy-world metres. Convert to Blender world first.
    world = (point[0], -point[2], point[1])
    pose = camera["camera_to_world"]
    delta = [world[i] - pose[i * 4 + 3] for i in range(3)]
    local = [sum(pose[row * 4 + col] * delta[row] for row in range(3)) for col in range(3)] + [1]
    matrix = camera["projection"]
    clip = [sum(matrix[row * 4 + col] * local[col] for col in range(4)) for row in range(4)]
    assert clip[3] > 0
    return (
        (clip[0] / clip[3] + 1) * dimensions["width"] / 2,
        (1 - clip[1] / clip[3]) * dimensions["height"] / 2,
    )


def mesh_edges(glb, mapping):
    size = struct.unpack_from("<I", glb, 12)[0]
    document = json.loads(glb[20 : 20 + size])
    binary = glb[20 + size + 8 :]
    result = []
    for record in mapping["nodes"]:
        if record["instance_id"] not in {"instance-blue", "instance-renamed"}:
            continue
        indices = document["scenes"][document.get("scene", 0)]["nodes"]
        for child in record["node_path"]:
            node = document["nodes"][indices[child]]
            indices = node.get("children", [])
        assert node["extras"]["proscenium_instance_id"] == record["instance_id"]
        accessor = document["accessors"][
            document["meshes"][node["mesh"]]["primitives"][0]["attributes"]["POSITION"]
        ]
        assert accessor["componentType"] == 5126 and accessor["type"] == "VEC3"
        view = document["bufferViews"][accessor["bufferView"]]
        offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        local = [
            struct.unpack_from("<fff", binary, offset + i * view.get("byteStride", 12))
            for i in range(accessor["count"])
        ]
        matrix = record["world_matrix"]
        vertices = [
            tuple(
                sum(matrix[row * 4 + col] * vertex[col] for col in range(3)) + matrix[row * 4 + 3]
                for row in range(3)
            )
            for vertex in local
        ]
        low = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
        high = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
        # Both endpoints of the far top edge must really exist in the exported mesh.
        endpoints = [(low[0], high[1], low[2]), (high[0], high[1], low[2])]
        assert all(
            any(max(abs(a - b) for a, b in zip(point, vertex)) < 1e-6 for vertex in vertices)
            for point in endpoints
        )
        result.append(
            {
                "object_id": record["object_id"],
                "instance_id": record["instance_id"],
                "edge_proxy_m": endpoints,
                "node_path": record["node_path"],
            }
        )
    assert len(result) == 2
    return result


def feature(image, x, y):
    # Pixel centres are i+1/2. Bilinear sampling avoids integer-coordinate rounding.
    x, y = x - 0.5, y - 0.5
    left, top = math.floor(x), math.floor(y)
    assert 0 <= left < image.width - 1 and 0 <= top < image.height - 1
    value = 0.0
    for dx in (0, 1):
        for dy in (0, 1):
            pixel = image.getpixel((left + dx, top + dy))
            weight = (x - left if dx else 1 - x + left) * (y - top if dy else 1 - y + top)
            value += weight * (pixel[2] - pixel[0])  # Blue cube vs neutral floor/wall.
    return value


def edge_fit(image, endpoints):
    a, b = endpoints
    tangent = (b[0] - a[0], b[1] - a[1])
    length = math.hypot(*tangent)
    normal = (-tangent[1] / length, tangent[0] / length)
    lanes = []
    for lane in range(17):
        t = 0.2 + 0.6 * lane / 16
        point = [a[i] + t * (b[i] - a[i]) for i in range(2)]

        def sample(distance):
            return feature(image, point[0] + distance * normal[0], point[1] + distance * normal[1])

        before = statistics.median(sample(v) for v in (-8, -7, -6, -5))
        after = statistics.median(sample(v) for v in (5, 6, 7, 8))
        assert abs(after - before) > 20, (before, after, point)
        threshold = (before + after) / 2
        samples = [(i / 8, sample(i / 8)) for i in range(-24, 25)]
        crossings = []
        for (x, v), (xx, vv) in zip(samples, samples[1:]):
            if (v - threshold) * (vv - threshold) <= 0 and vv != v:
                crossings.append(x + (threshold - v) * (xx - x) / (vv - v))
        assert crossings, point
        lanes.append(
            {
                "fraction": t,
                "offset_pixels": min(crossings, key=abs),
                "contrast": abs(after - before),
            }
        )
    median = statistics.median(lane["offset_pixels"] for lane in lanes)
    return {"median_normal_offset_pixels": median, "lanes": lanes}


def main(root):
    from m04_driver import source_snapshot

    state = read(root / "public-final.json")
    baseline = read(root / "fixture-import.json")
    assert baseline["baseline"] == read(root / "source-after.json")
    assert baseline["original_snapshot"] == source_snapshot(Path(baseline["original_root"]))
    project_root = Path(baseline["project"])
    accepted = [read(REPO / f"docs/evidence/w05/camera-selection-dpr{dpr}.json") for dpr in (1, 2)]
    glb = Path(accepted[0]["glbPath"]).read_bytes()
    assert sha(glb) == accepted[0]["glbSha256"]
    edges = mesh_edges(glb, accepted[0]["mapping"])
    measurements = []
    for dpr, capture in enumerate(accepted, 1):
        job = next(
            job
            for job in state["jobs"]
            if job["render_spec"]["purpose"] == "keyframe"
            and job["render_spec"]["dimensions"] == capture["intrinsic"]
        )
        receipt = next(frame for frame in state["frames"] if frame["job_id"] == job["job_id"])
        native_path = project_root / ".proscenium/artefacts/sha256" / receipt["sha256"] / "content"
        assert sha(native_path.read_bytes()) == receipt["sha256"]
        inspection_path = REPO / f"docs/evidence/w05/camera-capture-dpr{dpr}.png"
        assert sha(inspection_path.read_bytes()) == capture["uploadSha256"]
        native, inspection = (
            Image.open(native_path).convert("RGB"),
            Image.open(inspection_path).convert("RGB"),
        )
        assert (
            native.size
            == inspection.size
            == (capture["intrinsic"]["width"], capture["intrinsic"]["height"])
        )
        assert job["render_spec"]["camera"] == capture["selection"]["camera"]
        assert job["state"] == "succeeded" and job["native_exit_code"] == 0
        assert job["availability"] == "complete" and job["completed_frames"] == 1
        assert not [segment for segment in state["segments"] if segment["job_id"] == job["job_id"]]
        job_root = project_root / ".proscenium/jobs/previews" / job["job_id"] / job["attempt_id"]
        task = read(job_root / "task.json")
        profile = read(job_root / "native-profile.json")
        assert profile["threads"] == 4 and profile["samples"] == 8 and profile["device"] == "CPU"
        assert profile["build_hash"] == "62c1db4208e8" and profile["version"] == "4.5.14 LTS"
        for item in task["closure"]["files"]:
            assert (
                sha((Path(task["source_root"]) / item["destination"]).read_bytes())
                == item["origin"]["sha256"]
            )
        rows = []
        for edge in edges:
            pixels = [
                project(capture["selection"]["camera"], point, capture["intrinsic"])
                for point in edge["edge_proxy_m"]
            ]
            observed = edge_fit(inspection, pixels)
            rendered = edge_fit(native, pixels)
            error = abs(
                observed["median_normal_offset_pixels"] - rendered["median_normal_offset_pixels"]
            )
            rows.append(
                {
                    **edge,
                    "projected_pixels": pixels,
                    "inspection": observed,
                    "cycles_render": rendered,
                    "correspondence_error_pixels": error,
                    "within_half_pixel": error <= 0.5,
                }
            )
        measurements.append(
            {
                "dpr": dpr,
                "dimensions": capture["intrinsic"],
                "job_id": job["job_id"],
                "native_png_sha256": receipt["sha256"],
                "inspection_png_sha256": capture["uploadSha256"],
                "edges": rows,
                "profile": {
                    key: profile[key]
                    for key in (
                        "build_hash",
                        "version",
                        "threads",
                        "samples",
                        "device",
                        "review_encoding",
                    )
                },
                "timing": read(job_root / "timing-00000001.json"),
                "owned_processes_exited": all(
                    identity(read(job_root / name)["pid"]) is None
                    for name in ("native-process.json", "process.json")
                ),
            }
        )
    history = next((root / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"))
    identities = []
    for line in history.read_text().splitlines():
        event = json.loads(line)
        if event["type"] == "session.start":
            identities.append(
                {
                    "type": event["type"],
                    "session_id": event["data"]["sessionId"],
                    "model": event["data"]["selectedModel"],
                    "cli": event["data"]["copilotVersion"],
                }
            )
    assert len(identities) == 1 and all(
        record["model"] == "gpt-6.1-sol" and record["cli"] == "1.0.112" for record in identities
    )
    cleanup = read(root / "cleanup.json")
    sdk_pid = cleanup["owned_identities"]["sdk"]["pid"]
    result = {
        "label": "Read-only analysis of actual browser and Cycles PNG geometry, not colour-image equality",
        "measurements": measurements,
        "native_session_identities": identities,
        "source_unchanged": True,
        "original_fixture_unchanged": True,
        "sdk_process_gone": identity(sdk_pid) is None,
        "within_half_pixel": all(
            row["within_half_pixel"] for case in measurements for row in case["edges"]
        ),
    }
    (root / "framing-measurements.json").write_text(json.dumps(result, indent=2) + "\n")
    assert result["within_half_pixel"], "M04 mesh-edge correspondence exceeds 0.5 pixels"
    assert result["sdk_process_gone"] and all(
        case["owned_processes_exited"] for case in measurements
    )
    print(
        json.dumps(
            {
                "within_half_pixel": result["within_half_pixel"],
                "errors": [
                    [row["correspondence_error_pixels"] for row in case["edges"]]
                    for case in measurements
                ],
                "sdk_process_gone": result["sdk_process_gone"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    main(parser.parse_args().root.resolve(strict=True))
