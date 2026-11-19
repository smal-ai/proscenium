"""Explicit outbox dispatch and passive reconciliation for exact admitted attempts."""

import json
import os
import subprocess
import sys
from pathlib import Path

from proscenium.api.errors import DomainError
from proscenium.db.outbox import dispatch_one
from proscenium.jobs import TERMINAL
from proscenium.jobs.resources import check_storage
from proscenium.sources.common import fingerprint

from .journal import alive, atomic, read, renderer_hashes, sha
from .service import worker_caller

RESOURCE = "blender-render-writer"
MEDIA_VALIDATION_FAILED = "media_validation_failed"
INVALID_RECEIPT_CODES = frozenset(
    {
        "media_unavailable",
        "hash_mismatch",
        "invalid_frame_mapping",
        "frame_conflict",
        "invalid_request",
    }
)


def resource_for(job: dict) -> str:
    return RESOURCE if job["kind"] == "render" else "ffmpeg-encode-writer"


def release_owned(services, project_id: str, job: dict, evidence: str) -> None:
    resource = resource_for(job)
    with services.resources.store.transaction(write=False) as tx:
        row = tx.connection.execute(
            "SELECT project_id,job_id,attempt_id FROM resource_claims WHERE resource=?", (resource,)
        ).fetchone()
    if row and tuple(row) == (project_id, job["job_id"], job["attempt_id"]):
        services.resources.release(
            resource, project_id, job["job_id"], job["attempt_id"], terminal_evidence=evidence
        )


