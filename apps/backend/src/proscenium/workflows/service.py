"""Trusted native workflows. Explicit async lifecycle, no implicit watcher or prompt parser."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from threading import RLock

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.db import Transaction
from proscenium.services import Services
from proscenium.sources.common import active, admitted, binding, fingerprint, managed
from tools.unity.jobs import (
    attempt_directory,
    cancel_unity_job,
    reap_unity_supervisors,
    reconcile_unity_job,
    run_unity_job,
)
from tools.unity.worker import alive, editor_running, process_identity
from tools.unity.workflow import (
    bootstrap_project,
    existing_recipe,
    input_digest,
    make_recipe,
    prepare,
    scoped,
    source_key,
    unity_path,
    verify_materialisation,
    write_json,
)

from . import assembly, export_worker

NAMESPACE = "native_workflows"
TERMINAL = {"succeeded", "failed", "cancelled"}


def worker(job: dict) -> Caller:
    return Caller(
        "worker",
        "unity-workflow-reconciler",
        job["project_id"],
        job_id=job["job_id"],
        attempt_id=job["attempt_id"],
        input_digest=job["input_digest"],
        operations=("updateJobState",),
    )


class Workflows:
    def __init__(self, services: Services):
        self.services = services
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="native-workflow-io")
        self._io_jobs: set[asyncio.Future] = set()
        self._calls: set[asyncio.Task] = set()
        self._exports: dict[str, asyncio.subprocess.Process] = {}
        self._inflight: set[str] = set()
        self._closed = False
        self._close_complete = False
        self._activity_lock = RLock()
        self._sync_calls = 0

    @asynccontextmanager
    async def operation(self):
        task = asyncio.current_task()
        assert task is not None
        with self._activity_lock:
            if self._closed:
                raise DomainError("capability_unavailable", 503)
            self._calls.add(task)
        try:
            yield
        finally:
            with self._activity_lock:
                self._calls.discard(task)

    async def io(self, function, *args):
        with self._activity_lock:
            if self._closed:
                raise DomainError("capability_unavailable", 503)
            future = asyncio.get_running_loop().run_in_executor(self._pool, function, *args)
            self._io_jobs.add(future)

        def finished(value):
            with self._activity_lock:
                self._io_jobs.discard(value)
            if not value.cancelled():
                value.exception()  # Consume cancelled callback's eventual exception too.

        future.add_done_callback(finished)
        # A cancelled SDK callback does not detach an invisible writer. close() drains
        # this exact future before the parent releases Services/SQLite ownership.
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Native Stop must not report this callback quiescent while its source/
            # authoring writer still runs. Shutdown also retains/drains the future.
            try:
                await asyncio.shield(future)
            except Exception:
                pass
            raise

    @asynccontextmanager
    async def unity_operation(self, identity: str):
        async with self.operation():
            if identity in self._inflight:
                raise DomainError("project_busy", 409)
            self._inflight.add(identity)
            try:
                yield
            finally:
                self._inflight.discard(identity)

    def root(self, project_id: str) -> Path:
        if self.services.registry.active_id() != project_id:
            raise DomainError("scope_denied", 403)
        root = Path(self.services.registry.get(project_id)["path"])
        if root.resolve(strict=True) != root:
            raise DomainError("project_location_conflict", 409)
        return root

    def save(self, project_id: str, identity: str, row: dict) -> None:
        with self.services.registry.project_store(project_id).transaction() as tx:
            old = tx.get(NAMESPACE, identity, required=False)
            tx.put(
                NAMESPACE,
                identity,
                row,
                expected_revision=tx.revision(NAMESPACE, identity) if old else None,
            )

    def row_root(self, row: dict) -> Path:
        root = self.root(row["project_id"])
        if row.get("root", str(root)) != str(root):
            raise DomainError("project_location_conflict", 409)
        return root

    def begin(self, caller: Caller, body: dict, kind: str) -> tuple[str, dict, bool]:
        project_id = str(caller.project_id)
        active(self.services, caller, "admitToolJob", project_id)
        identity = fingerprint(dict(kind=kind, operation_id=body["operation_id"]))
        immutable = binding(caller, body)
        with self.services.registry.project_store(project_id).transaction() as tx:
            old = tx.get(NAMESPACE, identity, required=False)
            if old:
                if old["binding"] != immutable:
                    raise DomainError("idempotency_conflict", 409)
                return identity, old, False
            admitted(self.services, tx, caller)
            index = tx.get("native_workflow_jobs", body["job_id"], required=False)
            if index is not None or tx.get("jobs", body["job_id"], required=False) is not None:
                raise DomainError("idempotency_conflict", 409)
            row = dict(
                kind=kind,
                binding=immutable,
                phase="preparing",
                job_id=body["job_id"],
                project_id=project_id,
            )
            tx.put(NAMESPACE, identity, row)
            tx.put("native_workflow_jobs", body["job_id"], {"identity": identity, "kind": kind})
            return identity, row, True

    def require_effect(self, caller: Caller) -> None:
        active(self.services, caller, "admitToolJob", str(caller.project_id))
        with self.services.registry.project_store(str(caller.project_id)).transaction(
            write=False
        ) as tx:
            admitted(self.services, tx, caller)

    def existing_job(self, row: dict) -> dict:
        self.row_root(row)
        job = self.services.jobs.get(row["project_id"], row["job_id"])
        validate("JobRecord", job)
        if "admission" in row and any(
            job[field] != row["admission"][field]
            for field in ("project_id", "job_id", "kind", "input_digest", "target")
        ):
            raise DomainError("source_conflict", 409)
        return job

    async def replayable(self, caller: Caller, body: dict, kind: str) -> bool:
        """Only a proved existing effect may bypass native new-effect/fence admission."""
        async with self.operation():
            return await self.io(self._replayable, caller, body, kind)

    def _replayable(self, caller: Caller, body: dict, kind: str) -> bool:
        project_id = str(caller.project_id)
        active(self.services, caller, "admitToolJob", project_id)
        identity = fingerprint(dict(kind=kind, operation_id=body["operation_id"]))
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            row = tx.get(NAMESPACE, identity, required=False)
            if row is None:
                return False
            if row["binding"] != binding(caller, body):
                raise DomainError("idempotency_conflict", 409)
            job = tx.get("jobs", row["job_id"], required=False)
        if job is None:
            return False
        if kind == "assembly":
            return row["phase"] == "launched" or self._assembly_committed(row)
        return (
            job["state"] != "planned"
            or attempt_directory(self.root(project_id), job["attempt_id"]).exists()
        )

    async def assembly(self, caller: Caller, body: dict) -> dict:
        async with self.operation():
            identity, row, _ = await self.io(self.begin, caller, body, "assembly")
            if row["phase"] == "launched" or await self.io(self._assembly_committed, row):
                if row["phase"] != "launched":
                    row["phase"] = "launched"
                    await self.io(self.save, row["project_id"], identity, row)
                return await self.io(self.existing_job, row)
            await self.io(self.require_effect, caller)
            if row["phase"] != "prepared":
                try:
                    admission, sequence, frame_map = await self.io(
                        assembly.prepared, self.services, caller, body
                    )
                except DomainError as error:
                    row.update(phase="failed", error_code=error.code)
                    await self.io(self.save, row["project_id"], identity, row)
                    raise
                row.update(
                    phase="prepared", admission=admission, sequence=sequence, frame_map=frame_map
                )
                await self.io(self.save, row["project_id"], identity, row)
            result = await self.io(self._admit_assembly, caller, row)
            row["phase"] = "launched"
            await self.io(self.save, row["project_id"], identity, row)
            return result

    def _admit_assembly(self, caller: Caller, row: dict) -> dict:
        return self.services.extension("previews").assembly(
            caller, row["admission"], row["sequence"], row["frame_map"]
        )[0]

    def _assembly_committed(self, row: dict) -> bool:
        if "admission" not in row:
            return False
        with self.services.registry.project_store(row["project_id"]).transaction(write=False) as tx:
            snapshot = tx.get("preview_assemblies", row["job_id"], required=False)
            origin = tx.get("preview_assembly_origins", row["job_id"], required=False)
        if snapshot is None:
            return False
        if fingerprint(snapshot) != row["admission"]["input_digest"] or origin != {
            key: row["binding"][key] for key in ("work_id", "invocation_id")
        }:
            raise DomainError("idempotency_conflict", 409)
        self.existing_job(row)  # Prove original input/target identity too.
        return True

    def _prepare_unity(self, caller: Caller, body: dict, identity: str, row: dict) -> dict:
        self.require_effect(caller)
        root = self.root(row["project_id"])
        configured_editor = self.services.settings.runtime.get("unity_editor")
        if (
            not isinstance(configured_editor, str)
            or not Path(configured_editor).is_absolute()
            or not Path(configured_editor).is_file()
        ):
            raise DomainError("capability_unavailable", 501)
        if body["execution_profile_id"] not in self.services.settings.execution_profiles:
            raise DomainError("capability_unavailable", 501)
        if body["project"].startswith("unity/exports"):
            raise DomainError("scope_denied", 403)
        project = unity_path(root, body["project"])
        if editor_running(project):
            raise DomainError("project_busy", 409)
        version = self.services.extension("catalogue").version(
            row["project_id"], body["version"]["version_id"]
        )
        validate("VersionRecord", version)
        if version["project_id"] != row["project_id"]:
            raise DomainError("scope_denied", 403)
        if version["item_id"] != body["version"]["item_id"]:
            raise DomainError("source_conflict", 409)
        if version["kind"] == "unity_result":
            row["editor"] = str(Path(configured_editor).resolve(strict=True))
            row = self._unity_admission(row, existing_recipe(root, body, version))
            self.save(row["project_id"], identity, row)
            return row
        if version["kind"] not in {
            "scene",
            "asset",
        }:
            raise DomainError("source_conflict", 409)
        configured_blender = self.services.settings.rendering.get("blender")
        blender = shutil.which(configured_blender) if isinstance(configured_blender, str) else None
        if not blender:
            raise DomainError("capability_unavailable", 501)
        claim_path = self.services.settings.rendering.get("claim_path")
        if claim_path is not None and (
            not isinstance(claim_path, str) or not Path(claim_path).is_absolute()
        ):
            raise DomainError("capability_unavailable", 501)
        if version["source"]["required_runtime"]["application"].casefold() != "blender":
            raise DomainError("capability_unavailable", 501)
        if body["action"] != "import":
            key = source_key(version["item_id"])
            manifest = scoped(
                root, f"{body['project']}/Assets/Proscenium/Generated/{key}/source-manifest.json"
            )
            previous = json.loads(manifest.read_text()) if manifest.is_file() else {}
            scene = body.get("scene_path", f"Assets/Proscenium/Generated/{key}/Playable.unity")
            if (
                previous.get("source_version") != body["version"]
                or previous.get("source") != version["source"]
                or previous.get("scene_path") != scene
                or previous.get("camera") != body["camera"]
                or previous.get("pipeline") != body["pipeline"]
                or previous.get("spawn") != dict(zip(("x", "y", "z"), body["spawn"], strict=True))
            ):
                raise DomainError("source_conflict", 409)
            row["import_export_sha256"] = previous.get("export_sha256")
        materialised = self.services.extension("sources").materialise(
            row["project_id"], version["version_id"]
        )
        if not materialised.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
            raise DomainError("scope_denied", 403)
        entry = verify_materialisation(materialised, version["source"])
        # The intent already exists before the first project authoring/export effect.
        row["authoring_started"] = True
        self.save(row["project_id"], identity, row)
        if (project / "ProjectSettings").exists():
            prepare(root, body["project"])
        else:
            bootstrap_project(root, body["project"])
        export_relative = f"unity/exports/{identity}/environment.json"
        output = scoped(root, export_relative)
        output.parent.mkdir(parents=True, exist_ok=True)
        directory = managed(root, f".proscenium/jobs/native-workflows/{identity}")
        directory.mkdir(parents=True, exist_ok=True)
        request = dict(
            binding=fingerprint(row["binding"]),
            source_digest=version["source"]["digest"],
            blender=str(Path(blender).resolve()),
            entry=str(entry),
            materialised=str(materialised),
            output=str(output),
            claim_path=claim_path or str(directory.parent / "export.lock"),
        )
        write_json(directory / "export-request.json", request)
        row.update(
            phase="exporting",
            source=version["source"],
            editor=str(Path(configured_editor).resolve(strict=True)),
            export_path=export_relative,
            directory=str(directory),
            request=request,
            root=str(root),
        )
        self.save(row["project_id"], identity, row)
        return row

    def _finish_export(self, row: dict) -> dict:
        root = self.row_root(row)
        receipt_path = Path(row["directory"]) / "export-worker.json"
        if not receipt_path.is_file():
            raise DomainError("project_busy", 409)
        receipt = json.loads(receipt_path.read_text())
        if (
            receipt["binding"] != fingerprint(row["binding"])
            or receipt["source_digest"] != row["source"]["digest"]
        ):
            raise DomainError("source_conflict", 409)
        if receipt["state"] != "complete":
            raise DomainError("project_busy", 409)
        if receipt.get("native_exit_code") != 0 or receipt.get("error"):
            raise DomainError("capability_unavailable", 501)
        body = row["binding"]["body"]
        materialised = Path(row["request"]["materialised"])
        if not materialised.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
            raise DomainError("scope_denied", 403)
        verify_materialisation(materialised, row["source"])
        exported = json.loads((root / row["export_path"]).read_text())
        if (
            exported.get("format") != "proscenium-triangles-1"
            or exported.get("closure_digest") != row["source"]["digest"]
            or exported.get("units") != "metres"
            or exported.get("axes") != "XZY"
            or not exported.get("objects")
            or row["source"]["required_runtime"]["application"].casefold() != "blender"
            or str(exported.get("blender_version", "")).split(" ")[0]
            != row["source"]["required_runtime"]["version"].split(" ")[0]
        ):
            raise DomainError("source_conflict", 409)
        recipe = make_recipe(
            root,
            body["project"],
            source_id=body["version"]["item_id"],
            source_version=body["version"],
            closure=row["source"],
            export_path=row["export_path"],
            spawn=tuple(body["spawn"]),
            scene_path=body.get("scene_path"),
            camera=body["camera"],
            pipeline=body["pipeline"],
        )
        # F00 stores private records as canonical JSON. Normalise 0.0/-0.0/integer
        # spellings before W08's accepted recipe digest so a persisted retry hashes
        # exactly the same recipe bytes rather than its pre-storage Python spelling.
        recipe = json.loads(canonical(recipe))
        if (
            body["action"] != "import"
            and row.get("import_export_sha256") != recipe["export_sha256"]
        ):
            raise DomainError("source_conflict", 409)
        return self._unity_admission(row, recipe)

    @staticmethod
    def _unity_admission(row: dict, recipe: dict) -> dict:
        body = row["binding"]["body"]
        admission = dict(
            schema_version="1.0",
            operation_id=body["operation_id"],
            project_id=row["project_id"],
            job_id=body["job_id"],
            kind="unity",
            target=dict(
                target_id=body["version"]["item_id"], version=body["version"], shot_id=None
            ),
            input_digest=input_digest(
                dict(project=body["project"], recipe=recipe, action=body["action"])
            ),
            input_artifact_ids=[],
            execution_profile_id=body["execution_profile_id"],
        )
        validate("JobAdmission", admission)
        row.update(phase="prepared", recipe=recipe, admission=admission)
        return row

    async def _export(self, caller: Caller, identity: str, row: dict) -> None:
        await self.io(self.require_effect, caller)
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(export_worker.__file__).resolve()),
                str(Path(row["directory"]) / "export-request.json"),
                cwd=row["root"],
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        )
        cancelled = False
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            # Resolve spawn-before-ack before returning from shutdown. Never orphan an
            # unobserved async transport while its native worker is being created.
            process = await spawn
            cancelled = True
        self._exports[identity] = process
        try:
            if cancelled:
                raise asyncio.CancelledError
            await self.io(
                write_json,
                Path(row["directory"]) / "export-supervisor.json",
                await self.io(process_identity, process.pid),
            )
            await asyncio.wait_for(process.wait(), 210)
        finally:
            if process.returncode is None:
                process.terminate()  # Exact owned supervisor handles its native group's cleanup.
                try:
                    await asyncio.wait_for(process.wait(), 15)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            self._exports.pop(identity, None)

    async def unity(self, caller: Caller, body: dict) -> dict:
        identity = fingerprint(dict(kind="unity", operation_id=body["operation_id"]))
        async with self.unity_operation(identity):
            identity, row, fresh = await self.io(self.begin, caller, body, "unity")
            if row["phase"] == "launched":
                job = await self.io(self.existing_job, row)
                if job["state"] == "planned" and not await self.io(self._attempt_exists, row, job):
                    # Only F00 retry can allocate this new attempt. An explicit valid
                    # tool call may dispatch it from frozen inputs, without re-export.
                    await self.io(self.require_effect, caller)
                    return (await self.io(self._launch_unity, caller, row))["job"]
                return (await self.io(self._reconcile_one, row))["job"]
            if row["phase"] == "prepared" and await self.io(
                self._replayable, caller, body, "unity"
            ):
                result = await self.io(self._reconcile_one, row)
                row["phase"] = "launched"
                await self.io(self.save, row["project_id"], identity, row)
                return result["job"]
            if row["phase"] == "preparing":
                if not fresh and row.get("authoring_started"):
                    # Authoring or export launch may have occurred before acknowledgement.
                    raise DomainError("project_busy", 409)
                try:
                    row = await self.io(self._prepare_unity, caller, body, identity, row)
                except DomainError as error:
                    if not row.get("authoring_started"):
                        row.update(phase="failed", error_code=error.code)
                        await self.io(self.save, row["project_id"], identity, row)
                    raise
                if row["phase"] == "exporting":
                    await self._export(caller, identity, row)
            if row["phase"] == "exporting":
                row = await self.io(self._finish_export, row)
                await self.io(self.save, row["project_id"], identity, row)
            if row["phase"] != "prepared":
                if row["phase"] == "failed":
                    raise DomainError(row.get("error_code", "capability_unavailable"), 409)
                raise DomainError("project_busy", 409)
            await self.io(self.require_effect, caller)
            result = await self.io(self._launch_unity, caller, row)
            # A blocked prelaunch project remains prepared. Explicit matching retry may
            # proceed after the owner has saved/closed it; no second export is performed.
            if not result.get("blocked"):
                row["phase"] = "launched"
                await self.io(self.save, row["project_id"], identity, row)
            return result["job"]

    def _launch_unity(self, caller: Caller, row: dict) -> dict:
        body = row["binding"]["body"]
        return run_unity_job(
            self.services,
            caller,
            row["admission"],
            project=body["project"],
            recipe=row["recipe"],
            editor=Path(row["editor"]),
            action=body["action"],
            timeout=600,
            wait=False,
        )

    def _reconcile_one(self, row: dict) -> dict:
        job = self.existing_job(row)
        root = self.root(row["project_id"])
        if job["state"] == "planned" and not attempt_directory(root, job["attempt_id"]).exists():
            return {"job": job, "unity": None}
        return reconcile_unity_job(self.services, worker(job), job["job_id"])

    def _attempt_exists(self, row: dict, job: dict) -> bool:
        return attempt_directory(self.root(row["project_id"]), job["attempt_id"]).exists()

    async def unity_result(self, caller: Caller, body: dict) -> dict:
        async with self.operation():
            caller.permits("updateJobState", str(caller.project_id))
            if caller.channel != "tool":
                raise DomainError("scope_denied", 403)
            row = await self.io(self._known_unity, str(caller.project_id), body["job_id"])
            result = await self.io(self._reconcile_one, row)
            if result["job"]["state"] not in TERMINAL or result.get("unity") is None:
                raise DomainError("project_busy", 409)
            validate("UnityResult", result["unity"])
            return result["unity"]

    def _known_unity(self, project_id: str, job_id: str) -> dict:
        self.root(project_id)
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            index = tx.require("native_workflow_jobs", job_id)
            if index["kind"] != "unity":
                raise DomainError("scope_denied", 403)
            return tx.require(NAMESPACE, index["identity"])

    @staticmethod
    def reap_idle() -> None:
        """Reap owned children without reading project state or touching detached workers."""
        reap_unity_supervisors()

    async def reconcile(self, project_id: str) -> list[dict]:
        """Parent calls with gate closed too. Observation only, never dispatch or export."""
        async with self.operation():
            rows = await self.io(self._rows, project_id)
            results = []
            for row in rows:
                identity = fingerprint(
                    dict(kind=row["kind"], operation_id=row["binding"]["body"]["operation_id"])
                )
                if identity in self._inflight:
                    continue
                if (
                    row["kind"] == "assembly"
                    and row["phase"] == "prepared"
                    and await self.io(self._assembly_committed, row)
                ):
                    row["phase"] = "launched"
                    await self.io(self.save, project_id, identity, row)
                    results.append(await self.io(self.existing_job, row))
                if row["kind"] == "unity" and row["phase"] in {
                    "preparing",
                    "exporting",
                    "outcome_uncertain",
                }:
                    await self.io(self._observe_export, row)
                if row["kind"] == "unity" and "admission" in row:
                    try:
                        results.append((await self.io(self._reconcile_one, row))["job"])
                    except DomainError as error:
                        if error.code != "not_found":
                            raise
            await self.io(reap_unity_supervisors)
            return results

    def _observe_export(self, row: dict) -> None:
        self.row_root(row)
        identity = fingerprint(
            dict(kind="unity", operation_id=row["binding"]["body"]["operation_id"])
        )
        if identity in self._exports or identity in self._inflight:
            return
        if not row.get("authoring_started"):
            # The durable before-effect marker was never committed. Only source
            # reads/materialisation could have run, not authoring or native export.
            row.update(phase="failed", error_code="capability_unavailable")
            self.save(row["project_id"], identity, row)
            return
        path = Path(row["directory"]) / "export-worker.json" if "directory" in row else None
        if path is not None and path.is_file():
            receipt = json.loads(path.read_text())
            if (
                receipt["binding"] != fingerprint(row["binding"])
                or receipt["source_digest"] != row["source"]["digest"]
            ):
                raise DomainError("scope_denied", 403)
            if receipt["state"] == "complete":
                if receipt.get("native_exit_code") == 0 and not receipt.get("error"):
                    row.update(self._finish_export(row))
                else:
                    row["phase"] = "failed"
            elif (
                alive(receipt["supervisor"]) or receipt.get("process") and alive(receipt["process"])
            ):
                row["phase"] = "exporting"
            else:
                row["phase"] = "outcome_uncertain"
        else:
            acknowledged = (
                Path(row["directory"]) / "export-supervisor.json" if "directory" in row else None
            )
            if (
                acknowledged is not None
                and acknowledged.is_file()
                and alive(json.loads(acknowledged.read_text()))
            ):
                row["phase"] = "exporting"
            else:
                row["phase"] = "outcome_uncertain"
        self.save(row["project_id"], identity, row)

    def _rows(self, project_id: str) -> list[dict]:
        self.root(project_id)
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            return tx.all(NAMESPACE)

    async def pending(self, project_id: str) -> list[dict]:
        """Private lifecycle blockers before a Unity JobAdmission exists, not a wire DTO.

        Parent settlement must account for these alongside active work/jobs. A missing
        export acknowledgement is never equivalent to proving that nothing launched.
        """
        async with self.operation():
            rows = await self.io(self._rows, project_id)
            return [
                dict(
                    operation_id=row["binding"]["body"]["operation_id"],
                    job_id=row["job_id"],
                    kind=row["kind"],
                    phase=row["phase"],
                )
                for row in rows
                if row["phase"] not in {"launched", "failed"}
            ]

    async def cancel(self, project_id: str, job_id: str) -> dict:
        """Parent's job.cancel consumer, after F00 persists cancel_requested."""
        async with self.operation():
            row = await self.io(self._known_unity, project_id, job_id)
            job = await self.io(self.existing_job, row)
            return await self.io(self.cancel_job, job)

    def cancel_job(self, job: dict) -> dict:
        """Synchronous callback for the parent's existing owned outbox dispatcher."""
        with self.synchronous_operation():
            row = self._known_unity(job["project_id"], job["job_id"])
            current = self.existing_job(row)
            if current["attempt_id"] != job["attempt_id"]:
                raise DomainError("scope_denied", 403)
            return cancel_unity_job(self.services, worker(current), current["job_id"])["job"]

    def owns_cancellation(self, tx: Transaction, job: dict) -> bool:
        """Read-only host admission probe on the owner's existing transaction."""
        index = tx.get("native_workflow_jobs", job["job_id"], required=False)
        if not index or index.get("kind") != "unity":
            return False
        row = tx.get(NAMESPACE, index["identity"], required=False)
        if not row or row.get("kind") != "unity" or row.get("job_id") != job["job_id"]:
            return False
        admission = row.get("admission", {})
        return all(
            admission.get(field) == job[field]
            for field in ("project_id", "job_id", "kind", "input_digest", "target")
        )

    @contextmanager
    def synchronous_operation(self):
        with self._activity_lock:
            if self._closed:
                raise DomainError("capability_unavailable", 503)
            self._sync_calls += 1
        try:
            yield
        finally:
            with self._activity_lock:
                self._sync_calls -= 1

    def close_idle(self) -> None:
        """Synchronous passive-container cleanup, never detach an active writer."""
        with self._activity_lock:
            if self._close_complete:
                return
            if (
                self._closed
                or self._calls
                or self._io_jobs
                or self._exports
                or self._inflight
                or self._sync_calls
            ):
                raise RuntimeError(
                    "Await workflows.close() before releasing active workflow ownership"
                )
            self._closed = True
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._close_complete = True

    async def close(self) -> None:
        """Await before services.close(). Admitted Unity supervisors intentionally survive."""
        with self._activity_lock:
            if self._close_complete:
                return
            self._closed = True
        calls = [task for task in self._calls if task is not asyncio.current_task()]
        for task in calls:
            task.cancel()
        if calls:
            await asyncio.gather(*calls, return_exceptions=True)
        while self._io_jobs:
            await asyncio.gather(*list(self._io_jobs), return_exceptions=True)
        while self._sync_calls:
            await asyncio.sleep(0.01)
        self._pool.shutdown(wait=True, cancel_futures=True)
        reap_unity_supervisors()
        with self._activity_lock:
            self._close_complete = True
