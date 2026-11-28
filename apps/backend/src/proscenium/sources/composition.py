"""Verify direct, sealed inclusion evidence without walking the version graph."""

from collections.abc import Callable, Iterable
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.contracts import validate
from proscenium.db.paths import permit_project_parts
from proscenium.sources.common import RUNTIME_NAMES, SECRET_NAMES, fingerprint

MAX_INCLUDES = 128
MAX_DESTINATIONS = 4096
MAX_BYTES = 512 * 1024 * 1024
Lookup = Callable[[dict[str, Any]], dict[str, Any] | None]


def ref_key(ref: dict[str, str]) -> tuple[str, str]:
    return ref["item_id"], ref["version_id"]


def closure_digest(closure: dict[str, Any]) -> str:
    body = {k: v for k, v in closure.items() if k != "digest"}
    body["files"] = sorted(body["files"], key=lambda row: row["destination"])
    for key in ("dependencies", "included_sources"):
        if key in body:
            body[key] = sorted(body[key], key=ref_key)
    return fingerprint(body)


def closure_path(path: str) -> None:
    """Check a sealed path without consulting the mutable working tree."""
    parts = tuple(path.split("/"))
    if (
        any(c in path for c in (":", "\\", "\x00"))
        or any(p in {"", ".", ".."} or p.lower() in SECRET_NAMES | RUNTIME_NAMES for p in parts)
        or parts[0] == ".receipt.json"
    ):
        raise DomainError("scope_denied", 403)
    permit_project_parts(parts)


def destination_bounds(names: Iterable[str]) -> None:
    names = set(names)
    if len(names) > MAX_DESTINATIONS:
        raise DomainError(
            "invalid_request",
            422,
            "Composed source exceeds 4096 destinations; publish a smaller input",
            safe_for_model=True,
        )
    for name in sorted(names):
        parts = name.split("/")
        if any("/".join(parts[:i]) in names for i in range(1, len(parts))):
            raise DomainError("dependency_collision", 409, destination=name)


def byte_bound(size: int, remaining: int) -> None:
    if size > remaining:
        raise DomainError(
            "invalid_request",
            422,
            "Composed source exceeds 512 MiB of hydrated bytes; publish a smaller input",
            safe_for_model=True,
        )


def included_rows(
    refs: list[dict[str, str]],
    lookup: Lookup,
    project_id: str,
    repository_id: str,
    *,
    consuming_version: dict | None = None,
    producers: dict[str, dict] | None = None,
) -> dict[str, dict]:
    if len(refs) > MAX_INCLUDES:
        raise DomainError(
            "invalid_request",
            422,
            "Composed source accepts at most 128 direct includes; publish a smaller input",
            safe_for_model=True,
        )
    rows: dict[str, dict] = {}
    if producers is None:
        producers = {}
    for ref in sorted(refs, key=ref_key):
        version = lookup(ref)
        if version is None:
            raise DomainError(
                "dependency_missing",
                422,
                "Choose an existing published version in this project for source inclusion",
                safe_for_model=True,
                reference=ref,
                consuming_version=consuming_version,
            )
        if version.get("project_id") != project_id or ref_key(version) != ref_key(ref):
            raise DomainError("scope_denied", 403)
        source = validate("SourceClosure", version["source"])
        if source["digest"] != closure_digest(source):
            raise DomainError(
                "hash_mismatch", 422, reference=ref, consuming_version=consuming_version
            )
        entry = None
        for row in source["files"]:
            name, origin = row["destination"], row["origin"]
            closure_path(name)
            if origin["kind"] == "git":
                closure_path(origin["path"])
                if origin["repository_id"] != repository_id:
                    raise DomainError("scope_denied", 403)
            if name == source["entry_path"]:
                entry = origin
            if rows.setdefault(name, row) != row:
                first = producers[name]
                inclusion_collision(
                    name,
                    reference=ref,
                    consuming_version=consuming_version,
                    conflicting_references=[first, ref] if first != ref else None,
                )
            producers.setdefault(name, ref)
            if len(rows) > MAX_DESTINATIONS:
                destination_bounds(rows)
        if entry is None or (
            entry["kind"] == "git" and entry["commit"] != version["source_commit"]
        ):
            raise DomainError(
                "source_conflict", 409, reference=ref, consuming_version=consuming_version
            )
    destination_bounds(rows)
    return rows


def inclusion_collision(
    destination: str,
    *,
    reference: dict | None = None,
    consuming_version: dict | None = None,
    conflicting_references: list[dict] | None = None,
) -> None:
    raise DomainError(
        "dependency_collision",
        409,
        "Included source conflicts at this destination; change the owned path or publish a non-conflicting library",
        safe_for_model=True,
        destination=destination,
        reference=reference,
        consuming_version=consuming_version,
        conflicting_references=conflicting_references,
    )


def verify_included_sources(
    closure: dict[str, Any],
    lookup: Lookup,
    project_id: str,
    repository_id: str,
    *,
    consuming_version: dict | None = None,
    source_commit: str | None = None,
    producers: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """Return verified included destinations, not files from ordinary pins or lineage."""
    if "included_sources" not in closure:
        return {}
    validate("SourceClosure", closure)
    if closure_digest(closure) != closure["digest"]:
        raise DomainError("hash_mismatch", 422)
    refs = closure["included_sources"]
    if refs != sorted(refs, key=ref_key):
        raise DomainError(
            "invalid_request",
            422,
            "included_sources must be sorted by item_id and version_id",
            safe_for_model=True,
        )
    pins = {ref_key(ref) for ref in closure["dependencies"]}
    for ref in refs:
        if ref_key(ref) not in pins:
            raise DomainError(
                "dependency_missing",
                422,
                "Included sources must also be pinned in source dependencies",
                safe_for_model=True,
                reference=ref,
                consuming_version=consuming_version,
            )
    included = included_rows(
        refs,
        lookup,
        project_id,
        repository_id,
        consuming_version=consuming_version,
        producers=producers,
    )
    own: dict[str, dict] = {}
    for row in closure["files"]:
        name, origin = row["destination"], row["origin"]
        closure_path(name)
        if origin["kind"] == "git":
            closure_path(origin["path"])
            if origin["repository_id"] != repository_id:
                raise DomainError("scope_denied", 403)
        if name in own:
            inclusion_collision(name, consuming_version=consuming_version)
        own[name] = row
        if len(own) > MAX_DESTINATIONS:
            destination_bounds(own)
    destination_bounds(own)
    for name, row in included.items():
        if own.get(name) != row:
            raise DomainError(
                "dependency_missing",
                422,
                "Include every sealed producer file unchanged at its original destination",
                safe_for_model=True,
                destination=name,
                consuming_version=consuming_version,
            )
    entry = closure["entry_path"]
    if entry in included:
        inclusion_collision(entry, consuming_version=consuming_version)
    if entry not in own:
        raise DomainError(
            "dependency_missing", 422, destination=entry, consuming_version=consuming_version
        )
    origin = own[entry]["origin"]
    if source_commit is not None and origin["kind"] == "git" and origin["commit"] != source_commit:
        raise DomainError(
            "source_conflict",
            409,
            "Source closure must contain its entry_path, pinned to source_commit for a Git entry",
            safe_for_model=True,
            consuming_version=consuming_version,
        )
    return included
