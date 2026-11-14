"""One-shot inotify signal for an exact coordinator decision or durable final outcome."""

import ctypes
import json
import os
import select

from m10_continue_final_snapshot import FINAL


def main():
    libc = ctypes.CDLL(None, use_errno=True)
    descriptor = libc.inotify_init1(0)
    if descriptor < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1")
    try:
        if libc.inotify_add_watch(descriptor, os.fsencode(FINAL), 0x100 | 0x80 | 0x8) < 0:
            raise OSError(ctypes.get_errno(), "inotify_add_watch")
        while True:
            for name in ("unexpected-decision.private.json", "final.done.json"):
                path = FINAL / name
                if path.exists():
                    print(
                        json.dumps(
                            {"signal_file": str(path), "body": json.loads(path.read_text())},
                            indent=2,
                        )
                    )
                    return
            ready, _, _ = select.select([descriptor], [], [], 790)
            if not ready:
                raise TimeoutError("Owned final proof produced no signal")
            os.read(descriptor, 16384)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    main()
