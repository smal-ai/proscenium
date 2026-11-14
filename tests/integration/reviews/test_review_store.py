"""Synthetic offline review records and accepted-service stubs. No native/provider evidence."""

import copy
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.contracts import validate
from proscenium.reviews import install, router


@pytest.fixture
def reviews(services, tmp_path):
    class SyntheticArtifacts:
        def get(self, project_id, artifact_id):
            with services.registry.project_store(project_id).transaction(write=False) as tx:
                return tx.require("artifacts", artifact_id)

        def ready_path(self, project_id, artifact_id):
            return tmp_path / artifact_id

    services.extensions["artifacts"] = SyntheticArtifacts()
    result = install(services)
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put("items", "scene", {"item_id": "scene", "project_id": "project-test"})
        tx.put(
            "versions",
            "version",
            {
                "version_id": "version",
                "item_id": "scene",
                "project_id": "project-test",
                "kind": "scene",
                "artifact_ids": ["image"],
                "source": {"digest": "a" * 64},
            },
        )
        tx.put(
            "artifacts",
            "image",
            dict(
                schema_version="1.0",
                artifact_id="image",
                project_id="project-test",
                kind="image",
                purpose="keyframe",
                sha256="b" * 64,
                bytes=123,
                media_type="image/png",
                state="ready",
                derived_from=[],
                dimensions={"width": 8, "height": 4},
                source_version_id="version",
            ),
            schema="Artifact",
        )
        tx.put(
            "jobs",
            "render",
            {
                "job_id": "render",
                "target": {"version": {"item_id": "scene", "version_id": "version"}},
            },
        )
        tx.put(
            "frames",
            "frame",
            {
                "job_id": "render",
                "version_id": "version",
                "shot_id": "shot",
                "source_frame": 2,
                "artifact_id": "image",
                "sha256": "b" * 64,
                "dimensions": {"width": 8, "height": 4},
            },
        )
    return result


@pytest.fixture
def owner():
    return Caller("owner", "owner-test")


@pytest.fixture
def annotation():
    return dict(
        schema_version="1.0",
        operation_id="save-1",
        project_id="project-test",
        expected_revision=0,
        annotation=dict(
            annotation_id="note",
            saved_revision=None,
            target=dict(
                origin="blender_render",
                artifact_id="image",
                displayed_sha256="b" * 64,
                dimensions={"width": 8, "height": 4},
                item_id="scene",
                version_id="version",
                shot_id="shot",
                render_job_id="render",
                source_frame=2,
                media_artifact_id=None,
            ),
            range={"start": 2, "end": 4},
            camera=None,
            markups=[],
            text="Synthetic feedback",
            spatial_hits=[],
            spatial_status="unavailable",
        ),
    )


def store(reviews):
    return reviews.services.registry.project_store("project-test")


def assert_error(code, action):
    with pytest.raises(DomainError) as error:
        action()
    assert error.value.code == code


def test_annotation_wire_revision_and_old_retry(reviews, owner, annotation):
    first = reviews.save_annotation(owner, annotation)
    assert first["state_revision"] == 1
    second = copy.deepcopy(annotation)
    second.update(operation_id="save-2", expected_revision=1)
    second["annotation"].update(saved_revision=1, text="Edited")
    assert reviews.save_annotation(owner, second)["state_revision"] == 2
    assert reviews.save_annotation(owner, annotation) == {**first, "replayed": True}
    with store(reviews).transaction(write=False) as tx:
        saved = tx.require("annotations", "note")
        validate("AnnotationSnapshot", saved)
        assert saved["saved_revision"] == 2 and saved["text"] == "Edited"
        assert tx.revision("annotations", "note") == 1
        assert len(tx.events()["events"]) == 2


