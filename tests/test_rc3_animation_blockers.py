"""RC3 render-journal and visual-review regressions with no native or provider execution."""

import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.copilot.outcomes import get_tool_outcome
from proscenium.copilot.state import State, key
from proscenium.db import Store
from proscenium.jobs.resources import Resources
from proscenium.previews import runner
from proscenium.previews import service as preview_service
from proscenium.previews.journal import atomic, read, sha
from proscenium.previews.service import PreviewService, worker_caller
from proscenium.reviews.references import References
from test_vlm_critic_workflow import vlm_runtime as vlm_runtime
from test_worker_capabilities_and_publication import PROJECT, SESSION, WORK, invoke
from test_worker_capabilities_and_publication import offline as offline

DIMENSIONS = {"width": 640, "height": 360}
VERSION = {"item_id": "scene-one", "version_id": "scene-version-one"}
FPS = {"numerator": 24, "denominator": 1}
VIDEO = b"offline video fixture; no FFmpeg invocation"


@pytest.fixture
def studio(vlm_runtime, tmp_path, monkeypatch):
    runtime = vlm_runtime
    runtime.services.resources = Resources(Store(tmp_path / "claims.sqlite3"))
    runtime.previews = PreviewService(runtime.services)

    def forbid_launch(*args, **kwargs):
        pytest.fail("Preview regressions must never launch a native process")

    def verify_video(path, fps, count, dimensions, config):
        assert path.read_bytes() == VIDEO
        assert (fps, count, dimensions, config) == (FPS, 4, DIMENSIONS, {})

    monkeypatch.setattr(runner, "launch", forbid_launch)
    monkeypatch.setattr(runner, "alive", lambda process: process["pid"] == 123)
    monkeypatch.setattr(preview_service, "verify_video", verify_video)
    return runtime


def plan_render(studio, job_id="render-one", end=28):
    digest = sha(b"fixture source")
    with studio.store.transaction() as tx:
        return studio.services.jobs.create(
            tx,
            job_id=job_id,
            kind="render",
            target={"target_id": VERSION["item_id"], "version": VERSION, "shot_id": "shot-one"},
            input_digest=digest,
            operation_id=f"request-{job_id}",
            work_id=WORK,
            render_spec={
                "intent_id": job_id,
                "shot_id": "shot-one",
                "purpose": "draft_motion",
                "source_closure_digest": digest,
                "scene_name": "Fixture",
                "camera": None,
                "frames": {"start": 1, "end": end},
                "fps": FPS,
                "dimensions": DIMENSIONS,
                "engine": "CYCLES",
                "device": "CPU",
                "samples": 4,
                "colour_management": {
                    "view_transform": "AgX",
                    "look": "",
                    "exposure": 0,
                    "gamma": 1,
                },
                "renderer_revision": "fixture",
                "review_output": {
                    "media_type": "image/png",
                    "encoding": "view_transformed_srgb",
                    "bit_depth": 8,
                },
            },
            expected_frames=end,
            enqueue_kind="preview.render",
        )


def attempt(studio, end=28, *, job_id="render-one", acquire=True):
    job = plan_render(studio, job_id, end=end)
    if acquire:
        assert studio.services.resources.acquire(
            runner.RESOURCE, PROJECT, job["job_id"], job["attempt_id"]
        )
    root = studio.previews.root(job)
    atomic(root / "task.json", {**receipt_binding(job), "config": {}})
    (root / "launch.claim").touch()
    atomic(root / "process.json", {"task_sha256": task_hash(root), "pid": 123, "start_ticks": 1})
    job = studio.previews.state(job, "starting")
    return studio.previews.state(job, "running"), root


def task_hash(root):
    return sha((root / "task.json").read_bytes())


def terminal(root, state="cancelled", *, digest=None):
    atomic(
        root / "terminal.json",
        {
            "task_sha256": digest if digest is not None else task_hash(root),
            "state": state,
            "native_exit_code": 0 if state == "succeeded" else -9,
            "timed_out": False,
        },
    )


