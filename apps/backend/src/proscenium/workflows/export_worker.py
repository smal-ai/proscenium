"""Fixed bounded Blender export supervisor. No database or caller credentials in this process."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any


def identity(pid: int) -> dict:
    return dict(
        pid=pid,
        start_ticks=Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19],
        boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    )


def atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def command(request: dict) -> list[str]:
    script = Path(__file__).resolve().parents[5] / "tools/unity/export_blender.py"
    return [
        request["blender"],
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--threads",
        "4",
        request["entry"],
        "--python-exit-code",
        "1",
        "--python",
        str(script),
        "--",
        request["materialised"],
        request["output"],
        request["source_digest"],
    ]


def main(path: Path) -> None:
    request = json.loads(path.read_text())
    receipt = dict(
        binding=request["binding"],
        source_digest=request["source_digest"],
        state="starting",
        supervisor=identity(os.getpid()),
        native_exit_code=None,
    )
    destination = path.parent / "export-worker.json"
    atomic(destination, receipt)
    process: subprocess.Popen | None = None
    interrupted = False
    stopping = False

    def interrupt(_signal: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        # Do not unwind Popen between OS spawn and Python handle assignment. The
        # pending interruption is honoured as soon as the exact child is owned.
        if process is not None and not stopping:
            raise KeyboardInterrupt

    def stop() -> None:
        nonlocal stopping
        stopping = True
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    try:
        claim_path = Path(request["claim_path"])
        claim_path.parent.mkdir(parents=True, exist_ok=True)
        with claim_path.open("a") as claim, (path.parent / "export.log").open("w") as log:
            fcntl.flock(claim, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if interrupted:
                raise KeyboardInterrupt
            process = subprocess.Popen(
                command(request),
                cwd=request["materialised"],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            receipt.update(state="running", process=identity(process.pid))
            atomic(destination, receipt)
            if interrupted:
                raise KeyboardInterrupt
            try:
                code = process.wait(timeout=180)
                receipt.update(state="complete", native_exit_code=code)
            except subprocess.TimeoutExpired:
                stop()
                receipt.update(
                    state="complete", native_exit_code=process.returncode, error="timeout"
                )
    except (OSError, KeyboardInterrupt) as error:
        stop()
        receipt.update(
            state="complete",
            native_exit_code=process.returncode if process else None,
            error=type(error).__name__,
        )
    finally:
        stop()
        atomic(destination, receipt)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
