"""Own-process/browser setup, allowlisted export and exact-identity cleanup only."""

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from proscenium.copilot.launcher import identity


def alive(record):
    return identity(record["pid"]) == record["start"]


def stop(record, *, group=False):
    if not alive(record):
        return
    fd = os.pidfd_open(record["pid"])
    try:
        if not alive(record):
            return
        if group:
            assert os.getpgid(record["pid"]) == record["pid"]
            os.killpg(record["pid"], signal.SIGTERM)
        else:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
    finally:
        os.close(fd)
    end = time.monotonic() + 25
    while alive(record) and time.monotonic() < end:
        time.sleep(0.1)
    if alive(record):
        raise RuntimeError(
            "Own process did not stop gracefully; inspect exact identity before force"
        )


def export(root):
    path = root / "project/.proscenium/state.sqlite3"
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    allowed = {
        "requests",
        "contexts",
        "work",
        "invocations",
        "decisions",
        "jobs",
        "attempts",
        "items",
        "versions",
        "artifacts",
        "copilot.control",
        "copilot.sends",
        "copilot.stops",
        "copilot.resumes",
        "copilot.payloads",
        "copilot.hooks",
        "copilot.session",
    }
    result = {"records": {}, "events": []}
    try:
        for namespace, body in connection.execute(
            "SELECT namespace,body FROM records ORDER BY namespace,id"
        ):
            if namespace in allowed:
                result["records"].setdefault(namespace, []).append(json.loads(body))
        result["events"] = [
            json.loads(row[0])
            for row in connection.execute("SELECT body FROM events ORDER BY sequence")
        ]
        result["outbox"] = [
            dict(id=row[0], kind=row[1], state=row[2])
            for row in connection.execute("SELECT id,kind,state FROM outbox ORDER BY id")
        ]
    finally:
        connection.close()
    (root / "database-export.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                "requests": len(result["records"].get("requests", [])),
                "events": len(result["events"]),
                "decisions": [
                    {k: d.get(k) for k in ("decision_id", "state", "delivery_state")}
                    for d in result["records"].get("decisions", [])
                ],
            }
        )
    )


def main(root, action):
    root = root.resolve()
    assert root.parent == Path(
        "/home/quintin/.cache/proscenium-development"
    ) and root.name.startswith("m03-runtime-proof-")
    if action == "browser":
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cdp = sock.getsockname()[1]
        argv = [
            str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--window-size=1920,1080",
            "--force-device-scale-factor=1",
            f"--remote-debugging-port={cdp}",
            f"--user-data-dir={root}/browser-profile",
            "about:blank",
        ]
        with (root / "browser.private.log").open("wb") as log:
            process = subprocess.Popen(
                argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
        record = dict(pid=process.pid, start=identity(process.pid), cdp=cdp, argv=argv)
        (root / "browser.private.json").write_text(json.dumps(record, indent=2))
        (root / "browser.private.json").chmod(0o600)
        print(json.dumps({k: record[k] for k in ("pid", "start", "cdp")}))
    elif action == "export":
        export(root)
    elif action == "stop":
        export(root)
        info = json.loads((root / "host.private.json").read_text())
        browser = json.loads((root / "browser.private.json").read_text())
        stop(dict(pid=info["server_pid"], start=info["server_start"]))
        stop(browser, group=True)
        result = dict(
            server_stopped=identity(info["server_pid"]) != info["server_start"],
            browser_stopped=not alive(browser),
            helpers=[],
        )
        for marker in (root / "project/.proscenium/tool-output/m03").glob("*.jsonl"):
            first = json.loads(marker.read_text().splitlines()[0])
            pid = first["pid"]
            # A still-active helper is evidence of incomplete cancellation, not something
            # cleanup may silently relabel as stopped. Its explicit self-timeout is <=70s.
            result["helpers"].append(
                dict(marker=marker.name, pid=pid, proc_exists=Path(f"/proc/{pid}").exists())
            )
        native = root / "machine/copilot-runtime/process.json"
        if native.exists():
            record = json.loads(native.read_text())
            result["native_stopped"] = not alive(record)
            result["native_proc_exists"] = Path(f"/proc/{record['pid']}").exists()
        (root / "cleanup.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    else:
        raise ValueError("browser, export or stop")


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
