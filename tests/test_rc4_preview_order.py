"""Preview ordering regressions using disposable records and no renderer or provider."""

import struct
import subprocess
from dataclasses import replace
from threading import RLock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from proscenium.api.errors import DomainError
from proscenium.auth import Auth
from proscenium.contracts import canonical, validate
from proscenium.copilot.outcomes import get_tool_outcome
from proscenium.copilot.tools import tool_failure
from proscenium.db import Store
from proscenium.previews import install, media, router, runner
from proscenium.previews.config import REVISION
from proscenium.previews.journal import UNSUPPORTED_BLENDER_FEATURE_REASON, atomic, sha
from proscenium.sources.common import fingerprint
from proscenium.sources.resolver import closure_digest
from test_rc4_retry_and_context import (
    DIMENSIONS,
    DRAFT,
    FPS,
    SCENE,
    SHOTS,
    block_process,
    invocation,
    publish,
    run_tool,
    seed_frames,
)
from test_request_targeting import context, request_record
from test_worker_capabilities_and_publication import PROJECT, SESSION, WORK
from test_worker_capabilities_and_publication import offline as offline
from test_worker_capabilities_and_publication import runtime as runtime

PASS_IDS = ["preview-" + prefix + "0" * 62 for prefix in ("a4", "21", "09", "3c", "74", "2b")]
OTHER = {"item_id": "scene-other", "version_id": "scene-other-v1"}
PINNED = {"kind": "item", "version": SCENE}
NEW_ITEM = {"kind": "new_item", "item_kind": "scene", "intent_id": "new-scene"}
ORIGIN = "request-one"


