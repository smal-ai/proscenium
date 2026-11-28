"""Atomic publication, immutable version reads and typed authored projections."""

import json
import re
from fractions import Fraction
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.db import Transaction
from proscenium.db.store import now
from proscenium.previews.journal import (
    UNSUPPORTED_BLENDER_FEATURE_REASON,
    unsupported_blender_features,
)
from proscenium.services import Services
from proscenium.sources import Sources
from proscenium.sources.common import active, admitted, binding, fingerprint, git, replay

from . import validation
from .artifacts import Artifacts
from .relationship_projector import apply_version_projection, project_version_relationships

METADATA = ("description", "tags", "provenance")
CHECKPOINT_SOURCE_CONFLICT = "Checkpoint receipt is unavailable or does not match source_commit"


def natural_render_job_key(job: dict[str, Any]) -> tuple[int, int, int, tuple, str]:
    spec = job.get("render_spec") or {}
    frames = spec.get("frames") or {}
    label = str(job.get("target", {}).get("shot_id") or spec.get("scene_name") or "")
    tokens = tuple(
        (0, int(token)) if token.isdecimal() else (1, token)
        for token in re.split(r"(\d+)", label.casefold())
    )
    return (
        int(spec.get("purpose") == "proxy"),
        int(frames.get("start", 0)),
        int(frames.get("end", 0)),
        tokens,
        job["job_id"],
    )


