"""One renewed authoring/inspection allocation. No creative Blender code here."""

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    root = Path.cwd().resolve()
    script = (root / sys.argv[1]).resolve(strict=True)
    assert script.is_relative_to(root)
    output = root / "reports/native-adoption-allocation"
    output.mkdir(parents=True, exist_ok=True)
    claim = Path.home() / ".cache/proscenium/native-claims/blender-render.lock"
    available = (
        int(
            next(
                s
                for s in Path("/proc/meminfo").read_text().splitlines()
                if s.startswith("MemAvailable:")
            ).split()[1]
        )
        * 1024
    )
    assert available >= 4 * 1024**3, "Protected four-GiB host headroom unavailable"
    claim.parent.mkdir(parents=True, exist_ok=True)
    with claim.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (output / "adoption.claim").open("x") as reservation:
            reservation.write("One renewed process reserved. Do not retry an uncertain launch.\n")
            reservation.flush()
            os.fsync(reservation.fileno())
        command = [
            "/home/quintin/.local/opt/blender-4.5.14-linux-x64/blender",
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
        with (output / "blender.log").open("wb") as log:
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            record = {
                "pid": child.pid,
                "start": Path(f"/proc/{child.pid}/stat").read_text().split(") ")[1].split()[19],
                "command": command,
                "available_bytes_before": available,
                "claim": str(claim),
                "timeout": 180,
            }
            (output / "process.json").write_text(json.dumps(record, indent=2))
            try:
                record["exit_code"] = child.wait(timeout=180)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                record.update(exit_code=child.returncode, timeout_exceeded=True)
        record["reaped"] = True
        (output / "terminal.json").write_text(json.dumps(record, indent=2))
        print(json.dumps(record))
        if record["exit_code"]:
            raise SystemExit(record["exit_code"])


if __name__ == "__main__":
    main()
