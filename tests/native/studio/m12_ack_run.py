"""Own-process subreaper around the single browser/SDK fault allocation."""

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def state(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return {"pid": int(pid), "parent": int(fields[1]), "state": fields[0], "start": fields[19]}
    except (OSError, IndexError):
        return None


def own_children():
    rows = []
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            value = state(path.name)
            if value and value["parent"] == os.getpid():
                rows.append(value)
    return rows


def main():
    root = Path(sys.argv[1])
    os.environ["TMPDIR"] = str(root / "t")
    assert (
        ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
    )  # PR_SET_CHILD_SUBREAPER, this process only.
    reaped = []
    signals = []
    node = None

    def reap():
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if not pid:
                return
            reaped.append({"pid": pid, "exit_code": os.waitstatus_to_exitcode(status)})

    def expired(_sig, _frame):
        raise TimeoutError("Bounded ACK proof allocation")

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(265)
    result = {"status": "incomplete", "reaped_adopted_children": reaped, "signals": signals}
    try:
        node = subprocess.Popen(
            ["node", str(REPO / "tests/native/studio/m12_ack_browser.mjs"), str(root)],
            cwd=REPO,
            env=os.environ.copy(),
            start_new_session=True,
        )
        result["controller"] = state(node.pid)
        result["node_exit_code"] = node.wait(timeout=250)
    except (TimeoutError, subprocess.TimeoutExpired):
        result["limit"] = "allocation deadline"
    finally:
        # Kernel child relationship plus PID/start, never a daemon-name sweep.
        if node is not None and node.poll() is None:
            before = result["controller"]
            current = state(node.pid)
            if current and current["start"] == before["start"]:
                node.terminate()
                node.wait(timeout=5)
        deadline = time.monotonic() + 3
        while own_children() and time.monotonic() < deadline:
            reap()
            time.sleep(0.05)
        for value in own_children():
            current = state(value["pid"])
            if current and current["start"] == value["start"] and current["state"] != "Z":
                os.kill(value["pid"], signal.SIGTERM)
                signals.append({**value, "signal": "SIGTERM"})
        deadline = time.monotonic() + 3
        while own_children() and time.monotonic() < deadline:
            reap()
            time.sleep(0.05)
        for value in own_children():
            current = state(value["pid"])
            if current and current["start"] == value["start"] and current["state"] != "Z":
                os.kill(value["pid"], signal.SIGKILL)
                signals.append({**value, "signal": "SIGKILL"})
        deadline = time.monotonic() + 3
        while own_children() and time.monotonic() < deadline:
            reap()
            time.sleep(0.05)
        reap()
        result["remaining_children"] = own_children()
        path = root / "browser-result-private.json"
        if path.exists():
            proof = json.loads(path.read_text())
            result["status"] = proof["status"]
            result["recorded_process_cleanup"] = [
                {
                    **p,
                    "original_kernel_identity_gone": not (current := state(p["pid"]))
                    or current["start"] != p["start"],
                }
                for p in proof["processes"]
            ]
        with (root / "supervisor-private.json").open("x") as output:
            json.dump(result, output, indent=2)
        (root / "supervisor-private.json").chmod(0o600)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "remaining_children": len(result["remaining_children"]),
                }
            )
        )
    if (
        result["status"] != "accepted_native_send_ack_loss_recovery_pass"
        or result["remaining_children"]
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
