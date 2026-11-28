"""Relationship checks for catalogue records and authored documents."""

import json
from collections.abc import Iterator, Sequence
from fractions import Fraction
from functools import cache
from itertools import islice
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.validators import extend

from proscenium.api.errors import DomainError
from proscenium.audio_formats import AUDIO_DEMUXERS
from proscenium.catalogue.schema_paths import SchemaPathScope, path_origins
from proscenium.catalogue.schema_paths import safe_schema_path as safe_schema_path
from proscenium.contracts import SCHEMA, canonical, validate
from proscenium.db import Transaction

DISTINCT_DISSOLVE_SOURCES = "A dissolve requires distinct shots"
EXPLICIT_FRAME_MAP_REQUIRED = "Explicit hold/drop/dissolve map required"

SILENT_AUDIO_TRACKS = "Silent sequences must not declare audio_tracks"
MISSING_AUDIO_TRACKS = "Non-silent sequences require at least one audio track"
MISSING_CLIP_ENTRY = "Preserving clip audio requires at least one clip entry"
DUPLICATE_AUDIO_TRACK = "Duplicate audio track identity"
INVALID_AUDIO_ARTEFACT = "Audio track must reference a ready audio artefact"
INVALID_CLIP_ARTEFACT = "Clip entry must reference a ready MP4 with dimensions and frame count"
INVALID_AUDIO_START = "Audio track starts at or after the end of the sequence"
INVALID_AUDIO_FADE = "Audio track fade durations exceed the track duration"
INVALID_AUDIO_TRIM = "Audio track trim leaves no source audio"

PLANNED_ASSET_KIND = "planned_assets must reference published versions of kind asset"
PLANNED_ASSET_DUPLICATE = "Remove the duplicate VersionRef from planned_assets"
PLANNED_ASSET_MISSING = "Choose an existing asset version in this project for planned_assets"
PLANNED_ASSET_IDENTITY = "Use the exact item_id and version_id of an asset in this project"
AUTHORED_PIN_MISSING = "Authored version references must be pinned in the source closure"
PIN_DIAGNOSTIC_REASONS = frozenset(
    {
        PLANNED_ASSET_KIND,
        PLANNED_ASSET_DUPLICATE,
        PLANNED_ASSET_MISSING,
        PLANNED_ASSET_IDENTITY,
        AUTHORED_PIN_MISSING,
    }
)


def safe_pin_diagnostics(
    error: DomainError,
    schemas: Sequence[dict[str, Any]],
    *,
    scope: SchemaPathScope | None = None,
) -> dict[str, Any]:
    """Project only typed IDs and schema locations, never arbitrary error details."""
    details = {}
    for key, definition in (
        ("consuming_version", "VersionRef"),
        ("reference", "VersionRef"),
        ("shot_id", "Id"),
    ):
        if (value := error.details.get(key)) is not None:
            try:
                details[key] = validate(definition, value)
            except DomainError:
                pass
    path = error.details.get("path")
    if isinstance(path, str) and safe_schema_path(path, schemas, scope=scope):
        details["path"] = path
    return details


# Only fixed, host-authored text may be returned to Copilot as a semantic reason.
SAFE_VALIDATION_REASONS: frozenset[str] = frozenset(
    {
        *PIN_DIAGNOSTIC_REASONS,
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
        INVALID_CLIP_ARTEFACT,
        "Cannot mix clip and render-frame entries",
        "Clip range must fit the source and match the sequence length",
        DISTINCT_DISSOLVE_SOURCES,
        EXPLICIT_FRAME_MAP_REQUIRED,
    }
)


