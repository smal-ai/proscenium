"""Locate diagnostics in installed schemas without exposing dynamic object keys."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from proscenium.contracts import SCHEMA

_KEY = r"[A-Za-z_][A-Za-z_0-9]*"
_INDEX = r"\[[0-9]+\]"
_PATH = re.compile(rf"(?:{_KEY}|{_INDEX})(?:\.{_KEY}|{_INDEX})*")
_PART = re.compile(rf"{_KEY}|{_INDEX}")
_REDACTED = "field"


@dataclass(eq=False)
class _Location:
    properties: dict[str, _Location] = field(default_factory=dict)
    declared: set[str] = field(default_factory=set)
    patterns: list[tuple[re.Pattern[str], _Location]] = field(default_factory=list)
    additional: _Location | None = None
    items: _Location | None = None
    prefix: list[_Location] = field(default_factory=list)
    branches: list[_Location] = field(default_factory=list)


_LEAF = _Location()


def _literal(nodes: Sequence[_Location], name: str) -> list[_Location]:
    return [node.properties.get(name, _LEAF) for node in nodes if name in node.declared]


def _levels(locations: Sequence[_Location]) -> Iterator[_Location]:
    pending = list(locations)
    seen = set()
    while pending:
        location = pending.pop()
        if location not in seen:
            seen.add(location)
            pending.extend(location.branches)
            yield location


class SchemaPathScope:
    """Compiled property/array graph, with branches kept separate at each location."""

    def __init__(self, schemas: Sequence[dict[str, Any]]):
        self.roots = tuple(self._compile(root) for root in schemas)

    @staticmethod
    def _compile(root: dict[str, Any]) -> _Location:
        definitions = root.get("$defs", SCHEMA["$defs"])
        seen: dict[int, _Location] = {}

        def visit(schema: dict[str, Any] | bool) -> _Location:
            if id(schema) in seen:
                return seen[id(schema)]
            location = seen[id(schema)] = _Location()
            if not isinstance(schema, dict):
                return location
            deps = schema.get("dependentRequired", {})
            location.declared = {
                *schema.get("properties", {}),
                *schema.get("required", ()),
                *deps,
                *(key for names in deps.values() for key in names),
            }
            location.properties = {
                key: visit(child) for key, child in schema.get("properties", {}).items()
            }
            location.patterns = [
                (re.compile(pattern), visit(child))
                for pattern, child in schema.get("patternProperties", {}).items()
            ]
            if isinstance(schema.get("additionalProperties"), dict):
                location.additional = visit(schema["additionalProperties"])
            if "items" in schema:
                location.items = visit(schema["items"])
            location.prefix = [visit(child) for child in schema.get("prefixItems", ())]
            reference = schema.get("$ref", "")
            if reference.startswith("#/$defs/"):
                name = reference.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
                location.branches.append(visit(definitions[name]))
            location.branches.extend(
                visit(child) for key in ("anyOf", "oneOf", "allOf") for child in schema.get(key, ())
            )
            location.branches.extend(
                visit(schema[key]) for key in ("then", "else") if key in schema
            )
            location.branches.extend(
                visit(child) for child in schema.get("dependentSchemas", {}).values()
            )
            return location

        return visit(root)

    @staticmethod
    def _items(locations: Sequence[_Location], index: int) -> list[_Location]:
        return [
            child
            for node in _levels(locations)
            if (child := node.prefix[index] if index < len(node.prefix) else node.items) is not None
        ]

    def render(self, parts: Sequence[str | int], origins: Sequence[bool | None] = ()) -> str:
        """Render array indices and schema-declared keys, masking dynamic keys."""
        locations: Sequence[_Location] = self.roots
        path = ""
        for offset, part in enumerate(parts):
            origin = origins[offset] if offset < len(origins) else None
            if (
                isinstance(part, int)
                and origin is not False
                and (items := self._items(locations, part))
            ):
                path += f"[{part}]"
                locations = items
                continue
            key = str(part)
            nodes = list(_levels(locations))
            literal = _literal(nodes, key)
            if literal and origin is not False and re.fullmatch(_KEY, key):
                path += ("." if path else "") + key
                locations = literal
            else:
                path += ("." if path else "") + _REDACTED
                locations = []
                for node in nodes:
                    matches = [child for pattern, child in node.patterns if pattern.search(key)]
                    locations.extend(matches)
                    if not matches and key not in node.properties and node.additional is not None:
                        locations.append(node.additional)
        return path or "$"

    def accepts(self, path: str) -> bool:
        if path == "$":
            return bool(self.roots)
        if len(path) > 512 or not _PATH.fullmatch(path):
            return False
        locations: Sequence[_Location] = self.roots
        for part in _PART.findall(path):
            if part.startswith("["):
                locations = self._items(locations, int(part[1:-1]))
            else:
                nodes = list(_levels(locations))
                locations = _literal(nodes, part)
                if part == _REDACTED:
                    for node in nodes:
                        locations.extend(child for _, child in node.patterns)
                        if node.additional is not None:
                            locations.append(node.additional)
            if not locations:
                return False
        return True


def safe_schema_path(
    path: str, schemas: Sequence[dict[str, Any]], *, scope: SchemaPathScope | None = None
) -> bool:
    """Accept only structural paths in the failing tool's trusted schema family."""
    return (scope if scope is not None else SchemaPathScope(schemas)).accepts(path)


def path_origins(schema_path: Sequence[str | int]) -> list[bool | None]:
    """Distinguish literal properties from dynamic keys in jsonschema's error trace."""
    tokens = iter(schema_path)
    origins: list[bool | None] = []
    for token in tokens:
        if token in {
            "properties",
            "patternProperties",
            "prefixItems",
            "allOf",
            "anyOf",
            "oneOf",
            "dependentSchemas",
        }:
            next(tokens, None)  # Property, pattern, branch or item selector.
        if token == "properties":
            origins.append(True)
        elif token in {"patternProperties", "additionalProperties"}:
            origins.append(False)
        elif token in {"items", "prefixItems"}:
            origins.append(None)
    return origins
