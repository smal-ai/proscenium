"""Fail-closed filesystem, process and ownership checks for stopped-service recovery."""

import fcntl
import hashlib
import json
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


def still_live(process: Path) -> bool:
    """An exited/zombie process can briefly deny /proc links before reaping.

    Only observed terminal kernel state is safe to ignore. This does not infer a
    successful job exit or settle any application record.
    """
    try:
        state = (process / "stat").read_text().rsplit(") ", 1)[1].split()[0]
        return state not in {"Z", "X", "x"}
    except (FileNotFoundError, ProcessLookupError):
        return False
    except (PermissionError, IndexError):
        return True


def native_may_use_roots(process: Path, roots: list[Path]) -> bool:
    """Pinned SDK base_directory is passed as COPILOT_HOME, not an argv flag.

    Read only that scope value into the decision. Never log/export the environment.
    Explicit unrelated native homes are not a reason to interrupt another project.
    Missing/relative/uninspectable scope remains ambiguous and must block backup.
    """
    try:
        homes = [
            value.removeprefix(b"COPILOT_HOME=")
            for value in (process / "environ").read_bytes().split(b"\0")
            if value.startswith(b"COPILOT_HOME=")
        ]
        if len(homes) != 1:
            return True
        home = Path(os.fsdecode(homes[0]))
        if not home.is_absolute():
            return True
        home = home.resolve(strict=True)
        return any(home.is_relative_to(root) or root.is_relative_to(home) for root in roots)
    except (OSError, RuntimeError, ValueError):
        return True


def owned_identities(roots: list[Path]):
    """Read only the accepted native/preview/Unity process journals in these roots."""
    for root in roots:
        candidates = [root / "copilot-runtime/process.json"]
        jobs = root / ".proscenium/jobs"
        if jobs.exists():
            candidates.extend(jobs.glob("previews/**/process.json"))
            candidates.extend(jobs.glob("previews/**/native-process.json"))
            candidates.extend(jobs.glob("unity/**/worker.json"))
        for path in candidates:
            if not os.path.lexists(path):
                continue
            try:
                record = json.loads(safe_file(root, path.relative_to(root).as_posix()).read_text())
                identities = [record] if path.name != "worker.json" else [record["supervisor"]]
                if path.name == "worker.json" and record.get("process") is not None:
                    identities.append(record["process"])
                if "parent_pid" in record:
                    identities.append(
                        {"pid": record["parent_pid"], "start": record["parent_start"]}
                    )
                for identity in identities:
                    pid = identity["pid"]
                    start = str(identity.get("start_ticks", identity.get("start", "")))
                    if type(pid) is not int or pid <= 0 or not start.isdecimal() or int(start) <= 0:
                        raise ValueError("Invalid PID/start identity")
                    boot = identity.get("boot_id")
                    if boot is not None and (not isinstance(boot, str) or not boot.strip()):
                        raise ValueError("Invalid boot identity")
                    yield pid, start, boot
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise RecoveryError("corrupt_scoped_process_journal") from exc


def assert_stopped(roots: list[Path], *, proc_root: Path = Path("/proc")) -> None:
    """Locks/settlement are checked by the caller. Never signal or read credentials.

    An unassociated protected process is not evidence about this project. Known
    journal identities and positively scoped processes remain blockers, including
    when their other metadata is unreadable. Arbitrary external writers must be
    stopped by the operator: this scan cannot prove a global negative.
    """
    for pid, start, boot in owned_identities(roots):
        try:
            if (
                boot is not None
                and boot != (proc_root / "sys/kernel/random/boot_id").read_text().strip()
            ):
                continue  # Previous boot, not this worker. No job outcome is inferred.
            fields = (proc_root / str(pid) / "stat").read_text().rsplit(") ", 1)[1].split()
            if len(fields) < 20:
                raise ValueError("Truncated process identity")
            if fields[0] not in {"Z", "X", "x"} and fields[19] == start:
                raise RecoveryError(f"active_owned_process:{pid}")
            # A different start identity is PID reuse, not the original worker.
        except (FileNotFoundError, ProcessLookupError):
            continue
        except RecoveryError:
            raise
        except (PermissionError, ValueError, IndexError) as exc:
            raise RecoveryError(f"owned_process_inspection_denied:{pid}") from exc

    def scoped(value: str) -> bool:
        value = value.split("=", 1)[-1]
        return any(value == str(root) or value.startswith(str(root) + "/") for root in roots)

    own = os.getpid()
    for process in proc_root.iterdir():
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
            if any(scoped(os.fsdecode(arg)) for arg in argv):
                raise RecoveryError(f"active_or_ambiguous_process:{process.name}")
            native_ambiguous = executable == "copilot" and native_may_use_roots(process, roots)
            if native_ambiguous and still_live(process):
                raise RecoveryError(f"active_or_ambiguous_process:{process.name}")
            try:
                if scoped(os.readlink(process / "cwd")):
                    raise RecoveryError(f"active_or_ambiguous_process:{process.name}")
            except PermissionError:
                pass  # No association. Not evidence of a scoped worker.
            try:
                descriptors = list((process / "fd").iterdir())
            except PermissionError:
                descriptors = []
            for fd in descriptors:
                try:
                    if scoped(os.readlink(fd)):
                        raise RecoveryError(f"active_or_ambiguous_process:{process.name}")
                except (FileNotFoundError, PermissionError):
                    pass
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
