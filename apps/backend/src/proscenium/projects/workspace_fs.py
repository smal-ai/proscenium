"""Linux directory handles for owner workspace validation and bootstrap."""

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from proscenium.api.errors import DomainError

DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


@contextmanager
def workspace_errors(path: str | Path) -> Iterator[None]:
    try:
        yield
    except OSError as exc:
        if exc.errno in {errno.ENAMETOOLONG, errno.EINVAL, errno.ENOTDIR, errno.EISDIR}:
            raise DomainError("invalid_request", 400, f"Invalid workspace path: {path}") from exc
        if isinstance(exc, PermissionError) or exc.errno in {errno.ELOOP, errno.EROFS}:
            raise DomainError(
                "scope_denied", 403, f"Permission denied accessing workspace '{path}'"
            ) from exc
        if exc.errno == errno.ENOENT:
            raise DomainError("not_found", 404, f"Workspace folder not found: {path}") from exc
        raise


def fd_path(fd: int) -> Path:
    # A child Git process needs the owning process's descriptor, not its own.
    return Path(f"/proc/{os.getpid()}/fd/{fd}")


def verify_directory(fd: int, expected: Path) -> Path:
    actual = Path(os.readlink(fd_path(fd)))
    if actual != expected:
        raise DomainError("scope_denied", 403, f"Workspace directory changed: {expected}")
    return actual


def _check_component(name: str) -> None:
    if name in {"", ".", ".."} or "/" in name:
        raise DomainError("scope_denied", 403, "Invalid workspace directory component")


def open_child_directory(parent: int, name: str) -> int:
    _check_component(name)
    try:
        return os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
    except NotADirectoryError as exc:
        # Linux can report ENOTDIR rather than ELOOP for O_DIRECTORY | O_NOFOLLOW.
        try:
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            raise exc from None
        if stat.S_ISLNK(entry.st_mode):
            raise DomainError(
                "scope_denied", 403, f"Symlink in workspace directory: {name}"
            ) from exc
        raise


@contextmanager
def workspace_ancestor(
    root: Path, *, allow_missing: bool = False
) -> Iterator[tuple[int, Path, tuple[str, ...]]]:
    ancestor = Path("/")
    fd = os.open(ancestor, DIRECTORY_FLAGS)
    try:
        parts = root.parts[1:]
        for index, part in enumerate(parts):
            try:
                child = open_child_directory(fd, part)
            except FileNotFoundError:
                if not allow_missing:
                    raise
                yield fd, verify_directory(fd, ancestor), parts[index:]
                return
            os.close(fd)
            fd = child
            ancestor /= part
        yield fd, verify_directory(fd, ancestor), ()
    finally:
        os.close(fd)


def mkdir_child(parent: int, name: str) -> int:
    _check_component(name)
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    return open_child_directory(parent, name)


@contextmanager
def create_root(parent: int, ancestor: Path, parts: tuple[str, ...]) -> Iterator[int]:
    fd = os.dup(parent)
    try:
        for part in parts:
            verify_directory(fd, ancestor)
            child = mkdir_child(fd, part)
            os.close(fd)
            fd = child
            ancestor /= part
        verify_directory(fd, ancestor)
        yield fd
    finally:
        os.close(fd)


def check_entry(fd: int, name: str) -> os.stat_result | None:
    try:
        entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(entry.st_mode):
        raise DomainError("scope_denied", 403, f"Symlink at workspace entry: {name}")
    return entry


def open_regular(fd: int, name: str, flags: int) -> int:
    entry = check_entry(fd, name)
    if entry is not None and not stat.S_ISREG(entry.st_mode):
        raise DomainError("invalid_request", 400, f"Workspace entry is not a regular file: {name}")
    handle = os.open(name, flags | FILE_FLAGS, 0o600, dir_fd=fd)
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise DomainError(
                "invalid_request", 400, f"Workspace entry is not a regular file: {name}"
            )
    except BaseException:
        os.close(handle)
        raise
    return handle


def read_regular(fd: int, name: str) -> bytes:
    with os.fdopen(open_regular(fd, name, os.O_RDONLY), "rb") as stream:
        return stream.read()


@contextmanager
def runtime_directory(root_fd: int, root: Path, *, create: bool = False) -> Iterator[int | None]:
    try:
        fd = (
            mkdir_child(root_fd, ".proscenium")
            if create
            else open_child_directory(root_fd, ".proscenium")
        )
    except FileNotFoundError:
        if create:
            raise
        yield None
        return
    try:
        verify_directory(fd, root / ".proscenium")
        if not os.access(".", os.W_OK | os.X_OK, dir_fd=fd):
            raise DomainError(
                "scope_denied",
                403,
                f"Workspace runtime directory is not writable: {root / '.proscenium'}",
            )
        # SQLite reports EACCES as a generic OperationalError. Probe existing files
        # with their required access first, without creating or following them.
        for name in (
            "state.sqlite3",
            "state.sqlite3-wal",
            "state.sqlite3-shm",
            "state.sqlite3-journal",
            "owner.lock",
        ):
            try:
                handle = open_regular(fd, name, os.O_RDWR)
            except FileNotFoundError:
                continue
            os.close(handle)
        yield fd
    finally:
        os.close(fd)


def publish_config(fd: int, data: bytes) -> None:
    name = "project.json"
    temporary = ".project-" + uuid4().hex
    with os.fdopen(
        open_regular(fd, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL), "wb"
    ) as stream:
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            # Atomic publication without clobbering a file installed meanwhile.
            try:
                os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            except FileExistsError:
                if read_regular(fd, name) != data:
                    raise DomainError(
                        "source_conflict", 409, "Project configuration changed"
                    ) from None
            os.fsync(fd)
        finally:
            os.unlink(temporary, dir_fd=fd)
