"""Relationship checks over F00 records and registered, hash-verified artefacts."""

import hashlib
import json
import math
from fractions import Fraction
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.catalogue.service import Catalogue
from proscenium.catalogue.validation import validate_artifact_source_version
from proscenium.contracts import canonical, validate
from proscenium.db import Transaction
from proscenium.services import Services


class References:
    def __init__(self, services: Services):
        self.services = services

    @staticmethod
    def artifact(tx: Transaction, identity: str, *, ready: bool = True) -> dict[str, Any]:
        record = tx.require("artifacts", identity)
        if record["project_id"] != tx.project_id:
            raise DomainError("scope_denied", 403)
        if ready and record["state"] != "ready":
            raise DomainError("media_unavailable", 422, artifact_id=identity)
        return record

    @staticmethod
    def version(tx: Transaction, ref: dict[str, Any]) -> dict[str, Any]:
        record = tx.require("versions", ref["version_id"])
        if record["project_id"] != tx.project_id or record["item_id"] != ref["item_id"]:
            raise DomainError("scope_denied", 403)
        return record

    def payload(self, tx: Transaction, artifact_id: str, schema: str) -> dict[str, Any]:
        artifact = self.artifact(tx, artifact_id)
        path = self.services.extension("artifacts").ready_path(str(tx.project_id), artifact_id)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
            raise DomainError("media_unavailable", 422)
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise DomainError("invalid_request") from exc
        validate(schema, result)
        if result.get("project_id") != tx.project_id:
            raise DomainError("scope_denied", 403)
        return result

    def target(self, tx: Transaction, target: dict[str, Any], *, ready: bool = True) -> None:
        validate("FrameTarget", target)
        artifact = self.artifact(tx, target["artifact_id"], ready=ready)
        if (
            artifact["kind"] != "image"
            or artifact["sha256"] != target["displayed_sha256"]
            or artifact.get("dimensions") != target["dimensions"]
        ):
            raise DomainError("source_conflict", 409, message="Displayed image identity mismatch")
        if target["version_id"]:
            version = self.version(tx, target)
            validate_artifact_source_version(version, artifact, target)
        elif target["item_id"]:
            tx.require("items", target["item_id"])
        media_id = target["media_artifact_id"]
        if media_id:
            media = self.artifact(tx, media_id, ready=ready)
            if media["kind"] != "video":
                raise DomainError("source_conflict", 409)
        if target["origin"] == "blender_render":
            job = tx.require("jobs", target["render_job_id"] or "")
            version_ref = job["target"]["version"]
            if not version_ref or version_ref != {
                "item_id": target["item_id"],
                "version_id": target["version_id"],
            }:
                raise DomainError("source_conflict", 409)
            slot_receipts = [
                f
                for f in tx.all("frames")
                if f["job_id"] == target["render_job_id"]
                and f["source_frame"] == target["source_frame"]
            ]
            if not slot_receipts:
                raise DomainError("source_conflict", 409, message="No exact frame receipt")
            if any(
                any(
                    f[key] != target[key]
                    for key in ("version_id", "shot_id", "artifact_id", "dimensions")
                )
                or f["sha256"] != target["displayed_sha256"]
                or f["input_digest"] != job["input_digest"]
                for f in slot_receipts
            ):
                raise DomainError(
                    "source_conflict", 409, message="Conflicting frame receipts across attempts"
                )
            if media_id and not any(
                s["artifact_id"] == media_id
                and s["job_id"] == job["job_id"]
                and s["version_id"] == target["version_id"]
                and s["shot_id"] == target["shot_id"]
                and s["source_frames"]["start"]
                <= target["source_frame"]
                <= s["source_frames"]["end"]
                for s in tx.all("segments")
            ):
                raise DomainError("source_conflict", 409)
        if target["origin"] == "sequence_render":
            if target["sequence_version_id"] != target["version_id"]:
                raise DomainError("source_conflict", 409)
            mapping = self.payload(tx, target["frame_map_artifact_id"], "FrameMap")
            if mapping["sequence_version_id"] != target["version_id"]:
                raise DomainError("source_conflict", 409)
            if [r["sequence_frame"] for r in mapping["frames"]] != list(
                range(1, mapping["frame_count"] + 1)
            ):
                raise DomainError(
                    "source_conflict", 409, message="Frame map is not a complete ordered partition"
                )
            for mapped in mapping["frames"]:
                if (
                    sum(
                        (
                            Fraction(c["weight"]["numerator"], c["weight"]["denominator"])
                            for c in mapped["contributors"]
                        ),
                        Fraction(0),
                    )
                    != 1
                ):
                    raise DomainError(
                        "source_conflict", 409, message="Frame weights do not sum to one"
                    )
            row = next(
                (r for r in mapping["frames"] if r["sequence_frame"] == target["sequence_frame"]),
                None,
            )
            if not row:
                raise DomainError("source_conflict", 409)
            for contributor in row["contributors"]:
                matching = [
                    f
                    for f in tx.all("frames")
                    if all(
                        f[k] == contributor[k]
                        for k in ("job_id", "version_id", "shot_id", "source_frame")
                    )
                ]
                if not matching:
                    raise DomainError("media_unavailable", 422)
                for frame in matching:
                    self.artifact(tx, frame["artifact_id"], ready=ready)

    def annotation(self, tx: Transaction, annotation: dict[str, Any]) -> None:
        target = annotation["target"]
        # Offline/missing media remains honest, editable image-space feedback.
        self.target(tx, target, ready=False)
        temporal = annotation["range"]
        anchor = (
            target.get("sequence_frame")
            if target["origin"] == "sequence_render"
            else target["source_frame"]
        )
        if temporal and (anchor is None or not temporal["start"] <= anchor <= temporal["end"]):
            raise DomainError(
                "source_conflict", 409, message="Range must contain its captured anchor"
            )
        if temporal and target["render_job_id"]:
            spec = tx.require("jobs", target["render_job_id"]).get("render_spec")
            if spec and not (
                spec["frames"]["start"]
                <= temporal["start"]
                <= temporal["end"]
                <= spec["frames"]["end"]
            ):
                raise DomainError("source_conflict", 409, message="Range exceeds its render job")
        camera = annotation["camera"]
        if camera:
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
                or not math.isclose(
                    camera["aspect"],
                    target["dimensions"]["width"] / target["dimensions"]["height"],
                    abs_tol=1e-6,
                )
            ):
                raise DomainError(
                    "source_conflict", 409, message="Camera is not a rigid, matching image capture"
                )
        if annotation.get("derived_from"):
            tx.require("annotations", annotation["derived_from"])
        if annotation.get("markup_artifact_id"):
            composite = self.artifact(tx, annotation["markup_artifact_id"])
            if composite["kind"] != "image" or composite.get("dimensions") != target["dimensions"]:
                raise DomainError("source_conflict", 409)
        for markup in annotation["markups"]:
            if markup["kind"] != "mask":
                continue
            mask = self.artifact(tx, markup["mask_artifact_id"])
            if (
                mask["kind"] not in {"mask", "image"}
                or mask.get("dimensions") != target["dimensions"]
            ):
                raise DomainError("source_conflict", 409)
            self.services.extension("artifacts").validate_mask(
                str(tx.project_id),
                mask["artifact_id"],
                dimensions=target["dimensions"],
                semantics=markup["mask_encoding"]["semantics"],
            )
        if (annotation["spatial_status"] == "valid") != bool(annotation["spatial_hits"]):
            raise DomainError("source_conflict", 409)
        for hit in annotation["spatial_hits"]:
            version = self.version(tx, target)
            version_ids = set(Catalogue.version_view(tx, version)["artifact_ids"])
            map_record = self.artifact(tx, hit["mapping_artifact_id"])
            mapping = self.payload(tx, hit["mapping_artifact_id"], "ProxyMapping")
            proxy = self.artifact(tx, hit["proxy_artifact_id"])
            if (
                map_record["kind"] != "proxy_map"
                or proxy["kind"] != "proxy"
                or hit["mapping_artifact_id"] not in version_ids
                or hit["proxy_artifact_id"] not in version_ids
                or mapping.get("mapping_artifact_id") not in (None, hit["mapping_artifact_id"])
                or hit["version_id"] != target["version_id"]
                or hit["sample_frame"] != target["source_frame"]
                or mapping["version_id"] != hit["version_id"]
                or mapping["sample_frame"] != hit["sample_frame"]
                or mapping["proxy_artifact_id"] != hit["proxy_artifact_id"]
                or mapping["source_closure_digest"] != version["source"]["digest"]
                or annotation["camera"] is None
                or canonical(hit["camera"]) != canonical(annotation["camera"])
                or not all(math.isfinite(v) for v in (*hit["position"], *hit["normal"]))
                or not math.isclose(sum(v * v for v in hit["normal"]), 1.0, abs_tol=0.01)
                or not any(
                    n["object_id"] == hit["object_id"] and n["instance_id"] == hit["instance_id"]
                    for n in mapping["nodes"]
                )
            ):
                raise DomainError("source_conflict", 409, message="Spatial mapping mismatch")

    def selection(
        self,
        tx: Transaction,
        slot: dict[str, Any],
        choice: dict[str, Any] | None,
        document: dict[str, Any] | None = None,
    ) -> None:
        frame = None
        if slot["kind"] == "item":
            tx.require("items", slot["item_id"])
        else:
            key = "storyboard_version" if slot["kind"] == "storyboard_frame" else "sequence_version"
            version = self.version(tx, slot[key])
            data = document
            if data is None:
                raise DomainError("capability_unavailable", 501)
            if data["project_id"] != tx.project_id:
                raise DomainError("scope_denied", 403)
            if key == "sequence_version" and data["sequence_version_id"] != version["version_id"]:
                raise DomainError("source_conflict", 409)
            if key == "storyboard_version":
                frame = next(
                    (
                        f
                        for s in data["shots"]
                        if s["shot_id"] == slot["shot_id"]
                        for f in s["frames"]
                        if f["frame_id"] == slot["frame_id"]
                    ),
                    None,
                )
                if frame is None:
                    raise DomainError("source_conflict", 409, message="Selection slot missing")
            elif not any(e["entry_id"] == slot["entry_id"] for e in data["entries"]):
                raise DomainError("source_conflict", 409, message="Selection slot missing")
        if choice is None:
            return
        if choice["kind"] == "artifact":
            artifact = self.artifact(tx, choice["artifact_id"])
            if slot["kind"] != "storyboard_frame" or artifact["kind"] != "image":
                raise DomainError("source_conflict", 409)
        else:
            selected = self.version(tx, choice["version"])
            if slot["kind"] == "item" and selected["item_id"] != slot["item_id"]:
                raise DomainError("source_conflict", 409)
            if slot["kind"] == "assembly_shot" and selected["kind"] not in {"scene", "asset"}:
                raise DomainError("source_conflict", 409)
            if frame is not None:
                images = [
                    a for a in selected["artifact_ids"] if self.artifact(tx, a)["kind"] == "image"
                ]
                if len(images) != 1:
                    raise DomainError(
                        "source_conflict", 409, message="Choice does not resolve to one image"
                    )

    def selection_document(
        self, project_id: str, version: dict[str, Any], kind: str
    ) -> dict[str, Any]:
        """Historical materialisation occurs outside the mutation transaction."""
        root = self.services.extension("sources").materialise(project_id, version["version_id"])
        path = root / version["source"]["entry_path"] if root.is_dir() else root
        try:
            result = json.loads(path.read_bytes())
        except (ValueError, UnicodeDecodeError, OSError) as exc:
            raise DomainError("media_unavailable", 422) from exc
        validate("AuthoredShotPlan" if kind == "storyboard_frame" else "SequenceMap", result)
        return result
