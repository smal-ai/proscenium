"""Hydrate declared local objects into independent sealed source snapshots."""

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from proscenium.api.errors import DomainError
from proscenium.contracts import canonical, validate
from proscenium.services import Services
from proscenium.sources.common import (
    atomic_bytes,
    fingerprint,
    git,
    managed,
    mkdir_private,
    relative,
    sha,
)

LFS = re.compile(
    rb"version https://git-lfs.github.com/spec/v1\noid sha256:([a-f0-9]{64})\nsize ([0-9]+)\n?"
)


def closure_digest(closure: dict[str, Any]) -> str:
    body = {k: v for k, v in closure.items() if k != "digest"}
    body["files"] = sorted(body["files"], key=lambda row: row["destination"])
    body["dependencies"] = sorted(
        body["dependencies"], key=lambda row: (row["item_id"], row["version_id"])
    )
    return fingerprint(body)


class Resolver:
    def __init__(self, services: Services):
        self.services = services

    def git_bytes(
        self, project_id: str, origin: dict[str, Any], *, root: Path | None = None
    ) -> bytes:
        record = self.services.registry.get(project_id)
        if origin["repository_id"] != record["repository_id"]:
            raise DomainError("scope_denied", 403)
        root = root or Path(record["path"])
        relative(root, origin["path"])
        commit = origin["commit"]
        tree = git(root, "ls-tree", "-z", commit, "--", origin["path"])
        rows = [row for row in tree.split(b"\0") if row]
        if len(rows) != 1:
            raise DomainError("source_unavailable", 422)
        metadata, name = rows[0].split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if mode not in {b"100644", b"100755"} or kind != b"blob" or name.decode() != origin["path"]:
            raise DomainError("scope_denied", 403)
        content = git(root, "cat-file", "blob", oid.decode())
        if content.startswith(b"version https://git-lfs.github.com/spec/v1"):
            match = LFS.fullmatch(content)
            if not match:
                raise DomainError(
                    "source_unavailable", 422, "Invalid Git LFS pointer", safe_for_model=True
                )
            digest = match[1].decode()
            # Ask local Git LFS for its configured object store. No implicit network operation.
            environment = git(root, "lfs", "env").decode()
            media = next(
                (
                    line[14:]
                    for line in environment.splitlines()
                    if line.startswith("LocalMediaDir=")
                ),
                "",
            )
            if not media:
                raise DomainError("source_unavailable", 422)
            common = Path(git(root, "rev-parse", "--git-common-dir").decode().strip())
            common = common if common.is_absolute() else root / common
            if not Path(media).resolve().is_relative_to(common.resolve()):
                raise DomainError(
                    "scope_denied",
                    403,
                    "Git LFS storage is outside registered repository storage",
                    safe_for_model=True,
                )
            obj = Path(media) / digest[:2] / digest[2:4] / digest
            try:
                if obj.is_symlink() or not obj.resolve().is_relative_to(Path(media).resolve()):
                    raise DomainError("scope_denied", 403)
                content = obj.read_bytes()
            except OSError as exc:
                raise DomainError(
                    "source_unavailable",
                    422,
                    "Local Git LFS object missing",
                    sha256=digest,
                    safe_for_model=True,
                ) from exc
            if sha(content) != digest or len(content) != int(match[2]):
                raise DomainError("hash_mismatch", 422)
        if sha(content) != origin["sha256"]:
            raise DomainError("hash_mismatch", 422)
        return content

    def _verify_closure(
        self,
        project_id: str,
        closure: dict[str, Any],
        *,
        batch: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[Path, dict[str, bytes]]:
        validate("SourceClosure", closure)
        root = Path(self.services.registry.get(project_id)["path"])
        relative(root, closure["entry_path"])
        if closure_digest(closure) != closure["digest"]:
            raise DomainError("hash_mismatch", 422)
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            for ref in closure["dependencies"]:
                dependency = (batch or {}).get(ref["version_id"]) or tx.get(
                    "versions", ref["version_id"], required=False
                )
                if not dependency:
                    raise DomainError("dependency_missing", 422, version_id=ref["version_id"])
                if dependency["item_id"] != ref["item_id"]:
                    raise DomainError("scope_denied", 403)
        files: dict[str, bytes] = {}
        for row in closure["files"]:
            destination, origin = row["destination"], row["origin"]
            relative(root, destination)
            if destination.split("/")[0] == ".receipt.json":
                raise DomainError("scope_denied", 403)
            if origin["kind"] == "git":
                data = self.git_bytes(project_id, origin)
            else:
                with self.services.registry.project_store(project_id).transaction(
                    write=False
                ) as tx:
                    retained = tx.get("retained_sources", origin["artifact_id"], required=False)
                if not retained or retained["sha256"] != origin["sha256"]:
                    raise DomainError(
                        "retained_source_missing",
                        422,
                        artifact_id=origin["artifact_id"],
                        sha256=origin["sha256"],
                    )
                path = managed(root, f".proscenium/retained/{origin['sha256']}/content")
                try:
                    data = path.read_bytes()
                except OSError as exc:
                    raise DomainError(
                        "retained_source_missing",
                        422,
                        artifact_id=origin["artifact_id"],
                        sha256=origin["sha256"],
                    ) from exc
                if sha(data) != origin["sha256"]:
                    raise DomainError(
                        "retained_source_missing",
                        422,
                        artifact_id=origin["artifact_id"],
                        sha256=origin["sha256"],
                    )
            if destination in files and files[destination] != data:
                raise DomainError("dependency_collision", 409, destination=destination)
            files[destination] = data
        if closure["entry_path"] not in files:
            raise DomainError("dependency_missing", 422, path=closure["entry_path"])
        for name in files:
            if any(other.startswith(name + "/") for other in files):
                raise DomainError("dependency_collision", 409, destination=name)
        return root, files

    def resolve(
        self,
        project_id: str,
        closure: dict[str, Any],
        *,
        batch: dict[str, dict[str, Any]] | None = None,
    ) -> Path:
        root, files = self._verify_closure(project_id, closure, batch=batch)
        base = managed(root, ".proscenium/snapshots/" + closure["digest"])
        mkdir_private(base)
        # A fresh tree per materialisation prevents sharing writable renderer inputs.
        target = base / str(uuid4())
        target.mkdir(mode=0o700)
        for name, data in files.items():
            atomic_bytes(target / name, data)
        hashes = {name: sha(relative(target, name, exists=True).read_bytes()) for name in files}
        if hashes != {name: sha(data) for name, data in files.items()}:
            raise DomainError("hash_mismatch", 422)
        atomic_bytes(target / ".receipt.json", canonical({"closure": closure, "files": hashes}))
        for directory in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True):
            directory.chmod(0o500)
        target.chmod(0o500)
        return target

    def materialise(self, project_id: str, version_id: str) -> Path:
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            version = tx.require("versions", version_id)
        return self.resolve(project_id, version["source"])

    def authored(self, project_id: str, version: dict[str, Any]) -> dict[str, Any]:
        closure = version["source"]
        _, files = self._verify_closure(project_id, closure)
        try:
            document = json.loads(files[closure["entry_path"]])
            if not isinstance(document, dict):
                raise ValueError("Authored source must be an object")
            return document
        except ValueError as exc:
            raise DomainError("source_unavailable", 422) from exc
