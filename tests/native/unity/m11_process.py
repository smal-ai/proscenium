"""Own bounded M11 application/browser processes. Never touch another native session."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
INSTALLED = Path("/home/quintin/play/proscenium")


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def identity(pid):
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return dict(pid=pid, start=stat[19])


def main():
    root = Path(sys.argv[1])
    resuming = len(sys.argv) > 2 and sys.argv[2] == "resume-host"
    root.mkdir(mode=0o700, parents=True, exist_ok=resuming)
    for folder in ("commands", "receipts"):
        (root / folder).mkdir(exist_ok=resuming)
    (root / "DISPOSABLE-NATIVE-FIXTURE").write_text(
        "M11 actual product journey. Operator setup and tests are explicitly labelled.\n"
    )
    import shutil

    if not resuming:
        shutil.copytree(INSTALLED / "apps/frontend/dist", root / "compiled-frontend")
    origin, cdp = f"http://127.0.0.1:{port()}", port()
    config = json.loads((REPO / "config.example.json").read_text())
    if resuming:
        old = json.loads((root / "processes.json").read_text())
        (root / f"processes-before-{old['server']['pid']}.json").write_text(
            json.dumps(old, indent=2)
        )
        if (root / "cleanup.json").is_file():
            shutil.copyfile(
                root / "cleanup.json", root / f"cleanup-before-{old['server']['pid']}.json"
            )
        origin, cdp = old["origin"], old["cdp"]
    config.update(
        data_directory=str(root / "machine"),
        origin=origin,
        frontend_directory=str(root / "compiled-frontend"),
        services=[],
        provider_options={},
        execution_profiles=["unity-local"],
    )
    config["runtime"].update(
        cli_path=str(INSTALLED / "node_modules/@github/copilot-linux-x64/copilot"),
        lead_model="gpt-6.1-sol",
        worker_model="gpt-6.1-sol",
        unity_worker_model="gpt-6.1-sol",
        unity_editor="/home/quintin/Unity/Hub/Editor/6000.3.27f1/Editor/Unity",
        skill_directories=[str(REPO / "agent/skills")],
        mcp_servers={},
    )
    config["rendering"].update(
        blender=shutil.which("blender"), claim_path=str(root / "owned-blender.lock")
    )
    (root / "config.json").write_text(json.dumps(config, indent=2))
    (root / "config.json").chmod(0o600)
    env = {
        **os.environ,
        "PYTHONPATH": f"{REPO / 'apps/backend/src'}:{REPO}",
        "PATH": f"{INSTALLED / '.venv/bin'}:{os.environ['PATH']}",
    }
    children = {}
    commands = dict(
        server=[sys.executable, str(REPO / "tests/native/unity/m11_host.py"), str(root)],
        browser=[
            str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
            "--headless",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            "--window-size=1920,1080",
            f"--remote-debugging-port={cdp}",
            f"--user-data-dir={root / 'browser-profile'}",
            "about:blank",
        ],
    )
    info = dict(
        root=str(root),
        origin=origin,
        cdp=cdp,
        dependencies=str(INSTALLED / "node_modules"),
        supervisor=identity(os.getpid()),
        boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    )
    stopping = False

    def interrupt(*args):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    try:
        for name, argv in commands.items():
            with (root / (name + ".private.log")).open("w") as log:
                child = subprocess.Popen(
                    argv,
                    cwd=REPO,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            children[name] = child
            info[name] = {**identity(child.pid), "argv": argv}
        (root / "processes.json").write_text(json.dumps(info, indent=2))
        print(json.dumps({"root": str(root), "origin": origin, "cdp": cdp}), flush=True)
        deadline = time.monotonic() + 3600
        while not stopping and not (root / "STOP").exists() and time.monotonic() < deadline:
            if any(child.poll() is not None for child in children.values()):
                break
            time.sleep(0.2)
    finally:
        cleanup = {}
        for name, child in children.items():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
            try:
                code = child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                code = child.wait(timeout=10)
            cleanup[name] = dict(pid=child.pid, returncode=code, reaped=child.poll() is not None)
        (root / "cleanup.json").write_text(json.dumps(cleanup, indent=2))


if __name__ == "__main__":
    main()