@pytest.fixture
def studio(runtime, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", block_process)
    services = runtime.services
    services.activation_lock = RLock()
    services.settings = replace(services.settings, execution_profiles=frozenset({"blender-cpu"}))
    services.auth = Auth(Store(runtime.root / ".proscenium/machine.sqlite3"))
    runtime.previews = install(services)
    runtime.version = publish(runtime, SCENE, "scene")
    return runtime


def request_context(studio, *, request_id=ORIGIN, **selection):
    snapshot = context(f"context-{request_id}", **selection)
    with studio.store.transaction() as tx:
        tx.put("contexts", snapshot["context_id"], snapshot, schema="ContextSnapshot")
        previous = tx.get("requests", request_id, required=False)
        tx.put(
            "requests",
            request_id,
            request_record(
                request_id,
                snapshot["context_id"],
                kind="queue" if request_id == ORIGIN else "steer",
            ),
            schema="RequestRecord",
            expected_revision=tx.revision("requests", request_id) if previous is not None else None,
        )


def spec(version, intent, shot, first, last, *, scene="Signal Lost", purpose="draft_motion"):
    return dict(
        intent_id=intent,
        shot_id=shot,
        purpose=purpose,
        source_closure_digest=version["source"]["digest"],
        scene_name=scene,
        camera=None,
        frames={"start": first, "end": last},
        fps=FPS,
        dimensions=DIMENSIONS,
        engine="CYCLES",
        device="CPU",
        samples=4,
        colour_management={"view_transform": "AgX", "look": "", "exposure": 0, "gamma": 1},
        renderer_revision=REVISION,
        review_output={
            "media_type": "image/png",
            "encoding": "view_transformed_srgb",
            "bit_depth": 8,
        },
    )


def historical_job(
    studio,
    identity,
    shot,
    first=1,
    last=72,
    *,
    scene="Signal Lost",
    purpose="draft_motion",
    kind="render",
    ref=SCENE,
):
    with studio.store.transaction() as tx:
        version = tx.require("versions", ref["version_id"])
        render = (
            spec(version, identity, shot, first, last, scene=scene, purpose=purpose)
            if kind == "render"
            else None
        )
        return studio.services.jobs.create(
            tx,
            job_id=identity,
            kind=kind,
            target={"target_id": ref["item_id"], "version": ref, "shot_id": shot},
            input_digest=fingerprint({"version": ref, "render_spec": render}),
            operation_id=f"seed-{identity}",
            work_id=WORK,
            render_spec=render,
            expected_frames=last - first + 1 if render else None,
        )


@pytest.fixture
def historical(studio):
    for identity, (shot, start, end, *_unused) in zip(PASS_IDS[:5], SHOTS, strict=True):
        historical_job(studio, identity, shot, start, end)
    historical_job(studio, PASS_IDS[-1], None, 1, 1, scene="SL04_Exterior", purpose="proxy")
    return studio


def get_version(studio):
    return studio.services.extension("catalogue").version(PROJECT, SCENE["version_id"])


def order_body(*ids, operation="order-one"):
    return dict(
        schema_version="1.0",
        operation_id=operation,
        project_id=PROJECT,
        version=SCENE,
        job_ids=list(ids),
    )


def order_invocation(studio, *ids, **kwargs):
    return invocation(studio, "set_preview_order", order_body(*ids), work=WORK, **kwargs)


def request_preview(studio, shot, *, operation=None, ref=SCENE):
    version = studio.services.extension("catalogue").version(PROJECT, ref["version_id"])
    body = dict(
        schema_version="1.0",
        operation_id=operation or f"request-{shot}",
        project_id=PROJECT,
        version=ref,
        spec=spec(version, f"intent-{shot}", shot, 1, 24),
    )
    call = invocation(studio, "request_preview", body, work=WORK)
    return studio.previews.request(studio.tools.caller(call, name="request_preview"), body)


def test_request_preview_rejects_unsupported_blender_features_before_job_admission(studio):
    request_context(studio, request_target=PINNED)
    version_id = SCENE["version_id"]
    with studio.store.transaction() as tx:
        version = tx.require("versions", version_id)
        source = version["source"]
        source["required_runtime"] = dict(
            application=" Blender ", version="4.5.16 LTS", features=["optix"]
        )
        source["digest"] = closure_digest(source)
        tx.put(
            "versions",
            version_id,
            version,
            schema="VersionRecord",
            expected_revision=tx.revision("versions", version_id),
        )
    with pytest.raises(DomainError) as error:
        request_preview(studio, "shot-test")
    assert (error.value.code, error.value.status, error.value.message) == (
        "capability_unavailable",
        501,
        UNSUPPORTED_BLENDER_FEATURE_REASON,
    )
    assert (
        UNSUPPORTED_BLENDER_FEATURE_REASON
        in tool_failure(error.value, "request_preview").text_result_for_llm
    )
    with studio.store.transaction(write=False) as tx:
        assert not tx.all("jobs") and not tx.all("preview_intents")


def order_events(studio):
    with studio.store.transaction(write=False) as tx:
        return [
            event
            for event in tx.events(limit=100)["events"]
            if event["event_type"] == "preview.order_changed"
        ]


def test_historical_signal_lost_uses_frame_order_and_proxy_last(historical):
    with historical.store.transaction(write=False) as tx:
        assert tx.get("version_links", SCENE["version_id"], required=False) is None
        assert [job["job_id"] for job in tx.all("jobs")] == sorted(PASS_IDS)
    assert get_version(historical)["preview_job_ids"] == PASS_IDS


@pytest.mark.parametrize("label_by", ["shot_id", "scene_name"])
def test_local_source_ranges_use_natural_casefolded_names(studio, label_by):
    for identity, label in (
        ("preview-a", "shot-10"),
        ("preview-b", "Shot-2"),
        ("preview-c", "shot-1"),
    ):
        historical_job(studio, identity, label if label_by == "shot_id" else None, scene=label)
    assert get_version(studio)["preview_job_ids"] == ["preview-c", "preview-b", "preview-a"]


def test_admission_order_preserves_inline_and_artefact_links(studio):
    historical_job(studio, "preview-historical", "shot-1")
    with studio.store.transaction() as tx:
        # Preserve inline publication order, including an archived job reference.
        version = tx.require("versions", SCENE["version_id"])
        version["preview_job_ids"] = ["inline-archived"]
        tx.put(
            "versions", SCENE["version_id"], version, schema="VersionRecord", expected_revision=0
        )
        tx.put(
            "version_links",
            SCENE["version_id"],
            {"artifact_ids": ["proxy-map"], "added_preview_job_ids": ["stale"]},
        )
    b, replayed = request_preview(studio, "shot-b")
    assert not replayed
    a, replayed = request_preview(studio, "shot-a")
    assert not replayed
    assert request_preview(studio, "shot-b", operation="same-intent-another-operation") == (b, True)
    assert request_preview(studio, "shot-b") == (b, True)
    current = get_version(studio)
    assert current["preview_job_ids"] == [
        "inline-archived",
        b["job_id"],
        a["job_id"],
        "preview-historical",
    ]
    assert current["artifact_ids"] == ["proxy-map"]


def proxy_completion(studio):
    job = historical_job(studio, "proxy-preview", None, 1, 1, purpose="proxy")
    root = studio.previews.root(job)
    proxy_id = "proxy-result"
    closure = studio.version["source"]
    atomic(root / "task.json", {"closure": closure, "proxy_artifact_id": proxy_id})
    gltf = b'{"asset":{"version":"2.0"},"scenes":[{}],"scene":0}'
    gltf += b" " * (-len(gltf) % 4)
    glb = struct.pack("<4sIII4s", b"glTF", 2, 20 + len(gltf), len(gltf), b"JSON") + gltf
    mapping = canonical(
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            version_id=SCENE["version_id"],
            proxy_artifact_id=proxy_id,
            source_closure_digest=closure["digest"],
            sample_frame=1,
            space="proxy_world_m",
            nodes=[],
        )
    )
    (root / "proxy.glb").write_bytes(glb)
    (root / "proxy-map.json").write_bytes(mapping)
    atomic(root / "proxy-ready.json", {"proxy_sha256": sha(glb), "mapping_sha256": sha(mapping)})
    return job, root, [proxy_id, proxy_id + "-map"]


