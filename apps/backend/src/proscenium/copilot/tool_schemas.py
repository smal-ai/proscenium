"""Project installed JSON Schemas into portable model parameters, never validators."""

from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from proscenium.contracts import SCHEMA

_LIMIT_HINTS = {
    "multipleOf": "Multiple of {}.",
    "exclusiveMinimum": "Must be > {}.",
    "exclusiveMaximum": "Must be < {}.",
}
_KEYWORDS = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "description",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
}
_TYPES = {
    type(None): "null",
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
}
_MAX_PATTERN_LENGTH = 100
_SAFE_SEED = SCHEMA["$defs"]["VideoGenerationPrepare"]["properties"]["seed"]["pattern"]


def _inline_refs(node: Any, stack: tuple[str, ...]) -> Any:
    if not isinstance(node, dict):
        return node
    result = node.copy()
    if "properties" in result:
        result["properties"] = {
            name: _inline_refs(child, stack) for name, child in result["properties"].items()
        }
    if "items" in result:
        result["items"] = _inline_refs(result["items"], stack)
    for key in ("allOf", "anyOf", "oneOf"):
        if key in node:
            result[key] = [_inline_refs(branch, stack) for branch in node[key]]
    if reference := result.pop("$ref", None):
        prefix = "#/$defs/"
        if not reference.startswith(prefix):
            raise ValueError("Unsupported non-local normative tool reference")
        if reference in stack:
            raise ValueError(f"Cycle in tool schema references: {' -> '.join((*stack, reference))}")
        name = reference.removeprefix(prefix).replace("~1", "/").replace("~0", "~")
        resolved = _inline_refs(SCHEMA["$defs"][name], (*stack, reference))
        # A reference and its siblings are conjunctive, not replacements.
        result.setdefault("allOf", []).append(resolved)
    return result


