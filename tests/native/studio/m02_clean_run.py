"""One clean allocation: real CLI, one browser, two original readonly UI Sends."""

import hashlib
import json
import os
import signal
import socket
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[3]
CACHE = Path.home() / ".cache/proscenium-development"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected():
    paths = []
    for worktree in ("m01-proof", "m02-history", "m02-followup"):
        evidence = CACHE / "fleet" / worktree / "docs/evidence"
        paths += [p for d in evidence.glob("m0[12]*") for p in d.rglob("*") if p.is_file()]
    for root in (CACHE / "milestones").glob("m0[12]*"):
        paths += list(root.glob("project*/.proscenium/state.sqlite3"))
        paths += list(root.glob("project*/.git/HEAD")) + list(root.glob("project*/.git/index"))
        paths += list(root.glob("project*/.proscenium/artefacts/sha256/*/content"))
        paths += list(root.glob("machine/copilot-runtime/home/session-state/*/events.jsonl"))
    return {str(p): sha(p) for p in sorted(set(paths))}


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(root, label, argv, env):
    with (root / f"{label}.private.log").open("wb") as log:
        process = subprocess.Popen(
            argv,
            cwd=REPO,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return process, {
        "pid": process.pid,
        "start": Path(f"/proc/{process.pid}/stat").read_text().split(") ")[1].split()[19],
        "argv": argv,
    }


def stop(process, record):
    if process.poll() is None:
        assert (
            Path(f"/proc/{process.pid}/stat").read_text().split(") ")[1].split()[19]
            == record["start"]
        )
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
            record["forced"] = True
    record["exit_code"] = process.returncode


if __name__ == "__main__":
    # A second invocation cannot silently consume another two cloud requests.
    marker = REPO / ".venv/m02_clean_allocation.json"
    with marker.open("x") as stream:
        json.dump({"allocation": "one final clean R1 proof", "base": "e3cddf8"}, stream)
    root = Path.home() / ".cache" / datetime.now(UTC).strftime("m2c-%Y%m%dT%H%M%SZ")
    root.mkdir(mode=0o700, exist_ok=False)
    (root / "t").mkdir(mode=0o700)
    before = protected()
    (root / "protected-before.json").write_text(json.dumps(before, indent=2))
    config = json.loads((REPO / "config.example.json").read_text())
    origin, cdp = f"http://127.0.0.1:{port()}", port()
    config.update(
        data_directory=str(root / "m"),
        origin=origin,
        frontend_directory=str(REPO / "apps/frontend/dist"),
    )
    config["runtime"].update(
        cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
        skill_directories=[str(REPO / "agent/skills")],
        mcp_servers={},
    )
    config["rendering"]["claim_path"] = str(root / "never-render.lock")
    (root / "config.json").write_text(json.dumps(config, indent=2))
    os.chmod(root / "config.json", 0o600)
    env = {**os.environ, "TMPDIR": str(root / "t"), "XDG_RUNTIME_DIR": str(root / "t")}
    info = {
        "root": str(root),
        "origin": origin,
        "cdp": cdp,
        "test_base": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }
    info["driver_sha256"] = {
        p.name: sha(p) for p in (REPO / "tests/native/studio").glob("m02_clean_*") if p.is_file()
    }
    began = time.monotonic()
    server, info["server"] = start(
        root,
        "server",
        [
            str(REPO / ".venv/bin/python"),
            "-m",
            "proscenium",
            "--config",
            str(root / "config.json"),
            "--pair",
        ],
        env,
    )
    browser = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            assert server.poll() is None, "CLI exited. Inspect retained private log."
            try:
                if httpx.get(origin + "/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Server readiness timeout")
        browser, info["browser"] = start(
            root,
            "browser",
            [
                str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080",
                f"--remote-debugging-port={cdp}",
                f"--user-data-dir={root / 'c'}",
                "about:blank",
            ],
            env,
        )
        (root / "processes.json").write_text(json.dumps(info, indent=2))
        time.sleep(0.5)
        with (root / "driver.private.log").open("wb") as log:
            result = subprocess.run(
                ["node", str(REPO / "tests/native/studio/m02_clean_browser.mjs"), str(root)],
                cwd=REPO,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=265,
            )
        info["driver_exit"] = result.returncode
    finally:
        stop(server, info["server"])
        if browser:
            stop(browser, info["browser"])
        after = protected()
        (root / "protected-after.json").write_text(json.dumps(after, indent=2))
        info.update(
            prior_evidence_unchanged=before == after,
            elapsed_seconds=time.monotonic() - began,
            independent_blender_preserved=Path("/proc/2726862").exists(),
        )
        (root / "processes.json").write_text(json.dumps(info, indent=2))
    print(
        json.dumps(
            {
                "root": str(root),
                "driver_exit": info.get("driver_exit"),
                "elapsed_seconds": info["elapsed_seconds"],
                "prior_evidence_unchanged": info["prior_evidence_unchanged"],
            },
            indent=2,
        )
    )
