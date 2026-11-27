"""Copy a bounded SQLite WAL snapshot without dropping the server's POSIX locks."""

import os
import stat
import sys
from pathlib import Path

_SIZE_LIMIT = 64 * 1024 * 1024


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _copy(source: Path, target: Path, expected: os.stat_result) -> None:
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or _identity(info) != _identity(expected):
            raise OSError("Registry snapshot source changed")
        with target.open("xb") as output:
            remaining = info.st_size
            while remaining:
                chunk = os.read(fd, min(remaining, 1024 * 1024))
                if not chunk:
                    raise OSError("Registry snapshot source truncated")
                output.write(chunk)
                remaining -= len(chunk)
    finally:
        os.close(fd)


def _snapshot(source: Path, target: Path) -> None:
    sources = (source, source.with_name(source.name + "-wal"))
    targets = (target, target.with_name(target.name + "-wal"))
    initial = [path.lstat() for path in sources]
    if (
        not all(stat.S_ISREG(info.st_mode) for info in initial)
        or not initial[1].st_size
        or sum(info.st_size for info in initial) > _SIZE_LIMIT
    ):
        raise OSError("Registry snapshot source is not eligible")

    for path, copy, info in zip(sources, targets, initial, strict=True):
        _copy(path, copy, info)
    # Check both files after the copy in case a concurrent connection closed
    # and checkpointed the registry while the snapshot was being taken.
    for path, info in zip(sources, initial, strict=True):
        if _identity(path.lstat()) != _identity(info):
            raise OSError("Registry changed during snapshot")


if __name__ == "__main__":
    os.umask(0o077)
    try:
        _snapshot(Path(sys.argv[1]), Path(sys.argv[2]))
    except OSError:
        sys.exit(1)
