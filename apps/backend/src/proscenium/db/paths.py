import os
import stat
from pathlib import Path, PurePosixPath

from proscenium.api.errors import DomainError

DENIED_PARTS = {
    ".git",
    ".direnv",
    ".ssh",
    ".aws",
    ".azure",
    ".config",
    "credentials",
    "credential",
    "secrets",
    "tokens",
    "id_rsa",
    "id_ed25519",
}
MANAGED_ROOTS = {"artefacts", "retained", "snapshots", "jobs", "tool-output"}


def permit_project_parts(parts: tuple[str, ...], *, managed: bool = False) -> None:
    if ".proscenium" in parts:
        index = parts.index(".proscenium")
        if not managed or len(parts) <= index + 1 or parts[index + 1] not in MANAGED_ROOTS:
            raise DomainError("scope_denied", 403)
    for part in parts:
        lower = part.lower()
        if (
            lower in DENIED_PARTS
            or lower.startswith(".env")
            or lower.endswith((".pem", ".key"))
            or (lower == ".proscenium" and not managed)
        ):
            raise DomainError("scope_denied", 403)


def project_path(
    root: Path,
    relative: str,
    *,
    managed: bool = False,
    directory: bool = False,
    must_exist: bool = True,
) -> Path:
    """Validate lexical AND resolved paths. managed is trusted service policy, never wire input."""
    path = PurePosixPath(relative)
    if (
        not relative
        or ":" in relative
        or "\\" in relative
        or "\x00" in relative
        or path.is_absolute()
    ):
        raise DomainError("scope_denied", 403)
    if ".." in path.parts or (relative == "." and not directory):
        raise DomainError("scope_denied", 403)
    canonical_root = root.resolve(strict=True)
    try:
        resolved = (canonical_root / relative).resolve(strict=must_exist)
        actual = resolved.relative_to(canonical_root)
    except (ValueError, OSError, RuntimeError) as exc:
        raise DomainError("scope_denied", 403) from exc
    for parts in (path.parts, actual.parts):
        permit_project_parts(parts, managed=managed)
    if must_exist and (resolved.is_dir() != directory):
        raise DomainError("invalid_request", message="Unexpected file/directory kind")
    return resolved


def _open_without_links(path: Path, flags: int, root: Path | None) -> int:
    if root is None:
        return os.open(path, flags)
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise DomainError("scope_denied", 403) from exc
    if not parts or ".." in parts:
        raise DomainError("scope_denied", 403)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root, directory_flags)
    try:
        for component in parts[:-1]:
            child = os.open(component, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], flags, dir_fd=directory)
    finally:
        os.close(directory)


def read_bounded_regular_file(
    path: Path,
    max_bytes: int | None = None,
    *,
    root: Path | None = None,
) -> bytes:
    """Read the exact opened file, without blocking on a FIFO or following a symlink.

    For project staging, anchor the open at the trusted project root and refuse links
    in every component, not just the leaf. Callers still validate their staging scope.
    """
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    try:
        fd = _open_without_links(path, flags, root)
    except FileNotFoundError as exc:
        raise DomainError("not_found", 404) from exc
    except OSError as exc:
        raise DomainError("scope_denied", 403) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DomainError("scope_denied", 403, "Expected a regular file")
        if max_bytes is not None and info.st_size > max_bytes:
            raise DomainError("upload_too_large", 413)
        # Do not let buffering prefetch past the limit on small bounded reads.
        with os.fdopen(fd, "rb", buffering=0) as stream:
            fd = -1
            data = stream.read(max_bytes + 1) if max_bytes is not None else stream.read()
    finally:
        if fd >= 0:
            os.close(fd)
    if max_bytes is not None and len(data) > max_bytes:
        raise DomainError("upload_too_large", 413)
    return data
