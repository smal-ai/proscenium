"""Close only recorded M10 owners after terminal local jobs and known native shutdown."""

import json
import os
import signal
import time
from pathlib import Path

from m10_drive import records
from m10_fixture import ROOT, original_snapshot, write


def alive(record):
    try:
        fields = Path(f"/proc/{record['pid']}/stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return False
    return fields[19] == str(record["start"]) and fields[0] != "Z"


def stop(record):
    if not alive(record):
        return
    if os.getpgid(record["pid"]) != record["pid"]:
        raise ValueError("Recorded owner is not its process-group leader")
    os.killpg(record["pid"], signal.SIGTERM)
    until = time.monotonic() + 30
    while alive(record) and time.monotonic() < until:
        time.sleep(0.1)
    if alive(record):
        raise RuntimeError("Recorded owner did not exit gracefully. Do not kill another process")


def main():
    info = json.loads((ROOT / "processes.json").read_text())
    assert info["boot_id"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    jobs = records("jobs")
    assert all(job["state"] in {"succeeded", "failed", "cancelled"} for job in jobs)
    history = next((ROOT / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"))
    events = [json.loads(line) for line in history.read_text().splitlines()]
    assert events[-1]["type"] == "session.shutdown", "Never tear down unknown model work"
    before = json.loads((ROOT / "fixture.json").read_text())["original_before"]
    # JSON-normalise the tuple rows from SQLite for equality with the durable original receipt.
    original = json.loads(json.dumps(original_snapshot()))
    assert original == before
    observed = json.loads((ROOT / "native-request-result.json").read_text())["owned_processes"]
    stop(info["server"])
    stop(info["browser"])
    remaining = [row for row in observed.values() if alive(row)]
    report = {
        "server_closed": not alive(info["server"]),
        "browser_closed": not alive(info["browser"]),
        "remaining_observed_owned_processes": remaining,
        "original_e04_preserved": original == before,
        "original_e04_after": original,
        "local_jobs_before_shutdown": jobs,
        "model_shutdown_witnessed": True,
        "independent_services_mutated": False,
        "native_authoring_executed": False,
        "new_inference_calls": 0,
    }
    write(ROOT / "cleanup.json", report)
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"original_e04_after", "local_jobs_before_shutdown"}
            },
            indent=2,
        )
    )
    assert not remaining


if __name__ == "__main__":
    main()