def launch_assembly(service, project_id: str, body: dict) -> dict:
    services = service.services
    services.gate.require(project_id)
    job = services.jobs.get(project_id, body["job_id"])
    if job["attempt_id"] != body["attempt_id"] or job["input_digest"] != body["input_digest"]:
        raise DomainError("scope_denied", 403)
    if job["state"] in TERMINAL:
        return {"terminal": job["state"], "launched": False}
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        assembly = tx.require("preview_assemblies", job["job_id"])
    if fingerprint(assembly) != job["input_digest"]:
        raise DomainError("hash_mismatch", 422)
    root = service.root(job)
    if (root / "launch.claim").exists():
        raise DomainError("outcome_uncertain", 409)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = service.runtime(project_id, job)
    check_storage(root, services.settings.free_space_reserve_bytes)
    if not services.resources.acquire(
        resource_for(job), project_id, job["job_id"], job["attempt_id"]
    ):
        raise DomainError("owner_conflict", 409)
    from proscenium.sources.common import atomic_bytes

    for row in assembly["input_frames"]:
        data = service.artifacts.ready_path(project_id, row["artifact_id"]).read_bytes()
        if sha(data) != row["sha256"]:
            raise DomainError("hash_mismatch", 422)
        atomic_bytes(root / "assembly-inputs" / (row["sha256"] + ".png"), data)
    task = {
        "config": config,
        "assembly": assembly,
        **{key: job[key] for key in ("project_id", "job_id", "attempt_id", "input_digest")},
    }
    atomic(root / "task.json", task)
    fd = os.open(root / "launch.claim", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    service.state(job, "starting")
    with (root / "supervisor.log").open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "proscenium.previews.worker", str(root)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return {"pid": process.pid, "attempt_id": job["attempt_id"]}


def launch(service, project_id: str, body: dict) -> dict:
    services = service.services
    services.gate.require(project_id)
    job = services.jobs.get(project_id, body["job_id"])
    if job["attempt_id"] != body["attempt_id"] or job["input_digest"] != body["input_digest"]:
        raise DomainError("scope_denied", 403)
    if job["state"] in TERMINAL:
        return {"terminal": job["state"], "launched": False}
    root = service.root(job)
    if (root / "launch.claim").exists():
        raise DomainError("outcome_uncertain", 409, "Original launch must be reconciled")
    service.config.validate(job["render_spec"])
    config = service.runtime(project_id, job)
    if config["profile_id"] not in services.settings.execution_profiles:
        raise DomainError("capability_unavailable", 501)
    inputs = services.jobs.inputs(project_id, job["job_id"])
    version = services.extension("catalogue").version(
        project_id, job["target"]["version"]["version_id"]
    )
    if inputs["source"] != version["source"] or job["input_digest"] != fingerprint(
        {"version": job["target"]["version"], "render_spec": job["render_spec"]}
    ):
        raise DomainError("hash_mismatch", 422)
    source = services.extension("sources").materialise(project_id, version["version_id"])
    check_storage(
        source,
        services.settings.free_space_reserve_bytes,
        job["expected_frames"]
        * job["render_spec"]["dimensions"]["width"]
        * job["render_spec"]["dimensions"]["height"]
        * 4,
    )
    if not services.resources.acquire(RESOURCE, project_id, job["job_id"], job["attempt_id"]):
        return {"state": "waiting_resource", "launched": False}
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    task = dict(
        project_id=project_id,
        job_id=job["job_id"],
        attempt_id=job["attempt_id"],
        input_digest=job["input_digest"],
        version_id=version["version_id"],
        spec=job["render_spec"],
        closure=inputs["source"],
        config=config,
        source_root=str(source),
        proxy_artifact_id=job["job_id"] + "-proxy",
    )
    task["renderer_files_sha256"] = renderer_hashes()
    task["entrypoint_sha256"] = task["renderer_files_sha256"]["blender_worker.py"]
    atomic(root / "task.json", task)
    # Retries reuse only already registered, decoded, same-input immutable frames.
    # No old-attempt journal can inject a new file into this attempt.
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        reusable = service.records(tx, job, "frames")
    from proscenium.sources.common import atomic_bytes

    for row in reusable:
        data = service.artifacts.ready_path(project_id, row["artifact_id"]).read_bytes()
        if sha(data) != row["sha256"]:
            raise DomainError("hash_mismatch", 422)
        path = root / f"frames/{row['source_frame']:08d}.png"
        atomic_bytes(path, data)
        adopted = {
            **row,
            "attempt_id": job["attempt_id"],
            "receipt_id": f"{job['attempt_id']}-f{row['source_frame']}",
            "output_path": path.relative_to(root).as_posix(),
        }
        atomic(root / f"frame-{row['source_frame']:08d}.json", adopted)
    # O_EXCL fences the spawn-before-ack crash window. Absence of a receipt is never a relaunch grant.
    fd = os.open(root / "launch.claim", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    service.state(job, "starting")
    with (root / "supervisor.log").open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "proscenium.previews.worker", str(root)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=source,
        )
    # Acknowledgement is dispatch evidence, not a native exit or completed job.
    return {"pid": process.pid, "attempt_id": job["attempt_id"], "root": str(root)}


def cancel(service, project_id: str, body: dict) -> dict:
    job = service.services.jobs.get(project_id, body["job_id"])
    if job["attempt_id"] != body["attempt_id"]:
        raise DomainError("scope_denied", 403)
    root = service.root(job)
    if job["state"] in TERMINAL:
        return {"terminal": job["state"]}
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if cancellation_marker(root, job).get("reason") != MEDIA_VALIDATION_FAILED:
        atomic(root / "cancel.json", {"attempt_id": job["attempt_id"]})
    if not (root / "launch.claim").exists():
        service.state(job, "cancelled", evidence="Verified cancellation before any owned launch")
        return {"terminal": "cancelled", "launched": False}
    # A live supervisor observes this marker and terminates only its owned native group.
    # If its identity is absent/uncertain, do not claim cancellation or kill a guessed PID.
    return {"cancellation_marker": True, "terminal_proven": (root / "terminal.json").exists()}


def pump(service, project_id: str) -> list[dict]:
    services = service.services
    store = services.registry.project_store(project_id)
    with store.transaction() as tx:
        jobs = {
            j["job_id"]: j
            for j in tx.all("jobs")
            if j["kind"] == "render" or tx.get("preview_assemblies", j["job_id"], required=False)
        }
        # F00 retry allocates a new attempt but deliberately does not own feature dispatch.
        for job in jobs.values():
            if job["state"] == "planned" and job["attempt_number"] > 1:
                row = tx.connection.execute(
                    "SELECT id FROM outbox WHERE id=?", (job["attempt_id"],)
                ).fetchone()
                if row is None:
                    tx.enqueue(
                        job["attempt_id"],
                        "preview.render" if job["kind"] == "render" else "preview.assemble",
                        {key: job[key] for key in ("job_id", "attempt_id", "input_digest")},
                    )
        pending = [
            dict(row)
            for row in tx.connection.execute(
                "SELECT id,kind,body FROM outbox WHERE state='pending' AND kind IN ('preview.render','preview.assemble','job.cancel')"
            )
        ]
    results = []
    pending.sort(key=lambda row: row["kind"] != "job.cancel")
    for row in pending:
        body = json.loads(row["body"])
        job = jobs.get(body["job_id"])
        if not job:
            continue
        if row["kind"] in {"preview.render", "preview.assemble"} and job["state"] not in TERMINAL:
            services.gate.require(project_id)
            # Do not consume a resource-blocked outbox intent as though it launched.
            if not services.resources.acquire(
                resource_for(job), project_id, job["job_id"], job["attempt_id"]
            ):
                continue

        def guarded_launch(value: dict) -> dict:
            try:
                return (launch if row["kind"] == "preview.render" else launch_assembly)(
                    service, project_id, value
                )
            except Exception:
                # Proven pre-spawn admission rejection may fail, never an ambiguous spawn.
                if not (service.root(job) / "launch.claim").exists():
                    service.state(
                        job,
                        "failed",
                        evidence="Trusted prelaunch rejection, no launch claim created",
                    )
                    services.resources.release(
                        resource_for(job),
                        project_id,
                        job["job_id"],
                        job["attempt_id"],
                        terminal_evidence="Verified rejection before spawn",
                    )
                    return {"rejected_before_launch": True}
                raise

        dispatch_one(
            store,
            row["id"],
            services.gate,
            {
                "preview.render": guarded_launch,
                "preview.assemble": guarded_launch,
                "job.cancel": lambda value: cancel(service, project_id, value),
            },
        )
        results.append({"intent_id": row["id"]})
    return results


def publish_proxy(service, job: dict, root: Path) -> list[str]:
    proof = read(root / "proxy-ready.json")
    glb = (root / "proxy.glb").read_bytes()
    mapping_bytes = (root / "proxy-map.json").read_bytes()
    if sha(glb) != proof["proxy_sha256"] or sha(mapping_bytes) != proof["mapping_sha256"]:
        raise DomainError("hash_mismatch", 422)
    mapping = json.loads(mapping_bytes)
    from proscenium.contracts import validate

    validate("ProxyMapping", mapping)
    task = read(root / "task.json")
    if (
        mapping["version_id"] != job["target"]["version"]["version_id"]
        or mapping["source_closure_digest"] != task["closure"]["digest"]
        or mapping["proxy_artifact_id"] != task["proxy_artifact_id"]
    ):
        raise DomainError("scope_denied", 403)
    artifacts = [
        service.artifacts.promote(
            job["project_id"],
            task["proxy_artifact_id"],
            glb,
            kind="proxy",
            purpose="inspection",
            media_type="model/gltf-binary",
            metadata={"source_version_id": mapping["version_id"]},
        ),
        service.artifacts.promote(
            job["project_id"],
            task["proxy_artifact_id"] + "-map",
            mapping_bytes,
            kind="proxy_map",
            purpose="metadata",
            media_type="application/json",
            metadata={"source_version_id": mapping["version_id"]},
        ),
    ]
    with service.services.registry.project_store(job["project_id"]).transaction() as tx:
        current = tx.require("jobs", job["job_id"])
        if current["attempt_id"] != job["attempt_id"]:
            raise DomainError("scope_denied", 403)
        for artifact in artifacts:
            service.artifacts.put(tx, artifact)
        version_id = mapping["version_id"]
        previous = tx.get("version_links", version_id, required=False)
        links = list(
            dict.fromkeys(
                (previous or {}).get("artifact_ids", []) + [a["artifact_id"] for a in artifacts]
            )
        )
        if not previous or previous.get("artifact_ids") != links:
            service.update_version_links(tx, version_id, artifact_ids=links)
    return [a["artifact_id"] for a in artifacts]


def publish_assembly(service, job: dict, root: Path) -> list[str]:
    from .media import verify_video

    task = read(root / "task.json")
    snapshot = task["assembly"]
    manifest = read(root / "assembly-manifest.json")
    mapping = (root / "frame-map.json").read_bytes()
    data = (root / "delivery.mp4").read_bytes()
    if (
        fingerprint(snapshot) != job["input_digest"]
        or manifest["input_digest"] != job["input_digest"]
        or manifest["video_sha256"] != sha(data)
        or manifest["frame_map_sha256"] != sha(mapping)
        or json.loads(mapping) != snapshot["frame_map"]
    ):
        raise DomainError("hash_mismatch", 422)
    frame_map = snapshot["frame_map"]
    dimensions = snapshot["input_frames"][0]["dimensions"]
    verify_video(
        root / "delivery.mp4",
        frame_map["fps"],
        frame_map["frame_count"],
        dimensions,
        task["config"],
    )
    lineage = [
        {
            "kind": "encode",
            "input_artifact_ids": sorted({r["artifact_id"] for r in snapshot["input_frames"]}),
            "parameters": {"frame_map_sha256": sha(mapping), "blend_space": "linear_rgb"},
        }
    ]
    metadata = {"source_version_id": frame_map["sequence_version_id"]}
    artifacts = [
        service.artifacts.promote(
            job["project_id"],
            job["job_id"] + "-delivery",
            data,
            kind="video",
            purpose="final",
            media_type="video/mp4",
            derived_from=lineage,
            metadata={
                **metadata,
                "dimensions": dimensions,
                "fps": frame_map["fps"],
                "frame_count": frame_map["frame_count"],
            },
        ),
        service.artifacts.promote(
            job["project_id"],
            job["job_id"] + "-frame-map",
            mapping,
            kind="frame_map",
            purpose="metadata",
            media_type="application/json",
            metadata=metadata,
        ),
        service.artifacts.promote(
            job["project_id"],
            job["job_id"] + "-manifest",
            (root / "assembly-manifest.json").read_bytes(),
            kind="document",
            purpose="metadata",
            media_type="application/json",
            metadata=metadata,
        ),
    ]
    with service.services.registry.project_store(job["project_id"]).transaction() as tx:
        current = tx.require("jobs", job["job_id"])
        if current["attempt_id"] != job["attempt_id"]:
            raise DomainError("scope_denied", 403)
        for artifact in artifacts:
            service.artifacts.put(tx, artifact)
        version_id = frame_map["sequence_version_id"]
        previous = tx.get("version_links", version_id, required=False)
        links = list(
            dict.fromkeys(
                (previous or {}).get("artifact_ids", []) + [a["artifact_id"] for a in artifacts]
            )
        )
        if not previous or previous.get("artifact_ids") != links:
            service.update_version_links(tx, version_id, artifact_ids=links)
    return [a["artifact_id"] for a in artifacts]


def cancellation_marker(root: Path, job: dict) -> dict:
    try:
        marker = read(root / "cancel.json")
    except (OSError, TypeError, ValueError):
        return {}
    return marker if marker.get("attempt_id") == job["attempt_id"] else {}


def verified_terminal(root: Path, task_sha256: str) -> dict | None:
    try:
        terminal = read(root / "terminal.json")
    except FileNotFoundError:
        return None
    if terminal.get("task_sha256") != task_sha256:
        raise DomainError("hash_mismatch", 422)
    if terminal.get("state") not in TERMINAL or (
        "native_exit_code" not in terminal
        or (
            terminal["native_exit_code"] is not None
            and type(terminal["native_exit_code"]) is not int
        )
    ):
        raise DomainError("invalid_request", 422, "Invalid supervisor terminal journal")
    return terminal


def register_frames(service, caller, root: Path) -> None:
    for path in sorted(root.glob("frame-*.json")):
        if path.stem.removeprefix("frame-").isdigit():
            service.register(caller, read(path))


def reconcile(service, project_id: str) -> list[dict]:
    services = service.services
    store = services.registry.project_store(project_id)
    with store.transaction(write=False) as tx:
        jobs = [
            j
            for j in tx.all("jobs")
            if (j["kind"] == "render" or tx.get("preview_assemblies", j["job_id"], required=False))
        ]

    def reconcile_job(job: dict) -> dict | None:
        root = service.root(job)
        if not (root / "task.json").exists():
            return None
        task = read(root / "task.json")
        if any(task[key] != job[key] for key in ("job_id", "attempt_id", "input_digest")):
            raise DomainError("scope_denied", 403)
        task_sha256 = sha((root / "task.json").read_bytes())
        # A terminal journal seals the receipt set. If it arrives during this scan,
        # settle on the next tick, after scanning the full sealed set.
        terminal = verified_terminal(root, task_sha256)
        if job["state"] in TERMINAL:
            if terminal is not None:
                release_owned(
                    services,
                    project_id,
                    job,
                    "Recovered claim release from verified terminal journal",
                )
            return None
        process_path = root / "process.json"
        if process_path.exists():
            process = read(process_path)
            if process["task_sha256"] != task_sha256:
                raise DomainError("hash_mismatch", 422)
            if alive(process) and job["state"] in {"starting", "outcome_uncertain"}:
                job = service.state(
                    job, "running", evidence="Exact supervisor PID/start/argv observed"
                )
            if alive(process) or (root / "terminal.json").exists():
                with store.transaction() as tx:
                    row = tx.connection.execute(
                        "SELECT state,kind FROM outbox WHERE id=?", (job["attempt_id"],)
                    ).fetchone()
                    if (
                        row
                        and row[0] in {"dispatching", "outcome_uncertain"}
                        and row[1] in {"preview.render", "preview.assemble"}
                    ):
                        tx.settle_intent(
                            job["attempt_id"],
                            "acknowledged",
                            {
                                "reconciled_original_process": process["pid"],
                                "start_ticks": process["start_ticks"],
                            },
                        )
        # Frames first, then segments, irrespective of directory order or receipt arrival order.
        caller = worker_caller(job)
        try:
            register_frames(service, caller, root)
            for path in sorted(root.glob("segment-*.json")):
                segment = read(path)
                try:
                    service.register(caller, segment, segment=True)
                except DomainError as exc:
                    if exc.code != "dependency_missing":
                        raise
                    register_frames(service, caller, root)
                    try:
                        service.register(caller, segment, segment=True)
                    except DomainError as retry:
                        if retry.code != "dependency_missing" or terminal is not None:
                            raise
                        continue
        except (DomainError, ValueError, OSError) as exc:
            if isinstance(exc, DomainError) and exc.code not in INVALID_RECEIPT_CODES:
                if exc.code != "dependency_missing" or terminal is None:
                    raise
            # Corrupt/unusable output does not erase earlier published frames. While the
            # exact worker is live, request cancellation rather than permit an unsafe retry.
            terminal = terminal or verified_terminal(root, task_sha256)
            if terminal is None:
                atomic(
                    root / "cancel.json",
                    {"attempt_id": job["attempt_id"], "reason": MEDIA_VALIDATION_FAILED},
                    immutable=False,
                )
                if job["state"] not in TERMINAL and job["state"] != "cancel_requested":
                    job = service.state(
                        job,
                        "cancel_requested",
                        evidence="Media validation rejected worker receipt; cancellation requested",
                    )
                raise
            job = service.state(
                job,
                "failed",
                exit_code=terminal["native_exit_code"],
                evidence="Terminal worker observed, media validation rejected output",
            )
            release_owned(services, project_id, job, "Terminal worker and rejected media verified")
            return job
        extra = publish_proxy(service, job, root) if (root / "proxy-ready.json").exists() else []
        if (root / "assembly-ready.json").exists():
            extra = publish_assembly(service, job, root)
        if terminal is not None:
            state = terminal["state"]
            evidence = "Owned detached supervisor terminal journal verified"
            marker = cancellation_marker(root, job)
            if marker.get("reason") == MEDIA_VALIDATION_FAILED:
                state = "failed"
                evidence = "Terminal worker observed after media validation requested cancellation"
            elif state == "cancelled":
                if not marker:
                    state = "failed"
                    evidence = (
                        "Supervisor reported cancelled without matching attempt cancellation marker"
                    )
                elif job["state"] not in {"cancel_requested", "outcome_uncertain"}:
                    job = service.state(
                        job,
                        "cancel_requested",
                        evidence="Verified attempt cancellation marker reconciled before terminal state",
                    )
            job = service.state(
                job,
                state,
                exit_code=terminal["native_exit_code"],
                evidence=evidence,
                extra_artifacts=extra,
                error_code=terminal.get("error_code"),
            )
            release_owned(services, project_id, job, "Verified owned supervisor terminal journal")
        elif process_path.exists() and not alive(read(process_path)):
            if job["state"] != "outcome_uncertain":
                job = service.state(
                    job, "outcome_uncertain", evidence="Supervisor identity no longer live"
                )
        elif process_path.exists() and job["state"] == "running":
            with store.transaction(write=False) as tx:
                count = len(service.records(tx, job, "frames"))
            if count != job["completed_frames"]:
                job = service.state(job, "running")
        elif (
            not process_path.exists()
            and (root / "launch.claim").exists()
            and job["state"] != "outcome_uncertain"
        ):
            job = service.state(
                job,
                "outcome_uncertain",
                evidence="Launch claim exists but original supervisor identity is not yet provable",
            )
        return job

    results = []
    deferred_error: DomainError | None = None
    for job in jobs:
        try:
            result = reconcile_job(job)
        except DomainError as exc:
            if deferred_error is None:
                deferred_error = exc
        except (ValueError, OSError, KeyError, TypeError):
            # Keep malformed journals visible to lifecycle callers without killing the watcher.
            if deferred_error is None:
                deferred_error = DomainError("invalid_request", 422, "Invalid preview journal")
        else:
            if result is not None:
                results.append(result)
    if deferred_error is not None:
        raise deferred_error
    return results
