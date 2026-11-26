"""Exact JSON Schema validation shared by HTTP, repositories and Pydantic bindings."""

import hashlib
import json
from collections import OrderedDict
from functools import cache
from importlib.resources import files
from threading import Lock
from typing import Any

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from proscenium.api.errors import DomainError

SCHEMA = json.loads(files(__package__).joinpath("schema.json").read_text())
_FORMAT_CHECKER = FormatChecker()
_VALIDATED_JSON: OrderedDict[tuple[str, bytes], bool] = OrderedDict()
_VALIDATED_JSON_LOCK = Lock()
_VALIDATED_JSON_LIMIT = 8192


@cache
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]}, format_checker=_FORMAT_CHECKER
    )


def validate(name: str, value: Any) -> Any:
    try:
        _validator(name).validate(value)
        rfc8785.dumps(value)  # Reject non-finite/out-of-range JSON values as well.
    except (ValidationError, ValueError, rfc8785.CanonicalizationError) as exc:
        if (
            name == "CheckpointRequest"
            and isinstance(exc, ValidationError)
            and exc.validator == "dependentRequired"
        ):
            message = (
                "entry_path is required when building a source closure"
                if "entry_path" not in value
                else "exclude_paths requires base_version"
            )
            raise DomainError("invalid_request", message=message) from exc
        raise DomainError("invalid_request", message=f"Invalid {name}") from exc
    return value


def validate_stored_page(name: str, page: dict[str, Any]) -> dict[str, Any]:
    """Validate an envelope after each returned record has been proven valid."""
    if not isinstance(page.get("items"), list):
        raise DomainError("invalid_request", message=f"Invalid {name}")
    validate(name, {**page, "items": []})
    return page


def _proof_key(name: str, raw_json: str | bytes) -> tuple[str, bytes]:
    raw = raw_json.encode() if isinstance(raw_json, str) else raw_json
    return name, hashlib.blake2b(raw, digest_size=16).digest()


def _remember_proof(key: tuple[str, bytes]) -> None:
    with _VALIDATED_JSON_LOCK:
        _VALIDATED_JSON[key] = True
        _VALIDATED_JSON.move_to_end(key)
        if len(_VALIDATED_JSON) > _VALIDATED_JSON_LIMIT:
            _VALIDATED_JSON.popitem(last=False)


def validate_stored_json(name: str, raw_json: str, parsed: Any) -> Any:
    """Validate the unchanged parse of these exact SQLite bytes on a cache miss."""
    key = _proof_key(name, raw_json)
    with _VALIDATED_JSON_LOCK:
        if key in _VALIDATED_JSON:
            _VALIDATED_JSON.move_to_end(key)
            return parsed
    # Validate the SQLite parse, not the pre-encoding write object. An integral
    # float can round-trip as a JSON integer outside the canonical integer range.
    validate(name, parsed)
    _remember_proof(key)
    return parsed


@cache
def _retained_artifact_page_validator() -> Draft202012Validator:
    # These projection schemas are small and acyclic. Inline their references
    # once instead of resolving several IDs and version pins per artefact.
    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            if set(node) == {"$ref"} and node["$ref"].startswith("#/$defs/"):
                name = node["$ref"].removeprefix("#/$defs/")
                if name == "Artifact":
                    return {"type": "object"}
                return inline(SCHEMA["$defs"][name])
            return {key: inline(value) for key, value in node.items()}
        if isinstance(node, list):
            return [inline(value) for value in node]
        return node

    return Draft202012Validator(
        inline(SCHEMA["$defs"]["RetainedArtifactPage"]), format_checker=_FORMAT_CHECKER
    )


def validate_retained_artifact_page(page: dict[str, Any]) -> dict[str, Any]:
    """Validate new attribution and page fields without walking stored artefacts again."""
    try:
        _retained_artifact_page_validator().validate(page)
    except (ValidationError, ValueError) as exc:
        raise DomainError("invalid_request", message="Invalid RetainedArtifactPage") from exc
    return page


def canonical(value: Any) -> bytes:
    return rfc8785.dumps(value)
