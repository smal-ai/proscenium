"""Collect non-secret native evidence. Raw Editor logs remain in the disposable fixture."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.unity.workflow import sha256, write_json


def collect(root: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    receipts = []
    for number in (1, 2, 3):
        result = json.loads((root / f"result-{number}.json").read_text())
        shutil.copyfile(root / f"result-{number}.json", output / f"native-result-{number}.json")
        attempt = Path(result["evidence"])
        row: dict[str, Any] = dict(
            job_id=result["job"]["job_id"],
            attempt_id=result["job"]["attempt_id"],
            state=result["job"]["state"],
            logs={},
        )
        for name in ("editor.log", "console.log", "supervisor.log"):
            path = attempt / name
            if path.is_file():
                row["logs"][name] = dict(
                    path=str(path), sha256=sha256(path), bytes=path.stat().st_size
                )
        for name in (
            "request.json",
            "worker.json",
            "playmode.xml",
            "unity-result.json",
            "unity-result.json.guids.txt",
            "unity-result.json.metrics.txt",
        ):
            path = attempt / name
            if path.is_file():
                shutil.copyfile(path, output / f"attempt-{number}-{name}")
        receipts.append(row)
    for name in ("closure.json", "recipe.json", "allocation.json", "blocked-stale-lock.json"):
        shutil.copyfile(root / name, output / name)
    project = root / "unity/Fixture"
    shutil.copyfile(project / "Packages/packages-lock.json", output / "unity-packages-lock.json")
    shutil.copyfile(project / "Packages/manifest.json", output / "unity-packages-manifest.json")
    executed = {}
    for path in (project / "Assets/Proscenium").rglob("*"):
        if path.is_file() and path.suffix in {
            ".cs",
            ".asmdef",
            ".unity",
            ".meta",
            ".asset",
            ".mat",
        }:
            executed[path.relative_to(project).as_posix()] = sha256(path)
    write_json(output / "executed-unity-source-sha256.json", executed)
    shutil.copyfile(
        project / "Assets/Proscenium/Tests/PlayableTests.cs",
        output / "executed-PlayableTests.cs.txt",
    )
    export = json.loads((root / "environment.json").read_text())
    objects = []
    for obj in export["objects"]:
        coordinates = [[v[axis] for v in obj["vertices"]] for axis in ("x", "y", "z")]
        objects.append(
            dict(
                name=obj["name"],
                object_id=obj["object_id"],
                instance_id=obj["instance_id"],
                vertex_count=len(obj["vertices"]),
                triangle_count=len(obj["triangles"]) // 3,
                bounds_min=[min(axis) for axis in coordinates],
                bounds_max=[max(axis) for axis in coordinates],
                colour=obj["colour"],
            )
        )
    write_json(
        output / "export-inspection.json",
        dict(
            sha256=sha256(root / "environment.json"),
            blender_version=export["blender_version"],
            closure_digest=export["closure_digest"],
            units=export["units"],
            axes=export["axes"],
            objects=objects,
            note="Actual Blender evaluated mesh export. These exact bytes were consumed by the successful Unity API import.",
        ),
    )
    write_json(output / "native-log-identities.json", receipts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    collect(args.root, args.output)
