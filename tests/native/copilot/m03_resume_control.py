"""Preserve original M03 roots and control only this explicitly allocated follow-up."""

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from m03_control import stop
from proscenium.copilot.launcher import identity

BASE = Path("/home/quintin/.cache/proscenium-development")
ROOT = BASE / "m03-resume-proof-e3cddf8"
REPO = Path(__file__).resolve().parents[3]


def manifest(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            result[str(path.relative_to(directory))] = {"symlink": os.readlink(path)}
        elif path.is_file():
            with path.open("rb") as stream:
                result[str(path.relative_to(directory))] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    return result


def snapshot():
    assert not ROOT.exists(), "Never replace the allocated original follow-up"
    ROOT.mkdir(mode=0o700)
    for label in "ab":
        original = BASE / f"m03-runtime-proof-{label}"
        host = json.loads((original / "host.private.json").read_text())
        assert identity(host["server_pid"]) != host["server_start"]
        native = json.loads((original / "machine/copilot-runtime/process.json").read_text())
        assert identity(native["pid"]) != native["start"]
        before = manifest(original)
        destination = ROOT / "before" / label
        shutil.copytree(original, destination, symlinks=True)
        assert before == manifest(destination) == manifest(original)
        (ROOT / f"before-{label}.private.json").write_text(json.dumps(before, indent=2))
        print(json.dumps({"snapshot": label, "files": len(before), "exact_copy": True}))


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(label):
    assert (ROOT / f"before-{label}.private.json").exists()
    run = ROOT / label
    if run.exists():
        assert not (run / "host.private.json").exists(), "Do not replace an execution attempt"
        previous = json.loads((run / "server-process.json").read_text())
        assert identity(previous["pid"]) != previous["start"]
        (run / "server.private.log").rename(run / "server-bootstrap-failure.private.log")
        (run / "server-process.json").rename(run / "server-bootstrap-failure.json")
    else:
        run.mkdir(mode=0o700)
    with (run / "server.private.log").open("wb") as output:
        server = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("m03_resume_host.py")), label],
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "PYTHONPATH": str(REPO)},
        )
    (run / "server-process.json").write_text(
        json.dumps({"pid": server.pid, "start": identity(server.pid)})
    )
    deadline = time.monotonic() + 30
    while not (run / "host.private.json").exists():
        assert server.poll() is None, "Read this exact server log before any retry"
        assert time.monotonic() < deadline
        time.sleep(0.1)
    cdp = port()
    with (run / "browser.private.log").open("wb") as output:
        browser = subprocess.Popen(
            [
                str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080",
                "--force-device-scale-factor=1",
                f"--remote-debugging-port={cdp}",
                f"--user-data-dir={run}/browser-profile",
                "about:blank",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (run / "browser.private.json").write_text(
        json.dumps({"pid": browser.pid, "start": identity(browser.pid), "cdp": cdp})
    )
    print(json.dumps({"label": label, "server_pid": server.pid, "browser_pid": browser.pid}))


def export(label):
    original = BASE / f"m03-runtime-proof-{label}"
    db = sqlite3.connect(f"file:{original}/project/.proscenium/state.sqlite3?mode=ro", uri=True)
    try:
        result = {"records": {}, "events": []}
        for namespace, body in db.execute(
            "SELECT namespace,body FROM records ORDER BY namespace,id"
        ):
            result["records"].setdefault(namespace, []).append(json.loads(body))
        result["events"] = [
            json.loads(row[0]) for row in db.execute("SELECT body FROM events ORDER BY sequence")
        ]
        result["outbox"] = [
            dict(id=row[0], kind=row[1], state=row[2])
            for row in db.execute("SELECT id,kind,state FROM outbox ORDER BY id")
        ]
    finally:
        db.close()
    (ROOT / label / "after.private.json").write_text(json.dumps(result, indent=2))


def close(label):
    run = ROOT / label
    export(label)
    server = json.loads((run / "server-process.json").read_text())
    browser = json.loads((run / "browser.private.json").read_text())
    stop(server)
    stop(browser, group=True)
    original = BASE / f"m03-runtime-proof-{label}"
    native = json.loads((original / "machine/copilot-runtime/process.json").read_text())
    assert identity(native["pid"]) != native["start"], (
        "Do not claim native cleanup without exact exit"
    )
    export(label)
    records = {
        "server": server,
        "browser": browser,
        "native": {"pid": native["pid"], "start": native["start"]},
    }
    for record in records.values():
        record["same_identity_alive"] = identity(record["pid"]) == record["start"]
    records["helpers"] = []
    markers = original / "project/.proscenium/tool-output/m03"
    # Retain all helper observations, not just the first historic PID in each file.
    for marker in markers.glob("*.jsonl"):
        for line in marker.read_text().splitlines():
            row = json.loads(line)
            if row.get("event") == "started":
                records["helpers"].append(
                    {
                        "pid": row["pid"],
                        "observed_at": row["at"],
                        "proc_exists": Path(f"/proc/{row['pid']}").exists(),
                    }
                )
    (run / "cleanup.json").write_text(json.dumps(records, indent=2))
    print(json.dumps({"closed": label, "processes": records}))


if __name__ == "__main__":
    if sys.argv[1] == "snapshot":
        snapshot()
    elif sys.argv[1] == "start":
        start(sys.argv[2])
    elif sys.argv[1] == "export":
        export(sys.argv[2])
    elif sys.argv[1] == "close":
        close(sys.argv[2])
    elif sys.argv[1] == "ui-server":
        (ROOT / "ui-checks-server.json").write_text(
            json.dumps({"pid": os.getpid(), "start": identity(os.getpid())})
        )
        ThreadingHTTPServer(
            ("127.0.0.1", 4297),
            partial(SimpleHTTPRequestHandler, directory=str(REPO / "apps/frontend/dist")),
        ).serve_forever()
    else:
        raise ValueError("snapshot, start, export or close")
