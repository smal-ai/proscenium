"""Disposable recovery and assembly regressions. No Blender or provider execution."""

import asyncio
import json
import struct
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from copilot.tools import ToolInvocation
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from proscenium.api.dependencies import owner
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue.validation import sequence_map
from proscenium.contracts import canonical, validate
from proscenium.copilot.capabilities import validate_authored_document
from proscenium.copilot.context import Contexts
from proscenium.copilot.outcomes import get_tool_outcome
from proscenium.copilot.router import router as copilot_router
from proscenium.copilot.state import key
from proscenium.copilot.tools import tool_failure
from proscenium.db import Store
from proscenium.jobs.resources import Resources
from proscenium.previews import install as install_previews
from proscenium.previews import runner
from proscenium.previews.config import REVISION, Rendering
from proscenium.previews.media import verify_video
from proscenium.previews.service import worker_caller
from proscenium.reviews.references import References
from proscenium.sources.common import fingerprint, sha
from proscenium.sources.read_only import read_authored_json
from proscenium.sources.resolver import Resolver, closure_digest
from proscenium.workflows import install as install_workflows
from test_request_targeting import STAMP, context, request_record
from test_vlm_critic_workflow import make_png
from test_worker_capabilities_and_publication import PROJECT, SESSION, WORK, publication
from test_worker_capabilities_and_publication import offline as offline
from test_worker_capabilities_and_publication import runtime as runtime

SCENE = {"item_id": "scene-signal-lost-animation", "version_id": "signal-lost-animation-v1"}
DRAFT = {
    "item_id": "sequence-signal-lost-animation-draft",
    "version_id": "signal-lost-animation-draft-v1",
}
ASSET_V1 = {"item_id": "asset-kit", "version_id": "asset-kit-v1"}
ASSET_V3 = {"item_id": "asset-kit", "version_id": "asset-kit-v3"}
SHOTS = (
    ("sl-01-dormant-macro", 1, 72, 28, "cancelled"),
    ("sl-02-conduit", 73, 168, 57, "failed"),
    ("sl-03-console-wake", 169, 240, 53, "failed"),
    ("sl-04-exterior-reveal", 241, 360, 87, "failed"),
    ("sl-05-title-lockup", 361, 432, 72, "succeeded"),
)
MACRO, TITLE = SHOTS[0][0], SHOTS[-1][0]
PROXY = "signal-lost-proxy"
ASSET_RENDER = "asset-kit-keyframe"
NEW_WORK = "work-new"
NEW_REQUEST = "request-new"
SCENE_TARGET = {"kind": "item", "version": SCENE}
DRAFT_VIEW = {**DRAFT, "view_mode": "animation", "request_target": None}
EARLIER_RENDER = "This job belongs to an earlier request. No render was started."
EARLIER_JOB = "This job belongs to an earlier request. No job was started."
PLANNING = "Retry is unavailable while execution_scope is planning_only. No render was started."
FPS = {"numerator": 24, "denominator": 1}
DIMENSIONS = {"width": 64, "height": 64}


def block_process(*args, **kwargs):
    pytest.fail("Signal Lost regressions must not spawn a native process")


def publish(runtime, ref, kind, document=None, dependencies=()):
    # Install a real immutable source closure, not a mock of the retry probe's JSON reader.
    data = canonical(document if document is not None else ref)
    digest = sha(data)
    artifact_id = "retained-" + digest
    path = runtime.root / f".proscenium/retained/{digest}/content"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    source = dict(
        entry_path="source.json",
        files=[
            dict(
                destination="source.json",
                origin=dict(kind="retained", artifact_id=artifact_id, sha256=digest),
            )
        ],
        dependencies=list(dependencies),
        required_runtime=dict(application="json", version="1", features=[]),
        digest="0" * 64,
    )
    source["digest"] = closure_digest(source)
    version = dict(
        schema_version="1.0",
        project_id=PROJECT,
        **ref,
        kind=kind,
        name=ref["item_id"],
        publication_id=f"publication-{ref['version_id']}",
        source_commit="a" * 40,
        source=source,
        artifact_ids=[],
        preview_requests=[],
        preview_job_ids=[],
        created_at=STAMP,
        actor=dict(kind="tool", id="fixture"),
    )
    with runtime.store.transaction() as tx:
        item = tx.get("items", ref["item_id"], required=False)
        revision = item["state_revision"] if item else None
        tx.put("retained_sources", artifact_id, {"sha256": digest})
        tx.put("versions", ref["version_id"], version, schema="VersionRecord")
        tx.put(
            "items",
            ref["item_id"],
            dict(
                schema_version="1.0",
                project_id=PROJECT,
                item_id=ref["item_id"],
                kind=kind,
                name=ref["item_id"],
                current_version_id=ref["version_id"],
                selected_version_id=None,
                state_revision=0 if revision is None else revision + 1,
            ),
            schema="ItemRecord",
            expected_revision=revision,
        )
    return version


def seed_job(
    runtime,
    identity,
    ref,
    state,
    *,
    kind="render",
    frames=None,
    purpose="draft_motion",
    shot_id=None,
):
    services = runtime.services
    with runtime.store.transaction() as tx:
        source = tx.require("versions", ref["version_id"])["source"]
        spec = None
        if frames:
            first, last = frames
            spec = dict(
                intent_id=identity,
                shot_id=shot_id,
                purpose=purpose,
                source_closure_digest=source["digest"],
                scene_name="Signal Lost",
                camera=None,
                frames=dict(start=first, end=last),
                fps=FPS,
                dimensions=DIMENSIONS,
                engine="CYCLES",
                device="CPU",
                samples=4,
                colour_management=dict(view_transform="AgX", look="", exposure=0, gamma=1),
                renderer_revision=REVISION,
                review_output=dict(
                    media_type="image/png", encoding="view_transformed_srgb", bit_depth=8
                ),
            )
        job = services.jobs.create(
            tx,
            job_id=identity,
            kind=kind,
            target=dict(target_id=ref["item_id"], version=ref, shot_id=shot_id),
            input_digest=fingerprint(dict(version=ref, render_spec=spec)),
            operation_id=f"initial-{identity}",
            work_id=WORK,
            render_spec=spec,
            expected_frames=last - first + 1 if spec else None,
        )
        services.jobs.freeze_inputs(
            tx, identity, dict(source=source, configuration=services.settings.view(), work_id=WORK)
        )
    # Retain media in the store before recording terminal evidence, as the real adapter does.
    return job, state


