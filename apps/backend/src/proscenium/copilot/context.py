"""Immutable owner context, validated against registered, ready project content."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image

from proscenium.api.errors import DomainError
from proscenium.catalogue.service import Catalogue
from proscenium.contracts import validate
from proscenium.db import Transaction
from proscenium.db.paths import project_path
from proscenium.services import Services


class Contexts:
    def __init__(self, services: Services):
        self.services = services

    def get(self, project_id: str, context_id: str) -> dict[str, Any]:
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            return tx.require("contexts", context_id)

    def artifact(self, tx: Transaction, artifact_id: str) -> tuple[dict[str, Any], Path]:
        project_id = str(tx.project_id)
        service = self.services.extension("artifacts")
        artifact = service.get(project_id, artifact_id)
        validate("Artifact", artifact)
        if artifact["project_id"] != project_id or artifact["state"] != "ready":
            raise DomainError("media_unavailable", 422)
        root = Path(self.services.registry.get(project_id)["path"]).resolve()
        path = Path(service.ready_path(project_id, artifact_id)).resolve()
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise DomainError("scope_denied", 403) from exc
        path = project_path(root, relative, managed=True)
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if path.stat().st_size != artifact["bytes"] or digest != artifact["sha256"]:
            raise DomainError("hash_mismatch", 422)
        return artifact, path

    def target(self, tx: Transaction, target: dict[str, Any]) -> None:
        artifact, _ = self.artifact(tx, target["artifact_id"])
        if artifact["sha256"] != target["displayed_sha256"]:
            raise DomainError("hash_mismatch", 422)
        if artifact.get("dimensions") != target["dimensions"]:
            raise DomainError("frame_conflict", 422)
        if target.get("version_id"):
            version = tx.require("versions", target["version_id"])
            if version["item_id"] != target["item_id"]:
                raise DomainError("source_conflict", 409)
            if artifact.get("source_version_id") not in (None, target["version_id"]):
                raise DomainError("source_conflict", 409)
        if target.get("render_job_id"):
            job = tx.require("jobs", target["render_job_id"])
            if (
                job["target"]["version"] is None
                or job["target"]["version"]["version_id"] != target["version_id"]
            ):
                raise DomainError("frame_conflict", 422)
            matches = [
                f
                for f in tx.all("frames")
                if f["artifact_id"] == target["artifact_id"]
                and f["job_id"] == target["render_job_id"]
                and f["version_id"] == target["version_id"]
                and f["source_frame"] == target["source_frame"]
                and f["sha256"] == target["displayed_sha256"]
            ]
            if len(matches) != 1:
                raise DomainError("frame_conflict", 422)
        for key in ("media_artifact_id", "frame_map_artifact_id"):
            if target.get(key):
                self.artifact(tx, target[key])

    def validate_references(self, tx: Transaction, context: dict[str, Any]) -> None:
        validate("ContextSnapshot", context)
        if context["project_id"] != tx.project_id:
            raise DomainError("scope_denied", 403)
        if context["range"] and context["range"]["end"] < context["range"]["start"]:
            raise DomainError("invalid_frame_mapping", 422)
        if context["version_id"]:
            version = tx.require("versions", context["version_id"])
            if version["item_id"] != context["item_id"]:
                raise DomainError("source_conflict", 409)
        if context["target"]:
            self.target(tx, context["target"])
            if (
                context["target"]["version_id"]
                and context["target"]["version_id"] != context["version_id"]
            ):
                raise DomainError("source_conflict", 409)
        if context["selected_objects"]:
            if not context["version_id"]:
                raise DomainError("source_conflict", 409)
            version = tx.require("versions", context["version_id"])
            nodes = []
            for artifact_id in Catalogue.version_view(tx, version)["artifact_ids"]:
                artifact, path = self.artifact(tx, artifact_id)
                if artifact["kind"] == "proxy_map":
                    mapping = validate("ProxyMapping", json.loads(path.read_text()))
                    if (
                        mapping["version_id"] != version["version_id"]
                        or mapping["project_id"] != tx.project_id
                        or mapping["source_closure_digest"] != version["source"]["digest"]
                    ):
                        raise DomainError("source_conflict", 409)
                    nodes.extend(mapping["nodes"])
            if any(
                not any(
                    n["object_id"] == selected["object_id"]
                    and n["instance_id"] == selected["instance_id"]
                    for n in nodes
                )
                for selected in context["selected_objects"]
            ):
                raise DomainError("source_conflict", 409)
        self.camera(context["camera"])
        for finding in context["approved_findings"]:
            self.target(tx, finding["target"])
        for annotation in context["annotations"]:
            target = annotation["target"]
            self.target(tx, target)  # Each annotation retains its OWN frame identity.
            self.camera(annotation["camera"])
            if annotation["range"] and annotation["range"]["end"] < annotation["range"]["start"]:
                raise DomainError("invalid_frame_mapping", 422)
            hits = annotation["spatial_hits"]
            if hits and annotation["spatial_status"] != "valid":
                raise DomainError("frame_conflict", 422)
            for hit in hits:
                if (
                    hit["version_id"] != target["version_id"]
                    or hit["sample_frame"] != target["source_frame"]
                    or hit["camera"] != annotation["camera"]
                ):
                    raise DomainError("frame_conflict", 422)
                self.artifact(tx, hit["proxy_artifact_id"])
                _, path = self.artifact(tx, hit["mapping_artifact_id"])
                mapping = validate("ProxyMapping", json.loads(path.read_text()))
                version = tx.require("versions", hit["version_id"])
                if (
                    mapping["project_id"] != tx.project_id
                    or mapping["version_id"] != hit["version_id"]
                    or mapping["sample_frame"] != hit["sample_frame"]
                    or mapping["proxy_artifact_id"] != hit["proxy_artifact_id"]
                    or mapping["source_closure_digest"] != version["source"]["digest"]
                    or not any(
                        n["object_id"] == hit["object_id"]
                        and n["instance_id"] == hit["instance_id"]
                        for n in mapping["nodes"]
                    )
                    or not math.isclose(sum(v * v for v in hit["normal"]), 1.0, abs_tol=0.01)
                ):
                    raise DomainError("frame_conflict", 422)
            if annotation.get("markup_artifact_id"):
                self.artifact(tx, annotation["markup_artifact_id"])
            for markup in annotation["markups"]:
                self.validate_markup(tx, markup, target)
        self.files(tx, context)

    @staticmethod
    def camera(camera: dict[str, Any] | None) -> None:
        if camera and camera["near_m"] >= camera["far_m"]:
            raise DomainError("frame_conflict", 422)

    @staticmethod
    def attachments(context: dict[str, Any]) -> list[dict[str, Any]]:
        result = list(context["attachments"])
        targets = [
            context["target"],
            *[a["target"] for a in context["annotations"]],
            *[f["target"] for f in context["approved_findings"]],
        ]

        def append(artifact_id: str, role: str) -> None:
            if not any(a["artifact_id"] == artifact_id and a["role"] == role for a in result):
                result.append({"artifact_id": artifact_id, "role": role})

        for target in targets:
            if target:
                append(
                    target["artifact_id"],
                    {
                        "concept": "concept",
                        "reference": "reference",
                        "proxy_capture": "reference",
                    }.get(target["origin"], "render"),
                )
        for annotation in context["annotations"]:
            if annotation.get("markup_artifact_id"):
                append(annotation["markup_artifact_id"], "markup")
            for markup in annotation["markups"]:
                if markup["kind"] == "mask":
                    append(markup["mask_artifact_id"], "mask")
        return sorted(result, key=lambda a: {"markup": 1, "mask": 2}.get(a["role"], 0))

    def validate_markup(
        self, tx: Transaction, markup: dict[str, Any], target: dict[str, Any]
    ) -> None:
        # Mask metadata belongs to the normative markup union, not the current viewport.
        if markup.get("kind") != "mask":
            return
        mask = markup.get("mask", markup)
        artifact_id = mask.get("artifact_id", mask.get("mask_artifact_id"))
        if not artifact_id:
            raise DomainError("invalid_request")
        artifact, path = self.artifact(tx, artifact_id)
        validator = getattr(self.services.extension("artifacts"), "validate_mask", None)
        if validator is not None:
            validator(
                str(tx.project_id),
                artifact_id,
                dimensions=target["dimensions"],
                semantics=markup["mask_encoding"]["semantics"],
            )
            return
        with Image.open(path) as image:
            if (
                image.format != "PNG"
                or image.mode != "L"
                or image.size != (target["dimensions"]["width"], target["dimensions"]["height"])
            ):
                raise DomainError("frame_conflict", 422)
            if artifact["kind"] not in {"mask", "image"}:
                raise DomainError("frame_conflict", 422)
            if markup["mask_encoding"]["semantics"] == "binary" and set(
                image.get_flattened_data()
            ) - {0, 255}:
                raise DomainError("frame_conflict", 422)

    def files(self, tx: Transaction, context: dict[str, Any]) -> list[dict[str, Any]]:
        settings = self.services.settings
        attachments = self.attachments(context)
        if len(attachments) > settings.attachment_max_count:
            raise DomainError("attachment_limit_exceeded", 413)
        result, total = [], 0
        root = Path(self.services.registry.get(str(tx.project_id))["path"]).resolve()
        for attachment in attachments:
            artifact, path = self.artifact(tx, attachment["artifact_id"])
            if artifact["kind"] not in {"image", "mask"} or "dimensions" not in artifact:
                raise DomainError("unsupported_media_type", 415)
            total += artifact["bytes"]
            if (
                total > settings.attachment_max_bytes
                or artifact["bytes"] > settings.upload_max_bytes
            ):
                raise DomainError("attachment_limit_exceeded", 413)
            with Image.open(path) as image:
                if image.width * image.height > settings.upload_max_pixels:
                    raise DomainError("image_dimensions_exceeded", 413)
                if artifact["dimensions"] != {"width": image.width, "height": image.height}:
                    raise DomainError("hash_mismatch", 422)
                image.verify()
            result.append(
                dict(
                    artifact_id=artifact["artifact_id"],
                    role=attachment["role"],
                    sha256=artifact["sha256"],
                    path=path.relative_to(root).as_posix(),
                    dimensions=artifact["dimensions"],
                )
            )
        return result

    def capture(self, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        validate("ContextSnapshot", body)
        with self.services.registry.project_store(body["project_id"]).transaction() as tx:

            def effect() -> dict[str, Any]:
                self.validate_references(tx, body)
                tx.put("contexts", body["context_id"], body, schema="ContextSnapshot")
                return body

            return tx.operation("context.capture", body["context_id"], body, effect)

    def payload(self, tx: Transaction, record: dict[str, Any]) -> dict[str, Any]:
        submission = record["submission"]
        context = tx.require("contexts", submission["context_id"])
        project = self.services.registry.get(record["project_id"])
        return dict(
            schema_version="1.0",
            request_id=record["request_id"],
            work_id=record["receipt"]["work_id"],
            generation_task_id=record["generation_task_id"],
            delivery_mode=submission["type"],
            context_id=context["context_id"],
            project={"project_id": project["project_id"], "path": project["path"]},
            selection={
                key: context[key]
                for key in (
                    "item_id",
                    "version_id",
                    "view_mode",
                    "target",
                    "camera",
                    "range",
                    "selected_objects",
                )
            },
            annotations=context["annotations"],
            approved_findings=context["approved_findings"],
            attached_files=self.files(tx, context),
        )
