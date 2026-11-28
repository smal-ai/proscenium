"""Verify immutable source closures in memory, without materialising renderer inputs."""

import json
from pathlib import Path
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.contracts import validate
from proscenium.sources.common import managed, relative, sha
from proscenium.sources.resolver import Resolver, SourceReaderServices, closure_digest


def read_closure_files(
    services: SourceReaderServices,
    project_id: str,
    closure: dict[str, Any],
    *,
    batch: dict[str, dict[str, Any]] | None = None,
) -> dict[str, bytes]:
    validate("SourceClosure", closure)
    if closure_digest(closure) != closure["digest"]:
        raise DomainError("hash_mismatch", 422)
    root = Path(services.registry.get(project_id)["path"])
    relative(root, closure["entry_path"])
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        for ref in closure["dependencies"]:
            dependency = (batch or {}).get(ref["version_id"]) or tx.get(
                "versions", ref["version_id"], required=False
            )
            if dependency is None:
                raise DomainError("dependency_missing", 422)
            if not isinstance(dependency, dict) or dependency.get("project_id") != project_id:
                raise DomainError("scope_denied", 403)
            if (
                dependency.get("item_id") != ref["item_id"]
                or dependency.get("version_id") != ref["version_id"]
            ):
                raise DomainError("source_conflict", 409)
        retained = {
            row["origin"]["artifact_id"]: tx.get(
                "retained_sources", row["origin"]["artifact_id"], required=False
            )
            for row in closure["files"]
            if row["origin"]["kind"] == "retained"
        }
    files: dict[str, bytes] = {}
    resolver = Resolver(services)
    # Validate the entire path set before reading any bytes.
    names: set[str] = set()
    for row in closure["files"]:
        name = row["destination"]
        relative(root, name)
        if name.split("/")[0] == ".receipt.json":
            raise DomainError("scope_denied", 403)
        if any(name.startswith(other + "/") or other.startswith(name + "/") for other in names):
            raise DomainError("dependency_collision", 409)
        names.add(name)
    if closure["entry_path"] not in names:
        raise DomainError("dependency_missing", 422)
    for row in closure["files"]:
        origin = row["origin"]
        if origin["kind"] == "git":
            data = resolver.git_bytes(project_id, origin)
        else:
            record = retained[origin["artifact_id"]]
            if not record or record["sha256"] != origin["sha256"]:
                raise DomainError("retained_source_missing", 422)
            path = managed(root, f".proscenium/retained/{origin['sha256']}/content")
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise DomainError("retained_source_missing", 422) from exc
        if sha(data) != origin["sha256"]:
            raise DomainError("hash_mismatch", 422)
        name = row["destination"]
        if name in files and files[name] != data:
            raise DomainError("dependency_collision", 409, destination=name)
        files[name] = data
    return files


def read_authored_json(
    services: SourceReaderServices,
    project_id: str,
    version: dict[str, Any],
    *,
    batch: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if version["project_id"] != project_id:
        raise DomainError("scope_denied", 403)
    closure = version["source"]
    files = read_closure_files(services, project_id, closure, batch=batch)
    try:
        document = json.loads(files[closure["entry_path"]])
        if not isinstance(document, dict):
            raise ValueError("Authored source must be an object")
        return document
    except (ValueError, UnicodeError) as exc:
        if version.get("kind") == "storyboard":
            raise DomainError(
                "source_unavailable",
                422,
                "Storyboard source must be AuthoredShotPlan JSON",
                safe_for_model=True,
            ) from exc
        raise DomainError(
            "source_unavailable", 422, "Authored catalogue source is not JSON", safe_for_model=True
        ) from exc
