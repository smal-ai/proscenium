import asyncio
import copy
import io
import json
from pathlib import Path

import pytest
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.contracts import validate
from proscenium.previews.assembly import expand, technical
from proscenium.sources.common import fingerprint
from proscenium.workflows.assembly import prepared


def sequence_fixture(env, monkeypatch):
    source = env.source
    version = env.version("sequence-v1", kind="sequence")
    services = env.services
    spec = copy.deepcopy(
        json.loads(Path("docs/contracts/examples/publication.json").read_text())["items"][0][
            "preview_requests"
        ][0]
    )
    spec.update(frames={"start": 1, "end": 2}, dimensions={"width": 8, "height": 8})
    artifacts = services.extension("artifacts")
    images = []
    for frame in (1, 2):
        output = io.BytesIO()
        Image.new("RGB", (8, 8), (frame, 50, 80)).save(output, format="PNG")
        images.append(
            artifacts.promote(
                "project-test",
                f"source-frame-{frame}",
                output.getvalue(),
                kind="image",
                purpose="keyframe",
                media_type="image/png",
            )
        )
    with services.registry.project_store("project-test").transaction() as tx:
        for artifact in images:
            artifacts.put(tx, artifact)
        job = services.jobs.create(
            tx,
            job_id="source-render",
            kind="render",
            target=dict(
                target_id=source["item_id"],
                version={"item_id": source["item_id"], "version_id": source["version_id"]},
                shot_id="shot-fixture",
            ),
            input_digest="e" * 64,
            operation_id="source-render-admission",
            work_id="work-test",
            render_spec=spec,
        )
        receipts = []
        for frame, artifact in enumerate(images, start=1):
            receipt = dict(
                schema_version="1.0",
                receipt_id=f"frame-{frame}",
                project_id="project-test",
                job_id=job["job_id"],
                attempt_id=job["attempt_id"],
                version_id=source["version_id"],
                shot_id="shot-fixture",
                source_frame=frame,
                artifact_id=artifact["artifact_id"],
                output_path=f"frames/{frame:08d}.png",
                sha256=artifact["sha256"],
                dimensions={"width": 8, "height": 8},
                input_digest=job["input_digest"],
            )
            tx.put("frames", receipt["receipt_id"], receipt, schema="FrameReceipt")
            receipts.append(receipt)
    sequence = dict(
        schema_version="1.0",
        project_id="project-test",
        sequence_version_id=version["version_id"],
        fps=spec["fps"],
        frame_count=2,
        silent=True,
        entries=[
            dict(
                entry_id="authored-cut",
                shot_id="shot-fixture",
                version_id=source["version_id"],
                job_id="source-render",
                source_frames={"start": 1, "end": 2},
                sequence_frames={"start": 1, "end": 2},
                transform="identity",
                mapping_artifact_id=None,
                choice="agent",
            )
        ],
    )
    monkeypatch.setattr(
        services.extension("sources"), "authored", lambda project_id, row: copy.deepcopy(sequence)
    )
    body = dict(
        operation_id="assembly-operation",
        job_id="assembly-job",
        version={"item_id": version["item_id"], "version_id": version["version_id"]},
        execution_profile_id="offline",
    )
    return sequence, receipts, body


def test_prepared_assembly_uses_exact_receipts_and_native_admission(environment, monkeypatch):
    env = environment
    sequence, receipts, body = sequence_fixture(env, monkeypatch)
    admission, found, mapping = prepared(env.services, env.caller, body)
    assert admission["input_digest"] == fingerprint(
        dict(
            sequence=technical(sequence),
            frame_map=expand(sequence, None),
            input_frames=sorted(receipts, key=lambda row: row["artifact_id"]),
        )
    )
    assert admission["input_artifact_ids"] == [row["artifact_id"] for row in receipts]
    assert found == sequence and mapping == expand(sequence, None)

    async def run():
        tool_result = await env.tools.invoke(
            "assemble_sequence", env.invocation("assemble_sequence", body)
        )
        job = json.loads(tool_result.text_result_for_llm)
        validate("JobRecord", job)
        assert job["kind"] == "encode" and job["state"] == "planned"
        with env.services.registry.project_store("project-test").transaction(write=False) as tx:
            snapshot = tx.require("preview_assemblies", job["job_id"])
            assert fingerprint(snapshot) == admission["input_digest"]
            assert tx.connection.execute(
                "SELECT 1 FROM outbox WHERE kind='preview.assemble'"
            ).fetchone()
        env.services.gate.close("project-test")
        monkeypatch.setattr(
            env.services.extension("catalogue"),
            "sequence_map",
            lambda *a: pytest.fail("No rediscovery on matching replay"),
        )
        assert (await env.workflows.assembly(env.caller, body))["job_id"] == job["job_id"]
        with pytest.raises(DomainError):
            await env.workflows.assembly(
                env.caller, {**body, "version": {**body["version"], "item_id": "another-item"}}
            )
        await env.workflows.close()

    asyncio.run(run())


def test_unpublished_item_or_missing_frame_is_not_guessed(environment, monkeypatch):
    env = environment
    _, _, body = sequence_fixture(env, monkeypatch)
    with pytest.raises(DomainError):
        prepared(
            env.services,
            env.caller,
            {**body, "version": {**body["version"], "item_id": "wrong-item"}},
        )
    with env.services.registry.project_store("project-test").transaction() as tx:
        tx.connection.execute("DELETE FROM records WHERE namespace='frames' AND id='frame-2'")
    with pytest.raises(DomainError):
        prepared(env.services, env.caller, body)
    asyncio.run(env.workflows.close())


def test_assembly_snapshot_ack_loss_is_observed_without_new_admission(environment, monkeypatch):
    env = environment
    _, _, body = sequence_fixture(env, monkeypatch)
    save = env.workflows.save

    def lost(project_id, identity, row):
        if row["phase"] == "launched":
            raise RuntimeError("Offline workflow acknowledgement lost after W03 commit")
        return save(project_id, identity, row)

    monkeypatch.setattr(env.workflows, "save", lost)

    async def run():
        with pytest.raises(RuntimeError):
            await env.workflows.assembly(env.caller, body)
        monkeypatch.setattr(env.workflows, "save", save)
        monkeypatch.setattr(
            env.services.extension("previews"),
            "assembly",
            lambda *a: pytest.fail("No second assembly admission"),
        )
        env.services.gate.close("project-test")
        await env.workflows.reconcile("project-test")
        assert await env.workflows.pending("project-test") == []
        assert (await env.workflows.assembly(env.caller, body))["job_id"] == body["job_id"]
        await env.workflows.close()

    asyncio.run(run())