_LIMIT_GUIDANCE = {
    "minimum": "Must be >= {}",
    "maximum": "Must be <= {}",
    "exclusiveMinimum": "Must be > {}",
    "exclusiveMaximum": "Must be < {}",
    "multipleOf": "Must be a multiple of {}",
    "minLength": "Length must be >= {}",
    "maxLength": "Length must be <= {}",
    "minItems": "Must contain at least {} item(s)",
    "maxItems": "Must contain at most {} item(s)",
}
_SHAPE_KEYWORDS = (
    "enum",
    "const",
    "anyOf",
    "oneOf",
    "type",
    "required",
    "pattern",
    *_LIMIT_GUIDANCE,
    "uniqueItems",
    "format",
    "contains",
)
_SAFE_SCHEMA_CODES = frozenset(
    (*_SHAPE_KEYWORDS, "additionalProperties", "dependentRequired", "not")
)
_NUMBER_JSON_GUIDANCE = "Number must be a finite JSON-safe value within IEEE-754 bounds"
_CANONICAL_JSON_GUIDANCE = "Value must be representable as canonical JSON"
_FORMAT_CHECKER = FormatChecker()
# Read adapters consume exact Python integers (e.g. pagination slices). Wire
# validation retains Draft 2020-12 semantics, which accept integral floats.
_EXACT_INTEGER_VALIDATOR = extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: type(value) is int
    ),
)


def _describe_schema_shape(schema: dict[str, Any] | bool) -> str:
    """Summarise an installed schema branch without inspecting its input."""
    return _describe_branch(schema, frozenset())


def _describe_branch(schema: dict[str, Any] | bool, seen: frozenset[str]) -> str:
    if isinstance(schema, bool):
        return "any value" if schema else "no value"
    reference = schema.get("$ref")
    if reference and reference.startswith("#/$defs/") and reference not in seen:
        name = reference.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
        return _describe_branch(
            {**SCHEMA["$defs"][name], **{k: v for k, v in schema.items() if k != "$ref"}},
            seen | {reference},
        )
    for code in _SHAPE_KEYWORDS:
        if code not in schema:
            continue
        if code == "required":
            return f"Expected object requiring: {', '.join(sorted(schema[code]))}"
        return _schema_message(schema, code, seen)
    return "valid value"


def _schema_message(
    schema: dict[str, Any] | bool, code: str, seen: frozenset[str] = frozenset()
) -> str:
    """The issue and its allow-list entry must derive from the same metadata."""
    if not isinstance(schema, dict):
        return f"Field failed {code} validation"
    value: Any = schema.get(code)
    if code == "enum":
        return f"Invalid value. Allowed values: {', '.join(json.dumps(v) for v in value)}"
    if code == "const":
        return f"Expected exact value {json.dumps(value)}"
    if code == "type":
        if value == "object" and (properties := schema.get("properties")):
            return f"Expected object with keys: {', '.join(sorted(properties))}"
        return f"Expected {' or '.join(value) if isinstance(value, list) else value}"
    if code in {"anyOf", "oneOf"}:
        branches = (
            (
                _describe_branch(branch, seen) if seen else _describe_schema_shape(branch)
            ).removeprefix("Expected ")
            for branch in value
        )
        return f"Expected one of: {' | '.join(branches)}"
    if code == "contains":
        shape = _describe_branch(value, seen) if seen else _describe_schema_shape(value)
        return f"Must contain at least one item matching: {shape}"
    if code == "additionalProperties":
        if properties := schema.get("properties"):
            return f"Unexpected property. Allowed keys: {', '.join(sorted(properties))}"
        return "Unexpected property. No additional keys are allowed."
    if code == "pattern":
        return f"Must match pattern {value}"
    if code == "not" and isinstance(value, dict) and value.keys() == {"pattern"}:
        return f"Must not match pattern {value['pattern']}"
    if code in _LIMIT_GUIDANCE:
        return _LIMIT_GUIDANCE[code].format(value)
    if code == "uniqueItems":
        return "Items must be unique"
    if code == "format":
        return f"Must be a valid {value}"
    return f"Field failed {code} validation"


def _schema_nodes(schema: Any) -> Iterator[dict[str, Any]]:
    if not isinstance(schema, dict):
        return
    yield schema
    for key, value in schema.items():
        if key in {"$defs", "properties", "patternProperties", "dependentSchemas"}:
            for child in value.values():
                yield from _schema_nodes(child)
        elif key in {"allOf", "anyOf", "oneOf", "prefixItems"}:
            for child in value:
                yield from _schema_nodes(child)
        elif key in {
            "additionalProperties",
            "contains",
            "else",
            "if",
            "items",
            "not",
            "propertyNames",
            "then",
            "unevaluatedItems",
            "unevaluatedProperties",
        }:
            yield from _schema_nodes(value)