def receipt_binding(job):
    return {
        **{
            field: job[field]
            for field in ("schema_version", "project_id", "job_id", "attempt_id", "input_digest")
        },
        "version_id": VERSION["version_id"],
        "shot_id": job["target"]["shot_id"],
    }


def png(frame):
    output = io.BytesIO()
    Image.new("RGB", (640, 360), (frame, 90, 140)).save(output, format="PNG")
    return output.getvalue()


def frame_receipt(job, root, frame):
    filename = f"frames/{frame:08d}.png"
    data = png(frame)
    (root / "frames").mkdir(exist_ok=True)
    (root / filename).write_bytes(data)
    body = {
        **receipt_binding(job),
        "receipt_id": f"{job['attempt_id']}-f{frame}",
        "artifact_id": f"{job['job_id']}-f{frame}",
        "source_frame": frame,
        "dimensions": DIMENSIONS,
        "output_path": filename,
        "sha256": sha(data),
    }
    atomic(root / f"frame-{frame:08d}.json", body)
    return body


def segment_receipt(job, root, first, last):
    stem = f"segment-{first:08d}-{last:08d}"
    filename = f"{stem}.mp4"
    identity = f"{job['attempt_id']}-s{first}-{last}"
    (root / filename).write_bytes(VIDEO)
    body = {
        **receipt_binding(job),
        "segment_id": identity,
        "artifact_id": identity,
        "source_frames": {"start": first, "end": last},
        "frame_count": last - first + 1,
        "fps": FPS,
        "output_path": filename,
        "sha256": sha(VIDEO),
    }
    atomic(root / f"{stem}.json", body)
    return body


def records(studio, family):
    with studio.store.transaction(write=False) as tx:
        return tx.all(family)


def claim(studio):
    with studio.services.resources.store.transaction(write=False) as tx:
        row = tx.connection.execute(
            "SELECT project_id,job_id,attempt_id FROM resource_claims WHERE resource=?",
            (runner.RESOURCE,),
        ).fetchone()
    return tuple(row) if row else None


def test_stranded_cancelled_render_keeps_partial_media_and_releases_only_its_claim(studio):
    job, root = attempt(studio, end=72)
    frames = [frame_receipt(job, root, n) for n in range(1, 29)]
    for frame in frames:
        studio.previews.register(worker_caller(job), frame)
    segments = [segment_receipt(job, root, first, first + 3) for first in range(1, 29, 4)]
    for segment in segments:
        studio.previews.register(worker_caller(job), segment, segment=True)
    atomic(root / "cancel.json", {"attempt_id": job["attempt_id"]})
    terminal(root)

    successor = plan_render(studio, "render-two")
    assert not studio.services.resources.acquire(
        runner.RESOURCE, PROJECT, successor["job_id"], successor["attempt_id"]
    )
    [settled] = studio.previews.reconcile(PROJECT)
    assert (
        settled["state"],
        settled["availability"],
        settled["completed_frames"],
        settled["native_exit_code"],
    ) == ("cancelled", "partial", 28, -9)
    assert settled["expected_frames"] == 72
    assert set(settled["artifact_ids"]) == {row["artifact_id"] for row in [*frames, *segments]}
    assert sorted(records(studio, "frames"), key=lambda row: row["source_frame"]) == frames
    assert (
        sorted(records(studio, "segments"), key=lambda row: row["source_frames"]["start"])
        == segments
    )
    assert claim(studio) is None
    assert studio.services.resources.acquire(
        runner.RESOURCE, PROJECT, successor["job_id"], successor["attempt_id"]
    )
    assert studio.previews.reconcile(PROJECT) == []
    assert claim(studio) == (PROJECT, successor["job_id"], successor["attempt_id"])
    assert studio.services.jobs.get(PROJECT, job["job_id"]) == settled


