"""Opt-in bounded production W02 -> W03 -> artefact/event proof, not a fixture renderer."""

import json
import math
import os
import shutil
import struct
import threading
import time
from pathlib import Path

import pytest
from proscenium.auth import Caller
from proscenium.catalogue import install as catalogue_install
from proscenium.configuration import Settings
from proscenium.previews import install
from proscenium.previews.config import REVISION
from proscenium.previews.journal import read, sha
from proscenium.services import Services
from proscenium.sources import closure_digest

pytestmark = pytest.mark.native


def recipe(digest, intent, *, first=1, last=12, purpose="draft_motion", camera=None):
    value = dict(
        intent_id=intent,
        shot_id="fixture-shot",
        purpose=purpose,
        source_closure_digest=digest,
        scene_name="Fixture",
        camera=camera,
        frames={"start": first, "end": last},
        fps={"numerator": 24, "denominator": 1},
        dimensions={"width": 640, "height": 360},
        engine="CYCLES",
        device="CPU",
        samples=8,
        colour_management={"view_transform": "Standard", "look": "None", "exposure": 0, "gamma": 1},
        renderer_revision=REVISION,
    )
    if purpose != "proxy":
        value["review_output"] = {
            "media_type": "image/png",
            "encoding": "view_transformed_srgb",
            "bit_depth": 8,
        }
    return value


def wait(service, job):
    root = service.root(job)
    started = time.monotonic_ns()
    observations = {}
    while time.monotonic_ns() - started < 60_000_000_000:
        service.reconcile(job["project_id"])
        with service.services.registry.project_store(job["project_id"]).transaction(
            write=False
        ) as tx:
            frames = [r for r in tx.all("frames") if r["job_id"] == job["job_id"]]
            segments = [r for r in tx.all("segments") if r["job_id"] == job["job_id"]]
        if frames and "first_frame_observed_ns" not in observations:
            observations["first_frame_observed_ns"] = time.monotonic_ns()
        if segments and "first_segment_observed_ns" not in observations:
            observations["first_segment_observed_ns"] = time.monotonic_ns()
            observations["last_frame_existed_at_first_segment_publication"] = (
                root / f"frames/{job['render_spec']['frames']['end']:08d}.png"
            ).exists()
        current = service.services.jobs.get(job["project_id"], job["job_id"])
        if current["state"] in {"succeeded", "failed", "cancelled"}:
            assert current["state"] == "succeeded", (
                current,
                (
                    root / ("blender.log" if job["kind"] == "render" else "supervisor.log")
                ).read_text()[-5000:],
            )
            return current, observations
        threading.Event().wait(0.01)  # Polling the DB does not throttle the detached renderer.
    pytest.fail(f"Owned bounded job did not settle. Inspect {root}")


def camera(kind):
    near, far, aspect, shift_x, shift_y = 0.1, 100, 640 / 360, 0.08, -0.06
    if kind == "perspective":
        y = 1 / math.tan(math.radians(50) / 2)
        projection = [
            [y / aspect, 0, -2 * shift_x, 0],
            [0, y, 2 * shift_y, 0],
            [0, 0, -(far + near) / (far - near), -2 * far * near / (far - near)],
            [0, 0, -1, 0],
        ]
        optical = {"vertical_fov_degrees": 50}
    else:
        height = 6
        projection = [
            [2 / (height * aspect), 0, 0, 2 * shift_x],
            [0, 2 / height, 0, -2 * shift_y],
            [0, 0, -2 / (far - near), -(far + near) / (far - near)],
            [0, 0, 0, 1],
        ]
        optical = {"orthographic_height_m": height}
    # Look straight down -Z. Floor/cubes have measurable silhouettes in both projections.
    return dict(
        space="blender_world_m",
        kind=kind,
        camera_to_world=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 10, 0, 0, 0, 1],
        projection=[value for row in projection for value in row],
        aspect=aspect,
        near_m=near,
        far_m=far,
        source_camera_id=None,
        shift=[shift_x, shift_y],
        **optical,
    )