def _dependency_message(trigger: str, deps: list[str]) -> str:
    return f"Field {trigger} requires {', '.join(sorted(deps))}"


@cache
def schema_guidance_messages() -> dict[str, frozenset[str]]:
    """Allow only messages from installed schemas and fixed host checks."""
    # Catalogue validation is also used by the Copilot tools. Import lazily so
    # registering their static schemas does not make an import cycle.
    from proscenium.copilot.tools import (
        DISCOVERY_READS,
        READ_TOOLS,
        read_validation_parameters,
    )
    from proscenium.copilot.web_search import INVALID_QUERY
    from proscenium.copilot.workflows import WORKFLOW_TOOLS, validation_parameters

    schemas = (
        SCHEMA,
        *(validation_parameters(name) for name in WORKFLOW_TOOLS),
        *(read_validation_parameters(name) for name in (*READ_TOOLS, *DISCOVERY_READS)),
    )
    messages: dict[str, set[str]] = {code: set() for code in _SAFE_SCHEMA_CODES}
    messages["format"].update((INVALID_QUERY, _CANONICAL_JSON_GUIDANCE))
    messages["type"].add(_NUMBER_JSON_GUIDANCE)
    messages["maxItems"].add(SILENT_AUDIO_TRACKS)
    messages["contains"].add(MISSING_CLIP_ENTRY)
    for code in ("required", "minItems"):
        messages[code].add(MISSING_AUDIO_TRACKS)
    for root in schemas:
        for node in _schema_nodes(root):
            for name in node.get("required", ()):
                messages["required"].add(f"Missing required field {name}")
            for trigger, deps in node.get("dependentRequired", {}).items():
                messages["dependentRequired"].add(_dependency_message(trigger, deps))
            for code in (_SAFE_SCHEMA_CODES - {"required", "dependentRequired"}).intersection(node):
                messages[code].add(_schema_message(node, code))
    return {code: frozenset(values) for code, values in messages.items()}


def safe_schema_guidance(code: str, message: str) -> bool:
    return code in _SAFE_SCHEMA_CODES and message in schema_guidance_messages()[code]


@cache
def _issue_validator(name: str, branch: int | None) -> tuple[Draft202012Validator, SchemaPathScope]:
    definition = SCHEMA["$defs"][name]
    if branch is not None:
        definition = definition["oneOf"][branch]
    return (
        Draft202012Validator(
            {**definition, "$defs": SCHEMA["$defs"]}, format_checker=_FORMAT_CHECKER
        ),
        SchemaPathScope((definition,)),
    )


def schema_issues(name: str, document: Any) -> list[dict[str, str]]:
    """Bounded host-authored diagnostics. Never interpolate rejected values or keys."""
    branch = None
    if (
        name == "SequenceMap"
        and isinstance(document, dict)
        and type(document.get("silent")) is bool
    ):
        # Diagnose the selected soundtrack mode, not the other schema branches.
        branch = (
            0 if document["silent"] else 2 if document.get("preserve_clip_audio") is True else 1
        )
    validator, scope = _issue_validator(name, branch)
    return _schema_issues(validator, scope, document, sequence_map=name == "SequenceMap")


def schema_issues_for_schema(
    schema: dict[str, Any], document: Any, *, exact_integers: bool = False
) -> list[dict[str, str]]:
    """Diagnose a host-authored schema outside the named wire contracts."""
    validator_class = _EXACT_INTEGER_VALIDATOR if exact_integers else Draft202012Validator
    root = {"$defs": SCHEMA["$defs"], **schema}
    validator = validator_class(root, format_checker=_FORMAT_CHECKER)
    return _schema_issues(validator, SchemaPathScope((root,)), document)


