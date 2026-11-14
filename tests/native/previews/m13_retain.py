"""Retain small, already-proven M13 bytes for portable integrity checks.

Read-only access to the exact recorded fixture. No model, render, encode, Git
checkout or native-session operation. Existing identical copies are reused.
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.operations.applicability import local_git_environment  # noqa: E402

EVIDENCE = ROOT / "docs/evidence/m13"


def retain() -> dict:
    record = json.loads((EVIDENCE / "result.json").read_bytes())
    original = Path(record["retained_root"]).resolve(strict=True)
    project = original / "project"
    payloads: dict[str, bytes] = {}
    references: dict[str, list[str]] = {}

    def add(data: bytes, expected: str, pointer: str) -> None:
        if not re.fullmatch("[0-9a-f]{64}", expected):
            raise ValueError("Invalid recorded content hash")
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Recorded source bytes changed: {pointer}")
        if len(data) > 1024**2:
            raise ValueError("This retention allocation permits only small M13 media/documents")
        payloads[expected] = data
        references.setdefault(expected, []).append(pointer)

    def artifact(expected: str, pointer: str) -> None:
        path = project / ".proscenium/artefacts/sha256" / expected / "content"
        if not path.resolve(strict=True).is_relative_to(original):
            raise ValueError("Recorded artefact escaped the retained fixture")
        add(path.read_bytes(), expected, pointer)

    for number, edit in enumerate(record["edits"]):
        prefix = f"/edits/{number}"
        for index, frame in enumerate(edit["manifest"]["input_frames"]):
            artifact(frame["sha256"], f"{prefix}/manifest/input_frames/{index}")
        for index, row in enumerate(edit["artifacts"]):
            artifact(row["sha256"], f"{prefix}/artifacts/{index}")
        for index, download in enumerate(edit["downloads"]):
            path = Path(download["path"]).resolve(strict=True)
            if not path.is_relative_to(original):
                raise ValueError("Recorded download escaped the retained fixture")
            add(path.read_bytes(), download["sha256"], f"{prefix}/downloads/{index}")
        for index, file in enumerate(edit["version"]["source"]["files"]):
            origin = file["origin"]
            name = Path(origin["path"])
            if (
                not re.fullmatch("[0-9a-f]{40}", origin["commit"])
                or name.is_absolute()
                or ".." in name.parts
                or not name.is_relative_to("sequences/m13")
            ):
                raise ValueError("Unexpected source outside the M13 sequence-document allocation")
            data = subprocess.check_output(
                ["git", "--no-replace-objects", "show", f"{origin['commit']}:{name.as_posix()}"],
                cwd=project,
                env=local_git_environment(),
                timeout=10,
            )
            add(data, origin["sha256"], f"{prefix}/version/source/files/{index}")
    artifact(record["annotation"]["target"]["displayed_sha256"], "/annotation/target")
    if len(payloads) > 64 or sum(map(len, payloads.values())) > 16 * 1024**2:
        raise ValueError("Portable M13 retention exceeds the bounded allocation")

    destination = EVIDENCE / "retained"
    destination.mkdir(exist_ok=True)
    for identity, data in payloads.items():
        path = destination / identity
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("Existing retained evidence differs")
        else:
            with path.open("xb") as output:
                output.write(data)
    manifest = {
        "format": "proscenium-m13-portable-bytes-1",
        "source_report_sha256": hashlib.sha256((EVIDENCE / "result.json").read_bytes()).hexdigest(),
        "meaning": "Hash-verified copies of actual retained outputs and committed source documents, not new execution",
        "files": {
            identity: {"bytes": len(data), "record_pointers": references[identity]}
            for identity, data in sorted(payloads.items())
        },
    }
    path = EVIDENCE / "retained-bytes.json"
    data = (json.dumps(manifest, indent=2) + "\n").encode()
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("Existing retention manifest differs")
    else:
        with path.open("xb") as output:
            output.write(data)
    return {"files": len(payloads), "bytes": sum(map(len, payloads.values()))}


if __name__ == "__main__":
    print(json.dumps(retain()))
