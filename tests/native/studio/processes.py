"""Restart/reap only M01-owned process identities, with PID-reuse and boot checks."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

root, action = Path(sys.argv[1]), sys.argv[2]
repo = Path(__file__).resolve().parents[3]
info = json.loads((root / "processes.json").read_text())
assert info["boot_id"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def alive(record):
    try:
        stat = Path(f"/proc/{record['pid']}/stat").read_text().split(") ")[1].split()
    except FileNotFoundError:
        return False
    assert stat[19] == str(record.get("start", record.get("process_start"))), "PID identity changed"
    return stat[0] != "Z"


def stop(record):
    if not alive(record):
        return
    os.killpg(record["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 20
    while alive(record) and time.monotonic() < deadline:
        time.sleep(0.1)
    if alive(record):
        raise RuntimeError("Owned process did not stop gracefully; inspect before force")


if action == "restart":
    stop(info["server"])
    info["previous_server"] = info["server"]
    argv = [arg for arg in info["server"]["argv"] if arg != "--pair"]
    with (root / "server-restarted.private.log").open("wb") as log:
        child = subprocess.Popen(
            argv,
            cwd=repo,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    info["server"] = {
        "pid": child.pid,
        "start": Path(f"/proc/{child.pid}/stat").read_text().split(") ")[1].split()[19],
        "argv": argv,
    }
    (root / "processes.json").write_text(json.dumps(info, indent=2))
    print(json.dumps({"new_server_pid": child.pid}))
elif action == "stop":
    stop(info["server"])
    stop(info["browser"])
    blender = json.loads((root / "project/.proscenium/tool-output/blender/launch.json").read_text())
    assert (
        blender["isolated"]
        and blender["port"] == 9877
        and blender["project"] == str(root / "project")
    )
    stop(blender)
    result = {
        "server_stopped": not alive(info["server"]),
        "browser_stopped": not alive(info["browser"]),
        "owned_blender_group_stopped": not alive(blender),
        "independent_blender_2726862_preserved": Path("/proc/2726862").exists(),
    }
    (root / "cleanup.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
else:
    raise ValueError("restart or stop required")
