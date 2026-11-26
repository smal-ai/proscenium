"""Relationship checks for authored catalogue documents, using the existing wire schema."""

from fractions import Fraction
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from proscenium.api.errors import DomainError
from proscenium.audio_formats import AUDIO_DEMUXERS
from proscenium.contracts import SCHEMA, validate
from proscenium.db import Transaction

DISTINCT_DISSOLVE_SOURCES = "A dissolve requires distinct shots"
EXPLICIT_FRAME_MAP_REQUIRED = "Explicit hold/drop/dissolve map required"

SILENT_AUDIO_TRACKS = "Silent sequences must not declare audio_tracks"
MISSING_AUDIO_TRACKS = "Non-silent sequences require at least one audio track"
DUPLICATE_AUDIO_TRACK = "Duplicate audio track identity"
INVALID_AUDIO_ARTEFACT = "Audio track must reference a ready audio artefact"
INVALID_AUDIO_START = "Audio track starts at or after the end of the sequence"
INVALID_AUDIO_FADE = "Audio track fade durations exceed the track duration"
INVALID_AUDIO_TRIM = "Audio track trim leaves no source audio"


# Only fixed, host-authored text may be returned to Copilot as a semantic reason.
SAFE_VALIDATION_REASONS: frozenset[str] = frozenset(
    {
        "Frame rates and weights must be reduced",
        "Frame range start must not exceed end",
        "Duplicate shot identity",
        "Duplicate shot-frame identity",
        "Frame roles must be ordered start, intermediate, end",
        "Sequence contributor job must be a render job",
        "Sequence contributor job does not match the referenced version",
        "Sequence contributor shot_id must match the source render job's shot_id (use null for standalone scene renders)",
        "Sequence source_frames exceed the source render job's admitted frame range",
        "Sequence entries must partition destination frames",
        "Identity and trim sequence entries must have equal source_frames and sequence_frames lengths",
        "Frame-rate changes require an explicit retime map",
        "Sequence entries must cover frames 1 through frame_count contiguously",
        SILENT_AUDIO_TRACKS,
        MISSING_AUDIO_TRACKS,
        DUPLICATE_AUDIO_TRACK,
        INVALID_AUDIO_ARTEFACT,
        INVALID_AUDIO_START,
        INVALID_AUDIO_FADE,
        INVALID_AUDIO_TRIM,
        DISTINCT_DISSOLVE_SOURCES,
        EXPLICIT_FRAME_MAP_REQUIRED,
    }
)


