"""Bounded, receipt-producing supervisor. Only the admitted adapter constructs its request."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any


def atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def process_identity(pid: int) -> dict[str, Any]:
    # Linux process start ticks prevent PID reuse from being mistaken for our process.
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {
        "pid": pid,
        "start_ticks": stat[19],
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }


def alive(identity: dict[str, Any]) -> bool:
    try:
        stat = Path(f"/proc/{identity['pid']}/stat").read_text().rsplit(")", 1)[1].split()
        return stat[0] != "Z" and process_identity(identity["pid"]) == identity
    except (OSError, KeyError, IndexError):
        return False


def observe_attempt(attempt_id: str, request_path: Path) -> list[dict[str, Any]]:
    """Reconcile a lost launch acknowledgement without guessing that nothing launched."""
    observed = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            args = (process / "cmdline").read_bytes().decode().split("\0")
            native = (
                "--proscenium-attempt" in args
                and args[args.index("--proscenium-attempt") + 1] == attempt_id
            )
            worker = str(request_path) in args and any(
                value.endswith("/unity/worker.py") for value in args
            )
            if native or worker:
                observed.append(process_identity(int(process.name)))
        except (OSError, UnicodeError, IndexError):
            continue
    return observed


def editor_running(project: Path) -> bool:
    if (project / "Temp/UnityLockfile").exists():
        return True
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            args = (process / "cmdline").read_bytes().decode().split("\0")
            if Path(os.readlink(process / "exe")).name != "Unity":
                continue
            if (
                "-projectPath" in args
                and Path(args[args.index("-projectPath") + 1]).resolve() == project
            ):
                return True
        except (OSError, UnicodeError, IndexError):
            continue
    return False


def stop(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def build_output_state(project: Path, relative: str) -> tuple[int, int, int, int] | None:
    output = project / relative
    if output.resolve() != output or not output.is_relative_to(project):
        raise RuntimeError("Unity build output is outside its declared project path")
    if not output.is_file():
        return None
    stat = output.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def main(request_path: Path) -> None:
    request = json.loads(request_path.read_text())
    receipt = {
        key: request[key]
        for key in ("job_id", "attempt_id", "input_digest", "request_digest", "source_digest")
    }
    receipt.update(
        state="starting", supervisor=process_identity(os.getpid()), native_exit_code=None
    )
    destination = request_path.parent / "worker.json"
    authored = request.get("authored")
    atomic(destination, receipt)
    process: subprocess.Popen[Any] | None = None

    def interrupt(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    try:
        with Path(request["project_lock"]).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            project = Path(request["project"])
            if editor_running(project):
                raise RuntimeError(
                    "This project's Editor/lock is active. Save and close it explicitly."
                )
            build_output = authored.get("build_output") if authored else None
            previous = build_output_state(project, build_output) if build_output else None
            with (request_path.parent / "console.log").open("w") as output:
                env = {**os.environ, "DOTNET_PROCESSOR_COUNT": "4"}
                process = subprocess.Popen(
                    request["command"],
                    cwd=project,
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                receipt.update(state="running", process=process_identity(process.pid))
                if authored:
                    atomic(request_path.parent / "process.json", receipt)
                atomic(destination, receipt)
                try:
                    code = process.wait(timeout=request["timeout"])
                    receipt.update(state="complete", native_exit_code=code)
                    if build_output and code == 0:
                        current = build_output_state(project, build_output)
                        receipt["build_output_verified"] = (
                            current is not None and current != previous
                        )
                except subprocess.TimeoutExpired:
                    stop(process)
                    receipt.update(
                        state="complete", native_exit_code=process.returncode, error="timeout"
                    )
    except KeyboardInterrupt:
        if process is not None:
            stop(process)
        receipt.update(
            state="complete",
            native_exit_code=process.returncode if process else None,
            error="supervisor_interrupted",
        )
    except (OSError, RuntimeError) as error:
        # A failed launch is evidence, not an invented native exit code.
        receipt.update(state="complete", error=str(error))
    finally:
        if process is not None and process.poll() is None:
            stop(process)
        if authored:
            atomic(request_path.parent / "terminal.json", receipt)
        atomic(destination, receipt)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
