"""Pinned sequence assembly. Callable trusted tool, no additional HTTP schema."""

import io
import time
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue.validation import (
    EXPLICIT_FRAME_MAP_REQUIRED,
    INVALID_AUDIO_ARTEFACT,
    rational,
    sequence_map,
)
from proscenium.contracts import canonical, validate
from proscenium.db.paths import read_bounded_regular_file
from proscenium.providers.minimax_h3 import MAX_VIDEO_BYTES, verify_video_bytes
from proscenium.sources.common import active, admitted, atomic_bytes, fingerprint

from .journal import atomic, sha
from .media import assemble_clips, clip_canvas, encode, verify_video

AUDIO_EXTENSIONS = {
    "audio/flac": ".flac",
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
}


def audio_path(root: Path, row: dict) -> Path:
    return root / "assembly-inputs" / (row["sha256"] + AUDIO_EXTENSIONS[row["media_type"]])


def clip_path(root: Path, row: dict) -> Path:
    return root / "assembly-inputs" / (row["sha256"] + ".mp4")


def is_clip_sequence(sequence: dict) -> bool:
    return "clip_artifact_id" in sequence["entries"][0]


def clip_snapshot(artifacts: Any, project_id: str, sequence: dict) -> dict:
    clips = []
    for identity in sorted({entry["clip_artifact_id"] for entry in sequence["entries"]}):
        record = artifacts.get(project_id, identity)
        if record["bytes"] > MAX_VIDEO_BYTES:
            raise DomainError("upload_too_large", 413)
        clips.append(
            {
                k: record[k]
                for k in (
                    "artifact_id",
                    "sha256",
                    "bytes",
                    "media_type",
                    "dimensions",
                    "frame_count",
                    "fps",
                    "duration_seconds",
                    "sample_rate_hz",
                    "channels",
                )
                if k in record
            }
        )
    return with_audio(
        {"sequence": sequence, "frame_map": None, "input_frames": [], "input_clips": clips},
        artifacts,
        project_id,
    )


def input_audio(artifacts: Any, project_id: str, sequence: dict) -> list[dict]:
    result = []
    for identity in sorted({track["artifact_id"] for track in sequence.get("audio_tracks", [])}):
        record = artifacts.get(project_id, identity)
        if (
            record["kind"] != "audio"
            or record["state"] != "ready"
            or record["project_id"] != project_id
            or record["media_type"] not in AUDIO_EXTENSIONS
            or not record.get("duration_seconds")
        ):
            raise DomainError("media_unavailable", 422, INVALID_AUDIO_ARTEFACT, safe_for_model=True)
        artifacts.ready_path(project_id, identity)
        result.append(
            {
                k: record[k]
                for k in ("artifact_id", "sha256", "bytes", "media_type", "duration_seconds")
            }
        )
    return result


def with_audio(snapshot: dict, artifacts: Any, project_id: str) -> dict:
    if not snapshot["sequence"]["silent"]:
        snapshot["input_audio"] = input_audio(artifacts, project_id, snapshot["sequence"])
    return snapshot


def input_ids(snapshot: dict) -> list[str]:
    return sorted(
        {
            row["artifact_id"]
            for field in ("input_frames", "input_audio", "input_clips")
            for row in snapshot.get(field, [])
        }
    )


def output_dimensions(snapshot: dict) -> dict:
    if "input_clips" not in snapshot:
        return snapshot["input_frames"][0]["dimensions"]
    first = snapshot["sequence"]["entries"][0]["clip_artifact_id"]
    return clip_canvas(
        next(row["dimensions"] for row in snapshot["input_clips"] if row["artifact_id"] == first)
    )


def technical(value: dict) -> dict:
    result = deepcopy(value)
    for entry in result["entries"]:
        entry.pop("choice", None)
    return result


def expand(sequence: dict, frame_map: dict | None) -> dict:
    if frame_map is not None:
        validate("FrameMap", frame_map)
        return deepcopy(frame_map)
    rows = []
    for entry in sequence["entries"]:
        if entry["transform"] not in {"identity", "trim"}:
            raise DomainError(
                "invalid_frame_mapping", 422, EXPLICIT_FRAME_MAP_REQUIRED, safe_for_model=True
            )
        for destination in range(
            entry["sequence_frames"]["start"], entry["sequence_frames"]["end"] + 1
        ):
            rows.append(
                {
                    "sequence_frame": destination,
                    "contributors": [
                        {
                            "shot_id": entry["shot_id"],
                            "version_id": entry["version_id"],
                            "job_id": entry["job_id"],
                            "source_frame": entry["source_frames"]["start"]
                            + destination
                            - entry["sequence_frames"]["start"],
                            "weight": {"numerator": 1, "denominator": 1},
                        }
                    ],
                }
            )
    result = dict(
        schema_version="1.0",
        project_id=sequence["project_id"],
        sequence_version_id=sequence["sequence_version_id"],
        fps=sequence["fps"],
        frame_count=sequence["frame_count"],
        blend_space="linear_rgb",
        frames=rows,
    )
    validate("FrameMap", result)
    return result


