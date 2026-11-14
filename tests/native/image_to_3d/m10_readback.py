"""ONE allocated read-only native inspection, serialised with the existing OS claim."""

import fcntl
import json
import os
import signal
import sqlite3
import subprocess
import time
from pathlib import Path

from m10_fixture import CLAIM, ROOT, mem_available, sha, write


def main():
    marker = ROOT / "readback-process.json"
    if marker.exists():
        raise ValueError("An inspection is already allocated. Never start a second one")
    with sqlite3.connect(f"file:{ROOT}/project/.proscenium/state.sqlite3?mode=ro", uri=True) as db:
        versions = {
            row[0]: json.loads(row[1])
            for row in db.execute("SELECT id,body FROM records WHERE namespace='versions'")
        }
        jobs = [
            json.loads(row[0])
            for row in db.execute("SELECT body FROM records WHERE namespace='jobs'")
        ]
    assert "m10-asset-v2" in versions
    assert all(job["state"] in {"succeeded", "failed", "cancelled"} for job in jobs)
    project = (ROOT / "project").resolve()
    sources = []
    for version_id in ("m10-asset-v1", "m10-asset-v2"):
        version = versions[version_id]
        source = (project / version["source"]["entry_path"]).resolve(strict=True)
        assert source.is_relative_to(project)
        origin = next(
            row["origin"]
            for row in version["source"]["files"]
            if row["destination"] == version["source"]["entry_path"]
        )
        assert sha(source) == origin["sha256"]
        sources.append(source)
    if mem_available() < 5 * 1024**3:
        raise RuntimeError("Not enough host headroom for the single allocated inspection")
    script = Path(__file__).with_name("m10_inspect.py").resolve()
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
        str(ROOT / "readback.json"),
    ]
    report = {
        "state": "prepared",
        "argv": argv,
        "script_sha256": sha(script),
        "timeout": 180,
        "mem_available_before": mem_available(),
        "shared_claim_path": CLAIM,
    }
    with marker.open("x") as output:
        json.dump(report, output, indent=2)
    Path(CLAIM).parent.mkdir(parents=True, exist_ok=True)
    with Path(CLAIM).open("a+b") as lock, (ROOT / "readback.log").open("wb") as log:
        fcntl.flock(lock, fcntl.LOCK_EX)
        child = subprocess.Popen(
            argv,
            cwd=project,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4"},
            start_new_session=True,
        )
        start = Path(f"/proc/{child.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        report.update(
            state="running",
            pid=child.pid,
            start=start,
            process_group=os.getpgid(child.pid),
            started_ns=time.monotonic_ns(),
        )
        write(marker, report)
        try:
            report["exit_code"] = child.wait(timeout=180)
        except subprocess.TimeoutExpired:
            if Path(f"/proc/{child.pid}").exists():
                assert (
                    Path(f"/proc/{child.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
                    == start
                )
                os.killpg(child.pid, signal.SIGKILL)
            report["exit_code"] = child.wait(timeout=10)
            report["timeout_exceeded"] = True
        report.update(
            state="completed", ended_ns=time.monotonic_ns(), mem_available_after=mem_available()
        )
        write(marker, report)
    print(json.dumps(report, indent=2))
    assert report["exit_code"] == 0


if __name__ == "__main__":
    main()