def _schema_issues(
    validator: Draft202012Validator,
    scope: SchemaPathScope,
    document: Any,
    *,
    sequence_map: bool = False,
) -> list[dict[str, str]]:
    issues = []
    seen: set[tuple[str, str]] = set()
    for error in validator.iter_errors(document):
        parts = list(error.absolute_path)
        origins = path_origins(list(error.absolute_schema_path))[: len(parts)]
        code = str(error.validator)
        if code == "required":
            entries = [
                (parts + [key], f"Missing required field {key}")
                for key in error.validator_value
                if key not in error.instance
            ]
        elif code == "dependentRequired":
            entries = [
                (parts + [trigger], _dependency_message(trigger, deps))
                for trigger, deps in error.validator_value.items()
                if trigger in error.instance and any(dep not in error.instance for dep in deps)
            ]
        else:
            entries = [(parts, _schema_message(error.schema, code))]
        for issue_parts, message in entries:
            path = scope.render(issue_parts, origins)
            pair = (path, code)
            if pair in seen:
                continue
            seen.add(pair)
            if sequence_map and path == "audio_tracks":
                message = {
                    "maxItems": SILENT_AUDIO_TRACKS,
                    "minItems": MISSING_AUDIO_TRACKS,
                    "required": MISSING_AUDIO_TRACKS,
                }.get(code, message)
            if sequence_map and path == "entries" and code == "contains":
                message = MISSING_CLIP_ENTRY
            issues.append(dict(phase="schema", code=code, path=path, message=message))
            if len(issues) == 25:
                return issues
    return issues


