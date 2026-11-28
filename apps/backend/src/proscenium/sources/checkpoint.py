"""Durable checkpoint intent, isolated index and atomic branch/checkpoint-ref CAS."""

import fcntl
import os
import select
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import validate
from proscenium.db import Transaction
from proscenium.db.store import now
from proscenium.services import Services
from proscenium.sources import composition
from proscenium.sources.common import (
    active,
    admitted,
    binding,
    fingerprint,
    git,
    git_environment,
    managed,
    relative,
    replay,
    sha,
)
from proscenium.sources.resolver import Resolver, checked_file, closure_digest

INHERITED_ENTRY_CONFLICT = "Inherited entry_path does not match HEAD; include entry_path in paths"
BINARY_REQUIRES_LFS = "Binary authoring files require Git LFS tracking"
BINARY_SUFFIXES = frozenset(
    {
        ".blend",
        ".fbx",
        ".glb",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".exr",
        ".hdr",
        ".mp4",
        ".tga",
        ".tif",
        ".tiff",
        ".obj",
        ".mtl",
        ".abc",
        ".usd",
        ".usda",
        ".usdc",
        ".usdz",
        ".zip",
        ".7z",
    }
)


@contextmanager
def checkpoint_index(root: Path, parent: str | None) -> Iterator[dict[str, str]]:
    descriptor, name = tempfile.mkstemp(prefix="checkpoint-index-", dir=root / ".proscenium")
    os.close(descriptor)
    index = Path(name)
    index.unlink()  # Git requires absent or valid index, not an empty file.
    env = {"GIT_INDEX_FILE": name}
    try:
        git(root, "read-tree", parent or "--empty", env=env)
        yield env
    finally:
        index.unlink(missing_ok=True)
        Path(name + ".lock").unlink(missing_ok=True)


def require_lfs_tracking(root: Path, paths: list[str], parent: str | None) -> None:
    binaries = [os.fsencode(path) for path in paths if Path(path).suffix.lower() in BINARY_SUFFIXES]
    if not binaries:
        return
    for path in binaries:
        relative(root, os.fsdecode(path), exists=True)
    # Match hash-object's worktree attributes and parent-index fallback, not the
    # owner's unrelated staged .gitattributes. No clean filter runs here.
    with checkpoint_index(root, parent) as env:
        actual = git(
            root,
            "check-attr",
            "-z",
            "--stdin",
            "filter",
            env=env,
            data=b"\0".join(binaries) + b"\0",
        )
    if actual != b"".join(path + b"\0filter\0lfs\0" for path in binaries):
        raise DomainError("source_conflict", 409, BINARY_REQUIRES_LFS, safe_for_model=True)


@contextmanager
def prepared_branch(root: Path, branch: str, commit: str) -> Iterator[bool]:
    """Hold Git's ref lock without committing the verify-only transaction."""
    with subprocess.Popen(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), "update-ref", "--stdin"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=git_environment(),
        bufsize=0,
    ) as process:
        assert process.stdin is not None and process.stdout is not None
        stdout_fd = process.stdout.fileno()
        deadline = time.monotonic() + 5

        def acknowledge(expected: bytes) -> bool:
            remaining = expected
            while remaining:
                timeout = max(0, deadline - time.monotonic())
                if not select.select([stdout_fd], [], [], timeout)[0]:
                    return False
                data = os.read(stdout_fd, len(remaining))
                if not data or not remaining.startswith(data):
                    return False
                remaining = remaining[len(data) :]
            return True

        prepared = False
        try:
            try:
                process.stdin.write(f"start\nverify {branch} {commit}\nprepare\n".encode())
                prepared = acknowledge(b"start: ok\n") and acknowledge(b"prepare: ok\n")
            except BrokenPipeError:
                pass  # The ref moved or another Git writer holds its lock.
            yield prepared
        finally:
            # Also abort an unacknowledged transaction. EOF is the fallback if Git exited.
            try:
                process.stdin.write(b"abort\n")
            except BrokenPipeError:
                pass
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()


