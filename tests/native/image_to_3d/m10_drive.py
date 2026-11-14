"""One actual browser Queue, bounded root observation and owned-process accounting.

No direct model call or geometry authoring. SQLite reads are trusted harness diagnostics,
not exposed to the native model or substituted for the production browser/API path.
"""

import argparse
import json
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from m10_fixture import REPO, ROOT, mem_available, write


def browser(action, *args):
    return subprocess.run(
        ["node", str(Path(__file__).with_name("m10_browser.mjs")), str(ROOT), action, *args],
        cwd=REPO,
        check=True,
        timeout=90,
        capture_output=True,
        text=True,
    )


def records(namespace):
    with sqlite3.connect(f"file:{ROOT}/project/.proscenium/state.sqlite3?mode=ro", uri=True) as db:
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT body FROM records WHERE namespace=? ORDER BY id", (namespace,)
            )
        ]


def sample_processes(info, seen):
    table = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
            table[int(path.parent.name)] = (int(fields[1]), fields[19], fields[0])
        except (OSError, ValueError, IndexError):
            pass
    owned = {info[name]["pid"] for name in ("server", "browser")}
    owned.update(
        int(pid)
        for pid, row in seen.items()
        if int(pid) in table and table[int(pid)][1] == row["start"]
    )
    while True:
        added = {pid for pid, (parent, _, _) in table.items() if parent in owned}
        if added <= owned:
            break
        owned |= added
    for pid in owned:
        if pid not in table:
            continue
        parent, start, state = table[pid]
        try:
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            text = [part.decode(errors="replace") for part in argv if part]
            status = Path(f"/proc/{pid}/status").read_text().splitlines()
            rss = next(
                (int(line.split()[1]) * 1024 for line in status if line.startswith("VmRSS:")), 0
            )
        except OSError:
            continue
        previous = seen.get(str(pid), {})
        seen[str(pid)] = {
            "pid": pid,
            "ppid": parent,
            "start": start,
            "argv": text,
            "last_state": state,
            "max_rss_bytes": max(previous.get("max_rss_bytes", 0), rss),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmed-context-recovery", action="store_true")
    args = parser.parse_args()
    if (ROOT / "native-request-result.json").exists() and not args.confirmed_context_recovery:
        raise ValueError("An original request outcome exists. Never send another implicitly")
    info = json.loads((ROOT / "processes.json").read_text())
    before = records("requests")
    assert not [r for r in before if r["submission"]["type"] == "queue"]
    if args.confirmed_context_recovery:
        rejection = json.loads((ROOT / "context-rejection.json").read_text())
        assert (
            rejection["status"] == 409
            and rejection["response"]["error"]["code"] == "source_conflict"
        )
        assert rejection["requests_before"] == rejection["requests_after"] == []
        assert not records("copilot.session")
        (ROOT / "native-request-result.json").rename(ROOT / "unadmitted-driver-result.private.json")
    result: dict[str, Any] = {
        "model": "gpt-6.1-sol",
        "root_bound_seconds": 180,
        "children_allowed": 0,
        "scripted_test_user": True,
        "samples": [],
        "owned_processes": {},
        "browser_send_output": None,
    }
    sent = browser(
        "send", *(["confirmed-context-recovery"] if args.confirmed_context_recovery else [])
    )
    result["browser_send_output"] = sent.stdout
    deadline = time.monotonic() + 180
    try:
        while time.monotonic() < deadline:
            requests = [r for r in records("requests") if r["submission"]["type"] == "queue"]
            if requests:
                assert len(requests) == 1, "Never dispatch a duplicate cloud root"
                request = requests[0]
                # Admission time includes native startup and all model/tool work, a strict bound.
                elapsed = (
                    datetime.now(UTC)
                    - datetime.fromisoformat(request["accepted_at"].replace("Z", "+00:00"))
                ).total_seconds()
                result["request"] = request
                result["root_elapsed_seconds"] = elapsed
                children = [r for r in records("invocations") if r.get("kind") == "child"]
                if children:
                    result["blocker"] = "Native child activity exceeded this allocation"
                    browser("stop")
                    break
                available = mem_available()
                result["samples"].append(
                    {"monotonic_ns": time.monotonic_ns(), "mem_available_bytes": available}
                )
                if available < 5 * 1024**3:
                    result["blocker"] = "Protected host headroom threatened"
                    browser("stop")
                    break
                if request["receipt"]["state"] in {
                    "completed",
                    "failed",
                    "stopped",
                    "cancelled",
                    "outcome_uncertain",
                }:
                    break
                if elapsed >= 179:
                    result["blocker"] = "Native root reached 180-second allocation bound"
                    browser("stop")
                    break
            sample_processes(info, result["owned_processes"])
            write(ROOT / "native-request-progress.private.json", result)
            time.sleep(0.25)
        else:
            result["blocker"] = "Native root allocation expired"
            browser("stop")
        # Rendering is independently reconciled by the production lifespan, never resubmitted.
        until = time.monotonic() + 360
        while time.monotonic() < until:
            jobs = records("jobs")
            sample_processes(info, result["owned_processes"])
            result["jobs"] = jobs
            if all(job["state"] in {"succeeded", "failed", "cancelled"} for job in jobs):
                break
            write(ROOT / "native-request-progress.private.json", result)
            time.sleep(0.5)
        result["invocations"] = records("invocations")
        result["versions"] = records("versions")
        result["work"] = records("work")
        result["native_association"] = records("copilot.session")
        result["requests"] = records("requests")
        with sqlite3.connect(
            f"file:{ROOT}/project/.proscenium/state.sqlite3?mode=ro", uri=True
        ) as db:
            result["generation_reservations"] = [
                list(row) for row in db.execute("SELECT * FROM budget_reservations")
            ]
    finally:
        write(ROOT / "native-request-result.json", result)
        print(
            json.dumps(
                {
                    "request": result.get("request"),
                    "blocker": result.get("blocker"),
                    "jobs": [
                        {"id": job["job_id"], "state": job["state"]}
                        for job in result.get("jobs", [])
                    ],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
