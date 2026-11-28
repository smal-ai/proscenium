"""Offline owner import. Verified media are observations, never queued native work."""

import io
import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue import validation
from proscenium.catalogue.artifacts import Artifacts
from proscenium.catalogue.relationship_projector import (
    apply_version_projection,
    project_version_relationships,
)
from proscenium.catalogue.service import METADATA, Catalogue
from proscenium.contracts import canonical, validate
from proscenium.db import Transaction
from proscenium.db.ownership import Ownership
from proscenium.db.store import now
from proscenium.previews.assembly import expand, technical
from proscenium.previews.config import Rendering
from proscenium.previews.media import encode, inspect_video, verify_video
from proscenium.previews.service import PreviewService
from proscenium.reviews.references import References
from proscenium.services import Services
from proscenium.sources import Sources, closure_digest
from proscenium.sources.common import atomic_bytes, fingerprint, git, managed, relative, replay, sha

from .service import Projects, owner_only

SCOPE = "projects.import"
ITEM_MEDIA_TYPES = {
    "image": frozenset({"image/png", "image/jpeg", "image/webp"}),
    "video": frozenset({"video/mp4"}),
    "document": frozenset({"application/json", "text/plain", "text/markdown"}),
}


def fields(value: Any, required: str, optional: str = "") -> None:
    keys = set(required.split())
    if (
        not isinstance(value, dict)
        or keys - value.keys()
        or value.keys() - keys - set(optional.split())
    ):
        raise DomainError(
            "invalid_request", 422, f"Expected fields: {required}; optional: {optional}"
        )


