"""ONE read-only Blender inspection of actual v1 and published native-authored v2."""

import fcntl
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from m10_continue_final_snapshot import FINAL
from m10_continue_run import ticks
from m10_continue_snapshot import ORIGINAL, records, sha, write

CLAIM = Path("/tmp/opencode/proscenium-native-claims/blender-render.lock")


def main():
    marker = FINAL / "readback-process.json"
    if marker.exists():
        raise ValueError("An allocated readback receipt already exists")
    versions = records("versions")
    jobs = list(records("jobs").values())
    assert "m10-asset-v2" in versions
    assert all(job["state"] == "succeeded" for job in jobs)
    sources = []
    for version_id in ("m10-asset-v1", "m10-asset-v2"):
        version = versions[version_id]
        path = (ORIGINAL / "project" / version["source"]["entry_path"]).resolve(strict=True)
        assert path.is_relative_to((ORIGINAL / "project").resolve())
        origin = next(
            row["origin"]
            for row in version["source"]["files"]
            if row["destination"] == version["source"]["entry_path"]
        )
        assert sha(path) == origin["sha256"]
        sources.append(path)
    available = next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    if available < 5 * 1024**3:
        raise RuntimeError("Not enough host headroom for allocated readback")
    script = Path(__file__).with_name("m10_inspect.py")
    argv = [
        "/home/quintin/.local/bin/blender",
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--threads",
        "4",
        "--python-exit-code",
        "17",
        "--python",
        str(script),
        "--",
        *map(str, sources),
        str(FINAL / "readback.json"),
    ]
    report = {
        "state": "prepared",
        "argv": argv,
        "script_sha256": sha(script),
        "timeout": 180,
        "mem_available_before": available,
        "source_hashes_before": {str(path): sha(path) for path in sources},
    }
    with marker.open("x") as output:
        json.dump(report, output, indent=2)
    CLAIM.parent.mkdir(parents=True, exist_ok=True)
    with CLAIM.open("a+b") as lock, (FINAL / "readback.log").open("wb") as log:
        fcntl.flock(lock, fcntl.LOCK_EX)
        child = subprocess.Popen(
            argv,
            cwd=ORIGINAL / "project",
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4"},
            start_new_session=True,
        )
        report.update(
            pid=child.pid, start=ticks(child.pid), started_ns=time.monotonic_ns(), state="running"
        )
        write(marker, report)
        try:
            report["exit_code"] = child.wait(timeout=180)
        except subprocess.TimeoutExpired:
            if ticks(child.pid) == report["start"]:
                os.killpg(child.pid, signal.SIGKILL)
            report["exit_code"] = child.wait(timeout=10)
            report["timeout_exceeded"] = True
        report.update(
            state="completed",
            ended_ns=time.monotonic_ns(),
            source_hashes_after={str(path): sha(path) for path in sources},
        )
        write(marker, report)
    print(json.dumps(report, indent=2))
    assert report["exit_code"] == 0
    assert report["source_hashes_before"] == report["source_hashes_after"]


if __name__ == "__main__":
    main()
