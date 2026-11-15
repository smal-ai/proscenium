"""Relationship checks for authored catalogue documents, using the existing wire schema."""

from fractions import Fraction
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.contracts import validate
from proscenium.db import Transaction


def rational(value: dict[str, int]) -> Fraction:
    result = Fraction(value["numerator"], value["denominator"])
    if result.numerator != value["numerator"] or result.denominator != value["denominator"]:
        raise DomainError("invalid_frame_mapping", 422, "Frame rates and weights must be reduced")
    return result


def interval(value: dict[str, int]) -> int:
    if value["start"] > value["end"]:
        raise DomainError("invalid_frame_mapping", 422)
    return value["end"] - value["start"] + 1


def version_reference(
    tx: Transaction, reference: dict[str, str], batch: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    record = batch.get(reference["version_id"]) or tx.get(
        "versions", reference["version_id"], required=False
    )
    if not record:
        raise DomainError("dependency_missing", 422, version_id=reference["version_id"])
    if record["item_id"] != reference["item_id"]:
        raise DomainError("scope_denied", 403)
    return record


def shot_plan(tx: Transaction, document: dict[str, Any], batch: dict[str, dict[str, Any]]) -> None:
    validate("AuthoredShotPlan", document)
    if document["project_id"] != tx.project_id:
        raise DomainError("scope_denied", 403)
    for identity in document["brief_artifact_ids"]:
        tx.require("artifacts", identity)
    for reference in document["asset_dependencies"]:
        version_reference(tx, reference, batch)
    shots = set()
    for shot in document["shots"]:
        if shot["shot_id"] in shots:
            raise DomainError("source_conflict", 409, "Duplicate shot identity")
        shots.add(shot["shot_id"])
        if shot["scene_version"] is not None:
            if version_reference(tx, shot["scene_version"], batch)["kind"] != "scene":
                raise DomainError("source_conflict", 409)
        if shot["source_range"] is not None:
            interval(shot["source_range"])
        if shot["fps"] is not None:
            rational(shot["fps"])
        frames = set()
        for frame in shot["frames"]:
            if frame["frame_id"] in frames:
                raise DomainError("source_conflict", 409, "Duplicate shot-frame identity")
            frames.add(frame["frame_id"])
            if frame["artifact_id"] is not None:
                if tx.require("artifacts", frame["artifact_id"])["kind"] != "image":
                    raise DomainError("source_conflict", 409)
            elif frame["state"] in {"available", "selected"}:
                raise DomainError("media_unavailable", 422)


def source_job(tx: Transaction, source: dict[str, Any]) -> dict[str, Any]:
    version = tx.require("versions", source["version_id"])
    job = tx.require("jobs", source["job_id"])
    if (
        job["kind"] != "render"
        or job["target"]["version"]
        != {"item_id": version["item_id"], "version_id": version["version_id"]}
        or job["target"]["shot_id"] != source["shot_id"]
        or job["render_spec"] is None
    ):
        raise DomainError("invalid_frame_mapping", 422)
    frames = source.get("source_frames") or {
        "start": source["source_frame"],
        "end": source["source_frame"],
    }
    interval(frames)
    admitted = job["render_spec"]["frames"]
    if frames["start"] < admitted["start"] or frames["end"] > admitted["end"]:
        raise DomainError("invalid_frame_mapping", 422)
    return job


def sequence_map(
    tx: Transaction, document: dict[str, Any], version_id: str, maps: dict[str, dict[str, Any]]
) -> None:
    validate("SequenceMap", document)
    if document["project_id"] != tx.project_id or document["sequence_version_id"] != version_id:
        raise DomainError("scope_denied", 403)
    rational(document["fps"])
    next_frame = 1
    entries = set()
    for entry in document["entries"]:
        interval(entry["source_frames"])
        length = interval(entry["sequence_frames"])
        if entry["entry_id"] in entries or entry["sequence_frames"]["start"] != next_frame:
            raise DomainError(
                "invalid_frame_mapping", 422, "Sequence entries must partition destination frames"
            )
        entries.add(entry["entry_id"])
        next_frame = entry["sequence_frames"]["end"] + 1
        job = source_job(tx, entry)
        if (
            entry["transform"] in {"identity", "trim"}
            and interval(entry["source_frames"]) != length
        ):
            raise DomainError("invalid_frame_mapping", 422)
        if (
            entry["transform"] in {"identity", "trim"}
            and job["render_spec"]["fps"] != document["fps"]
        ):
            raise DomainError(
                "invalid_frame_mapping", 422, "Frame-rate changes require an explicit retime map"
            )
        for contributor in entry.get("contributors", []):
            interval(contributor["source_frames"])
            if contributor.get("job_id"):
                source_job(tx, contributor)
            else:
                tx.require("versions", contributor["version_id"])
    if next_frame != document["frame_count"] + 1:
        raise DomainError("invalid_frame_mapping", 422)
    for identity in {
        entry["mapping_artifact_id"]
        for entry in document["entries"]
        if entry["mapping_artifact_id"] is not None
    }:
        mapping = maps[identity]
        validate("FrameMap", mapping)
        if (
            mapping["project_id"] != tx.project_id
            or mapping["sequence_version_id"] != version_id
            or mapping["fps"] != document["fps"]
            or mapping["frame_count"] != document["frame_count"]
            or [row["sequence_frame"] for row in mapping["frames"]]
            != list(range(1, document["frame_count"] + 1))
        ):
            raise DomainError("invalid_frame_mapping", 422)
        for row in mapping["frames"]:
            entry = next(
                entry
                for entry in document["entries"]
                if entry["sequence_frames"]["start"]
                <= row["sequence_frame"]
                <= entry["sequence_frames"]["end"]
            )
            declared = (
                entry.get("contributors", []) if entry["transform"] == "transition" else [entry]
            )
            if (
                sum(
                    (rational(contributor["weight"]) for contributor in row["contributors"]),
                    Fraction(),
                )
                != 1
            ):
                raise DomainError("invalid_frame_mapping", 422)
            if entry["transform"] == "transition" and len(row["contributors"]) < 2:
                raise DomainError("invalid_frame_mapping", 422)
            if entry["transform"] == "retime" and len(row["contributors"]) != 1:
                raise DomainError("invalid_frame_mapping", 422)
            for contributor in row["contributors"]:
                source_job(tx, contributor)
                matches = [
                    source
                    for source in declared
                    if source["version_id"] == contributor["version_id"]
                    and source["shot_id"] == contributor["shot_id"]
                    and source.get("job_id") in {None, contributor["job_id"]}
                    and source["source_frames"]["start"]
                    <= contributor["source_frame"]
                    <= source["source_frames"]["end"]
                ]
                if not matches:
                    raise DomainError("invalid_frame_mapping", 422)
            if entry["transform"] in {"identity", "trim"}:
                if (
                    len(row["contributors"]) != 1
                    or row["contributors"][0]["source_frame"]
                    != entry["source_frames"]["start"]
                    + row["sequence_frame"]
                    - entry["sequence_frames"]["start"]
                ):
                    raise DomainError("invalid_frame_mapping", 422)
