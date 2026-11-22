"""Dependency-free durable worker journal, also imported by Blender's Python."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

RENDERER_FILES = ("blender_worker.py", "geometry_nodes.py", "journal.py")
UNSUPPORTED_BLENDER_FEATURE_REASON = (
    "Required Blender runtime feature is not supported by proscenium-blender-1; "
    "use features=[] or supported Cycles CPU features"
)
SUPPORTED_BLENDER_FEATURES = frozenset(
    {
        "cycles",
        "cycles-cpu",
        "cpu",
        "denoise",
        "openimagedenoise",
        "oidn",
        "agx",
        "mesh",
        "meshes",
        "native-mesh",
        "curve",
        "curves",
        "font",
        "text",
        "surface",
        "metaball",
        "materials",
        "modifiers",
        "subsurf",
        "subdivision",
        "bevel",
        "solidify",
        "boolean",
        "array",
        "mirror",
        "gltf",
        "glb",
        "proxy",
        "animation",
        "keyframes",
        "linked-libraries",
        "linked-textures",
        "external-textures",
        "packed-textures",
        "geometry-nodes",
        "static-hair",
        "python",
    }
)


def normalise_blender_feature(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def unsupported_blender_features(features: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    # Keep original strings for diagnostics. Malformed native task values may
    # not be hashable or sortable, so report their string representations.
    return sorted(
        {
            item if isinstance(item, str) else repr(item)
            for item in features
            if not isinstance(item, str)
            or normalise_blender_feature(item) not in SUPPORTED_BLENDER_FEATURES
        }
    )


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def renderer_hashes(directory: Path | None = None) -> dict[str, str]:
    """Seal the fixed Blender entry point and its complete direct local imports."""
    directory = directory or Path(__file__).parent
    return {name: sha((directory / name).read_bytes()) for name in RENDERER_FILES}


def verify_renderer(task: dict, directory: Path | None = None) -> dict[str, str]:
    expected = task.get("renderer_files_sha256")
    # Legacy tasks were never sealed transitively. Do not invent historical hashes
    # from today's files. Already-running workers/journals remain reconcilable.
    if not isinstance(expected, dict) or set(expected) != set(RENDERER_FILES):
        raise ValueError("Renderer dependency seal missing or incomplete; native spawn refused")
    actual = renderer_hashes(directory)
    if actual != expected or actual["blender_worker.py"] != task.get("entrypoint_sha256"):
        raise ValueError("Installed renderer dependency changed since attempt preparation")
    return actual


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