def linear_blend(images: list[Image.Image], weights: list[Fraction]) -> Image.Image:
    if sum(weights, Fraction()) != 1 or any(w < 0 for w in weights):
        raise DomainError("invalid_frame_mapping", 422)
    if (
        len(images) != len(weights)
        or not images
        or any(image.size != images[0].size for image in images)
    ):
        raise DomainError("invalid_frame_mapping", 422)
    if len(images) == 1:
        return images[0].convert("RGB")
    lut = [
        v / 255 / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4
        for v in range(256)
    ]
    result = bytearray(images[0].width * images[0].height * 3)
    factors = [float(weight) for weight in weights]
    for index, values in enumerate(zip(*(image.convert("RGB").tobytes() for image in images))):
        value = sum(lut[v] * weight for v, weight in zip(values, factors))
        display = 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
        result[index] = max(0, min(255, round(display * 255)))
    return Image.frombytes("RGB", images[0].size, bytes(result))


def admit_assembly(
    service, caller: Caller, admission: dict, sequence: dict, frame_map: dict | None = None
) -> tuple[dict, bool]:
    """Use normative JobAdmission(kind=encode), SequenceMap and full FrameMap only.

    admission.input_digest fingerprints the snapshot, including any audio inputs.
    Input artefact IDs must equal the unique pinned frames or clips, and audio artefacts.
    """
    validate("JobAdmission", admission)
    active(service.services, caller, "admitToolJob", admission["project_id"])
    store = service.services.registry.project_store(admission["project_id"])
    with store.transaction(write=False) as tx:
        existing = tx.get("preview_assemblies", admission["job_id"], required=False)
        if existing is not None:
            origin = tx.require("preview_assembly_origins", admission["job_id"])
            inputs = tx.require("job_inputs", admission["job_id"])
            if (
                origin != {"work_id": caller.work_id, "invocation_id": caller.invocation_id}
                or inputs["admission"] != admission
                or fingerprint(existing) != admission["input_digest"]
                or existing["sequence"] != technical(sequence)
                or frame_map is not None
                and frame_map != existing["frame_map"]
            ):
                raise DomainError("idempotency_conflict", 409)
    if existing is not None:
        return service.services.jobs.admit(caller, admission)
    snapshot = prepare(service, caller, admission, sequence, frame_map)
    job, replayed = service.services.jobs.admit(caller, admission)
    store = service.services.registry.project_store(admission["project_id"])
    with store.transaction() as tx:
        old = tx.get("preview_assemblies", job["job_id"], required=False)
        if old is not None and old != snapshot:
            raise DomainError("idempotency_conflict", 409)
        if old is None:
            tx.put("preview_assemblies", job["job_id"], snapshot)
            tx.put(
                "preview_assembly_origins",
                job["job_id"],
                {"work_id": caller.work_id, "invocation_id": caller.invocation_id},
            )
        if (
            tx.connection.execute(
                "SELECT id FROM outbox WHERE id=?", (job["attempt_id"],)
            ).fetchone()
            is None
        ):
            tx.enqueue(
                job["attempt_id"],
                "preview.assemble",
                {key: job[key] for key in ("job_id", "attempt_id", "input_digest")},
            )
    return job, replayed


def require_profile(service, profile_id: str) -> None:
    configured = service.config.profile_id
    if configured not in service.services.settings.execution_profiles:
        raise DomainError(
            "capability_unavailable",
            501,
            "Execution profile is not configured",
            safe_for_model=True,
        )
    if profile_id != configured:
        raise DomainError(
            "invalid_request",
            422,
            f"Unsupported assembly execution_profile_id; omit it or use '{configured}'",
            configured_assembly_profile_id=configured,
        )