class Checkpoints:
    def __init__(self, services: Services, resolver: Resolver):
        self.services, self.resolver = services, resolver

    def checkpoint(self, caller: Caller, request: dict[str, Any]) -> dict[str, Any]:
        validate("CheckpointRequest", request)
        paths = request["paths"]
        project_id, identity = request["project_id"], request["operation_id"]
        root = active(self.services, caller, "checkpoint_sources", project_id)
        store = self.services.registry.project_store(project_id)
        normalized = {**request, "paths": sorted(paths)}
        legacy_binding = binding(caller, normalized)
        normalized = dict(normalized)
        for key in ("dependencies", "include_sources"):
            if key in normalized:
                normalized[key] = sorted(normalized[key], key=composition.ref_key)
        sorted_binding = binding(caller, normalized)
        lockpath = store.path.parent / "checkpoint.lock"
        with os.fdopen(
            os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a+b"
        ) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with store.transaction() as tx:
                intent = tx.get("checkpoint_intents", identity, required=False)
                recovering = intent is not None
                immutable = (
                    sorted_binding
                    if intent is None or intent.get("sorted_ref_binding") is True
                    else legacy_binding
                )
                if (
                    intent is not None
                    and intent.get("modern_binding") is not True
                    and intent.get("sorted_ref_binding") is not True
                ):
                    # The oldest format is invocation-bound. Never try the
                    # newer normalised binding against one of these intents.
                    previous = self._legacy_completed_replay(tx, root, caller, request, identity)
                    if previous is None:
                        raise DomainError("idempotency_conflict", 409)
                else:
                    previous = replay(tx, "sources.checkpoint", identity, immutable)
                if previous:
                    saved = tx.require("checkpoint_intents", identity)
                    reference = "refs/proscenium/checkpoints/" + sha(identity.encode())
                    committed = git(root, "rev-parse", "--verify", reference).decode().strip()
                    if committed != previous["source_commit"]:
                        raise DomainError("source_conflict", 409)
                    closure = self.verify(project_id, request, saved, committed)
                    if closure is None:
                        closure = self.source_closure(project_id, root, request, saved, committed)
                    if previous.get("source_closure") != closure:
                        raise DomainError("source_conflict", 409)
                    validate("CheckpointReceipt", previous)
                    self.sync_index(root, saved, committed)
                    return {**previous, "replayed": True}
                if intent and intent["fingerprint"] != fingerprint(immutable):
                    raise DomainError("idempotency_conflict", 409)
                if not intent:
                    admitted(self.services, tx, caller)
                    branch = git(root, "symbolic-ref", "-q", "HEAD").decode().strip()
                    head = (
                        git(root, "rev-parse", "--verify", "HEAD", required=False).decode().strip()
                        or None
                    )
                    if head != request["expected_head"] or not branch.startswith("refs/heads/"):
                        raise DomainError("source_conflict", 409)
                    inherited, includes = self.validate_closure_inputs(
                        project_id, root, request, head
                    )
                    if len(set(paths)) != len(paths):
                        raise DomainError("invalid_request")
                    require_lfs_tracking(root, paths, head)
                    files = (
                        self.composed_input_files(project_id, root, request, inherited, head)
                        if includes
                        else [
                            {"path": p, "sha256": sha(relative(root, p, exists=True).read_bytes())}
                            for p in sorted(paths)
                        ]
                    )
                    # Author identity and date are frozen before any Git object/ref changes.
                    author = git(root, "var", "GIT_AUTHOR_IDENT").decode().strip()
                    intent = dict(
                        fingerprint=fingerprint(immutable),
                        modern_binding=True,
                        sorted_ref_binding=True,
                        branch=branch,
                        files=files,
                        parent=head,
                        date=now(),
                        author=author,
                        state="prepared",
                        modes={
                            p: "100755"
                            if relative(root, p, exists=True).stat().st_mode & 0o111
                            else "100644"
                            for p in paths
                        },
                    )
                    if includes:
                        intent["composition"] = self.composition_fingerprint(inherited, includes)
                    tx.put("checkpoint_intents", identity, intent)
            reference = "refs/proscenium/checkpoints/" + sha(identity.encode())
            commit = git(root, "rev-parse", "--verify", reference, required=False).decode().strip()
            if not commit:
                if intent["state"] == "ref_outcome_uncertain":
                    return self.uncertain(request, intent, replayed=True)
                # Recovery can re-use a prepared immutable candidate, but never invent another
                # commit after a successful ref transaction whose acknowledgement was lost.
                if "candidate" in intent:
                    commit = intent["candidate"]
                else:
                    commit, tree = self.prepare(root, request, intent)
                    with store.transaction() as tx:
                        intent.update(candidate=commit, tree=tree, state="outcome_uncertain")
                        tx.put(
                            "checkpoint_intents",
                            identity,
                            intent,
                            expected_revision=tx.revision("checkpoint_intents", identity),
                        )
                self.verify(project_id, request, intent, commit)
                current = (
                    git(root, "rev-parse", "--verify", "HEAD", required=False).decode().strip()
                    or None
                )
                if (
                    current != intent["parent"]
                    or git(root, "symbolic-ref", "-q", "HEAD").decode().strip() != intent["branch"]
                ):
                    raise DomainError("source_conflict", 409)
                for row in intent["files"]:
                    path = relative(root, row["path"], exists=True)
                    if "composition" in intent:
                        try:
                            checked_file(path, composition.MAX_BYTES, row["sha256"])
                        except DomainError as exc:
                            raise DomainError("source_conflict", 409) from exc
                    elif sha(path.read_bytes()) != row["sha256"]:
                        raise DomainError("source_conflict", 409)
                zero = "0" * 40
                commands = (
                    f"start\nupdate {intent['branch']} {commit} {intent['parent'] or zero}\n"
                    f"create {reference} {commit}\nprepare\ncommit\n"
                )
                try:
                    git(root, "update-ref", "--stdin", data=commands.encode())
                except (DomainError, subprocess.TimeoutExpired):
                    observed = (
                        git(root, "rev-parse", "--verify", reference, required=False)
                        .decode()
                        .strip()
                    )
                    if observed != commit:
                        with store.transaction() as tx:
                            saved = tx.require("checkpoint_intents", identity)
                            tx.put(
                                "checkpoint_intents",
                                identity,
                                {**saved, "state": "ref_outcome_uncertain"},
                                expected_revision=tx.revision("checkpoint_intents", identity),
                            )
                        return self.uncertain(request, intent, replayed=False)
            closure = self.verify(project_id, request, intent, commit)
            receipt = dict(
                schema_version="1.0",
                operation_id=identity,
                project_id=project_id,
                state="completed",
                source_commit=commit,
                parent_commit=intent["parent"],
                files=intent["files"],
                replayed=False,
            )
            if closure is None:
                closure = self.source_closure(project_id, root, request, intent, commit)
            if closure is not None:
                receipt["source_closure"] = closure
            validate("CheckpointReceipt", receipt)
            self.sync_index(root, intent, commit)
            with store.transaction() as tx:
                result, repeated = tx.operation(
                    "sources.checkpoint", identity, immutable, lambda: receipt
                )
                old = tx.require("checkpoint_intents", identity)
                tx.put(
                    "checkpoint_intents",
                    identity,
                    {**old, "state": "completed", "receipt": result},
                    expected_revision=tx.revision("checkpoint_intents", identity),
                )
            return {**result, "replayed": repeated or recovering}

    @staticmethod
    def version(tx: Transaction, project_id: str, ref: dict[str, Any]) -> dict[str, Any]:
        version = tx.get("versions", ref["version_id"], required=False)
        if not version:
            raise DomainError("dependency_missing", 422, version_id=ref["version_id"])
        if version["project_id"] != project_id or version["item_id"] != ref["item_id"]:
            raise DomainError("scope_denied", 403)
        return version

    @staticmethod
    def closure_path(root: Path, path: str) -> None:
        composition.closure_path(path)
        relative(root, path)

    def validate_closure_inputs(
        self, project_id: str, root: Path, request: dict[str, Any], parent: str | None
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
        if "entry_path" not in request:
            if any(
                key in request
                for key in (
                    "dependencies",
                    "required_runtime",
                    "retained_files",
                    "base_version",
                    "exclude_paths",
                    "include_sources",
                )
            ):
                raise DomainError(
                    "invalid_request",
                    message="entry_path is required when building a source closure",
                    safe_for_model=True,
                )
            return {}, []
        self.closure_path(root, request["entry_path"])
        if "exclude_paths" in request and "base_version" not in request:
            raise DomainError(
                "invalid_request",
                message="exclude_paths requires base_version",
                safe_for_model=True,
            )
        excluded = set(request.get("exclude_paths", []))
        for name in sorted(excluded):
            self.closure_path(root, name)
        retained_files = request.get("retained_files", [])
        supplied = list(request["paths"]) + [row["destination"] for row in retained_files]
        seen: set[str] = set()
        for name in supplied:
            self.closure_path(root, name)
            if name in seen or name in excluded:
                raise DomainError("dependency_collision", 409, destination=name)
            seen.add(name)
        inherited: dict[str, dict[str, Any]] = {}
        includes = request.get("include_sources", [])
        repository_id = self.services.registry.get(project_id)["repository_id"]
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:

            def lookup(ref: dict) -> dict | None:
                return tx.get("versions", ref["version_id"], required=False)

            if "base_version" in request:
                version = self.version(tx, project_id, request["base_version"])
                base = version["source"]
                validate("SourceClosure", base)
                if closure_digest(base) != base["digest"]:
                    raise DomainError("hash_mismatch", 422)
                old_included = composition.verify_included_sources(
                    base,
                    lookup,
                    project_id,
                    repository_id,
                    source_commit=version.get("source_commit"),
                )
                check_origins = "include_sources" in request or "included_sources" in base
                for row in base["files"]:
                    name = row["destination"]
                    if name in old_included:
                        continue
                    if (
                        check_origins
                        and name not in excluded
                        and name in inherited
                        and inherited[name] != row
                    ):
                        composition.inclusion_collision(name)
                    inherited[name] = row
                if "include_sources" not in request and old_included:
                    includes = base["included_sources"]
            retired_owned = inherited.keys() & excluded
            inherited = {name: row for name, row in inherited.items() if name not in excluded}
            imported = (
                composition.included_rows(includes, lookup, project_id, repository_id)
                if includes
                else {}
            )
            for name in sorted(
                imported.keys()
                & (seen | inherited.keys() | (excluded - retired_owned) | {request["entry_path"]})
            ):
                composition.inclusion_collision(name)
            inherited.update(imported)
            for ref in request.get("dependencies", []):
                self.version(tx, project_id, ref)
            for row in (
                []
                if includes
                else [
                    *retained_files,
                    *(
                        row
                        for name, row in inherited.items()
                        if name not in seen and row["origin"]["kind"] == "retained"
                    ),
                ]
            ):
                origin = row["origin"]
                retained = tx.get("retained_sources", origin["artifact_id"], required=False)
                path = managed(root, f".proscenium/retained/{origin['sha256']}/content")
                try:
                    valid = (
                        retained is not None
                        and retained["sha256"] == origin["sha256"]
                        and sha(path.read_bytes()) == origin["sha256"]
                    )
                except OSError:
                    valid = False
                if not valid:
                    raise DomainError(
                        "retained_source_missing",
                        422,
                        artifact_id=origin["artifact_id"],
                        sha256=origin["sha256"],
                    )
        destinations = seen | inherited.keys()
        if includes:
            composition.destination_bounds(destinations)
        for name in sorted(destinations):
            if includes:
                composition.closure_path(name)
            else:
                self.closure_path(root, name)
            parts = name.split("/")
            if any("/".join(parts[:i]) in destinations for i in range(1, len(parts))):
                raise DomainError("dependency_collision", 409, destination=name)
        if request["entry_path"] not in destinations:
            raise DomainError("dependency_missing", 422, path=request["entry_path"])
        entry = self.inherited_git_entry(request, inherited)
        if entry is not None:
            if not includes:
                self.pin_entry(project_id, entry, parent)
        return inherited, sorted(includes, key=composition.ref_key)

    def composed_input_files(
        self,
        project_id: str,
        root: Path,
        request: dict,
        inherited: dict,
        parent: str | None,
    ) -> list[dict]:
        replacement = set(request["paths"]) | {
            r["destination"] for r in request.get("retained_files", [])
        }
        entry = self.inherited_git_entry(request, inherited)
        if entry is not None and parent is None:
            raise DomainError("source_conflict", 409, INHERITED_ENTRY_CONFLICT, safe_for_model=True)
        rows = [r for dest, r in inherited.items() if dest not in replacement]
        rows.extend(request.get("retained_files", []))
        remaining = composition.MAX_BYTES
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            for row in sorted(rows, key=lambda row: row["destination"]):
                origin = row["origin"]
                if row is entry:
                    origin = {**origin, "commit": parent}
                retained = (
                    tx.get("retained_sources", origin["artifact_id"], required=False)
                    if origin["kind"] == "retained"
                    else None
                )
                try:
                    size, _ = self.resolver.bounded_origin(
                        project_id, root, origin, remaining, retained=retained
                    )
                except DomainError as exc:
                    if row is entry and exc.code != "invalid_request":
                        raise DomainError(
                            "source_conflict", 409, INHERITED_ENTRY_CONFLICT, safe_for_model=True
                        ) from exc
                    raise
                remaining -= size
        files = []
        for path in sorted(request["paths"]):
            size, digest = checked_file(relative(root, path, exists=True), remaining, None)
            remaining -= size
            files.append({"path": path, "sha256": digest.decode("ascii")})
        return files

    @staticmethod
    def inherited_git_entry(
        request: dict[str, Any], inherited: dict[str, dict[str, Any]]
    ) -> dict[str, Any] | None:
        name = request.get("entry_path")
        entry = inherited.get(name) if name is not None else None
        if (
            entry is not None
            and entry["origin"]["kind"] == "git"
            and name not in request["paths"]
            and all(r["destination"] != name for r in request.get("retained_files", []))
        ):
            return entry
        return None

    def pin_entry(
        self, project_id: str, entry: dict[str, Any], commit: str | None
    ) -> dict[str, Any]:
        if commit is None:
            raise DomainError("source_conflict", 409, INHERITED_ENTRY_CONFLICT, safe_for_model=True)
        origin = {**entry["origin"], "commit": commit}
        try:
            self.resolver.git_bytes(project_id, origin)
        except DomainError as exc:
            raise DomainError(
                "source_conflict", 409, INHERITED_ENTRY_CONFLICT, safe_for_model=True
            ) from exc
        return {**entry, "origin": origin}

    @staticmethod
    def composition_fingerprint(inherited: dict, includes: list[dict[str, str]]) -> str:
        return fingerprint({"inherited": inherited, "included_sources": includes})

    def source_closure(
        self,
        project_id: str,
        root: Path,
        request: dict[str, Any],
        intent: dict[str, Any],
        commit: str,
    ) -> dict[str, Any] | None:
        files, includes = self.validate_closure_inputs(project_id, root, request, intent["parent"])
        if "entry_path" not in request:
            return None
        if includes and intent.get("composition") != self.composition_fingerprint(files, includes):
            raise DomainError("source_conflict", 409)
        entry = self.inherited_git_entry(request, files)
        if entry is not None:
            files[request["entry_path"]] = (
                {**entry, "origin": {**entry["origin"], "commit": commit}}
                if includes
                else self.pin_entry(project_id, entry, commit)
            )
        repository_id = self.services.registry.get(project_id)["repository_id"]
        files.update(
            (
                row["path"],
                {
                    "destination": row["path"],
                    "origin": {
                        "kind": "git",
                        "repository_id": repository_id,
                        "commit": commit,
                        "path": row["path"],
                        "sha256": row["sha256"],
                    },
                },
            )
            for row in intent["files"]
        )
        files.update((row["destination"], row) for row in request.get("retained_files", []))
        closure = {
            "entry_path": request["entry_path"],
            "files": [files[name] for name in sorted(files)],
            "dependencies": sorted(
                {
                    composition.ref_key(ref): ref
                    for ref in [*request.get("dependencies", []), *includes]
                }.values(),
                key=composition.ref_key,
            ),
            "required_runtime": request.get(
                "required_runtime", {"application": "proscenium", "version": "1.0", "features": []}
            ),
        }
        if includes:
            closure["included_sources"] = includes
        closure["digest"] = closure_digest(closure)
        if not includes:
            validate("SourceClosure", closure)
        return closure

    @staticmethod
    def uncertain(
        request: dict[str, Any], intent: dict[str, Any], *, replayed: bool
    ) -> dict[str, Any]:
        result = dict(
            schema_version="1.0",
            operation_id=request["operation_id"],
            project_id=request["project_id"],
            state="outcome_uncertain",
            source_commit=None,
            parent_commit=intent["parent"],
            files=intent["files"],
            replayed=replayed,
        )
        validate("CheckpointReceipt", result)
        return result

    @staticmethod
    def sync_index(root: Path, intent: dict[str, Any], commit: str) -> None:
        """CAS selected stage-zero entries under Git's real index lock, never a reset."""
        index = Path(os.fsdecode(git(root, "rev-parse", "--git-path", "index").rstrip(b"\n")))
        if not index.is_absolute():
            index = root / index
        lock = index.with_name(index.name + ".lock")
        # A concurrent Git writer must finish before replay can repair this index.
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            return

        def still_current() -> bool:
            return (
                git(root, "symbolic-ref", "-q", "HEAD", required=False).decode().strip()
                == intent["branch"]
                and git(root, "rev-parse", "--verify", "HEAD", required=False).decode().strip()
                == commit
            )

        def entries(data: bytes) -> dict[bytes, list[tuple[bytes, bytes, bytes]]]:
            rows: dict[bytes, list[tuple[bytes, bytes, bytes]]] = {}
            for line in data.split(b"\0"):
                if line:
                    metadata, name = line.split(b"\t", 1)
                    mode, oid, stage = metadata.split()
                    rows.setdefault(name, []).append((mode, oid, stage))
            return rows

        def tree_at(revision: str | None, path: str) -> list[tuple[bytes, bytes, bytes]]:
            if revision is None:
                return []
            for line in git(
                root, "--literal-pathspecs", "ls-tree", "-z", revision, "--", path
            ).split(b"\0"):
                if line:
                    metadata, name = line.split(b"\t", 1)
                    if name == os.fsencode(path):
                        mode, kind, oid = metadata.split()
                        # Tree entries are deliberately not treated as absent.
                        return [(mode, oid, b"0" if kind == b"blob" else kind)]
            return []

        temporary: Path | None = None
        installed = False
        try:
            with os.fdopen(fd, "wb") as stream:
                if not still_current():
                    return  # Recovery on a different branch/HEAD must not change its index.
                original = entries(git(root, "ls-files", "-s", "-z"))
                updates: dict[bytes, list[tuple[bytes, bytes, bytes]]] = {}
                for row in intent["files"]:
                    path = row["path"]
                    name = os.fsencode(path)
                    actual = original.get(name, [])
                    if actual != tree_at(intent["parent"], path):
                        continue  # Includes every unmerged stage.
                    target = tree_at(commit, path)
                    if not target or actual == target:
                        continue
                    # Never let Git's directory/file replacement evict another index entry.
                    if any(
                        k.startswith(name + b"/") or name.startswith(k + b"/") for k in original
                    ):
                        continue
                    updates[name] = target
                if not updates:
                    return
                fd_tmp, name_tmp = tempfile.mkstemp(prefix="proscenium-index-", dir=index.parent)
                os.close(fd_tmp)
                temporary = Path(name_tmp)
                env = {"GIT_INDEX_FILE": name_tmp}
                if index.exists():
                    mode = stat.S_IMODE(index.stat().st_mode)
                    os.fchmod(stream.fileno(), mode)
                    shutil.copyfile(index, temporary)
                else:
                    temporary.unlink()
                    git(root, "read-tree", "--empty", env=env)
                git(
                    root,
                    "update-index",
                    "--add",
                    "-z",
                    "--index-info",
                    env=env,
                    data=b"".join(
                        mode + b" " + oid + b"\t" + name + b"\0"
                        for name, [(mode, oid, _)] in updates.items()
                    ),
                )
                if entries(git(root, "ls-files", "-s", "-z", env=env)) != original | updates:
                    raise DomainError("source_conflict", 409)
                with prepared_branch(root, intent["branch"], commit) as prepared:
                    if not prepared or not still_current():
                        return
                    with temporary.open("rb") as source:
                        shutil.copyfileobj(source, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                    lock.replace(index)
                    installed = True
        finally:
            if not installed:  # Do not unlink a new writer's lock after replace.
                lock.unlink()
            if temporary is not None:
                temporary.unlink(missing_ok=True)
                temporary.with_name(temporary.name + ".lock").unlink(missing_ok=True)

    def prepare(
        self, root: Path, request: dict[str, Any], intent: dict[str, Any]
    ) -> tuple[str, str]:
        with checkpoint_index(root, intent["parent"]) as env:
            for row in intent["files"]:
                path = relative(root, row["path"], exists=True)
                if "composition" in intent:
                    checked_file(path, composition.MAX_BYTES, row["sha256"])
                    hash_args = ["--path=" + row["path"], "--", str(path)]
                    data = None
                else:
                    data = path.read_bytes()
                    if sha(data) != row["sha256"]:
                        raise DomainError("source_conflict", 409)
                    hash_args = ["--stdin", "--path=" + row["path"]]
                oid = (
                    git(
                        root,
                        "hash-object",
                        "-w",
                        *hash_args,
                        data=data,
                        env=env,
                    )
                    .decode()
                    .strip()
                )
                mode = intent["modes"][row["path"]]
                if path.suffix.lower() in BINARY_SUFFIXES:
                    if (
                        "composition" in intent and int(git(root, "cat-file", "-s", oid)) > 1024
                    ) or not git(root, "cat-file", "blob", oid).startswith(
                        b"version https://git-lfs.github.com/spec/v1\n"
                    ):
                        raise DomainError(
                            "source_conflict",
                            409,
                            BINARY_REQUIRES_LFS,
                            safe_for_model=True,
                        )
                git(root, "update-index", "--add", "--cacheinfo", mode, oid, row["path"], env=env)
            tree = git(root, "write-tree", env=env).decode().strip()
            message = (
                request["message"].rstrip()
                + "\n\nProscenium-Checkpoint: "
                + request["operation_id"]
                + "\nProscenium-Fingerprint: "
                + intent["fingerprint"]
                + "\n"
            )
            # Parse Git's own ident, whose timestamp/timezone are valid GIT_*_DATE inputs.
            author, dates = intent["author"].rsplit("> ", 1)
            person, email = author.rsplit(" <", 1)
            env.update(
                GIT_AUTHOR_NAME=person,
                GIT_AUTHOR_EMAIL=email,
                GIT_AUTHOR_DATE=dates,
                GIT_COMMITTER_NAME=person,
                GIT_COMMITTER_EMAIL=email,
                GIT_COMMITTER_DATE=dates,
            )
            args = ["commit-tree", tree]
            if intent["parent"]:
                args.extend(["-p", intent["parent"]])
            return git(root, *args, data=message.encode(), env=env).decode().strip(), tree

    def _legacy_completed_replay(
        self, tx: Transaction, root: Path, caller: Caller, request: dict[str, Any], identity: str
    ) -> dict[str, Any] | None:
        import json

        saved = tx.get("checkpoint_intents", identity, required=False)
        if (
            not saved
            or saved.get("state") != "completed"
            or saved.get("modern_binding") is True
            or saved.get("sorted_ref_binding") is True
            or any(k in request for k in ("base_version", "exclude_paths", "include_sources"))
        ):
            return None
        row = tx.connection.execute(
            "SELECT fingerprint,result FROM operations WHERE scope='sources.checkpoint' AND id=?",
            (identity,),
        ).fetchone()
        # a3efc98 fingerprinted the raw request and the original invocation.
        # A receipt is not evidence of omitted fields or the original order.
        exact = fingerprint(
            {"body": request, "work_id": caller.work_id, "invocation_id": caller.invocation_id}
        )
        if not row or exact != row[0] or exact != saved["fingerprint"]:
            return None
        if saved.get("parent") != request["expected_head"] or [
            r["path"] for r in saved.get("files", [])
        ] != sorted(request["paths"]):
            return None
        candidate = saved.get("candidate")
        if not isinstance(candidate, str) or not candidate:
            return None
        commit_msg = git(root, "show", "-s", "--format=%B", candidate, required=False).decode()
        trailer = (
            "\n\nProscenium-Checkpoint: "
            + request["operation_id"]
            + "\nProscenium-Fingerprint: "
            + saved["fingerprint"]
        )
        if commit_msg.rstrip("\n") != request["message"].rstrip() + trailer:
            return None
        previous = validate("CheckpointReceipt", json.loads(row[1]))
        closure = previous.get("source_closure")
        dependencies = request.get("dependencies", [])
        retained = request.get("retained_files", [])
        if closure is None:
            if any(
                k in request
                for k in ("entry_path", "required_runtime", "dependencies", "retained_files")
            ):
                return None
        elif (
            closure["entry_path"] != request.get("entry_path")
            or closure["required_runtime"]
            != request.get(
                "required_runtime", {"application": "proscenium", "version": "1.0", "features": []}
            )
            or closure["dependencies"] != sorted(dependencies, key=composition.ref_key)
            or [item for item in closure["files"] if item["origin"]["kind"] == "retained"]
            != sorted(retained, key=lambda item: item["destination"])
        ):
            return None

        return previous

    def verify(
        self, project_id: str, request: dict[str, Any], intent: dict[str, Any], commit: str
    ) -> dict[str, Any] | None:
        root = Path(self.services.registry.get(project_id)["path"])
        if "composition" not in intent:
            inherited, _ = self.validate_closure_inputs(project_id, root, request, intent["parent"])
            entry = self.inherited_git_entry(request, inherited)
            if entry is not None:
                self.pin_entry(project_id, entry, commit)
        parents = git(root, "rev-list", "--parents", "-n", "1", commit).decode().split()[1:]
        expected = [intent["parent"]] if intent["parent"] else []
        message = git(root, "show", "-s", "--format=%B", commit).decode()
        tree = git(root, "rev-parse", commit + "^{tree}").decode().strip()
        if (
            commit != intent.get("candidate")
            or parents != expected
            or tree != intent.get("tree")
            or "Proscenium-Checkpoint: " + request["operation_id"] not in message.splitlines()
            or "Proscenium-Fingerprint: " + intent["fingerprint"] not in message.splitlines()
        ):
            raise DomainError("source_conflict", 409)
        if "composition" in intent:
            closure = self.source_closure(project_id, root, request, intent, commit)
            if closure is None or "included_sources" not in closure:
                raise DomainError("source_conflict", 409)
            self.resolver.verify_composed(project_id, closure, collect=False, source_commit=commit)
            return closure
        repository_id = self.services.registry.get(project_id)["repository_id"]
        for row in intent["files"]:
            self.resolver.git_bytes(
                project_id,
                dict(
                    kind="git",
                    repository_id=repository_id,
                    commit=commit,
                    path=row["path"],
                    sha256=row["sha256"],
                ),
            )
        return None
