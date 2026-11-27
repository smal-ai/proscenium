"""Systemd user-service operations on a verified Proscenium unit."""

import argparse
import fcntl
import os
import re
import shlex
import stat
import subprocess
import sys
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from proscenium.configuration import APP_ROOT, config_home, default_config_path
from proscenium.control import _is_exclusive_user_group, _open_validated_directory, _trusted_sticky
from proscenium.operator import OperatorError, absolute_local_path, load_settings

SYSTEMCTL = ["systemctl", "--user"]
DEFAULT_UNIT = "proscenium.service"
ACTIONS = ("start", "stop", "restart", "status", "logs")
_UNIT_LIMIT = 64 * 1024
_SYMLINK_LIMIT = 40
# Reject directives outside the maintained shape, including executable hooks and
# dependencies that could start, stop, or enable another service as a side effect.
_DIRECTIVES = {
    "Unit": {"Description", "After"},
    "Service": {
        "Type",
        "WorkingDirectory",
        "Environment",
        "UMask",
        "ExecStart",
        "Restart",
        "KillMode",
        "TimeoutStopSec",
    },
    "Install": {"WantedBy"},
}


class Runner(Protocol):
    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]: ...


Confirm = Callable[[str], bool]


def confirm_external(prompt: str) -> bool:
    try:
        if not sys.stdin.isatty():
            return False
        print(prompt, end="", file=sys.stderr, flush=True)
        return sys.stdin.readline().strip().casefold() in {"y", "yes"}
    except (OSError, ValueError, KeyboardInterrupt):
        return False


@dataclass(frozen=True)
class _UnitFile:
    info: os.stat_result
    proscenium: bool
    text: str


@dataclass(frozen=True)
class _Confirmation:
    force: bool
    yes: bool
    confirm: Confirm

    def approve(self, unit: str, path: Path, action: str, file: _UnitFile) -> bool:
        if file.proscenium:
            return False
        if not self.force:
            raise OperatorError(
                f"Refusing {unit} ({path}): not a verified Proscenium unit. "
                "Re-run with --force to confirm managing an external user unit."
            )
        print(
            f"Warning: {action} on {unit} ({path}), not a verified Proscenium unit.",
            file=sys.stderr,
        )
        if self.yes:
            return False
        if not self.confirm(f"Proceed with {action} on external unit {unit}? [y/N]: "):
            raise OperatorError(
                "External unit action not confirmed. Confirm interactively or pass --force --yes."
            )
        return True


def unit_name(value: str) -> str:
    base = value.removesuffix(".service")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}", base) or ".." in base:
        raise argparse.ArgumentTypeError("invalid systemd unit name")
    return f"{base}.service"


def journal_lines(value: str) -> int:
    try:
        count = int(value)
        if 1 <= count <= 10_000:
            return count
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("--lines must be an integer from 1 to 10000")


def unit_directory() -> Path:
    return config_home() / "systemd/user"


def _safe_unit_input(path: Path, option: str) -> Path:
    try:
        for candidate in (path, path.expanduser().absolute()):
            if any(c.isspace() or c in "\0%\"'\\$" or ord(c) < 32 for c in str(candidate)):
                raise OperatorError(
                    f"{option} contains unsafe characters for an unquoted systemd path."
                )
        return absolute_local_path(path)
    except (ValueError, RuntimeError) as exc:
        raise OperatorError(f"{option} must name a safe local path.") from exc