def seed_frames(runtime, job, first, count):
    artifacts = runtime.services.extension("artifacts")
    version_id = job["target"]["version"]["version_id"]
    with runtime.store.transaction() as tx:
        for frame in range(first, first + count):
            identity = f"{job['job_id']}-f{frame}"
            data = make_png((frame % 256, frame // 256, 140))
            artifact = artifacts.promote(
                PROJECT,
                identity,
                data,
                kind="image",
                purpose="draft_motion",
                media_type="image/png",
                metadata={"source_version_id": version_id},
            )
            artifacts.put(tx, artifact)
            receipt = dict(
                **{
                    k: job[k]
                    for k in (
                        "schema_version",
                        "project_id",
                        "job_id",
                        "attempt_id",
                        "input_digest",
                    )
                },
                receipt_id=f"{job['attempt_id']}-f{frame}",
                artifact_id=identity,
                version_id=version_id,
                shot_id=job["target"]["shot_id"],
                source_frame=frame,
                dimensions=DIMENSIONS,
                output_path=f"frames/{frame:08d}.png",
                sha256=artifact["sha256"],
            )
            tx.put("frames", receipt["receipt_id"], receipt, schema="FrameReceipt")


def proxy_artifacts(runtime, version):
    gltf = b'{"asset":{"version":"2.0"},"scenes":[{}],"scene":0}'
    gltf += b" " * (-len(gltf) % 4)
    glb = struct.pack("<4sIII4s", b"glTF", 2, 20 + len(gltf), len(gltf), b"JSON") + gltf
    mapping = canonical(
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            version_id=version["version_id"],
            proxy_artifact_id="signal-lost-glb",
            mapping_artifact_id="signal-lost-map",
            source_closure_digest=version["source"]["digest"],
            sample_frame=300,
            space="proxy_world_m",
            nodes=[],
        )
    )
    artifacts = runtime.services.extension("artifacts")
    with runtime.store.transaction() as tx:
        for identity, data, kind, media in (
            ("signal-lost-glb", glb, "proxy", "model/gltf-binary"),
            ("signal-lost-map", mapping, "proxy_map", "application/json"),
        ):
            artifacts.put(
                tx,
                artifacts.promote(
                    PROJECT,
                    identity,
                    data,
                    kind=kind,
                    purpose="inspection" if kind == "proxy" else "metadata",
                    media_type=media,
                    metadata={"source_version_id": version["version_id"]},
                ),
            )
    return ["signal-lost-glb", "signal-lost-map"]


def settle(runtime, job_and_state, artefacts=None):
    job, state = job_and_state
    runtime.previews.state(job, "starting")
    runtime.previews.state(job, "running")
    if state == "cancelled":
        runtime.previews.state(job, "cancel_requested")
    return runtime.previews.state(
        job,
        state,
        evidence="Offline terminal receipt",
        exit_code=0 if state == "succeeded" else -9,
        extra_artifacts=artefacts,
        error_code="runtime_timeout" if state == "failed" else None,
    )


def set_work_state(runtime, identity, state):
    with runtime.store.transaction() as tx:
        work = tx.require("work", identity)
        work.update(state=state, state_revision=work["state_revision"] + 1)
        tx.put(
            "work",
            identity,
            work,
            schema="WorkRecord",
            expected_revision=tx.revision("work", identity),
        )


@pytest.fixture
def signal_lost(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", block_process)
    services = runtime.services
    services.settings = replace(services.settings, execution_profiles=frozenset({"blender-cpu"}))
    services.resources = Resources(Store(tmp_path / "claims.sqlite3"))
    services.extensions["sources"] = SimpleNamespace(
        authored=lambda project_id, version: read_authored_json(services, project_id, version),
        materialise=lambda project_id, version_id: runtime.root,
    )
    services.extension("catalogue").sources = services.extension("sources")
    runtime.contexts = Contexts(services)
    services.extensions["copilot"] = SimpleNamespace(contexts=runtime.contexts)
    runtime.previews = install_previews(services)
    workflows = install_workflows(services)
    scene = publish(runtime, SCENE, "scene")
    publish(runtime, ASSET_V1, "asset")
    publish(runtime, ASSET_V3, "asset")
    entries = []
    runtime.jobs = {}
    for identity, first, last, retained, state in SHOTS:
        planned = seed_job(runtime, identity, SCENE, state, frames=(first, last), shot_id=identity)
        seed_frames(runtime, planned[0], first, retained)
        runtime.jobs[identity] = settle(runtime, planned)
        entries.append(
            dict(
                entry_id=f"entry-{identity}",
                shot_id=identity,
                version_id=SCENE["version_id"],
                job_id=identity,
                source_frames=dict(start=first, end=last),
                sequence_frames=dict(start=first, end=last),
                transform="identity",
                mapping_artifact_id=None,
            )
        )
    runtime.sequence = dict(
        schema_version="1.0",
        project_id=PROJECT,
        sequence_version_id=DRAFT["version_id"],
        fps=FPS,
        frame_count=432,
        silent=True,
        entries=entries,
    )
    validate("SequenceMap", runtime.sequence)
    publish(runtime, DRAFT, "sequence", runtime.sequence, dependencies=[SCENE])
    runtime.jobs[PROXY] = settle(
        runtime,
        seed_job(
            runtime, PROXY, SCENE, "succeeded", frames=(300, 300), purpose="proxy", shot_id=PROXY
        ),
        proxy_artifacts(runtime, scene),
    )
    runtime.jobs[ASSET_RENDER] = settle(
        runtime,
        seed_job(
            runtime,
            ASSET_RENDER,
            ASSET_V1,
            "failed",
            frames=(1, 1),
            purpose="keyframe",
            shot_id=ASSET_RENDER,
        ),
    )
    for kind in ("encode", "unity"):
        identity = f"{kind}-earlier"
        runtime.jobs[identity] = settle(
            runtime, seed_job(runtime, identity, DRAFT, "failed", kind=kind)
        )
    set_work_state(runtime, WORK, "completed")
    try:
        yield runtime
    finally:
        workflows.close_idle()


def records(runtime, family):
    with runtime.store.transaction(write=False) as tx:
        return tx.all(family)


def new_turn(runtime, selection):
    snapshot, _ = runtime.contexts.capture(context("retry-context", **selection))
    with runtime.store.transaction() as tx:
        original = tx.require("work", WORK)
        tx.put(
            "work",
            NEW_WORK,
            {
                **original,
                "work_id": NEW_WORK,
                "originating_request_id": NEW_REQUEST,
                "state": "running",
                "state_revision": 0,
            },
            schema="WorkRecord",
        )
        request = request_record(NEW_REQUEST, snapshot["context_id"])
        request["receipt"]["work_id"] = NEW_WORK
        tx.put("requests", NEW_REQUEST, request, schema="RequestRecord")


def invocation(runtime, name, body, *, work=NEW_WORK, call=None, context_request=None):
    call = call or f"call-{body['operation_id']}"
    identity = key(SESSION, "tool", call)
    with runtime.store.transaction() as tx:
        if tx.get("invocations", identity, required=False) is None:
            tx.put(
                "invocations",
                identity,
                dict(
                    invocation_id=identity,
                    agent_id=None,
                    session_id=SESSION,
                    work_id=work,
                    request_id=tx.require("work", work)["originating_request_id"],
                    context_request_id=context_request,
                    owner_epoch="epoch",
                    state="running",
                ),
            )
    return ToolInvocation(
        session_id=SESSION, tool_call_id=call, tool_name=name, arguments=body, agent_id=None
    )


def run_tool(runtime, invocation):
    result = asyncio.run(runtime.tools.invoke(invocation.tool_name, invocation))
    return result.result_type, json.loads(result.text_result_for_llm)


def retry(runtime, identity, **kwargs):
    return invocation(
        runtime,
        "retry_job",
        dict(
            schema_version="1.0",
            operation_id=f"retry-{identity}",
            project_id=PROJECT,
            job_id=identity,
            expected_revision=runtime.jobs[identity]["state_revision"],
        ),
        **kwargs,
    )


def rejection(runtime, identity, code, message=None, *, status=None, stale=False, **kwargs):
    call = retry(runtime, identity, **kwargs)
    if stale:
        call.arguments["expected_revision"] = 0
    attempts = records(runtime, "attempts")
    audit = records(runtime, "job_retry_authorisations")
    result_type, error = run_tool(runtime, call)
    assert result_type == "failure" and error["error"] == code
    assert "prepare_image_generation" not in canonical(error).decode()
    if message:
        assert error["message"] == message
    if status:
        with pytest.raises(DomainError) as caught:
            runtime.services.jobs.retry(runtime.tools.caller(call), call.arguments)
        assert (caught.value.status, caught.value.code, caught.value.message) == (
            status,
            code,
            message,
        )
    assert records(runtime, "attempts") == attempts
    assert records(runtime, "job_retry_authorisations") == audit


def test_current_view_captures_the_unassembled_297_of_432_frame_draft(signal_lost):
    studio = signal_lost
    snapshot = context("no-movie", **DRAFT_VIEW)
    assert studio.contexts.capture(snapshot) == (snapshot, False)
    assert snapshot["target"] is None
    frames = records(studio, "frames")
    assert sum(studio.jobs[identity]["expected_frames"] for identity, *_ in SHOTS) == 432
    assert len(frames) == 297
    assert sum(last - first + 1 - retained for _, first, last, retained, _ in SHOTS) == 135
    for identity, first, last, retained, state in SHOTS:
        job = studio.jobs[identity]
        assert (job["state"], job["completed_frames"], job["expected_frames"]) == (
            state,
            retained,
            last - first + 1,
        )
        assert sorted(f["source_frame"] for f in frames if f["job_id"] == identity) == list(
            range(first, first + retained)
        )
    proxy = studio.jobs[PROXY]
    assert (proxy["state"], proxy["completed_frames"], proxy["render_spec"]["frames"]) == (
        "succeeded",
        0,
        {"start": 300, "end": 300},
    )
    artifacts = studio.services.extension("artifacts")
    assert {artifacts.get(PROJECT, a)["kind"] for a in proxy["artifact_ids"]} == {
        "proxy",
        "proxy_map",
    }
    assert all(artifacts.ready_path(PROJECT, a).is_file() for a in proxy["artifact_ids"])
    assert (
        studio.services.extension("catalogue").version(PROJECT, DRAFT["version_id"])["artifact_ids"]
        == []
    )


@pytest.mark.parametrize(
    ("selection", "message"),
    [
        ({"request_target": {"kind": "item", "version": ASSET_V3}}, EARLIER_RENDER),
        ({"request_target": {"kind": "project"}}, EARLIER_RENDER),
        ({"request_target": None}, EARLIER_RENDER),
        ({"request_target": {"kind": "project"}, "reference_versions": [SCENE]}, EARLIER_RENDER),
        ({**SCENE, "request_target": None}, EARLIER_RENDER),
        ({**DRAFT_VIEW, "execution_scope": "planning_only"}, PLANNING),
    ],
)
def test_unpinned_and_planning_turns_cannot_recover(signal_lost, selection, message):
    new_turn(signal_lost, selection)
    rejection(signal_lost, MACRO, "scope_denied", message, status=403, stale=True)


@pytest.mark.parametrize("kind", ["encode", "unity"])
def test_cross_turn_non_render_retry_is_denied(signal_lost, kind):
    new_turn(signal_lost, {"request_target": SCENE_TARGET})
    rejection(signal_lost, f"{kind}-earlier", "scope_denied", EARLIER_JOB, status=403)


@pytest.mark.parametrize("state", ["running", "awaiting_resume"])
def test_non_terminal_origin_is_not_recoverable(signal_lost, state):
    new_turn(signal_lost, {"request_target": SCENE_TARGET})
    set_work_state(signal_lost, WORK, state)
    rejection(signal_lost, MACRO, "scope_denied", EARLIER_RENDER, status=403)


def assert_direct_retry_denied(studio, caller, body):
    before = {
        name: records(studio, name) for name in ("jobs", "attempts", "job_retry_authorisations")
    }
    with pytest.raises(DomainError) as denied:
        studio.services.jobs.retry(caller, body)
    assert (denied.value.status, denied.value.code, denied.value.message) == (
        403,
        "scope_denied",
        EARLIER_RENDER,
    )
    assert {name: records(studio, name) for name in before} == before


@pytest.mark.parametrize(
    "invalid", ["no-id", "missing", "completed", "failed", "stale-epoch", "wrong-work"]
)
def test_direct_cross_turn_retry_requires_a_live_invocation(signal_lost, invalid):
    studio = signal_lost
    new_turn(studio, {"request_target": SCENE_TARGET})
    call = retry(studio, MACRO)
    caller = studio.tools.caller(call)
    identity = caller.invocation_id
    assert identity is not None
    with studio.store.transaction(write=False) as tx:
        live = tx.require("invocations", identity)

    def save_invocation(record):
        with studio.store.transaction() as tx:
            tx.put(
                "invocations",
                identity,
                record,
                expected_revision=tx.revision("invocations", identity),
            )

    changes = {
        "completed": {"state": "completed"},
        "failed": {"state": "failed"},
        "stale-epoch": {"owner_epoch": "previous-epoch"},
        "wrong-work": {"work_id": WORK},
    }.get(invalid)
    if changes:
        bad_caller = caller
        save_invocation({**live, **changes})
    else:
        bad_caller = replace(
            caller, invocation_id=None if invalid == "no-id" else "unknown-invocation"
        )
    assert_direct_retry_denied(studio, bad_caller, call.arguments)
    with studio.store.transaction(write=False) as tx:
        assert studio.previews.can_retry_cross_work(tx, bad_caller, studio.jobs[MACRO]) is None

    save_invocation({**live, "state": "admitted"})
    recovered, replayed = studio.services.jobs.retry(caller, call.arguments)
    assert (recovered["attempt_number"], replayed) == (2, False)
    if changes:
        save_invocation({**live, **changes})
    attempts = records(studio, "attempts")
    audit = records(studio, "job_retry_authorisations")
    assert studio.services.jobs.retry(bad_caller, call.arguments) == (recovered, True)
    assert records(studio, "attempts") == attempts
    assert records(studio, "job_retry_authorisations") == audit


def test_direct_same_turn_retry_rejects_an_invalid_supplied_invocation(signal_lost):
    studio = signal_lost
    set_work_state(studio, WORK, "awaiting_user")
    call = retry(studio, MACRO, work=WORK)
    caller = studio.tools.caller(call)
    assert_direct_retry_denied(
        studio, replace(caller, invocation_id="unknown-invocation"), call.arguments
    )
    recovered, replayed = studio.services.jobs.retry(
        replace(caller, invocation_id=None), call.arguments
    )
    assert (recovered["attempt_number"], replayed) == (2, False)


@pytest.mark.parametrize("origin", [None, "missing-work"])
def test_direct_cross_turn_retry_requires_an_existing_origin(signal_lost, origin):
    studio = signal_lost
    new_turn(studio, {"request_target": SCENE_TARGET})
    with studio.store.transaction() as tx:
        job = tx.require("jobs", MACRO)
        revision = tx.revision("jobs", MACRO)
        job.update(work_id=origin, state_revision=revision + 1)
        if origin is None:
            job["owner_operation_id"] = "owner-render"
        tx.put("jobs", MACRO, job, schema="JobRecord", expected_revision=revision)
    studio.jobs[MACRO] = job
    call = retry(studio, MACRO)
    assert_direct_retry_denied(studio, studio.tools.caller(call), call.arguments)


@pytest.mark.parametrize("identity", [MACRO, "encode-earlier", "unity-earlier"])
def test_same_work_retry_remains_available(signal_lost, identity):
    set_work_state(signal_lost, WORK, "awaiting_user")
    result_type, job = run_tool(signal_lost, retry(signal_lost, identity, work=WORK))
    assert (result_type, job["state"], job["attempt_number"], job["work_id"]) == (
        "success",
        "planned",
        2,
        WORK,
    )
    assert not records(signal_lost, "job_retry_authorisations")


@pytest.mark.parametrize(
    ("target", "allowed"),
    [
        pytest.param({"kind": "item", "version": ASSET_V1}, True, id="exact-asset-version"),
        pytest.param({"kind": "item", "version": ASSET_V3}, False, id="other-asset-version"),
        pytest.param(SCENE_TARGET, False, id="other-item"),
        pytest.param({"kind": "project"}, False, id="unpinned-project"),
    ],
)
def test_cross_turn_asset_preview_retry_requires_exact_pinned_version(signal_lost, target, allowed):
    studio = signal_lost
    new_turn(studio, {"request_target": target})
    if not allowed:
        rejection(studio, ASSET_RENDER, "scope_denied", EARLIER_RENDER, status=403)
        return

    call = retry(studio, ASSET_RENDER)
    original = studio.jobs[ASSET_RENDER]
    result_type, job = run_tool(studio, call)
    assert (result_type, job["state"], job["attempt_number"]) == ("success", "planned", 2)
    assert job["attempt_id"] != original["attempt_id"]
    assert job["target"]["version"] == ASSET_V1
    assert job["job_id"] == ASSET_RENDER and job["work_id"] == WORK
    assert job["input_digest"] == original["input_digest"]
    assert records(studio, "job_retry_authorisations") == [
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            job_id=ASSET_RENDER,
            attempt_id=job["attempt_id"],
            attempt_number=2,
            originating_work_id=WORK,
            authorising_work_id=NEW_WORK,
            authorising_request_id=NEW_REQUEST,
            invocation_id=key(SESSION, "tool", call.tool_call_id),
            operation_id=call.arguments["operation_id"],
        )
    ]


@pytest.mark.parametrize(
    "selection", [DRAFT_VIEW, {"request_target": SCENE_TARGET}], ids=["draft", "scene"]
)
def test_recover_four_pinned_shots_and_never_assemble_missing_frames(
    signal_lost, monkeypatch, selection
):
    studio = signal_lost
    new_turn(studio, selection)
    calls = []
    for identity, *_ in SHOTS[:-1]:
        call = retry(studio, identity)
        original = studio.jobs[identity]
        result_type, recovered = run_tool(studio, call)
        assert (result_type, recovered["state"], recovered["attempt_number"]) == (
            "success",
            "planned",
            2,
        )
        assert recovered["attempt_id"] != original["attempt_id"]
        for field in ("job_id", "work_id", "input_digest", "artifact_ids", "completed_frames"):
            assert recovered[field] == original[field]
        assert recovered["external_job_id"] is None and recovered["owner_epoch"] == "epoch"
        assert not {"native_exit_code", "error_code"} & recovered.keys()
        assert run_tool(studio, call) == ("success", recovered)
        calls.append((call, recovered))
        with studio.store.transaction(write=False) as tx:
            invocation_id = key(SESSION, "tool", call.tool_call_id)
            assert tx.require("job_retry_authorisations", recovered["attempt_id"]) == dict(
                schema_version="1.0",
                project_id=PROJECT,
                job_id=identity,
                attempt_id=recovered["attempt_id"],
                attempt_number=2,
                originating_work_id=WORK,
                authorising_work_id=NEW_WORK,
                authorising_request_id=NEW_REQUEST,
                invocation_id=invocation_id,
                operation_id=call.arguments["operation_id"],
            )
            assert get_tool_outcome(tx, invocation_id)["job_ids"] == [identity]
            assert sorted(
                a["attempt_number"] for a in tx.all("attempts") if a["job_id"] == identity
            ) == [
                1,
                2,
            ]

    # A busy offline resource leaves the new attempts in the real outbox without a launch.
    monkeypatch.setattr(studio.services.resources, "acquire", lambda *args: False)
    assert studio.previews.pump(PROJECT) == []
    with studio.store.transaction(write=False) as tx:
        outbox = {
            r["id"]: (r["kind"], r["state"], json.loads(r["body"]))
            for r in tx.connection.execute("SELECT id,kind,state,body FROM outbox")
        }
        assert len(outbox) == 4
        for _, job in calls:
            assert outbox[job["attempt_id"]] == (
                "preview.render",
                "pending",
                {k: job[k] for k in ("job_id", "attempt_id", "input_digest")},
            )
    rejection(studio, TITLE, "invalid_state_transition")
    if selection == DRAFT_VIEW:
        # A proxy isn't a contributor to the draft's FrameMap. Only the scene pins it.
        rejection(studio, PROXY, "scope_denied", EARLIER_RENDER, status=403)
    else:
        rejection(studio, PROXY, "invalid_state_transition")
    rejection(studio, ASSET_RENDER, "scope_denied", EARLIER_RENDER, status=403)

    assemble = invocation(
        studio,
        "assemble_sequence",
        dict(
            operation_id="assemble-draft",
            job_id="draft-movie",
            execution_profile_id="blender-cpu",
            version=DRAFT,
        ),
    )
    result_type, error = run_tool(studio, assemble)
    assert (result_type, error["error"]) == ("failure", "media_unavailable")
    assert len(records(studio, "frames")) == 297
    assert not any(j["job_id"] == "draft-movie" for j in records(studio, "jobs"))


@pytest.fixture
def sequence_runtime(runtime, tmp_path):
    services = runtime.services
    services.settings = services.jobs.settings = replace(
        services.settings,
        execution_profiles=frozenset({"blender-cpu"}),
        rendering=dict(
            blender="/bin/false", claim_path=str(tmp_path / "encode.lock"), encode_timeout=10
        ),
    )
    services.resources = Resources(Store(tmp_path / "claims.sqlite3"))
    runtime.previews = install_previews(services)
    workflows = install_workflows(services)
    try:
        yield runtime
    finally:
        workflows.close_idle()


@pytest.mark.parametrize(
    "mode", ["identity", "distinct-scenes", "duplicate-scene", "duplicate-shot"]
)
def test_standalone_scene_sequence_publication_and_assembly(sequence_runtime, mode, monkeypatch):
    studio = sequence_runtime
    services = studio.services
    artifacts = services.extension("artifacts")
    second_scene = {"item_id": "scene-two", "version_id": "scene-two-v1"}
    refs = [SCENE, second_scene] if mode in {"distinct-scenes", "duplicate-shot"} else [SCENE]
    shot_id = "shot-01" if mode == "duplicate-shot" else None
    source_frames = {"start": 1, "end": 2}
    sources = []
    for i, ref in enumerate(refs):
        publish(studio, ref, "scene")
        planned = seed_job(
            studio, f"scene-render-{i}", ref, "succeeded", frames=(1, 2), shot_id=shot_id
        )
        seed_frames(studio, planned[0], 1, 2)
        rendered = settle(studio, planned)
        assert rendered["target"]["shot_id"] == shot_id
        sources.append(
            dict(
                shot_id=shot_id,
                version_id=ref["version_id"],
                job_id=rendered["job_id"],
                source_frames=source_frames,
            )
        )

    entry = dict(
        entry_id="scene-entry",
        **sources[0],
        sequence_frames=source_frames,
        transform="identity",
        mapping_artifact_id=None,
    )
    sequence = dict(
        schema_version="1.0",
        project_id=PROJECT,
        sequence_version_id=DRAFT["version_id"],
        fps=FPS,
        frame_count=2,
        silent=True,
        entries=[entry],
    )
    declared_map = None
    if mode != "identity":
        contributors = [sources[0], sources[-1]]
        entry.update(
            transform="transition", contributors=contributors, mapping_artifact_id="dissolve-map"
        )
        declared_map = dict(
            schema_version="1.0",
            project_id=PROJECT,
            sequence_version_id=DRAFT["version_id"],
            fps=FPS,
            frame_count=2,
            blend_space="linear_rgb",
            frames=[
                dict(
                    sequence_frame=frame,
                    contributors=[
                        dict(
                            shot_id=s["shot_id"],
                            version_id=s["version_id"],
                            job_id=s["job_id"],
                            source_frame=frame,
                            weight=dict(numerator=1, denominator=2),
                        )
                        for s in contributors
                    ],
                )
                for frame in (1, 2)
            ],
        )
        with studio.store.transaction() as tx:
            artifacts.put(
                tx,
                artifacts.promote(
                    PROJECT,
                    "dissolve-map",
                    canonical(declared_map),
                    kind="frame_map",
                    purpose="metadata",
                    media_type="application/json",
                ),
            )

    request = dict(
        schema_version="1.0", project_id=PROJECT, document_kind="sequence_map", document=sequence
    )
    validated = validate_authored_document(services, PROJECT, request)
    assert (validated["valid"], validated["schema_valid"], validated["semantic_valid"]) == (
        True,
        True,
        True,
    )
    if mode == "identity":
        wrong_shot = {**sequence, "entries": [{**entry, "shot_id": "shot-01"}]}
        invalid = validate_authored_document(services, PROJECT, dict(request, document=wrong_shot))
        assert (invalid["valid"], invalid["schema_valid"], invalid["semantic_valid"]) == (
            False,
            True,
            False,
        )
        assert invalid["issues"] == [
            dict(
                phase="semantic",
                code="invalid_frame_mapping",
                path="$",
                message="Sequence contributor shot_id must match the source render job's shot_id (use null for standalone scene renders)",
            )
        ]

    body = publication(studio, canonical(sequence))
    item = body["items"][0]
    item.update(**DRAFT, kind="sequence", name="Scene assembly")
    item["source"]["dependencies"] = refs
    item["source"]["digest"] = closure_digest(item["source"])
    catalogue = services.extension("catalogue")
    catalogue.sources = Resolver(services)
    result_type, result = run_tool(
        studio, invocation(studio, "publish_outputs", body, work=WORK, call="publish-sequence")
    )
    assert result_type == "success", result
    assert result["versions"] == [DRAFT]

    job_id = "standalone-delivery"
    result_type, result = run_tool(
        studio,
        invocation(
            studio,
            "assemble_sequence",
            dict(
                operation_id="assemble-scenes",
                job_id=job_id,
                execution_profile_id="blender-cpu",
                version=DRAFT,
            ),
            work=WORK,
        ),
    )
    if mode.startswith("duplicate-"):
        assert (result_type, result["error"], result["message"]) == (
            "failure",
            "invalid_frame_mapping",
            "A dissolve requires distinct shots",
        )
        assert not any(job["job_id"] == job_id for job in records(studio, "jobs"))
        return
    assert result_type == "success", result
    assert (result["kind"], result["state"]) == ("encode", "planned")

    # Own and reap the one permitted detached worker. It only encodes two 64×64 frames.
    spawn = subprocess.Popen
    processes = []
    attempt_root = studio.previews.root(result)

    def owned_worker(argv, **kwargs):
        assert argv == [sys.executable, "-m", "proscenium.previews.worker", str(attempt_root)]
        process = spawn(argv, **kwargs)
        processes.append(process)
        return process

    try:
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, "Popen", owned_worker)
            studio.previews.pump(PROJECT)
        assert len(processes) == 1
        assert processes[0].wait(timeout=45) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()

    studio.previews.reconcile(PROJECT)
    finished = services.jobs.get(PROJECT, job_id)
    assert (finished["state"], finished["availability"]) == ("succeeded", "complete")
    video_id, map_id, manifest_id = finished["artifact_ids"]
    video = artifacts.get(PROJECT, video_id)
    assert (video["kind"], video["purpose"], video["media_type"], video["state"]) == (
        "video",
        "final",
        "video/mp4",
        "ready",
    )
    assert set(finished["artifact_ids"]) <= set(
        catalogue.version(PROJECT, DRAFT["version_id"])["artifact_ids"]
    )
    video_path = artifacts.ready_path(PROJECT, video_id)
    assert verify_video(video_path, FPS, 2, DIMENSIONS, studio.previews.config.snapshot())[
        "metadata"
    ] == dict(dimensions=DIMENSIONS, fps=FPS, frame_count=2)
    assert artifacts.get(PROJECT, map_id)["kind"] == "frame_map"
    mapping_bytes = artifacts.ready_path(PROJECT, map_id).read_bytes()
    mapping = validate("FrameMap", json.loads(mapping_bytes))
    if declared_map is not None:
        assert mapping == declared_map
    else:
        assert [row["contributors"][0]["source_frame"] for row in mapping["frames"]] == [1, 2]
        assert all(row["contributors"][0]["shot_id"] is None for row in mapping["frames"])
    manifest = json.loads(artifacts.ready_path(PROJECT, manifest_id).read_bytes())
    assert manifest["input_digest"] == finished["input_digest"]
    assert manifest["sequence"] == sequence
    assert manifest["frame_map_sha256"] == sha(mapping_bytes)
    assert manifest["video_sha256"] == video["sha256"] == sha(video_path.read_bytes())