@pytest.mark.parametrize(
    ("initial", "acquire", "corruption", "code"),
    [
        ("running", True, "digest", "hash_mismatch"),
        ("running", False, "digest", "hash_mismatch"),
        ("cancelled", True, "digest", "hash_mismatch"),
        ("cancelled", True, "json", "invalid_request"),
        ("cancelled", True, "shape", "invalid_request"),
    ],
)
def test_corrupt_terminal_never_releases_its_claim_or_starves_later_jobs(
    studio, initial, acquire, corruption, code
):
    bad, bad_root = attempt(studio, job_id="render-a", acquire=acquire)
    atomic(bad_root / "cancel.json", {"attempt_id": bad["attempt_id"]})
    if initial == "cancelled":
        bad = studio.previews.state(bad, "cancel_requested")
        bad = studio.previews.state(bad, "cancelled")
    if corruption == "digest":
        terminal(bad_root, digest="0" * 64)
    elif corruption == "json":
        (bad_root / "terminal.json").write_text("{")
    else:
        atomic(bad_root / "terminal.json", {"task_sha256": task_hash(bad_root)})

    good, good_root = attempt(studio, end=4, job_id="render-b", acquire=False)
    frames = [frame_receipt(good, good_root, frame) for frame in range(1, 5)]
    terminal(good_root, "succeeded")
    with pytest.raises(DomainError) as error:
        studio.previews.reconcile(PROJECT)
    assert error.value.code == code
    assert claim(studio) == ((PROJECT, bad["job_id"], bad["attempt_id"]) if acquire else None)
    assert studio.services.jobs.get(PROJECT, bad["job_id"]) == bad
    good = studio.services.jobs.get(PROJECT, good["job_id"])
    assert (good["state"], good["completed_frames"]) == ("succeeded", 4)
    assert records(studio, "frames") == frames


@pytest.mark.parametrize(
    ("initial", "marker", "expected"),
    [
        ("running", None, "failed"),
        ("running", "different-attempt", "failed"),
        ("outcome_uncertain", None, "failed"),
        ("cancel_requested", None, "failed"),
        ("cancel_requested", "different-attempt", "failed"),
        ("cancel_requested", "matching", "cancelled"),
    ],
)
def test_supervisor_cancelled_requires_matching_marker(studio, initial, marker, expected):
    job, root = attempt(studio)
    if initial != "running":
        job = studio.previews.state(job, initial)
    if initial == "outcome_uncertain":
        atomic(root / "process.json", {"task_sha256": task_hash(root), "pid": 999}, immutable=False)
    if marker is not None:
        atomic(
            root / "cancel.json",
            {"attempt_id": job["attempt_id"] if marker == "matching" else marker},
        )
    terminal(root)
    [settled] = studio.previews.reconcile(PROJECT)
    assert (settled["state"], settled["native_exit_code"]) == (expected, -9)
    assert claim(studio) is None


@pytest.mark.parametrize("arrival", ["next_tick", "during_scan", "never"])
def test_segment_dependencies_rescan_defer_or_fail_when_terminal(studio, monkeypatch, arrival):
    job, root = attempt(studio, end=4)
    frames = [frame_receipt(job, root, n) for n in (1, 2, 3)]
    (root / "frame-not-a-receipt.json").write_text("not a receipt")
    segment = segment_receipt(job, root, 1, 4)
    if arrival == "during_scan":
        register = studio.previews.register

        def land_final_frame(caller, body, *, segment=False):
            if segment and len(frames) == 3:
                frames.append(frame_receipt(job, root, 4))
                # The fresh disk receipt has not entered SQLite yet.
                assert len(records(studio, "frames")) == 3
            return register(caller, body, segment=segment)

        monkeypatch.setattr(studio.previews, "register", land_final_frame)
    if arrival == "never":
        terminal(root, "succeeded")

    [current] = studio.previews.reconcile(PROJECT)
    assert not (root / "cancel.json").exists()
    if arrival == "never":
        assert (current["state"], current["completed_frames"]) == ("failed", 3)
        assert records(studio, "segments") == []
        assert claim(studio) is None
        return
    assert current["state"] == "running"
    if arrival == "next_tick":
        assert sorted(row["source_frame"] for row in records(studio, "frames")) == [1, 2, 3]
        assert records(studio, "segments") == []
        frames.append(frame_receipt(job, root, 4))
        [current] = studio.previews.reconcile(PROJECT)
    assert studio.services.jobs.get(PROJECT, job["job_id"])["completed_frames"] == 4
    assert records(studio, "segments") == [segment]
    assert not (root / "cancel.json").exists()


