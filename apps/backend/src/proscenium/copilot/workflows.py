"""Native-only argument envelopes, composed from the existing normative components."""

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from proscenium.api.errors import DomainError
from proscenium.catalogue.validation import (
    canonical_json_issues,
    schema_issues,
    schema_issues_for_schema,
)
from proscenium.contracts import SCHEMA, canonical, validate

from .tool_schemas import portable_tool_schema

WORKFLOW_TOOLS = {
    "assemble_sequence": (
        "assembly",
        "JobRecord",
        "Assemble an exact published sequence. Omit execution_profile_id to use the host's configured assembly profile. The host resolves and hashes its registered frames or clips, and audio. Returns an admitted job, not completed media.",
    ),
    "run_unity_workflow": (
        "unity",
        "JobRecord",
        "Import pinned Blender source or validate/build a published Unity result in its existing project. Uses configured executables only. Returns a tracked job; read get_unity_result after settlement.",
    ),
    "get_unity_result": (
        "unity_result",
        "UnityResult",
        "Read the actual UnityResult of a finished Unity workflow job. Running/uncertain jobs have no completed result. Does not launch or retry.",
    ),
}


def parameters(name: str) -> dict[str, Any]:
    return portable_tool_schema(validation_parameters(name))


def validation_parameters(name: str) -> dict[str, Any]:
    properties: dict[str, Any]
    if name == "get_unity_result":
        properties = {"job_id": {"$ref": "#/$defs/Id"}}
        required = ["job_id"]
    else:
        properties = {
            key: {"$ref": "#/$defs/Id"}
            for key in ("operation_id", "job_id", "execution_profile_id")
        }
        properties["version"] = {"$ref": "#/$defs/VersionRef"}
        required = list(properties)
        if name == "assemble_sequence":
            required.remove("execution_profile_id")
            properties["execution_profile_id"]["description"] = (
                "Omit to use the host's configured assembly profile."
            )
        elif name == "run_unity_workflow":
            properties.update(
                project={"$ref": "#/$defs/RelativePath"},
                spawn={"$ref": "#/$defs/Vector3"},
                camera={"enum": ["perspective", "orthographic"]},
                pipeline={"enum": ["builtin", "urp"]},
                action={"enum": ["import", "validate", "build"]},
                scene_path={"$ref": "#/$defs/RelativePath"},
            )
            required += ["project", "spawn", "camera", "pipeline", "action"]
    return dict(
        type="object",
        properties=properties,
        required=required,
        additionalProperties=False,
    )


def arguments(name: str, value: Any) -> dict[str, Any]:
    schema = validation_parameters(name)
    try:
        Draft202012Validator({**schema, "$defs": SCHEMA["$defs"]}).validate(value)
    except ValidationError as error:
        raise DomainError(
            "invalid_request", issues=schema_issues_for_schema(schema, value)
        ) from error
    try:
        canonical(value)
    except ValueError as error:
        raise DomainError("invalid_request", issues=canonical_json_issues(schema, value)) from error
    components = dict.fromkeys(("operation_id", "job_id", "execution_profile_id"), "Id")
    components.update(version="VersionRef", spawn="Vector3")
    for field, component in components.items():
        if field not in value:
            continue
        try:
            validate(component, value[field])
        except DomainError as error:
            issues = schema_issues(component, value[field])
            if not issues:
                raise
            for issue in issues:
                path = issue["path"]
                issue["path"] = (
                    field if path == "$" else field + (path if path.startswith("[") else f".{path}")
                )
            raise DomainError(error.code, error.status, error.message, issues=issues) from error
    return value
