"""Start the bounded real M01 host and isolated browser. Never prints pairing secrets."""

import json
import os
import socket
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from mcp_configuration import configuration

REPO = Path(__file__).resolve().parents[3]
ROOT = (
    Path.home()
    / ".cache/proscenium-development/milestones"
    / datetime.now(UTC).strftime("m01-%Y%m%dT%H%M%SZ")
)
ROOT.mkdir(mode=0o700, parents=True, exist_ok=False)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


port, cdp = free_port(), free_port()
config = json.loads((REPO / "config.example.json").read_text())
config.update(
    data_directory=str(ROOT / "machine"),
    origin=f"http://127.0.0.1:{port}",
    frontend_directory=str(REPO / "apps/frontend/dist"),
)
config["runtime"].update(
    cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
    skill_directories=[str(REPO / "agent/skills")],
    mcp_servers=configuration("/home/quintin/.local/bin/uvx"),
)
config["rendering"].update(
    claim_path=str(ROOT / "render.lock"), max_frames=1, max_pixels=230400, max_samples=8
)
(ROOT / "config.json").write_text(json.dumps(config, indent=2))
os.chmod(ROOT / "config.json", 0o600)
records = {}
for name, argv in {
    "server": [
        str(REPO / ".venv/bin/python"),
        "-m",
        "proscenium",
        "--config",
        str(ROOT / "config.json"),
        "--pair",
    ],
    "browser": [
        str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
        "--headless",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        f"--remote-debugging-port={cdp}",
        f"--user-data-dir={ROOT / 'browser-profile'}",
        "about:blank",
    ],
}.items():
    with (ROOT / f"{name}.private.log").open("wb") as log:
        child = subprocess.Popen(
            argv,
            cwd=REPO,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    records[name] = {
        "pid": child.pid,
        "start": Path(f"/proc/{child.pid}/stat").read_text().split(") ")[1].split()[19],
        "argv": argv,
    }
records.update(
    root=str(ROOT),
    origin=config["origin"],
    cdp=cdp,
    boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
)
(ROOT / "processes.json").write_text(json.dumps(records, indent=2))
time.sleep(2)
print(
    json.dumps(
        {
            "root": str(ROOT),
            "origin": config["origin"],
            "cdp": cdp,
            "server_pid": records["server"]["pid"],
            "browser_pid": records["browser"]["pid"],
        }
    )
)