@pytest.mark.parametrize("remove_bad_journal", [False, True])
def test_live_media_failure_requests_cancellation_but_settles_failed(studio, remove_bad_journal):
    job, root = attempt(studio)
    good = frame_receipt(job, root, 1)
    corrupt = frame_receipt(job, root, 2)
    (root / corrupt["output_path"]).write_bytes(b"corrupt")
    with pytest.raises(DomainError) as error:
        studio.previews.reconcile(PROJECT)
    assert error.value.code == "hash_mismatch"
    assert read(root / "cancel.json") == {
        "attempt_id": job["attempt_id"],
        "reason": "media_validation_failed",
    }
    assert studio.services.jobs.get(PROJECT, job["job_id"])["state"] == "cancel_requested"
    assert claim(studio) == (PROJECT, job["job_id"], job["attempt_id"])
    assert runner.cancel(studio.previews, PROJECT, job)["cancellation_marker"] is True
    assert read(root / "cancel.json")["reason"] == "media_validation_failed"
    if remove_bad_journal:
        (root / "frame-00000002.json").unlink()
    terminal(root)
    [settled] = studio.previews.reconcile(PROJECT)
    assert (settled["state"], settled["availability"], settled["artifact_ids"]) == (
        "failed",
        "partial",
        [good["artifact_id"]],
    )
    assert claim(studio) is None


def frame_target(receipt):
    return {
        "origin": "blender_render",
        **VERSION,
        "shot_id": receipt["shot_id"],
        "render_job_id": receipt["job_id"],
        "source_frame": receipt["source_frame"],
        "artifact_id": receipt["artifact_id"],
        "media_artifact_id": None,
        "displayed_sha256": receipt["sha256"],
        "dimensions": receipt["dimensions"],
    }


def review_request(targets):
    return {
        "schema_version": "1.0",
        "review_id": "draft-motion-review",
        "project_id": PROJECT,
        "targets": targets,
        "criteria": "Check motion continuity across the sampled frames.",
        "telemetry_artifact_ids": [],
    }


