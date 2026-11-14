"""Start only the M10 compiled host and isolated Full-HD SwiftShader browser."""

import argparse
import json
import os
import socket
import subprocess
from pathlib import Path

from m10_fixture import REPO, ROOT, write


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--browser-only",
        action="store_true",
        help="Recover a proven exited pre-interaction browser only",
    )
    args = parser.parse_args()
    if (ROOT / "processes.json").exists() and not args.browser_only:
        raise ValueError("An owned host already exists. Inspect it, do not duplicate it")
    fixture = json.loads((ROOT / "fixture.json").read_text())
    if fixture["job"]["state"] != "succeeded":
        raise ValueError("Original proxy must succeed before a cloud root or UI journey")
    config = json.loads((ROOT / "config.json").read_text())
    if not args.browser_only:
        config["origin"] = f"http://127.0.0.1:{free_port()}"
    assert config["services"] == [] and config["runtime"]["mcp_servers"] == {}
    write(ROOT / "config.json", config)
    cdp = free_port()
    records = {
        "root": str(ROOT),
        "origin": config["origin"],
        "cdp": cdp,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }
    if args.browser_only:
        records = json.loads((ROOT / "processes.json").read_text())
        if Path(f"/proc/{records['browser']['pid']}").exists():
            raise ValueError("Original browser is not proven exited")
        records.setdefault("previous_browsers", []).append(records["browser"])
        (ROOT / "browser.private.log").rename(ROOT / "browser-startup-failed.private.log")
        cdp = records["cdp"]
    commands = {
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
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            "--window-size=1920,1080",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={cdp}",
            f"--user-data-dir={ROOT / 'browser-profile'}",
            "about:blank",
        ],
    }
    for name, argv in commands.items():
        if args.browser_only and name != "browser":
            continue
        temporary = Path("/tmp/opencode/m10-a")
        temporary.mkdir(parents=True, exist_ok=True)
        with (ROOT / f"{name}.private.log").open("wb") as log:
            child = subprocess.Popen(
                argv,
                cwd=REPO,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env={
                    **os.environ,
                    "OMP_NUM_THREADS": "4",
                    "MKL_NUM_THREADS": "4",
                    "TMPDIR": str(temporary),
                },
            )
        records[name] = {
            "pid": child.pid,
            "start": Path(f"/proc/{child.pid}/stat").read_text().rsplit(")", 1)[1].split()[19],
            "argv": argv,
        }
        write(ROOT / "processes.json", records)
    print(json.dumps({"root": str(ROOT), "origin": config["origin"], "cdp": cdp}))


if __name__ == "__main__":
    main()
