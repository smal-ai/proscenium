"""Composed W02/W03/F00 receipt atomicity. No native renderer is launched."""

import io
from dataclasses import replace

import pytest
from PIL import Image
from proscenium.auth import Caller
from proscenium.catalogue import install as install_catalogue
from proscenium.db.store import Transaction
from proscenium.previews import install
from proscenium.previews.config import REVISION
from proscenium.previews.journal import atomic, sha
from proscenium.previews.service import worker_caller


def test_composed_frame_job_attempt_event_rollback(services, monkeypatch):
    services.settings = replace(services.settings, execution_profiles=frozenset({"blender-cpu"}))
    services.jobs.settings = services.settings
    install_catalogue(services)
    previews = install(services)
    services.gate.resume("project-test", reconciliation_evidence="Synthetic explicit admission")
    store = services.registry.project_store("project-test")
    with store.transaction() as tx:
        tx.put(
            "invocations",
            "atomic-invocation",
            {"work_id": "work-test", "owner_epoch": services.ownership.epoch, "state": "running"},
        )
        tx.put(
            "versions",
            "atomic-version",
            {
                "version_id": "atomic-version",
                "item_id": "atomic-item",
                "source": {"digest": "a" * 64},
            },
        )
    caller = Caller(
        "tool",
        "atomic-tool",
        "project-test",
        "work-test",
        services.ownership.epoch,
        operations=("requestPreview",),
        invocation_id="atomic-invocation",
    )
    spec = dict(
        intent_id="atomic-intent",
        shot_id="atomic-shot",
        purpose="keyframe",
        source_closure_digest="a" * 64,
        scene_name="Fixture",
        camera=None,
        frames={"start": 1, "end": 1},
        fps={"numerator": 24, "denominator": 1},
        dimensions={"width": 16, "height": 16},
        engine="CYCLES",
        device="CPU",
        samples=1,
        colour_management={"view_transform": "Standard", "look": "None", "exposure": 0, "gamma": 1},
        renderer_revision=REVISION,
        review_output={
            "media_type": "image/png",
            "encoding": "view_transformed_srgb",
            "bit_depth": 8,
        },
    )
    job, _ = previews.request(
        caller,
        dict(
            schema_version="1.0",
            operation_id="atomic-preview",
            project_id="project-test",
            version={"item_id": "atomic-item", "version_id": "atomic-version"},
            spec=spec,
        ),
    )
    root = previews.root(job)
    root.mkdir(parents=True)
    atomic(root / "task.json", {key: job[key] for key in ("job_id", "attempt_id", "input_digest")})
    image = io.BytesIO()
    Image.new("RGB", (16, 16), (12, 24, 48)).save(image, format="PNG")
    (root / "frame.png").write_bytes(image.getvalue())
    body = dict(
        schema_version="1.0",
        receipt_id="atomic-receipt",
        project_id="project-test",
        job_id=job["job_id"],
        attempt_id=job["attempt_id"],
        version_id="atomic-version",
        shot_id="atomic-shot",
        source_frame=1,
        artifact_id="atomic-artifact",
        output_path="frame.png",
        sha256=sha(image.getvalue()),
        dimensions=spec["dimensions"],
        input_digest=job["input_digest"],
    )
    with store.transaction(write=False) as tx:
        before = tx.watermark()
        attempt = tx.require("attempts", job["attempt_id"])
    original = Transaction.emit

    def fail_after_job_writes(tx, kind, *args, **kwargs):
        if kind == "job.state_changed":
            raise RuntimeError("Injected job event failure after frame/artefact/attempt writes")
        return original(tx, kind, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Transaction, "emit", fail_after_job_writes)
        with pytest.raises(RuntimeError, match="Injected job event"):
            previews.register(worker_caller(job), body)
    with store.transaction(write=False) as tx:
        assert tx.require("jobs", job["job_id"]) == job
        assert tx.require("attempts", job["attempt_id"]) == attempt
        assert tx.watermark() == before
        for namespace in ("artifacts", "frames", "preview_progress", "job_evidence"):
            assert tx.all(namespace) == []
    previews.register(worker_caller(job), body)
    with store.transaction(write=False) as tx:
        current = tx.require("jobs", job["job_id"])
        assert current["availability"] == "complete" and current["completed_frames"] == 1
        assert current["artifact_ids"] == ["atomic-artifact"]
        assert tx.require("attempts", job["attempt_id"])["state"] == "starting"
        assert tx.require("job_evidence", job["attempt_id"])["evidence"]
        assert tx.watermark() == before + 2
    assert previews.register(worker_caller(job), body)[1]
