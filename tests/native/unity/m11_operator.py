"""Explicit F01 setup, original-authority replay and admitted native test fixtures, not Copilot."""

import asyncio
import json
import shutil
import time
from pathlib import Path

from proscenium.auth import Caller
from proscenium.sources import closure_digest
from proscenium.sources.common import git

from tools.unity.jobs import attempt_directory, reconcile_unity_job, run_unity_job
from tools.unity.workflow import input_digest, prepare, sha256, write_json

SOURCE = {"item_id": "m11-source", "version_id": "m11-source-v1"}
IMPORT_BODY = dict(
    operation_id="m11-import-operation",
    job_id="m11-import-job",
    version=SOURCE,
    execution_profile_id="unity-local",
    project="unity/M11",
    spawn=[0, 0.92, -2],
    camera="perspective",
    pipeline="urp",
    action="import",
)
F01 = Path("/tmp/opencode/proscenium-f01-20261114-a/historical")


def fixture_caller(services, action):
    project = services.registry.active_id()
    identity = "m11-operator-" + action
    with services.registry.project_store(project).transaction() as tx:
        assert tx.get("work", identity, required=False) is None, (
            "Never repeat an uncertain fixture command"
        )
        tx.put(
            "work",
            identity,
            dict(
                schema_version="1.0",
                project_id=project,
                work_id=identity,
                originating_request_id=identity + "-fixture-request",
                directive_request_ids=[],
                generation_task_id=identity + "-task",
                native_session_id="m11-operator-fixture-not-a-native-session",
                job_ids=[],
                state="running",
                state_revision=0,
            ),
            schema="WorkRecord",
        )
        tx.put(
            "invocations",
            identity,
            dict(
                work_id=identity,
                owner_epoch=services.ownership.epoch,
                state="running",
                fixture="operator acceptance, not SDK/model",
            ),
        )
    return Caller(
        "tool",
        identity,
        project,
        identity,
        services.ownership.epoch,
        operations=(
            "checkpoint_sources",
            "publishOutputs",
            "admitToolJob",
            "updateJobState",
            "registerToolArtifact",
        ),
        invocation_id=identity,
    )


def finish(services, caller):
    with services.registry.project_store(caller.project_id).transaction() as tx:
        row = tx.require("work", caller.work_id)
        row.update(state="completed", state_revision=row["state_revision"] + 1)
        tx.put(
            "work",
            caller.work_id,
            row,
            expected_revision=row["state_revision"] - 1,
            schema="WorkRecord",
        )
        invocation = tx.require("invocations", caller.invocation_id)
        invocation["state"] = "completed"
        tx.put(
            "invocations",
            caller.invocation_id,
            invocation,
            expected_revision=tx.revision("invocations", caller.invocation_id),
        )