def array(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise DomainError("invalid_request", 422, "Expected an array")
    return value


def identity(prefix: str, *parts: Any) -> str:
    return prefix + "-" + fingerprint(list(parts))


def ref(row: dict) -> dict:
    return {key: row[key] for key in ("item_id", "version_id")}


def reject_non_json(value: str) -> None:
    raise ValueError(f"Not a JSON value: {value}")


@dataclass
class PreparedJob:
    record: dict
    inputs: dict
    receipts: list[dict] = field(default_factory=list)
    segment: dict | None = None
    assembly: dict | None = None


class Import:
    def __init__(self, services: Services, caller: Caller, project: dict, manifest: dict):
        self.services, self.caller, self.manifest = services, caller, manifest
        self.project_id, self.repository_id = project["project_id"], project["repository_id"]
        self.root = Path(project["path"])
        self.store = services.registry.project_store(self.project_id, active=False)
        self.sources = Sources(services)
        self.artifacts = Artifacts(services)
        self.catalogue = Catalogue(services, self.sources, self.artifacts)
        self.operation = manifest["import_id"]
        self.publication = identity("import-publication", self.operation)
        self.actor = {"kind": "user", "id": caller.subject_id}
        self.rows: list[dict] = []
        self.documents: dict[str, dict] = {}
        self.selection_documents: dict[str, dict] = {}
        self.maps: dict[str, dict] = {}
        self.media: dict[str, dict] = {}
        self.jobs: dict[str, PreparedJob] = {}
        self.historical: dict[str, dict] = {}
        # Media-only imports do not need a Blender executable or the native render claim.
        self.config = asdict(Rendering(**services.settings.rendering))
        self.config["claim_path"] = str(managed(self.root, ".proscenium/jobs/import-encode.lock"))

    def read_verified(self, entry: dict) -> bytes:
        validate("RelativePath", entry["path"])
        validate("Sha256", entry["sha256"])
        path = relative(self.root, entry["path"], exists=True)
        if not path.is_file():
            raise DomainError("source_unavailable", 422)
        data = path.read_bytes()
        if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise DomainError(
                "source_unavailable", 422, "Unhydrated Git LFS pointer", path=entry["path"]
            )
        if sha(data) != entry["sha256"]:
            raise DomainError("hash_mismatch", 422, path=entry["path"])
        return data

    def source_origin(self, commit: str, entry: dict) -> dict:
        return dict(kind="git", repository_id=self.repository_id, commit=commit, **entry)

    def checkpoint(self, files: dict[str, str]) -> str:
        """Use the native isolated-index writer, pinning a private ref without moving HEAD."""
        key = identity("import-checkpoint", self.operation)
        reference = f"refs/proscenium/checkpoints/{key}"
        current = (
            git(self.root, "rev-parse", "--verify", reference, required=False).decode().strip()
        )
        head = git(self.root, "rev-parse", "--verify", "HEAD", required=False).decode().strip()
        rows = [dict(path=path, sha256=digest) for path, digest in sorted(files.items())]
        request = dict(message="Proscenium owner import", operation_id=key)
        intent: dict[str, Any] = dict(
            parent=head or None,
            files=rows,
            fingerprint=fingerprint(self.manifest),
            author="Proscenium Owner Import <import@proscenium.invalid> 946684800 +0200",
            modes={},
        )
        if not current:
            for entry in rows:
                self.read_verified(entry)
                path = relative(self.root, entry["path"], exists=True)
                if git(
                    self.root, "check-ignore", "--no-index", "--", entry["path"], required=False
                ):
                    raise DomainError("scope_denied", 403, "Cannot checkpoint ignored source")
                intent["modes"][entry["path"]] = (
                    "100755" if path.stat().st_mode & 0o111 else "100644"
                )
            commit, tree = self.sources.checkpoints.prepare(self.root, request, intent)
            intent.update(candidate=commit, tree=tree)
            self.sources.checkpoints.verify(self.project_id, request, intent, commit)
            commands = [f"create {reference} {commit}"]
            # A fresh repository needs a real HEAD for recovery. In an existing
            # repository the checkpoint is deliberately not a branch/index edit.
            if not head:
                branch = git(self.root, "symbolic-ref", "HEAD").decode().strip()
                commands.append(f"create {branch} {commit}")
            git(self.root, "update-ref", "--stdin", data=("\n".join(commands) + "\n").encode())
            if not head and not (self.root / ".git/index").exists():
                git(self.root, "read-tree", commit)
            return commit
        message = git(self.root, "show", "-s", "--format=%B", current).decode().splitlines()
        if f"Proscenium-Fingerprint: {intent['fingerprint']}" not in message:
            raise DomainError("idempotency_conflict", 409)
        return current

    def sources_for_items(self, items: list[dict]) -> None:
        checkpoint_files: dict[str, str] = {}
        for item in items:
            fields(
                item,
                "item_id version_id name kind source",
                "description tags provenance derived_from unity artifacts renders delivery expected_current_version_id",
            )
            source = item["source"]
            fields(source, "entry_path files required_runtime", "source_commit dependencies")
            files = array(source["files"])
            names = set()
            for entry in files:
                fields(entry, "path sha256")
                path, digest = entry["path"], entry["sha256"]
                validate("RelativePath", path)
                validate("Sha256", digest)
                relative(self.root, path)
                if path in names or path.split("/")[0] == ".receipt.json":
                    raise DomainError("dependency_collision", 409, path=path)
                names.add(path)
                if "source_commit" not in source:
                    if checkpoint_files.setdefault(path, digest) != digest:
                        raise DomainError("hash_mismatch", 422, path=path)
            if source["entry_path"] not in names:
                raise DomainError("dependency_missing", 422, "Source entry must be listed in files")
        if (
            checkpoint_files
            or not git(self.root, "rev-parse", "--verify", "HEAD", required=False).strip()
        ):
            for name in ("project.json", ".gitignore", ".gitattributes"):
                digest = sha(relative(self.root, name, exists=True).read_bytes())
                if checkpoint_files.setdefault(name, digest) != digest:
                    raise DomainError("hash_mismatch", 422, path=name)
            checkpoint = self.checkpoint(checkpoint_files)
        else:
            checkpoint = None

        pinned: set[str] = set()
        verified: set[tuple[str, str, str]] = set()
        for item in items:
            source = item["source"]
            commit = source.get("source_commit", checkpoint)
            validate("Commit", commit)
            if commit not in pinned:
                # Reject a tag/tree rather than silently peeling it into another SHA.
                if git(self.root, "cat-file", "-t", commit).strip() != b"commit":
                    raise DomainError("source_unavailable", 422)
                pinned.add(commit)
            files = []
            kind = item["kind"]
            for entry in sorted(source["files"], key=lambda row: row["path"]):
                path = entry["path"]
                origin = self.source_origin(commit, entry)
                is_document = path == source["entry_path"] and kind in {"storyboard", "sequence"}
                key = (commit, path, entry["sha256"])
                if key not in verified or is_document:
                    data = self.sources.git_bytes(self.project_id, origin)
                    verified.add(key)
                    if is_document:
                        document = json.loads(data)
                        validate(
                            "AuthoredShotPlan" if kind == "storyboard" else "SequenceMap", document
                        )
                        if kind == "sequence":
                            self.validate_sequence_bounds(document)
                        self.documents[item["version_id"]] = document
                files.append(dict(destination=path, origin=origin))
            closure = dict(
                entry_path=source["entry_path"],
                files=files,
                dependencies=source.get("dependencies", []),
                required_runtime=source["required_runtime"],
            )
            closure["digest"] = closure_digest(closure)
            row = {
                key: item[key]
                for key in ("kind", "name", "derived_from", "unity", *METADATA)
                if key in item
            }
            row.update(
                **ref(item),
                source=closure,
                source_commit=commit,
                expected_current_version_id=item.get("expected_current_version_id"),
                artifact_ids=[],
                preview_requests=[],
            )
            validate("PublishedItem", row)
            if (
                ("unity" in row) != (kind == "unity_result")
                or ("renders" in item and kind != "scene")
                or ("delivery" in item and kind != "sequence")
            ):
                raise DomainError(
                    "invalid_request", 422, "Media declaration does not match item kind"
                )
            self.rows.append(row)
        # Retain exact externally committed source even if the owner's branch later moves.
        for commit in sorted(pinned - {checkpoint}):
            git(self.root, "update-ref", f"refs/proscenium/import-sources/{commit}", commit)

    def promote(self, artifact_id: str, data: bytes, **description: Any) -> dict:
        if artifact_id in self.media:
            raise DomainError("idempotency_conflict", 409, artifact_id=artifact_id)
        result = self.artifacts.promote(self.project_id, artifact_id, data, **description)
        self.media[artifact_id] = result
        return result

    def item_artifact(self, entry: dict, row: dict | None) -> str:
        fields(
            entry,
            "artifact_id path sha256 media_type kind purpose",
            "external_id provenance dimensions fps frame_count",
        )
        kind, media_type = entry["kind"], entry["media_type"]
        if row:
            allowed = {"asset": {"reference", "concept"}, "storyboard": {"keyframe"}}
            if entry["kind"] != "image" or entry["purpose"] not in allowed.get(row["kind"], set()):
                raise DomainError("invalid_request", 422, "Invalid catalogue image purpose")
        elif not isinstance(kind, str) or kind not in ITEM_MEDIA_TYPES:
            raise DomainError("invalid_request", 422, "Unsupported retained artefact kind")
        if not isinstance(media_type, str) or media_type not in ITEM_MEDIA_TYPES[kind]:
            raise DomainError("unsupported_media_type", 422, f"Unsupported media type for {kind}")
        if "provenance" in entry:
            validate("Provenance", entry["provenance"])
        if "external_id" in entry and (
            not isinstance(entry["external_id"], str) or not entry["external_id"]
        ):
            raise DomainError(
                "invalid_request", 422, "External evidence ID must be a non-empty string"
            )
        data = self.read_verified(entry)
        metadata = {"source_version_id": row["version_id"]} if row else {}
        expected = {}
        for key, schema in (("dimensions", "Dimensions"), ("fps", "Fps")):
            if key in entry:
                expected[key] = validate(schema, entry[key])
        if "frame_count" in entry:
            if type(entry["frame_count"]) is not int or entry["frame_count"] <= 0:
                raise DomainError("invalid_request", 422, "Expected a positive frame count")
            expected["frame_count"] = entry["frame_count"]
        if kind == "video":
            with tempfile.TemporaryDirectory(
                prefix="import-retained-video-", dir=self.store.path.parent
            ) as tmp:
                path = Path(tmp) / "retained.mp4"
                atomic_bytes(path, data)
                metadata.update(inspect_video(path, self.config, expected=expected))
        elif expected:
            raise DomainError("invalid_request", 422, "Declared video metadata requires a video")
        elif kind == "document":
            try:
                text = data.decode("utf-8", errors="strict")
                if media_type == "application/json":
                    json.loads(text, parse_constant=reject_non_json)
                elif b"\x00" in data:
                    raise ValueError("NUL in text document")
            except ValueError as exc:
                description = (
                    "UTF-8 JSON"
                    if media_type == "application/json"
                    else "UTF-8 text without NUL bytes"
                )
                raise DomainError(
                    "source_unavailable", 422, f"Retained document must be valid {description}"
                ) from exc
        self.promote(
            entry["artifact_id"],
            data,
            kind=kind,
            purpose=entry["purpose"],
            media_type=media_type,
            metadata=metadata,
        )
        if not row:
            self.historical[entry["artifact_id"]] = {
                "owner_operation_id": self.operation,
                "actor": self.actor,
                **{
                    key: entry[key]
                    for key in ("external_id", "provenance", "path", "sha256")
                    if key in entry
                },
            }
        return entry["artifact_id"]

    def add_job(
        self,
        declaration: dict,
        row: dict,
        kind: str,
        digest: str,
        count: int,
        artifacts: list[str],
        spec: dict | None = None,
    ) -> dict:
        job_id, external = declaration["job_id"], declaration["external_job_id"]
        if not isinstance(external, str) or not external.strip():
            raise DomainError("invalid_request", 422, "Actual external job identity is required")
        if job_id in self.jobs:
            raise DomainError("idempotency_conflict", 409, job_id=job_id)
        job = dict(
            schema_version="1.0",
            job_id=job_id,
            project_id=self.project_id,
            work_id=None,
            owner_operation_id=self.operation,
            target=dict(
                target_id=row["item_id"],
                version=ref(row),
                shot_id=spec["shot_id"] if spec else None,
            ),
            attempt_id=identity("import-attempt", self.project_id, job_id),
            attempt_number=1,
            kind=kind,
            state="succeeded",
            state_revision=0,
            availability="complete",
            input_digest=digest,
            external_job_id=external,
            provider_id=None,
            owner_epoch=self.services.ownership.epoch,
            completed_frames=count,
            expected_frames=count,
            artifact_ids=artifacts,
            render_spec=spec,
        )
        validate("JobRecord", job)
        return job

    def verified_video(self, path: Path, fps: dict, count: int, dimensions: dict) -> bytes:
        verify_video(path, fps, count, dimensions, self.config)
        return path.read_bytes()

    def render(self, row: dict, declaration: dict) -> None:
        fields(declaration, "job_id external_job_id spec frames", "segment")
        spec = dict(declaration["spec"])
        spec.setdefault("source_closure_digest", row["source"]["digest"])
        validate("RenderSpec", spec)
        frames, dimensions = spec["frames"], spec["dimensions"]
        start, end = frames["start"], frames["end"]
        count = validation.interval(frames)
        validation.rational(spec["fps"])
        if spec["source_closure_digest"] != row["source"]["digest"]:
            raise DomainError("hash_mismatch", 422)
        if spec["purpose"] == "proxy" or spec["shot_id"] is None:
            raise DomainError(
                "invalid_request", 422, "Imported renders need a shot and raster purpose"
            )
        if (
            count > self.config["max_frames"]
            or dimensions["width"] * dimensions["height"] > self.config["max_pixels"]
        ):
            raise DomainError("invalid_request", 422, "Imported render exceeds host bounds")
        declared: dict[int, dict] = {}
        for entry in array(declaration["frames"]):
            fields(entry, "source_frame path sha256")
            frame = entry["source_frame"]
            if type(frame) is not int or frame in declared:
                raise DomainError("invalid_frame_mapping", 422)
            declared[frame] = entry
        if set(declared) != set(range(start, end + 1)):
            raise DomainError(
                "invalid_frame_mapping", 422, "Every source frame must be declared once"
            )
        external_id = declaration["external_job_id"]
        inputs = dict(
            source=row["source"],
            version=ref(row),
            render_spec=spec,
            external_job_id=external_id,
            verified_frames=[
                {"source_frame": f, "sha256": declared[f]["sha256"]} for f in range(start, end + 1)
            ],
        )
        job = self.add_job(declaration, row, "render", fingerprint(inputs), count, [], spec)
        job_id = job["job_id"]
        metadata = {"source_version_id": row["version_id"], "dimensions": dimensions}
        common = dict(
            schema_version="1.0",
            project_id=self.project_id,
            job_id=job_id,
            attempt_id=job["attempt_id"],
            version_id=row["version_id"],
            shot_id=spec["shot_id"],
            input_digest=job["input_digest"],
        )
        receipts = []
        segment_entry = declaration.get("segment")
        with tempfile.TemporaryDirectory(
            prefix="import-render-", dir=self.store.path.parent
        ) as tmp:
            directory = Path(tmp)
            for frame in range(start, end + 1):
                entry = declared[frame]
                data = self.read_verified(entry)
                try:
                    with Image.open(io.BytesIO(data)) as image:
                        if (
                            image.format != "PNG"
                            or image.mode != "RGB"
                            or image.size != (dimensions["width"], dimensions["height"])
                        ):
                            raise DomainError(
                                "media_unavailable", 422, "Expected exact RGB PNG frame"
                            )
                        image.verify()
                except (UnidentifiedImageError, OSError, SyntaxError) as exc:
                    raise DomainError("media_unavailable", 422, "Invalid PNG frame") from exc
                artifact_id = identity("import-frame", job_id, frame)
                artifact = self.promote(
                    artifact_id,
                    data,
                    kind="image",
                    purpose=spec["purpose"],
                    media_type="image/png",
                    metadata=metadata,
                )
                receipts.append(
                    dict(
                        **common,
                        receipt_id=identity("import-frame-receipt", job_id, frame),
                        artifact_id=artifact_id,
                        output_path=entry["path"],
                        sha256=artifact["sha256"],
                        dimensions=dimensions,
                        source_frame=frame,
                    )
                )
                if segment_entry is None:
                    atomic_bytes(directory / "frames" / f"{frame:08d}.png", data)
            video = directory / "segment.mp4"
            if segment_entry is None:
                encode(directory, start, count, spec["fps"], self.config, video)
                output_path = "segment.mp4"
            else:
                fields(segment_entry, "path sha256")
                atomic_bytes(video, self.read_verified(segment_entry))
                output_path = segment_entry["path"]
            data = self.verified_video(video, spec["fps"], count, dimensions)
        artifact_id = identity("import-segment", job_id)
        self.promote(
            artifact_id,
            data,
            kind="video",
            purpose=spec["purpose"],
            media_type="video/mp4",
            metadata={**metadata, "fps": spec["fps"], "frame_count": count},
        )
        segment = dict(
            **common,
            segment_id=identity("import-segment-receipt", job_id),
            source_frames=frames,
            fps=spec["fps"],
            frame_count=count,
            artifact_id=artifact_id,
            output_path=output_path,
            sha256=sha(data),
        )
        job["artifact_ids"] = [r["artifact_id"] for r in receipts] + [artifact_id]
        self.jobs[job_id] = PreparedJob(job, inputs, receipts, segment)
        row["preview_requests"].append(spec)

    def validate_sequence_bounds(self, sequence: dict) -> None:
        self.require_silent_sequence(sequence)
        count = sequence["frame_count"]
        ceiling = self.config["max_frames"]
        if not 1 <= count <= ceiling:
            raise DomainError("invalid_frame_mapping", 422, "Sequence exceeds the host frame limit")
        cursor = 1
        for entry in sequence["entries"]:
            source_count = validation.interval(entry["source_frames"])
            dest = entry["sequence_frames"]
            dest_count = validation.interval(dest)
            if (
                source_count > ceiling
                or dest["start"] != cursor
                or dest["end"] > count
                or (entry["transform"] in {"identity", "trim"} and source_count != dest_count)
            ):
                raise DomainError(
                    "invalid_frame_mapping",
                    422,
                    "Sequence ranges do not partition its bounded frame count",
                )
            if any(
                validation.interval(source["source_frames"]) > ceiling
                for source in entry.get("contributors", [])
            ):
                raise DomainError(
                    "invalid_frame_mapping",
                    422,
                    "Sequence contributor exceeds the host frame limit",
                )
            cursor = dest["end"] + 1
        if cursor != count + 1:
            raise DomainError(
                "invalid_frame_mapping",
                422,
                "Sequence ranges do not cover the declared frame count",
            )

    def frame_map(self, sequence: dict) -> dict:
        for entry in sequence["entries"]:
            key = entry["mapping_artifact_id"]
            if key is None or key in self.maps:
                continue
            with self.store.transaction(write=False) as tx:
                artifact = self.media.get(key) or tx.require("artifacts", key)
            if artifact["kind"] != "frame_map" or artifact["state"] != "ready":
                raise DomainError("invalid_frame_mapping", 422)
            data = self.artifacts.path(self.project_id, artifact["sha256"]).read_bytes()
            if sha(data) != artifact["sha256"]:
                raise DomainError("hash_mismatch", 422)
            mapping = validate("FrameMap", json.loads(data))
            if (
                mapping["frame_count"] != sequence["frame_count"]
                or len(mapping["frames"]) != sequence["frame_count"]
            ):
                raise DomainError(
                    "invalid_frame_mapping",
                    422,
                    "Explicit map does not match the bounded frame count",
                )
            self.maps[key] = mapping
        maps = [
            self.maps[e["mapping_artifact_id"]]
            for e in sequence["entries"]
            if e["mapping_artifact_id"]
        ]
        full = expand(sequence, maps[0] if maps else None)
        if any(value != full for value in maps):
            raise DomainError("invalid_frame_mapping", 422, "Frame maps do not agree")
        return full

    @staticmethod
    def require_silent_sequence(sequence: dict) -> None:
        if not sequence["silent"]:
            raise DomainError(
                "invalid_request", 422, "Offline import currently supports silent sequences only"
            )

    def delivery(self, row: dict, declaration: dict, sequence: dict, full: dict) -> None:
        self.require_silent_sequence(sequence)
        fields(declaration, "job_id external_job_id video dimensions")
        fields(declaration["video"], "path sha256")
        dimensions = declaration["dimensions"]
        validate("Dimensions", dimensions)
        validate("FrameMap", full)
        receipts: dict[tuple[str, str, str | None, int], dict] = {}
        used: dict[str, dict] = {}
        with self.store.transaction(write=False) as tx:
            for job_id in {c["job_id"] for frame in full["frames"] for c in frame["contributors"]}:
                staged = self.jobs.get(job_id)
                if staged is None:
                    job = tx.require("jobs", job_id)
                    frames = PreviewService.records(tx, job, "frames")
                else:
                    frames = staged.receipts
                for source_receipt in frames:
                    key = (
                        job_id,
                        source_receipt["version_id"],
                        source_receipt["shot_id"],
                        source_receipt["source_frame"],
                    )
                    receipts[key] = source_receipt
            for frame in full["frames"]:
                for c in frame["contributors"]:
                    receipt = receipts.get(
                        (c["job_id"], c["version_id"], c["shot_id"], c["source_frame"])
                    )
                    if receipt is None or receipt["dimensions"] != dimensions:
                        raise DomainError(
                            "invalid_frame_mapping", 422, "Missing or incompatible frame receipt"
                        )
                    artifact_id = receipt["artifact_id"]
                    artifact = self.media.get(artifact_id) or tx.require("artifacts", artifact_id)
                    if artifact["state"] != "ready" or artifact["sha256"] != receipt["sha256"]:
                        raise DomainError("media_unavailable", 422)
                    if used.setdefault(artifact_id, receipt) != receipt:
                        raise DomainError("frame_conflict", 409)
        for receipt in used.values():
            if receipt["artifact_id"] not in self.media:
                path = self.artifacts.path(self.project_id, receipt["sha256"])
                if sha(path.read_bytes()) != receipt["sha256"]:
                    raise DomainError("hash_mismatch", 422)
        snapshot = dict(
            sequence=technical(sequence),
            frame_map=full,
            input_frames=[used[key] for key in sorted(used)],
        )
        digest = fingerprint(snapshot)
        count, fps = full["frame_count"], full["fps"]
        with tempfile.TemporaryDirectory(
            prefix="import-delivery-", dir=self.store.path.parent
        ) as tmp:
            path = Path(tmp) / "delivery.mp4"
            atomic_bytes(path, self.read_verified(declaration["video"]))
            data = self.verified_video(path, fps, count, dimensions)
        mapping = canonical(full)
        job = self.add_job(declaration, row, "encode", digest, count, [])
        job_id = job["job_id"]
        metadata = {"source_version_id": row["version_id"]}
        # Verification binds the supplied delivery to technical expectations and the
        # owner's declared mapping. It does not claim that we ran the external encoder.
        manifest = canonical(
            dict(
                input_digest=digest,
                sequence=snapshot["sequence"],
                frame_map_sha256=sha(mapping),
                video_sha256=sha(data),
                input_frames=snapshot["input_frames"],
                external_job_id=job["external_job_id"],
                imported=True,
            )
        )
        output = [
            self.promote(
                identity("import-delivery", job_id),
                data,
                kind="video",
                purpose="final",
                media_type="video/mp4",
                derived_from=[
                    dict(
                        kind="encode",
                        input_artifact_ids=sorted(used),
                        parameters={
                            "frame_map_sha256": sha(mapping),
                            "blend_space": "linear_rgb",
                            "imported": True,
                        },
                    )
                ],
                metadata={**metadata, "dimensions": dimensions, "fps": fps, "frame_count": count},
            ),
            self.promote(
                identity("import-frame-map", job_id),
                mapping,
                kind="frame_map",
                purpose="metadata",
                media_type="application/json",
                metadata=metadata,
            ),
            self.promote(
                identity("import-assembly-manifest", job_id),
                manifest,
                kind="document",
                purpose="metadata",
                media_type="application/json",
                metadata=metadata,
            ),
        ]
        job["artifact_ids"] = [a["artifact_id"] for a in output]
        inputs = dict(
            import_id=self.operation,
            source=row["source"],
            input_artifact_ids=sorted(used),
            input_digest=digest,
        )
        self.jobs[job_id] = PreparedJob(job, inputs, assembly=snapshot)

    def selection(self, tx: Transaction, entry: dict) -> None:
        fields(entry, "selection_id slot choice")
        record = dict(
            schema_version="1.0",
            project_id=self.project_id,
            **entry,
            state="selected",
            state_revision=1,
            actor=self.actor,
        )
        validate("SelectionRecord", record)
        slot = record["slot"]
        key = fingerprint(slot)
        identity = record["selection_id"]
        if record["choice"] is None or tx.get("review.selection_slots", key, required=False):
            raise DomainError("source_conflict", 409, "Selection must name a choice for a new slot")
        target = slot.get("storyboard_version") or slot.get("sequence_version")
        document = self.selection_documents.get(target["version_id"]) if target else None
        References(self.services).selection(tx, slot, record["choice"], document)
        tx.put("selections", identity, {"reserved": True})
        tx.put("selections", identity, record, expected_revision=0, schema="SelectionRecord")
        tx.put("review.selection_slots", key, {"selection_id": identity, "revision": 1})
        tx.put("review.selection_history", identity + ":1", {"selection": record})
        tx.emit(
            "selection.changed",
            dict(
                schema_version="1.0",
                operation_id=self.operation,
                resource_id=identity,
                state="selected",
                state_revision=1,
                replayed=False,
            ),
            actor=self.actor,
        )

    def prepare_selections(self) -> None:
        for entry in array(self.manifest.get("selections", [])):
            fields(entry, "selection_id slot choice")
            slot = validate("SelectionSlot", entry["slot"])
            target = slot.get("storyboard_version") or slot.get("sequence_version")
            if target is None:
                continue
            version_id = target["version_id"]
            kind = "storyboard" if slot["kind"] == "storyboard_frame" else "sequence"
            schema = "AuthoredShotPlan" if kind == "storyboard" else "SequenceMap"
            if version_id in self.documents:
                self.selection_documents[version_id] = validate(schema, self.documents[version_id])
                continue
            with self.store.transaction(write=False) as tx:
                version = References.version(tx, target)
                if version["kind"] != kind:
                    raise DomainError("source_conflict", 409)
            source = version["source"]
            origin = next(
                row["origin"]
                for row in source["files"]
                if row["destination"] == source["entry_path"]
            )
            if origin["kind"] == "git":
                data = self.sources.git_bytes(self.project_id, origin)
            else:
                with self.store.transaction(write=False) as tx:
                    retained = tx.require("retained_sources", origin["artifact_id"])
                if retained["sha256"] != origin["sha256"]:
                    raise DomainError("source_conflict", 409)
                data = managed(
                    self.root, f".proscenium/retained/{origin['sha256']}/content", exists=True
                ).read_bytes()
                if sha(data) != origin["sha256"]:
                    raise DomainError("hash_mismatch", 422)
            self.selection_documents[version_id] = validate(schema, json.loads(data))

    def commit(self, tx: Transaction, submission: dict) -> dict:
        for artifact in self.media.values():
            self.artifacts.put(tx, artifact)
        for artifact_id, evidence in self.historical.items():
            tx.put("imported_artifact_evidence", artifact_id, evidence)
        self.catalogue.validate_batch(tx, submission)
        jobs_by_version: dict[str, list[PreparedJob]] = {}
        for prepared_job in self.jobs.values():
            key = prepared_job.record["target"]["version"]["version_id"]
            jobs_by_version.setdefault(key, []).append(prepared_job)
        for row in self.rows:
            jobs = jobs_by_version.get(row["version_id"], [])
            previews = [job.record["job_id"] for job in jobs if job.record["kind"] == "render"]
            delivery = [
                identity for job in jobs if job.assembly for identity in job.record["artifact_ids"]
            ]
            version = {
                key: value for key, value in row.items() if key != "expected_current_version_id"
            }
            version.update(
                schema_version="1.0",
                project_id=self.project_id,
                publication_id=self.publication,
                preview_job_ids=previews,
                created_at=now(),
                actor=self.actor,
            )
            tx.put("versions", row["version_id"], version, schema="VersionRecord")
            links = {}
            if previews:
                links["ordered_preview_job_ids"] = previews
            if delivery:
                links["artifact_ids"] = delivery
            if links:
                tx.put("version_links", row["version_id"], links)
            existing = tx.get("items", row["item_id"], required=False)
            revision = tx.revision("items", row["item_id"]) if existing else None
            item = dict(
                schema_version="1.0",
                project_id=self.project_id,
                item_id=row["item_id"],
                kind=row["kind"],
                name=row["name"],
                current_version_id=row["version_id"],
                selected_version_id=None,
                state_revision=0 if revision is None else revision + 1,
                **{key: row[key] for key in METADATA if key in row},
            )
            tx.put("items", row["item_id"], item, expected_revision=revision, schema="ItemRecord")
        for prepared in self.jobs.values():
            job = prepared.record
            job_id = job["job_id"]
            tx.put("jobs", job_id, job, schema="JobRecord")
            self.services.jobs.save_attempt(tx, job, self.operation)
            self.services.jobs.freeze_inputs(tx, job_id, prepared.inputs)
            if spec := job["render_spec"]:
                tx.put(
                    "preview_intents",
                    spec["intent_id"],
                    dict(version=job["target"]["version"], render_spec=spec, job_id=job_id),
                )
                tx.put("preview_progress", job_id, {"revision": len(prepared.receipts) + 1})
                for receipt in prepared.receipts:
                    tx.put("frames", receipt["receipt_id"], receipt, schema="FrameReceipt")
                assert prepared.segment is not None
                tx.put(
                    "segments",
                    prepared.segment["segment_id"],
                    prepared.segment,
                    schema="PreviewSegment",
                )
            if prepared.assembly is not None:
                tx.put("preview_assemblies", job_id, prepared.assembly)
                tx.put(
                    "preview_assembly_origins",
                    job_id,
                    dict(work_id=None, invocation_id=None, owner_operation_id=self.operation),
                )
        self.catalogue.validate_documents(tx, submission, self.documents, self.maps)
        for row in self.rows:
            version = tx.require("versions", row["version_id"])
            apply_version_projection(
                tx, project_version_relationships(version, self.documents.get(row["version_id"]))
            )
        for prepared in self.jobs.values():
            if snapshot := prepared.assembly:
                local = {
                    **snapshot["sequence"],
                    "entries": [
                        {**entry, "mapping_artifact_id": "complete-map"}
                        for entry in snapshot["sequence"]["entries"]
                    ],
                }
                validation.sequence_map(
                    tx, local, local["sequence_version_id"], {"complete-map": snapshot["frame_map"]}
                )
        for prepared in self.jobs.values():
            for receipt in prepared.receipts:
                tx.emit("frame.available", receipt, actor=self.actor)
            if prepared.segment:
                tx.emit("segment.available", prepared.segment, actor=self.actor)
            self.services.jobs.emit(tx, prepared.record, self.caller)
        for selection in array(self.manifest.get("selections", [])):
            self.selection(tx, selection)
        receipt = dict(
            schema_version="1.0",
            publication_id=self.publication,
            project_id=self.project_id,
            versions=[ref(row) for row in self.rows],
            job_ids=list(self.jobs),
            event_sequence=tx.watermark() + 1,
            replayed=False,
        )
        if self.rows:
            tx.put(
                "publications",
                self.publication,
                dict(submission=submission, receipt=receipt, owner_operation_id=self.operation),
            )
            tx.emit("publication.created", receipt, actor=self.actor)
        return dict(
            project_id=self.project_id,
            import_id=self.operation,
            versions=receipt["versions"],
            job_ids=receipt["job_ids"],
            artifact_ids=sorted(self.media),
            event_sequence=tx.watermark(),
        )

    def run(self) -> dict:
        with self.store.transaction(write=False) as tx:
            previous = replay(tx, SCOPE, self.operation, self.manifest)
        if previous is not None:
            return {**previous, "replayed": True}
        items = array(self.manifest.get("items", []))
        historical = array(self.manifest.get("retained_artifacts", []))
        frame_maps = array(self.manifest.get("frame_maps", []))
        if not (items or historical or frame_maps or self.manifest.get("selections")):
            raise DomainError("invalid_request", 422, "Import has no records")
        self.sources_for_items(items)
        for artifact in historical:
            self.item_artifact(artifact, None)
        for mapping in frame_maps:
            fields(mapping, "artifact_id path sha256")
            self.promote(
                mapping["artifact_id"],
                self.read_verified(mapping),
                kind="frame_map",
                purpose="metadata",
                media_type="application/json",
            )
        for row, item in zip(self.rows, items, strict=True):
            row["artifact_ids"] = [
                self.item_artifact(artifact, row) for artifact in array(item.get("artifacts", []))
            ]
            for render in array(item.get("renders", [])):
                self.render(row, render)
        for row, item in zip(self.rows, items, strict=True):
            if row["kind"] == "sequence":
                document = self.documents[row["version_id"]]
                full = self.frame_map(document)
                if "delivery" in item:
                    self.delivery(row, item["delivery"], document, full)
        submission = dict(
            schema_version="1.0",
            project_id=self.project_id,
            publication_id=self.publication,
            items=self.rows,
        )
        if self.rows:
            validate("PublicationSubmit", submission)
        self.prepare_selections()
        with self.store.transaction() as tx:
            result, repeated = tx.operation(
                SCOPE, self.operation, self.manifest, lambda: self.commit(tx, submission)
            )
        return {**result, "replayed": repeated}


@contextmanager
def _owned(path: Path) -> Iterator[None]:
    lock = Ownership(path)
    try:
        yield
    finally:
        lock.close()


def import_project(services: Services, caller: Caller, project_root: Path, manifest: dict) -> dict:
    """Import exclusively into one owner project without changing active project or starting work."""
    owner_only(caller)
    fields(
        manifest,
        "schema_version import_id",
        "project items retained_artifacts frame_maps selections",
    )
    if manifest["schema_version"] != "1.0":
        raise DomainError("invalid_request", 422, "Expected import format 1.0")
    validate("Id", manifest["import_id"])
    validate("Id", caller.subject_id)
    project = manifest.get("project", {})
    fields(project, "", "name project_id repository_id")
    declared = {key: project[key] for key in ("project_id", "repository_id") if key in project}
    if declared and len(declared) != 2:
        raise DomainError("invalid_request", 422, "Both project identity fields are required")
    for value in declared.values():
        validate("Id", value)
    root = project_root.expanduser().resolve(strict=True)
    projects = Projects(services)
    projects.workspace_boundary(root)
    if services.settings.data_directory.resolve().is_relative_to(root):
        raise DomainError("scope_denied", 403, "Registry must be outside the imported project")
    if (
        not root.is_dir()
        or (root / ".git").is_symlink()
        or ((root / ".git").exists() and not (root / ".git").is_dir())
    ):
        raise DomainError("unsupported_repository", 415, "Import needs a standalone worktree")
    metadata = root / ".git"
    if (
        any(
            (metadata / name).exists()
            for name in ("shallow", "commondir", "objects/info/alternates")
        )
        or any(path.is_symlink() for path in metadata.rglob("*"))
        or any((metadata / "objects/pack").glob("*.promisor"))
    ):
        raise DomainError(
            "unsupported_repository", 415, "Import requires complete local Git storage"
        )
    top = git(root, "rev-parse", "--show-toplevel", required=False).decode().strip()
    if top and top != str(root):
        raise DomainError("unsupported_repository", 415, "Import needs the Git worktree root")
    if services.runtime is not None or "lifecycle" in services.extensions:
        raise DomainError("project_busy", 409, "Shut down the runtime before importing")
    with services.activation_lock:
        # Open may initialise project state. Acquire project ownership before changing it,
        # including when another registry has independently opened the same workspace.
        lock_path = root / ".proscenium/owner.lock"
        if lock_path.parent.is_symlink() or lock_path.is_symlink():
            raise DomainError("scope_denied", 403)
        owned = any(
            Path(owner.file.name) == lock_path for owner in services.project_ownership.values()
        )
        with nullcontext() if owned else _owned(lock_path):
            if declared and (root / "project.json").exists():
                existing = Projects.config(root)
                if any(existing[key] != value for key, value in declared.items()):
                    raise DomainError(
                        "source_conflict", 409, "Manifest does not match project.json"
                    )
            name = project.get("name", root.name)
            opened, _ = projects.open(
                caller,
                dict(
                    schema_version="1.0",
                    path=str(root),
                    create=True,
                    name=name,
                    operation_id=identity("import-open", str(root), name, declared),
                ),
                identity=declared or None,
                install_lfs_hooks=False,
            )
            current = services.registry.get(opened["project_id"])
            if current["path"] != str(root):
                raise DomainError("project_location_conflict", 409)
            services.registry.assert_settled(opened["project_id"])
            return Import(services, caller, current, manifest).run()
