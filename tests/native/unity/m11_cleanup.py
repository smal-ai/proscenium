"""Read-only cleanup verification after the owned supervisor has reaped its children."""

import json
import sys
from pathlib import Path

from tools.unity.worker import alive

root = Path(sys.argv[1])
output = Path(__file__).resolve().parents[3] / "docs/evidence/m11/cleanup.json"
records = []
for path in [root / "processes.json", *root.glob("processes-before*.json")]:
    info = json.loads(path.read_text())
    for kind in ("server", "browser", "supervisor"):
        value = info[kind]
        try:
            stat = Path(f"/proc/{value['pid']}/stat").read_text().rsplit(")", 1)[1].split()
            live = stat[19] == value["start"] and stat[0] != "Z"
        except FileNotFoundError:
            live = False
        records.append(dict(kind=kind, pid=value["pid"], start=value["start"], original_alive=live))
native = json.loads((root / "machine/copilot-runtime/process.json").read_text())
try:
    stat = Path(f"/proc/{native['pid']}/stat").read_text().rsplit(")", 1)[1].split()
    native_alive = stat[19] == str(native["start"]) and stat[0] != "Z"
except FileNotFoundError:
    native_alive = False
workers = []
for path in (root / "project/.proscenium/jobs").rglob("worker.json"):
    value = json.loads(path.read_text())
    workers.append(
        dict(
            job_id=value["job_id"],
            supervisor_alive=alive(value["supervisor"]),
            editor_alive=alive(value["process"]),
        )
    )
for path in (root / "project/.proscenium/jobs/native-workflows").glob("*/export-worker.json"):
    value = json.loads(path.read_text())
    workers.append(
        dict(
            kind="blender-export",
            supervisor_alive=alive(value["supervisor"]),
            blender_alive=alive(value["process"]),
        )
    )
assert not any(r["original_alive"] for r in records) and not native_alive
assert not any(
    r.get("supervisor_alive") or r.get("editor_alive") or r.get("blender_alive") for r in workers
)
result = dict(
    owned_host_browser_processes=records,
    latest_sdk_runtime=dict(pid=native["pid"], original_alive=native_alive),
    native_workers=workers,
    reaped=json.loads((root / "cleanup.json").read_text()),
    independent_services_touched=False,
)
output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
