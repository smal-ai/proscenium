"""Native-only argument envelopes, composed from the existing normative components."""

from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from proscenium.api.errors import DomainError
from proscenium.contracts import SCHEMA, canonical, validate

WORKFLOW_TOOLS = {
    "assemble_sequence": (
        "assembly",
        "JobRecord",
        "Assemble an exact published sequence. The host resolves and hashes its registered frames or clips, and audio. Returns an admitted job, not completed media.",
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
        if name == "run_unity_workflow":
            properties.update(
                project={"$ref": "#/$defs/RelativePath"},
                spawn={"$ref": "#/$defs/Vector3"},
                camera={"enum": ["perspective", "orthographic"]},
                pipeline={"enum": ["builtin", "urp"]},
                action={"enum": ["import", "validate", "build"]},
                scene_path={"$ref": "#/$defs/RelativePath"},
            )
            required += ["project", "spawn", "camera", "pipeline", "action"]
    names = {"Id", "VersionRef", "RelativePath", "Vector3"}
    return dict(
        type="object",
        properties=properties,
        required=required,
        additionalProperties=False,
        **{"$defs": {key: deepcopy(SCHEMA["$defs"][key]) for key in names}},
    )


def arguments(name: str, value: Any) -> dict[str, Any]:
    try:
        Draft202012Validator(parameters(name)).validate(value)
        canonical(value)
        for field in ("operation_id", "job_id", "execution_profile_id"):
            if field in value:
                validate("Id", value[field])
        if "version" in value:
            validate("VersionRef", value["version"])
        if "spawn" in value:
            validate("Vector3", value["spawn"])
    except (ValidationError, ValueError) as error:
        raise DomainError("invalid_request") from error
    return value