class Catalogue:
    def __init__(self, services: Services, sources: Sources, artifacts: Artifacts):
        self.services, self.sources, self.artifacts = services, sources, artifacts

    @staticmethod
    def choice_from_records(records: list[dict[str, Any]]) -> dict[str, Any] | None:
        if len(records) > 1:
            raise DomainError(
                "source_conflict",
                409,
                "More than one current selection record for a slot",
                safe_for_model=True,
            )
        if not records or records[0]["state"] != "selected":
            return None
        return dict(records[0]["choice"])

    @classmethod
    def selected_choice(cls, tx: Transaction, slot: dict[str, Any]) -> dict[str, Any] | None:
        return cls.choice_from_records([row for row in tx.all("selections") if row["slot"] == slot])

    @staticmethod
    def selections_by_slot(tx: Transaction) -> dict[bytes, list[dict[str, Any]]]:
        result: dict[bytes, list[dict[str, Any]]] = {}
        for row in tx.all("selections"):
            result.setdefault(canonical(row["slot"]), []).append(row)
        return result

    @classmethod
    def item_view(
        cls,
        tx: Transaction,
        row: dict[str, Any],
        *,
        selections_by_slot: dict[bytes, list[dict[str, Any]]] | None = None,
        selected_versions: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result = {**row, "selected_version_id": None}
        slot = {"kind": "item", "item_id": row["item_id"]}
        choice = (
            cls.selected_choice(tx, slot)
            if selections_by_slot is None
            else cls.choice_from_records(selections_by_slot.get(canonical(slot), []))
        )
        if choice:
            if choice["kind"] != "version" or choice["version"]["item_id"] != row["item_id"]:
                raise DomainError("source_conflict", 409)
            identity = choice["version"]["version_id"]
            version = (
                tx.require("versions", identity)
                if selected_versions is None
                else selected_versions.get(identity)
            )
            if version is None:
                raise DomainError("not_found", 404)
            if version["item_id"] != row["item_id"]:
                raise DomainError("scope_denied", 403)
            result["selected_version_id"] = version["version_id"]
        return result

    @staticmethod
    def selected_versions(
        tx: Transaction, selections: dict[bytes, list[dict[str, Any]]]
    ) -> dict[str, dict[str, Any]]:
        identities = {
            row["choice"]["version"]["version_id"]
            for bucket in selections.values()
            for row in bucket
            if row["slot"]["kind"] == "item"
            and row["state"] == "selected"
            and row["choice"]["kind"] == "version"
        }
        if not identities:
            return {}
        return {
            identity: json.loads(body)
            for identity, body in tx.connection.execute(
                "SELECT id, body FROM records WHERE namespace='versions' "
                "AND id IN (SELECT value FROM json_each(?))",
                (json.dumps(sorted(identities)),),
            )
        }

    @staticmethod
    def render_jobs_by_version(tx: Transaction) -> dict[tuple[str, str], list[dict[str, Any]]]:
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for job in tx.all("jobs"):
            if job["kind"] == "render" and (ver := job["target"].get("version")) is not None:
                buckets.setdefault((ver["item_id"], ver["version_id"]), []).append(job)
        for jobs in buckets.values():
            jobs.sort(key=natural_render_job_key)
        return buckets

    @staticmethod
    def version_view(
        tx: Transaction,
        row: dict[str, Any],
        *,
        render_jobs_by_version: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
        version_links: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        identity = row["version_id"]
        links = (
            (tx.get("version_links", identity, required=False) or {})
            if version_links is None
            else version_links.get(identity, {})
        )
        reference = (row["item_id"], identity)
        if render_jobs_by_version is None:
            jobs = sorted(
                (
                    job
                    for job in tx.all("jobs")
                    if job["kind"] == "render"
                    and job["target"].get("version")
                    == {"item_id": row["item_id"], "version_id": identity}
                ),
                key=natural_render_job_key,
            )
        else:
            jobs = render_jobs_by_version.get(reference, [])
        valid_ids = {job["job_id"] for job in jobs}
        inline_ids = row["preview_job_ids"]
        ordered_override = links.get("ordered_preview_job_ids")
        base_ids = (
            ordered_override
            if isinstance(ordered_override, list)
            else inline_ids + links.get("added_preview_job_ids", [])
        )
        base_ids = [
            identity for identity in base_ids if identity in valid_ids or identity in inline_ids
        ]
        return {
            **row,
            "preview_job_ids": list(dict.fromkeys(base_ids + [job["job_id"] for job in jobs])),
            "artifact_ids": list(
                dict.fromkeys(
                    row["artifact_ids"]
                    + links.get("artifact_ids", [])
                    + [identity for job in jobs for identity in job["artifact_ids"]]
                )
            ),
        }

    @staticmethod
    def resolve_checkpoint_sources(tx: Transaction, body: dict[str, Any]) -> dict[str, Any]:
        items = []
        for row in body["items"]:
            operation_id = row.get("checkpoint_operation_id")
            if operation_id is None:
                items.append(row)
                continue
            intent = tx.get("checkpoint_intents", operation_id, required=False)
            receipt = (
                intent.get("receipt") if intent and intent.get("state") == "completed" else None
            )
            if (
                not isinstance(receipt, dict)
                or not isinstance(receipt.get("source_closure"), dict)
                or row["source_commit"] != receipt.get("source_commit")
            ):
                raise DomainError(
                    "source_conflict", 409, CHECKPOINT_SOURCE_CONFLICT, safe_for_model=True
                )
            items.append(
                {
                    **{k: v for k, v in row.items() if k != "checkpoint_operation_id"},
                    "source": receipt["source_closure"],
                }
            )
        return {**body, "items": items}

    def validate_batch(self, tx: Transaction, body: dict[str, Any]) -> None:
        items = body["items"]
        batch = {row["version_id"]: row for row in items}
        if len(batch) != len(items) or len({row["item_id"] for row in items}) != len(items):
            raise DomainError(
                "invalid_request",
                message="Publication must use unique item_id and version_id values",
                safe_for_model=True,
            )
        visiting: set[str] = set()
        visited: set[str] = set()

        def dependency(reference: dict[str, Any]) -> dict[str, Any]:
            result = batch.get(reference["version_id"]) or tx.get(
                "versions", reference["version_id"], required=False
            )
            if not result:
                raise DomainError("dependency_missing", 422, version_id=reference["version_id"])
            if result["item_id"] != reference["item_id"]:
                raise DomainError("scope_denied", 403)
            return result

        def visit(row: dict[str, Any]) -> None:
            identity = row["version_id"]
            if identity in visiting:
                raise DomainError("source_conflict", 409, "Dependency cycle", safe_for_model=True)
            if identity in visited:
                return
            visiting.add(identity)
            for ref in row["source"]["dependencies"]:
                visit(dependency(ref))
            for ref in row.get("derived_from", []):
                visit(dependency(ref))
            visiting.remove(identity)
            visited.add(identity)

        intents: set[str] = set()
        for row in items:
            if tx.get("versions", row["version_id"], required=False):
                raise DomainError(
                    "source_conflict",
                    409,
                    "version_id already identifies a published version",
                    safe_for_model=True,
                )
            item = tx.get("items", row["item_id"], required=False)
            if (item["current_version_id"] if item else None) != row["expected_current_version_id"]:
                raise DomainError("publication_head_conflict", 409, item_id=row["item_id"])
            if item and item["kind"] != row["kind"]:
                raise DomainError(
                    "source_conflict",
                    409,
                    "Publication kind must match the existing item kind",
                    safe_for_model=True,
                )
            visit(row)
            for identity in row["artifact_ids"]:
                if tx.require("artifacts", identity)["state"] != "ready":
                    raise DomainError("media_unavailable", 422)
            runtime = row["source"]["required_runtime"]
            if (
                row["preview_requests"]
                and runtime["application"].strip().lower() == "blender"
                and unsupported_blender_features(runtime["features"])
            ):
                raise DomainError(
                    "capability_unavailable",
                    501,
                    UNSUPPORTED_BLENDER_FEATURE_REASON,
                    safe_for_model=True,
                )
            for spec in row["preview_requests"]:
                if spec["intent_id"] in intents or tx.get(
                    "preview_intents", spec["intent_id"], required=False
                ):
                    raise DomainError("idempotency_conflict", 409)
                intents.add(spec["intent_id"])
                if spec["source_closure_digest"] != row["source"]["digest"]:
                    raise DomainError("hash_mismatch", 422)
                if spec["frames"]["start"] > spec["frames"]["end"]:
                    raise DomainError(
                        "invalid_request",
                        message="Preview frame range start must not exceed end",
                        safe_for_model=True,
                    )
                fps = spec["fps"]
                reduced = Fraction(fps["numerator"], fps["denominator"])
                if (reduced.numerator, reduced.denominator) != (
                    fps["numerator"],
                    fps["denominator"],
                ):
                    raise DomainError(
                        "invalid_request", message="FPS must be reduced", safe_for_model=True
                    )

    def frame_maps(self, project_id: str, document: dict[str, Any]) -> dict[str, dict[str, Any]]:
        result = {}
        for entry in document["entries"]:
            identity = entry["mapping_artifact_id"]
            if identity is None or identity in result:
                continue
            artifact = self.artifacts.get(project_id, identity)
            if artifact["kind"] != "frame_map":
                raise DomainError("invalid_frame_mapping", 422)
            try:
                result[identity] = json.loads(
                    self.artifacts.ready_path(project_id, identity).read_bytes()
                )
            except ValueError as exc:
                raise DomainError("invalid_frame_mapping", 422) from exc
        return result

    @staticmethod
    def validate_documents(
        tx: Transaction,
        body: dict[str, Any],
        documents: dict[str, dict[str, Any]],
        maps: dict[str, dict[str, Any]],
    ) -> None:
        batch = {row["version_id"]: row for row in body["items"]}
        for row in body["items"]:
            document = documents.get(row["version_id"])
            required_refs: list[dict[str, Any]] = []
            if row["kind"] == "storyboard":
                assert document is not None
                validation.shot_plan(tx, document, batch)
                required_refs = document["asset_dependencies"] + [
                    shot["scene_version"]
                    for shot in document["shots"]
                    if shot["scene_version"] is not None
                ]
                identities = document["brief_artifact_ids"] + [
                    frame["artifact_id"]
                    for shot in document["shots"]
                    for frame in shot["frames"]
                    if frame["artifact_id"] is not None
                ]
                if any(
                    tx.require("artifacts", identity)["state"] != "ready" for identity in identities
                ):
                    raise DomainError("media_unavailable", 422)
            elif row["kind"] == "sequence":
                assert document is not None
                validation.sequence_map(tx, document, row["version_id"], maps)
                ids = {
                    entry["version_id"] for entry in document["entries"] if "version_id" in entry
                }
                ids.update(
                    source["version_id"]
                    for entry in document["entries"]
                    for source in entry.get("contributors", [])
                )
                required_refs = [
                    {"item_id": tx.require("versions", identity)["item_id"], "version_id": identity}
                    for identity in ids
                ]
            elif row["kind"] == "unity_result":
                paths = {file["destination"] for file in row["source"]["files"]}
                if row["unity"]["scene_path"] not in paths or (
                    "player_script" in row["unity"] and row["unity"]["player_script"] not in paths
                ):
                    raise DomainError("dependency_missing", 422)
            validation.required_dependencies(row, required_refs)

    def publish(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        validate("PublicationSubmit", body)
        project_id = body["project_id"]
        active(self.services, caller, "publishOutputs", project_id)
        store = self.services.registry.project_store(project_id)
        immutable = binding(caller, body)
        submission = body
        with store.transaction(write=False) as tx:
            previous = replay(tx, "catalogue.publish", body["publication_id"], immutable)
            if previous:
                return {**previous, "replayed": True}, True
            admitted(self.services, tx, caller)
            body = self.resolve_checkpoint_sources(tx, submission)
            self.validate_batch(tx, body)
        # All filesystem/object work is before the database-only commit boundary.
        documents: dict[str, dict[str, Any]] = {}
        maps: dict[str, dict[str, Any]] = {}
        for row in body["items"]:
            git(
                active(self.services, caller, "publishOutputs", project_id),
                "cat-file",
                "-e",
                row["source_commit"] + "^{commit}",
            )
            materialised = self.sources.resolve(
                project_id, row["source"], batch={r["version_id"]: r for r in body["items"]}
            )
            if row["kind"] in {"storyboard", "sequence"}:
                try:
                    document = json.loads((materialised / row["source"]["entry_path"]).read_bytes())
                    if not isinstance(document, dict):
                        raise ValueError("Authored source must be an object")
                except (ValueError, UnicodeError) as exc:
                    if row["kind"] == "storyboard":
                        raise DomainError(
                            "source_unavailable",
                            422,
                            "Storyboard source must be AuthoredShotPlan JSON",
                            safe_for_model=True,
                        ) from exc
                    raise DomainError(
                        "source_unavailable",
                        422,
                        "Authored catalogue source is not JSON",
                        safe_for_model=True,
                    ) from exc
                issues = validation.schema_issues(
                    "AuthoredShotPlan" if row["kind"] == "storyboard" else "SequenceMap", document
                )
                if issues:
                    if row["kind"] == "storyboard":
                        raise DomainError(
                            "invalid_request",
                            422,
                            "Storyboard source failed AuthoredShotPlan validation",
                            issues=issues,
                            safe_for_model=True,
                        )
                    raise DomainError(
                        "invalid_request",
                        422,
                        "Sequence source failed SequenceMap validation",
                        issues=issues,
                        safe_for_model=True,
                    )
                documents[row["version_id"]] = document
                if row["kind"] == "storyboard":
                    for identity in document["brief_artifact_ids"] + [
                        frame["artifact_id"]
                        for shot in document["shots"]
                        for frame in shot["frames"]
                        if frame["artifact_id"] is not None
                    ]:
                        self.artifacts.ready_path(project_id, identity)
                if row["kind"] == "sequence":
                    maps.update(self.frame_maps(project_id, document))
            validation.publication_entry(row)
            for identity in row["artifact_ids"]:
                self.artifacts.ready_path(project_id, identity)
        with store.transaction() as tx:

            def effect() -> dict[str, Any]:
                work = admitted(self.services, tx, caller)
                if self.resolve_checkpoint_sources(tx, submission) != body:
                    raise DomainError(
                        "source_conflict", 409, CHECKPOINT_SOURCE_CONFLICT, safe_for_model=True
                    )
                self.validate_batch(tx, body)
                self.validate_documents(tx, body, documents, maps)
                job_ids = []
                versions = []
                for row in body["items"]:
                    ref = {"item_id": row["item_id"], "version_id": row["version_id"]}
                    jobs = []
                    for spec in row["preview_requests"]:
                        job_id = "preview-" + fingerprint(
                            {"project_id": project_id, "intent_id": spec["intent_id"]}
                        )
                        target = dict(
                            target_id=row["item_id"], version=ref, shot_id=spec["shot_id"]
                        )
                        job = self.services.jobs.create(
                            tx,
                            job_id=job_id,
                            kind="render",
                            target=target,
                            input_digest=fingerprint({"version": ref, "render_spec": spec}),
                            operation_id=body["publication_id"],
                            work_id=caller.work_id,
                            render_spec=spec,
                            expected_frames=spec["frames"]["end"] - spec["frames"]["start"] + 1,
                            enqueue_kind="preview.render",
                        )
                        tx.put(
                            "preview_intents",
                            spec["intent_id"],
                            {"version": ref, "render_spec": spec, "job_id": job_id},
                        )
                        self.services.jobs.freeze_inputs(
                            tx,
                            job_id,
                            {
                                "source": row["source"],
                                "configuration": self.services.settings.view(),
                                "work_id": caller.work_id,
                                "invocation_id": caller.invocation_id,
                                "owner_epoch": caller.owner_epoch,
                            },
                        )
                        self.services.jobs.emit(tx, job, caller)
                        jobs.append(job_id)
                    version = {k: v for k, v in row.items() if k != "expected_current_version_id"}
                    version.update(
                        schema_version="1.0",
                        project_id=project_id,
                        publication_id=body["publication_id"],
                        preview_job_ids=jobs,
                        created_at=now(),
                        actor={"kind": "tool", "id": caller.subject_id},
                    )
                    tx.put("versions", row["version_id"], version, schema="VersionRecord")
                    apply_version_projection(
                        tx, project_version_relationships(version, documents.get(row["version_id"]))
                    )
                    old = tx.get("items", row["item_id"], required=False)
                    revision = tx.revision("items", row["item_id"]) if old else None
                    item = dict(
                        schema_version="1.0",
                        project_id=project_id,
                        item_id=row["item_id"],
                        kind=row["kind"],
                        name=row["name"],
                        current_version_id=row["version_id"],
                        selected_version_id=old["selected_version_id"] if old else None,
                        state_revision=0 if revision is None else revision + 1,
                    )
                    item.update({k: row[k] for k in METADATA if k in row})
                    tx.put(
                        "items",
                        row["item_id"],
                        item,
                        expected_revision=revision,
                        schema="ItemRecord",
                    )
                    versions.append(ref)
                    job_ids.extend(jobs)
                receipt = dict(
                    schema_version="1.0",
                    publication_id=body["publication_id"],
                    project_id=project_id,
                    versions=versions,
                    job_ids=job_ids,
                    event_sequence=tx.watermark() + 1,
                    replayed=False,
                )
                tx.put(
                    "publications",
                    body["publication_id"],
                    {
                        "submission": submission,
                        "receipt": receipt,
                        "invocation_id": caller.invocation_id,
                        "work_id": caller.work_id,
                        "native_session_id": work["native_session_id"],
                        "request_id": work["originating_request_id"],
                    },
                )
                tx.emit(
                    "publication.created",
                    receipt,
                    actor={"kind": "tool", "id": caller.subject_id},
                    work_id=caller.work_id,
                    request_id=work["originating_request_id"],
                    native_session_id=work["native_session_id"],
                )
                return receipt

            result, repeated = tx.operation(
                "catalogue.publish", body["publication_id"], immutable, effect
            )
            return {**result, "replayed": repeated}, repeated

    def version(self, project_id: str, version_id: str) -> dict[str, Any]:
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            return self.version_view(tx, tx.require("versions", version_id))

    def shot_plan(self, project_id: str, version_id: str) -> dict[str, Any]:
        version = self.version(project_id, version_id)
        if version["kind"] != "storyboard":
            raise DomainError(
                "invalid_request",
                message="Request a shot plan from a published storyboard version",
                safe_for_model=True,
            )
        result = self.sources.authored(project_id, version)
        validate("AuthoredShotPlan", result)
        if result["project_id"] != project_id:
            raise DomainError("scope_denied", 403)
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            validation.shot_plan(tx, result, {})
            selections = self.selections_by_slot(tx)
            for shot in result["shots"]:
                for frame in shot["frames"]:
                    slot = dict(
                        kind="storyboard_frame",
                        storyboard_version={
                            "item_id": version["item_id"],
                            "version_id": version_id,
                        },
                        shot_id=shot["shot_id"],
                        frame_id=frame["frame_id"],
                    )
                    selected = False
                    choice = self.choice_from_records(selections.get(canonical(slot), []))
                    if choice:
                        if choice["kind"] == "artifact":
                            chosen = choice["artifact_id"]
                        else:
                            record = tx.require("versions", choice["version"]["version_id"])
                            images = [
                                a
                                for a in record["artifact_ids"]
                                if tx.require("artifacts", a)["kind"] == "image"
                            ]
                            chosen = (
                                images[0]
                                if len(images) == 1
                                and record["item_id"] == choice["version"]["item_id"]
                                else None
                            )
                        selected = (
                            frame["artifact_id"] is not None and frame["artifact_id"] == chosen
                        )
                    frame["owner_selected"] = selected
        result["source_commit"] = version["source_commit"]
        validate("ShotPlan", result)
        return result

    def sequence_map(self, project_id: str, version_id: str) -> dict[str, Any]:
        version = self.version(project_id, version_id)
        if version["kind"] != "sequence":
            raise DomainError(
                "invalid_request",
                message="Request a sequence map from a published sequence version",
                safe_for_model=True,
            )
        result = self.sources.authored(project_id, version)
        validate("SequenceMap", result)
        maps = self.frame_maps(project_id, result)
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            validation.sequence_map(tx, result, version_id, maps)
            selections = self.selections_by_slot(tx)
            for entry in result["entries"]:
                slot = {
                    "kind": "assembly_shot",
                    "sequence_version": {
                        "item_id": version["item_id"],
                        "version_id": version_id,
                    },
                    "entry_id": entry["entry_id"],
                }
                choice = self.choice_from_records(selections.get(canonical(slot), []))
                if "clip_artifact_id" in entry:
                    if choice == {"kind": "artifact", "artifact_id": entry["clip_artifact_id"]}:
                        entry["choice"] = "owner"
                    elif "choice" in entry:
                        entry["choice"] = "agent"
                    continue
                if (
                    choice
                    and choice["kind"] == "version"
                    and choice["version"]["version_id"] == entry["version_id"]
                ):
                    selected = tx.require("versions", entry["version_id"])
                    entry["choice"] = (
                        "owner" if selected["item_id"] == choice["version"]["item_id"] else "agent"
                    )
                elif "choice" in entry:
                    entry["choice"] = "agent"
        return result

    def proxy_mapping(
        self,
        project_id: str,
        version_id: str,
        mapping_artifact_id: str | None = None,
    ) -> dict[str, Any]:
        if mapping_artifact_id is not None and not re.fullmatch(
            r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", mapping_artifact_id
        ):
            raise DomainError(
                "invalid_request",
                message="mapping_artifact_id must be a valid artefact ID",
                safe_for_model=True,
            )
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            version = self.version_view(tx, tx.require("versions", version_id))
            ids = version["artifact_ids"]
            maps = [
                identity
                for identity in ids
                if tx.require("artifacts", identity)["kind"] == "proxy_map"
            ]
            if mapping_artifact_id is not None:
                artifact = tx.require("artifacts", mapping_artifact_id)
                if artifact["kind"] != "proxy_map" or mapping_artifact_id not in maps:
                    raise DomainError(
                        "source_conflict",
                        409,
                        "Proxy mapping does not belong to version",
                        safe_for_model=True,
                    )
                chosen_id = mapping_artifact_id
            else:
                if not maps:
                    raise DomainError(
                        "media_unavailable",
                        422,
                        "No pinned proxy mapping available",
                        safe_for_model=True,
                    )
                ready_maps = [
                    identity
                    for identity in maps
                    if tx.require("artifacts", identity)["state"] == "ready"
                ]
                chosen_id = (ready_maps or maps)[-1]
        try:
            result = json.loads(self.artifacts.ready_path(project_id, chosen_id).read_bytes())
        except ValueError as exc:
            raise DomainError("media_unavailable", 422) from exc
        validate("ProxyMapping", result)
        if (
            result.get("mapping_artifact_id") not in (None, chosen_id)
            or result["project_id"] != project_id
            or result["version_id"] != version_id
            or result["source_closure_digest"] != version["source"]["digest"]
            or result["proxy_artifact_id"] not in ids
        ):
            raise DomainError("source_conflict", 409)
        proxy = self.artifacts.get(project_id, result["proxy_artifact_id"])
        if proxy["kind"] != "proxy":
            raise DomainError("source_conflict", 409)
        self.artifacts.ready_path(project_id, result["proxy_artifact_id"])
        response = {**result, "mapping_artifact_id": chosen_id}
        validate("ProxyMapping", response)
        return response
