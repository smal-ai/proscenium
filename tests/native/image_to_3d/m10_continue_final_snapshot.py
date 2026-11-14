"""Private before-snapshot for the corrected ORIGINAL M10 continuation only."""

import json
from pathlib import Path

import m10_continue_snapshot as snapshot

FINAL = Path("/home/quintin/.cache/proscenium-development/milestones/m10-final-a")


def main():
    snapshot.ROOT = FINAL
    snapshot.main()
    failed = [
        snapshot.ORIGINAL / name
        for name in (
            "fixture.json",
            "native-request-result.json",
            "context-rejection.json",
            "cleanup.json",
            "public-blocked.json",
        )
    ]
    prior = Path("/home/quintin/.cache/proscenium-development/milestones/m10-continuation-a")
    failed += [prior / name for name in ("before.json", "host.json", "browser-result.private.json")]
    before = json.loads((FINAL / "before.json").read_text())
    before["failed_report_hashes"] = {str(path): snapshot.sha(path) for path in failed}
    before["orphaned_bash_before"] = next(
        row
        for row in snapshot.records("invocations").values()
        if row.get("native_id") == "call_Yo8Pyi1HvNYCI5E6CSQxCamx"
    )
    assert before["orphaned_bash_before"]["state"] == "failed"
    snapshot.write(FINAL / "before.json", before)


if __name__ == "__main__":
    main()
