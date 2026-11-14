"""Submit one immutable trusted fixture command. Never retries an uncertain effect."""

import json
import sys
import time
from pathlib import Path

root, identity, action = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
path = root / "commands" / f"{identity}.json"
assert not path.with_suffix(".claimed").exists(), (
    "Original command already claimed. Inspect it, never redispatch."
)
assert not (root / "receipts" / path.name).exists(), (
    "Original receipt already exists. Read it instead."
)
with path.open("x") as stream:
    json.dump({"action": action}, stream)
deadline = time.monotonic() + (190 if action == "inspect" else 90)
while time.monotonic() < deadline:
    receipt = root / "receipts" / path.name
    if receipt.exists():
        value = json.loads(receipt.read_text())
        print(
            json.dumps(
                {
                    "status": value["status"],
                    "receipt": str(receipt),
                    "error": value.get("message"),
                    "result": value.get("result", {}).get("receipt"),
                },
                indent=2,
            )
        )
        if value["status"] != "completed":
            raise SystemExit(2)
        break
    time.sleep(0.1)
else:
    raise RuntimeError("Unknown outcome. Inspect original claim/receipt, never blindly resubmit.")
