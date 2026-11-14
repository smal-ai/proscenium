"""Read-only retained M11 guards and private snapshots. No native effects."""

import hashlib
import importlib.metadata
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

from proscenium.contracts import validate
from proscenium.sources.common import relative

REPO = Path(__file__).resolve().parents[3]
INSTALLED = Path("/home/quintin/play/proscenium")
ORIGINAL = Path("/home/quintin/.cache/proscenium-development/milestones/m11-20261114-native-a")
PROJECT = ORIGINAL / "project"
PROJECT_ID = "project-6efca2f5-8ccd-49bb-bea1-f552ef065224"
WORK = "work-4a7e644e78514109b4a5232316e3aacc"
REQUEST = "1a914aa8-2bbc-461a-a87d-1bb7431de02c"
SESSION = "0cbee1a2-b5ed-4ab8-962b-ccf1d9f8a454"
HEAD = "8d49caaee980d18a140111e81b15a46c71e229ce"
ROOT = ORIGINAL / "publication-complete-20261114-a"
BUILD = Path("/tmp/opencode/m11-complete-build-17c0a59")
DATABASE = PROJECT / ".proscenium/state.sqlite3"


def write(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def records(namespace, database=DATABASE):
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        return {
            r[0]: json.loads(r[1])
            for r in db.execute(
                "SELECT id,body FROM records WHERE namespace=? ORDER BY id", (namespace,)
            )
        }


def identity(pid):
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return dict(pid=pid, start=stat[19])


def alive(record):
    try:
        stat = Path(f"/proc/{record['pid']}/stat").read_text().rsplit(")", 1)[1].split()
        return stat[19] == str(record["start"]) and stat[0] != "Z"
    except FileNotFoundError:
        return False


def headroom():
    available = next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    assert available >= 4 * 1024**3, available
    return dict(available_bytes=available, disk_free=shutil.disk_usage(ORIGINAL).free)


def checkpoint():
    result = records("checkpoint_intents")["m11-unity-source-checkpoint"]["receipt"]
    validate("CheckpointReceipt", result)
    assert result["source_commit"] == HEAD and result["state"] == "completed"
    for row in result["files"]:
        assert sha(relative(PROJECT, row["path"], exists=True)) == row["sha256"]
        value = subprocess.check_output(["git", "show", f"{HEAD}:{row['path']}"], cwd=PROJECT)
        assert hashlib.sha256(value).hexdigest() == row["sha256"]
    return result


def inspect():
    work = records("work")[WORK]
    assert work["state"] == "stopped" and work["state_revision"] == 10, work
    assert work["native_session_id"] == SESSION
    assert all(
        r["state"] in {"completed", "cancelled", "failed", "stopped"}
        for r in records("invocations").values()
    )
    stop = records("requests")["33048c8a-df79-4027-a6c2-10ba396e5044"]
    assert stop["receipt"]["state"] == "completed"
    assert (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True).strip()
        == HEAD
    )
    assert importlib.metadata.version("github-copilot-sdk") == "1.0.34"
    native_process = ORIGINAL / "machine/copilot-runtime/process.json"
    assert not alive(json.loads(native_process.read_text()))
    return dict(
        status="passed",
        work=work,
        stop=stop,
        checkpoint=checkpoint(),
        guard=headroom(),
        sdk="1.0.34",
        specification="3.2.0",
        contract="1.0.0",
        wire="1.0",
    )


def snapshot(label):
    target = ROOT / f"private-{label}"
    target.mkdir(mode=0o700, exist_ok=False)
    for name, source in {
        "project.sqlite3": DATABASE,
        "registry.sqlite3": ORIGINAL / "machine/registry.sqlite3",
    }.items():
        with (
            sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src,
            sqlite3.connect(target / name) as dst,
        ):
            src.backup(dst)
        (target / name).chmod(0o600)
    for name, source in {
        "native-events.jsonl": ORIGINAL
        / f"machine/copilot-runtime/home/session-state/{SESSION}/events.jsonl",
        "native-process.json": ORIGINAL / "machine/copilot-runtime/process.json",
    }.items():
        shutil.copyfile(source, target / name)
        (target / name).chmod(0o600)
    receipt = checkpoint()
    paths = [r["path"] for r in receipt["files"]] + [
        "assets/cube/asset.blend",
        "assets/cube/colour.png",
        "scenes/fixture/scene.blend",
        "project.json",
    ]
    result = dict(
        source_files={p: sha(PROJECT / p) for p in paths},
        old_reports={p.name: sha(p) for p in ORIGINAL.iterdir() if p.is_file()},
        head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True).strip(),
        refs=subprocess.check_output(
            ["git", "for-each-ref", "--format=%(refname) %(objectname)"], cwd=PROJECT, text=True
        ).splitlines(),
        git_index=sha(PROJECT / ".git/index") if (PROJECT / ".git/index").is_file() else None,
        private_copies={p.name: sha(p) for p in target.iterdir()},
        records={
            n: records(n, target / "project.sqlite3")
            for n in (
                "work",
                "requests",
                "invocations",
                "contexts",
                "jobs",
                "attempts",
                "artifacts",
                "job_inputs",
                "job_evidence",
                "versions",
                "items",
                "publications",
                "checkpoint_intents",
                "copilot.continuation_sends",
                "copilot.continuations",
                "copilot.bindings",
                "copilot.permissions",
                "copilot.completion",
                "copilot.idle",
                "decisions",
            )
        },
    )
    write(ROOT / f"{label}.private.json", result)
    return result
