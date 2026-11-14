"""Detached bounded supervisor. Journals survive backend loss, but never start on boot."""

import concurrent.futures
import fcntl
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .journal import atomic, identity, read, sha
from .media import encode, verify_video


def segment(root: Path, task: dict, first: int, last: int) -> dict:
    target = root / f"segment-{first:08d}-{last:08d}.mp4"
    pending = target.with_suffix(".pending.mp4")
    started = time.monotonic_ns()
    argv = encode(root, first, last - first + 1, task["spec"]["fps"], task["config"], pending)
    verify_video(
        pending, task["spec"]["fps"], last - first + 1, task["spec"]["dimensions"], task["config"]
    )
    pending.replace(target)
    body = dict(
        schema_version="1.0",
        segment_id=f"{task['attempt_id']}-s{first}-{last}",
        project_id=task["project_id"],
        job_id=task["job_id"],
        attempt_id=task["attempt_id"],
        shot_id=task["spec"]["shot_id"],
        version_id=task["version_id"],
        source_frames={"start": first, "end": last},
        fps=task["spec"]["fps"],
        frame_count=last - first + 1,
        artifact_id=f"{task['attempt_id']}-s{first}-{last}",
        output_path=target.name,
        sha256=sha(target.read_bytes()),
        input_digest=task["input_digest"],
    )
    atomic(target.with_suffix(".json"), body)
    atomic(
        root / f"encode-{first:08d}.json",
        {
            "started_ns": started,
            "ready_ns": time.monotonic_ns(),
            "argv": argv,
            "last_frame_existed": (
                root / f"frames/{task['spec']['frames']['end']:08d}.png"
            ).exists(),
        },
    )
    return body


def supervise(root: Path) -> None:
    task = read(root / "task.json")
    config = task["config"]
    own_identity = identity(os.getpid())
    assert own_identity is not None
    atomic(
        root / "process.json",
        {
            **own_identity,
            "task_sha256": sha((root / "task.json").read_bytes()),
            "started_ns": time.monotonic_ns(),
        },
    )
    native_exit = None
    native = None
    terminal: dict[str, Any] = {"state": "failed", "native_exit_code": None}
    try:
        if "assembly" in task:
            from .assembly import execute

            with (root / "writer.lock").open("a+") as writer:
                fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
                execute(root, task)
            terminal["state"] = "succeeded"
            return  # In-process adapter evidence, no invented native/Blender exit code.
        claim = Path(config["claim_path"])
        claim.parent.mkdir(parents=True, exist_ok=True)
        with (root / "writer.lock").open("a+") as writer, claim.open("a+") as resource:
            fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(resource, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if (root / "cancel.json").exists():
                terminal["state"] = "cancelled"
                return
            worker = Path(__file__).with_name("blender_worker.py")
            if sha(worker.read_bytes()) != task["entrypoint_sha256"]:
                raise ValueError("Installed renderer changed since the attempt was prepared")
            argv = [
                config["blender"],
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--threads",
                str(config["threads"]),
                "--python-exit-code",
                "1",
                "--python",
                str(worker),
                "--",
                str(root),
            ]
            started = time.monotonic()
            with (root / "blender.log").open("wb") as log:
                native = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env={
                        **os.environ,
                        "OMP_NUM_THREADS": str(config["threads"]),
                        "OPENBLAS_NUM_THREADS": str(config["threads"]),
                    },
                )
                native_identity = identity(native.pid)
                assert native_identity is not None
                atomic(root / "native-process.json", native_identity)
                assert native.stdout is not None
                selector = selectors.DefaultSelector()
                selector.register(native.stdout, selectors.EVENT_READ)
                futures: list[concurrent.futures.Future] = []
                scheduled = set()
                spec = task["spec"]
                # PNG keyframes preserve the exact captured camera/intrinsic dimensions.
                # Keep the existing optional segment for even-sized keyframes, but never
                # pad/crop/round an odd still merely to satisfy yuv420p encoding.
                encode_segments = spec["purpose"] != "proxy" and not (
                    spec["purpose"] == "keyframe"
                    and (spec["dimensions"]["width"] % 2 or spec["dimensions"]["height"] % 2)
                )
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as encoder:
                    while True:
                        if encode_segments:
                            for first in range(
                                spec["frames"]["start"], spec["frames"]["end"] + 1, 4
                            ):
                                last = min(first + 3, spec["frames"]["end"])
                                if first not in scheduled and all(
                                    (root / f"frame-{frame:08d}.json").exists()
                                    for frame in range(first, last + 1)
                                ):
                                    for frame in range(first, last + 1):
                                        row = read(root / f"frame-{frame:08d}.json")
                                        if (
                                            sha((root / row["output_path"]).read_bytes())
                                            != row["sha256"]
                                        ):
                                            raise ValueError("Frame changed before encoding")
                                    scheduled.add(first)
                                    futures.append(encoder.submit(segment, root, task, first, last))
                        cancelled = (root / "cancel.json").exists()
                        timed_out = time.monotonic() - started > config["timeout"]
                        if (
                            (cancelled and not (root / "native-complete.json").exists())
                            or timed_out
                        ) and native.poll() is None:
                            os.killpg(native.pid, signal.SIGKILL)
                        events = selector.select(timeout=0.01)
                        if events:
                            chunk = os.read(native.stdout.fileno(), 65536)
                            if chunk:
                                log.write(chunk)
                                log.flush()
                            elif native.poll() is not None:
                                # One final scheduling pass handles the last completed group.
                                if not encode_segments or len(scheduled) == sum(
                                    all(
                                        (root / f"frame-{frame:08d}.json").exists()
                                        for frame in range(
                                            first, min(first + 3, spec["frames"]["end"]) + 1
                                        )
                                    )
                                    for first in range(
                                        spec["frames"]["start"], spec["frames"]["end"] + 1, 4
                                    )
                                ):
                                    break
                    native_exit = native.wait()
                    if (root / "native-error.json").exists():
                        terminal["error_code"] = read(root / "native-error.json")["error_code"]
                    selector.close()
                    optional_segment_errors = []
                    for future in futures:
                        try:
                            future.result()
                        except Exception as error:
                            if spec["purpose"] != "keyframe":
                                raise
                            optional_segment_errors.append(type(error).__name__)
                    if optional_segment_errors:
                        # The PNG contract is authoritative for keyframes. A failed
                        # optional encode has no segment receipt and is never playable.
                        terminal["optional_segment_errors"] = optional_segment_errors
                complete = (
                    (root / "proxy-ready.json").exists()
                    if spec["purpose"] == "proxy"
                    else all(
                        (root / f"frame-{frame:08d}.json").exists()
                        for frame in range(spec["frames"]["start"], spec["frames"]["end"] + 1)
                    )
                )
                terminal["state"] = (
                    "succeeded"
                    if native_exit == 0 and complete
                    else "cancelled"
                    if cancelled
                    else "failed"
                )
                terminal["timed_out"] = timed_out
    except Exception as exc:
        terminal["error"] = type(exc).__name__
        if "assembly" in task and (root / "cancel.json").exists():
            terminal["state"] = "cancelled"
        if native is not None and native.poll() is None:
            os.killpg(native.pid, signal.SIGKILL)
            native_exit = native.wait()
    finally:
        terminal.update(
            native_exit_code=native_exit,
            finished_ns=time.monotonic_ns(),
            task_sha256=sha((root / "task.json").read_bytes()),
        )
        atomic(root / "terminal.json", terminal)


if __name__ == "__main__":
    supervise(Path(sys.argv[1]).resolve(strict=True))
