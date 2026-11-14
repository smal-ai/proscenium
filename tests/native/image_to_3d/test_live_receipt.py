"""Read an existing successful E04 proof only. No HTTP, process launch or inference."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from proscenium.contracts import canonical, validate
from proscenium.providers.mesh import inspect_glb


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.mark.native
def test_real_mesh_import_checkpoint_publication_receipt():
    value = os.environ.get("PROSCENIUM_W09_LIVE_RECEIPT")
    if not value:
        pytest.skip("Supply a retained actual mesh/import/checkpoint/publication receipt")
    report_path = Path(value)
    report = json.loads(report_path.read_bytes())
    assert report["allocation"] == "e04-live-b"
    assert report["hardware_mesh_import_checkpoint_publication_proven"] is True
    assert report["e04_passed"] is True and report["m10_passed"] is False
    assert report["remaining_owned_processes"] == []
    assert report["existing_services_mutated"] is False
    assert report["resource_guard_reasons"] == []
    assert report["baseline"]["mem_available_bytes"] >= 12 * 1024**3
    assert report["before_server_start"]["mem_available_bytes"] >= 12 * 1024**3
    assert report["before_inference"]["mem_available_bytes"] >= 12 * 1024**3
    assert report["sampled_resources"]["minimum_mem_available_bytes"] >= 4 * 1024**3
    assert report["verified_weights"]["sha256"] == (
        "360bc281fc956d4acac0c3d36d5ec0ebf8cdddbf4b8892e894d12419388d479b"
    )
    job = report["job"]
    validate("JobRecord", job)
    assert job["kind"] == "mesh_generation" and job["state"] == "succeeded"
    assert report["execution"]["prompt_id"] == job["external_job_id"]
    graph = report["execution"]["graph"]
    assert hashlib.sha256(canonical(graph)).hexdigest() == report["execution"]["graph_sha256"]
    assert graph["3"]["inputs"]["steps"] == 20
    assert graph["3"]["inputs"]["cfg"] == 8
    assert graph["3"]["inputs"]["seed"] == 1
    assert graph["3"]["inputs"]["sampler_name"] == "euler"
    assert graph["66"]["inputs"]["resolution"] == 3072
    assert graph["61"]["inputs"]["octree_resolution"] == 256
    assert graph["61"]["inputs"]["num_chunks"] == 8000
    assert not graph["56"]["inputs"]["image"].startswith("artifact:")
    assert len(report["reservations"]) == 1
    assert report["reservations"][0]["state"] == "consumed"
    assert report["remaining_resource_claims"] == []
    project = Path(report["project"]["path"])
    for artifact in report["artifacts"]:
        validate("Artifact", artifact)
        path = project / ".proscenium/artefacts/sha256" / artifact["sha256"] / "content"
        assert sha(path) == artifact["sha256"]
    glb = project / "assets/e04/original.glb"
    geometry = inspect_glb(glb)
    assert geometry["vertices"] > 3 and geometry["triangles"] > 1
    assert geometry["sha256"] == report["blender"]["source_glb_sha256"]
    assert sha(project / "assets/e04/asset.blend") == report["blender"]["blend_sha256"]
    assert report["blender_exit_code"] == 0
    assert report["blender"]["objects"]
    assert all(obj["object_id"] and obj["instance_id"] for obj in report["blender"]["objects"])
    assert report["checkpoint"]["source_commit"] == report["published_version"]["source_commit"]
    assert report["published_version"]["kind"] == "asset"
    assert report["published_version"]["provenance"]["licence"]
    assert report["publication"]
    for row in report["closure"]["files"]:
        assert row["origin"]["repository_id"] == report["project"]["repository_id"]
        assert sha(project / row["destination"]) == row["origin"]["sha256"]
