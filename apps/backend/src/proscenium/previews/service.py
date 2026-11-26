"""Preview admission and verified, incremental immutable media publication."""

import io
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from PIL import Image

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue.validation import sequence_map as validate_sequence_map
from proscenium.contracts import validate
from proscenium.db import Transaction
from proscenium.jobs import TERMINAL, retry_context
from proscenium.jobs.resources import check_storage
from proscenium.services import Services
from proscenium.sources.common import active, admitted, binding, fingerprint, managed, replay
from proscenium.sources.read_only import read_authored_json

from .config import Rendering
from .journal import (
    UNSUPPORTED_BLENDER_FEATURE_REASON,
    output,
    read,
    sha,
    unsupported_blender_features,
)
from .media import verify_video


def sequence_contributors(
    document: dict[str, Any], maps: dict[str, dict[str, Any]]
) -> Iterator[dict[str, Any]]:
    for entry in document["entries"]:
        if "clip_artifact_id" in entry:
            continue
        yield entry
        if entry["transform"] == "transition":
            yield from entry["contributors"]
        artifact_id = entry["mapping_artifact_id"]
        bounds = entry["sequence_frames"]
        if artifact_id is not None:
            for row in maps[artifact_id]["frames"]:
                if bounds["start"] <= row["sequence_frame"] <= bounds["end"]:
                    yield from row["contributors"]


def ranges(frames: set[int]) -> list[dict[str, int]]:
    result: list[dict[str, int]] = []
    for frame in sorted(frames):
        if result and result[-1]["end"] + 1 == frame:
            result[-1]["end"] = frame
        else:
            result.append({"start": frame, "end": frame})
    return result


def worker_caller(job: dict) -> Caller:
    return Caller(
        "worker",
        job["attempt_id"],
        job["project_id"],
        job["work_id"],
        job["owner_epoch"],
        job["job_id"],
        job["attempt_id"],
        job["input_digest"],
        ("registerFrameReceipt", "registerPreviewSegment", "updateJobState"),
    )