@pytest.mark.parametrize("origin", ["concept", "reference", "blender_render", "sequence_render"])
def test_vlm_draft_motion_stills_require_exact_receipt_before_job_admission(
    studio, monkeypatch, origin
):
    raw = png(1)
    staging = ".proscenium/tool-output/staging/standalone.png"
    path = studio.root / staging
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    artefact, status = invoke(
        studio,
        "register_artifact",
        {
            "schema_version": "1.0",
            "project_id": PROJECT,
            "operation_id": "register-standalone",
            "artifact_id": "standalone-frame",
            "staging_path": staging,
            "kind": "image",
            "purpose": "draft_motion",
            "media_type": "image/png",
            "sha256": sha(raw),
        },
        identity="call-register",
    )
    assert status == "success", artefact
    target = {
        "origin": origin,
        "item_id": None,
        "version_id": None,
        "shot_id": None,
        "render_job_id": None,
        "source_frame": None,
        "artifact_id": artefact["artifact_id"],
        "media_artifact_id": None,
        "displayed_sha256": artefact["sha256"],
        "dimensions": artefact["dimensions"],
    }
    code = "invalid_request"
    message = (
        "Draft motion visual review targets must reference receipt-backed render or sequence frames"
    )
    expected_frames = []
    if origin == "blender_render":
        job = plan_render(studio)
        with studio.store.transaction() as tx:
            tx.put("versions", VERSION["version_id"], {**VERSION, "project_id": PROJECT})
        target.update(**VERSION, render_job_id=job["job_id"], shot_id="shot-one", source_frame=1)
        code = "source_conflict"
        message = "No exact frame receipt"
    elif origin == "sequence_render":
        job, root = attempt(studio, end=1)
        contributor = frame_receipt(job, root, 1)
        studio.previews.register(worker_caller(job), contributor)
        expected_frames = [contributor]
        sequence = {"item_id": "sequence-one", "version_id": "sequence-version-one"}
        mapping = {
            "schema_version": "1.0",
            "project_id": PROJECT,
            "sequence_version_id": sequence["version_id"],
            "fps": FPS,
            "frame_count": 1,
            "blend_space": "linear_rgb",
            "frames": [
                {
                    "sequence_frame": 1,
                    "contributors": [
                        {
                            **{
                                key: contributor[key]
                                for key in ("job_id", "version_id", "shot_id", "source_frame")
                            },
                            "weight": {"numerator": 1, "denominator": 1},
                        }
                    ],
                }
            ],
        }
        artefacts = studio.previews.artifacts
        frame_map = artefacts.promote(
            PROJECT,
            "sequence-frame-map",
            json.dumps(mapping).encode(),
            kind="frame_map",
            purpose="metadata",
            media_type="application/json",
        )
        with studio.store.transaction() as tx:
            tx.put("versions", sequence["version_id"], {**sequence, "project_id": PROJECT})
            artefacts.put(tx, frame_map)
        target.update(
            **sequence,
            sequence_version_id=sequence["version_id"],
            sequence_frame=1,
            frame_map_artifact_id=frame_map["artifact_id"],
        )
        assert target["displayed_sha256"] == contributor["sha256"]
        assert target["artifact_id"] != contributor["artifact_id"]
        # A valid map and a byte-identical contributor cannot prove receipt of this artefact.
        with studio.store.transaction(write=False) as tx:
            References(studio.services).target(tx, target)

    def forbid_job_creation(*args, **kwargs):
        pytest.fail("Target preflight must reject before attempting review job allocation")

    monkeypatch.setattr(studio.services.jobs, "create", forbid_job_creation)
    prior_jobs = records(studio, "jobs")
    error, status = invoke(
        studio, "request_vlm_review", review_request([target]), identity="call-reject"
    )
    assert (status, error["error"], error["message"]) == ("failure", code, message)
    assert records(studio, "frames") == expected_frames
    assert records(studio, "reviews") == []
    assert records(studio, "providers.tickets") == []
    assert records(studio, "jobs") == prior_jobs
    assert studio.vlm_state["requests"] == []


def test_vlm_draft_motion_stills_are_admitted_replayed_and_labelled(studio):
    job, root = attempt(studio)
    targets = [frame_target(frame_receipt(job, root, n)) for n in (1, 14, 28)]
    studio.previews.reconcile(PROJECT)
    with studio.store.transaction() as tx:
        tx.put("versions", VERSION["version_id"], {**VERSION, "project_id": PROJECT})
    request = review_request(targets)
    first, status = invoke(studio, "request_vlm_review", request, identity="call-admit")
    assert status == "success", first
    replay, status = invoke(studio, "request_vlm_review", request, identity="call-replay")
    assert (replay, status) == (first, "success")
    assert [row["review_id"] for row in records(studio, "reviews")] == [request["review_id"]]
    assert [row["job_id"] for row in records(studio, "jobs") if row["kind"] == "vlm_review"] == [
        first["job_id"]
    ]
    assert studio.vlm_state["requests"] == []
    [pumped] = studio.provider.pump_reviews(PROJECT)
    assert (pumped["job_id"], pumped["state"]) == (first["job_id"], "succeeded")
    assert studio.provider.pump_reviews(PROJECT) == []
    [submission] = [r for r in studio.vlm_state["requests"] if r.url.path == "/v1/chat/completions"]
    parts = json.loads(submission.content)["messages"][1]["content"]
    labels = [json.loads(part["text"]) for part in parts[1::2]]
    assert [(label["origin"], label["source_frame"], label["purpose"]) for label in labels] == [
        ("blender_render", n, "draft_motion") for n in (1, 14, 28)
    ]
    assert all(
        part["image_url"]["url"].startswith("data:image/png;base64,") for part in parts[2::2]
    )