@pytest.mark.parametrize("via_frame_map", [False, True], ids=["direct", "frame-map"])
def test_sequence_transition_requires_an_exact_pinned_contributor(signal_lost, via_frame_map):
    studio = signal_lost
    dissolve = {**DRAFT, "version_id": "signal-lost-dissolve-v1"}
    document = deepcopy(studio.sequence)
    document["sequence_version_id"] = dissolve["version_id"]
    macro, conduit, *_ = document["entries"]

    def source(entry):
        return {k: entry[k] for k in ("shot_id", "version_id", "job_id", "source_frames")}

    contributors = [source(macro), source(conduit)]
    macro.update(
        **source(conduit),
        transform="transition",
        mapping_artifact_id="dissolve-map",
        contributors=contributors,
    )
    frames = []
    for entry in document["entries"]:
        sources = contributors if entry is macro else [entry]
        first, last = entry["sequence_frames"]["start"], entry["sequence_frames"]["end"]
        for frame in range(first, last + 1):
            frames.append(
                dict(
                    sequence_frame=frame,
                    contributors=[
                        dict(
                            shot_id=s["shot_id"],
                            version_id=s["version_id"],
                            job_id=s["job_id"],
                            source_frame=s["source_frames"]["start"] + frame - first,
                            weight=dict(numerator=1, denominator=len(sources)),
                        )
                        for s in sources
                    ],
                )
            )
    if via_frame_map:
        contributors[0]["job_id"] = None
    mapping = dict(
        schema_version="1.0",
        project_id=PROJECT,
        sequence_version_id=dissolve["version_id"],
        fps=FPS,
        frame_count=document["frame_count"],
        blend_space="linear_rgb",
        frames=frames,
    )
    map_id = macro["mapping_artifact_id"]
    artifacts = studio.services.extension("artifacts")

    def promote(frame_map):
        return artifacts.promote(
            PROJECT,
            map_id,
            canonical(frame_map),
            kind="frame_map",
            purpose="metadata",
            media_type="application/json",
        )

    publish(studio, dissolve, "sequence", document, dependencies=[SCENE])
    new_turn(studio, {**DRAFT_VIEW, **dissolve})
    call = retry(studio, MACRO)
    caller = studio.tools.caller(call)
    job = studio.jobs[MACRO]
    # Neither a direct contributor nor a mapped contributor authorises retry without the full map.
    assert_direct_retry_denied(studio, caller, call.arguments)
    mismatched = deepcopy(mapping)
    # Valid JSON, hash, and source job, but the last row cannot cite a transition contributor.
    mismatched["frames"][-1]["contributors"] = [
        {**frames[0]["contributors"][0], "weight": {"numerator": 1, "denominator": 1}}
    ]
    with studio.store.transaction() as tx:
        artifacts.put(tx, promote(mismatched))
    assert_direct_retry_denied(studio, caller, call.arguments)

    with studio.store.transaction() as tx:
        sequence_map(tx, document, dissolve["version_id"], {map_id: mapping})
        tx.put(
            "artifacts",
            map_id,
            promote(mapping),
            schema="Artifact",
            expected_revision=tx.revision("artifacts", map_id),
        )
    data = canonical(mapping)
    path = artifacts.ready_path(PROJECT, map_id)
    path.chmod(0o600)
    path.write_bytes(data.replace(b'"source_frame":1,', b'"source_frame":2,', 1))
    assert_direct_retry_denied(studio, caller, call.arguments)
    path.write_bytes(data)
    with studio.store.transaction(write=False) as tx:
        assert studio.previews.can_retry_cross_work(tx, caller, job) == NEW_REQUEST
        unpinned = {**job, "target": {**job["target"], "shot_id": "different-shot"}}
        assert studio.previews.can_retry_cross_work(tx, caller, unpinned) is None
    result_type, recovered = run_tool(studio, call)
    assert (result_type, recovered["attempt_number"]) == ("success", 2)


