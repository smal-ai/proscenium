"""One owned production/browser stack, bounded to 600 seconds including cleanup."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
from m10_complete_snapshot import ORIGINAL, REPO, ROOT
from m10_continue_run import free_port, ticks
from m10_continue_snapshot import records, write
from m10_drive import sample_processes


def main(repair=False):
    assert (ROOT / "before.json").exists()
    prior = None
    if repair:
        prior = json.loads((ROOT / "host.json").read_text())
        assert prior["server_exit"] == 1 and prior["browser_exit"] is None
        assert not (ROOT / "sdk-send-claimed.json").exists()
        assert "ModuleNotFoundError: No module named" in (ROOT / "server.private.log").read_text()
        assert ticks(prior["server"]["pid"]) != prior["server"]["start"]
        with (ROOT / "presend-import-repair.json").open("x") as output:
            json.dump(
                {
                    "scope": "Add repository root to child PYTHONPATH, no SDK/session/request/native effect in failed startup",
                    "global_deadline_epoch_ms": prior["deadline_epoch_ms"],
                },
                output,
            )
        for old, new in (
            ("host.json", "host-import-failed.json"),
            ("server.private.log", "server-import-failed.log"),
            ("stack.done.json", "stack-import-failed.done.json"),
        ):
            (ROOT / old).rename(ROOT / new)
    if (ROOT / "host.json").exists():
        raise ValueError("Existing bounded stack must be reconciled, never restarted implicitly")
    start = time.monotonic()
    remaining = (prior["deadline_epoch_ms"] / 1000 - time.time()) if prior else 570
    assert remaining > 300, "Insufficient original global stack budget for the follow-up"
    deadline = start + remaining
    available = next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    assert available >= 5 * 1024**3
    temporary = Path("/tmp/opencode/m10x")
    temporary.mkdir(exist_ok=True)
    config = json.loads((ORIGINAL / "config.json").read_text())
    config.update(
        origin=prior["origin"] if prior else f"http://127.0.0.1:{free_port()}",
        frontend_directory=str(REPO / "apps/frontend/dist"),
    )
    config["runtime"].update(
        cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
        skill_directories=[str(REPO / "agent/skills")],
    )
    assert config["services"] == [] and config["runtime"]["mcp_servers"] == {}
    write(ROOT / "config.private.json", config)
    (ROOT / "config.private.json").chmod(0o600)
    info = {
        "origin": config["origin"],
        "original_fixture": str(ORIGINAL),
        "attempt_root": str(ROOT),
        "cdp": free_port(),
        "base": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "deadline_epoch_ms": prior["deadline_epoch_ms"]
        if prior
        else int((time.time() + 570) * 1000),
        "mem_available_before_host": available,
        "root_bound_seconds": 240,
        "stack_bound_seconds": 600,
        "owned_processes": {},
        "headroom_samples": [],
    }
    environment = {
        **os.environ,
        "TMPDIR": str(temporary),
        "OMP_NUM_THREADS": "4",
        "MKL_NUM_THREADS": "4",
        "PYTHONPATH": str(REPO),
    }
    server = browser = None
    try:
        with (ROOT / "server.private.log").open("xb") as log:
            argv = [
                str(REPO / ".venv/bin/python"),
                "-u",
                str(Path(__file__).with_name("m10_complete_host.py")),
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
        with httpx.Client(timeout=2, trust_env=False) as client:
            for _ in range(40):
                assert server.poll() is None, "Owned host failed before readiness"
                try:
                    if client.get(config["origin"] + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("Owned host readiness failed")
        with (ROOT / "browser-driver.private.log").open("xb") as log:
            argv = ["node", str(Path(__file__).with_name("m10_complete_browser.mjs")), str(ROOT)]
            browser = subprocess.Popen(
                argv,
                cwd=REPO,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        info["browser"] = {"pid": browser.pid, "start": ticks(browser.pid), "argv": argv}
        write(ROOT / "host.json", info)
        while browser.poll() is None and time.monotonic() < deadline:
            sample_processes(info, info["owned_processes"])
            info["headroom_samples"].append(
                {
                    "monotonic_ns": time.monotonic_ns(),
                    "available_bytes": next(
                        int(line.split()[1]) * 1024
                        for line in Path("/proc/meminfo").read_text().splitlines()
                        if line.startswith("MemAvailable:")
                    ),
                }
            )
            time.sleep(0.25)
        if browser.poll() is None:
            info["blocker"] = "Bounded browser stack reached cleanup deadline"
        if (ROOT / "result.private.json").exists():
            result = json.loads((ROOT / "result.private.json").read_text())
            if result.get("phase") == "v2_visible" and time.monotonic() < deadline - 195:
                # The only optional independent readback is read-only and has no model send.
                info["readback_driver_exit"] = subprocess.run(
                    [
                        str(REPO / ".venv/bin/python"),
                        str(Path(__file__).with_name("m10_complete_readback.py")),
                    ],
                    cwd=REPO,
                    env=environment,
                    timeout=190,
                ).returncode
    except Exception as error:
        info["blocker"] = f"{type(error).__name__}: {error}"
    finally:
        for name, child in (("browser", browser), ("server", server)):
            if child and child.poll() is None and ticks(child.pid) == info[name]["start"]:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if ticks(child.pid) == info[name]["start"]:
                        os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
        info.update(
            server_exit=server.returncode if server else None,
            browser_exit=browser.returncode if browser else None,
            stack_seconds=570 - (deadline - time.monotonic()),
            original_request_after=records("requests"),
            original_work_after=records("work"),
        )
        write(ROOT / "host.json", info)
        write(
            ROOT / "stack.done.json",
            {"stack_seconds": info["stack_seconds"], "blocker": info.get("blocker")},
        )
        print(
            json.dumps(
                {
                    "root": str(ROOT),
                    "server_exit": info["server_exit"],
                    "browser_exit": info["browser_exit"],
                    "stack_seconds": info["stack_seconds"],
                    "blocker": info.get("blocker"),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main(repair="--repair-presend-import" in sys.argv)
