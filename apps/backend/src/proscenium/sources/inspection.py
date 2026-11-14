"""Trusted, bounded native inspection. No implicit Blender launch on HTTP/source reads."""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.contracts import validate
from proscenium.services import Services
from proscenium.sources.common import managed, relative, sha
from proscenium.sources.resolver import closure_digest


@dataclass(frozen=True)
class BlenderInspectionAllocation:
    allocation_id: str
    executable: Path
    threads: int = 4
    timeout: int = 180


def inspect_blender(
    services: Services,
    project_id: str,
    materialisation: Path,
    allocation: BlenderInspectionAllocation,
    *,
    expected_digest: str | None = None,
) -> dict[str, Any]:
    if (
        not allocation.allocation_id
        or not 1 <= allocation.threads <= 4
        or not 1 <= allocation.timeout <= 180
    ):
        raise ValueError("An explicitly bounded Blender CPU inspection allocation is required")
    root = Path(services.registry.get(project_id)["path"])
    try:
        relative_name = materialisation.relative_to(root).as_posix()
    except ValueError as exc:
        raise DomainError("scope_denied", 403) from exc
    if not relative_name.startswith(".proscenium/snapshots/"):
        raise DomainError("scope_denied", 403)
    # The managed helper expects file slots, so validate the sealed receipt then its parent.
    receipt_path = managed(root, relative_name + "/.receipt.json", exists=True)
    receipt = json.loads(receipt_path.read_bytes())
    validate("SourceClosure", receipt["closure"])
    digest = closure_digest(receipt["closure"])
    declared = {row["destination"]: row["origin"]["sha256"] for row in receipt["closure"]["files"]}
    if (
        digest != receipt["closure"]["digest"]
        or materialisation.parent.name != digest
        or expected_digest is not None
        and digest != expected_digest
        or receipt["files"] != declared
    ):
        raise DomainError("hash_mismatch", 422)
    if receipt["closure"]["required_runtime"]["application"].lower() != "blender":
        raise DomainError("invalid_request")
    for name, digest in receipt["files"].items():
        if sha(relative(materialisation, name, exists=True).read_bytes()) != digest:
            raise DomainError("hash_mismatch", 422)
    entry = relative(materialisation, receipt["closure"]["entry_path"], exists=True)
    executable = allocation.executable.resolve(strict=True)
    command = [
        str(executable),
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--threads",
        str(allocation.threads),
        str(entry),
        "--python-exit-code",
        "2",
        "--python",
        str(Path(__file__).with_name("blender_inspect.py")),
        "--",
        str(materialisation),
        allocation.allocation_id,
    ]
    try:
        process = subprocess.run(
            command,
            cwd=materialisation,
            capture_output=True,
            timeout=allocation.timeout,
            env={
                **os.environ,
                "OMP_NUM_THREADS": str(allocation.threads),
                "OPENBLAS_NUM_THREADS": str(allocation.threads),
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DomainError(
            "source_unavailable", 422, "Allocated Blender inspection did not complete"
        ) from exc
    lines = process.stdout.decode(errors="replace").splitlines()
    payloads = [
        line.removeprefix("PROSCENIUM_SOURCE_INSPECTION=")
        for line in lines
        if line.startswith("PROSCENIUM_SOURCE_INSPECTION=")
    ]
    if len(payloads) != 1:
        raise DomainError(
            "source_unavailable", 422, "Blender did not return a source inspection receipt"
        )
    result = dict(json.loads(payloads[0]))
    if result["missing"]:
        raise DomainError("dependency_missing", 422, missing=result["missing"])
    if process.returncode:
        raise DomainError("source_unavailable", 422)
    if result["version"] != receipt["closure"]["required_runtime"]["version"]:
        raise DomainError("capability_unavailable", 501, "Pinned Blender version is not available")
    result.update(
        source_closure_digest=receipt["closure"]["digest"],
        threads=allocation.threads,
        native_exit_code=process.returncode,
        executable=str(executable),
        source_files=receipt["files"],
    )
    for name, digest in receipt["files"].items():
        if sha(relative(materialisation, name, exists=True).read_bytes()) != digest:
            raise DomainError("hash_mismatch", 422)
    return result
