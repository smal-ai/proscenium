"""One-shot file-event signal. No output polling or native/model action."""

import ctypes
import json
import os
import select
import sys

from m10_complete_snapshot import ROOT


def main():
    names = (
        ("ui-preflight-ready.json", "observer-blocked.json", "stack.done.json")
        if sys.argv[-1] == "preflight"
        else ("unexpected-decision.private.json", "observer-blocked.json", "stack.done.json")
    )
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.inotify_init1(0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1")
    try:
        assert libc.inotify_add_watch(fd, os.fsencode(ROOT), 0x100 | 0x80 | 0x8) >= 0
        while True:
            for name in names:
                path = ROOT / name
                if path.exists():
                    try:
                        value = json.loads(path.read_text())
                    except json.JSONDecodeError:
                        continue
                    print(json.dumps({"signal": str(path), "body": value}, indent=2))
                    return
            ready, _, _ = select.select([fd], [], [], 600)
            if not ready:
                raise TimeoutError("No bounded fixture signal")
            os.read(fd, 16384)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
