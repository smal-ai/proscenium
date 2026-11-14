"""Export only retained C16/C25 observations. Never dispatch or alter runtime state."""

import json
from pathlib import Path

from m14_fault_driver import BLEND_SHA, REPO, ROOT
from proscenium.previews.journal import alive, atomic, read, sha


def main():
    result = read(ROOT / "report-latest.json")
    out = REPO / "docs/evidence/m14-native-faults"
    out.mkdir(parents=True, exist_ok=True)
    atomic(out / "actual-run.json", result)
    files = [
        ROOT / name
        for name in (
            "prepared.json",
            "admission-preflight.json",
            "allocation.json",
            "result.json",
            "report-latest.json",
            "reporting-repair-allocation.json",
            "import-preparation-repair.json",
        )
    ]
    files += list((ROOT / "operator-reports").glob("*.json"))
    identities = []
    rendered = []
    segments = []
    for attempt in result["attempts"]:
        root = Path(attempt["root"])
        files += [path for path in root.rglob("*") if path.is_file()]
        for label in ("process.json", "native-process.json"):
            record = read(root / label)
            identities.append(
                {
                    "attempt_id": attempt["job"]["attempt_id"],
                    "kind": label,
                    "identity": record,
                    "alive_at_export": alive(record),
                }
            )
        task = read(root / "task.json")
        verified = read(root / "renderer-verified.json")
        terminal = read(root / "terminal.json")
        assert (
            terminal["task_sha256"]
            == verified["task_sha256"]
            == sha((root / "task.json").read_bytes())
        )
        assert verified["renderer_files_sha256"] == task["renderer_files_sha256"]
        assert (
            sha((Path(task["source_root"]) / task["closure"]["entry_path"]).read_bytes())
            == BLEND_SHA
        )
        files += [
            Path(task["source_root"]) / task["closure"]["entry_path"],
            Path(task["source_root"]) / ".receipt.json",
        ]
        for path in root.glob("timing-*.json"):
            rendered.append(
                {
                    "attempt_id": attempt["job"]["attempt_id"],
                    "frame": int(path.stem.removeprefix("timing-")),
                    "timing": read(path),
                }
            )
        for path in root.glob("segment-*.json"):
            segments.append({"attempt_id": attempt["job"]["attempt_id"], "receipt": read(path)})
    atomic(
        out / "retention.json",
        {
            "root": str(ROOT),
            "identities": identities,
            "actual_rendered_frames": rendered,
            "normal_segment_encodes": segments,
            "files": [
                {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path.read_bytes())}
                for path in sorted(set(files))
            ],
            "media_policy": "Actual PNGs/MP4, source materialisations, injected bytes, both attempt roots and disk-backed databases remain in the private root. No automatic deletion.",
        },
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "rendered": len(rendered),
                "encodes": len(segments),
                "remaining_owned_processes": sum(row["alive_at_export"] for row in identities),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
