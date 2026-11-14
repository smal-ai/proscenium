"""One ORIGINAL-work continuation allocation. No replacement Queue or private repair."""

import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx
from m10_continue_snapshot import ORIGINAL, REPO, ROOT, records, sha, write


def ticks(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    assert (ROOT / "before.json").exists(), "Take the private before-snapshot first"
    if (ROOT / "host.json").exists():
        raise ValueError("An original attempt exists. Never repeat the allocation implicitly")
    available = next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    if available < 5 * 1024**3:
        raise RuntimeError("Not enough host headroom for the one original-continuation attempt")
    temporary = Path("/tmp/opencode/m10c")
    temporary.mkdir(parents=True, exist_ok=True)
    config = json.loads((ORIGINAL / "config.json").read_text())
    config.update(
        origin=f"http://127.0.0.1:{free_port()}",
        frontend_directory=str(REPO / "apps/frontend/dist"),
    )
    config["runtime"].update(
        cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
        skill_directories=[str(REPO / "agent/skills")],
    )
    assert Path(config["data_directory"]) == ORIGINAL / "machine"
    assert config["services"] == [] and config["runtime"]["mcp_servers"] == {}
    write(ROOT / "config.private.json", config)
    (ROOT / "config.private.json").chmod(0o600)
    environment = {
        **os.environ,
        "TMPDIR": str(temporary),
        "OMP_NUM_THREADS": "4",
        "MKL_NUM_THREADS": "4",
    }
    info = {
        "origin": config["origin"],
        "original_fixture": str(ORIGINAL),
        "attempt_root": str(ROOT),
        "base": subprocess.check_output(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
        ).strip(),
        "mem_available_before_host": available,
        "protected_blender_3834895_start_before": ticks(3834895),
        "sdk_continuation_sends_allowed": 1,
    }
    server = node = None
    try:
        with (ROOT / "server.private.log").open("wb") as log:
            argv = [
                str(REPO / ".venv/bin/python"),
                "-m",
                "proscenium",
                "--config",
                str(ROOT / "config.private.json"),
                "--pair",
            ]
            server = subprocess.Popen(
                argv,
                cwd=REPO,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        info["server"] = {"pid": server.pid, "start": ticks(server.pid), "argv": argv}
        write(ROOT / "host.json", info)
        with httpx.Client(timeout=2, trust_env=False) as read_client:
            for _ in range(40):
                if server.poll() is not None:
                    raise RuntimeError("The one owned host exited before readiness")
                try:
                    if read_client.get(config["origin"] + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError("The one owned host did not become ready")
        with (ROOT / "browser-driver.private.log").open("wb") as log:
            argv = ["node", str(Path(__file__).with_name("m10_continue_browser.mjs")), str(ROOT)]
            node = subprocess.Popen(
                argv,
                cwd=REPO,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        info["browser_driver"] = {"pid": node.pid, "start": ticks(node.pid), "argv": argv}
        write(ROOT / "host.json", info)
        info["browser_driver_exit"] = node.wait(timeout=780)
    except Exception as error:
        info["blocker"] = f"{type(error).__name__}: {error}"
    finally:
        if node and node.poll() is None and ticks(node.pid) == info["browser_driver"]["start"]:
            os.killpg(node.pid, signal.SIGTERM)
            node.wait(timeout=15)
        if server and server.poll() is None and ticks(server.pid) == info["server"]["start"]:
            os.killpg(server.pid, signal.SIGTERM)
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                info["server_close_timeout"] = True
                if ticks(server.pid) == info["server"]["start"]:
                    os.killpg(server.pid, signal.SIGKILL)
                server.wait(timeout=10)
        info.update(
            server_exit=server.returncode if server else None,
            browser_driver_exit=node.returncode if node else None,
            protected_blender_3834895_start_after=ticks(3834895),
        )
        info["original_control_after"] = records("copilot.control")
        info["original_requests_after"] = records("requests")
        info["original_work_after"] = records("work")
        info["permission_stages_after"] = records("copilot.permissions")
        info["invocations_after"] = records("invocations")
        info["continuation_sends_after"] = records("copilot.continuation_sends")
        info["script_sha256_after"] = sha(ORIGINAL / "project/assets/e04/revise_asset.py")
        write(ROOT / "host.json", info)
        print(
            json.dumps(
                {
                    "attempt": str(ROOT),
                    "server_exit": info["server_exit"],
                    "browser_driver_exit": info["browser_driver_exit"],
                    "blocker": info.get("blocker"),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
