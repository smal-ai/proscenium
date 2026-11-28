"""Hydrate declared local objects into independent sealed source snapshots."""

import json
import os
import re
import shutil
import stat
import subprocess
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import IO, Any, Protocol
from uuid import uuid4

from proscenium.api.errors import DomainError
from proscenium.contracts import canonical, validate
from proscenium.db import Store
from proscenium.db.registry import Registry
from proscenium.guidance import INCLUDED_RETAINED_SOURCE_MISSING, RETAINED_SOURCE_RECOVERY
from proscenium.sources import composition
from proscenium.sources.common import (
    atomic_bytes,
    git,
    git_environment,
    managed,
    mkdir_private,
    relative,
    sha,
)
from proscenium.sources.composition import closure_digest as closure_digest

LFS = re.compile(
    rb"version https://git-lfs.github.com/spec/v1\noid sha256:([a-f0-9]{64})\nsize ([0-9]+)\n?"
)


def checked_stream(
    stream: IO[bytes],
    remaining: int,
    expected_size: int,
    expected_sha: str | None,
    *,
    collect: bool = False,
    sink: IO[bytes] | None = None,
    reject_pointer: bool = False,
) -> tuple[int, bytes]:
    composition.byte_bound(expected_size, remaining)
    length = 0
    digest = sha256()
    output = BytesIO() if collect else None
    while block := stream.read(min(1024 * 1024, expected_size - length + 1)):
        if (
            reject_pointer
            and length == 0
            and block.startswith(b"version https://git-lfs.github.com/spec/v1")
        ):
            raise DomainError(
                "source_unavailable", 422, "Invalid Git LFS pointer", safe_for_model=True
            )
        length += len(block)
        composition.byte_bound(length, remaining)
        if length > expected_size:
            raise DomainError("hash_mismatch", 422)
        digest.update(block)
        if output is not None:
            output.write(block)
        if sink is not None:
            sink.write(block)
    actual = digest.hexdigest()
    if length != expected_size or (expected_sha is not None and actual != expected_sha):
        raise DomainError("hash_mismatch", 422)
    if output is not None:
        return length, output.getvalue()
    # Admission hashes owned worktree files before a frozen SHA is available.
    return length, actual.encode("ascii") if expected_sha is None else b""


def checked_file(
    path: Path,
    remaining: int,
    expected_sha: str | None,
    *,
    expected_size: int | None = None,
    collect: bool = False,
    sink: IO[bytes] | None = None,
    reject_pointer: bool = False,
) -> tuple[int, bytes]:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise DomainError("scope_denied", 403)
        if expected_size is not None and info.st_size != expected_size:
            raise DomainError("hash_mismatch", 422)
        return checked_stream(
            stream,
            remaining,
            info.st_size,
            expected_sha,
            collect=collect,
            sink=sink,
            reject_pointer=reject_pointer,
        )


class SourceReaderServices(Protocol):
    @property
    def registry(self) -> Registry: ...


