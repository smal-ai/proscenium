"""Export only retained C16/C25 observations. Never dispatch or alter runtime state."""

import json
import sqlite3
from pathlib import Path

from m14_fault_driver import BLEND_SHA, REPO, ROOT
from proscenium.previews.journal import alive, read, sha


def export(path, body):
    """Readable generated evidence, refusing a different previously exported observation."""
    if path.exists() and read(path) != body:
        raise ValueError("Conflicting exported evidence")
    data = json.dumps(body, indent=2) + "\n"
    if path.exists() and path.read_text() == data:
        return
    pending = path.with_suffix(".pending")
    pending.write_text(data)
    pending.replace(path)


def main():
    result = read(ROOT / "report-latest.json")
    out = REPO / "docs/evidence/m14-native-faults"
    out.mkdir(parents=True, exist_ok=True)
    export(out / "actual-run.json", result)
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
    actual_identities = []
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
        native_profile = read(root / "native-profile.json")
        actual = native_profile["process"]
        recorded = read(root / "native-process.json")
        assert (actual["pid"], actual["start_ticks"]) == (recorded["pid"], recorded["start_ticks"])
        actual_identities.append(
            {
                "attempt_id": attempt["job"]["attempt_id"],
                "kind": "native-profile.json/process",
                "identity": actual,
                "alive_at_export": alive(actual),
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
    with sqlite3.connect(f"file:{ROOT}/machine/registry.sqlite3?mode=ro", uri=True) as db:
        claims = list(
            db.execute("SELECT resource,project_id,job_id,attempt_id FROM resource_claims")
        )
    with sqlite3.connect(f"file:{ROOT}/project/.proscenium/state.sqlite3?mode=ro", uri=True) as db:
        reservations = list(
            db.execute("SELECT id,task_id,target_id,state FROM budget_reservations")
        )
    assert not claims and not reservations
    export(
        out / "post-export-verification.json",
        {
            "actual_native_identities": actual_identities,
            "resource_claims_after": claims,
            "generation_reservations_after": reservations,
            "native_identity_note": "Initial native-process.json may capture the /bin/sh launcher before exec. The production native-profile records the actual Blender executable with the identical PID/start. Both identities are retained; actual Blender profiles are dead at export.",
        },
    )
    export(
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