def test_annotation_cas_and_immutable_target(reviews, owner, annotation):
    reviews.save_annotation(owner, annotation)
    stale = copy.deepcopy(annotation)
    stale["operation_id"] = "stale"
    assert_error("revision_conflict", lambda: reviews.save_annotation(owner, stale))
    changed = copy.deepcopy(annotation)
    changed.update(operation_id="move", expected_revision=1)
    changed["annotation"].update(saved_revision=1)
    changed["annotation"]["target"]["source_frame"] = 3
    assert_error("source_conflict", lambda: reviews.save_annotation(owner, changed))
    with store(reviews).transaction(write=False) as tx:
        assert tx.require("annotations", "note")["saved_revision"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_frame", 3),
        ("shot_id", "wrong"),
        ("render_job_id", "wrong"),
        ("displayed_sha256", "c" * 64),
        ("dimensions", {"width": 4, "height": 8}),
        ("version_id", "wrong"),
    ],
)
def test_exact_target_mismatch(reviews, owner, annotation, field, value):
    annotation["annotation"]["target"][field] = value
    with pytest.raises(DomainError):
        reviews.save_annotation(owner, annotation)
    with store(reviews).transaction(write=False) as tx:
        assert tx.get("annotations", "note", required=False) is None
        assert tx.watermark() == 0


def test_no_tool_owner_save_or_mutated_retry(reviews, owner, annotation):
    assert_error(
        "scope_denied", lambda: reviews.save_annotation(Caller("tool", "tool"), annotation)
    )
    reviews.save_annotation(owner, annotation)
    annotation["annotation"]["text"] = "Changed under same ID"
    assert_error("idempotency_conflict", lambda: reviews.save_annotation(owner, annotation))


def test_unavailable_image_space_facts_retained(reviews, owner, annotation):
    with store(reviews).transaction() as tx:
        image = tx.require("artifacts", "image")
        image["state"] = "unavailable"
        tx.put("artifacts", "image", image, expected_revision=0)
    reviews.save_annotation(owner, annotation)
    with store(reviews).transaction(write=False) as tx:
        assert tx.require("annotations", "note")["spatial_status"] == "unavailable"


@pytest.mark.parametrize(
    "mode,pixel,semantics,valid",
    [
        ("L", 255, "binary", True),
        ("L", 64, "soft", True),
        ("L", 64, "binary", False),
        ("RGB", 255, "binary", False),
    ],
)
def test_masks_decoded_not_filename(
    reviews, owner, annotation, tmp_path, mode, pixel, semantics, valid
):
    path = tmp_path / "mask"
    Image.new(mode, (8, 4), pixel).save(path, format="PNG")
    with store(reviews).transaction() as tx:
        tx.put(
            "artifacts",
            "mask",
            {
                "project_id": "project-test",
                "artifact_id": "mask",
                "kind": "mask",
                "state": "ready",
                "dimensions": {"width": 8, "height": 4},
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            },
        )
    annotation["annotation"]["markups"] = [
        {
            "id": "mask-mark",
            "kind": "mask",
            "points": [[0.5, 0.5]],
            "mask_artifact_id": "mask",
            "mask_encoding": {
                "channel": "luma",
                "bit_depth": 8,
                "selected_value": 255,
                "unselected_value": 0,
                "semantics": semantics,
            },
        }
    ]
    if valid:
        assert reviews.save_annotation(owner, annotation)["state_revision"] == 1
    else:
        assert_error("invalid_request", lambda: reviews.save_annotation(owner, annotation))


@pytest.fixture
def selection():
    return dict(
        schema_version="1.0",
        operation_id="select-1",
        project_id="project-test",
        selection_id="selection",
        expected_revision=0,
        slot={"kind": "item", "item_id": "scene"},
        choice={"kind": "version", "version": {"item_id": "scene", "version_id": "version"}},
        action="select",
    )


def test_selection_slot_unique_and_revisioned(reviews, owner, selection):
    first = reviews.save_selection(owner, selection)
    assert first["state_revision"] == 1
    bypass = {**selection, "operation_id": "bypass", "selection_id": "another"}
    assert_error("revision_conflict", lambda: reviews.save_selection(owner, bypass))
    withdraw = {
        **selection,
        "operation_id": "withdraw",
        "expected_revision": 1,
        "choice": None,
        "action": "withdraw",
    }
    assert reviews.save_selection(owner, withdraw)["state_revision"] == 2
    assert reviews.save_selection(owner, selection)["state_revision"] == 1
    with store(reviews).transaction(write=False) as tx:
        validate("SelectionRecord", tx.require("selections", "selection"))
        assert len(tx.all("review.selection_history")) == 2


