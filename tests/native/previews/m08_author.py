"""Bounded supervisor for a MODEL-authored in-project recipe. No animation recipe here."""

import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


def main():
    root = Path.cwd().resolve()
    assert (root / "project.json").is_file()
    recipe = (root / sys.argv[1]).resolve(strict=True)
    assert recipe.is_relative_to(root) and recipe.suffix == ".py"
    available = (
        int(
            next(
                line.split()[1]
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            )
        )
        * 1024
    )
    if available < 4 * 1024**3:
        raise RuntimeError("The allocated 4 GiB host headroom is unavailable")
    claim = Path("/tmp/opencode/proscenium-native-claims/blender-render.lock")
    claim.parent.mkdir(parents=True, exist_ok=True)
    ledger = root.parent.parent / "m08-a2a2097-authoring.jsonl"
    with claim.open("a+") as held, ledger.open("a+") as budget:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(budget, fcntl.LOCK_EX)
        budget.seek(0)
        rows = [json.loads(line) for line in budget if line.strip()]
        assert len(rows) < 2, "Two-process background authoring/inspection allocation exhausted"
        record = {
            "root": str(root),
            "recipe": str(recipe.relative_to(root)),
            "recipe_sha256": hashlib.sha256(recipe.read_bytes()).hexdigest(),
            "started_ns": time.monotonic_ns(),
            "headroom_bytes": available,
        }
        budget.write(json.dumps(record) + "\n")
        budget.flush()
        os.fsync(budget.fileno())
        blender = shutil.which("blender")
        if not blender:
            raise RuntimeError("Installed Blender is unavailable")
        argv = [
            blender,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--threads",
            "4",
            "--python-exit-code",
            "1",
            "--python",
            str(recipe),
        ]
        with subprocess.Popen(
            argv,
            start_new_session=True,
            env={**os.environ, "OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "4"},
        ) as child:
            output = root / ".proscenium/tool-output"
            output.mkdir(parents=True, exist_ok=True)
            stat = Path(f"/proc/{child.pid}/stat").read_text().rpartition(")")[2].split()
            (output / f"m08-author-{len(rows) + 1}-process.json").write_text(
                json.dumps(
                    {
                        "pid": child.pid,
                        "start_ticks": int(stat[19]),
                        "argv": argv,
                        "supervisor_pid": os.getpid(),
                        "started_ns": record["started_ns"],
                    }
                )
                + "\n"
            )
            try:
                code = child.wait(timeout=180)
            except BaseException:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                raise
        record.update(
            pid=child.pid, argv=argv, native_exit_code=code, finished_ns=time.monotonic_ns()
        )
        output = root / ".proscenium/tool-output"
        output.mkdir(parents=True, exist_ok=True)
        (output / f"m08-author-{len(rows) + 1}.json").write_text(
            json.dumps(record, indent=2) + "\n"
        )
        if code:
            raise RuntimeError(f"Allocated authoring process failed with observed exit {code}")


if __name__ == "__main__":
    main()