def assembly_completion(studio, monkeypatch):
    source = historical_job(studio, "assembly-source", "shot-source", 1, 1)
    seed_frames(studio, source, 1, 1)
    identity = "encode-preview"
    frame_map = dict(
        schema_version="1.0",
        project_id=PROJECT,
        sequence_version_id=DRAFT["version_id"],
        fps=FPS,
        frame_count=1,
        blend_space="linear_rgb",
        frames=[
            dict(
                sequence_frame=1,
                contributors=[
                    dict(
                        version_id=SCENE["version_id"],
                        job_id=source["job_id"],
                        shot_id="shot-source",
                        source_frame=1,
                        weight={"numerator": 1, "denominator": 1},
                    )
                ],
            )
        ],
    )
    snapshot = dict(
        frame_map=frame_map,
        input_frames=[{"artifact_id": "assembly-source-f1", "dimensions": DIMENSIONS}],
    )
    digest = fingerprint(snapshot)
    with studio.store.transaction() as tx:
        job = studio.services.jobs.create(
            tx,
            job_id=identity,
            kind="encode",
            target={"target_id": DRAFT["item_id"], "version": DRAFT, "shot_id": None},
            input_digest=digest,
            operation_id="seed-encode",
            work_id=WORK,
        )
    root = studio.previews.root(job)
    atomic(root / "task.json", {"assembly": snapshot, "config": {}})
    mapping, video = canonical(frame_map), b"fixture video bytes"
    (root / "frame-map.json").write_bytes(mapping)
    (root / "delivery.mp4").write_bytes(video)
    atomic(
        root / "assembly-manifest.json",
        dict(
            input_digest=digest,
            video_sha256=sha(video),
            frame_map_sha256=sha(mapping),
        ),
    )

    def verify_fixture_video(path, fps, frame_count, dimensions, config):
        # FFprobe is the only substituted adapter. The publisher still verifies the
        # committed hashes, validates artefact records and writes the actual links.
        assert path.read_bytes() == video
        assert (fps, frame_count, dimensions) == (FPS, 1, DIMENSIONS)

    monkeypatch.setattr(media, "verify_video", verify_fixture_video)
    return job, root, [f"{identity}-{suffix}" for suffix in ("delivery", "frame-map", "manifest")]


