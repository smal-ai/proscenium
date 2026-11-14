#!/usr/bin/env python3
"""Record exact browser identity, then exec the pinned browser for UI-only regressions."""

import json
import os
import sys
from pathlib import Path

root = Path("/home/quintin/.cache/proscenium-development/m03-resume-proof-e3cddf8")
record = {
    "pid": os.getpid(),
    "start": Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19],
}
with (root / "ui-checks-browsers.jsonl").open("a") as output:
    output.write(json.dumps(record) + "\n")
binary = str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome")
os.execv(binary, [binary, "--disable-gpu", *sys.argv[1:]])