class Resolver:
    def __init__(self, services: SourceReaderServices):
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
            try:
                obj = self.lfs_object(root, digest)
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

    @staticmethod
    def lfs_object(root: Path, digest: str) -> Path:
        # Ask local Git LFS for its configured object store. No implicit network operation.
        environment = git(root, "lfs", "env").decode()
        media = next(
            (line[14:] for line in environment.splitlines() if line.startswith("LocalMediaDir=")),
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
        if obj.is_symlink() or not obj.resolve().is_relative_to(Path(media).resolve()):
            raise DomainError("scope_denied", 403)
        return obj

    def bounded_origin(
        self,
        project_id: str,
        root: Path,
        origin: dict,
        remaining: int,
        *,
        retained: dict | None = None,
        collect: bool = False,
        sink: IO[bytes] | None = None,
        commits: set[str] | None = None,
        destination: str | None = None,
        reference: dict | None = None,
    ) -> tuple[int, bytes]:
        """Check object size before hydration; stream the exact bytes and hash in chunks."""
        digest = origin["sha256"]
        if origin["kind"] == "retained":
            try:
                if not retained or retained["sha256"] != digest:
                    raise DomainError("retained_source_missing", 422)
                path = managed(root, f".proscenium/retained/{digest}/content")
                return checked_file(path, remaining, digest, collect=collect, sink=sink)
            except (OSError, DomainError) as exc:
                if isinstance(exc, DomainError) and exc.code not in {
                    "retained_source_missing",
                    "hash_mismatch",
                    "scope_denied",
                }:
                    raise
                if reference is not None:
                    raise DomainError(
                        "retained_source_missing",
                        422,
                        INCLUDED_RETAINED_SOURCE_MISSING,
                        safe_for_model=True,
                        destination=destination,
                        reference=reference,
                    ) from exc
                raise DomainError(
                    "retained_source_missing",
                    422,
                    RETAINED_SOURCE_RECOVERY,
                    safe_for_model=True,
                    destination=destination,
                ) from exc

        object_id, size, data, lfs_digest = self.git_source_metadata(
            project_id, root, origin, commits
        )
        composition.byte_bound(size, remaining)
        if lfs_digest is not None:
            if lfs_digest != digest:
                raise DomainError("hash_mismatch", 422)
            try:
                return checked_file(
                    self.lfs_object(root, digest),
                    remaining,
                    digest,
                    expected_size=size,
                    collect=collect,
                    sink=sink,
                )
            except OSError as exc:
                raise DomainError(
                    "source_unavailable",
                    422,
                    "Local Git LFS object missing",
                    sha256=digest,
                    safe_for_model=True,
                ) from exc
        if data is not None:
            return checked_stream(
                BytesIO(data), remaining, size, digest, collect=collect, sink=sink
            )
        with subprocess.Popen(
            ["git", "-C", str(root), "cat-file", "blob", object_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=git_environment(),
        ) as process:
            assert process.stdout is not None
            try:
                result = checked_stream(
                    process.stdout,
                    remaining,
                    size,
                    digest,
                    collect=collect,
                    sink=sink,
                    reject_pointer=True,
                )
            finally:
                process.stdout.close()
            if process.wait(timeout=60):
                raise DomainError("source_unavailable", 422)
        return result

    def git_source_metadata(
        self, project_id: str, root: Path, origin: dict, commits: set[str] | None = None
    ) -> tuple[str, int, bytes | None, str | None]:
        """Return the blob ID and declared hydrated size without reading bulk content."""
        if origin["repository_id"] != self.services.registry.get(project_id)["repository_id"]:
            raise DomainError("scope_denied", 403)
        composition.closure_path(origin["path"])
        commit = origin["commit"]
        if commits is None or commit not in commits:
            if git(root, "cat-file", "-t", commit).strip() != b"commit":
                raise DomainError("source_unavailable", 422)
            if commits is not None:
                commits.add(commit)
        lines = git(
            root, "--literal-pathspecs", "ls-tree", "-z", origin["commit"], "--", origin["path"]
        ).split(b"\0")
        if len(lines) != 2 or not lines[0]:
            raise DomainError("source_unavailable", 422)
        metadata, name = lines[0].split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if (
            mode not in {b"100644", b"100755"}
            or kind != b"blob"
            or name != os.fsencode(origin["path"])
        ):
            raise DomainError("scope_denied", 403)
        object_id = oid.decode()
        size = int(git(root, "cat-file", "-s", object_id))
        if size <= 1024:  # Git LFS pointer blobs intentionally fit this limit.
            data = git(root, "cat-file", "blob", object_id)
            if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
                match = LFS.fullmatch(data)
                if not match:
                    raise DomainError(
                        "source_unavailable", 422, "Invalid Git LFS pointer", safe_for_model=True
                    )
                return object_id, int(match[2]), None, match[1].decode()
            return object_id, size, data, None
        return object_id, size, None, None

    def _verify_closure(
        self,
        project_id: str,
        closure: dict[str, Any],
        *,
        batch: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[Path, dict[str, bytes]]:
        if "included_sources" in closure:
            return self.verify_composed(project_id, closure, batch=batch)
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

    def verify_composed(
        self,
        project_id: str,
        closure: dict[str, Any],
        *,
        batch: dict[str, dict[str, Any]] | None = None,
        collect: bool = True,
        entry_only: bool = False,
        target: Path | None = None,
        store: Store | None = None,
        source_commit: str | None = None,
    ) -> tuple[Path, dict[str, bytes]]:
        record = self.services.registry.get(project_id)
        root = Path(record["path"])
        if "included_sources" not in closure:
            raise DomainError("invalid_request", 422)
        with (store or self.services.registry.project_store(project_id)).transaction(
            write=False
        ) as tx:

            def lookup(ref: dict) -> dict | None:
                return (batch or {}).get(ref["version_id"]) or tx.get(
                    "versions", ref["version_id"], required=False
                )

            producers: dict[str, dict] = {}
            composition.verify_included_sources(
                closure,
                lookup,
                project_id,
                record["repository_id"],
                source_commit=source_commit,
                producers=producers,
            )
            for ref in closure["dependencies"]:
                dependency = lookup(ref)
                if dependency is None:
                    raise DomainError("dependency_missing", 422, version_id=ref["version_id"])
                if dependency.get("project_id") != project_id or composition.ref_key(
                    dependency
                ) != composition.ref_key(ref):
                    raise DomainError("scope_denied", 403)
            retained = {
                row["origin"]["artifact_id"]: tx.get(
                    "retained_sources", row["origin"]["artifact_id"], required=False
                )
                for row in closure["files"]
                if row["origin"]["kind"] == "retained"
            }
        files = {}
        remaining = composition.MAX_BYTES
        commits: set[str] = set()
        for row in closure["files"]:
            name, origin = row["destination"], row["origin"]
            retained_record = retained.get(origin.get("artifact_id"))
            if target is None:
                keep = collect and (not entry_only or name == closure["entry_path"])
                size, data = self.bounded_origin(
                    project_id,
                    root,
                    origin,
                    remaining,
                    retained=retained_record,
                    collect=keep,
                    commits=commits,
                    destination=name,
                    reference=producers.get(name),
                )
                if keep:
                    files[name] = data
            else:
                path = target / name
                mkdir_private(path.parent)
                with path.open("xb") as stream:
                    size, _ = self.bounded_origin(
                        project_id,
                        root,
                        origin,
                        remaining,
                        retained=retained_record,
                        sink=stream,
                        commits=commits,
                        destination=name,
                        reference=producers.get(name),
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                checked_file(path, size, origin["sha256"], expected_size=size)
                path.chmod(0o400)
            remaining -= size
        return root, files

    def resolve(
        self,
        project_id: str,
        closure: dict[str, Any],
        *,
        batch: dict[str, dict[str, Any]] | None = None,
        source_commit: str | None = None,
    ) -> Path:
        if "included_sources" in closure:
            validate("SourceClosure", closure)
            root = Path(self.services.registry.get(project_id)["path"])
            base = managed(root, ".proscenium/snapshots/" + closure["digest"])
            mkdir_private(base)
            target = base / str(uuid4())
            target.mkdir(mode=0o700)
            try:
                self.verify_composed(
                    project_id,
                    closure,
                    batch=batch,
                    target=target,
                    collect=False,
                    source_commit=source_commit,
                )
                hashes = {row["destination"]: row["origin"]["sha256"] for row in closure["files"]}
                self.seal(target, closure, hashes)
            except BaseException:
                shutil.rmtree(target)
                raise
            return target
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
        self.seal(target, closure, hashes)
        return target

    @staticmethod
    def seal(target: Path, closure: dict, hashes: dict) -> None:
        atomic_bytes(target / ".receipt.json", canonical({"closure": closure, "files": hashes}))
        for directory in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True):
            directory.chmod(0o500)
        target.chmod(0o500)

    def materialise(self, project_id: str, version_id: str) -> Path:
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            version = tx.require("versions", version_id)
        return self.resolve(
            project_id, version["source"], source_commit=version.get("source_commit")
        )

    def authored(self, project_id: str, version: dict[str, Any]) -> dict[str, Any]:
        closure = version["source"]
        if "included_sources" in closure:
            _, files = self.verify_composed(
                project_id, closure, entry_only=True, source_commit=version.get("source_commit")
            )
        else:
            _, files = self._verify_closure(project_id, closure)
        try:
            document = json.loads(files[closure["entry_path"]])
            if not isinstance(document, dict):
                raise ValueError("Authored source must be an object")
            return document
        except ValueError as exc:
            raise DomainError("source_unavailable", 422) from exc