def test_item_choice_must_match(reviews, owner, selection):
    with store(reviews).transaction() as tx:
        tx.put("items", "other", {"item_id": "other"})
    selection["slot"]["item_id"] = "other"
    assert_error("source_conflict", lambda: reviews.save_selection(owner, selection))


def test_storyboard_exact_version_slot_and_no_source_mutation(reviews, owner, selection, tmp_path):
    plan = {
        "schema_version": "1.0",
        "project_id": "project-test",
        "plan_id": "plan",
        "brief_artifact_ids": [],
        "asset_dependencies": [],
        "unresolved": [],
        "shots": [
            {
                "shot_id": "shot",
                "intent": "Synthetic",
                "camera_intent": "Static",
                "motion_beats": [],
                "scene_version": None,
                "source_range": None,
                "fps": None,
                "frames": [
                    {
                        "frame_id": "start",
                        "role": "start",
                        "composition": "Synthetic",
                        "artifact_id": "image",
                        "state": "available",
                    }
                ],
            }
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))

    class SyntheticSources:
        def materialise(self, project_id, version_id):
            return path

        def checkpoint(self, caller, request):
            raise AssertionError("Review reads do not checkpoint")

    reviews.services.extensions["sources"] = SyntheticSources()
    with store(reviews).transaction() as tx:
        tx.put(
            "versions",
            "board-v1",
            {
                "project_id": "project-test",
                "item_id": "board",
                "version_id": "board-v1",
                "source": {"entry_path": "plan.json"},
            },
        )
    selection["slot"] = {
        "kind": "storyboard_frame",
        "storyboard_version": {"item_id": "board", "version_id": "board-v1"},
        "shot_id": "shot",
        "frame_id": "wrong",
    }
    selection["choice"] = {"kind": "artifact", "artifact_id": "image"}
    assert_error("source_conflict", lambda: reviews.save_selection(owner, selection))
    selection["slot"]["frame_id"] = "start"
    reviews.save_selection(owner, selection)
    assert json.loads(path.read_text()) == plan
    with store(reviews).transaction(write=False) as tx:
        frame = plan["shots"][0]["frames"][0]
        assert reviews.owner_selected(
            tx, {"item_id": "board", "version_id": "board-v1"}, "shot", frame
        )
        assert not reviews.owner_selected(
            tx, {"item_id": "board", "version_id": "board-v2"}, "shot", frame
        )
        assert not reviews.owner_selected(
            tx,
            {"item_id": "board", "version_id": "board-v1"},
            "shot",
            {**frame, "artifact_id": "different"},
        )