def _merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Keep both sets of object fields and prefer existing scalar constraints."""
    for key, value in source.items():
        if key == "properties":
            properties = target.setdefault(key, {})
            for name, child in value.items():
                if name in properties and properties[name] != child:
                    properties[name] = {"allOf": [properties[name], child]}
                else:
                    properties[name] = child
        elif key == "required":
            target[key] = list(dict.fromkeys([*target.get(key, ()), *value]))
        else:
            target.setdefault(key, value)


def _choice_hint(branches: list[Any], *, exclusive: bool) -> str:
    groups = [branch.get("required") if isinstance(branch, dict) else None for branch in branches]
    if len(groups) < 2 or not all(groups):
        return ""
    choices = " or ".join(" and ".join(group) for group in groups if group)
    if (
        exclusive
        and len(groups) == 2
        and all(
            branch.get("not") == {"required": other}
            for branch, other in zip(branches, groups[::-1])
        )
    ):
        return f"Supply either {choices}, not both."
    return f"Supply {'one' if exclusive else 'at least one'} of: {choices}."


def _conjuncts(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for branch in node.get("allOf", ()):
        if isinstance(branch, dict) and not {"if", "then", "else"}.intersection(branch):
            yield from _conjuncts(branch)


def _project(node: Any, *, root: bool = False) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {}  # Boolean schemas have no portable counterpart.

    result: dict[str, Any] = {}
    hints: list[str] = []
    choices: list[Any] = []
    for part in _conjuncts(node):
        _merge(result, {key: part[key] for key in part if key in _KEYWORDS})
        if "const" in part:
            value = part["const"]
            result["enum"] = [value]
            result.setdefault("type", _TYPES[type(value)])
        hints.extend(
            template.format(part[key]) for key, template in _LIMIT_HINTS.items() if key in part
        )
        if part.get("uniqueItems") is True:
            hints.append("Items must be unique.")
        for key, deps in part.get("dependentRequired", {}).items():
            hints.append(f"'{key}' requires {', '.join(sorted(deps))}.")
        for key in ("anyOf", "oneOf"):
            if key in part:
                choices.extend(part[key])
                if root or key == "oneOf":
                    hints.append(_choice_hint(part[key], exclusive=key == "oneOf"))

    if len(result.get("pattern", "")) > _MAX_PATTERN_LENGTH:
        if result.pop("pattern") == _SAFE_SEED:
            result["pattern"] = "^[0-9]{1,16}$"
            hints.append("Decimal integer seed (0..9007199254740991).")
    description = result.pop("description", "")

    if root:
        # Root FunctionDeclarations are object envelopes, not unions. Keep any
        # branch-only fields visible but optional and do not narrow shared fields.
        if choices and result.get("type", "object") == "object":
            properties = result.setdefault("properties", {})
            for branch in choices:
                if isinstance(branch, dict):
                    for part in _conjuncts(branch):
                        for name, child in part.get("properties", {}).items():
                            properties.setdefault(name, child)
    elif choices:
        flat: list[dict[str, Any]] = []
        for choice in choices:
            branch = _project(choice)
            alternatives = branch.pop("anyOf", None)
            if alternatives is None:
                alternatives = [branch]
            elif branch:
                # Resolve sibling constraints before emitting an alternative.
                alternatives = [_project({"allOf": [branch, child]}) for child in alternatives]
            for alternative in alternatives:
                if alternative not in flat:
                    flat.append(alternative)
        result["anyOf"] = flat

    if "properties" in result:
        result["properties"] = {key: _project(child) for key, child in result["properties"].items()}
    if "items" in result:
        result["items"] = _project(result["items"])

    kinds = result.get("type")
    values = result.get("enum")
    if kinds is None and values:
        kinds = list(dict.fromkeys(_TYPES[type(value)] for value in values))
        if set(kinds) == {"integer", "number"}:
            kinds = ["number"]
        if len(kinds) == 1:
            result["type"] = kinds = kinds[0]
    if isinstance(kinds, list):
        result.pop("type", None)
        if not root:
            # A nullable scalar keeps its constraints on the non-null arm.
            union = []
            alternatives = result.pop("anyOf", [{}])
            for kind in kinds:
                constraints = (
                    {} if kind == "null" else {k: v for k, v in result.items() if k != "enum"}
                )
                if values is not None:
                    matching = [
                        v
                        for v in values
                        if _TYPES[type(v)] == kind or kind == "number" and type(v) is int
                    ]
                    if not matching:
                        continue
                    constraints["enum"] = matching
                for alternative in alternatives:
                    branch = _project({"allOf": [{"type": kind, **constraints}, alternative]})
                    if branch not in union:
                        union.append(branch)
            result = {"anyOf": union}
        else:
            # Published tool roots are objects. Other root unions are only hints.
            result.pop("enum", None)
    if result == {"type": "object"} and not description:
        hints.append("Arbitrary JSON object.")
    if text := " ".join(dict.fromkeys(filter(None, [description, *hints]))):
        result["description"] = text
    return result


def _branch_alternatives(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Expand a union branch into schemas with no descendant unions."""
    if "anyOf" in node:
        siblings = {key: value for key, value in node.items() if key != "anyOf"}
        for choice in node["anyOf"]:
            branch = _project({"allOf": [siblings, choice]}) if siblings else choice
            yield from _branch_alternatives(branch)
        return

    if "items" in node:
        first, *rest = list(_branch_alternatives(node["items"])) or [{}]
        # Array elements may choose different arms. Lifting the items union to
        # the array would wrongly require every element to choose the same arm.
        items = {
            key: value
            for key, value in first.items()
            if all(key in arm and arm[key] == value for arm in rest)
        }
        node = {**node, "items": items}

    branches = [node]
    for key, child in node.get("properties", {}).items():
        arms = list(_branch_alternatives(child))
        if arms != [child]:
            branches = [
                {**branch, "properties": {**branch["properties"], key: arm}}
                for branch in branches
                for arm in arms
            ]
    yield from branches


def _lift_descendant_unions(node: dict[str, Any]) -> None:
    for child in node.get("properties", {}).values():
        _lift_descendant_unions(child)
    if "items" in node:
        _lift_descendant_unions(node["items"])
    if "anyOf" in node:
        flat: list[dict[str, Any]] = []
        for branch in node["anyOf"]:
            for arm in _branch_alternatives(branch):
                if arm not in flat:
                    flat.append(arm)
        node["anyOf"] = flat


def portable_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Retain model-visible structure and hints in the portable provider subset."""
    projected = _project(_inline_refs(schema, ()), root=True)
    _lift_descendant_unions(projected)
    return deepcopy(projected)


def inline_tool_schema(root: dict[str, Any]) -> dict[str, Any]:
    """Inline installed definitions and project them for model-facing parameters."""
    return portable_tool_schema(root)