def canonical_json_issues(schema: dict[str, Any], document: Any) -> list[dict[str, str]]:
    """Locate non-canonical leaves after schema validation, without echoing values."""
    scope = SchemaPathScope((schema,))

    def failed(value: Any, parts: tuple[str | int, ...]) -> Iterator[dict[str, str]]:
        if isinstance(value, dict):
            for key, child in value.items():
                yield from failed(child, (*parts, key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from failed(child, (*parts, index))
        else:
            try:
                canonical(value)
            except ValueError:
                number = type(value) in {int, float}
                yield dict(
                    phase="schema",
                    code="type" if number else "format",
                    path=scope.render(parts),
                    message=_NUMBER_JSON_GUIDANCE if number else _CANONICAL_JSON_GUIDANCE,
                )

    return list(islice(failed(document, ()), 25))


def rational(value: dict[str, int]) -> Fraction:
    result = Fraction(value["numerator"], value["denominator"])
    if result.numerator != value["numerator"] or result.denominator != value["denominator"]:
        raise DomainError(
            "invalid_frame_mapping",
            422,
            "Frame rates and weights must be reduced",
            safe_for_model=True,
        )
    return result


def interval(value: dict[str, int]) -> int:
    if value["start"] > value["end"]:
        raise DomainError(
            "invalid_frame_mapping",
            422,
            "Frame range start must not exceed end",
            safe_for_model=True,
        )
    return value["end"] - value["start"] + 1


def validate_artifact_source_version(
    version: dict[str, Any], artifact: dict[str, Any], target: dict[str, Any]
) -> None:
    if artifact.get("source_version_id") in (None, target["version_id"]):
        return
    # Only the immutable publication counts, not later links or render outputs.
    origin = target.get("origin")
    if (
        artifact.get("kind") == "image"
        and origin in {"concept", "reference"}
        and origin == artifact.get("purpose")
        and artifact["artifact_id"] in version["artifact_ids"]
    ):
        return
    raise DomainError(
        "source_conflict",
        409,
        message="Artifact belongs to a different version. Only concept and reference images "
        "published on the target version may be reused.",
        safe_for_model=True,
    )


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


def publication_entry(version: dict[str, Any]) -> None:
    source = version["source"]
    entry = next(
        (f["origin"] for f in source["files"] if f["destination"] == source["entry_path"]), None
    )
    if entry is None or (entry["kind"] == "git" and entry["commit"] != version["source_commit"]):
        raise DomainError(
            "source_conflict",
            409,
            "Source closure must contain its entry_path, pinned to source_commit for a Git entry",
            safe_for_model=True,
        )


PinLocations = dict[tuple[str, str], tuple[str, str | None]]


def required_dependencies(version: dict[str, Any], references: PinLocations) -> None:
    sealed = {version_ref_key(ref) for ref in version["source"]["dependencies"]}
    for (item_id, version_id), (path, shot_id) in references.items():
        if (item_id, version_id) not in sealed:
            raise DomainError(
                "dependency_missing",
                422,
                AUTHORED_PIN_MISSING,
                safe_for_model=True,
                path=path,
                reference={"item_id": item_id, "version_id": version_id},
                consuming_version=exact_ref(version),
                shot_id=shot_id,
            )


def sequence_dependencies(
    tx: Transaction, document: dict[str, Any], version: dict[str, Any]
) -> None:
    """Require every entry or contributor's exact version after sequence validation."""
    records: dict[str, dict[str, Any]] = {}
    refs: PinLocations = {}
    for index, entry in enumerate(document["entries"]):
        path = f"entries[{index}]"
        if "version_id" in entry:
            version_id = entry["version_id"]
            item_id = _cached_record(tx, "versions", version_id, records)["item_id"]
            refs.setdefault((item_id, version_id), (f"{path}.version_id", entry["shot_id"]))
        for offset, contributor in enumerate(entry.get("contributors", [])):
            if "version_id" in contributor:
                version_id = contributor["version_id"]
                item_id = _cached_record(tx, "versions", version_id, records)["item_id"]
                refs.setdefault(
                    (item_id, version_id),
                    (f"{path}.contributors[{offset}].version_id", contributor["shot_id"]),
                )
    required_dependencies(version, refs)


def shot_plan(
    tx: Transaction,
    document: dict[str, Any],
    batch: dict[str, dict[str, Any]],
    *,
    historical: bool = False,
    version: dict[str, Any] | None = None,
) -> None:
    validate("AuthoredShotPlan", document)
    if document["project_id"] != tx.project_id:
        raise DomainError("scope_denied", 403)
    if not historical:
        for identity in document["brief_artifact_ids"]:
            if tx.require("artifacts", identity)["state"] != "ready":
                raise DomainError("media_unavailable", 422)
    refs: PinLocations = {}
    consuming_version = exact_ref(version) if version is not None else None
    for index, reference in enumerate(document["asset_dependencies"]):
        version_reference(tx, reference, batch)
        refs.setdefault(version_ref_key(reference), (f"asset_dependencies[{index}]", None))
    shots = set()
    frames = set()
    for index, shot in enumerate(document["shots"]):
        if shot["shot_id"] in shots:
            raise DomainError(
                "source_conflict", 409, "Duplicate shot identity", safe_for_model=True
            )
        shots.add(shot["shot_id"])
        planned = set()
        for pin_index, reference in enumerate(shot.get("planned_assets", [])):
            key = version_ref_key(reference)
            path = f"shots[{index}].planned_assets[{pin_index}]"
            if key in planned:
                raise DomainError(
                    "invalid_request",
                    422,
                    PLANNED_ASSET_DUPLICATE,
                    safe_for_model=True,
                    path=path,
                    reference=reference,
                    consuming_version=consuming_version,
                    shot_id=shot["shot_id"],
                )
            planned.add(key)
            planned_asset_reference(
                tx,
                reference,
                batch,
                path=path,
                shot_id=shot["shot_id"],
                consuming_version=consuming_version,
            )
            refs.setdefault(key, (path, shot["shot_id"]))
        if shot["scene_version"] is not None:
            if version_reference(tx, shot["scene_version"], batch)["kind"] != "scene":
                raise DomainError("source_conflict", 409)
            refs.setdefault(
                version_ref_key(shot["scene_version"]),
                (f"shots[{index}].scene_version", shot["shot_id"]),
            )
        if shot["source_range"] is not None:
            interval(shot["source_range"])
        if shot["fps"] is not None:
            rational(shot["fps"])
        roles = [{"start": 0, "intermediate": 1, "end": 2}[f["role"]] for f in shot["frames"]]
        if roles != sorted(roles):
            raise DomainError(
                "source_conflict",
                409,
                "Frame roles must be ordered start, intermediate, end",
                safe_for_model=True,
            )
        for frame in shot["frames"]:
            if frame["frame_id"] in frames:
                raise DomainError(
                    "source_conflict", 409, "Duplicate shot-frame identity", safe_for_model=True
                )
            frames.add(frame["frame_id"])
            if frame["artifact_id"] is not None:
                artifact = tx.get("artifacts", frame["artifact_id"], required=not historical)
                if artifact is not None and artifact["kind"] != "image":
                    raise DomainError("source_conflict", 409)
                if not historical and artifact is not None and artifact["state"] != "ready":
                    raise DomainError("media_unavailable", 422)
            elif frame["state"] in {"available", "selected"}:
                raise DomainError("media_unavailable", 422)
    if version is not None:
        required_dependencies(version, refs)


def version_ref_key(reference: dict[str, str]) -> tuple[str, str]:
    return reference["item_id"], reference["version_id"]


def exact_ref(version: dict[str, Any]) -> dict[str, str]:
    return {key: version[key] for key in ("item_id", "version_id")}


def planned_asset_reference(
    tx: Transaction,
    reference: dict[str, str],
    batch: dict[str, dict[str, Any]],
    *,
    path: str,
    shot_id: str,
    consuming_version: dict[str, str] | None,
) -> None:
    version_id = reference["version_id"]
    record = batch.get(version_id)
    # Inline batch items inherit the enclosing publication's project.
    if record is None:
        record = tx.get("versions", version_id, required=False)
        project_id = record.get("project_id") if record else None
    else:
        project_id = record.get("project_id", tx.project_id)
    if record is None:
        raise DomainError(
            "dependency_missing",
            422,
            PLANNED_ASSET_MISSING,
            safe_for_model=True,
            path=path,
            reference=reference,
            consuming_version=consuming_version,
            shot_id=shot_id,
        )
    if project_id != tx.project_id or any(
        record.get(key) != value for key, value in reference.items()
    ):
        raise DomainError(
            "scope_denied",
            403,
            PLANNED_ASSET_IDENTITY,
            safe_for_model=True,
            path=path,
            reference=reference,
            consuming_version=consuming_version,
            shot_id=shot_id,
        )
    if record["kind"] != "asset":
        raise DomainError(
            "invalid_request",
            422,
            PLANNED_ASSET_KIND,
            safe_for_model=True,
            path=path,
            reference=reference,
            consuming_version=consuming_version,
            shot_id=shot_id,
        )


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
            "invalid_frame_mapping",
            422,
            "Sequence contributor job must be a render job",
            safe_for_model=True,
        )
    if job["target"]["version"] != {
        "item_id": version["item_id"],
        "version_id": version["version_id"],
    }:
        raise DomainError(
            "invalid_frame_mapping",
            422,
            "Sequence contributor job does not match the referenced version",
            safe_for_model=True,
        )
    if job["target"]["shot_id"] != source["shot_id"]:
        raise DomainError(
            "invalid_frame_mapping",
            422,
            "Sequence contributor shot_id must match the source render job's shot_id (use null for standalone scene renders)",
            safe_for_model=True,
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
            safe_for_model=True,
        )
    return job


def distinct_transition_source(contributor: dict[str, Any]) -> Any:
    return (
        contributor["shot_id"]
        if contributor["shot_id"] is not None
        else ("scene", contributor["version_id"], contributor["job_id"])
    )


def validate_audio_tracks(
    tx: Transaction, document: dict[str, Any], duration: Fraction, *, historical: bool = False
) -> None:
    ids = set()
    for track in document.get("audio_tracks", []):
        identity = track["track_id"]
        if identity in ids:
            raise DomainError("invalid_request", 422, DUPLICATE_AUDIO_TRACK, safe_for_model=True)
        ids.add(identity)
        artifact = tx.get("artifacts", track["artifact_id"], required=False)
        if not (historical and artifact is None) and (
            not artifact
            or artifact["kind"] != "audio"
            or (not historical and artifact["state"] != "ready")
            or artifact["project_id"] != tx.project_id
            or artifact["media_type"] not in AUDIO_DEMUXERS
            or not artifact.get("duration_seconds")
        ):
            raise DomainError("media_unavailable", 422, INVALID_AUDIO_ARTEFACT, safe_for_model=True)
        start = Fraction(str(track["start_seconds"]))
        if start >= duration:
            raise DomainError("invalid_request", 422, INVALID_AUDIO_START, safe_for_model=True)
        effective = duration - start
        if "duration_seconds" in track:
            effective = min(effective, Fraction(str(track["duration_seconds"])))
        if artifact is not None:
            source = Fraction(str(artifact["duration_seconds"])) - Fraction(
                str(track.get("trim_start_seconds", 0))
            )
            if source <= 0:
                raise DomainError("invalid_request", 422, INVALID_AUDIO_TRIM, safe_for_model=True)
            effective = min(effective, source)
        fades = sum(Fraction(str(track.get(k, 0))) for k in ("fade_in_seconds", "fade_out_seconds"))
        if fades > effective:
            raise DomainError("invalid_request", 422, INVALID_AUDIO_FADE, safe_for_model=True)


def sequence_map(
    tx: Transaction,
    document: dict[str, Any],
    version_id: str,
    maps: dict[str, dict[str, Any]] | None,
) -> None:
    # Historical reads verify the sealed SequenceMap, not mutable media readiness
    # or bytes of separate FrameMap artefacts.
    historical = maps is None
    tracks = document.get("audio_tracks")
    if document.get("silent") is True and isinstance(tracks, list) and tracks:
        raise DomainError("invalid_request", 422, SILENT_AUDIO_TRACKS, safe_for_model=True)
    if (
        document.get("preserve_clip_audio") is not True
        and document.get("silent") is False
        and (tracks is None or tracks == [])
    ):
        raise DomainError("invalid_request", 422, MISSING_AUDIO_TRACKS, safe_for_model=True)
    validate("SequenceMap", document)
    if document["project_id"] != tx.project_id or document["sequence_version_id"] != version_id:
        raise DomainError("scope_denied", 403)
    kinds = {"clip_artifact_id" in entry for entry in document["entries"]}
    if len(kinds) != 1:
        raise DomainError(
            "invalid_frame_mapping",
            422,
            "Cannot mix clip and render-frame entries",
            safe_for_model=True,
        )
    clip_based = True in kinds
    fps = rational(document["fps"])
    validate_audio_tracks(tx, document, document["frame_count"] / fps, historical=historical)
    next_frame = 1
    entries = set()
    version_cache: dict[str, dict[str, Any]] = {}
    job_cache: dict[str, dict[str, Any]] = {}

    def resolve(source: dict[str, Any]) -> dict[str, Any]:
        return source_job(tx, source, version_cache=version_cache, job_cache=job_cache)

    for entry in document["entries"]:
        source_length = interval(entry["source_frames"])
        length = interval(entry["sequence_frames"])
        if entry["entry_id"] in entries or entry["sequence_frames"]["start"] != next_frame:
            raise DomainError(
                "invalid_frame_mapping",
                422,
                "Sequence entries must partition destination frames",
                safe_for_model=True,
            )
        entries.add(entry["entry_id"])
        next_frame = entry["sequence_frames"]["end"] + 1
        if clip_based:
            clip = tx.get("artifacts", entry["clip_artifact_id"], required=False)
            dimensions = (clip or {}).get("dimensions")
            if not (historical and clip is None) and (
                not clip
                or clip["kind"] != "video"
                or (not historical and clip["state"] != "ready")
                or clip["project_id"] != tx.project_id
                or clip["media_type"] != "video/mp4"
                or type(clip.get("frame_count")) is not int
                or clip["frame_count"] < 1
                or not isinstance(dimensions, dict)
                or any(
                    type(dimensions.get(k)) is not int or dimensions[k] < 1
                    for k in ("width", "height")
                )
            ):
                raise DomainError(
                    "media_unavailable", 422, INVALID_CLIP_ARTEFACT, safe_for_model=True
                )
            if source_length != length or (
                clip is not None and entry["source_frames"]["end"] > clip["frame_count"]
            ):
                raise DomainError(
                    "invalid_frame_mapping",
                    422,
                    "Clip range must fit the source and match the sequence length",
                    safe_for_model=True,
                )
            continue
        job = resolve(entry)
        if entry["transform"] in {"identity", "trim"} and source_length != length:
            raise DomainError(
                "invalid_frame_mapping",
                422,
                "Identity and trim sequence entries must have equal source_frames and sequence_frames lengths",
                safe_for_model=True,
            )
        if (
            entry["transform"] in {"identity", "trim"}
            and job["render_spec"]["fps"] != document["fps"]
        ):
            raise DomainError(
                "invalid_frame_mapping",
                422,
                "Frame-rate changes require an explicit retime map",
                safe_for_model=True,
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
            safe_for_model=True,
        )
    if maps is None:
        return
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
                raise DomainError(
                    "invalid_frame_mapping", 422, DISTINCT_DISSOLVE_SOURCES, safe_for_model=True
                )
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
