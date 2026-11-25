"""Immutable project media. Public IDs never resolve caller-provided file paths."""

import io
import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import validate
from proscenium.db import Transaction
from proscenium.services import Services
from proscenium.sources.common import (
    active,
    admitted,
    atomic_bytes,
    binding,
    managed,
    replay,
    sha,
)

AUDIO_MEDIA_TYPES = frozenset({"audio/flac", "audio/wav", "audio/mpeg", "audio/ogg", "audio/mp4"})
AUDIO_METADATA = frozenset({"duration_seconds", "sample_rate_hz", "channels"})

TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


@dataclass(frozen=True)
class ImageLimits:
    bytes: int = 25 * 1024**2
    pixels: int = 40_000_000


class Artifacts:
    def list_retained(
        self,
        project_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
        kind: str | None = None,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        from proscenium.contracts import SCHEMA
        from proscenium.copilot.outcomes import artifact_attributions

        for field, value in (("kind", kind), ("purpose", purpose)):
            if (
                value is not None
                and value not in SCHEMA["$defs"]["Artifact"]["properties"][field]["enum"]
            ):
                raise DomainError("invalid_request")
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            attributions = artifact_attributions(tx)
            records = []
            for artifact in tx.all("artifacts"):
                if artifact["project_id"] != project_id:
                    raise DomainError("scope_denied", 403)
                if (kind is None or artifact["kind"] == kind) and (
                    purpose is None or artifact["purpose"] == purpose
                ):
                    summary = dict(
                        schema_version="1.0",
                        artifact=artifact,
                        **attributions[artifact["artifact_id"]],
                    )
                    validate("RetainedArtifactSummary", summary)
                    records.append({"artifact_id": artifact["artifact_id"], "summary": summary})
        result = self.services.cursors.page(
            f"{project_id}:retained-artifacts:{kind}:{purpose}",
            records,
            "artifact_id",
            cursor=cursor,
            limit=limit,
        )
        result["items"] = [row["summary"] for row in result["items"]]
        return validate("RetainedArtifactPage", result)

    def __init__(self, services: Services, limits: ImageLimits | None = None):
        self.services = services
        self.limits = limits or ImageLimits(
            services.settings.upload_max_bytes, services.settings.upload_max_pixels
        )

    def path(self, project_id: str, digest: str) -> Path:
        root = Path(self.services.registry.get(project_id)["path"])
        return managed(root, f".proscenium/artefacts/sha256/{digest}/content")

    def get(self, project_id: str, artifact_id: str) -> dict[str, Any]:
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            return tx.require("artifacts", artifact_id)

    def ready_path(self, project_id: str, artifact_id: str) -> Path:
        artifact = self.get(project_id, artifact_id)
        if artifact["state"] != "ready":
            raise DomainError("media_unavailable", 422)
        path = self.path(project_id, artifact["sha256"])
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise DomainError("media_unavailable", 422) from exc
        if len(data) != artifact["bytes"] or sha(data) != artifact["sha256"]:
            raise DomainError("hash_mismatch", 422)
        return path

    def decode(self, data: bytes, media_type: str, *, mask: bool = False) -> Image.Image:
        if len(data) > self.limits.bytes:
            raise DomainError("upload_too_large", 413)
        if media_type not in TYPES.values():
            raise DomainError("unsupported_media_type", 415)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image = Image.open(io.BytesIO(data))
                if image.width * image.height > self.limits.pixels:
                    raise DomainError("image_dimensions_exceeded", 413)
                if TYPES.get(str(image.format)) != media_type or getattr(image, "n_frames", 1) != 1:
                    raise DomainError("unsupported_media_type", 415)
                image.load()
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise DomainError("image_dimensions_exceeded", 413) from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise DomainError("unsupported_media_type", 415) from exc
        if mask and (
            image.format != "PNG"
            or image.mode != "L"
            or image.info.get("icc_profile")
            or image.getexif().get(274, 1) != 1
            or "transparency" in image.info
            or "gamma" in image.info
            or "srgb" in image.info
        ):
            raise DomainError(
                "invalid_request", message="Mask must be untagged 8-bit greyscale PNG"
            )
        return image

    def promote(
        self,
        project_id: str,
        artifact_id: str,
        data: bytes,
        *,
        kind: str,
        purpose: str,
        media_type: str,
        derived_from: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = dict(metadata or {})
        if kind == "audio":
            if media_type not in AUDIO_MEDIA_TYPES:
                raise DomainError("unsupported_media_type", 415)
            if media_type == "audio/flac":
                from proscenium.providers.stable_audio import inspect_flac_bytes

                inspection = inspect_flac_bytes(data)
                for key in AUDIO_METADATA:
                    if metadata.get(key, inspection[key]) != inspection[key]:
                        raise DomainError("hash_mismatch", 422)
                    metadata[key] = inspection[key]
        if not data:
            raise DomainError("invalid_request")
        if kind in {"proxy_map", "frame_map"}:
            if media_type != "application/json":
                raise DomainError("unsupported_media_type", 415)
            try:
                mapping = json.loads(data)
            except ValueError as exc:
                raise DomainError("invalid_request") from exc
            validate("ProxyMapping" if kind == "proxy_map" else "FrameMap", mapping)
            if mapping["project_id"] != project_id:
                raise DomainError("scope_denied", 403)
        dimensions = None
        if kind in {"image", "mask"}:
            image = self.decode(data, media_type, mask=kind == "mask")
            dimensions = dict(width=image.width, height=image.height)
        result: dict[str, Any] = dict(
            schema_version="1.0",
            artifact_id=artifact_id,
            project_id=project_id,
            kind=kind,
            purpose=purpose,
            sha256=sha(data),
            bytes=len(data),
            media_type=media_type,
            state="ready",
            derived_from=derived_from or [],
        )
        if dimensions:
            result["dimensions"] = dimensions
        if set(metadata) - (
            {"fps", "frame_count", "source_version_id", "dimensions"}
            | (AUDIO_METADATA if kind == "audio" else frozenset())
        ):
            raise ValueError("Trusted media metadata cannot replace immutable byte/identity fields")
        if dimensions and (metadata or {}).get("dimensions", dimensions) != dimensions:
            raise DomainError("hash_mismatch", 422)
        result.update(metadata or {})
        validate("Artifact", result)
        atomic_bytes(self.path(project_id, result["sha256"]), data)
        return result

    @staticmethod
    def put(tx: Transaction, artifact: dict[str, Any]) -> None:
        old = tx.get("artifacts", artifact["artifact_id"], required=False)
        if old is not None:
            if old != artifact:
                raise DomainError("idempotency_conflict", 409)
            return
        tx.put("artifacts", artifact["artifact_id"], artifact, schema="Artifact")

    def upload(
        self, caller: Caller, project_id: str, upload_id: str, data: bytes, media_type: str
    ) -> tuple[dict[str, Any], bool]:
        caller.permits("uploadImage", project_id)
        if caller.channel != "owner":
            raise DomainError("scope_denied", 403)
        validate("Id", upload_id)
        store = self.services.registry.project_store(project_id)
        immutable = dict(sha256=sha(data), media_type=media_type)
        with store.transaction(write=False) as tx:
            previous = replay(tx, "artifacts.upload", upload_id, immutable)
        if previous:
            return previous, True
        image = self.decode(data, media_type)
        original_id = "original-" + sha(upload_id.encode())
        transformations = []
        displayed = ImageOps.exif_transpose(image)
        if image.getexif().get(274, 1) != 1:
            transformations.append(
                dict(
                    kind="orientation",
                    input_artifact_ids=[original_id],
                    parameters={"exif_orientation": image.getexif()[274]},
                )
            )
        if image.info.get("icc_profile"):
            try:
                converted = ImageCms.profileToProfile(
                    displayed,
                    ImageCms.ImageCmsProfile(io.BytesIO(image.info["icc_profile"])),
                    ImageCms.createProfile("sRGB"),
                    outputMode="RGBA" if "A" in image.mode else "RGB",
                )
                if converted is None:
                    raise DomainError("unsupported_media_type", 415)
                displayed = converted
            except (OSError, ValueError, ImageCms.PyCMSError) as exc:
                raise DomainError("unsupported_media_type", 415) from exc
            transformations.append(
                dict(
                    kind="encode",
                    input_artifact_ids=[original_id],
                    parameters={"colour_space": "sRGB"},
                )
            )
        elif displayed.mode not in {"RGB", "RGBA", "L", "LA", "P", "1"}:
            source_mode = displayed.mode
            displayed = displayed.convert("RGB")
            transformations.append(
                dict(
                    kind="encode",
                    input_artifact_ids=[original_id],
                    parameters={
                        "source_mode": source_mode,
                        "output_mode": "RGB",
                        "decoder": "Pillow",
                    },
                )
            )
        original = None
        if transformations:
            original = self.promote(
                project_id,
                original_id,
                data,
                kind="image",
                purpose="reference",
                media_type=media_type,
            )
            output = io.BytesIO()
            displayed.info.clear()
            displayed.save(output, format="PNG")
            data, media_type = output.getvalue(), "image/png"
        artifact = self.promote(
            project_id,
            upload_id,
            data,
            kind="image",
            purpose="reference",
            media_type=media_type,
            derived_from=transformations,
        )
        with store.transaction() as tx:

            def effect() -> dict[str, Any]:
                if original:
                    self.put(tx, original)
                self.put(tx, artifact)
                return artifact

            return tx.operation("artifacts.upload", upload_id, immutable, effect)

    def register(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        validate("ArtifactRegister", body)
        project_id = body["project_id"]
        root = active(self.services, caller, "registerToolArtifact", project_id)
        store = self.services.registry.project_store(project_id)
        immutable = binding(caller, body)
        with store.transaction(write=False) as tx:
            previous = replay(tx, "artifacts.register", body["operation_id"], immutable)
            if previous:
                return previous, True
            admitted(self.services, tx, caller)
        if not body["staging_path"].startswith(".proscenium/tool-output/staging/"):
            raise DomainError("scope_denied", 403)
        path = managed(root, body["staging_path"], exists=True)
        data = path.read_bytes()
        if sha(data) != body["sha256"]:
            raise DomainError("hash_mismatch", 422)
        artifact = self.promote(
            project_id,
            body["artifact_id"],
            data,
            kind=body["kind"],
            purpose=body["purpose"],
            media_type=body["media_type"],
            derived_from=body.get("derived_from"),
        )
        with store.transaction() as tx:

            def effect() -> dict[str, Any]:
                admitted(self.services, tx, caller)
                for transform in body.get("derived_from", []):
                    for identity in transform["input_artifact_ids"]:
                        if tx.require("artifacts", identity)["state"] != "ready":
                            raise DomainError("media_unavailable", 422)
                self.put(tx, artifact)
                return artifact

            return tx.operation("artifacts.register", body["operation_id"], immutable, effect)

    def validate_mask(
        self, project_id: str, artifact_id: str, *, dimensions: dict[str, int], semantics: str
    ) -> dict[str, Any]:
        artifact = self.get(project_id, artifact_id)
        if (
            artifact["kind"] not in {"mask", "image"}
            or artifact.get("dimensions") != dimensions
            or semantics not in {"binary", "soft"}
        ):
            raise DomainError(
                "invalid_request", message="Mask encoding/target dimensions do not match"
            )
        image = self.decode(
            self.ready_path(project_id, artifact_id).read_bytes(), "image/png", mask=True
        )
        if semantics == "binary" and any(value not in {0, 255} for value in image.tobytes()):
            raise DomainError("invalid_request", message="Binary masks contain only 0 and 255")
        return artifact

    def retain(self, caller: Caller, project_id: str, artifact_id: str, *, reason: str) -> None:
        """Owner's explicit keep-out-of-Git decision, not inferred from licence metadata."""
        if caller.channel != "owner" or not reason.strip():
            raise DomainError("permission_denied", 403)
        artifact = self.get(project_id, artifact_id)
        if artifact["kind"] != "source_blob":
            raise DomainError("invalid_request")
        root = Path(self.services.registry.get(project_id)["path"])
        data = self.ready_path(project_id, artifact_id).read_bytes()
        atomic_bytes(managed(root, f".proscenium/retained/{artifact['sha256']}/content"), data)
        with self.services.registry.project_store(project_id).transaction() as tx:
            if not tx.get("retained_sources", artifact_id, required=False):
                tx.put(
                    "retained_sources",
                    artifact_id,
                    {"sha256": artifact["sha256"], "reason": reason, "owner_id": caller.subject_id},
                )
