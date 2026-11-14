"""Explicit isolated weight acquisition and CPU-only safetensors inspection."""

import fcntl
import hashlib
import json
import os
import shutil
import struct
import urllib.request
from pathlib import Path
from typing import Any

from .candidate import DISK_RESERVE, DOWNLOAD_CAP, SHA256, SIZE, URL, WEIGHT_PATH


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect_weights(path: Path = WEIGHT_PATH) -> dict:
    """Hash bytes and inspect only the JSON tensor directory, never load tensors."""
    if path.is_symlink() or path.stat().st_size != SIZE or digest(path) != SHA256:
        raise ValueError("checkpoint size/SHA-256 mismatch or symlink")
    with path.open("rb") as stream:
        length = struct.unpack("<Q", stream.read(8))[0]
        if not 0 < length < 16 * 1024**2:
            raise ValueError("invalid safetensors directory length")
        header = json.loads(stream.read(length))
    required_prefixes = ("model.", "conditioner.main_image_encoder.model.", "vae.")
    if not all(any(key.startswith(prefix) for key in header) for prefix in required_prefixes):
        raise ValueError("checkpoint lacks the expected DiT/vision/VAE tensor groups")
    groups: dict[str, int] = {}
    for key in header:
        if key != "__metadata__":
            group = key.split(".")[0]
            groups[group] = groups.get(group, 0) + 1
    return {
        "path": str(path),
        "size_bytes": SIZE,
        "sha256": SHA256,
        "tensor_groups": groups,
        "required_prefixes": list(required_prefixes),
        "header_bytes": length,
        "metadata": header.get("__metadata__", {}),
        "tensor_loading": False,
    }


def download() -> dict:
    """Fixed destination, locked partial resume, bounded public Range requests.

    Each requested range is charged BEFORE HTTP. Lost/failed requests retain that
    charge, so even process death cannot silently exceed the six-GiB allocation.
    There are no automatic network retries. Re-run explicitly to resume.
    """
    destination = WEIGHT_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    for parent in (destination.parent, destination.parent.parent):
        if parent.is_symlink():
            raise ValueError("model directories must not be symlinks")
    lock = destination.with_suffix(".lock")
    with lock.open("a+b") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if destination.is_symlink():
            raise ValueError("refusing symlink checkpoint")
        if destination.exists():
            return inspect_weights(destination)
        partial = destination.with_suffix(".part")
        journal = destination.with_suffix(".download.json")
        if partial.is_symlink() or journal.is_symlink():
            raise ValueError("refusing symlink download state")
        state: dict[str, Any] = (
            json.loads(journal.read_text())
            if journal.exists()
            else {
                "url": URL,
                "sha256": SHA256,
                "requested_bytes": 0,
            }
        )
        if state["url"] != URL or state["sha256"] != SHA256:
            raise ValueError("different candidate in existing download state")
        start = partial.stat().st_size if partial.exists() else 0
        if start > SIZE or (start and not journal.exists()):
            raise ValueError("untracked or oversized partial checkpoint")
        if shutil.disk_usage(destination.parent).free < SIZE - start + DISK_RESERVE:
            raise ValueError("insufficient disk after ten-GiB reserve")
        with partial.open("ab") as output:
            while start < SIZE:
                end = min(start + 64 * 1024**2, SIZE) - 1
                count = end - start + 1
                if state["requested_bytes"] + count > DOWNLOAD_CAP:
                    raise ValueError("six-GiB allocation exhausted, retain partial and escalate")
                state["requested_bytes"] += count
                temporary = journal.with_suffix(".new")
                with temporary.open("w") as receipt:
                    json.dump(state, receipt, indent=2)
                    receipt.flush()
                    os.fsync(receipt.fileno())
                temporary.replace(journal)
                request = urllib.request.Request(
                    f"{URL}?download=true&range_start={start}",
                    headers={"Range": f"bytes={start}-{end}"},
                )
                with urllib.request.urlopen(request, timeout=120) as response:
                    expected = f"bytes {start}-{end}/{SIZE}"
                    if response.status != 206 or response.headers.get("Content-Range") != expected:
                        raise ValueError("server did not honour exact bounded Range")
                    remaining = count
                    while remaining:
                        data = response.read(min(1024**2, remaining))
                        if not data:
                            raise ValueError("incomplete range, explicit resume required")
                        output.write(data)
                        remaining -= len(data)
                output.flush()
                os.fsync(output.fileno())
                start = output.tell()
                print(f"checkpoint bytes {start}/{SIZE}", flush=True)
        report = inspect_weights(partial)
        partial.replace(destination)
        report["path"] = str(destination)
        report["requested_bytes"] = state["requested_bytes"]
        return report
