"""Linux exec-only parent-death fence. No intermediate process remains after exec.

PR_SET_PDEATHSIG survives exec of the pinned, non-setuid native binary. It applies
only to this process, not to independent admitted worker/provider processes.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
from pathlib import Path


def identity(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if stat[0] == "Z" else stat[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=int, required=True)
    parser.add_argument("--parent-start", required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("binary")
    args, native_args = parser.parse_known_args()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # Linux PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), "Cannot establish native parent-death fence")
    # Close the fork/prctl race. Never execute if the original backend died already.
    if os.getppid() != args.parent or identity(args.parent) != args.parent_start:
        raise SystemExit(125)
    record = {
        "pid": os.getpid(),
        "start": identity(os.getpid()),
        "parent_pid": args.parent,
        "parent_start": args.parent_start,
        "binary": str(Path(args.binary).resolve()),
    }
    descriptor = os.open(args.record, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(record, output)
        output.flush()
        os.fsync(output.fileno())
    os.execv(args.binary, [args.binary, *native_args])


if __name__ == "__main__":
    main()