@pytest.mark.parametrize("completion", ["proxy", "assembly"])
def test_preview_order_survives_artefact_publication(studio, monkeypatch, completion):
    ref = SCENE if completion == "proxy" else DRAFT
    if completion == "assembly":
        publish(studio, DRAFT, "sequence")
    version_id = ref["version_id"]
    request_context(studio, request_target={"kind": "item", "version": ref})
    with studio.store.transaction() as tx:
        tx.put("version_links", version_id, {"artifact_ids": ["existing-artefact"]})
    job_b, _ = request_preview(studio, "shot-b", ref=ref)
    job_a, _ = request_preview(studio, "shot-a", ref=ref)
    call = invocation(
        studio,
        "set_preview_order",
        {**order_body(job_a["job_id"]), "version": ref},
        work=WORK,
    )
    assert run_tool(studio, call)[0] == "success"
    with studio.store.transaction(write=False) as tx:
        before = tx.require("version_links", version_id)
    assert before["added_preview_job_ids"] == [job_b["job_id"], job_a["job_id"]]
    assert before["ordered_preview_job_ids"] == [job_a["job_id"], job_b["job_id"]]

    if completion == "proxy":
        job, root, ids = proxy_completion(studio)
        publish_media = runner.publish_proxy
    else:
        job, root, ids = assembly_completion(studio, monkeypatch)
        publish_media = runner.publish_assembly
    assert publish_media(studio.previews, job, root) == ids
    with studio.store.transaction(write=False) as tx:
        assert tx.require("version_links", version_id) == {
            **before,
            "artifact_ids": ["existing-artefact", *ids],
        }
        assert all(tx.require("artifacts", identity)["state"] == "ready" for identity in ids)
        revision = tx.revision("version_links", version_id)
    view = studio.services.extension("catalogue").version(PROJECT, version_id)
    assert view["artifact_ids"] == ["existing-artefact", *ids]
    trailing = [job["job_id"]] if completion == "proxy" else []
    assert view["preview_job_ids"] == [*before["ordered_preview_job_ids"], *trailing]
    assert publish_media(studio.previews, job, root) == ids
    with studio.store.transaction(write=False) as tx:
        assert tx.revision("version_links", version_id) == revision


def test_http_order_preserves_omitted_jobs_and_replays_after_new_admission(historical):
    studio = historical
    request_context(studio, request_target=PINNED)
    requested = [PASS_IDS[i] for i in (4, 0, 2, 1, 3)]
    call = order_invocation(studio, *requested)
    caller = studio.tools.caller(call, name="set_preview_order", admit=False)
    token = studio.services.auth.issue_capability(caller)
    app = FastAPI()
    app.state.services = studio.services
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(error.envelope(), status_code=error.status)

    route = "/internal/v1/previews/order"
    headers = {
        "Idempotency-Key": call.arguments["operation_id"],
        "Authorization": f"Bearer {token}",
    }
    with TestClient(app) as client:
        assert client.post(route, json=call.arguments).status_code == 401
        response = client.post(route, json=call.arguments, headers=headers)
        assert response.status_code == 200
        result = response.json()
        assert result == get_version(studio)
        assert result["preview_job_ids"] == [*requested, PASS_IDS[-1]]
        late, _ = request_preview(studio, "shot-0")
        assert get_version(studio)["preview_job_ids"] == [*requested, PASS_IDS[-1], late["job_id"]]
        studio.services.gate.close(PROJECT)
        repeated = client.post(route, json=call.arguments, headers=headers)
        assert (repeated.status_code, repeated.json()) == (200, result)
        conflict = client.post(
            route, json={**call.arguments, "job_ids": requested[::-1]}, headers=headers
        )
        assert (conflict.status_code, conflict.json()["error"]["code"]) == (
            409,
            "idempotency_conflict",
        )
    (event,) = order_events(studio)
    validate("Event", event)
    assert event["payload"] == result
    assert (event["actor"], event["request_id"], event["work_id"], event["native_session_id"]) == (
        {"kind": "tool", "id": caller.subject_id},
        ORIGIN,
        WORK,
        SESSION,
    )