@pytest.mark.parametrize(
    ("case", "code", "message"),
    [
        (
            "displayed_sha256",
            "hash_mismatch",
            "Target displayed_sha256 does not match the registered image artefact",
        ),
        (
            "dimensions",
            "frame_conflict",
            "Target dimensions do not match the registered image artefact",
        ),
        (
            "mask",
            "invalid_request",
            "Artefact purpose is not eligible for visual review; supply concept, reference, keyframe, draft_motion, inspection or final still images",
        ),
        (
            "video",
            "unsupported_media_type",
            "Visual review targets must be ready PNG, JPEG or WebP still images",
        ),
        (
            "stored_corruption",
            "hash_mismatch",
            "Stored artefact bytes do not match their recorded SHA-256 digest",
        ),
    ],
)
def test_vlm_preflight_rejects_bad_media_without_review_or_provider_call(
    studio, case, code, message
):
    job, root = attempt(studio, end=4)
    receipt = frame_receipt(job, root, 1)
    target = frame_target(receipt)
    if case == "video":
        for n in (2, 3, 4):
            frame_receipt(job, root, n)
        segment = segment_receipt(job, root, 1, 4)
        target.update(artifact_id=segment["artifact_id"], displayed_sha256=segment["sha256"])
    studio.previews.reconcile(PROJECT)
    artefacts = studio.previews.artifacts
    if case == "displayed_sha256":
        target["displayed_sha256"] = "0" * 64
    elif case == "dimensions":
        target["dimensions"] = {**DIMENSIONS, "width": 320}
    elif case == "mask":
        mask = artefacts.promote(
            PROJECT, "mask-one", png(1), kind="image", purpose="mask", media_type="image/png"
        )
        with studio.store.transaction() as tx:
            artefacts.put(tx, mask)
        target.update(artifact_id=mask["artifact_id"], displayed_sha256=mask["sha256"])
    elif case == "stored_corruption":
        path = artefacts.ready_path(PROJECT, receipt["artifact_id"])
        path.chmod(0o600)
        path.write_bytes(b"corrupt")

    state = State(studio.services, SimpleNamespace())
    with studio.store.transaction() as tx:
        state.bind(
            tx,
            SESSION,
            "event",
            "root",
            {"work_id": WORK, "request_id": "request-one", "delivery_id": "request-one"},
        )
    state.reduce(
        PROJECT,
        SESSION,
        {
            "id": "start",
            "type": "tool.execution_start",
            "parentId": "root",
            "data": {"toolCallId": "call-review", "toolName": "request_vlm_review"},
        },
    )
    error, status = invoke(
        studio, "request_vlm_review", review_request([target]), identity="call-review"
    )
    assert (status, error["error"], error["message"]) == ("failure", code, message)
    state.reduce(
        PROJECT,
        SESSION,
        {
            "id": "complete",
            "type": "tool.execution_complete",
            "parentId": "start",
            "data": {
                "toolCallId": "call-review",
                "success": False,
                "error": {"message": "Raw tool output must not leak"},
            },
        },
    )
    with studio.store.transaction(write=False) as tx:
        outcome = get_tool_outcome(tx, key(SESSION, "tool", "call-review"))
        assert outcome["safe_summary"] == message
        [activity] = [
            event["payload"]
            for event in tx.events()["events"]
            if event["event_type"] == "tool.completed"
        ]
    assert (activity["state"], activity["summary"]) == ("failed", message)
    assert records(studio, "reviews") == []
    assert records(studio, "providers.tickets") == []
    assert [row for row in records(studio, "jobs") if row["kind"] == "vlm_review"] == []
    assert studio.vlm_state["requests"] == []
