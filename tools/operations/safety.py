"""Fail-closed filesystem, process and ownership checks for stopped-service recovery."""

import fcntl
import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from proscenium.db.paths import DENIED_PARTS


class RecoveryError(ValueError):
    pass


def relative(value: str) -> Path:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(p in {"", ".", ".."} for p in value.split("/"))
        or any(c in value for c in ("\\", "\x00", ":"))
    ):
        raise RecoveryError("unsafe_relative_path")
    return Path(*path.parts)


def safe_file(root: Path, value: str) -> Path:
    path = root
    for component in relative(value).parts:
        path = path / component
        if path.is_symlink():
            raise RecoveryError("symlink_refused")
    if not stat.S_ISREG(path.stat().st_mode):
        raise RecoveryError("regular_file_required")
    return path


def secret_path(value: str) -> bool:
    return any(
        p.lower() in DENIED_PARTS | {"auth.json", "hosts.yml", "cookies", ".netrc", ".npmrc"}
        or p.lower().startswith(".env")
        or p.lower().endswith((".pem", ".key"))
        for p in relative(value).parts
    )


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def private_directory(path: Path) -> None:
    # Refuse even a dangling symlink or an existing empty destination.
    if os.path.lexists(path):
        raise RecoveryError("destination_exists")
    if path.parent.resolve(strict=True) != path.parent.absolute():
        raise RecoveryError("destination_parent_not_canonical")
    path.mkdir(mode=0o700)


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive creation and O_NOFOLLOW at both ends. Never buffer media in RAM.
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise RecoveryError("regular_file_required")
        target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(target_fd, "wb") as output, os.fdopen(os.dup(source_fd), "rb") as input_:
            while block := input_.read(1024 * 1024):
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(source_fd)


def tree_files(root: Path):
    if root.is_symlink() or not root.is_dir():
        raise RecoveryError("directory_required")
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = Path(directory) / name
            if path.is_symlink():
                raise RecoveryError("symlink_refused")
        for name in sorted(files):
            path = Path(directory) / name
            yield safe_file(root, path.relative_to(root).as_posix())


@contextmanager
def locks(paths: list[Path]):
    opened = []
    try:
        for path in paths:
            if path.is_symlink() or path.parent.resolve(strict=True) != path.parent.absolute():
                raise RecoveryError("symlink_lock_refused")
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            opened.append(fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RecoveryError("service_or_project_owned") from exc
        yield
    finally:
        for fd in reversed(opened):
            os.close(fd)


def assert_stopped(roots: list[Path]) -> None:
    """No signals. Refuse scoped users of files and ambiguous native Copilot processes.

    /proc races with exited processes are harmless. Unreadable living same-user
    processes are not proof of quiescence. Independent model services are untouched.
    """
    own = os.getpid()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == own:
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            argv = (process / "cmdline").read_bytes().split(b"\0")
            if not argv[0]:  # Kernel thread or zombie, no writes possible.
                continue
            executable = Path(os.fsdecode(argv[0])).name
            if (
                int(process.name) == os.getppid()
                and executable == "uv"
                and [os.fsdecode(a) for a in argv[1:5]]
                == ["run", "python", "-m", "tools.operations.recovery"]
            ):
                continue  # This command's own waiting launcher, not another writer.
            # These OS session/auth daemons deliberately disable ptrace. They
            # cannot be Proscenium/native authoring workers. Do not inspect their
            # private descriptors (or turn their presence into a global outage).
            if os.fsdecode(argv[0]) in {
                "/usr/lib/systemd/systemd",
                "(sd-pam)",
                "/usr/bin/ssh-agent",
                "ssh-agent",
            }:
                continue
            paths = [os.fsdecode(arg) for arg in argv]
            paths.append(os.readlink(process / "cwd"))
            for fd in (process / "fd").iterdir():
                try:
                    paths.append(os.readlink(fd))
                except FileNotFoundError:
                    pass
            scoped = any(
                value == str(root) or value.startswith(str(root) + "/")
                for value in paths
                for root in roots
            )
            if scoped or executable == "copilot":
                raise RecoveryError(f"active_or_ambiguous_process:{process.name}")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as exc:
            if process.exists():
                raise RecoveryError(f"process_inspection_denied:{process.name}") from exc