def prepare(
    service, caller: Caller, admission: dict, sequence: dict, frame_map: dict | None
) -> dict:
    validate("JobAdmission", admission)
    validate("SequenceMap", sequence)
    project_id = admission["project_id"]
    active(service.services, caller, "admitToolJob", project_id)
    if admission["kind"] != "encode" or sequence["project_id"] != project_id:
        raise DomainError("scope_denied", 403)
    require_profile(service, admission["execution_profile_id"])
    catalogue = service.services.extension("catalogue")
    published = catalogue.sequence_map(project_id, sequence["sequence_version_id"])

    # Optional owner/agent creative-choice labels do not change the assembly geometry.
    sequence = technical(sequence)
    if sequence != technical(published):
        raise DomainError(
            "source_conflict",
            409,
            "Assembly must use its pinned published SequenceMap",
            safe_for_model=True,
        )
    if is_clip_sequence(sequence):
        if frame_map is not None:
            raise DomainError(
                "invalid_frame_mapping",
                422,
                "Clip sequences do not use a FrameMap",
                safe_for_model=True,
            )
        with service.services.registry.project_store(project_id).transaction(write=False) as tx:
            admitted(service.services, tx, caller)
            check_target(tx, admission, sequence)
            sequence_map(tx, sequence, sequence["sequence_version_id"], {})
        snapshot = clip_snapshot(service.artifacts, project_id, sequence)
        check_snapshot(admission, snapshot)
        return snapshot
    maps = catalogue.frame_maps(project_id, sequence)
    if frame_map is None and maps:
        frame_map = next(iter(maps.values()))
    full = expand(sequence, frame_map)
    if any(value != full for value in maps.values()):
        raise DomainError("invalid_frame_mapping", 422)
    local = deepcopy(sequence)
    for entry in local["entries"]:
        entry["mapping_artifact_id"] = "complete-map"
    inputs = {}
    with service.services.registry.project_store(project_id).transaction(write=False) as tx:
        admitted(service.services, tx, caller)
        check_target(tx, admission, sequence)
        sequence_map(tx, local, sequence["sequence_version_id"], {"complete-map": full})
        # A long imported cut can reuse thousands of frames. Read each job's
        # receipts once rather than decoding the entire project ledger per frame.
        receipts = {}
        for job_id in {c["job_id"] for row in full["frames"] for c in row["contributors"]}:
            source_job = tx.require("jobs", job_id)
            for receipt in service.records(tx, source_job, "frames"):
                key = (job_id, receipt["version_id"], receipt["shot_id"], receipt["source_frame"])
                if key in receipts:
                    raise DomainError("frame_conflict", 409)
                receipts[key] = receipt
        for row in full["frames"]:
            for contributor in row["contributors"]:
                key = tuple(
                    contributor[k] for k in ("job_id", "version_id", "shot_id", "source_frame")
                )
                receipt = receipts.get(key)
                if receipt is None:
                    raise DomainError(
                        "media_unavailable",
                        422,
                        "A pinned contributor frame is unavailable",
                        safe_for_model=True,
                    )
                inputs[receipt["artifact_id"]] = receipt
    ordered = [inputs[key] for key in sorted(inputs)]
    snapshot = with_audio(
        {"sequence": sequence, "frame_map": full, "input_frames": ordered},
        service.artifacts,
        project_id,
    )
    check_snapshot(admission, snapshot)
    dimensions = {tuple(sorted(row["dimensions"].items())) for row in ordered}
    if len(dimensions) != 1:
        raise DomainError(
            "invalid_frame_mapping",
            422,
            "Assembly input dimensions must agree",
            safe_for_model=True,
        )
    for row in ordered:
        service.artifacts.ready_path(project_id, row["artifact_id"])
    return snapshot


def check_target(tx: Any, admission: dict, sequence: dict) -> None:
    version = tx.require("versions", sequence["sequence_version_id"])
    reference = {"item_id": version["item_id"], "version_id": version["version_id"]}
    if admission["target"]["version"] != reference:
        raise DomainError("scope_denied", 403)


def check_snapshot(admission: dict, snapshot: dict) -> None:
    if sorted(admission["input_artifact_ids"]) != input_ids(snapshot):
        raise DomainError("invalid_frame_mapping", 422)
    if admission["input_digest"] != fingerprint(snapshot):
        raise DomainError(
            "hash_mismatch",
            422,
            "Assembly digest does not bind exact contributors",
            safe_for_model=True,
        )