def test_copilot_partial_order_records_outcome_and_replays_after_invocation_settles(historical):
    studio = historical
    request_context(studio, request_target=PINNED)
    requested = [PASS_IDS[4], PASS_IDS[2]]
    call = order_invocation(studio, *requested)
    caller = studio.tools.caller(call, name="set_preview_order")
    assert caller.invocation_id is not None
    with studio.store.transaction(write=False) as tx:
        jobs_before = tx.all("jobs")
    status, result = run_tool(studio, call)
    assert status == "success"
    assert result["preview_job_ids"] == [
        *requested,
        PASS_IDS[0],
        PASS_IDS[1],
        PASS_IDS[3],
        PASS_IDS[5],
    ]
    with studio.store.transaction() as tx:
        assert tx.all("jobs") == jobs_before
        assert get_tool_outcome(tx, caller.invocation_id)["job_ids"] == sorted(requested)
        record = tx.require("invocations", caller.invocation_id)
        tx.put(
            "invocations",
            caller.invocation_id,
            {**record, "state": "completed"},
            expected_revision=0,
        )
    studio.services.gate.close(PROJECT)
    assert run_tool(studio, call) == ("success", result)
    assert [event["payload"] for event in order_events(studio)] == [result]


@pytest.mark.parametrize("bad_job", ["wrong-version", "non-render", "missing"])
def test_rejects_jobs_outside_target_render_membership(historical, bad_job):
    studio = historical
    request_context(studio, request_target=PINNED)
    publish(studio, OTHER, "scene")
    historical_job(studio, "wrong-version", "other-shot", ref=OTHER)
    historical_job(studio, "non-render", None, kind="encode")
    call = order_invocation(studio, PASS_IDS[4], bad_job)
    with pytest.raises(DomainError) as denied:
        studio.previews.set_order(
            studio.tools.caller(call, name="set_preview_order"), call.arguments
        )
    assert (denied.value.status, denied.value.code) == (409, "source_conflict")
    status, result = run_tool(studio, call)
    assert status == "failure"
    assert (
        result["message"] == "All ordered preview jobs must be render passes on the target version"
    )
    assert get_version(studio)["preview_job_ids"] == PASS_IDS
    assert not order_events(studio)


@pytest.mark.parametrize(
    "selection",
    [
        {"request_target": PINNED, "execution_scope": "planning_only"},
        {"request_target": {"kind": "item", "version": OTHER}},
        {"request_target": {"kind": "shot", "storyboard_version": OTHER, "shot_id": "shot-1"}},
        {"request_target": NEW_ITEM},
        {"reference_versions": [SCENE]},
    ],
)
def test_denies_out_of_scope_requests(historical, selection):
    studio = historical
    request_context(studio, **selection)
    call = order_invocation(studio, PASS_IDS[4])
    with pytest.raises(DomainError) as denied:
        studio.previews.set_order(
            studio.tools.caller(call, name="set_preview_order"), call.arguments
        )
    assert (denied.value.status, denied.value.code) == (403, "scope_denied")
    assert not order_events(studio)


