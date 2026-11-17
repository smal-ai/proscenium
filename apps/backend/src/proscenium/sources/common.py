"""Project-scoped source primitives. No fetch, mutable HEAD fallback or shell strings."""

import hashlib
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical
from proscenium.db import Transaction
from proscenium.db.paths import project_path
from proscenium.services import Services


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(body: Any) -> str:
    return sha(canonical(body))


SECRET_NAMES = {
    ".npmrc",
    ".netrc",
    ".pypirc",
    ".git-credentials",
    ".docker",
    ".kube",
    ".copilot",
    ".opencode",
    "auth.json",
    "kubeconfig",
    "accesstokens.json",
    "azureprofile.json",
    "msal_token_cache.json",
    "token.json",
}
RUNTIME_NAMES = {
    ".cache",
    ".venv",
    "node_modules",
    "__pycache__",
    "library",
    "temp",
    "logs",
    "builds",
    "usersettings",
    "memorycaptures",
}


def git(
    root: Path,
    *args: str,
    data: bytes | None = None,
    env: dict[str, str] | None = None,
    required: bool = True,
) -> bytes:
    options = [] if args[:2] == ("lfs", "install") else ["-c", "core.hooksPath=/dev/null"]
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_CONFIG_COUNT",
        }
        and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    process = subprocess.run(
        ["git", *options, "-C", str(root), *args],
        input=data,
        capture_output=True,
        timeout=60,
        env={
            **environment,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_GRAFT_FILE": "/dev/null",
            **(env or {}),
        },
    )
    if process.returncode and required:
        raise DomainError("source_unavailable", 422, "Local Git operation failed")
    return process.stdout if not process.returncode else b""


def relative(root: Path, value: str, *, exists: bool = False) -> Path:
    if any(p in {"", ".", ".."} for p in value.split("/")):
        raise DomainError("scope_denied", 403)
    if any(p.lower() in SECRET_NAMES | RUNTIME_NAMES for p in PurePosixPath(value).parts):
        raise DomainError("scope_denied", 403)
    result = project_path(root, value, must_exist=exists)
    # Sources cannot traverse even an in-project symlink. Historical Git uses mode checks too.
    current = root
    for part in PurePosixPath(value).parts:
        current /= part
        if current.is_symlink():
            raise DomainError("scope_denied", 403)
    return result


def managed(root: Path, value: str, *, exists: bool = False) -> Path:
    if any(p.lower() in SECRET_NAMES for p in PurePosixPath(value).parts):
        raise DomainError("scope_denied", 403)
    result = project_path(root, value, managed=True, must_exist=exists)
    current = root
    for part in PurePosixPath(value).parts:
        current /= part
        if current.is_symlink():
            raise DomainError("scope_denied", 403)
    return result


def active(services: Services, caller: Caller, operation: str, project_id: str) -> Path:
    caller.permits(operation, project_id)
    if caller.channel != "tool" or services.registry.active_id() != project_id:
        raise DomainError("scope_denied", 403)
    if caller.owner_epoch != services.ownership.epoch:
        raise DomainError("owner_conflict", 409)
    return Path(services.registry.get(project_id)["path"])


def admitted(services: Services, tx: Transaction, caller: Caller) -> dict[str, Any]:
    services.gate.require(str(tx.project_id))
    if not caller.work_id or not caller.invocation_id:
        raise DomainError("attribution_unavailable", 409)
    work = tx.require("work", caller.work_id)
    invocation = tx.require("invocations", caller.invocation_id)
    if (
        work["state"] not in {"running", "awaiting_user"}
        or invocation.get("work_id") != caller.work_id
        or invocation.get("owner_epoch") != caller.owner_epoch
        or invocation.get("state") not in {"admitted", "running"}
    ):
        raise DomainError("attribution_unavailable", 409)
    return work


def binding(caller: Caller, body: dict[str, Any]) -> dict[str, Any]:
    return {"body": body, "work_id": caller.work_id}


def replay(tx: Transaction, scope: str, identity: str, body: Any) -> dict[str, Any] | None:
    # F00 has no read-only operation lookup. Reuse its exact canonical fingerprint semantics.
    import json

    row = tx.connection.execute(
        "SELECT fingerprint,result FROM operations WHERE scope=? AND id=?", (scope, identity)
    ).fetchone()
    if row:
        if row[0] != fingerprint(body):
            raise DomainError("idempotency_conflict", 409)
        return dict(json.loads(row[1]))
    return None


def mkdir_private(path: Path) -> None:
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)


def atomic_bytes(path: Path, data: bytes) -> None:
    """Immutable atomic promotion with fsync. Existing bytes must agree."""
    import tempfile

    mkdir_private(path.parent)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != data:
            raise DomainError("hash_mismatch", 422)
        return
    fd, temporary = tempfile.mkstemp(prefix=".promote-", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.chmod(0o400)
        try:
            os.link(temp, path)  # Only the private temporary file, never live/shared source.
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise DomainError("hash_mismatch", 422) from None
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)
