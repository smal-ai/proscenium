"""Exact JSON Schema validation shared by HTTP, repositories and Pydantic bindings."""

import json
from importlib.resources import files
from typing import Any

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from proscenium.api.errors import DomainError

SCHEMA = json.loads(files(__package__).joinpath("schema.json").read_text())


def validate(name: str, value: Any) -> Any:
    schema = {"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]}
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
        rfc8785.dumps(value)  # Reject non-finite/out-of-range JSON values as well.
    except (ValidationError, ValueError, rfc8785.CanonicalizationError) as exc:
        raise DomainError("invalid_request", message=f"Invalid {name}") from exc
    return value


def canonical(value: Any) -> bytes:
    return rfc8785.dumps(value)