def test_steer_context_is_the_durable_authority_and_only_service_replay_bypasses_the_gate(
    signal_lost,
    monkeypatch,
):
    studio = signal_lost
    new_turn(studio, {"request_target": {"kind": "project"}})
    steer_id = "request-steer"
    snapshot, _ = studio.contexts.capture(context("steer-context", request_target=SCENE_TARGET))
    with studio.store.transaction() as tx:
        steer = request_record(steer_id, snapshot["context_id"], kind="steer")
        steer["submission"]["target_work_id"] = NEW_WORK
        steer["receipt"]["work_id"] = NEW_WORK
        tx.put("requests", steer_id, steer, schema="RequestRecord")
    call = retry(studio, MACRO, context_request=steer_id)
    caller = studio.tools.caller(call)
    result_type, job = run_tool(studio, call)
    assert result_type == "success"
    [audit] = records(studio, "job_retry_authorisations")
    assert audit["authorising_request_id"] == steer_id

    unseen = retry(studio, SHOTS[1][0], context_request=steer_id)
    unseen_caller = studio.tools.caller(unseen)
    monkeypatch.setattr(studio.state, "control", lambda tx: {"fenced": True})
    with pytest.raises(DomainError) as fenced:
        studio.tools.caller(call)
    assert fenced.value.code == "permission_denied"
    studio.services.gate.close(PROJECT)
    set_work_state(studio, NEW_WORK, "completed")
    assert studio.services.jobs.retry(caller, call.arguments) == (job, True)
    with pytest.raises(DomainError) as denied:
        studio.services.jobs.retry(unseen_caller, unseen.arguments)
    assert denied.value.code == "project_busy"
    studio.services.gate.resume(PROJECT, reconciliation_evidence="Offline fixture")
    with pytest.raises(DomainError) as inactive:
        studio.services.jobs.retry(unseen_caller, unseen.arguments)
    assert (inactive.value.status, inactive.value.code) == (409, "no_active_work")


