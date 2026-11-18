"""Hermetic HTTP/store tests for 3D proxy-mapping selection and Save/Queue spatial annotation parity."""

import copy
import io
import json

import pytest
import test_worker_capabilities_and_publication as shared
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
from proscenium.api.dependencies import owner
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue.router import router as catalogue_router
from proscenium.copilot.context import Contexts
from proscenium.copilot.router import router as copilot_router
from proscenium.copilot.service import Copilot
from proscenium.reviews.routes import router as review_router
from proscenium.reviews.store import install as install_reviews
from test_request_targeting import add_version, context
from test_worker_capabilities_and_publication import PROJECT, SESSION

STAMP = "2026-11-18T12:00:00+02:00"
IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


class _NoopNative:
    def __init__(self):
        self.session = type("S", (), {"session_id": SESSION, "send": self._send})()

    async def _send(self, *args, **kwargs):
        return "msg-1"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    shared.offline.__wrapped__(monkeypatch)


def make_png(
    width: int = 160, height: int = 90, colour: tuple[int, int, int] = (32, 64, 96)
) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


def rigid_camera(width: int = 160, height: int = 90) -> dict:
    return dict(
        space="blender_world_m",
        kind="perspective",
        camera_to_world=[1, 0, 0, 0, 0, 0, -1, -10, 0, 1, 0, 2, 0, 0, 0, 1],
        projection=[0.5625, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1.002002, -0.2002002, 0, 0, -1, 0],
        aspect=width / height,
        near_m=0.1,
        far_m=100.0,
        source_camera_id=None,
        vertical_fov_degrees=90.0,
    )


def promote_proxy_pair(
    rt,
    version_id: str,
    digest: str,
    proxy_id: str,
    map_id: str,
    *,
    sample_frame: int = 1,
    include_map_id_in_json: bool = False,
) -> dict:
    artifacts = rt.services.extension("artifacts")
    glb = b"glTF\x02\x00\x00\x00\x14\x00\x00\x00\x02\x00\x00\x00JSON{}"
    proxy_art = artifacts.promote(
        PROJECT,
        proxy_id,
        glb,
        kind="proxy",
        purpose="inspection",
        media_type="model/gltf-binary",
    )
    payload = dict(
        schema_version="1.0",
        project_id=PROJECT,
        version_id=version_id,
        proxy_artifact_id=proxy_id,
        source_closure_digest=digest,
        sample_frame=sample_frame,
        space="proxy_world_m",
        nodes=[
            dict(
                node_path=[0],
                object_id="housing",
                instance_id="housing-1",
                display_name="Housing",
                world_matrix=IDENTITY,
            ),
            dict(
                node_path=[0, 0],
                object_id="collar",
                instance_id="collar-1",
                display_name="Collar",
                world_matrix=IDENTITY,
            ),
        ],
    )
    if include_map_id_in_json:
        payload["mapping_artifact_id"] = map_id
    map_art = artifacts.promote(
        PROJECT,
        map_id,
        json.dumps(payload).encode(),
        kind="proxy_map",
        purpose="metadata",
        media_type="application/json",
    )
    with rt.store.transaction() as tx:
        artifacts.put(tx, proxy_art)
        artifacts.put(tx, map_art)
        links = tx.get("version_links", version_id, required=False)
        existing = links["artifact_ids"] if links else []
        tx.put(
            "version_links",
            version_id,
            {"version_id": version_id, "artifact_ids": [*existing, proxy_id, map_id]},
            expected_revision=tx.revision("version_links", version_id) if links else None,
        )
    return payload


@pytest.fixture
def spatial_app(tmp_path):
    for rt in shared.runtime.__wrapped__(tmp_path):
        install_reviews(rt.services)
        rt.services.extensions["copilot"] = Copilot(rt.services, native=_NoopNative())
        rt.services.extensions["contexts"] = Contexts(rt.services)
        app = FastAPI()
        app.state.services = rt.services
        app.dependency_overrides[owner] = lambda: Caller("owner", "synthetic-owner")

        @app.exception_handler(DomainError)
        async def handle_domain_error(request: Request, exc: DomainError):
            return JSONResponse(exc.envelope(), status_code=exc.status)

        app.include_router(catalogue_router)
        app.include_router(review_router)
        app.include_router(copilot_router)
        with TestClient(app) as client:
            rt.client = client
            yield rt


def make_version(rt, item_id: str, version_id: str, doc: dict | None = None) -> dict:
    add_version(
        (None, rt.store, rt.root, None),
        item_id=item_id,
        version_id=version_id,
        kind="asset",
        document=doc or {"version": version_id},
    )
    with rt.store.transaction(write=False) as tx:
        return tx.require("versions", version_id)


