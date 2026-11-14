"""Private before-snapshot of the ORIGINAL M10 fixture. No runtime/model effects."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ORIGINAL = Path("/home/quintin/.cache/proscenium-development/milestones/m10-asset-a")
ROOT = Path("/home/quintin/.cache/proscenium-development/milestones/m10-continuation-a")
REQUEST = "927cf238-56c2-454e-a063-014e072f9010"
WORK = "work-bc19a5da5fcd4aaf9ae552a13d06b20d"
SESSION = "5971843f-f216-40f8-b8cb-995675f48161"
PROJECT = "project-f7d845f4-1910-4099-8578-ce15509690da"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    temporary = path.with_suffix(".new")
    with temporary.open("w") as output:
        json.dump(value, output, indent=2)
        output.write("\n")
    temporary.replace(path)


def records(namespace):
    with sqlite3.connect(
        f"file:{ORIGINAL}/project/.proscenium/state.sqlite3?mode=ro", uri=True
    ) as db:
        return {
            key: json.loads(body)
            for key, body in db.execute(
                "SELECT id,body FROM records WHERE namespace=?", (namespace,)
            )
        }


def main():
    ROOT.mkdir(parents=True, mode=0o700, exist_ok=False)
    private = ROOT / "before-private"
    private.mkdir(mode=0o700)
    for source, name in (
        (ORIGINAL / "machine/registry.sqlite3", "machine-registry.sqlite3"),
        (ORIGINAL / "project/.proscenium/state.sqlite3", "project.sqlite3"),
    ):
        with (
            sqlite3.connect(f"file:{source}?mode=ro", uri=True) as db,
            sqlite3.connect(private / name) as backup,
        ):
            db.backup(backup)
        (private / name).chmod(0o600)
    session = ORIGINAL / "machine/copilot-runtime/home/session-state" / SESSION
    shutil.copytree(session, private / "native-session", copy_function=shutil.copy2)
    files = {
        str(path.relative_to(ORIGINAL)): sha(path)
        for path in (ORIGINAL / "project/assets/e04").iterdir()
        if path.is_file()
    }
    fixture = json.loads((ORIGINAL / "fixture.json").read_text())
    proxy = Path(fixture["job_root"])
    for name in ("proxy.glb", "proxy-map.json", "task.json", "terminal.json"):
        files[str((proxy / name).relative_to(ORIGINAL))] = sha(proxy / name)
    history = session / "events.jsonl"
    original_request = records("requests")[REQUEST]
    original_work = records("work")[WORK]
    assert original_request["receipt"]["work_id"] == WORK
    assert original_work["native_session_id"] == SESSION
    assert not (ORIGINAL / "project/assets/e04/asset-v2.blend").exists()
    assert len(records("requests")) == 1
    with sqlite3.connect(
        f"file:{ORIGINAL}/project/.proscenium/state.sqlite3?mode=ro", uri=True
    ) as db:
        reservations = list(
            db.execute("SELECT id,task_id,target_id,state FROM budget_reservations")
        )
    e04 = Path("/home/quintin/.cache/proscenium-development/image-to-3d/runs/e04-live-b/project")
    with sqlite3.connect(f"file:{e04}/.proscenium/state.sqlite3?mode=ro", uri=True) as db:
        old_reservations = list(
            db.execute("SELECT id,task_id,target_id,state FROM budget_reservations")
        )
    snapshot = {
        "recorded_at": datetime.now(timezone(timedelta(hours=2))).isoformat(),
        "base": subprocess.check_output(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
        ).strip(),
        "request": original_request,
        "work": original_work,
        "original_context": records("contexts")[original_request["submission"]["context_id"]],
        "native_association": records("copilot.session"),
        "control": records("copilot.control"),
        "invocations": records("invocations"),
        "permissions": records("copilot.permissions"),
        "file_hashes": files,
        "history_sha256": sha(history),
        "git_refs": subprocess.check_output(
            ["git", "-C", str(ORIGINAL / "project"), "show-ref"], text=True
        ).splitlines(),
        "generation_reservations": reservations,
        "e04_generation_reservations": old_reservations,
        "e04_file_hashes": {
            str(path.relative_to(e04)): sha(path)
            for path in (e04 / "assets/e04").iterdir()
            if path.is_file()
        },
        "backup_files": [
            {"path": name, "sha256": sha(private / name)}
            for name in ("machine-registry.sqlite3", "project.sqlite3")
        ],
    }
    write(ROOT / "before.json", snapshot)
    os.chmod(ROOT / "before.json", 0o600)
    print(
        json.dumps(
            {
                "private_snapshot": str(ROOT),
                "request": REQUEST,
                "work": WORK,
                "session": SESSION,
                "history_sha256": snapshot["history_sha256"],
                "v2_exists": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