def apply(services, run_root, command, loop):
    action = command["action"]
    project_id = services.registry.active_id()
    root = Path(services.registry.get(project_id)["path"])
    if action == "lean-observer":
        import sys

        host = sys.modules["__main__"]
        original = getattr(host, "append")

        def append(name, value):
            if name == "native-events.private.jsonl" and value.get("ephemeral"):
                return
            original(name, value)

        setattr(host, "append", append)
        return dict(
            fixture="Removed per-token fsync observation after health failure; actual production callbacks unchanged",
            at=time.time(),
            reason="Ephemeral stream observation is not acceptance evidence and can delay RPC reader",
        )
    if action == "seed":
        caller = fixture_caller(services, "seed")
        services.gate.resume(
            project_id, reconciliation_evidence="Explicit operator F01 setup only, not a cloud root"
        )
        files = ["assets/cube/asset.blend", "assets/cube/colour.png", "scenes/fixture/scene.blend"]
        for relative in files:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(F01 / relative, target)
        receipt = services.extension("sources").checkpoint(
            caller,
            dict(
                schema_version="1.0",
                operation_id="m11-fixture-source-checkpoint",
                project_id=project_id,
                paths=files,
                message="M11 operator fixture: copied historical F01, not model authored",
                expected_head=git(root, "rev-parse", "--verify", "HEAD", required=False)
                .decode()
                .strip()
                or None,
            ),
        )
        closure = dict(
            entry_path=files[-1],
            files=[
                dict(
                    destination=p,
                    origin=dict(
                        kind="git",
                        repository_id=services.registry.get(project_id)["repository_id"],
                        commit=receipt["source_commit"],
                        path=p,
                        sha256=sha256(root / p),
                    ),
                )
                for p in sorted(files)
            ],
            dependencies=[],
            required_runtime=dict(application="Blender", version="4.5.14 LTS", features=[]),
        )
        closure["digest"] = closure_digest(closure)
        publication, _ = services.extension("catalogue").publish(
            caller,
            dict(
                schema_version="1.0",
                publication_id="m11-fixture-source-publication",
                project_id=project_id,
                items=[
                    dict(
                        **SOURCE,
                        kind="scene",
                        name="M11 operator F01 floor wall two cubes",
                        source_commit=receipt["source_commit"],
                        source=closure,
                        expected_current_version_id=None,
                        artifact_ids=[],
                        preview_requests=[],
                        description="Scripted native F01 input fixture, not Copilot authoring. Metres: floor top 0, wall near face Blender Y=2.4, two 1m cubes.",
                        tags=["M11", "operator-fixture"],
                    )
                ],
            ),
        )
        finish(services, caller)
        services.gate.close(project_id)
        return dict(checkpoint=receipt, publication=publication, source=closure)
    if action == "state":
        with services.registry.project_store(project_id).transaction(write=False) as tx:
            return {
                key: tx.all(key)
                for key in (
                    "requests",
                    "work",
                    "jobs",
                    "attempts",
                    "versions",
                    "items",
                    "native_workflows",
                    "invocations",
                    "copilot.agents",
                    "copilot.hooks",
                    "copilot.resumes",
                    "copilot.control",
                )
            }
    if action == "wait-import":
        started = time.time()
        while time.time() - started < 630:
            job = services.jobs.get(project_id, "m11-import-job")
            if job["state"] in {"succeeded", "failed", "cancelled", "outcome_uncertain"}:
                attempt = attempt_directory(root, job["attempt_id"])
                result = (
                    json.loads((attempt / "unity-result.json").read_text())
                    if (attempt / "unity-result.json").exists()
                    else None
                )
                data = dict(
                    fixture="Read-only observation of terminal JobRecord updated by the actual production workflow watcher while SPA disconnected",
                    at=time.time(),
                    job=job,
                    unity=result,
                    evidence=str(attempt),
                )
                write_json(run_root / "import-observed.json", data)
                return data
            time.sleep(0.2)
        raise TimeoutError(
            "Import outcome not known inside allocated worker bound; do not relaunch"
        )
    if action == "replay":
        with services.registry.project_store(project_id).transaction(write=False) as tx:
            row = next(
                r for r in tx.all("native_workflows") if r["job_id"] == IMPORT_BODY["job_id"]
            )
            origin = row["binding"]
            record = tx.require("invocations", origin["invocation_id"])
        caller = Caller(
            "tool",
            "m11-original-authority-replay-fixture",
            project_id,
            origin["work_id"],
            record["owner_epoch"],
            operations=("admitToolJob", "updateJobState"),
            invocation_id=origin["invocation_id"],
        )
        before = native_snapshot(services, run_root)
        job = asyncio.run_coroutine_threadsafe(
            services.extension("workflows").unity(caller, origin["body"]), loop
        ).result(30)
        after = native_snapshot(services, run_root)
        assert before == after, "Original replay must not export, launch or duplicate scene roots"
        return dict(
            fixture="Actual original persisted authority replay, no extra root/child",
            job=job,
            before=before,
            after=after,
        )
    if action in {"repeat-proof", "playmode"}:
        with services.registry.project_store(project_id).transaction(write=False) as tx:
            row = next(
                r for r in tx.all("native_workflows") if r["job_id"] == IMPORT_BODY["job_id"]
            )
        caller = fixture_caller(services, action)
        services.gate.resume(
            project_id, reconciliation_evidence="Explicit bounded operator native acceptance job"
        )
        unity_action = "playtest" if action == "playmode" else "repeat-proof"
        if action == "playmode":
            prepare(root, "unity/M11", tests=True)
            manifest = root / "unity/M11/Packages/manifest.json"
            data = json.loads(manifest.read_text())
            data["dependencies"]["com.unity.test-framework"] = "1.6.0"
            write_json(manifest, data)
        admission = dict(
            schema_version="1.0",
            operation_id="m11-operator-" + action,
            job_id="m11-" + action + "-job",
            project_id=project_id,
            kind="unity",
            target=dict(target_id="m11-operator-verified-fixture", version=SOURCE, shot_id=None),
            input_digest=input_digest(
                dict(project="unity/M11", recipe=row["recipe"], action=unity_action)
            ),
            input_artifact_ids=[],
            execution_profile_id="unity-local",
        )
        result = run_unity_job(
            services,
            caller,
            admission,
            project="unity/M11",
            recipe=row["recipe"],
            editor=Path(services.settings.runtime["unity_editor"]),
            action=unity_action,
            wait=False,
        )
        finish(services, caller)
        write_json(run_root / (action + "-admission.json"), result)
        return result
    if action == "observe-operator":
        job = services.jobs.get(project_id, command["job_id"])
        caller = Caller(
            "worker",
            "m11-verified-operator-reconciler",
            project_id,
            job_id=job["job_id"],
            attempt_id=job["attempt_id"],
            input_digest=job["input_digest"],
            operations=("updateJobState",),
        )
        started = time.monotonic()
        while True:
            result = reconcile_unity_job(services, caller, job["job_id"])
            if (
                result["job"]["state"] in {"succeeded", "failed", "cancelled", "outcome_uncertain"}
                or time.monotonic() - started > 630
            ):
                break
            time.sleep(0.2)
        if job["state"] in {"succeeded", "failed", "cancelled"} or result["job"]["state"] in {
            "succeeded",
            "failed",
            "cancelled",
        }:
            write_json(run_root / (job["job_id"] + "-result.json"), result)
        return result
    if action == "attest":
        result = json.loads((run_root / "m11-playmode-job-result.json").read_text())
        assert (
            result["job"]["state"] == "succeeded"
            and result["unity"]["validation"]["status"] == "passed"
        )
        caller = fixture_caller(services, "attest")
        services.gate.resume(
            project_id, reconciliation_evidence="Verified operator test evidence registration only"
        )
        attempt = Path(result["evidence"])
        data = dict(
            fixture="Actual separate operator F05 PlayMode job, not generic import validation",
            job=result["job"],
            unity=result["unity"],
            metrics=(attempt / "unity-result.json.metrics.txt").read_text(),
            xml_sha256=sha256(attempt / "playmode.xml"),
            source_version=SOURCE,
        )
        metadata = root / "unity/M11/M11-operator-playmode.json"
        write_json(metadata, data)
        artifacts = []
        for suffix, path, media in [
            ("attestation", metadata, "application/json"),
            ("xml", attempt / "playmode.xml", "application/xml"),
            ("metrics", attempt / "unity-result.json.metrics.txt", "text/plain"),
        ]:
            staged = root / ".proscenium/tool-output/staging" / ("m11-" + suffix)
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, staged)
            artifact, _ = services.extension("artifacts").register(
                caller,
                dict(
                    schema_version="1.0",
                    operation_id="m11-register-" + suffix,
                    project_id=project_id,
                    artifact_id="m11-playmode-" + suffix,
                    staging_path=staged.relative_to(root).as_posix(),
                    kind="log" if suffix != "attestation" else "document",
                    purpose="log" if suffix != "attestation" else "metadata",
                    sha256=sha256(staged),
                    media_type=media,
                    derived_from=[],
                ),
            )
            artifacts.append(artifact)
        finish(services, caller)
        return dict(attestation=data, artifacts=artifacts)
    raise ValueError(action)


def native_snapshot(services, run_root):
    root = Path(services.registry.get(services.registry.active_id())["path"])
    with services.registry.project_store(services.registry.active_id()).transaction(
        write=False
    ) as tx:
        ids = {
            kind: [r["job_id" if kind == "jobs" else "attempt_id"] for r in tx.all(kind)]
            for kind in ("jobs", "attempts")
        }
    return dict(
        ids=ids,
        unity_claims=len(list(run_root.glob("unity-*.claim"))),
        exports=len(list(root.glob(".proscenium/jobs/native-workflows/*/export-request.json"))),
        meta={
            p.relative_to(root).as_posix(): sha256(p)
            for p in (root / "unity/M11/Assets").rglob("*.meta")
        },
        scenes={
            p.relative_to(root).as_posix(): sha256(p)
            for p in (root / "unity/M11/Assets").rglob("*.unity")
        },
    )
