"""Integrity of retained bounded native proof. Does not launch SDK/Blender/media jobs."""

import json
from pathlib import Path

from proscenium.contracts import validate
from proscenium.sources.resolver import closure_digest

EVIDENCE = Path(__file__).resolve().parents[3] / "docs/evidence/geometry-nodes"


def report():
    return json.loads((EVIDENCE / "final-run.json").read_bytes())


def test_separate_follow_up_allocation_and_no_children():
    proof = report()
    assert proof["status"] == "bounded_stateless_geometry_nodes_native_proof_pass"
    assert proof["allocation"] == {
        "roots": 1,
        "children": 0,
        "authoring_processes": 1,
        "proxy_exports": 1,
        "cpu_stills": 1,
        "unused_second_still": True,
    }
    assert proof["runtime"]["model"] == "gpt-6.1-sol"
    assert proof["seconds_to_terminal_root_and_previews"] < 180
    assert proof["session_allocation"]["excluded_tools"] == ["task"]
    assert proof["session_allocation"]["custom_agents"] == []
    assert proof["session_allocation"]["permission_handler_preserved"]
    blocked = json.loads((EVIDENCE / "blocked-initial.json").read_bytes())
    assert blocked["session_id"] != proof["runtime"]["session_id"]
    assert blocked["owner_decision_count"] == 0
    assert blocked["allocation"]["authoring_processes"] == 0


def test_actual_checkpoint_and_saved_editable_nested_graph():
    proof = report()
    checkpoint, version = proof["checkpoint"], proof["source_version"]
    assert checkpoint["source_commit"] == version["source_commit"]
    assert closure_digest(version["source"]) == version["source"]["digest"]
    assert version["source"]["required_runtime"]["features"] == ["geometry-nodes"]
    graph = proof["saved_graph_and_evaluation"]
    assert graph["base_vertex_count"] == 0 and graph["has_nodes_modifier"]
    assert len(graph["reachable_groups"]) == 2
    assert graph["evaluated_vertex_count"] == 8 and graph["evaluated_polygon_count"] == 6
    assert (
        proof["editable_source_sha256_before_after"][0]
        == proof["editable_source_sha256_before_after"][1]
    )
    assert proof["authorship"]["recipe_absent_at_fixture_head"]
    assert proof["authoring"]["native_exit_code"] == 0


def test_actual_production_mesh_bounds_match_saved_node_socket_parameters():
    proof = report()
    graph = proof["saved_graph_and_evaluation"]
    minimum = [t - size / 2 for t, size in zip(graph["translation"], graph["mesh_cube_size"])]
    maximum = [t + size / 2 for t, size in zip(graph["translation"], graph["mesh_cube_size"])]
    for purpose in ("proxy", "keyframe"):
        production = proof["production"][purpose]
        assert production["terminal"]["native_exit_code"] == 0
        profile = production["profile"]
        assert profile["device"] == "CPU" and profile["threads"] == 4 and profile["samples"] == 8
        (mesh,) = profile["procedural_meshes"]
        assert mesh["bounds_min"] == minimum and mesh["bounds_max"] == maximum
        assert mesh["source_vertices"] == 0 and mesh["evaluated_vertices"] == 8
        assert mesh["evaluated_polygons"] == 6
        assert set(production["renderer_verified"]["renderer_files_sha256"]) == {
            "blender_worker.py",
            "geometry_nodes.py",
            "journal.py",
        }
        assert (
            production["renderer_verified"]["task_sha256"] == production["terminal"]["task_sha256"]
        )


def test_actual_glb_triangle_hit_keeps_generator_identity_and_source():
    proof = report()
    validate("ProxyMapping", proof["proxy_mapping"])
    validate("FrameReceipt", proof["frame_receipt"])
    triangle = proof["actual_glb_triangle_identity"]
    assert triangle["triangle_count"] == 12 and triangle["index_count"] == 36
    assert triangle["position_accessor_count"] == 24 and len(triangle["unique_positions"]) == 8
    assert triangle["ray"]["nearest"]["triangle_index"] == 2
    assert triangle["ray"]["nearest"]["distance_m"] == 4.25
    assert triangle["ray"]["hit_point_proxy_m"] == [0.6, 0.4, 0.75]
    assert triangle["ray"]["object_id"] == "object-procedural-block"
    assert triangle["ray"]["instance_id"] == "instance-procedural-block"
    assert (
        proof["proxy_mapping"]["source_closure_digest"]
        == proof["source_version"]["source"]["digest"]
    )
    assert proof["cleanup"]["sdk_process_absent"]
    assert all(
        receipt["owned_process_gone"] for receipt in proof["cleanup"]["native_processes"].values()
    )