class PreviewService:
    def __init__(self, services: Services):
        self.services = services
        self.config = Rendering(**services.settings.rendering)
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def artifacts(self) -> Any:
        return self.services.extension("artifacts")

    def can_retry_cross_work(
        self, tx: Transaction, caller: Caller, job: dict[str, Any]
    ) -> str | None:
        work = tx.get("work", str(caller.work_id), required=False)
        if (
            not work
            or work["project_id"] != tx.project_id
            or work["state"] not in {"running", "awaiting_user"}
        ):
            return None
        try:
            request_id, context = retry_context(
                tx,
                caller,
                work,
                owner_epoch=self.services.jobs.owner_epoch,
                require_invocation=True,
            )
        except DomainError:
            return None
        if (
            not context
            or context["project_id"] != tx.project_id
            or context.get("execution_scope") == "planning_only"
        ):
            return None
        job_target = job.get("target") or {}
        job_version = job_target.get("version")
        if not isinstance(job_version, dict) or not {"item_id", "version_id"} <= job_version.keys():
            return None
        target = context.get("request_target")
        if target and target["kind"] == "item":
            ref = target["version"]
            version = tx.get("versions", ref["version_id"], required=False)
            if (
                version
                and version["project_id"] == tx.project_id
                and version["item_id"] == ref["item_id"]
                and version["kind"] in {"scene", "asset"}
                and job_version == ref
            ):
                return request_id
        if target is not None or not context.get("item_id") or not context.get("version_id"):
            return None
        version = tx.get("versions", context["version_id"], required=False)
        if (
            not version
            or version["project_id"] != tx.project_id
            or version["item_id"] != context["item_id"]
            or version["kind"] != "sequence"
        ):
            return None
        sequence = self.validated_sequence(tx, version)
        if sequence and any(
            contributor.get("job_id") == job["job_id"]
            and contributor.get("version_id") == job_version["version_id"]
            and contributor.get("shot_id") == job_target.get("shot_id")
            for contributor in sequence_contributors(*sequence)
        ):
            return request_id
        return None

    def validated_sequence(
        self, tx: Transaction, version: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]] | None:
        try:
            doc = read_authored_json(
                self.services,
                str(tx.project_id),
                version,
                batch={v["version_id"]: v for v in tx.all("versions")},
            )
            validate("SequenceMap", doc)
        except DomainError:
            return None
        if (
            doc["project_id"] != tx.project_id
            or doc["sequence_version_id"] != version["version_id"]
        ):
            return None

        maps: dict[str, dict[str, Any]] = {}
        for artifact_id in dict.fromkeys(entry["mapping_artifact_id"] for entry in doc["entries"]):
            if artifact_id is None:
                continue
            artifact = tx.get("artifacts", artifact_id, required=False)
            if (
                not artifact
                or artifact["project_id"] != tx.project_id
                or artifact["kind"] != "frame_map"
                or artifact["state"] != "ready"
            ):
                return None
            try:
                raw = self.artifacts.ready_path(str(tx.project_id), artifact_id).read_bytes()
                if sha(raw) != artifact["sha256"]:
                    return None
                mapping = json.loads(raw)
                validate("FrameMap", mapping)
            except (DomainError, OSError, ValueError):
                return None
            maps[artifact_id] = mapping
        try:
            validate_sequence_map(tx, doc, version["version_id"], maps)
        except DomainError:
            return None
        return doc, maps

    @staticmethod
    def update_version_links(tx: Transaction, version_id: str, **updates: Any) -> None:
        previous = tx.get("version_links", version_id, required=False)
        tx.put(
            "version_links",
            version_id,
            {"artifact_ids": [], **(previous or {}), **updates},
            expected_revision=tx.revision("version_links", version_id)
            if previous is not None
            else None,
        )

    def request(self, caller: Caller, request: Any) -> tuple[dict, bool]:
        body = request.model_dump(mode="json") if hasattr(request, "model_dump") else request
        validate("PreviewRequest", body)
        project_id = body["project_id"]
        active(self.services, caller, "requestPreview", project_id)
        store = self.services.registry.project_store(project_id)
        immutable = binding(caller, body)
        with self.services.activation_lock, store.transaction() as tx:
            previous = replay(tx, "previews.request", body["operation_id"], immutable)
            if previous:
                return previous, True
            admitted(self.services, tx, caller)
            version = tx.require("versions", body["version"]["version_id"])
            if version["item_id"] != body["version"]["item_id"]:
                raise DomainError("scope_denied", 403)
            self.config.validate(body["spec"])
            if body["spec"]["source_closure_digest"] != version["source"]["digest"]:
                raise DomainError("hash_mismatch", 422)
            runtime = version["source"]["required_runtime"]
            if runtime["application"].strip().lower() == "blender" and unsupported_blender_features(
                runtime["features"]
            ):
                raise DomainError("capability_unavailable", 501, UNSUPPORTED_BLENDER_FEATURE_REASON)
            if self.config.profile_id not in self.services.settings.execution_profiles:
                raise DomainError("capability_unavailable", 501, "Rendering profile is not enabled")
            check_storage(store.path.parent, self.services.settings.free_space_reserve_bytes)

            reused_intent = False

            def effect() -> dict:
                nonlocal reused_intent
                intent = tx.get("preview_intents", body["spec"]["intent_id"], required=False)
                if intent:
                    if (
                        intent["version"] != body["version"]
                        or intent["render_spec"] != body["spec"]
                    ):
                        raise DomainError("idempotency_conflict", 409)
                    reused_intent = True
                    return tx.require("jobs", intent["job_id"])
                job_id = "preview-" + fingerprint(
                    {"project_id": project_id, "intent_id": body["spec"]["intent_id"]}
                )
                spec = body["spec"]
                job = self.services.jobs.create(
                    tx,
                    job_id=job_id,
                    kind="render",
                    target={
                        "target_id": version["item_id"],
                        "version": body["version"],
                        "shot_id": spec["shot_id"],
                    },
                    input_digest=fingerprint({"version": body["version"], "render_spec": spec}),
                    operation_id=body["operation_id"],
                    work_id=caller.work_id,
                    render_spec=spec,
                    expected_frames=spec["frames"]["end"] - spec["frames"]["start"] + 1,
                    enqueue_kind="preview.render",
                )
                tx.put(
                    "preview_intents",
                    spec["intent_id"],
                    {"version": body["version"], "render_spec": spec, "job_id": job_id},
                )
                version_id = version["version_id"]
                links = tx.get("version_links", version_id, required=False) or {}
                order_updates = {
                    key: list(dict.fromkeys(links.get(key, []) + [job_id]))
                    for key in ("added_preview_job_ids", "ordered_preview_job_ids")
                    if key == "added_preview_job_ids" or key in links
                }
                self.update_version_links(tx, version_id, **order_updates)
                self.services.jobs.freeze_inputs(
                    tx,
                    job_id,
                    {
                        "source": version["source"],
                        "configuration": self.services.settings.view(),
                        "work_id": caller.work_id,
                        "invocation_id": caller.invocation_id,
                        "owner_epoch": caller.owner_epoch,
                    },
                )
                self.services.jobs.emit(tx, job, caller)
                return job

            result, replayed = tx.operation(
                "previews.request", body["operation_id"], immutable, effect
            )
            return result, replayed or reused_intent

    def check_order_scope(
        self,
        tx: Transaction,
        work: dict[str, Any],
        context: dict[str, Any],
        version: dict[str, Any],
    ) -> None:
        publication = tx.get("publications", version["publication_id"], required=False)
        same_work = publication is not None and publication.get("work_id") == work["work_id"]
        reference = {key: version[key] for key in ("item_id", "version_id")}
        target = context.get("request_target")
        if target is not None:
            match target["kind"]:
                case "shot":
                    raise DomainError(
                        "scope_denied",
                        403,
                        "Storyboard shot targets cannot reorder scene preview passes",
                    )
                case "new_item" if not same_work:
                    raise DomainError(
                        "scope_denied",
                        403,
                        "New-item requests cannot reorder an existing item's preview passes",
                    )
                case "item" if target["version"] != reference:
                    raise DomainError(
                        "scope_denied",
                        403,
                        "Target version is outside the active pinned item scope",
                    )
            return

        selected_id = context.get("version_id")
        if same_work or selected_id == version["version_id"]:
            return
        selected = tx.get("versions", selected_id, required=False) if selected_id else None
        if selected and selected["kind"] == "sequence" and selected["project_id"] == tx.project_id:
            sequence = self.validated_sequence(tx, selected)
            if sequence and any(
                contributor["version_id"] == version["version_id"]
                for contributor in sequence_contributors(*sequence)
            ):
                return
        raise DomainError(
            "scope_denied", 403, "Pin the target version before reordering its preview passes"
        )

    def set_order(self, caller: Caller, request: Any) -> tuple[dict[str, Any], bool]:
        body = request.model_dump(mode="json") if hasattr(request, "model_dump") else request
        validate("PreviewOrderUpdate", body)
        project_id = body["project_id"]
        active(self.services, caller, "setPreviewOrder", project_id)
        store = self.services.registry.project_store(project_id)
        immutable = binding(caller, body)
        with self.services.activation_lock, store.transaction() as tx:
            previous = replay(tx, "previews.order", body["operation_id"], immutable)
            if previous:
                return previous, True

            def effect() -> dict[str, Any]:
                try:
                    work = admitted(self.services, tx, caller)
                except DomainError as exc:
                    if exc.code == "not_found":
                        raise DomainError("attribution_unavailable", 409) from exc
                    raise
                _, context = retry_context(
                    tx,
                    caller,
                    work,
                    owner_epoch=self.services.jobs.owner_epoch,
                    require_invocation=True,
                )
                if not context or context["project_id"] != project_id:
                    raise DomainError("scope_denied", 403)
                if context.get("execution_scope") == "planning_only":
                    raise DomainError(
                        "scope_denied",
                        403,
                        "Preview passes cannot be reordered in planning-only work",
                    )
                version_id = body["version"]["version_id"]
                version = tx.require("versions", version_id)
                if (
                    version["project_id"] != project_id
                    or version["item_id"] != body["version"]["item_id"]
                ):
                    raise DomainError("scope_denied", 403)
                self.check_order_scope(tx, work, context, version)
                for job_id in body["job_ids"]:
                    job = tx.get("jobs", job_id, required=False)
                    if (
                        not job
                        or job["project_id"] != project_id
                        or job["kind"] != "render"
                        or job["target"].get("version") != body["version"]
                    ):
                        raise DomainError(
                            "source_conflict",
                            409,
                            "All ordered preview jobs must be render passes on the target version",
                        )
                catalogue = self.services.extension("catalogue")
                current_ids = catalogue.version_view(tx, version)["preview_job_ids"]
                merged_ids = list(dict.fromkeys(body["job_ids"] + current_ids))
                self.update_version_links(tx, version_id, ordered_preview_job_ids=merged_ids)
                view = catalogue.version_view(tx, version)
                validate("VersionRecord", view)
                tx.emit(
                    "preview.order_changed",
                    view,
                    actor={"kind": "tool", "id": caller.subject_id},
                    work_id=caller.work_id,
                    request_id=work["originating_request_id"],
                    native_session_id=work["native_session_id"],
                )
                return view

            return tx.operation("previews.order", body["operation_id"], immutable, effect)

    def root(self, job: dict) -> Path:
        project = Path(self.services.registry.get(job["project_id"])["path"])
        name = f".proscenium/jobs/previews/{job['job_id']}/{job['attempt_id']}"
        # IDs are path components as well as schema identities. Never permit slash/traversal IDs.
        if any("/" in job[key] or job[key] in {".", ".."} for key in ("job_id", "attempt_id")):
            raise DomainError("scope_denied", 403)
        return managed(project, name + "/task.json").parent

    def runtime(self, project_id: str, job: dict) -> dict:
        inputs = self.services.jobs.inputs(project_id, job["job_id"])
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            existing = tx.get("preview_runtime", job["job_id"], required=False)
        if existing:
            return existing
        # Initial W02 snapshots contain ConfigurationView, not the private rendering object.
        # Never silently bind a changed revision to an older publication intent.
        if inputs["configuration"] != self.services.settings.view():
            raise DomainError(
                "source_conflict", 409, "Original rendering configuration is unavailable"
            )
        config = self.config.snapshot()
        with self.services.registry.project_store(project_id).transaction() as tx:
            previous = tx.get("preview_runtime", job["job_id"], required=False)
            if previous:
                return previous
            tx.put("preview_runtime", job["job_id"], config)
        return config

    @staticmethod
    def records(tx: Transaction, job: dict, family: str) -> list[dict]:
        # Old verified frames remain reusable across same-input attempts. Segments carry their
        # own commissioning attempt and are not rewritten to pretend they belong to the retry.
        rows = [
            r
            for r in tx.all(family)
            if r["job_id"] == job["job_id"] and r["input_digest"] == job["input_digest"]
        ]
        if family == "frames":
            unique: dict[int, dict] = {}
            for row in rows:
                old = unique.get(row["source_frame"])
                if old and old["sha256"] != row["sha256"]:
                    raise DomainError("frame_conflict", 409)
                unique[row["source_frame"]] = row
            return sorted(unique.values(), key=lambda row: row["source_frame"])
        intervals: dict[tuple[int, int], dict] = {}
        for row in rows:
            key = (row["source_frames"]["start"], row["source_frames"]["end"])
            old = intervals.get(key)
            if old and old["sha256"] != row["sha256"]:
                raise DomainError("frame_conflict", 409)
            intervals[key] = row
        return sorted(intervals.values(), key=lambda row: row["source_frames"]["start"])

    def availability(self, project_id: str, job_id: str) -> dict:
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            job = tx.require("jobs", job_id)
            spec = job["render_spec"]
            if spec is None:
                raise DomainError("invalid_request")
            ready = {row["source_frame"] for row in self.records(tx, job, "frames")}
            playable = set()
            for row in self.records(tx, job, "segments"):
                interval = set(
                    range(row["source_frames"]["start"], row["source_frames"]["end"] + 1)
                )
                if interval <= ready:
                    playable |= interval
            missing = set(range(spec["frames"]["start"], spec["frames"]["end"] + 1)) - ready
            failed = missing if job["state"] in {"failed", "cancelled"} else set()
            progress = tx.get("preview_progress", job_id, required=False) or {"revision": 0}
            result = dict(
                schema_version="1.0",
                project_id=project_id,
                job_id=job_id,
                version_id=job["target"]["version"]["version_id"],
                source_range=spec["frames"],
                fps=spec["fps"],
                available_ranges=ranges(ready),
                playable_ranges=ranges(playable),
                pending_ranges=ranges(missing - failed),
                failed_ranges=ranges(failed),
                state_revision=job["state_revision"] + progress["revision"],
            )
            validate("FrameAvailability", result)
            return result

    def bound(self, caller: Caller, body: dict, operation: str, tx: Transaction) -> dict:
        caller.permits(operation, body["project_id"])
        job = tx.require("jobs", body["job_id"])
        if (
            caller.channel != "worker"
            or caller.job_id != job["job_id"]
            or caller.attempt_id != body["attempt_id"]
            or caller.input_digest != body["input_digest"]
            or job["attempt_id"] != body["attempt_id"]
            or job["input_digest"] != body["input_digest"]
            or job["target"]["version"]["version_id"] != body["version_id"]
            or job["target"]["shot_id"] != body["shot_id"]
        ):
            raise DomainError("scope_denied", 403)
        return job

    def register(self, caller: Caller, body: dict, *, segment: bool = False) -> tuple[dict, bool]:
        schema, family, identity, operation = (
            ("PreviewSegment", "segments", "segment_id", "registerPreviewSegment")
            if segment
            else ("FrameReceipt", "frames", "receipt_id", "registerFrameReceipt")
        )
        validate(schema, body)
        store = self.services.registry.project_store(body["project_id"])
        with store.transaction(write=False) as tx:
            # An old identical receipt can be acknowledged, but never mutate a new attempt.
            caller.permits(operation, body["project_id"])
            existing = tx.get(family, body[identity], required=False)
            if (
                caller.channel != "worker"
                or caller.job_id != body["job_id"]
                or caller.attempt_id != body["attempt_id"]
                or caller.input_digest != body["input_digest"]
            ):
                raise DomainError("scope_denied", 403)
            if existing:
                if existing != body:
                    raise DomainError("frame_conflict", 409)
                return self.receipt(body[identity], True), True
            job = self.bound(caller, body, operation, tx)
        if job["state"] in TERMINAL:
            raise DomainError("invalid_state_transition")
        root = self.root(job)
        task = read(root / "task.json")
        if any(task[key] != job[key] for key in ("job_id", "attempt_id", "input_digest")):
            raise DomainError("scope_denied", 403)
        path = output(root, body["output_path"])
        data = path.read_bytes()
        if sha(data) != body["sha256"]:
            raise DomainError("hash_mismatch", 422)
        spec = job["render_spec"]
        first = body["source_frames"]["start"] if segment else body["source_frame"]
        last = body["source_frames"]["end"] if segment else first
        if first < spec["frames"]["start"] or last > spec["frames"]["end"] or first > last:
            raise DomainError("invalid_frame_mapping", 422)
        if segment:
            if body["fps"] != spec["fps"] or body["frame_count"] != last - first + 1:
                raise DomainError("invalid_frame_mapping", 422)
            with store.transaction(write=False) as tx:
                ready = {r["source_frame"] for r in self.records(tx, job, "frames")}
            if not set(range(first, last + 1)) <= ready:
                raise DomainError("dependency_missing", 422)
            verify_video(path, body["fps"], body["frame_count"], spec["dimensions"], task["config"])
        else:
            if body["dimensions"] != spec["dimensions"]:
                raise DomainError("invalid_frame_mapping", 422)
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                if (
                    image.format != "PNG"
                    or image.mode != "RGB"
                    or image.size != (body["dimensions"]["width"], body["dimensions"]["height"])
                ):
                    raise DomainError("media_unavailable", 422)
        metadata = {"dimensions": spec["dimensions"], "source_version_id": body["version_id"]}
        if segment:
            metadata.update(fps=body["fps"], frame_count=body["frame_count"])
        artifact = self.artifacts.promote(
            body["project_id"],
            body["artifact_id"],
            data,
            kind="video" if segment else "image",
            purpose=spec["purpose"],
            media_type="video/mp4" if segment else "image/png",
            metadata=metadata,
        )
        with self.lock, store.transaction() as tx:
            self.bound(caller, body, operation, tx)

            def effect() -> dict:
                current = tx.require("jobs", job["job_id"])
                if current["state"] in TERMINAL:
                    raise DomainError("invalid_state_transition")
                for row in self.records(tx, current, family):
                    a, b = (
                        (row["source_frames"]["start"], row["source_frames"]["end"])
                        if segment
                        else (row["source_frame"], row["source_frame"])
                    )
                    if not (last < a or first > b):
                        if a == first and b == last and row["sha256"] == body["sha256"]:
                            break
                        raise DomainError("frame_conflict", 409)
                self.artifacts.put(tx, artifact)
                tx.put(family, body[identity], body, schema=schema)
                progress = tx.get("preview_progress", job["job_id"], required=False)
                tx.put(
                    "preview_progress",
                    job["job_id"],
                    {"revision": progress["revision"] + 1 if progress else 1},
                    expected_revision=tx.revision("preview_progress", job["job_id"])
                    if progress
                    else None,
                )
                tx.emit(
                    "segment.available" if segment else "frame.available",
                    body,
                    actor={"kind": "worker", "id": caller.subject_id},
                    work_id=job["work_id"],
                )
                # F00 owns job records. A parent-supplied transaction-taking primitive
                # makes the job projection part of this exact commit without opening a
                # nested SQLite writer or implementing another job state machine here.
                update_tx = getattr(self.services.jobs, "update_in_transaction", None)
                if update_tx is not None:
                    ready_frames = self.records(tx, current, "frames")
                    ready_segments = self.records(tx, current, "segments")
                    count = len(ready_frames)
                    complete = count == current["expected_frames"] and count > 0
                    update = dict(
                        schema_version="1.0",
                        operation_id=body[identity] + "-job",
                        project_id=body["project_id"],
                        job_id=current["job_id"],
                        attempt_id=current["attempt_id"],
                        expected_revision=current["state_revision"],
                        state="starting" if current["state"] == "planned" else current["state"],
                        availability="complete" if complete else "partial",
                        completed_frames=count,
                        artifact_ids=list(
                            dict.fromkeys(
                                current["artifact_ids"]
                                + [r["artifact_id"] for r in ready_frames + ready_segments]
                            )
                        ),
                    )
                    update_tx(tx, caller, update, evidence="Verified immutable worker receipt")
                return self.receipt(body[identity], False)

            return tx.operation(f"previews.{family}", body[identity], body, effect)

    @staticmethod
    def receipt(identity: str, replayed: bool) -> dict:
        return dict(
            schema_version="1.0",
            operation_id=identity,
            resource_id=identity,
            state="ready",
            state_revision=0,
            replayed=replayed,
        )

    def state(
        self,
        job: dict,
        state: str,
        *,
        exit_code: int | None = None,
        evidence: str | None = None,
        extra_artifacts: list[str] | None = None,
        error_code: str | None = None,
    ) -> dict:
        with self.services.registry.project_store(job["project_id"]).transaction(write=False) as tx:
            current = tx.require("jobs", job["job_id"])
            if current["attempt_id"] != job["attempt_id"]:
                raise DomainError("scope_denied", 403)
            if current["state"] in TERMINAL:
                return current
            frames = self.records(tx, current, "frames")
            segments = self.records(tx, current, "segments")
        artifact_ids = list(
            dict.fromkeys(
                current["artifact_ids"]
                + [r["artifact_id"] for r in frames + segments]
                + (extra_artifacts or [])
            )
        )
        complete = len(frames) == current["expected_frames"] and bool(frames)
        if current["render_spec"] and current["render_spec"]["purpose"] == "proxy":
            complete = bool(extra_artifacts)
        if current["kind"] == "encode":
            complete = bool(extra_artifacts)
        body = dict(
            schema_version="1.0",
            operation_id=f"{job['attempt_id']}-state-{current['state_revision']}",
            project_id=job["project_id"],
            job_id=job["job_id"],
            attempt_id=job["attempt_id"],
            expected_revision=current["state_revision"],
            state=state,
            availability="complete" if complete else "partial" if frames else "pending",
            completed_frames=len(frames),
            artifact_ids=artifact_ids,
        )
        if exit_code is not None:
            body["native_exit_code"] = exit_code
        if error_code is not None:
            body["error_code"] = error_code
        return self.services.jobs.update(worker_caller(job), body, evidence=evidence)[0]

    def pump(self, project_id: str) -> list[dict]:
        from .runner import pump

        with self.lock:
            return pump(self, project_id)

    def assembly(
        self, caller: Caller, admission: dict, sequence: dict, frame_map: dict | None = None
    ) -> tuple[dict, bool]:
        from .assembly import admit_assembly

        return admit_assembly(self, caller, admission, sequence, frame_map)

    def reconcile(self, project_id: str) -> list[dict]:
        from .runner import reconcile

        with self.lock:
            return reconcile(self, project_id)

    def start(self, project_id: str, *, interval: float = 0.05) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def watch() -> None:
            while not self._stop.wait(interval):
                try:
                    self.reconcile(project_id)
                    # Admission gate is checked by pump. Closed boot means reconciliation only.
                    self.pump(project_id)
                except DomainError:
                    continue

        self._thread = threading.Thread(target=watch, name="proscenium-previews", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        # Detached workers intentionally survive owner/browser shutdown.
