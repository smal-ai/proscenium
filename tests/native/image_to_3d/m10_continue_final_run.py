"""One original SDK continuation on accepted recovery, with owned lifecycle only."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import httpx
from m10_continue_final_snapshot import FINAL
from m10_continue_run import free_port, ticks
from m10_continue_snapshot import ORIGINAL, REPO, records, write


def main():
    assert (FINAL / "before.json").exists()
    if (FINAL / "host.json").exists():
        raise ValueError("An original final attempt already exists")
    available = next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    if available < 5 * 1024**3:
        raise RuntimeError("Not enough host headroom for the authorised CPU proof")
    temporary = Path("/tmp/opencode/m10f")
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
    write(FINAL / "config.private.json", config)
    (FINAL / "config.private.json").chmod(0o600)
    info = {
        "origin": config["origin"],
        "original_fixture": str(ORIGINAL),
        "attempt_root": str(FINAL),
        "base": subprocess.check_output(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
        ).strip(),
        "mem_available_before_host": available,
        "protected_blender_3834895_start_before": ticks(3834895),
    }
    environment = {
        **os.environ,
        "TMPDIR": str(temporary),
        "OMP_NUM_THREADS": "4",
        "MKL_NUM_THREADS": "4",
    }
    server = node = None
    try:
        with (FINAL / "server.private.log").open("wb") as log:
            argv = [
                str(REPO / ".venv/bin/python"),
                "-m",
                "proscenium",
                "--config",
                str(FINAL / "config.private.json"),
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
        write(FINAL / "host.json", info)
        with httpx.Client(timeout=2, trust_env=False) as client:
            for _ in range(40):
                if server.poll() is not None:
                    raise RuntimeError("One owned host exited before readiness")
                try:
                    if client.get(config["origin"] + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError("One owned host readiness failed")
        with (FINAL / "browser-driver.private.log").open("wb") as log:
            argv = [
                "node",
                str(Path(__file__).with_name("m10_continue_final_browser.mjs")),
                str(FINAL),
            ]
            node = subprocess.Popen(
                argv,
                cwd=REPO,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        info["driver"] = {"pid": node.pid, "start": ticks(node.pid), "argv": argv}
        write(FINAL / "host.json", info)
        info["driver_exit"] = node.wait(timeout=780)
    except Exception as error:
        info["blocker"] = f"{type(error).__name__}: {error}"
    finally:
        if node and node.poll() is None and ticks(node.pid) == info["driver"]["start"]:
            os.killpg(node.pid, signal.SIGTERM)
            node.wait(timeout=15)
        if server and server.poll() is None and ticks(server.pid) == info["server"]["start"]:
            os.killpg(server.pid, signal.SIGTERM)
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                if ticks(server.pid) == info["server"]["start"]:
                    os.killpg(server.pid, signal.SIGKILL)
                server.wait(timeout=10)
        info.update(
            server_exit=server.returncode if server else None,
            driver_exit=node.returncode if node else None,
            protected_blender_3834895_start_after=ticks(3834895),
            original_request_after=records("requests"),
            original_work_after=records("work"),
            continuation_sends_after=records("copilot.continuation_sends"),
        )
        write(FINAL / "host.json", info)
        if not (FINAL / "final.done.json").exists():
            write(
                FINAL / "final.done.json",
                {"phase": "host_outcome_retained", "blocker": info.get("blocker")},
            )
        print(
            json.dumps(
                {
                    "attempt": str(FINAL),
                    "server_exit": info["server_exit"],
                    "driver_exit": info["driver_exit"],
                    "blocker": info.get("blocker"),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