@contextmanager
def _unit_directory_fd(
    path: Path, *, create: bool = False, missing_ok: bool = False
) -> Iterator[int | None]:
    try:
        fd = _open_validated_directory(path, create=create, allow_private_group=True)
    except FileNotFoundError as exc:
        if missing_ok:
            yield None
            return
        raise OperatorError("Systemd user unit directory does not exist.") from exc
    except OSError as exc:
        raise OperatorError(
            "Refusing an unsafe or inaccessible systemd user unit directory."
        ) from exc
    try:
        # Serialize cooperating operators without adding a lock file.
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def _is_proscenium_unit_text(text: str) -> bool:
    section = ""
    command = None
    for raw in text.split("\n"):
        # Systemd joins continuations before parsing directives, including comments.
        if raw.rstrip().endswith("\\"):
            return False
        line = raw.rstrip("\r").strip(" \t")
        if not line or line.startswith(("#", ";")):
            continue
        if any(ord(c) < 32 and c != "\t" for c in line) or "\\" in line:
            return False
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if section not in _DIRECTIVES:
                return False
            continue
        key, equals, value = line.partition("=")
        key = key.strip(" \t")
        if not equals or key not in _DIRECTIVES.get(section, ()) or any(c in value for c in "%$"):
            return False
        if key == "ExecStart":
            if command is not None:
                return False
            command = value
        elif key == "Environment" and not re.fullmatch(r"\s*PYTHONUNBUFFERED=[01]\s*", value):
            return False
        elif key == "WantedBy" and value.strip() != "default.target":
            return False
        elif key == "Type" and value.strip() not in {"simple", "exec"}:
            return False
    if command is None:
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if len(tokens) < 3 or tokens[1:3] != ["-m", "proscenium"]:
        return False
    executable = tokens[0]
    if not re.fullmatch(r"(?:/[A-Za-z0-9_./@+-]+/|/)?python(?:[23](?:\.\d+)?)?", executable):
        return False
    options = iter(tokens[3:])
    for token in options:
        flag, equals, value = token.partition("=")
        if flag not in {"--config", "--origin", "--frontend-dir"}:
            return False
        if not equals:
            value = next(options, "")
        if not value or value.startswith("-"):
            return False
    return True


