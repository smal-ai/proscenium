"""One <=300-second own browser/server stack, four-GiB available-memory stopguard."""

import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from m07_review_fixture import REPO, inventory, protected


def memory():
    rows = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(rows["MemAvailable"].split()[0]) * 1024


def main(root):
    assert not (root / "run.json").exists(), "Never reuse a consumed UI allocation"
    assert memory() >= 4 * 1024**3, "Insufficient headroom, no browser/server started"
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", 18969)) != 0, "Own port is occupied"
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    report: dict[str, Any] = {"source": source, "start": time.time(), "children": {}, "samples": []}
    report["build"] = {
        str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (REPO / "apps/frontend/dist").rglob("*")
        if path.is_file()
    }
    (root / "run.json").write_text(json.dumps(report, indent=2))
    children = {}
    started = time.monotonic()
    try:
        for name, command in (
            (
                "server",
                [sys.executable, str(REPO / "tests/native/studio/m07_review_server.py"), str(root)],
            ),
            (
                "browser",
                ["node", str(REPO / "tests/native/studio/m07_review_browser.mjs"), str(root)],
            ),
        ):
            with (root / f"{name}.private.log").open("xb") as log:
                process = subprocess.Popen(
                    command,
                    cwd=REPO,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env={
                        **os.environ,
                        "M07_REVIEW_SOURCE": source,
                        "M07_REVIEW_HOLD_FOR_INSPECTION": "1",
                    },
                    start_new_session=True,
                )
            children[name] = process
            report["children"][name] = {
                "pid": process.pid,
                "start": Path(f"/proc/{process.pid}/stat")
                .read_text()
                .rsplit(") ", 1)[1]
                .split()[19],
            }
        while children["browser"].poll() is None:
            available = memory()
            report["samples"].append(
                {"seconds": time.monotonic() - started, "available": available}
            )
            assert available >= 4 * 1024**3, "Four-GiB headroom breached"
            assert time.monotonic() - started < 270, "Own UI allocation deadline"
            assert children["server"].poll() is None, "Own server exited early"
            time.sleep(0.2)
        assert children["browser"].returncode == 0, "Browser verification failed, retain evidence"
        report["pass"] = True
    finally:
        for name, process in reversed(list(children.items())):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            report["children"][name].update(returncode=process.returncode, reaped=True)
        before = json.loads((root / "original-before.private.json").read_text())
        report["original_unchanged"] = inventory() == before
        report["protected_identities_unchanged"] = protected(
            root / "restored/project"
        ) == json.loads((root / "protected-before.json").read_text())
        report["elapsed"] = time.monotonic() - started
        with socket.socket() as sock:
            report["port_released"] = sock.connect_ex(("127.0.0.1", 18969)) != 0
        (root / "run.json").write_text(json.dumps(report, indent=2) + "\n")
    assert (
        report["original_unchanged"]
        and report["port_released"]
        and report["protected_identities_unchanged"]
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
