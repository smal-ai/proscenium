"""Project installed JSON Schemas into self-contained model tool parameters."""

from copy import deepcopy
from typing import Any

from proscenium.contracts import SCHEMA

_ANNOTATIONS = {"description", "title", "default", "examples"}
_UNEVALUATED = {"unevaluatedItems", "unevaluatedProperties"}


def _json_type(value: Any) -> str:
    # Exact types keep bool distinct from int.
    return {
        type(None): "null",
        bool: "boolean",
        int: "integer",
        str: "string",
        float: "number",
        list: "array",
        dict: "object",
    }[type(value)]


def inline_tool_schema(root: dict[str, Any]) -> dict[str, Any]:
    """Inline local definitions without changing the installed contract or literals."""

    def visit(node: Any, stack: tuple[str, ...]) -> Any:
        if not isinstance(node, dict):
            return node  # Boolean subschema.
        siblings: dict[str, Any] = {}
        for key, value in node.items():
            if key in {"$ref", "$defs", "$comment", "const"}:
                continue
            if key in {"properties", "patternProperties", "dependentSchemas"}:
                siblings[key] = {name: visit(child, stack) for name, child in value.items()}
            elif key in {"allOf", "anyOf", "oneOf", "prefixItems"}:
                siblings[key] = [visit(child, stack) for child in value]
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
                siblings[key] = visit(value, stack)
            else:
                siblings[key] = deepcopy(value)
        if "const" in node:
            value = node["const"]
            const_type = {"type": _json_type(value), "enum": [deepcopy(value)]}
            if siblings.keys() & const_type.keys():
                siblings.setdefault("allOf", []).append(const_type)
            else:
                siblings.update(const_type)
        if "$ref" not in node:
            return siblings

        reference = node["$ref"]
        prefix = "#/$defs/"
        if not reference.startswith(prefix):
            raise ValueError("Unsupported non-local normative tool reference")
        if reference in stack:
            raise ValueError(f"Cycle in tool schema references: {' -> '.join((*stack, reference))}")
        name = reference.removeprefix(prefix).replace("~1", "/").replace("~0", "~")
        resolved = visit(SCHEMA["$defs"][name], (*stack, reference))
        annotations = {key: siblings.pop(key) for key in siblings.keys() & _ANNOTATIONS}
        unevaluated = {key: siblings.pop(key) for key in siblings.keys() & _UNEVALUATED}
        # A non-overlapping sibling can still alter the referenced constraint.
        # For example, additionalProperties only sees properties in its own group.
        if siblings or unevaluated:
            # Unlike additionalProperties, unevaluated* consumes annotations
            # from its enclosing allOf, so it must remain outside each branch.
            branches = [resolved, siblings] if siblings else [resolved]
            return {"allOf": branches, **annotations, **unevaluated}
        if isinstance(resolved, dict):
            return {**resolved, **annotations}
        return {"allOf": [resolved], **annotations}

    result: dict[str, Any] = visit(root, ())
    return result
