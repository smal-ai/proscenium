"""Reopen only the retained M05 store/project with the accepted production host."""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.services import Services

REPO = Path(__file__).resolve().parents[3]


def main():
    proof = Path(sys.argv[1]).resolve()
    assert (proof / "before.json").exists()
    with (proof / "host.claim").open("x") as claim:
        claim.write(
            "One continuous owned server/browser launch, not permission to replay a send.\n"
        )
    short = Path.home() / ".cache/p5t-1840"
    short.mkdir(mode=0o700, exist_ok=True)
    os.environ.update(PATH=f"{REPO / '.venv/bin'}:{os.environ['PATH']}", TMPDIR=str(short))
    tempfile.tempdir = str(short)
    config = json.loads((proof / "original-config.json").read_text())
    config["frontend_directory"] = str(REPO / "apps/frontend/dist")
    config["runtime"].update(
        cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
        skill_directories=[str(REPO / "agent/skills")],
        mcp_servers={},
    )
    config.update(services=[], provider_options={})
    (proof / "config.json").write_text(json.dumps(config, indent=2))
    services = Services.create(Settings.load(proof / "config.json"))
    app = create_app(services.settings, services=services, manage_features=True)
    pairing = proof / "pairing.url"
    pairing.write_text(f"{config['origin']}/#pair={services.auth.issue_pairing()}")
    os.chmod(pairing, 0o600)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        cdp = sock.getsockname()[1]
    argv = [
        str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
        "--headless",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--use-gl=angle",
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
        f"--remote-debugging-port={cdp}",
        f"--user-data-dir={proof / 'browser-profile'}",
        "about:blank",
    ]
    with (proof / "browser.private.log").open("wb") as log:
        browser = subprocess.Popen(
            argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
    (proof / "processes.json").write_text(
        json.dumps(
            {
                "server_pid": os.getpid(),
                "browser_pid": browser.pid,
                "cdp": cdp,
                "origin": config["origin"],
                "tmpdir": str(short),
            },
            indent=2,
        )
    )
    url = urlsplit(config["origin"])
    server = uvicorn.Server(uvicorn.Config(app, host=url.hostname, port=url.port, workers=1))
    stopped = threading.Event()

    def watch():
        deadline = time.monotonic() + 1800
        while not stopped.wait(0.2):
            if (proof / "STOP").exists() or time.monotonic() > deadline:
                server.should_exit = True
                return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        server.run()
    finally:
        stopped.set()
        watcher.join()
        services.close()
        if browser.poll() is None:
            os.killpg(browser.pid, signal.SIGTERM)
        browser.wait(timeout=20)
        native = json.loads(
            (Path(config["data_directory"]) / "copilot-runtime/process.json").read_text()
        )
        (proof / "cleanup.json").write_text(
            json.dumps(
                {
                    "browser_pid": browser.pid,
                    "browser_reaped": True,
                    "native_pid": native["pid"],
                    "native_proc_absent": not Path(f"/proc/{native['pid']}").exists(),
                    "independent_services_touched": False,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
