"""Explicitly allocated M06 host. Own/reap only its server and browser children."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[3]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def identity(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().split(") ")[1].split()
        return {"pid": pid, "start": stat[19], "state": stat[0]}
    except FileNotFoundError:
        return None


def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    reference = Image.new("RGB", (640, 360), "#d8e6e9")
    draw = ImageDraw.Draw(reference)
    draw.text((18, 14), "SYNTHETIC OWNER REFERENCE / PAPER KITE / NOT GENERATED", fill="#183642")
    draw.polygon(
        [(340, 70), (425, 145), (340, 220), (265, 145)], fill="#e9b65e", outline="#294951", width=4
    )
    draw.line([(340, 70), (340, 220), (300, 270), (345, 310)], fill="#294951", width=3)
    draw.line([(265, 145), (425, 145)], fill="#294951", width=3)
    reference.save(root / "owner-reference.png")
    origin, cdp = f"http://127.0.0.1:{port()}", port()
    config = json.loads((REPO / "config.example.json").read_text())
    config.update(
        data_directory=str(root / "machine"),
        origin=origin,
        frontend_directory=str(REPO / "apps/frontend/dist"),
        services=[],
        execution_profiles=[],
        provider_options={},
    )
    config["runtime"].update(
        cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
        skill_directories=[str(REPO / "agent/skills")],
        mcp_servers={},
    )
    (root / "config.json").write_text(json.dumps(config, indent=2))
    os.chmod(root / "config.json", 0o600)
    children = {}
    records = {}
    env = dict(os.environ, PATH=f"{REPO / '.venv/bin'}:{os.environ['PATH']}")
    stopping = False

    def request_stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        commands = {
            "server": [
                sys.executable,
                "-m",
                "proscenium",
                "--config",
                str(root / "config.json"),
                "--pair",
            ],
            "browser": [
                str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
                "--headless",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                f"--remote-debugging-port={cdp}",
                f"--user-data-dir={root / 'browser-profile'}",
                "about:blank",
            ],
        }
        for name, command in commands.items():
            with (root / f"{name}.private.log").open("wb") as log:
                child = subprocess.Popen(
                    command,
                    cwd=REPO,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            children[name] = child
            records[name] = {**identity(child.pid), "argv": command}
        records.update(
            root=str(root),
            origin=origin,
            cdp=cdp,
            supervisor=identity(os.getpid()),
            boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        )
        (root / "processes.json").write_text(json.dumps(records, indent=2))
        print(json.dumps({"root": str(root), "origin": origin, "cdp": cdp}), flush=True)
        deadline = time.monotonic() + 1800
        while not stopping and not (root / "STOP").exists() and time.monotonic() < deadline:
            if any(child.poll() is not None for child in children.values()):
                break
            time.sleep(0.2)
    finally:
        cleanup = {}
        runtime = root / "machine/copilot-runtime/process.json"
        native = json.loads(runtime.read_text()) if runtime.exists() else None
        for name, child in children.items():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
            try:
                code = child.wait(timeout=25)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                code = child.wait(timeout=10)
            cleanup[name] = {
                "pid": child.pid,
                "returncode": code,
                "reaped": child.poll() is not None,
            }
        if native:
            current = identity(native["pid"])
            cleanup["native"] = {
                "pid": native["pid"],
                "original_start": native["start"],
                "live_original": bool(
                    current and current["start"] == str(native["start"]) and current["state"] != "Z"
                ),
            }
        cleanup["independent_services_touched"] = False
        (root / "cleanup.json").write_text(json.dumps(cleanup, indent=2))


if __name__ == "__main__":
    main()