def test_proxy_mapping_returns_mapping_id_for_legacy_json_and_supports_multi_map_selection(
    spatial_app,
):
    rt = spatial_app
    v2 = make_version(rt, "dish", "dish-v2")
    v3 = make_version(rt, "dish", "dish-v3")
    promote_proxy_pair(rt, "dish-v2", v2["source"]["digest"], "proxy-f1", "map-f1", sample_frame=1)
    promote_proxy_pair(
        rt, "dish-v2", v2["source"]["digest"], "proxy-f24", "map-f24", sample_frame=24
    )
    promote_proxy_pair(rt, "dish-v3", v3["source"]["digest"], "proxy-v3", "map-v3", sample_frame=1)

    # Default read selects the latest linked ready proxy map rather than failing with 422,
    # and populates mapping_artifact_id even though stored JSON omitted it.
    latest = rt.client.get(f"/api/v1/projects/{PROJECT}/versions/dish-v2/proxy-mapping")
    assert latest.status_code == 200, latest.text
    assert latest.json()["mapping_artifact_id"] == "map-f24"
    assert latest.json()["sample_frame"] == 24

    # Explicit mapping_artifact_id reopens the exact historical map on a multi-map version.
    pinned = rt.client.get(
        f"/api/v1/projects/{PROJECT}/versions/dish-v2/proxy-mapping",
        params={"mapping_artifact_id": "map-f1"},
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["mapping_artifact_id"] == "map-f1"
    assert pinned.json()["proxy_artifact_id"] == "proxy-f1"
    assert pinned.json()["sample_frame"] == 1

    # Foreign version map and missing map fail with exact domain statuses.
    foreign = rt.client.get(
        f"/api/v1/projects/{PROJECT}/versions/dish-v2/proxy-mapping",
        params={"mapping_artifact_id": "map-v3"},
    )
    assert foreign.status_code == 409
    assert foreign.json()["error"]["code"] == "source_conflict"

    missing = rt.client.get(
        f"/api/v1/projects/{PROJECT}/versions/dish-v2/proxy-mapping",
        params={"mapping_artifact_id": "map-does-not-exist"},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_spatial_annotation_save_and_queue_enforce_matching_unit_normal_and_fallback_rules(
    spatial_app,
):
    rt = spatial_app
    v2 = make_version(rt, "dish", "dish-v2")
    make_version(rt, "dish", "dish-v3")
    promote_proxy_pair(rt, "dish-v2", v2["source"]["digest"], "proxy-f1", "map-f1", sample_frame=1)
    promote_proxy_pair(
        rt, "dish-v2", v2["source"]["digest"], "proxy-f24", "map-f24", sample_frame=24
    )

    clean_resp = rt.client.put(
        f"/api/v1/projects/{PROJECT}/uploads/upload-clean-1",
        content=make_png(160, 90, (20, 30, 40)),
        headers={"Idempotency-Key": "upload-clean-1", "Content-Type": "image/png"},
    )
    assert clean_resp.status_code == 201
    clean_art = clean_resp.json()

    marked_resp = rt.client.put(
        f"/api/v1/projects/{PROJECT}/uploads/upload-marked-1",
        content=make_png(160, 90, (200, 50, 50)),
        headers={"Idempotency-Key": "upload-marked-1", "Content-Type": "image/png"},
    )
    assert marked_resp.status_code == 201
    marked_art = marked_resp.json()

    cam = rigid_camera(160, 90)
    valid_annotation = dict(
        annotation_id="ann-spatial-v2",
        saved_revision=None,
        target=dict(
            origin="proxy_capture",
            artifact_id=clean_art["artifact_id"],
            displayed_sha256=clean_art["sha256"],
            dimensions={"width": 160, "height": 90},
            item_id="dish",
            version_id="dish-v2",
            shot_id=None,
            render_job_id=None,
            source_frame=1,
            media_artifact_id=None,
        ),
        range={"start": 1, "end": 1},
        camera=cam,
        markups=[
            dict(
                id="pin-1",
                kind="pin",
                points=[[0.5, 0.5]],
                width_px=6,
                colour="#ff5555",
                text="Chamfer collar",
            )
        ],
        text="Chamfer the collar lip by 2 mm",
        spatial_hits=[
            dict(
                proxy_artifact_id="proxy-f1",
                mapping_artifact_id="map-f1",
                version_id="dish-v2",
                sample_frame=1,
                object_id="collar",
                instance_id="collar-1",
                space="blender_world_m",
                position=[0.0, 0.0, 2.0],
                normal=[0.0, -0.6, 0.8],
                camera=cam,
            )
        ],
        spatial_status="valid",
        markup_artifact_id=marked_art["artifact_id"],
    )

    # Non-unit normal MUST fail on BOTH Save and Queue (cannot save a broken pin that later fails Queue).
    bad_normal = copy.deepcopy(valid_annotation)
    bad_normal["spatial_hits"][0]["normal"] = [0.0, -2.0, 0.0]
    save_bad = rt.client.post(
        f"/api/v1/projects/{PROJECT}/annotations",
        json=dict(
            schema_version="1.0",
            operation_id="op-bad-normal",
            project_id=PROJECT,
            expected_revision=0,
            annotation=bad_normal,
        ),
        headers={"Idempotency-Key": "op-bad-normal"},
    )
    assert save_bad.status_code == 409

    ctx_bad = rt.client.post(
        f"/api/v1/projects/{PROJECT}/contexts",
        json=context(
            "ctx-bad-normal",
            item_id="dish",
            version_id="dish-v2",
            view_mode="3d_inspection",
            target=bad_normal["target"],
            camera=cam,
            range={"start": 1, "end": 1},
            selected_objects=[{"object_id": "collar", "instance_id": "collar-1"}],
            annotations=[{**bad_normal, "saved_revision": 1}],
        ),
        headers={"Idempotency-Key": "ctx-bad-normal"},
    )
    assert ctx_bad.status_code == 422

    # Valid spatial annotation saves cleanly and replays idempotently on lost ACK retry.
    save_body = dict(
        schema_version="1.0",
        operation_id="op-save-v2",
        project_id=PROJECT,
        expected_revision=0,
        annotation=valid_annotation,
    )
    save_ok = rt.client.post(
        f"/api/v1/projects/{PROJECT}/annotations",
        json=save_body,
        headers={"Idempotency-Key": "op-save-v2"},
    )
    assert save_ok.status_code == 200, save_ok.text
    assert save_ok.json()["state_revision"] == 1
    assert save_ok.json()["replayed"] is False

    replay = rt.client.post(
        f"/api/v1/projects/{PROJECT}/annotations",
        json=save_body,
        headers={"Idempotency-Key": "op-save-v2"},
    )
    assert replay.status_code == 200
    assert replay.json()["state_revision"] == 1
    assert replay.json()["replayed"] is True

    # Capturing context for dish-v2 succeeds even though dish-v3 is now current head and dish-v2 has two maps.
    saved_annotation = {**valid_annotation, "saved_revision": 1}
    ctx_ok = rt.client.post(
        f"/api/v1/projects/{PROJECT}/contexts",
        json=context(
            "ctx-valid-spatial",
            item_id="dish",
            version_id="dish-v2",
            request_target={
                "kind": "item",
                "version": {"item_id": "dish", "version_id": "dish-v2"},
            },
            view_mode="3d_inspection",
            target=saved_annotation["target"],
            camera=cam,
            range={"start": 1, "end": 1},
            selected_objects=[{"object_id": "collar", "instance_id": "collar-1"}],
            attachments=[
                {"artifact_id": clean_art["artifact_id"], "role": "reference"},
                {"artifact_id": marked_art["artifact_id"], "role": "markup"},
            ],
            annotations=[saved_annotation],
        ),
        headers={"Idempotency-Key": "ctx-valid-spatial"},
    )
    assert ctx_ok.status_code == 201, ctx_ok.text

    # Simulate proxy unavailability on disk; explicit image-only derived fallback still saves and captures pinned to dish-v2.
    artifacts = rt.services.extension("artifacts")
    artifacts.ready_path(PROJECT, "proxy-f1").unlink()
    fallback_ann = {
        **saved_annotation,
        "annotation_id": "ann-fallback-v2",
        "saved_revision": None,
        "spatial_hits": [],
        "spatial_status": "unavailable",
        "derived_from": "ann-spatial-v2",
    }
    fb_save = rt.client.post(
        f"/api/v1/projects/{PROJECT}/annotations",
        json=dict(
            schema_version="1.0",
            operation_id="op-fallback-v2",
            project_id=PROJECT,
            expected_revision=0,
            annotation=fallback_ann,
        ),
        headers={"Idempotency-Key": "op-fallback-v2"},
    )
    assert fb_save.status_code == 200, fb_save.text
    fb_ctx = rt.client.post(
        f"/api/v1/projects/{PROJECT}/contexts",
        json=context(
            "ctx-fallback-v2",
            item_id="dish",
            version_id="dish-v2",
            request_target={
                "kind": "item",
                "version": {"item_id": "dish", "version_id": "dish-v2"},
            },
            view_mode="keyframes",
            target=fallback_ann["target"],
            camera=cam,
            range={"start": 1, "end": 1},
            selected_objects=[],
            attachments=[
                {"artifact_id": clean_art["artifact_id"], "role": "reference"},
                {"artifact_id": marked_art["artifact_id"], "role": "markup"},
            ],
            annotations=[{**fallback_ann, "saved_revision": 1}],
        ),
        headers={"Idempotency-Key": "ctx-fallback-v2"},
    )
    assert fb_ctx.status_code == 201, fb_ctx.text
