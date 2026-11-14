"""Dependency-free durable worker journal, also imported by Blender's Python."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(path: Path) -> dict:
    return dict(json.loads(path.read_bytes()))


def atomic(path: Path, value: dict, *, immutable: bool = True) -> None:
    data = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if immutable and path.exists():
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError("Conflicting immutable worker journal")
        return
    fd, name = tempfile.mkstemp(prefix=".journal-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        if immutable:
            os.link(temporary, path)
        else:
            temporary.replace(path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def identity(pid: int) -> dict | None:
    try:
        base = Path("/proc") / str(pid)
        fields = (base / "stat").read_text().rpartition(")")[2].split()
        return {
            "pid": pid,
            "start_ticks": int(fields[19]),
            "state": fields[0],
            "argv": (base / "cmdline").read_bytes().decode().strip("\0").split("\0"),
        }
    except (OSError, ValueError, IndexError):
        return None


def alive(record: dict) -> bool:
    actual = identity(record["pid"])
    return bool(
        actual
        and actual["start_ticks"] == record["start_ticks"]
        and actual["argv"] == record["argv"]
        and actual["state"] not in {"Z", "X"}
    )


def output(root: Path, name: str) -> Path:
    parts = name.split("/")
    if any(p in {"", ".", ".."} for p in parts) or Path(name).is_absolute():
        raise ValueError("Unsafe worker output path")
    path = root
    for part in parts:
        path /= part
        if path.is_symlink():
            raise ValueError("Symlink in worker output path")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Worker output escapes its admitted root")
    return path
