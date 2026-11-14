"""One-shot operator command transport. Never repeats an unknown claimed effect."""

import json
import sys
import time
from pathlib import Path

root, identity = Path(sys.argv[1]), sys.argv[2]
command = json.loads(sys.argv[3])
path = root / "commands" / (identity + ".json")
assert not path.exists() and not path.with_suffix(".claimed").exists()
with path.open("x") as output:
    json.dump(command, output)
receipt = root / "receipts" / path.name
deadline = time.monotonic() + (
    660 if command["action"] in {"wait-import", "observe-operator"} else 180
)
while not receipt.exists() and time.monotonic() < deadline:
    time.sleep(0.1)
if not receipt.exists():
    raise SystemExit("Original command is uncertain. Inspect it, never resend.")
print(receipt.read_text())
if json.loads(receipt.read_text())["status"] != "completed":
    raise SystemExit(1)
