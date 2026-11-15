"""Pinned silent sequence assembly. Callable trusted tool, no additional HTTP schema."""

import io
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

from PIL import Image

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue.validation import rational, sequence_map
from proscenium.contracts import canonical, validate
from proscenium.sources.common import active, admitted, atomic_bytes, fingerprint

from .journal import atomic, sha
from .media import encode, verify_video


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
                "invalid_frame_mapping", 422, "Explicit hold/drop/dissolve map required"
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

    admission.input_digest fingerprints {sequence,frame_map,input_frames}; see prepare().
    Input artefact IDs must equal the unique pinned source receipt artefacts exactly.
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


def prepare(
    service, caller: Caller, admission: dict, sequence: dict, frame_map: dict | None
) -> dict:
    validate("JobAdmission", admission)
    validate("SequenceMap", sequence)
    project_id = admission["project_id"]
    active(service.services, caller, "admitToolJob", project_id)
    if admission["kind"] != "encode" or sequence["project_id"] != project_id:
        raise DomainError("scope_denied", 403)
    if admission["execution_profile_id"] != service.config.profile_id:
        raise DomainError(
            "capability_unavailable",
            501,
            "Assembly execution profile does not match the installed adapter",
        )
    catalogue = service.services.extension("catalogue")
    published = catalogue.sequence_map(project_id, sequence["sequence_version_id"])

    # Optional owner/agent creative-choice labels do not change the assembly geometry.
    sequence = technical(sequence)
    if sequence != technical(published):
        raise DomainError(
            "source_conflict", 409, "Assembly must use its pinned published SequenceMap"
        )
    maps = catalogue.frame_maps(project_id, sequence)
    if frame_map is None and maps:
        frame_map = next(iter(maps.values()))
    full = expand(sequence, frame_map)
    for entry in sequence["entries"]:
        if (
            entry["transform"] == "transition"
            and len({c["shot_id"] for c in entry["contributors"]}) < 2
        ):
            raise DomainError("invalid_frame_mapping", 422, "A dissolve requires distinct shots")
    if any(value != full for value in maps.values()):
        raise DomainError("invalid_frame_mapping", 422)
    local = deepcopy(sequence)
    for entry in local["entries"]:
        entry["mapping_artifact_id"] = "complete-map"
    inputs = {}
    with service.services.registry.project_store(project_id).transaction(write=False) as tx:
        admitted(service.services, tx, caller)
        version = tx.require("versions", sequence["sequence_version_id"])
        reference = {"item_id": version["item_id"], "version_id": version["version_id"]}
        if admission["target"]["version"] != reference:
            raise DomainError("scope_denied", 403)
        sequence_map(tx, local, sequence["sequence_version_id"], {"complete-map": full})
        for row in full["frames"]:
            for contributor in row["contributors"]:
                source_job = tx.require("jobs", contributor["job_id"])
                matches = [
                    r
                    for r in service.records(tx, source_job, "frames")
                    if r["source_frame"] == contributor["source_frame"]
                    and r["version_id"] == contributor["version_id"]
                    and r["shot_id"] == contributor["shot_id"]
                ]
                if len(matches) != 1:
                    raise DomainError(
                        "media_unavailable", 422, "A pinned contributor frame is unavailable"
                    )
                receipt = matches[0]
                inputs[receipt["artifact_id"]] = receipt
    ordered = [inputs[key] for key in sorted(inputs)]
    if sorted(admission["input_artifact_ids"]) != sorted(inputs):
        raise DomainError("invalid_frame_mapping", 422)
    snapshot = {"sequence": sequence, "frame_map": full, "input_frames": ordered}
    if admission["input_digest"] != fingerprint(snapshot):
        raise DomainError("hash_mismatch", 422, "Assembly digest does not bind exact contributors")
    dimensions = {tuple(sorted(row["dimensions"].items())) for row in ordered}
    if len(dimensions) != 1:
        raise DomainError("invalid_frame_mapping", 422, "Assembly input dimensions must agree")
    for row in ordered:
        service.artifacts.ready_path(project_id, row["artifact_id"])
    return snapshot


def execute(root: Path, task: dict) -> None:
    """Runs in the detached supervisor from sealed, copied source PNGs only."""
    snapshot = task["assembly"]
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
    fps, count = snapshot["frame_map"]["fps"], snapshot["frame_map"]["frame_count"]
    video = root / "delivery.mp4"
    command = encode(root, 1, count, fps, task["config"], video)
    verify_video(video, fps, count, frames[0]["dimensions"], task["config"])
    atomic_bytes(root / "frame-map.json", canonical(snapshot["frame_map"]))
    manifest = {
        "input_digest": task["input_digest"],
        "sequence": snapshot["sequence"],
        "frame_map_sha256": sha((root / "frame-map.json").read_bytes()),
        "video_sha256": sha(video.read_bytes()),
        "input_frames": frames,
        "encode_argv": command,
    }
    atomic(root / "assembly-manifest.json", manifest)
    atomic(root / "assembly-ready.json", {"complete": True})
