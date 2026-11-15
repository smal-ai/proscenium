"""Atomic publication, immutable version reads and typed authored projections."""

import json
from fractions import Fraction
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import validate
from proscenium.db import Transaction
from proscenium.db.store import now
from proscenium.services import Services
from proscenium.sources import Sources
from proscenium.sources.common import active, admitted, binding, fingerprint, git, replay

from . import validation
from .artifacts import Artifacts

METADATA = ("description", "tags", "provenance")


class Catalogue:
    def __init__(self, services: Services, sources: Sources, artifacts: Artifacts):
        self.services, self.sources, self.artifacts = services, sources, artifacts

    @staticmethod
    def selected_choice(tx: Transaction, slot: dict[str, Any]) -> dict[str, Any] | None:
        records = [row for row in tx.all("selections") if row["slot"] == slot]
        if len(records) > 1:
            raise DomainError(
                "source_conflict", 409, "More than one current selection record for a slot"
            )
        if not records or records[0]["state"] != "selected":
            return None
        return dict(records[0]["choice"])

    @classmethod
    def item_view(cls, tx: Transaction, row: dict[str, Any]) -> dict[str, Any]:
        result = {**row, "selected_version_id": None}
        choice = cls.selected_choice(tx, {"kind": "item", "item_id": row["item_id"]})
        if choice:
            if choice["kind"] != "version" or choice["version"]["item_id"] != row["item_id"]:
                raise DomainError("source_conflict", 409)
            version = tx.require("versions", choice["version"]["version_id"])
            if version["item_id"] != row["item_id"]:
                raise DomainError("scope_denied", 403)
            result["selected_version_id"] = version["version_id"]
        return result

    @staticmethod
    def version_view(tx: Transaction, row: dict[str, Any]) -> dict[str, Any]:
        links = tx.get("version_links", row["version_id"], required=False) or {}
        reference = {"item_id": row["item_id"], "version_id": row["version_id"]}
        jobs = [
            job
            for job in tx.all("jobs")
            if job["target"]["version"] == reference and job["kind"] == "render"
        ]
        return {
            **row,
            "preview_job_ids": list(
                dict.fromkeys(row["preview_job_ids"] + [job["job_id"] for job in jobs])
            ),
            "artifact_ids": list(
                dict.fromkeys(
                    row["artifact_ids"]
                    + links.get("artifact_ids", [])
                    + [identity for job in jobs for identity in job["artifact_ids"]]
                )
            ),
        }

    def validate_batch(self, tx: Transaction, body: dict[str, Any]) -> None:
        items = body["items"]
        batch = {row["version_id"]: row for row in items}
        if len(batch) != len(items) or len({row["item_id"] for row in items}) != len(items):
            raise DomainError("invalid_request")
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
                raise DomainError("source_conflict", 409, "Dependency cycle")
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
                raise DomainError("source_conflict", 409)
            item = tx.get("items", row["item_id"], required=False)
            if (item["current_version_id"] if item else None) != row["expected_current_version_id"]:
                raise DomainError("publication_head_conflict", 409, item_id=row["item_id"])
            if item and item["kind"] != row["kind"]:
                raise DomainError("source_conflict", 409)
            visit(row)
            for identity in row["artifact_ids"]:
                if tx.require("artifacts", identity)["state"] != "ready":
                    raise DomainError("media_unavailable", 422)
            for spec in row["preview_requests"]:
                if spec["intent_id"] in intents or tx.get(
                    "preview_intents", spec["intent_id"], required=False
                ):
                    raise DomainError("idempotency_conflict", 409)
                intents.add(spec["intent_id"])
                if spec["source_closure_digest"] != row["source"]["digest"]:
                    raise DomainError("hash_mismatch", 422)
                if spec["frames"]["start"] > spec["frames"]["end"]:
                    raise DomainError("invalid_request")
                fps = spec["fps"]
                reduced = Fraction(fps["numerator"], fps["denominator"])
                if (reduced.numerator, reduced.denominator) != (
                    fps["numerator"],
                    fps["denominator"],
                ):
                    raise DomainError("invalid_request", message="FPS must be reduced")

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
                ids = {entry["version_id"] for entry in document["entries"]}
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
            if any(reference not in row["source"]["dependencies"] for reference in required_refs):
                raise DomainError(
                    "dependency_missing",
                    422,
                    "Authored version references must be pinned in the source closure",
                )

    def publish(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        validate("PublicationSubmit", body)
        project_id = body["project_id"]
        active(self.services, caller, "publishOutputs", project_id)
        store = self.services.registry.project_store(project_id)
        immutable = binding(caller, body)
        with store.transaction(write=False) as tx:
            previous = replay(tx, "catalogue.publish", body["publication_id"], immutable)
            if previous:
                return {**previous, "replayed": True}, True
            admitted(self.services, tx, caller)
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
                    raise DomainError(
                        "source_unavailable",
                        422,
                        "Storyboard source must be AuthoredShotPlan JSON"
                        if row["kind"] == "storyboard"
                        else "Authored catalogue source is not JSON",
                    ) from exc
                issues = validation.schema_issues(
                    "AuthoredShotPlan" if row["kind"] == "storyboard" else "SequenceMap", document
                )
                if issues:
                    raise DomainError(
                        "invalid_request",
                        422,
                        "Storyboard source failed AuthoredShotPlan validation"
                        if row["kind"] == "storyboard"
                        else "Sequence source failed SequenceMap validation",
                        issues=issues,
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
            entry = [
                f for f in row["source"]["files"] if f["destination"] == row["source"]["entry_path"]
            ]
            if not entry or (
                entry[0]["origin"]["kind"] == "git"
                and entry[0]["origin"]["commit"] != row["source_commit"]
            ):
                raise DomainError("source_conflict", 409)
            for identity in row["artifact_ids"]:
                self.artifacts.ready_path(project_id, identity)
        with store.transaction() as tx:

            def effect() -> dict[str, Any]:
                work = admitted(self.services, tx, caller)
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
                        "submission": body,
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
            raise DomainError("invalid_request")
        result = self.sources.authored(project_id, version)
        validate("AuthoredShotPlan", result)
        if result["project_id"] != project_id:
            raise DomainError("scope_denied", 403)
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            validation.shot_plan(tx, result, {})
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
                    choice = self.selected_choice(tx, slot)
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
            raise DomainError("invalid_request")
        result = self.sources.authored(project_id, version)
        validate("SequenceMap", result)
        maps = self.frame_maps(project_id, result)
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            validation.sequence_map(tx, result, version_id, maps)
            for entry in result["entries"]:
                choice = self.selected_choice(
                    tx,
                    {
                        "kind": "assembly_shot",
                        "sequence_version": {
                            "item_id": version["item_id"],
                            "version_id": version_id,
                        },
                        "entry_id": entry["entry_id"],
                    },
                )
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

    def proxy_mapping(self, project_id: str, version_id: str) -> dict[str, Any]:
        version = self.version(project_id, version_id)
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            # W03 links newly completed proxy artefacts without rewriting the publication.
            links = tx.get("version_links", version_id, required=False) or {}
            ids = list(dict.fromkeys(version["artifact_ids"] + links.get("artifact_ids", [])))
            maps = [
                identity
                for identity in ids
                if tx.require("artifacts", identity)["kind"] == "proxy_map"
            ]
        if len(maps) != 1:
            raise DomainError("media_unavailable", 422, "Expected one pinned proxy mapping")
        try:
            result = json.loads(self.artifacts.ready_path(project_id, maps[0]).read_bytes())
        except ValueError as exc:
            raise DomainError("media_unavailable", 422) from exc
        validate("ProxyMapping", result)
        if (
            result["project_id"] != project_id
            or result["version_id"] != version_id
            or result["source_closure_digest"] != version["source"]["digest"]
            or result["proxy_artifact_id"] not in ids
        ):
            raise DomainError("source_conflict", 409)
        proxy = self.artifacts.get(project_id, result["proxy_artifact_id"])
        if proxy["kind"] != "proxy":
            raise DomainError("source_conflict", 409)
        self.artifacts.ready_path(project_id, result["proxy_artifact_id"])
        return dict(result)
