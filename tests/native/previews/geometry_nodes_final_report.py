"""Validate retained real node proof, GLB triangles and hashes. No native startup."""

import argparse
import json
import math
import struct
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image
from proscenium.contracts import validate
from proscenium.previews.journal import identity, read, renderer_hashes, sha
from proscenium.sources.common import git
from proscenium.sources.resolver import closure_digest


def close(actual, expected):
    assert len(actual) == len(expected)
    assert all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(actual, expected))


def glb_triangles(data: bytes, mapping: dict) -> dict:
    """Decode actual binary accessor/index data, then ray-intersect mapped triangles.

    This proof intentionally requires the actual fixture's single identity node.
    It is not a second general-purpose production glTF loader.
    """
    assert struct.unpack_from("<III", data) == (0x46546C67, 2, len(data))
    size, kind = struct.unpack_from("<II", data, 12)
    assert kind == 0x4E4F534A
    document = json.loads(data[20 : 20 + size])
    binary_size, binary_kind = struct.unpack_from("<II", data, 20 + size)
    assert binary_kind == 0x004E4942
    binary = data[28 + size : 28 + size + binary_size]
    assert len(binary) == binary_size
    validate("ProxyMapping", mapping)
    assert len(mapping["nodes"]) == 1
    mapped = mapping["nodes"][0]
    assert mapped["node_path"] == [0]
    close(mapped["world_matrix"], [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    scene = document["scenes"][document.get("scene", 0)]
    assert len(scene["nodes"]) == 1
    node = document["nodes"][scene["nodes"][0]]
    assert not any(
        key in node for key in ("children", "matrix", "rotation", "scale", "translation")
    )
    assert (
        node["extras"]["proscenium_object_id"] == mapped["object_id"] == "object-procedural-block"
    )
    assert (
        node["extras"]["proscenium_instance_id"]
        == mapped["instance_id"]
        == "instance-procedural-block"
    )

    def accessor(index):
        value = document["accessors"][index]
        assert "sparse" not in value and not value.get("normalized", False)
        view = document["bufferViews"][value["bufferView"]]
        assert view["buffer"] == 0
        components = {"SCALAR": 1, "VEC3": 3}[value["type"]]
        code = {5123: "H", 5125: "I", 5126: "f"}[value["componentType"]]
        width = struct.calcsize("<" + code * components)
        offset = view.get("byteOffset", 0) + value.get("byteOffset", 0)
        stride = view.get("byteStride", width)
        return [
            struct.unpack_from("<" + code * components, binary, offset + i * stride)
            for i in range(value["count"])
        ]

    primitives = document["meshes"][node["mesh"]]["primitives"]
    assert len(primitives) == 1
    primitive = primitives[0]
    assert primitive.get("mode", 4) == 4
    points = accessor(primitive["attributes"]["POSITION"])
    indices = [row[0] for row in accessor(primitive["indices"])]
    assert len(indices) == 36 and max(indices) < len(points)
    unique = sorted(set(points))
    assert len(unique) == 8
    lower = [min(point[i] for point in points) for i in range(3)]
    upper = [max(point[i] for point in points) for i in range(3)]
    close(lower, [-0.25, 0, -0.75])
    close(upper, [1.75, 1, 0.75])

    def subtract(a, b):
        return [a[i] - b[i] for i in range(3)]

    def dot(a, b):
        return sum(a[i] * b[i] for i in range(3))

    def cross(a, b):
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]

    origin, direction = [0.6, 0.4, 5], [0, 0, -1]
    hits = []
    for first in range(0, len(indices), 3):
        triangle_indices = indices[first : first + 3]
        a, b, c = [points[index] for index in triangle_indices]
        e1, e2 = subtract(b, a), subtract(c, a)
        h = cross(direction, e2)
        determinant = dot(e1, h)
        if abs(determinant) < 1e-9:
            continue
        s = subtract(origin, a)
        u = dot(s, h) / determinant
        q = cross(s, e1)
        v = dot(direction, q) / determinant
        distance = dot(e2, q) / determinant
        if u >= 0 and v >= 0 and u + v <= 1 and distance > 0:
            hits.append(
                {
                    "triangle_index": first // 3,
                    "indices": triangle_indices,
                    "distance_m": distance,
                    "barycentric": [1 - u - v, u, v],
                }
            )
    assert hits
    nearest = min(hits, key=lambda hit: hit["distance_m"])
    hit_point = [origin[i] + direction[i] * nearest["distance_m"] for i in range(3)]
    close(hit_point, [0.6, 0.4, 0.75])
    return {
        "position_accessor_count": len(points),
        "unique_positions": unique,
        "index_count": len(indices),
        "triangle_count": len(indices) // 3,
        "bounds_proxy_y_up_m": {"min": lower, "max": upper},
        "axis_conversion": "Blender (x,y,z) -> glTF (x,z,-y)",
        "all_triangles_mapped_to": mapped,
        "ray": {
            "origin_proxy_m": origin,
            "direction": direction,
            "nearest": nearest,
            "hit_point_proxy_m": hit_point,
            "object_id": mapped["object_id"],
            "instance_id": mapped["instance_id"],
        },
    }


def main(root: Path, export: bool):
    assert root.name.endswith("-b")
    result = read(root / "native-result.json")
    state, project = result["state"], root / "project"
    assert (
        result["status"]
        == "native_model_graph_and_production_previews_pass_pending_geometry_checks"
    )
    assert len(state["requests"]) == len(state["versions"]) == len(state["publications"]) == 1
    assert state["requests"][0]["receipt"]["state"] == "completed" and not state["decisions"]
    assert len(state["jobs"]) == 2 and len(state["frames"]) == 1
    assert result["seconds_to_both_previews"] < 180
    history = next((root / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"))
    events = [json.loads(line) for line in history.read_text().splitlines()]
    session = next(event["data"] for event in events if event["type"] == "session.start")
    assert session["selectedModel"] == "gpt-6.1-sol"
    assert not any(event["type"].startswith("subagent.") for event in events)
    assert not any(
        event["type"] == "permission.completed"
        and event["data"]["result"]["kind"].startswith("denied")
        for event in events
    )
    users = [
        event
        for event in events
        if event["type"] == "user.message" and not event["data"].get("isAutopilotContinuation")
    ]
    assert len(users) == 1
    calls = [event["data"] for event in events if event["type"] == "tool.execution_start"]
    assert not any(call["toolName"] in {"task", "glob"} for call in calls)
    author_calls = [
        call
        for call in calls
        if call["toolName"] == "bash"
        and "python3 tools/asset_author.py tools/asset.py" in call["arguments"]["command"]
    ]
    assert len(author_calls) == 1
    assert (
        git(project, "ls-tree", result["fixture_head"], "tools/asset.py", "assets/procedural")
        == b""
    )
    assert any(call["toolName"] == "apply_patch" for call in calls)
    checkpoint_id = next(
        call["native_id"] for call in state["invocations"] if call["name"] == "checkpoint_sources"
    )
    checkpoint = json.loads(
        next(
            event["data"]["result"]["content"]
            for event in events
            if event["type"] == "tool.execution_complete"
            and event["data"]["toolCallId"] == checkpoint_id
        )
    )
    assert checkpoint["state"] == "completed" and not checkpoint["replayed"]
    for file in checkpoint["files"]:
        assert sha((project / file["path"]).read_bytes()) == file["sha256"]
    version = state["versions"][0]
    closure = version["source"]
    assert closure_digest(closure) == closure["digest"]
    assert closure["required_runtime"]["features"] == ["geometry-nodes"]
    assert (
        checkpoint["source_commit"]
        == version["source_commit"]
        == git(project, "rev-parse", "HEAD").decode().strip()
    )
    worktree_status = git(project, "status", "--porcelain").decode()
    expected_hash = closure["files"][0]["origin"]["sha256"]
    assert sha((project / closure["entry_path"]).read_bytes()) == expected_hash
    readback = result["authoring_readback"]
    assert readback == read(project / "assets/procedural/readback.json")
    assert readback["has_nodes_modifier"] and readback["base_vertex_count"] == 0
    assert readback["evaluated_vertex_count"] == 8 and readback["evaluated_polygon_count"] == 6
    assert readback["unresolved_instance_count"] == 0 and readback["units"] == "metres"
    groups = readback["reachable_groups"]
    assert len(groups) == 2 and sum(len(group["links"]) for group in groups.values()) == 5
    assert all(link["is_valid"] for group in groups.values() for link in group["links"])
    node_types = {node["type"] for group in groups.values() for node in group["nodes"]}
    assert {
        "GeometryNodeGroup",
        "GeometryNodeMeshCube",
        "GeometryNodeTransform",
        "GeometryNodeRealizeInstances",
    } <= node_types
    minimum = [t - size / 2 for t, size in zip(readback["translation"], readback["mesh_cube_size"])]
    maximum = [t + size / 2 for t, size in zip(readback["translation"], readback["mesh_cube_size"])]
    close(readback["world_bounds"]["min"], minimum)
    close(readback["world_bounds"]["max"], maximum)
    author = read(project / ".proscenium/tool-output/author-terminal.json")
    assert author["native_exit_code"] == 0 and author["recipe_sha256"] == sha(
        (project / "tools/asset.py").read_bytes()
    )
    assert (author["finished_ns"] - author["started_ns"]) / 1e9 < 180
    assert author["headroom_bytes"] >= 4 * 1024**3
    assert (
        "--threads" in author["argv"]
        and author["argv"][author["argv"].index("--threads") + 1] == "4"
    )
    author_actual = identity(author["pid"])
    assert (
        author_actual is None
        or author_actual["start_ticks"] != author["start_ticks"]
        or author_actual["state"] in {"Z", "X"}
    )
    jobs = {}
    for job in state["jobs"]:
        assert job["state"] == "succeeded"
        attempt = project / ".proscenium/jobs/previews" / job["job_id"] / job["attempt_id"]
        task, terminal = read(attempt / "task.json"), read(attempt / "terminal.json")
        verified = read(attempt / "renderer-verified.json")
        assert (
            verified["task_sha256"]
            == terminal["task_sha256"]
            == sha((attempt / "task.json").read_bytes())
        )
        assert (
            verified["renderer_files_sha256"] == task["renderer_files_sha256"] == renderer_hashes()
        )
        assert terminal["native_exit_code"] == 0 and not terminal["timed_out"]
        assert (
            sha((Path(task["source_root"]) / closure["entry_path"]).read_bytes()) == expected_hash
        )
        profile = read(attempt / "native-profile.json")
        assert profile["device"] == "CPU" and profile["samples"] == 8 and profile["threads"] == 4
        (mesh,) = profile["procedural_meshes"]
        assert (
            mesh["source_vertices"] == 0
            and mesh["evaluated_vertices"] == 8
            and mesh["evaluated_polygons"] == 6
        )
        assert set(mesh["node_groups"]) == set(groups)
        close(mesh["bounds_min"], minimum)
        close(mesh["bounds_max"], maximum)
        assert mesh["object_id"] == readback["proscenium_object_id"]
        assert mesh["instance_id"] == readback["proscenium_instance_id"]
        supervisor = read(attempt / "process.json")
        assert (terminal["finished_ns"] - supervisor["started_ns"]) / 1e9 < 300
        jobs[job["render_spec"]["purpose"]] = {
            "job": job,
            "profile": profile,
            "terminal": terminal,
            "renderer_verified": verified,
            "supervisor": supervisor,
            "attempt": attempt,
        }
    proxy = jobs["proxy"]["attempt"]
    mapping = read(proxy / "proxy-map.json")
    triangle_proof = glb_triangles((proxy / "proxy.glb").read_bytes(), mapping)
    artifacts = state["artifacts"]
    for artifact in artifacts:
        path = project / ".proscenium/artefacts/sha256" / artifact["sha256"] / "content"
        assert sha(path.read_bytes()) == artifact["sha256"]
    image = next(artifact for artifact in artifacts if artifact["kind"] == "image")
    image_path = project / ".proscenium/artefacts/sha256" / image["sha256"] / "content"
    with Image.open(image_path) as pixels:
        pixels.load()
        assert pixels.size == (640, 360) and pixels.mode == "RGB"
        assert pixels.getpixel((320, 180)) != pixels.getpixel((0, 0))
    assert next(artifact["sha256"] for artifact in artifacts if artifact["kind"] == "proxy") == sha(
        (proxy / "proxy.glb").read_bytes()
    )
    assert next(
        artifact["sha256"] for artifact in artifacts if artifact["kind"] == "proxy_map"
    ) == sha((proxy / "proxy-map.json").read_bytes())
    allocation = read(root / "session-allocation.json")
    assert allocation == {
        "create_count": 1,
        "custom_agents": [],
        "excluded_tools": ["task"],
        "included_builtin_agents": [],
        "enable_skills": False,
        "permission_handler_preserved": True,
    }
    assert result["cleanup"]["sdk_process_absent"]
    assert len(result["cleanup"]["native_processes"]) == 2
    assert all(
        record["owned_process_gone"] for record in result["cleanup"]["native_processes"].values()
    )
    report = {
        "status": "bounded_stateless_geometry_nodes_native_proof_pass",
        "implementation_commit": result["implementation_commit"],
        "base_commit": result["base_commit"],
        "retained_root": str(root),
        "original_blocked_root": "geometry-nodes-20261114-a remains unchanged and is not an owner denial",
        "runtime": {
            "sdk": "1.0.34",
            "cli": session["copilotVersion"],
            "model": session["selectedModel"],
            "session_id": session["sessionId"],
            "blender": readback["blender"],
        },
        "allocation": {
            "roots": 1,
            "children": 0,
            "authoring_processes": 1,
            "proxy_exports": 1,
            "cpu_stills": 1,
            "unused_second_still": True,
        },
        "session_allocation": allocation,
        "root_request": state["requests"][0]["receipt"],
        "accepted_at": datetime.fromisoformat(state["requests"][0]["accepted_at"])
        .astimezone(ZoneInfo("Africa/Johannesburg"))
        .isoformat(),
        "seconds_to_terminal_root_and_previews": result["seconds_to_both_previews"],
        "initial_headroom_bytes": result["initial_headroom_bytes"],
        "checkpoint": checkpoint,
        "publication": state["publications"][0]["receipt"],
        "source_version": version,
        "authorship": {
            "recipe_absent_at_fixture_head": True,
            "model_apply_patch_observed": True,
            "recipe_sha256": author["recipe_sha256"],
            "authoring_command": author_calls[0]["arguments"]["command"],
        },
        "authoring": author,
        "authoring_process_after_cleanup": author_actual,
        "saved_graph_and_evaluation": readback,
        "production": {
            purpose: {key: value for key, value in proof.items() if key != "attempt"}
            for purpose, proof in jobs.items()
        },
        "proxy_mapping": mapping,
        "actual_glb_triangle_identity": triangle_proof,
        "artifacts": artifacts,
        "frame_receipt": state["frames"][0],
        "editable_source_sha256_before_after": [
            expected_hash,
            sha((project / closure["entry_path"]).read_bytes()),
        ],
        "project_git_status": worktree_status,
        "project_index_note": "The accepted checkpoint uses an isolated index and advances HEAD without rewriting the user's index. Its newly committed paths therefore appear as staged deletions plus untracked working files relative to the old index. The reporter preserves that index and verifies every checkpoint file's exact working bytes instead of claiming a clean index.",
        "source_preservation": "Actual authoring saved nested graph and read it back; both production processes reopened the exact checkpoint bytes with empty base mesh and the same reachable groups. Working and materialised source hashes remain identical after proxy/still. No second inspection Blender process was used.",
        "cleanup": result["cleanup"],
        "limits": [
            "Stateless saved graph compatibility only, no simulation/bake/cache/import or unresolved-instance expansion.",
            "Actual GLB binary accessor/index ray test, not a browser picking/UI claim.",
            "CPU eight-sample still, not final-quality or universal performance acceptance.",
            "Renderer dependency seal validates local source identity before spawn; legacy unsealed tasks are refused at spawn, not silently migrated.",
            "No extra milestone or release acceptance claim; FR08 coverage remains coordinator-owned.",
        ],
        "retained_sha256": {
            name: sha((root / name).read_bytes())
            for name in ("root-reservation.json", "session-allocation.json", "native-result.json")
        },
        "native_history_sha256": sha(history.read_bytes()),
    }
    if export:
        target = Path(__file__).resolve().parents[3] / "docs/evidence/geometry-nodes/final-run.json"
        target.write_text(json.dumps(report, indent=2) + "\n")
        target.with_name("procedural-still.png").write_bytes(image_path.read_bytes())
    print(
        json.dumps(
            {
                "status": report["status"],
                "allocation": report["allocation"],
                "checkpoint": checkpoint,
                "publication": report["publication"],
                "glb": triangle_proof,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    main(args.root.resolve(strict=True), args.export)
