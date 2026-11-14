"""Allocation guard only. Creative Blender scripts must be authored by actual Copilot."""

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

BLENDER = "/home/quintin/.local/opt/blender-4.5.14-linux-x64/blender"
CLAIM = Path.home() / ".cache/proscenium/native-claims/blender-render.lock"


def main():
    root = Path.cwd().resolve()
    name, script = sys.argv[1:3]
    script = (root / script).resolve(strict=True)
    assert script.is_relative_to(root) and name in {"import", "revise", "history-adopt"}
    receipts = root / "reports/native-allocation"
    receipts.mkdir(parents=True, exist_ok=True)
    if len(list(receipts.glob("*.claim"))) >= 3:
        raise SystemExit("Three-process native allocation exhausted")
    available = (
        int(
            next(
                line
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            ).split()[1]
        )
        * 1024
    )
    assert available >= 4 * 1024**3, "Protected four-GiB host headroom unavailable"
    CLAIM.parent.mkdir(parents=True, exist_ok=True)
    with CLAIM.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (receipts / f"{name}.claim").open("x") as claim:
            claim.write("Reserved before launch; never repeat an uncertain authoring effect.\n")
            claim.flush()
            os.fsync(claim.fileno())
        command = [
            BLENDER,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--threads",
            "4",
            "--python-exit-code",
            "1",
            "--python",
            str(script),
        ]
        record = {
            "name": name,
            "command": command,
            "available_bytes_before": available,
            "claim": str(CLAIM),
            "timeout": 180,
        }
        with (receipts / f"{name}.log").open("wb") as log:
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            record["pid"] = child.pid
            record["start"] = Path(f"/proc/{child.pid}/stat").read_text().split(") ")[1].split()[19]
            (receipts / f"{name}.json").write_text(json.dumps(record, indent=2))
            try:
                record["exit_code"] = child.wait(timeout=180)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                record["timeout_exceeded"] = True
                record["exit_code"] = child.returncode
        record["reaped"] = True
        (receipts / f"{name}.json").write_text(json.dumps(record, indent=2))
        print(json.dumps(record))
        if record["exit_code"]:
            raise SystemExit(record["exit_code"])


if __name__ == "__main__":
    main()
