"""One-process CPU authoring allocation. The model supplies the asset recipe."""

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
    recipe = (root / sys.argv[1]).resolve(strict=True)
    assert (root / "project.json").is_file() and recipe.is_relative_to(root)
    if "bpy.ops.render" in recipe.read_text():
        raise ValueError("The authoring allocation does not include rendering")
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
        raise RuntimeError("Required 4 GiB host headroom is unavailable")
    claim = Path("/tmp/opencode/proscenium-native-claims/blender-render.lock")
    claim.parent.mkdir(parents=True, exist_ok=True)
    with claim.open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (root.parent / "authoring-reservation.json").open("x") as reservation:
            record = {
                "recipe": str(recipe.relative_to(root)),
                "recipe_sha256": hashlib.sha256(recipe.read_bytes()).hexdigest(),
                "headroom_bytes": available,
                "started_ns": time.monotonic_ns(),
            }
            json.dump(record, reservation)
            reservation.flush()
            os.fsync(reservation.fileno())
        binary = shutil.which("blender")
        if not binary:
            raise RuntimeError("Blender is unavailable")
        argv = [
            binary,
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
        output = root / ".proscenium/tool-output"
        output.mkdir(parents=True, exist_ok=True)
        with (output / "author.log").open("wb") as log:
            with subprocess.Popen(
                argv,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "4"},
            ) as child:
                stat = Path(f"/proc/{child.pid}/stat").read_text().rpartition(")")[2].split()
                record.update(pid=child.pid, start_ticks=int(stat[19]), argv=argv)
                (output / "author-process.json").write_text(json.dumps(record, indent=2) + "\n")
                try:
                    code = child.wait(timeout=180)
                except BaseException:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                    raise
        record.update(native_exit_code=code, finished_ns=time.monotonic_ns())
        (output / "author-terminal.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"native_exit_code": code, "recipe_sha256": record["recipe_sha256"]}))
        if code:
            raise RuntimeError("The single allocated authoring process failed; inspect its log")


if __name__ == "__main__":
    main()