def test_scope_guidance_is_generic_except_for_image_preparation():
    generic = DomainError("scope_denied", 403)
    assert (
        "prepare_image_generation"
        in tool_failure(generic, "prepare_image_generation").text_result_for_llm
    )
    assert "prepare_image_generation" not in tool_failure(generic, "retry_job").text_result_for_llm


@pytest.mark.parametrize(
    "conflict", ["sha256", "artifact_id", "version_id", "shot_id", "dimensions", "input_digest"]
)
def test_adopted_frame_is_valid_until_any_receipt_conflicts(signal_lost, monkeypatch, conflict):
    studio = signal_lost
    new_turn(studio, {"request_target": SCENE_TARGET})
    original = studio.jobs[MACRO]
    result_type, job = run_tool(studio, retry(studio, MACRO))
    assert result_type == "success"
    # Exercise the real adopted receipts and their registration; the spawned process is a stub.
    monkeypatch.setattr(Rendering, "snapshot", lambda self: {"profile_id": self.profile_id})
    monkeypatch.setattr(
        runner,
        "subprocess",
        SimpleNamespace(
            DEVNULL=subprocess.DEVNULL,
            STDOUT=subprocess.STDOUT,
            Popen=lambda *args, **kwargs: SimpleNamespace(pid=123),
        ),
    )
    assert studio.previews.pump(PROJECT)
    runner.register_frames(studio.previews, worker_caller(job), studio.previews.root(job))
    slot = [f for f in records(studio, "frames") if f["job_id"] == MACRO and f["source_frame"] == 1]
    assert {f["receipt_id"] for f in slot} == {f"{j['attempt_id']}-f1" for j in (original, job)}
    first, second = sorted(slot, key=lambda f: f["attempt_id"] != original["attempt_id"])
    assert {k: v for k, v in first.items() if k not in {"receipt_id", "attempt_id"}} == {
        k: v for k, v in second.items() if k not in {"receipt_id", "attempt_id"}
    }
    target = dict(
        origin="blender_render",
        **SCENE,
        shot_id=MACRO,
        render_job_id=MACRO,
        source_frame=1,
        artifact_id=first["artifact_id"],
        media_artifact_id=None,
        displayed_sha256=first["sha256"],
        dimensions=DIMENSIONS,
    )
    view = dict(**SCENE, view_mode="animation", target=target, request_target=None)
    snapshot, _ = studio.contexts.capture(context("adopted-frame", **view))
    assert studio.contexts.attachments(snapshot) == [
        {"artifact_id": first["artifact_id"], "role": "render"}
    ]
    references = References(studio.services)
    with studio.store.transaction() as tx:
        references.target(tx, target)
        bad = {**second, "receipt_id": "conflicting-f1", "attempt_id": "conflicting-attempt"}
        bad[conflict] = (
            "0" * 64
            if conflict in {"sha256", "input_digest"}
            else {"width": 32, "height": 32}
            if conflict == "dimensions"
            else "asserted-conflicting-id"
        )
        tx.put("frames", bad["receipt_id"], bad, schema="FrameReceipt")
    with pytest.raises(DomainError) as capture:
        studio.contexts.capture(context("conflicting-frame", **view))
    assert (capture.value.status, capture.value.code) == (422, "frame_conflict")
    with studio.store.transaction(write=False) as tx, pytest.raises(DomainError) as review:
        references.target(tx, target)
    assert (review.value.status, review.value.code, review.value.message) == (
        409,
        "source_conflict",
        "Conflicting frame receipts across attempts",
    )