@pytest.mark.parametrize("state", ["completed", "missing", "unbound"])
def test_new_effect_needs_active_invocation(historical, state):
    studio = historical
    request_context(studio, request_target=PINNED)
    call = order_invocation(studio, PASS_IDS[4])
    caller = studio.tools.caller(call, name="set_preview_order")
    assert caller.invocation_id is not None
    if state == "completed":
        with studio.store.transaction() as tx:
            record = tx.require("invocations", caller.invocation_id)
            tx.put(
                "invocations", caller.invocation_id, {**record, "state": state}, expected_revision=0
            )
    else:
        caller = replace(caller, invocation_id="unknown-invocation" if state == "missing" else None)
    with pytest.raises(DomainError) as denied:
        studio.previews.set_order(caller, call.arguments)
    assert (denied.value.status, denied.value.code) == (409, "attribution_unavailable")
    assert not order_events(studio)


def test_new_item_work_can_reorder_its_own_publication_using_steer_context(historical):
    studio = historical
    request_context(studio, request_target=PINNED, execution_scope="planning_only")
    request_context(studio, request_id="steer-one", request_target=NEW_ITEM)
    with studio.store.transaction() as tx:
        tx.put("publications", studio.version["publication_id"], {"work_id": WORK})
    call = order_invocation(studio, PASS_IDS[4], context_request="steer-one")
    result, replayed = studio.previews.set_order(
        studio.tools.caller(call, name="set_preview_order"), call.arguments
    )
    assert not replayed
    assert result["preview_job_ids"] == [PASS_IDS[4], *PASS_IDS[:4], PASS_IDS[5]]
    assert order_events(studio)[0]["request_id"] == ORIGIN


def test_steer_item_pin_overrides_same_work_publication(historical):
    studio = historical
    publish(studio, OTHER, "scene")
    request_context(studio, request_target=NEW_ITEM)
    request_context(
        studio, request_id="steer-one", request_target={"kind": "item", "version": OTHER}
    )
    with studio.store.transaction() as tx:
        tx.put("publications", studio.version["publication_id"], {"work_id": WORK})
    call = order_invocation(studio, PASS_IDS[4], context_request="steer-one")
    with pytest.raises(DomainError) as denied:
        studio.previews.set_order(
            studio.tools.caller(call, name="set_preview_order"), call.arguments
        )
    assert (denied.value.status, denied.value.code) == (403, "scope_denied")
    assert get_version(studio)["preview_job_ids"] == PASS_IDS
    assert not order_events(studio)


@pytest.mark.parametrize("semantic_validity", [True, False])
def test_legacy_sequence_scope_requires_a_valid_contributor(historical, semantic_validity):
    studio = historical
    first, last = SHOTS[0][1:3]
    document = dict(
        schema_version="1.0",
        project_id=PROJECT,
        sequence_version_id=DRAFT["version_id"],
        fps=FPS,
        frame_count=last,
        silent=True,
        entries=[
            dict(
                entry_id="entry-one",
                shot_id=SHOTS[0][0],
                version_id=SCENE["version_id"],
                job_id=PASS_IDS[0],
                source_frames={"start": first, "end": last},
                sequence_frames={"start": first if semantic_validity else first + 1, "end": last},
                transform="identity",
                mapping_artifact_id=None,
            )
        ],
    )
    publish(studio, DRAFT, "sequence", document, dependencies=[SCENE])
    request_context(studio, **DRAFT, view_mode="animation")
    call = order_invocation(studio, PASS_IDS[4])
    if semantic_validity:
        assert (
            studio.previews.set_order(
                studio.tools.caller(call, name="set_preview_order"), call.arguments
            )[0]["preview_job_ids"][0]
            == PASS_IDS[4]
        )
    else:
        with pytest.raises(DomainError, match="Pin the target version"):
            studio.previews.set_order(
                studio.tools.caller(call, name="set_preview_order"), call.arguments
            )
        assert not order_events(studio)