def _inspect_unit(fd: int, name: str, *, local: bool = True) -> _UnitFile | None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        source = os.open(name, flags, dir_fd=fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OperatorError(
            f"Refusing {name}: cannot open the unit without following symlinks."
        ) from exc
    try:
        info = os.fstat(source)
        owners = (os.getuid(),) if local else (0, os.getuid())
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_mode & 0o022:
            raise OperatorError(f"Refusing {name}: expected a trusted regular unit file.")
        try:
            with os.fdopen(source, "rb", closefd=False) as stream:
                body = stream.read(_UNIT_LIMIT + 1)
            if len(body) > _UNIT_LIMIT:
                raise OperatorError(f"Refusing {name}: the unit file exceeds the inspection limit.")
            text = body.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise OperatorError(f"Refusing {name}: could not read the unit file.") from exc
        return _UnitFile(info, _is_proscenium_unit_text(text), text)
    finally:
        os.close(source)


def _inspect_fragment(path: Path, unit: str) -> _UnitFile:
    try:
        if not path.is_absolute() or path != absolute_local_path(path):
            raise ValueError("Invalid unit fragment path")
        parent = _open_validated_directory(path.parent, trusted_leaf=True, allow_private_group=True)
        try:
            info = _inspect_unit(parent, path.name, local=False)
            if info is None:
                raise FileNotFoundError
            return info
        finally:
            os.close(parent)
    except (OSError, ValueError, OperatorError) as exc:
        raise OperatorError(
            f"Refusing {unit}: systemd's loaded fragment is not a safely inspectable regular unit."
        ) from exc


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _require_same_unit(unit: str, expected: _UnitFile, current: _UnitFile | None) -> None:
    first, second = expected.info, current.info if current else None
    if (
        second is None
        or not _same_file(first, second)
        or (first.st_size, first.st_ctime_ns) != (second.st_size, second.st_ctime_ns)
    ):
        raise OperatorError(f"Refusing {unit}: the unit file changed during the operation.")


def _write_unit(fd: int, unit: str, body: str, *, replace: _UnitFile | None) -> None:
    temporary = f".{unit}.{uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    output = os.open(temporary, flags, 0o600, dir_fd=fd)
    try:
        with os.fdopen(output, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if replace is None:
            try:
                os.link(temporary, unit, src_dir_fd=fd, dst_dir_fd=fd)
            except FileExistsError as exc:
                raise OperatorError(
                    f"{unit} already exists. Re-run with --force to inspect and replace it."
                ) from exc
        else:
            _require_same_unit(unit, replace, _inspect_unit(fd, unit))
            os.replace(temporary, unit, src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=fd)
    os.fsync(fd)


def _loaded_fragment(unit: str, runner: Runner, *, shadow_check: bool = False) -> Path | None:
    try:
        reply = runner(
            [
                *SYSTEMCTL,
                "show",
                "-p",
                "LoadState,FragmentPath",
                "-p",
                "DropInPaths,NeedDaemonReload,Id",
                unit,
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except subprocess.SubprocessError as exc:
        raise OperatorError(f"Could not verify {unit} with systemctl --user show.") from exc
    properties = dict(
        line.split("=", 1) for line in (reply.stdout or "").splitlines() if "=" in line
    )
    if reply.returncode or properties.get("LoadState") not in {"loaded", "not-found"}:
        raise OperatorError(f"Could not verify {unit} with systemctl --user show.")
    if properties.get("DropInPaths") or properties.get("Id", unit) != unit:
        raise OperatorError(f"Refusing {unit}: systemd reports drop-ins or an alias.")
    if properties.get("NeedDaemonReload") == "yes":
        raise OperatorError(
            f"Refusing {unit}: run `systemctl --user daemon-reload` to reconcile the loaded unit, then retry."
        )
    fragment = properties.get("FragmentPath")
    loaded = properties["LoadState"] == "loaded"
    if (not loaded and fragment) or (shadow_check and loaded and not fragment):
        raise OperatorError(
            f"Refusing {unit}: systemd did not report a verifiable load state and fragment."
        )
    if not loaded:
        return None
    return Path(fragment) if fragment else None


def _verify_selection(
    unit: str,
    directory: Path,
    fd: int | None,
    fragment: Path | None,
    *,
    local_only: bool,
    read_only: bool = False,
    allow_external_fragment: bool = False,
) -> tuple[Path, _UnitFile]:
    if fragment is None and not read_only:
        raise OperatorError(
            f"Refusing {unit}: systemd has no loaded fragment. Install the unit or run "
            "`systemctl --user daemon-reload` first."
        )
    if fragment is not None and fragment != directory / unit:
        loaded = _inspect_fragment(fragment, unit)
        if local_only and not (
            allow_external_fragment
            and not loaded.proscenium
            and (fd is None or _inspect_unit(fd, unit) is None)
        ):
            raise OperatorError(
                f"Refusing {unit}: systemd is using a different fragment than the local unit."
            )
        return fragment, loaded
    info = _inspect_unit(fd, unit) if fd is not None else None
    if info is None:
        raise OperatorError(f"Refusing {unit}: no safely inspectable local user unit.")
    if fragment is not None and not _same_file(info.info, _inspect_fragment(fragment, unit).info):
        raise OperatorError(
            f"Refusing {unit}: the loaded fragment does not match the local unit file."
        )
    return directory / unit, info


def _render_unit(checkout: Path, config: Path) -> str:
    source = (APP_ROOT / "deployment/proscenium.service.in").read_text(encoding="utf-8")
    placeholders = ("@CHECKOUT@", "@CONFIG_FILE@")
    rendered = source.replace("@CHECKOUT@", str(checkout)).replace("@CONFIG_FILE@", str(config))
    if (
        "--pair" in rendered
        or any(token not in source or token in rendered for token in placeholders)
        or not _is_proscenium_unit_text(rendered)
    ):
        raise OperatorError("Refusing an invalid or unsafe shipped Proscenium unit template.")
    return rendered


def _trusted_interpreter_node(info: os.stat_result) -> bool:
    return (
        info.st_uid in (0, os.getuid())
        and not info.st_mode & 0o002
        and (not info.st_mode & 0o020 or _is_exclusive_user_group(info.st_uid, info.st_gid))
    )


def _trusted_interpreter(bin_fd: int) -> bool:
    pending = deque(["python"])
    flags = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
    current = os.dup(bin_fd)
    links = 0
    try:
        while pending:
            component = pending.popleft()
            parent = os.fstat(current)
            entry = os.open(component, flags, dir_fd=current)
            try:
                info = os.fstat(entry)
                if stat.S_ISLNK(info.st_mode):
                    links += 1
                    if (
                        links > _SYMLINK_LIMIT
                        or info.st_uid not in (0, os.getuid())
                        or not _trusted_interpreter_node(parent)
                    ):
                        return False
                    # Linux readlinkat with an empty path reads this exact pinned symlink.
                    target = os.readlink("", dir_fd=entry)
                    if target.startswith("/"):
                        root = os.open("/", flags | os.O_DIRECTORY)
                        os.close(current)
                        current = root
                        if not _trusted_interpreter_node(os.fstat(current)):
                            return False
                    pieces = [part for part in target.split("/") if part]
                    if target.endswith("/"):
                        pieces.append(".")  # A trailing slash requires a directory.
                    pending.extendleft(reversed(pieces))
                    continue
                if not pending:
                    return (
                        _trusted_interpreter_node(parent)
                        and stat.S_ISREG(info.st_mode)
                        and _trusted_interpreter_node(info)
                        and os.access(f"/proc/self/fd/{entry}", os.X_OK, effective_ids=True)
                    )
                if not stat.S_ISDIR(info.st_mode) or not (
                    _trusted_interpreter_node(info) or _trusted_sticky(info)
                ):
                    return False
                os.close(current)
                current = entry
                entry = -1
            finally:
                if entry >= 0:
                    os.close(entry)
        return False
    finally:
        os.close(current)


def _validate_checkout(checkout: Path) -> None:
    try:
        os.close(_open_validated_directory(checkout, trusted_leaf=False, allow_private_group=True))
    except OSError as exc:
        raise OperatorError(
            "--checkout must be a trusted existing directory without symlinks."
        ) from exc
    try:
        os.close(
            _open_validated_directory(
                checkout / ".venv", trusted_leaf=False, allow_private_group=True
            )
        )
        bin_fd = _open_validated_directory(
            checkout / ".venv/bin", trusted_leaf=False, allow_private_group=True
        )
    except OSError as exc:
        raise OperatorError(
            "Checkout needs trusted .venv and .venv/bin directories without symlinks."
        ) from exc
    try:
        trusted = _trusted_interpreter(bin_fd)
    except OSError:
        trusted = False
    finally:
        os.close(bin_fd)
    if not trusted:
        raise OperatorError(
            "Checkout needs .venv/bin/python pointing to a trusted executable regular file through trusted directories."
        )


def install(
    args: argparse.Namespace, runner: Runner, directory: Path, confirmation: _Confirmation
) -> int:
    checkout = _safe_unit_input(args.checkout or APP_ROOT, "--checkout")
    config = _safe_unit_input(args.config or default_config_path(), "--config")
    _validate_checkout(checkout)
    load_settings(config)
    rendered = _render_unit(checkout, config)

    target = directory / args.unit
    with _unit_directory_fd(directory, create=True) as fd:
        assert fd is not None
        existing = _inspect_unit(fd, args.unit)
        if existing is not None and not confirmation.force:
            confirmation.approve(args.unit, target, "overwrite", existing)
            raise OperatorError(f"{args.unit} already exists. Use --force to replace it.")
        try:
            fragment = _loaded_fragment(args.unit, runner, shadow_check=True)
        except FileNotFoundError as exc:
            raise OperatorError(
                "systemctl was not found for the shadowing check. No unit file was written."
            ) from exc
        shadowed = None
        if fragment is not None:
            if fragment != target:
                shadowed = _inspect_fragment(fragment, args.unit)
            else:
                _, loaded = _verify_selection(args.unit, directory, fd, fragment, local_only=True)
                if existing is None:
                    raise OperatorError(
                        f"Refusing {args.unit}: a unit appeared during the shadowing check."
                    )
                _require_same_unit(args.unit, existing, loaded)
        prompted = existing is not None and confirmation.approve(
            args.unit, target, "overwrite", existing
        )
        if fragment is not None and shadowed is not None:
            prompted = confirmation.approve(args.unit, fragment, "shadow", shadowed) or prompted
        if prompted:
            try:
                current_fragment = _loaded_fragment(args.unit, runner, shadow_check=True)
            except FileNotFoundError as exc:
                raise OperatorError(
                    "systemctl was not found to recheck shadowing. No unit file was written."
                ) from exc
            if fragment != current_fragment:
                raise OperatorError(
                    f"Refusing {args.unit}: the loaded fragment changed during confirmation."
                )
            if fragment == target and existing is not None:
                _, current = _verify_selection(args.unit, directory, fd, fragment, local_only=True)
                _require_same_unit(args.unit, existing, current)
        if fragment is not None and shadowed is not None:
            _require_same_unit(args.unit, shadowed, _inspect_fragment(fragment, args.unit))
        _write_unit(fd, args.unit, rendered, replace=existing)
        if not (checkout / "apps/frontend/dist/index.html").is_file():
            print(
                "Build the frontend with 'make build-frontend' before opening Studio.",
                file=sys.stderr,
            )
        step = "daemon-reload"
        try:
            code = runner([*SYSTEMCTL, step]).returncode
            for action, requested in (("enable", args.enable), ("start", args.start)):
                if code or not requested:
                    continue
                step = "show"
                fragment = _loaded_fragment(args.unit, runner)
                _, loaded = _verify_selection(args.unit, directory, fd, fragment, local_only=True)
                if not loaded.proscenium:
                    raise OperatorError(
                        f"Refusing {args.unit}: expected the installed Proscenium unit."
                    )
                step = action
                code = runner([*SYSTEMCTL, step, args.unit]).returncode
        except FileNotFoundError:
            print(
                f"Unit written to {target}; systemctl was not found, so systemctl --user {step} could not run.",
                file=sys.stderr,
            )
            return 1
        except OperatorError as exc:
            print(f"Unit written to {target}; {exc}", file=sys.stderr)
            return 2
        if code:
            print(f"Unit written to {target}; {step} failed.", file=sys.stderr)
            return code
    suffix = f" --unit {args.unit}" if args.unit != DEFAULT_UNIT else ""
    pair_hint = f" --config {config}" if config != default_config_path().absolute() else ""
    print(
        f"Installed {target}. Use `proscenium pair{pair_hint}` or `proscenium service status{suffix}`."
    )
    return 0


def _also_targets(value: str) -> list[str]:
    targets = [word for word in shlex.split(value) if word]
    if "\\" in value or any(not re.fullmatch(r"[A-Za-z0-9_.:@+-]+", word) for word in targets):
        raise ValueError("Unsupported Also= value")
    return targets


def _local_also_targets(text: str) -> list[str]:
    section = ""
    pending = ""
    targets: list[str] = []
    for raw in text.removeprefix("\ufeff").removesuffix("\n").split("\n"):
        line = raw.removesuffix("\r")
        if line.lstrip(" \t").startswith(("#", ";")):
            continue  # Systemd skips comments even inside a continuation.
        if any(ord(c) < 32 and c != "\t" for c in line):
            raise ValueError("Invalid unit line")
        if line.rstrip(" \t").endswith("\\"):
            if not line.endswith("\\") or line.endswith("\\\\"):
                raise ValueError("Ambiguous unit continuation")
            pending += line[:-1] + " "
            continue
        line = (pending + line).strip(" \t")
        pending = ""
        if not line:
            continue
        if line.startswith("["):
            match = re.fullmatch(r"\[([A-Za-z0-9-]+)\]", line)
            if not match:
                raise ValueError("Invalid unit section")
            section = match[1]
        else:
            key, separator, value = line.partition("=")
            if not separator or not section:
                raise ValueError("Invalid unit directive")
            if section == "Install" and key.strip(" \t") == "Also":
                targets.extend(_also_targets(value))
    if pending:
        raise ValueError("Incomplete unit continuation")
    return targets


def _guard_external_uninstall(unit: str, selected: _UnitFile, runner: Runner) -> None:
    targets = []
    unavailable = []
    try:
        targets.extend(_local_also_targets(selected.text))
    except ValueError:
        unavailable.append("local unit file")
    try:
        result = runner(
            [*SYSTEMCTL, "show", "-p", "Also", unit],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        rows = (result.stdout or "").splitlines()
        if result.returncode or len(rows) != 1 or not rows[0].startswith("Also="):
            raise ValueError("Systemd did not report Also=")
        targets.extend(_also_targets(rows[0].removeprefix("Also=")))
    except (OSError, ValueError, subprocess.SubprocessError):
        unavailable.append("systemd")
    if targets or unavailable:
        reasons = []
        if targets:
            reasons.append("Also= names companion units: " + ", ".join(sorted(set(targets))))
        if unavailable:
            reasons.append("could not verify Also= from " + " and ".join(unavailable))
        raise OperatorError(
            f"Refusing to uninstall {unit}: {'. '.join(reasons)}. "
            "Review the unit, disable it directly with `systemctl --user`, and remove its local file separately."
        )


def _uninstall(unit: str, directory: Path, fd: int, expected: _UnitFile, runner: Runner) -> int:
    for action, harmless in (
        ("stop", ("not loaded", "not active")),
        ("disable", ("not enabled", "does not exist", "no such file or directory")),
    ):
        if action == "disable":
            fragment = _loaded_fragment(unit, runner)
            _, current = _verify_selection(unit, directory, fd, fragment, local_only=True)
            _require_same_unit(unit, expected, current)
        if not expected.proscenium:
            _guard_external_uninstall(unit, expected, runner)
            _require_same_unit(unit, expected, _inspect_unit(fd, unit))
        result = runner(
            [*SYSTEMCTL, action, unit],
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
        )
        if result.returncode and not any(
            value in (result.stderr or "").casefold() for value in harmless
        ):
            print(f"Could not {action} {unit}; the unit file was retained.", file=sys.stderr)
            return result.returncode
    _require_same_unit(unit, expected, _inspect_unit(fd, unit))
    os.unlink(unit, dir_fd=fd)
    os.fsync(fd)
    try:
        code = runner([*SYSTEMCTL, "daemon-reload"]).returncode
    except FileNotFoundError:
        print(f"Removed {unit}, but systemctl was not found for daemon-reload.", file=sys.stderr)
        return 1
    if code:
        print(f"Removed {unit}, but daemon-reload failed.", file=sys.stderr)
        return code
    print(f"Uninstalled {unit}. Configuration and project data were not changed.")
    return 0


def service_command(
    args: argparse.Namespace,
    runner: Runner,
    directory: Path,
    *,
    confirm: Confirm = confirm_external,
) -> int:
    try:
        directory = absolute_local_path(directory)
    except (ValueError, RuntimeError) as exc:
        raise OperatorError("Invalid systemd user unit directory.") from exc
    confirmation = _Confirmation(args.force, args.yes, confirm)
    if confirmation.yes and not confirmation.force:
        raise OperatorError("--yes requires --force.")
    if args.action == "install":
        return install(args, runner, directory, confirmation)
    tool = "systemctl"
    try:
        read_only = args.action in {"status", "logs"}
        allow_external_fragment = args.action in {"start", "stop", "restart"}
        with _unit_directory_fd(directory, missing_ok=True) as fd:
            fragment = _loaded_fragment(args.unit, runner)
            path, selected = _verify_selection(
                args.unit,
                directory,
                fd,
                fragment,
                local_only=not read_only,
                read_only=read_only,
                allow_external_fragment=allow_external_fragment,
            )
            prompted = confirmation.approve(args.unit, path, args.action, selected)
            if not selected.proscenium:
                _require_same_unit(args.unit, selected, _inspect_fragment(path, args.unit))
            if prompted:
                confirmed_fragment = _loaded_fragment(args.unit, runner)
                if fragment != confirmed_fragment:
                    raise OperatorError(
                        f"Refusing {args.unit}: the loaded fragment changed during confirmation."
                    )
                _, current = _verify_selection(
                    args.unit,
                    directory,
                    fd,
                    confirmed_fragment,
                    local_only=not read_only,
                    read_only=read_only,
                    allow_external_fragment=allow_external_fragment,
                )
                _require_same_unit(args.unit, selected, current)
            if args.action == "uninstall":
                assert fd is not None
                return _uninstall(args.unit, directory, fd, selected, runner)
            if not read_only:
                return runner([*SYSTEMCTL, args.action, args.unit]).returncode
        if args.action == "logs":
            tool = "journalctl"
            command = [tool, "--user", "-u", args.unit, "-n", str(args.lines), "--no-pager"]
            return runner([*command, "-f"] if args.follow else command).returncode
        return runner([*SYSTEMCTL, args.action, args.unit]).returncode
    except FileNotFoundError:
        print(f"{tool} was not found on PATH.", file=sys.stderr)
        return 1
