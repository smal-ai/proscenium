"""Owned M02 processes and read-only M01 guard. Never starts authoring Blender."""

import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
M01 = Path.home() / ".cache/proscenium-development/milestones/m01-20261114T132348Z"


def guard():
    paths = [
        M01 / "project/scenes/loop/scene.blend",
        M01 / "project/.git/index",
        M01 / "project/.proscenium/state.sqlite3",
    ]
    paths += list((M01 / "project/.proscenium/artefacts").glob("sha256/*/content"))
    paths += list((M01 / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"))
    return {
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=M01 / "project", text=True
        ).strip(),
        "files": {
            str(p.relative_to(M01)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
            if p.is_file()
        },
    }


def spawn(root, name, argv):
    with (root / f"{name}.private.log").open("ab") as log:
        process = subprocess.Popen(
            argv,
            cwd=REPO,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return {
        "pid": process.pid,
        "start": Path(f"/proc/{process.pid}/stat").read_text().split(") ")[1].split()[19],
        "argv": argv,
    }


def alive(record):
    try:
        stat = Path(f"/proc/{record['pid']}/stat").read_text().split(") ")[1].split()
    except FileNotFoundError:
        return False
    assert stat[19] == record["start"]
    return stat[0] != "Z"


def stop(record):
    if alive(record):
        os.killpg(record["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 25
    while alive(record) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not alive(record), "Inspect owned process before force or retry"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "start":
        root = (
            Path.home()
            / ".cache/proscenium-development/milestones"
            / datetime.now(UTC).strftime("m02-%Y%m%dT%H%M%SZ")
        )
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        for name in ("commands", "receipts"):
            (root / name).mkdir()
        origin, cdp = f"http://127.0.0.1:{free_port()}", free_port()
        config = json.loads((REPO / "config.example.json").read_text())
        config.update(
            data_directory=str(root / "machine"),
            origin=origin,
            frontend_directory=str(REPO / "apps/frontend/dist"),
        )
        config["runtime"].update(
            cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            skill_directories=[str(REPO / "agent/skills")],
            mcp_servers={},
        )
        config["rendering"].update(claim_path=str(root / "render.lock"))
        (root / "config.json").write_text(json.dumps(config, indent=2))
        os.chmod(root / "config.json", 0o600)
        (root / "m01-before.json").write_text(json.dumps(guard(), indent=2))
        info = {
            "origin": origin,
            "root": str(root),
            "cdp": cdp,
            "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        }
        info["server"] = spawn(
            root,
            "server",
            [
                str(REPO / ".venv/bin/python"),
                str(REPO / "tests/native/studio/m02_host.py"),
                str(root),
            ],
        )
        info["browser"] = spawn(
            root,
            "browser",
            [
                str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
                "--headless",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080",
                f"--remote-debugging-port={cdp}",
                f"--user-data-dir={root / 'browser-profile'}",
                "about:blank",
            ],
        )
    else:
        root = Path(sys.argv[2])
        info = json.loads((root / "processes.json").read_text())
        assert info["boot"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        stop(info["server"])
        if action == "restart":
            info.setdefault("previous_servers", []).append(info["server"])
            info["server"] = spawn(root, "server", info["server"]["argv"])
        elif action == "stop":
            stop(info["browser"])
            after = guard()
            assert after == json.loads((root / "m01-before.json").read_text())
            (root / "m01-after.json").write_text(json.dumps(after, indent=2))
            (root / "cleanup.json").write_text(
                json.dumps(
                    {
                        "server_exited": True,
                        "browser_exited": True,
                        "m01_unchanged": True,
                        "independent_blender_preserved": Path("/proc/2726862").exists(),
                        "at": datetime.now(UTC).isoformat(),
                    },
                    indent=2,
                )
            )
        else:
            raise ValueError(action)
    (root / "processes.json").write_text(json.dumps(info, indent=2))
    print(
        json.dumps(
            {
                "root": str(root),
                "server_pid": info["server"]["pid"],
                "browser_pid": info["browser"]["pid"],
            }
        )
    )