def render_frames(root: Path, snapshot: dict) -> None:
    frames = snapshot["input_frames"]
    by_identity = {
        (r["job_id"], r["version_id"], r["shot_id"], r["source_frame"]): r for r in frames
    }
    output_frames = root / "frames"
    output_frames.mkdir(exist_ok=True)
    for row in snapshot["frame_map"]["frames"]:
        if (root / "cancel.json").exists():
            raise RuntimeError("Assembly cancellation requested")
        images: list[Image.Image] = []
        weights: list[Fraction] = []
        for contributor in row["contributors"]:
            receipt = by_identity[
                (
                    contributor["job_id"],
                    contributor["version_id"],
                    contributor["shot_id"],
                    contributor["source_frame"],
                )
            ]
            data = (root / "assembly-inputs" / (receipt["sha256"] + ".png")).read_bytes()
            if sha(data) != receipt["sha256"]:
                raise ValueError("Pinned assembly input changed")
            source_image = Image.open(io.BytesIO(data))
            source_image.load()
            images.append(source_image)
            weights.append(rational(contributor["weight"]))
        image = linear_blend(images, weights)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        atomic_bytes(output_frames / f"{row['sequence_frame']:08d}.png", stream.getvalue())


def execute(root: Path, task: dict) -> None:
    """Runs in the detached supervisor from sealed input media."""
    snapshot, config = task["assembly"], task["config"]
    sequence = snapshot["sequence"]
    timeline = sequence if "input_clips" in snapshot else snapshot["frame_map"]
    fps, count = timeline["fps"], timeline["frame_count"]
    video = root / "delivery.mp4"
    sequenced_audio = {}
    for row in snapshot.get("input_audio", []):
        path = audio_path(root, row)
        data = path.read_bytes()
        if len(data) != row["bytes"] or sha(data) != row["sha256"]:
            raise ValueError("Pinned assembly audio input changed")
        identity = row["artifact_id"]
        sequenced_audio[identity] = {
            "path": path,
            "media_type": row["media_type"],
            "source_duration_seconds": row["duration_seconds"],
        }
    tracks = [
        {**track, **sequenced_audio[track["artifact_id"]]}
        for track in sequence.get("audio_tracks", [])
    ]
    if "input_clips" in snapshot:
        clips = {}
        deadline = time.monotonic() + config["encode_timeout"]
        for row in snapshot["input_clips"]:
            if (root / "cancel.json").exists():
                raise RuntimeError("Assembly cancellation requested")
            path = clip_path(root, row)
            data = read_bounded_regular_file(path, MAX_VIDEO_BYTES, root=root)
            if len(data) != row["bytes"] or sha(data) != row["sha256"]:
                raise DomainError("hash_mismatch", 422)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DomainError(
                    "media_unavailable",
                    422,
                    "Clip verification exceeded its deadline",
                    safe_for_model=True,
                )
            metadata = verify_video_bytes(
                data, ffmpeg=config["ffmpeg"], ffprobe=config["ffprobe"], timeout=min(remaining, 30)
            )
            if any(
                row[key] != metadata.get(key)
                for key in metadata.keys() | {"sample_rate_hz", "channels"}
                if key in row
            ):
                raise DomainError(
                    "hash_mismatch",
                    422,
                    "Pinned clip metadata does not match the MP4",
                    safe_for_model=True,
                )
            clips[row["artifact_id"]] = {**metadata, "path": path}
        command = assemble_clips(
            clips,
            sequence["entries"],
            fps,
            video,
            silent=sequence["silent"],
            preserve_clip_audio=sequence.get("preserve_clip_audio", False),
            audio_tracks=tracks,
            ffmpeg=config["ffmpeg"],
            ffprobe=config["ffprobe"],
            timeout=config["encode_timeout"],
            claim_path=config["claim_path"],
        )
    else:
        render_frames(root, snapshot)
        command = encode(root, 1, count, fps, config, video, audio_tracks=tracks)
        verify_video(
            video,
            fps,
            count,
            output_dimensions(snapshot),
            config,
            expect_audio=not sequence["silent"],
        )
        atomic_bytes(root / "frame-map.json", canonical(snapshot["frame_map"]))
    manifest = {
        "input_digest": task["input_digest"],
        "sequence": sequence,
        "video_sha256": sha(video.read_bytes()),
        "input_frames": snapshot["input_frames"],
        "encode_argv": command,
    }
    if "input_clips" in snapshot:
        manifest["input_clips"] = snapshot["input_clips"]
    else:
        manifest["frame_map_sha256"] = sha((root / "frame-map.json").read_bytes())
    if "input_audio" in snapshot:
        manifest["input_audio"] = snapshot["input_audio"]
    atomic(root / "assembly-manifest.json", manifest)
    atomic(root / "assembly-ready.json", {"complete": True})