def test_real_progressive_camera_and_proxy(tmp_path):
    fixture = os.environ.get("PROSCENIUM_W03_NATIVE_FIXTURE")
    if not fixture:
        pytest.skip("Requires explicit W03 CPU/native allocation and disposable F01 fixture")
    original = Path(fixture).resolve(strict=True)
    metadata = read(original.parent / "fixture-evidence.json")
    root = tmp_path / "project"
    shutil.copytree(
        original,
        root,
        ignore=shutil.ignore_patterns(".env*", ".proscenium", "*.blend1", "*.blend2"),
    )
    services = Services.create(
        Settings(
            data_directory=tmp_path / "machine",
            free_space_reserve_bytes=0,
            execution_profiles=frozenset({"blender-cpu"}),
            rendering={"max_frames": 20, "max_pixels": 640 * 360, "max_samples": 16},
        )
    )
    services.registry.register(
        dict(
            schema_version="1.0",
            project_id="w03-native",
            repository_id="w03-repo",
            name="Disposable W03 production proof",
            path=str(root),
            native_session_id=None,
            state="inactive",
            state_revision=0,
        )
    )
    services.activate_project("w03-native")
    catalogue = catalogue_install(services)
    previews = install(services)
    services.gate.resume(
        "w03-native", reconciliation_evidence="Explicit bounded W03 native allocation"
    )
    caller = Caller(
        "tool",
        "w03-native-tool",
        "w03-native",
        "work-native",
        services.ownership.epoch,
        operations=("publishOutputs", "requestPreview"),
        invocation_id="invocation-native",
    )
    with services.registry.project_store("w03-native").transaction() as tx:
        tx.put(
            "project_identity",
            "identity",
            {"project_id": "w03-native", "repository_id": "w03-repo"},
        )
        tx.put(
            "work",
            "work-native",
            dict(
                schema_version="1.0",
                project_id="w03-native",
                work_id="work-native",
                originating_request_id="request-native",
                directive_request_ids=[],
                state="running",
                state_revision=0,
                native_session_id="native-fixture-not-a-model-session",
                job_ids=[],
                generation_task_id="generation-native",
            ),
            schema="WorkRecord",
        )
        tx.put(
            "invocations",
            "invocation-native",
            {"work_id": "work-native", "owner_epoch": services.ownership.epoch, "state": "running"},
        )
    closure = dict(
        entry_path="scenes/fixture/scene.blend",
        files=[
            {
                "destination": row["path"],
                "origin": {
                    "kind": "git",
                    "repository_id": "w03-repo",
                    "commit": metadata["asset_v1_commit"],
                    "path": row["path"],
                    "sha256": row["sha256"],
                },
            }
            for row in metadata["historical_files"]
        ],
        dependencies=[],
        required_runtime={
            "application": "blender",
            "version": metadata["blender"],
            "features": ["linked-libraries", "external-textures"],
        },
        digest="0" * 64,
    )
    closure["digest"] = closure_digest(closure)
    proof = {
        "base_commit": "66f653bcf1ef036a7473c7784fd15dd94db5a6a7",
        "output_project": str(root),
        "fixture": str(original),
        "closure": closure,
        "jobs": [],
    }
    try:
        publication = dict(
            schema_version="1.0",
            publication_id="publication-native",
            project_id="w03-native",
            items=[
                dict(
                    item_id="fixture-scene",
                    version_id="fixture-v1",
                    kind="scene",
                    name="Pinned F01 v1",
                    expected_current_version_id=None,
                    source_commit=metadata["asset_v1_commit"],
                    source=closure,
                    artifact_ids=[],
                    preview_requests=[recipe(closure["digest"], "draft-native")],
                )
            ],
        )
        receipt, _ = catalogue.publish(caller, publication)
        job = services.jobs.get("w03-native", receipt["job_ids"][0])
        started = time.monotonic_ns()
        previews.pump("w03-native")
        complete, observations = wait(previews, job)
        assert not observations["last_frame_existed_at_first_segment_publication"]
        assert complete["native_exit_code"] == 0
        availability = previews.availability("w03-native", job["job_id"])
        assert availability["available_ranges"] == [{"start": 1, "end": 12}]
        assert availability["playable_ranges"] == [{"start": 1, "end": 12}]
        proof["jobs"].append(
            {
                "job": complete,
                "observations": observations,
                "started_ns": started,
                "root": str(previews.root(job)),
                "availability": availability,
            }
        )
        for label in ("perspective", "orthographic", "proxy"):
            spec = recipe(
                closure["digest"],
                label,
                first=1,
                last=1,
                purpose="proxy" if label == "proxy" else "keyframe",
                camera=None if label == "proxy" else camera(label),
            )
            job, _ = previews.request(
                caller,
                dict(
                    schema_version="1.0",
                    operation_id=f"preview-{label}",
                    project_id="w03-native",
                    version={"item_id": "fixture-scene", "version_id": "fixture-v1"},
                    spec=spec,
                ),
            )
            previews.pump("w03-native")
            complete, observations = wait(previews, job)
            proof["jobs"].append(
                {"job": complete, "observations": observations, "root": str(previews.root(job))}
            )
        mapping = catalogue.proxy_mapping("w03-native", "fixture-v1")
        pairs = [(r["object_id"], r["instance_id"]) for r in mapping["nodes"]]
        assert ("object-cube-source", "instance-blue") in pairs
        assert ("object-cube-source", "instance-renamed") in pairs
        glb = (
            services.extension("artifacts")
            .ready_path("w03-native", mapping["proxy_artifact_id"])
            .read_bytes()
        )
        size = struct.unpack_from("<I", glb, 12)[0]
        document = json.loads(glb[20 : 20 + size])
        for node in mapping["nodes"]:
            indices = document["scenes"][document.get("scene", 0)]["nodes"]
            for step in node["node_path"]:
                actual = document["nodes"][indices[step]]
                indices = actual.get("children", [])
            assert actual["extras"]["proscenium_instance_id"] == node["instance_id"]
            assert "mesh" in actual
        proof["proxy_mapping"] = mapping
        with services.registry.project_store("w03-native").transaction(write=False) as tx:
            proof["artifacts"] = tx.all("artifacts")
            proof["events"] = tx.events(after=0, limit=100)
        assert all(
            sha((original.parent / "historical" / row["path"]).read_bytes()) == row["sha256"]
            for row in metadata["historical_files"]
        )
    finally:
        previews.close()
        services.close()
        target = os.environ.get("PROSCENIUM_W03_EVIDENCE")
        if target:
            Path(target).write_text(json.dumps(proof, indent=2) + "\n")
