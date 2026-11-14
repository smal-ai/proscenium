"""Fixture-only allocation guard. The native model supplies/repairs the authoring script.

No geometry, checkpoint or publication code lives here. Execution claims are append-only.
"""

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

CLAIM = Path("/tmp/opencode/proscenium-native-claims/blender-render.lock")
BLENDER = "/home/quintin/.local/opt/blender-4.5.14-linux-x64/blender"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def admission(directory, script):
    claims = sorted(directory.glob("claim-*.json"))
    if len(claims) >= 2:
        raise ValueError("Two authoring invocations exhausted")
    if claims:
        first = json.loads((directory / "terminal-1.json").read_text())
        if first["exit_code"] == 0 or first.get("uncertain") or first.get("timed_out"):
            raise ValueError("No second authoring after success or uncertain/timed-out execution")
        if first["script_sha256"] == sha(script):
            raise ValueError("Second authoring requires an actual model-authored script repair")
    return len(claims) + 1


def execute(project, script):
    script = script.resolve(strict=True)
    if not script.is_relative_to(project / "assets/e04") or script.suffix != ".py":
        raise ValueError("Only the model-owned asset authoring script is allocated")
    directory = project / "tools/m10-complete-receipts"
    directory.mkdir(exist_ok=True)
    with (directory / "allocation.lock").open("a+b") as allocation:
        fcntl.flock(allocation, fcntl.LOCK_EX | fcntl.LOCK_NB)
        number = admission(directory, script)
        available = next(
            int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
        if available < 5 * 1024**3:
            raise RuntimeError("Protected four-GiB host headroom unavailable")
        argv = [
            BLENDER,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--threads",
            "4",
            "--python-exit-code",
            "17",
            "--python",
            str(script),
        ]
        record = {
            "number": number,
            "script": str(script.relative_to(project)),
            "script_sha256": sha(script),
            "argv": argv,
            "timeout_seconds": 180,
            "mem_available_before": available,
        }
        with (directory / f"claim-{number}.json").open("x") as output:
            json.dump(record, output, indent=2)
        with CLAIM.open("a+b") as resource:
            fcntl.flock(resource, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (directory / f"blender-{number}.log").open("xb") as log:
                child = subprocess.Popen(
                    argv,
                    cwd=project,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env={
                        **os.environ,
                        "OMP_NUM_THREADS": "4",
                        "MKL_NUM_THREADS": "4",
                        "OPENBLAS_NUM_THREADS": "4",
                    },
                    start_new_session=True,
                )
            start = Path(f"/proc/{child.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
            record.update(pid=child.pid, start_ticks=start, started_ns=time.monotonic_ns())
            with (directory / f"process-{number}.json").open("x") as output:
                json.dump(record, output, indent=2)
            try:
                record["exit_code"] = child.wait(timeout=180)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                record.update(exit_code=child.wait(timeout=10), timed_out=True)
            record["finished_ns"] = time.monotonic_ns()
            with (directory / f"terminal-{number}.json").open("x") as output:
                json.dump(record, output, indent=2)
            print((directory / f"blender-{number}.log").read_text()[-16000:])
            print(json.dumps(record, sort_keys=True))
            return record["exit_code"]


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[1]
    sys.exit(execute(project, Path(sys.argv[1])))
