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
from proscenium.catalogue.validation import validate_artifact_source_version
from proscenium.contracts import canonical, validate
from proscenium.db import Transaction
from proscenium.db.paths import project_path
from proscenium.services import Services
from proscenium.sources.read_only import read_authored_json


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
            validate_artifact_source_version(version, artifact, target)
        if target.get("render_job_id"):
            job = tx.require("jobs", target["render_job_id"])
            if (
                job["target"]["version"] is None
                or job["target"]["version"]["version_id"] != target["version_id"]
            ):
                raise DomainError("frame_conflict", 422)
            slot_receipts = [
                f
                for f in tx.all("frames")
                if f["job_id"] == target["render_job_id"]
                and f["source_frame"] == target["source_frame"]
            ]
            if not slot_receipts or any(
                any(
                    f[key] != target[key]
                    for key in ("artifact_id", "version_id", "shot_id", "dimensions")
                )
                or f["sha256"] != target["displayed_sha256"]
                or f["input_digest"] != job["input_digest"]
                for f in slot_receipts
            ):
                raise DomainError("frame_conflict", 422)
        for key in ("media_artifact_id", "frame_map_artifact_id"):
            if target.get(key):
                self.artifact(tx, target[key])

    def validate_references(self, tx: Transaction, context: dict[str, Any]) -> None:
        validate("ContextSnapshot", context)
        if context["project_id"] != tx.project_id:
            raise DomainError("scope_denied", 403)
        self.request_references(tx, context)
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
            self.camera(annotation["camera"], dimensions=target["dimensions"])
            if annotation["range"] and annotation["range"]["end"] < annotation["range"]["start"]:
                raise DomainError("invalid_frame_mapping", 422)
            hits = annotation["spatial_hits"]
            if (annotation["spatial_status"] == "valid") != bool(hits):
                raise DomainError("frame_conflict", 422)
            for hit in hits:
                if (
                    hit["version_id"] != target["version_id"]
                    or hit["sample_frame"] != target["source_frame"]
                    or annotation["camera"] is None
                    or canonical(hit["camera"]) != canonical(annotation["camera"])
                ):
                    raise DomainError("frame_conflict", 422)
                proxy_artifact, _ = self.artifact(tx, hit["proxy_artifact_id"])
                map_artifact, path = self.artifact(tx, hit["mapping_artifact_id"])
                mapping = validate("ProxyMapping", json.loads(path.read_text()))
                version = tx.require("versions", hit["version_id"])
                version_ids = set(Catalogue.version_view(tx, version)["artifact_ids"])
                if (
                    proxy_artifact["kind"] != "proxy"
                    or map_artifact["kind"] != "proxy_map"
                    or hit["proxy_artifact_id"] not in version_ids
                    or hit["mapping_artifact_id"] not in version_ids
                    or mapping.get("mapping_artifact_id") not in (None, hit["mapping_artifact_id"])
                    or mapping["project_id"] != tx.project_id
                    or mapping["version_id"] != hit["version_id"]
                    or mapping["sample_frame"] != hit["sample_frame"]
                    or mapping["proxy_artifact_id"] != hit["proxy_artifact_id"]
                    or mapping["source_closure_digest"] != version["source"]["digest"]
                    or not any(
                        n["object_id"] == hit["object_id"]
                        and n["instance_id"] == hit["instance_id"]
                        for n in mapping["nodes"]
                    )
                    or not all(math.isfinite(v) for v in (*hit["position"], *hit["normal"]))
                    or not math.isclose(sum(v * v for v in hit["normal"]), 1.0, abs_tol=0.01)
                ):
                    raise DomainError("frame_conflict", 422)
            if annotation.get("markup_artifact_id"):
                self.artifact(tx, annotation["markup_artifact_id"])
            for markup in annotation["markups"]:
                self.validate_markup(tx, markup, target)
        self.files(tx, context)

    def request_references(self, tx: Transaction, context: dict[str, Any]) -> None:
        def pinned(ref: dict[str, Any]) -> dict[str, Any]:
            item = tx.require("items", ref["item_id"])
            version = tx.require("versions", ref["version_id"])
            if item["project_id"] != tx.project_id or version["project_id"] != tx.project_id:
                raise DomainError("scope_denied", 403)
            if version["item_id"] != ref["item_id"]:
                raise DomainError(
                    "source_conflict",
                    409,
                    f"Version {ref['version_id']} belongs to item {version['item_id']}, not {ref['item_id']}.",
                )
            return version

        for ref in context.get("reference_versions", []):
            pinned(ref)
        target = context.get("request_target")
        if not target:
            return
        if target["kind"] in {"project", "new_item"}:
            if context["item_id"] is not None or context["version_id"] is not None:
                raise DomainError(
                    "source_conflict",
                    409,
                    f"Request target '{target['kind']}' cannot be combined with pinned selection "
                    f"{context['version_id'] or context['item_id']}.",
                )
            return
        ref = target["version"] if target["kind"] == "item" else target["storyboard_version"]
        version = pinned(ref)
        if version["kind"] not in {"asset", "scene", "storyboard"}:
            raise DomainError(
                "source_conflict",
                409,
                f"Version {version['version_id']} has kind '{version['kind']}', "
                f"which cannot be used as an '{target['kind']}' request target.",
            )
        if target["kind"] == "shot" and version["kind"] != "storyboard":
            raise DomainError(
                "source_conflict",
                409,
                f"Shot target '{target['shot_id']}' requires a storyboard version, "
                f"but {version['version_id']} has kind '{version['kind']}'.",
            )
        if context["version_id"] is not None and (
            context["item_id"] != ref["item_id"] or context["version_id"] != ref["version_id"]
        ):
            raise DomainError(
                "source_conflict",
                409,
                f"Request target {ref['item_id']} ({ref['version_id']}) conflicts with viewed selection "
                f"{context['item_id']} ({context['version_id']}).",
            )
        if target["kind"] == "shot":
            # Read immutable objects only. The exact version and dependencies come from
            # this transaction, including records not yet visible to another reader.
            batch = {v["version_id"]: v for v in tx.all("versions")}
            plan = read_authored_json(self.services, str(tx.project_id), version, batch=batch)
            validate("AuthoredShotPlan", plan)
            if plan["project_id"] != tx.project_id:
                raise DomainError("scope_denied", 403)
            if not any(shot["shot_id"] == target["shot_id"] for shot in plan["shots"]):
                raise DomainError("not_found", 404)
            if tx.require("versions", ref["version_id"]) != version:
                raise DomainError(
                    "source_conflict",
                    409,
                    f"Storyboard version {ref['version_id']} changed while validating shot target "
                    f"'{target['shot_id']}'.",
                )

    @staticmethod
    def camera(camera: dict[str, Any] | None, *, dimensions: dict[str, int] | None = None) -> None:
        if not camera:
            return
        matrix = camera["camera_to_world"]
        axes = [[matrix[row * 4 + col] for row in range(3)] for col in range(3)]
        rigid = all(
            math.isclose(
                sum(a * b for a, b in zip(axes[i], axes[j], strict=True)),
                float(i == j),
                abs_tol=1e-6,
            )
            for i in range(3)
            for j in range(3)
        )
        if (
            not rigid
            or matrix[12:] != [0, 0, 0, 1]
            or camera["near_m"] >= camera["far_m"]
            or (
                dimensions is not None
                and not math.isclose(
                    camera["aspect"],
                    dimensions["width"] / dimensions["height"],
                    abs_tol=1e-6,
                )
            )
        ):
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
            request_target=context.get("request_target"),
            reference_versions=context.get("reference_versions", []),
            execution_scope=context.get("execution_scope", "default"),
            attached_files=self.files(tx, context),
        )