def test_sequence_composite_target_uses_explicit_map_not_source_frame(
    reviews, owner, annotation, tmp_path
):
    mapping = {
        "schema_version": "1.0",
        "project_id": "project-test",
        "sequence_version_id": "sequence-v1",
        "fps": {"numerator": 24000, "denominator": 1001},
        "frame_count": 2,
        "blend_space": "linear_rgb",
        "frames": [
            {
                "sequence_frame": i,
                "contributors": [
                    {
                        "shot_id": "shot",
                        "version_id": "version",
                        "job_id": "render",
                        "source_frame": 2,
                        "weight": {"numerator": 1, "denominator": 1},
                    }
                ],
            }
            for i in (1, 2)
        ],
    }
    raw = json.dumps(mapping).encode()
    (tmp_path / "frame-map").write_bytes(raw)
    with store(reviews).transaction() as tx:
        tx.put(
            "versions",
            "sequence-v1",
            {"project_id": "project-test", "item_id": "sequence", "version_id": "sequence-v1"},
        )
        image = tx.require("artifacts", "image")
        tx.put(
            "artifacts",
            "composite",
            {**image, "artifact_id": "composite", "source_version_id": "sequence-v1"},
        )
        tx.put(
            "artifacts",
            "frame-map",
            {
                "project_id": "project-test",
                "artifact_id": "frame-map",
                "state": "ready",
                "kind": "frame_map",
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
    annotation["annotation"]["target"].update(
        origin="sequence_render",
        artifact_id="composite",
        item_id="sequence",
        version_id="sequence-v1",
        shot_id=None,
        render_job_id=None,
        source_frame=None,
        sequence_version_id="sequence-v1",
        sequence_frame=2,
        frame_map_artifact_id="frame-map",
    )
    annotation["annotation"]["range"] = {"start": 1, "end": 2}
    assert reviews.save_annotation(owner, annotation)["state_revision"] == 1
    bad = copy.deepcopy(annotation)
    bad["operation_id"] = "out-of-range"
    bad["annotation"]["annotation_id"] = "other"
    bad["annotation"]["target"]["sequence_frame"] = 3
    assert_error("source_conflict", lambda: reviews.save_annotation(owner, bad))


def test_spatial_mapping_binds_version_source_sample_and_instance(
    reviews, owner, annotation, tmp_path
):
    camera = {
        "space": "blender_world_m",
        "kind": "perspective",
        "camera_to_world": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 10, 0, 0, 0, 1],
        "projection": [0.5, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1.002, -0.2002, 0, 0, -1, 0],
        "aspect": 2,
        "near_m": 0.1,
        "far_m": 100,
        "source_camera_id": None,
        "vertical_fov_degrees": 90,
    }
    mapping = {
        "schema_version": "1.0",
        "project_id": "project-test",
        "version_id": "version",
        "proxy_artifact_id": "proxy",
        "source_closure_digest": "a" * 64,
        "sample_frame": 2,
        "space": "proxy_world_m",
        "nodes": [
            {
                "node_path": [0],
                "object_id": "object",
                "instance_id": "blue",
                "display_name": "Synthetic blue instance",
                "world_matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            }
        ],
    }
    raw = json.dumps(mapping).encode()
    (tmp_path / "proxy-map").write_bytes(raw)
    with store(reviews).transaction() as tx:
        tx.put(
            "artifacts",
            "proxy-map",
            {
                "project_id": "project-test",
                "artifact_id": "proxy-map",
                "state": "ready",
                "kind": "proxy_map",
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        tx.put(
            "artifacts",
            "proxy",
            {
                "project_id": "project-test",
                "artifact_id": "proxy",
                "state": "ready",
                "kind": "proxy",
            },
        )
    annotation["annotation"].update(
        camera=camera,
        spatial_status="valid",
        spatial_hits=[
            {
                "proxy_artifact_id": "proxy",
                "mapping_artifact_id": "proxy-map",
                "version_id": "version",
                "sample_frame": 2,
                "object_id": "object",
                "instance_id": "blue",
                "space": "blender_world_m",
                "position": [0, 0, 0],
                "normal": [0, 0, 1],
                "camera": camera,
            }
        ],
    )
    assert reviews.save_annotation(owner, annotation)["state_revision"] == 1
    for field, value in (("sample_frame", 3), ("instance_id", "wrong"), ("version_id", "wrong")):
        bad = copy.deepcopy(annotation)
        bad["operation_id"] = "bad-" + field
        bad["annotation"]["annotation_id"] = "bad-" + field
        bad["annotation"]["spatial_hits"][0][field] = value
        assert_error("source_conflict", lambda: reviews.save_annotation(owner, bad))


def create_review(reviews, annotation):
    request = dict(
        schema_version="1.0",
        review_id="review",
        project_id="project-test",
        targets=[annotation["annotation"]["target"]],
        criteria="Synthetic composition",
        telemetry_artifact_ids=[],
    )
    with store(reviews).transaction() as tx:
        tx.put(
            "jobs",
            "vlm",
            {
                "job_id": "vlm",
                "kind": "vlm_review",
                "target": {"target_id": "review"},
                "attempt_id": "attempt",
                "input_digest": "f" * 64,
                "work_id": None,
            },
        )
        ticket = reviews.create(tx, request, "vlm")
    worker = Caller(
        "worker",
        "worker",
        "project-test",
        job_id="vlm",
        attempt_id="attempt",
        input_digest="f" * 64,
        operations=("completeToolReview",),
    )
    finding = dict(
        schema_version="1.0",
        finding_id="finding",
        review_id="review",
        project_id="project-test",
        revision=1,
        target=request["targets"][0],
        observation="Visual only",
        recommendation="Move the object",
        evidence=[
            {
                "kind": "visual",
                "artifact_id": "image",
                "description": "Image observation, not measurement",
            }
        ],
        state="unreviewed",
    )
    completion = dict(
        schema_version="1.0",
        operation_id="complete",
        project_id="project-test",
        review_id="review",
        status="ready",
        findings=[finding],
    )
    return ticket, worker, completion


def test_review_complete_ticket_only_and_exact_target(reviews, annotation):
    ticket, worker, completion = create_review(reviews, annotation)
    assert set(ticket) == {"schema_version", "review_id", "job_id", "status"}
    bad = copy.deepcopy(completion)
    bad["findings"][0]["target"]["source_frame"] = 3
    assert_error("source_conflict", lambda: reviews.complete(worker, bad))
    ready = reviews.complete(worker, completion)
    assert ready == {**ticket, "status": "ready"}
    assert reviews.complete(worker, completion) == ready
    with store(reviews).transaction(write=False) as tx:
        assert tx.require("findings", "finding")["state"] == "unreviewed"
        assert "recommendation" not in json.dumps(tx.events()["events"])


def test_review_worker_binding_and_measurement(reviews, annotation):
    _, worker, completion = create_review(reviews, annotation)
    bad_worker = Caller(
        "worker",
        "wrong",
        "project-test",
        job_id="other",
        attempt_id="attempt",
        input_digest="f" * 64,
        operations=("completeToolReview",),
    )
    assert_error("scope_denied", lambda: reviews.complete(bad_worker, completion))
    completion["findings"][0]["evidence"][0]["kind"] = "measured"
    assert_error("source_conflict", lambda: reviews.complete(worker, completion))


def test_finding_atomic_submission_rejection_and_original_retry(reviews, owner, annotation):
    _, worker, completion = create_review(reviews, annotation)
    reviews.complete(worker, completion)
    rejected = reviews.reject(
        owner,
        "review",
        "finding",
        {
            "schema_version": "1.0",
            "operation_id": "reject",
            "project_id": "project-test",
            "expected_revision": 1,
        },
    )
    assert rejected["state_revision"] == 2
    approved = [
        {
            "finding_id": "finding",
            "finding_revision": 2,
            "approved_text": "My amended instruction",
            "target": completion["findings"][0]["target"],
        }
    ]
    with pytest.raises(RuntimeError), store(reviews).transaction() as tx:
        reviews.validate_and_submit_findings(tx, "request", approved)
        raise RuntimeError("Synthetic request acceptance rollback")
    with store(reviews).transaction() as tx:
        assert tx.require("findings", "finding")["state"] == "rejected"
        reviews.validate_and_submit_findings(tx, "request", approved)
        reviews.validate_and_submit_findings(tx, "request", approved)
        assert tx.require("findings", "finding")["revision"] == 3
        assert (
            tx.require("review.submissions", "finding")["approved"]["approved_text"]
            == "My amended instruction"
        )
        assert tx.require("findings", "finding")["recommendation"] == "Move the object"
    with store(reviews).transaction() as tx:
        assert_error(
            "revision_conflict",
            lambda: reviews.validate_and_submit_findings(tx, "another-request", approved),
        )


def test_http_owner_only_and_registered_operations(services, reviews):
    with TestClient(
        create_app(services.settings, services=services, routers=[router]),
        base_url=services.settings.origin,
    ) as client:
        assert client.get("/api/v1/projects/project-test/reviews").status_code == 401
        body = {
            "schema_version": "1.0",
            "operation_id": "pair-review",
            "client_nonce": "synthetic",
            "pairing_code": services.auth.issue_pairing(),
        }
        session = client.post(
            "/api/v1/owner/session",
            json=body,
            headers={"Origin": services.settings.origin, "Idempotency-Key": "pair-review"},
        )
        assert session.status_code == 201
        for path in ("reviews", "annotations", "selections"):
            response = client.get(f"/api/v1/projects/project-test/{path}")
            assert response.status_code == 200 and response.json()["items"] == []
    operations = {route.operation_id for route in router.routes}
    assert operations == {
        "listAnnotations",
        "saveAnnotation",
        "listSelections",
        "saveSelection",
        "listOwnerReviews",
        "listOwnerReviewFindings",
        "rejectReviewFinding",
    }
