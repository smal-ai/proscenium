"""Own isolated M05 production app/browser and a labelled operator fixture mailbox."""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import m05_fixture
import uvicorn
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.services import Services

REPO = Path(__file__).resolve().parents[3]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    os.environ["PATH"] = f"{REPO / '.venv/bin'}:{os.environ['PATH']}"
    root = Path(sys.argv[1]).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ("commands", "receipts"):
        (root / name).mkdir()
    config = json.loads((REPO / "config.example.json").read_text())
    config.update(
        data_directory=str(root / "machine"),
        origin=f"http://127.0.0.1:{port()}",
        frontend_directory=str(REPO / "apps/frontend/dist"),
        services=[],
        provider_options={},
        execution_profiles=["blender-cpu"],
    )
    config["runtime"].update(
        cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
        skill_directories=[str(REPO / "agent/skills")],
        mcp_servers={},
    )
    config["rendering"].update(
        blender="/home/quintin/.local/opt/blender-4.5.14-linux-x64/blender",
        max_frames=1,
        max_pixels=230400,
        max_samples=8,
        timeout=300,
        claim_path=str(Path.home() / ".cache/proscenium/native-claims/blender-render.lock"),
    )
    (root / "config.json").write_text(json.dumps(config, indent=2))
    os.chmod(root / "config.json", 0o600)
    services = Services.create(Settings.load(root / "config.json"))
    app = create_app(services.settings, services=services, manage_features=True)
    pairing = root / "pairing.url"
    pairing.write_text(f"{config['origin']}/#pair={services.auth.issue_pairing()}")
    os.chmod(pairing, 0o600)
    cdp = port()
    argv = [
        str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
        "--headless",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--use-gl=angle",
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
        f"--remote-debugging-port={cdp}",
        f"--user-data-dir={root / 'browser-profile'}",
        "about:blank",
    ]
    with (root / "browser.private.log").open("wb") as log:
        browser = subprocess.Popen(
            argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
    (root / "processes.json").write_text(
        json.dumps(
            {
                "root": str(root),
                "origin": config["origin"],
                "cdp": cdp,
                "server_pid": os.getpid(),
                "browser_pid": browser.pid,
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            },
            indent=2,
        )
    )
    url = urlsplit(config["origin"])
    server = uvicorn.Server(uvicorn.Config(app, host=url.hostname, port=url.port, workers=1))
    stop = threading.Event()

    def mailbox():
        deadline = time.monotonic() + 2400
        while not stop.wait(0.1):
            if (root / "STOP").exists() or time.monotonic() >= deadline:
                server.should_exit = True
                return
            for path in sorted((root / "commands").glob("*.json")):
                claim = path.with_suffix(".claimed")
                receipt = root / "receipts" / path.name
                if receipt.exists() or claim.exists():
                    continue
                path.rename(claim)
                try:
                    result = {
                        "status": "completed",
                        "result": m05_fixture.apply(services, root, json.loads(claim.read_text())),
                    }
                except Exception as error:
                    result = {
                        "status": "failed",
                        "type": type(error).__name__,
                        "code": getattr(error, "code", None),
                        "message": str(error),
                    }
                receipt.write_text(json.dumps(result, indent=2))

    thread = threading.Thread(target=mailbox, name="m05-operator-mailbox", daemon=True)
    thread.start()
    try:
        server.run()
    finally:
        stop.set()
        thread.join()
        if browser.poll() is None:
            os.killpg(browser.pid, signal.SIGTERM)
        browser.wait(timeout=20)
        services.close()
        native_path = root / "machine/copilot-runtime/process.json"
        native = json.loads(native_path.read_text()) if native_path.exists() else None
        (root / "cleanup.json").write_text(
            json.dumps(
                {
                    "browser_pid": browser.pid,
                    "browser_reaped": True,
                    "native_pid": native["pid"] if native else None,
                    "native_proc_absent": not native or not Path(f"/proc/{native['pid']}").exists(),
                    "independent_services_touched": False,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
