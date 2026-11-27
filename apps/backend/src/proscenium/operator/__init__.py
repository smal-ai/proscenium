"""Local operator commands, separate from the running application."""

import os
import stat
from pathlib import Path

from proscenium.api.errors import DomainError
from proscenium.configuration import Settings, default_config_path
from proscenium.control import _open_validated_directory

_CONFIG_LIMIT = 1024 * 1024


class OperatorError(Exception):
    """An actionable error safe to print without private configuration values."""


def absolute_local_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    if ".." in path.parts or "\0" in str(path):
        raise ValueError("Expected a path without parent traversal or NUL")
    # Linux treats the double-slash anchor like '/'. Use one spelling for comparisons.
    return Path("/", *path.parts[1:])


def _open_safe_file_fd(path: Path) -> tuple[int, os.stat_result]:
    try:
        parent = _open_validated_directory(path.parent, trusted_leaf=True, allow_private_group=True)
    except FileNotFoundError as exc:
        # Only a missing leaf under a validated existing chain permits defaults.
        raise OperatorError("Configuration parent directory does not exist.") from exc
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise OperatorError("Configuration must be a user-owned regular JSON file.")
        return fd, info
    except BaseException:
        os.close(fd)
        raise


def load_config(path: Path, *, allow_missing: bool = False) -> tuple[Settings, int | None]:
    try:
        path = absolute_local_path(path)
        fd, info = _open_safe_file_fd(path)
    except FileNotFoundError as exc:
        if allow_missing:
            return Settings(), None
        raise OperatorError("Configuration must be an existing regular JSON file.") from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise OperatorError(
            "Configuration must be an accessible regular JSON file without symlinks."
        ) from exc
    try:
        with os.fdopen(fd, "rb") as stream:
            if info.st_size > _CONFIG_LIMIT:
                raise OperatorError("Configuration exceeds the 1 MiB limit.")
            contents = stream.read(_CONFIG_LIMIT + 1)
        if len(contents) > _CONFIG_LIMIT:
            raise OperatorError("Configuration exceeds the 1 MiB limit.")
        return Settings._from_json_text(
            contents.decode("utf-8"), config_dir=path.parent
        ), info.st_mode
    except (DomainError, OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        # Schema and input errors can contain private values. Keep them out of diagnostics.
        raise OperatorError("Cannot read or validate the configuration JSON.") from exc


def load_settings(path: Path | None) -> Settings:
    return load_config(path or default_config_path(), allow_missing=path is None)[0]
