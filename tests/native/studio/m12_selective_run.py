"""Bounded supervisor/subreaper for one selective-response-loss browser run."""

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from m12_ack_run import own_children, state

REPO = Path(__file__).resolve().parents[3]


def main():
    root = Path(sys.argv[1])
    os.environ["TMPDIR"] = str(root / "t")
    assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
    result = {
        "status": "incomplete",
        "reaped_adopted_children": [],
        "signals": [],
        "supervisor": state(os.getpid()),
    }

    def reap():
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if not pid:
                return
            result["reaped_adopted_children"].append(
                {"pid": pid, "exit_code": os.waitstatus_to_exitcode(status)}
            )

    def expired(_sig, _frame):
        raise TimeoutError("Bounded selective proof")

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(265)
    node = None
    try:
        node = subprocess.Popen(
            ["node", str(REPO / "tests/native/studio/m12_selective_browser.mjs"), str(root)],
            cwd=REPO,
            env=os.environ.copy(),
            start_new_session=True,
        )
        result["controller"] = state(node.pid)
        result["node_exit_code"] = node.wait(timeout=250)
    except (TimeoutError, subprocess.TimeoutExpired):
        result["limit"] = "allocation deadline"
    finally:
        if node is not None and node.poll() is None:
            original = result["controller"]
            current = state(node.pid)
            if current and current["start"] == original["start"]:
                node.terminate()
                try:
                    node.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if (current := state(node.pid)) and current["start"] == original["start"]:
                        node.kill()
                        node.wait(timeout=3)
        for sig in (None, signal.SIGTERM, signal.SIGKILL):
            if sig is not None:
                for child in own_children():
                    current = state(child["pid"])
                    if current and current["start"] == child["start"] and current["state"] != "Z":
                        os.kill(child["pid"], sig)
                        result["signals"].append({**child, "signal": sig.name})
            deadline = time.monotonic() + 3
            while own_children() and time.monotonic() < deadline:
                reap()
                time.sleep(0.03)
        reap()
        result["remaining_children"] = own_children()
        if (root / "browser-result-private.json").exists():
            proof = json.loads((root / "browser-result-private.json").read_text())
            result["status"] = proof["status"]
            result["recorded_process_cleanup"] = [
                {
                    **p,
                    "original_kernel_identity_gone": not (current := state(p["pid"]))
                    or current["start"] != p["start"],
                }
                for p in proof["processes"]
            ]
        with (root / "supervisor-private.json").open("x") as out:
            json.dump(result, out, indent=2)
        (root / "supervisor-private.json").chmod(0o600)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "remaining_children": len(result["remaining_children"]),
                }
            )
        )
    if result["status"] != "selective_accepted_ack_recovery_pass" or result["remaining_children"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