@pytest.mark.parametrize(
    ("selection", "message"),
    [
        (
            {"request_target": {"kind": "item", "version": DRAFT}},
            "Version signal-lost-animation-draft-v1 has kind 'sequence', which cannot be used as an 'item' request target.",
        ),
        (
            {"request_target": {"kind": "shot", "storyboard_version": SCENE, "shot_id": MACRO}},
            "Shot target 'sl-01-dormant-macro' requires a storyboard version, but signal-lost-animation-v1 has kind 'scene'.",
        ),
        (
            {**DRAFT, "request_target": {"kind": "project"}},
            "Request target 'project' cannot be combined with pinned selection signal-lost-animation-draft-v1.",
        ),
        (
            {
                "request_target": {
                    "kind": "item",
                    "version": {"item_id": "asset-kit", "version_id": SCENE["version_id"]},
                }
            },
            "Version signal-lost-animation-v1 belongs to item scene-signal-lost-animation, not asset-kit.",
        ),
        (
            {**ASSET_V3, "request_target": SCENE_TARGET},
            "Request target scene-signal-lost-animation (signal-lost-animation-v1) conflicts with viewed selection asset-kit (asset-kit-v3).",
        ),
    ],
)
def test_post_context_reports_the_exact_incompatible_identity(signal_lost, selection, message):
    app = FastAPI()
    app.state.services = signal_lost.services
    app.dependency_overrides[owner] = lambda: Caller("owner", "owner-test")
    app.add_exception_handler(
        DomainError, lambda request, exc: JSONResponse(exc.envelope(), status_code=exc.status)
    )
    app.include_router(copilot_router)
    snapshot = context("http-context", **selection)
    with TestClient(app) as client:
        result = client.post(
            f"/api/v1/projects/{PROJECT}/contexts",
            json=snapshot,
            headers={"Idempotency-Key": snapshot["context_id"]},
        )
    assert result.status_code == 409
    error = result.json()["error"]
    assert (error["code"], error["message"]) == ("source_conflict", message)
