"""One fresh bounded live proof. Never retries a prompt or starts a native renderer."""

import hashlib
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected():
    result = {
        str(p.relative_to(REPO)): sha(p)
        for p in (REPO / "docs/evidence/m02").rglob("*")
        if p.is_file()
    }
    for label in ("m01-20261114T132348Z", "m02-20261114T135654Z"):
        root = Path.home() / ".cache/proscenium-development/milestones" / label
        for directory in root.glob("project*"):
            if not (directory / ".git").exists():
                continue
            result[f"{label}/{directory.name}/HEAD"] = (
                subprocess.check_output(
                    ["git", "rev-parse", "--verify", "HEAD"], cwd=directory, text=True
                ).strip()
                if (directory / "scenes").exists() and list((directory / "scenes").rglob("*.blend"))
                else "unborn"
            )
            paths = [directory / ".git/index", directory / ".proscenium/state.sqlite3"]
            paths += list((directory / ".proscenium/artefacts").glob("sha256/*/content"))
            paths += list((directory / "scenes").rglob("*.blend"))
            for path in paths:
                if path.is_file():
                    result[str(path.relative_to(root.parent))] = sha(path)
        for path in (root / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"):
            result[str(path.relative_to(root.parent))] = sha(path)
    return result


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(root, label, argv, environment):
    with (root / f"{label}.private.log").open("wb") as log:
        process = subprocess.Popen(
            argv,
            cwd=REPO,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return process, {
        "pid": process.pid,
        "start": Path(f"/proc/{process.pid}/stat").read_text().split(") ")[1].split()[19],
        "argv": argv,
        "started_monotonic_ns": time.monotonic_ns(),
    }


def finish(process, record):
    if process.poll() is None:
        assert (
            Path(f"/proc/{process.pid}/stat").read_text().split(") ")[1].split()[19]
            == record["start"]
        )
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
            record["forced_after_timeout"] = True
    record.update(
        exit_code=process.returncode,
        elapsed_seconds=(time.monotonic_ns() - record["started_monotonic_ns"]) / 1e9,
    )
    assert record["elapsed_seconds"] <= 300


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Reuse only proven phase boundaries in the one allocated root. Never
        # resend completed A, retry an uncertain control, or exceed two roots.
        root = Path(sys.argv[1])
        info = json.loads((root / "processes.json").read_text())
        assert not Path(f"/proc/{info['server']['pid']}").exists()
        clouds = list(root.glob("cloud-*"))
        if clouds:
            assert [p.name for p in clouds] == ["cloud-A.claim"]
            assert "browser" in info and not Path(f"/proc/{info['browser']['pid']}").exists()
            for label, count in (("a", 1), ("b", 0)):
                with sqlite3.connect(
                    f"file:{root}/project-{label}/.proscenium/state.sqlite3?mode=ro", uri=True
                ) as connection:
                    requests = [
                        json.loads(row[0])
                        for row in connection.execute(
                            "SELECT body FROM records WHERE namespace='requests'"
                        )
                    ]
                    assert len(requests) == count
                    assert all(r["receipt"]["state"] == "completed" for r in requests)
            native = json.loads((root / "machine/copilot-runtime/process.json").read_text())
            assert not Path(f"/proc/{native['pid']}").exists()
            if len(sys.argv) > 2 and sys.argv[2] == "--r2-only":
                assert (
                    not (root / "planned.request").exists()
                    and not (root / "planned.claim").exists()
                )
                (root / "r1-reattach-timeout.json").write_text(json.dumps(info, indent=2))
                for source, target in [
                    ("server.private.log", "r1-reattach-server.private.log"),
                    ("browser-driver.private.log", "r1-reattach-driver.private.log"),
                    ("browser-result.json", "r1-reattach-result.json"),
                    ("failure.png", "r1-reattach-failure.png"),
                    ("pairing.url", "used-reattach-pairing.private.url"),
                ]:
                    (root / source).rename(root / target)
                info["r2_only"] = True
            else:
                assert not (root / "interrupted-before-b-send.json").exists(), (
                    "Never repeat an uncertain reattach/control operation"
                )
                (root / "interrupted-before-b-send.json").write_text(json.dumps(info, indent=2))
                for source, target in [
                    ("server.private.log", "interrupted-server.private.log"),
                    ("browser-driver.private.log", "interrupted-driver.private.log"),
                    ("browser-result.json", "interrupted-result.json"),
                    ("failure.png", "interrupted-failure.png"),
                    ("pairing.url", "used-interrupted-pairing.private.url"),
                ]:
                    (root / source).rename(root / target)
            info.pop("browser")
            info.pop("driver_exit", None)
            info["reattach_settled_a_without_resending"] = True
        elif "browser" not in info:
            assert info["server"]["exit_code"] == 1 and not (root / "machine").exists()
            assert not (root / "pairing.url").exists() and not list(root.glob("project-*"))
            (root / "bootstrap-failure.json").write_text(json.dumps(info, indent=2))
            (root / "server.private.log").rename(root / "bootstrap-failure.private.log")
        else:
            # A detached setup-popover failure occurred before the first Send.
            # Prove zero requests/native associations before reusing this root.
            assert not Path(f"/proc/{info['browser']['pid']}").exists()
            assert not (root / "machine/copilot-runtime").exists()
            for database in root.glob("project-*/.proscenium/state.sqlite3"):
                with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
                    assert (
                        connection.execute(
                            "SELECT COUNT(*) FROM records WHERE namespace IN ('requests','copilot.session')"
                        ).fetchone()[0]
                        == 0
                    )
            (root / "service-setup-failure.json").write_text(json.dumps(info, indent=2))
            for source, target in [
                ("server.private.log", "service-setup-failure.private.log"),
                ("browser-driver.private.log", "setup-failure.private.log"),
                ("browser-result.json", "setup-failure.json"),
                ("failure.png", "setup-failure.png"),
                ("pairing.url", "used-setup-pairing.private.url"),
            ]:
                (root / source).rename(root / target)
            info.pop("browser")
            info.pop("driver_exit", None)
        before = json.loads((root / "protected-before.json").read_text())
        assert before == protected()
        temporary, origin, cdp = Path(info["temporary"]), info["origin"], info["cdp"]
    else:
        label = datetime.now(UTC).strftime("m02-followup-%Y%m%dT%H%M%SZ")
        root = Path.home() / ".cache/proscenium-development/milestones" / label
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        temporary = Path.home() / ".cache" / ("m2f-" + label[-9:-1])
        temporary.mkdir(mode=0o700, exist_ok=False)
        before = protected()
        (root / "protected-before.json").write_text(json.dumps(before, indent=2))
        config = json.loads((REPO / "config.example.json").read_text())
        origin, cdp = f"http://127.0.0.1:{free_port()}", free_port()
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
        config["rendering"].update(claim_path=str(root / "never-render.lock"))
        (root / "config.json").write_text(json.dumps(config, indent=2))
        os.chmod(root / "config.json", 0o600)
        info = {
            "root": str(root),
            "origin": origin,
            "cdp": cdp,
            "temporary": str(temporary),
            "test_base": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        }
    environment = {
        **os.environ,
        "TMPDIR": str(temporary),
        "XDG_RUNTIME_DIR": str(temporary),
        "PYTHONPATH": str(REPO),
    }
    server, info["server"] = start(
        root,
        "server",
        [
            str(REPO / ".venv/bin/python"),
            str(REPO / "tests/native/studio/m02_followup_host.py"),
            str(root),
        ],
        environment,
    )
    browser = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if httpx.get(origin + "/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Owned server not ready; inspect private log")
        browser, info["browser"] = start(
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
            environment,
        )
        (root / "processes.json").write_text(json.dumps(info, indent=2))
        time.sleep(0.5)
        with (root / "browser-driver.private.log").open("wb") as log:
            result = subprocess.run(
                ["node", str(REPO / "tests/native/studio/m02_followup_browser.mjs"), str(root)],
                cwd=REPO,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=265,
            )
        info["driver_exit"] = result.returncode
    finally:
        finish(server, info["server"])
        if browser:
            finish(browser, info["browser"])
        after = protected()
        (root / "protected-after.json").write_text(json.dumps(after, indent=2))
        info["prior_evidence_and_private_sources_unchanged"] = before == after
        info["independent_blender_preserved"] = Path("/proc/2726862").exists()
        (root / "processes.json").write_text(json.dumps(info, indent=2))
    print(
        json.dumps(
            {
                "root": str(root),
                "driver_exit": info.get("driver_exit"),
                "protected_unchanged": before == after,
            },
            indent=2,
        )
    )