@cache
def schema_guidance_messages() -> dict[str, frozenset[str]]:
    """Allow only messages derived from installed schema keys, never submitted fields."""
    messages: dict[str, set[str]] = {"required": set(), "additionalProperties": set()}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for name in value.get("required", []):
                messages["required"].add(f"Missing required field {name}")
            properties = value.get("properties", {})
            if properties:
                messages["additionalProperties"].add(
                    "Unexpected property. Allowed keys: " + ", ".join(sorted(properties))
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(SCHEMA)
    return {code: frozenset(values) for code, values in messages.items()}


def safe_schema_guidance(code: str, message: str) -> bool:
    return (
        (code == "maxItems" and message == SILENT_AUDIO_TRACKS)
        or (code in {"required", "minItems"} and message == MISSING_AUDIO_TRACKS)
        or message == f"Field failed {code} validation"
        or message in schema_guidance_messages().get(code, frozenset())
    )


@cache
def _issue_validator(name: str, silent: bool | None) -> tuple[Draft202012Validator, frozenset[str]]:
    definition = SCHEMA["$defs"][name]
    if silent is not None:
        definition = definition["oneOf"][0 if silent else 1]
    known = frozenset(
        key
        for schema in (*SCHEMA["$defs"].values(), definition)
        for key in schema.get("properties", {})
    )
    return (
        Draft202012Validator(
            {**definition, "$defs": SCHEMA["$defs"]}, format_checker=FormatChecker()
        ),
        known,
    )


def schema_issues(name: str, document: Any) -> list[dict[str, str]]:
    """Bounded host-authored diagnostics. Never interpolate rejected values or keys."""
    silent = None
    if (
        name == "SequenceMap"
        and isinstance(document, dict)
        and type(document.get("silent")) is bool
    ):
        # Diagnose the chosen branch, not the failures from its opposite.
        silent = document["silent"]
    validator, known = _issue_validator(name, silent)
    issues = []
    seen: set[tuple[str, str]] = set()
    for error in validator.iter_errors(document):
        parts = list(error.absolute_path)
        if error.validator == "required" and isinstance(error.instance, dict):
            missing = [key for key in error.validator_value if key not in error.instance]
            paths = [parts + [key] for key in missing]
        else:
            paths = [parts]
        for parts in paths:
            path = ""
            for part in parts:
                if isinstance(part, int):
                    path += f"[{part}]"
                else:
                    path += ("." if path else "") + (part if part in known else "field")
            pair = (path or "$", str(error.validator))
            if pair in seen:
                continue
            seen.add(pair)
            if name == "SequenceMap" and path == "audio_tracks":
                audio_reason = {
                    "maxItems": SILENT_AUDIO_TRACKS,
                    "minItems": MISSING_AUDIO_TRACKS,
                    "required": MISSING_AUDIO_TRACKS,
                }.get(pair[1])
                if audio_reason:
                    issues.append(
                        dict(phase="schema", code=pair[1], path=path, message=audio_reason)
                    )
                    if len(issues) >= 25:
                        return issues
                    continue
            allowed_keys = (
                sorted(error.schema.get("properties", {}))
                if error.validator == "additionalProperties" and isinstance(error.schema, dict)
                else []
            )
            message = (
                f"Unexpected property. Allowed keys: {', '.join(allowed_keys)}"
                if allowed_keys
                else f"Missing required field {parts[-1]}"
                if error.validator == "required" and parts and isinstance(parts[-1], str)
                else f"Field failed {error.validator} validation"
            )
            issues.append(
                dict(
                    phase="schema",
                    code=pair[1],
                    path=pair[0],
                    message=message,
                )
            )
            if len(issues) == 25:
                return issues
    return issues


def rational(value: dict[str, int]) -> Fraction:
    result = Fraction(value["numerator"], value["denominator"])
    if result.numerator != value["numerator"] or result.denominator != value["denominator"]:
        raise DomainError("invalid_frame_mapping", 422, "Frame rates and weights must be reduced")
    return result


def interval(value: dict[str, int]) -> int:
    if value["start"] > value["end"]:
        raise DomainError("invalid_frame_mapping", 422, "Frame range start must not exceed end")
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
        if tx.require("artifacts", identity)["state"] != "ready":
            raise DomainError("media_unavailable", 422)
    for reference in document["asset_dependencies"]:
        version_reference(tx, reference, batch)
    shots = set()
    frames = set()
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
        roles = [{"start": 0, "intermediate": 1, "end": 2}[f["role"]] for f in shot["frames"]]
        if roles != sorted(roles):
            raise DomainError(
                "source_conflict", 409, "Frame roles must be ordered start, intermediate, end"
            )
        for frame in shot["frames"]:
            if frame["frame_id"] in frames:
                raise DomainError("source_conflict", 409, "Duplicate shot-frame identity")
            frames.add(frame["frame_id"])
            if frame["artifact_id"] is not None:
                artifact = tx.require("artifacts", frame["artifact_id"])
                if artifact["kind"] != "image":
                    raise DomainError("source_conflict", 409)
                if artifact["state"] != "ready":
                    raise DomainError("media_unavailable", 422)
            elif frame["state"] in {"available", "selected"}:
                raise DomainError("media_unavailable", 422)


def _cached_record(
    tx: Transaction, namespace: str, identity: str, records: dict[str, dict[str, Any]] | None
) -> dict[str, Any]:
    if records is None:
        return tx.require(namespace, identity)
    if identity not in records:
        records[identity] = tx.require(namespace, identity)
    return records[identity]


def source_job(
    tx: Transaction,
    source: dict[str, Any],
    *,
    version_cache: dict[str, dict[str, Any]] | None = None,
    job_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    version = _cached_record(tx, "versions", source["version_id"], version_cache)
    job = _cached_record(tx, "jobs", source["job_id"], job_cache)
    if job["kind"] != "render" or job["render_spec"] is None:
        raise DomainError(
            "invalid_frame_mapping", 422, "Sequence contributor job must be a render job"
        )
    if job["target"]["version"] != {
        "item_id": version["item_id"],
        "version_id": version["version_id"],
    }:
        raise DomainError(
            "invalid_frame_mapping",
            422,
            "Sequence contributor job does not match the referenced version",
        )
    if job["target"]["shot_id"] != source["shot_id"]:
        raise DomainError(
            "invalid_frame_mapping",
            422,
            "Sequence contributor shot_id must match the source render job's shot_id (use null for standalone scene renders)",
        )
    frames = source.get("source_frames") or {
        "start": source["source_frame"],
        "end": source["source_frame"],
    }
    interval(frames)
    admitted = job["render_spec"]["frames"]
    if frames["start"] < admitted["start"] or frames["end"] > admitted["end"]:
        raise DomainError(
            "invalid_frame_mapping",
            422,
            "Sequence source_frames exceed the source render job's admitted frame range",
        )
    return job


def distinct_transition_source(contributor: dict[str, Any]) -> Any:
    return (
        contributor["shot_id"]
        if contributor["shot_id"] is not None
        else ("scene", contributor["version_id"], contributor["job_id"])
    )


def validate_audio_tracks(tx: Transaction, document: dict[str, Any], duration: Fraction) -> None:
    ids = set()
    for track in document.get("audio_tracks", []):
        identity = track["track_id"]
        if identity in ids:
            raise DomainError("invalid_request", 422, DUPLICATE_AUDIO_TRACK)
        ids.add(identity)
        artifact = tx.get("artifacts", track["artifact_id"], required=False)
        if (
            not artifact
            or artifact["kind"] != "audio"
            or artifact["state"] != "ready"
            or artifact["project_id"] != tx.project_id
            or artifact["media_type"] not in AUDIO_DEMUXERS
            or not artifact.get("duration_seconds")
        ):
            raise DomainError("media_unavailable", 422, INVALID_AUDIO_ARTEFACT)
        start = Fraction(str(track["start_seconds"]))
        if start >= duration:
            raise DomainError("invalid_request", 422, INVALID_AUDIO_START)
        effective = duration - start
        if "duration_seconds" in track:
            effective = min(effective, Fraction(str(track["duration_seconds"])))
        source = Fraction(str(artifact["duration_seconds"])) - Fraction(
            str(track.get("trim_start_seconds", 0))
        )
        if source <= 0:
            raise DomainError("invalid_request", 422, INVALID_AUDIO_TRIM)
        effective = min(effective, source)
        fades = sum(Fraction(str(track.get(k, 0))) for k in ("fade_in_seconds", "fade_out_seconds"))
        if fades > effective:
            raise DomainError("invalid_request", 422, INVALID_AUDIO_FADE)


def sequence_map(
    tx: Transaction, document: dict[str, Any], version_id: str, maps: dict[str, dict[str, Any]]
) -> None:
    tracks = document.get("audio_tracks")
    if document.get("silent") is True and isinstance(tracks, list) and tracks:
        raise DomainError("invalid_request", 422, SILENT_AUDIO_TRACKS)
    if document.get("silent") is False and (tracks is None or tracks == []):
        raise DomainError("invalid_request", 422, MISSING_AUDIO_TRACKS)
    validate("SequenceMap", document)
    if document["project_id"] != tx.project_id or document["sequence_version_id"] != version_id:
        raise DomainError("scope_denied", 403)
    fps = rational(document["fps"])
    validate_audio_tracks(tx, document, document["frame_count"] / fps)
    next_frame = 1
    entries = set()
    version_cache: dict[str, dict[str, Any]] = {}
    job_cache: dict[str, dict[str, Any]] = {}

    def resolve(source: dict[str, Any]) -> dict[str, Any]:
        return source_job(tx, source, version_cache=version_cache, job_cache=job_cache)

    for entry in document["entries"]:
        interval(entry["source_frames"])
        length = interval(entry["sequence_frames"])
        if entry["entry_id"] in entries or entry["sequence_frames"]["start"] != next_frame:
            raise DomainError(
                "invalid_frame_mapping", 422, "Sequence entries must partition destination frames"
            )
        entries.add(entry["entry_id"])
        next_frame = entry["sequence_frames"]["end"] + 1
        job = resolve(entry)
        if (
            entry["transform"] in {"identity", "trim"}
            and interval(entry["source_frames"]) != length
        ):
            raise DomainError(
                "invalid_frame_mapping",
                422,
                "Identity and trim sequence entries must have equal source_frames and sequence_frames lengths",
            )
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
                resolve(contributor)
            else:
                _cached_record(tx, "versions", contributor["version_id"], version_cache)
    if next_frame != document["frame_count"] + 1:
        raise DomainError(
            "invalid_frame_mapping",
            422,
            "Sequence entries must cover frames 1 through frame_count contiguously",
        )
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
            if (
                entry["transform"] == "transition"
                and len({distinct_transition_source(c) for c in row["contributors"]}) < 2
            ):
                raise DomainError("invalid_frame_mapping", 422, DISTINCT_DISSOLVE_SOURCES)
            if entry["transform"] == "retime" and len(row["contributors"]) != 1:
                raise DomainError("invalid_frame_mapping", 422)
            for contributor in row["contributors"]:
                resolve(contributor)
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
