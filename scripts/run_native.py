"""Run one explicitly selected, bounded native acceptance profile in disposable storage."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path("/tmp/opencode") if Path("/tmp/opencode").is_dir() else Path(tempfile.gettempdir())

# Integration-owned profiles. A package must supply its actual entry point and limits
# before joining this catalogue. An unknown profile never runs a guessed command.
PROFILES = {
    "blender-source": {
        "resource": "blender-cpu",
        "timeout_seconds": 900,
        "cpu_threads": 4,
        "cloud_root_requests": 0,
        "gpu": False,
        "command": [
            sys.executable,
            str(ROOT / "tools/proofs/blender/create_fixture.py"),
            "--output",
            "{output}/fixture",
        ],
    }
}


@contextmanager
def claim(path: Path):
    """OS-held ownership, with no timestamp expiry or PID-only lease takeover."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def run_profile(name: str, profile: dict, output: Path) -> int:
    command = [part.replace("{output}", str(output)) for part in profile["command"]]
    source = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    receipt = {
        "profile": name,
        "application_commit": source,
        "contract_revision": "1.0.0",
        "started_at": datetime.now(UTC).isoformat(),
        "allocation": {key: value for key, value in profile.items() if key != "command"},
        "command": command,
        "status": "running",
    }
    receipt_path = output / "run.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    environment = {
        **os.environ,
        "PROSCENIUM_TEST_OUTPUT": str(output),
        "PROSCENIUM_NATIVE_PROFILE": name,
        "PROSCENIUM_NATIVE_ALLOCATED": "1",
        "OMP_NUM_THREADS": str(profile["cpu_threads"]),
    }
    # The new process group contains only this profile and its deliberately spawned
    # children. Timeout cancellation never targets a shared service or desktop app.
    with (output / "command.log").open("wb") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=profile["timeout_seconds"])
            receipt["status"] = "passed" if code == 0 else "failed"
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 124
            receipt["status"] = "timed_out"
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            receipt["status"] = "interrupted"
            receipt["finished_at"] = datetime.now(UTC).isoformat()
            receipt["native_exit_code"] = process.returncode
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
            raise
    receipt["finished_at"] = datetime.now(UTC).isoformat()
    receipt["native_exit_code"] = process.returncode
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"status": receipt["status"], "profile": name, "evidence": str(output)}))
    if code:
        print((output / "command.log").read_text(errors="replace")[-8000:], file=sys.stderr)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", nargs="?", choices=sorted(PROFILES))
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        print(json.dumps(PROFILES, indent=2))
        return 0
    if not args.profile:
        parser.error("An explicit PROFILE is required")
    profile = PROFILES[args.profile]
    try:
        with claim(TEMP_ROOT / "proscenium-native-claims" / f"{profile['resource']}.lock"):
            output = Path(tempfile.mkdtemp(prefix=f"proscenium-{args.profile}-", dir=TEMP_ROOT))
            return run_profile(args.profile, profile, output)
    except BlockingIOError:
        print("Native resource is already owned. No test process started.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
