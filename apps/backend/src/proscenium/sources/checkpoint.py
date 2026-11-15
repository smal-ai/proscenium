"""Durable checkpoint intent, isolated index and atomic branch/checkpoint-ref CAS."""

import fcntl
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import validate
from proscenium.db.store import now
from proscenium.services import Services
from proscenium.sources.common import (
    active,
    admitted,
    binding,
    fingerprint,
    git,
    managed,
    relative,
    replay,
    sha,
)
from proscenium.sources.resolver import Resolver, closure_digest


class Checkpoints:
    def __init__(self, services: Services, resolver: Resolver):
        self.services, self.resolver = services, resolver

    def checkpoint(self, caller: Caller, request: dict[str, Any]) -> dict[str, Any]:
        validate("CheckpointRequest", request)
        project_id, identity = request["project_id"], request["operation_id"]
        root = active(self.services, caller, "checkpoint_sources", project_id)
        store = self.services.registry.project_store(project_id)
        immutable = binding(caller, request)
        lockpath = store.path.parent / "checkpoint.lock"
        with os.fdopen(
            os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a+b"
        ) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.validate_closure_inputs(project_id, root, request)
            with store.transaction() as tx:
                previous = replay(tx, "sources.checkpoint", identity, immutable)
                if previous:
                    saved = tx.require("checkpoint_intents", identity)
                    reference = "refs/proscenium/checkpoints/" + sha(identity.encode())
                    committed = git(root, "rev-parse", "--verify", reference).decode().strip()
                    if committed != previous["source_commit"]:
                        raise DomainError("source_conflict", 409)
                    self.verify(project_id, request, saved, committed)
                    closure = self.source_closure(project_id, root, request, saved, committed)
                    if closure is not None and previous.get("source_closure") != closure:
                        raise DomainError("source_conflict", 409)
                    validate("CheckpointReceipt", previous)
                    return {**previous, "replayed": True}
                intent = tx.get("checkpoint_intents", identity, required=False)
                recovering = intent is not None
                if intent and intent["fingerprint"] != fingerprint(immutable):
                    raise DomainError("idempotency_conflict", 409)
                if not intent:
                    admitted(self.services, tx, caller)
                    paths = request["paths"]
                    if len(set(paths)) != len(paths):
                        raise DomainError("invalid_request")
                    branch = git(root, "symbolic-ref", "-q", "HEAD").decode().strip()
                    head = (
                        git(root, "rev-parse", "--verify", "HEAD", required=False).decode().strip()
                        or None
                    )
                    if head != request["expected_head"] or not branch.startswith("refs/heads/"):
                        raise DomainError("source_conflict", 409)
                    files = [
                        {"path": p, "sha256": sha(relative(root, p, exists=True).read_bytes())}
                        for p in sorted(paths)
                    ]
                    # Author identity and date are frozen before any Git object/ref changes.
                    author = git(root, "var", "GIT_AUTHOR_IDENT").decode().strip()
                    intent = dict(
                        fingerprint=fingerprint(immutable),
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
                    if sha(relative(root, row["path"], exists=True).read_bytes()) != row["sha256"]:
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
            self.verify(project_id, request, intent, commit)
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
            closure = self.source_closure(project_id, root, request, intent, commit)
            if closure is not None:
                receipt["source_closure"] = closure
            validate("CheckpointReceipt", receipt)
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

    def validate_closure_inputs(self, project_id: str, root: Path, request: dict[str, Any]) -> None:
        if "entry_path" not in request:
            if any(
                key in request for key in ("dependencies", "required_runtime", "retained_files")
            ):
                raise DomainError(
                    "invalid_request",
                    message="entry_path is required when building a source closure",
                )
            return
        relative(root, request["entry_path"])
        if request["entry_path"].split("/")[0] == ".receipt.json":
            raise DomainError("scope_denied", 403)
        retained_files = request.get("retained_files", [])
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            for ref in request.get("dependencies", []):
                version = tx.get("versions", ref["version_id"], required=False)
                if not version:
                    raise DomainError("dependency_missing", 422, version_id=ref["version_id"])
                if version["project_id"] != project_id or version["item_id"] != ref["item_id"]:
                    raise DomainError("scope_denied", 403)
            for row in retained_files:
                relative(root, row["destination"])
                if row["destination"].split("/")[0] == ".receipt.json":
                    raise DomainError("scope_denied", 403)
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
        destinations = list(request["paths"]) + [row["destination"] for row in retained_files]
        for name in destinations:
            if destinations.count(name) > 1 or any(
                other != name and (other.startswith(name + "/") or name.startswith(other + "/"))
                for other in destinations
            ):
                raise DomainError("dependency_collision", 409, destination=name)
        if request["entry_path"] not in destinations:
            raise DomainError("dependency_missing", 422, path=request["entry_path"])

    def source_closure(
        self,
        project_id: str,
        root: Path,
        request: dict[str, Any],
        intent: dict[str, Any],
        commit: str,
    ) -> dict[str, Any] | None:
        self.validate_closure_inputs(project_id, root, request)
        if "entry_path" not in request:
            return None
        repository_id = self.services.registry.get(project_id)["repository_id"]
        files = [
            {
                "destination": row["path"],
                "origin": {
                    "kind": "git",
                    "repository_id": repository_id,
                    "commit": commit,
                    "path": row["path"],
                    "sha256": row["sha256"],
                },
            }
            for row in intent["files"]
        ] + request.get("retained_files", [])
        closure = {
            "entry_path": request["entry_path"],
            "files": sorted(files, key=lambda row: row["destination"]),
            "dependencies": sorted(
                request.get("dependencies", []),
                key=lambda row: (row["item_id"], row["version_id"]),
            ),
            "required_runtime": request.get(
                "required_runtime", {"application": "proscenium", "version": "1.0", "features": []}
            ),
        }
        closure["digest"] = closure_digest(closure)
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

    def prepare(
        self, root: Path, request: dict[str, Any], intent: dict[str, Any]
    ) -> tuple[str, str]:
        descriptor, name = tempfile.mkstemp(prefix="checkpoint-index-", dir=root / ".proscenium")
        os.close(descriptor)
        index = Path(name)
        index.unlink()  # Git requires absent or valid index, not an empty file.
        env = {"GIT_INDEX_FILE": name}
        try:
            if intent["parent"]:
                git(root, "read-tree", intent["parent"], env=env)
            else:
                git(root, "read-tree", "--empty", env=env)
            for row in intent["files"]:
                path = relative(root, row["path"], exists=True)
                data = path.read_bytes()
                if sha(data) != row["sha256"]:
                    raise DomainError("source_conflict", 409)
                oid = (
                    git(
                        root,
                        "hash-object",
                        "-w",
                        "--stdin",
                        "--path=" + row["path"],
                        data=data,
                        env=env,
                    )
                    .decode()
                    .strip()
                )
                mode = intent["modes"][row["path"]]
                if path.suffix.lower() in {
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
                }:
                    if not git(root, "cat-file", "blob", oid).startswith(
                        b"version https://git-lfs.github.com/spec/v1\n"
                    ):
                        raise DomainError(
                            "source_conflict",
                            409,
                            "Binary authoring files require Git LFS tracking",
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
        finally:
            index.unlink(missing_ok=True)
            Path(name + ".lock").unlink(missing_ok=True)

    def verify(
        self, project_id: str, request: dict[str, Any], intent: dict[str, Any], commit: str
    ) -> None:
        root = Path(self.services.registry.get(project_id)["path"])
        self.validate_closure_inputs(project_id, root, request)
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
