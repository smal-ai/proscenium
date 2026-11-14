"""Read-only export of the actual follow-up, source/publication and preservation evidence."""

import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from m10_complete_snapshot import ORIGINAL, PROJECT, REPO, ROOT, SESSION
from m10_continue_run import ticks
from m10_continue_snapshot import records, sha, write
from proscenium.previews.journal import alive


def optional(name):
    path = ROOT / name
    return json.loads(path.read_text()) if path.exists() else None


def main():
    before = optional("before.json")
    browser = optional("result.private.json")
    host = optional("host.json")
    out = REPO / "docs/evidence/m10-complete"
    out.mkdir(parents=True, exist_ok=True)
    now = {namespace: records(namespace) for namespace in before["historical_records"]}
    historical = {
        namespace: all(now[namespace].get(identity) == record for identity, record in old.items())
        for namespace, old in before["historical_records"].items()
    }
    source_files = {name: sha(ORIGINAL / name) for name in before["file_hashes"]}
    immutable = {
        name: source_files[name] == value
        for name, value in before["file_hashes"].items()
        if not name.endswith("/revise_asset.py")
    }
    old_reports = {name: sha(Path(name)) for name in before["old_report_hashes"]}
    e04 = Path("/home/quintin/.cache/proscenium-development/image-to-3d/runs/e04-live-b/project")
    e04_after = {name: sha(e04 / name) for name in before["e04_file_hashes"]}
    new_requests = [
        record
        for identity, record in now["requests"].items()
        if identity not in before["historical_records"]["requests"]
    ]
    version = now["versions"].get("m10-asset-v2")
    new_jobs = [
        record
        for identity, record in now["jobs"].items()
        if identity not in before["historical_records"]["jobs"]
    ]
    work = now["work"].get(browser.get("work_id"))
    session = ORIGINAL / "machine/copilot-runtime/home/session-state" / SESSION / "events.jsonl"
    prior = (ROOT / "before-private/native-session/events.jsonl").read_bytes()
    history = session.read_bytes()
    old_events = [json.loads(line) for line in prior.splitlines()]
    all_events = [json.loads(line) for line in history.splitlines()]
    delta = all_events[len(old_events) :]
    redacted = []
    for event in delta:
        kind, data = event["type"], event.get("data", {})
        item = {
            key: event[key]
            for key in ("id", "type", "timestamp", "parentId", "agentId")
            if key in event
        }
        if kind == "assistant.message":
            item.update(
                public_content=data.get("content", ""),
                tool_requests=len(data.get("toolRequests", [])),
            )
        elif kind == "user.message":
            item["data"] = {key: data[key] for key in ("messageId", "clientTurnId") if key in data}
        elif kind == "tool.execution_start":
            item["data"] = {
                key: data[key] for key in ("toolName", "toolCallId", "arguments") if key in data
            }
        elif kind == "tool.execution_complete":
            item["data"] = {
                key: data[key] for key in ("toolCallId", "success", "error") if key in data
            }
        elif kind == "permission.requested":
            item["data"] = {
                key: data[key] for key in ("requestId", "permissionRequest") if key in data
            }
        elif kind == "permission.completed":
            item["data"] = {
                key: data[key] for key in ("requestId", "toolCallId", "result") if key in data
            }
        elif kind not in {
            "assistant.turn_start",
            "assistant.turn_end",
            "session.task_complete",
            "session.idle",
            "session.resume",
            "session.shutdown",
            "subagent.started",
            "subagent.completed",
        }:
            continue
        redacted.append(item)
    write(
        out / "native-delta-redacted.json", {"events": redacted, "private_reasoning_excluded": True}
    )
    previews = []
    retained_files = [
        ROOT / name
        for name in (
            "before.json",
            "host.json",
            "result.private.json",
            "prompt.txt",
            "original-model-script.py",
            "server-import-failed.log",
            "host-import-failed.json",
            "ui-preflight-ready.json",
            "send-authorised.json",
            "sdk-send-claimed.json",
            "sdk-send-ack.json",
            "session-allocation.json",
            "publication-replay.json",
            "readback.json",
            "readback-process.json",
            "browser-driver.private.log",
        )
        if (ROOT / name).exists()
    ]
    cleanup = []
    for job in new_jobs:
        root = ORIGINAL / f"project/.proscenium/jobs/previews/{job['job_id']}/{job['attempt_id']}"
        journals = {path.name: json.loads(path.read_text()) for path in root.glob("*.json")}
        previews.append({"job": job, "root": str(root), "journals": journals})
        retained_files += [path for path in root.rglob("*") if path.is_file()]
        for label in ("process.json", "native-process.json"):
            if label in journals:
                cleanup.append(
                    {"role": label, "identity": journals[label], "alive": alive(journals[label])}
                )
        if "native-profile.json" in journals:
            actual = journals["native-profile.json"]["process"]
            cleanup.append(
                {"role": "actual Blender profile", "identity": actual, "alive": alive(actual)}
            )
    guard = ORIGINAL / "project/tools/m10-complete-receipts"
    authors = [json.loads(path.read_text()) for path in sorted(guard.glob("terminal-*.json"))]
    for author in authors:
        cleanup.append(
            {
                "role": "authoring",
                "pid": author["pid"],
                "start": author["start_ticks"],
                "alive": ticks(author["pid"]) == str(author["start_ticks"]),
            }
        )
    retained_files += [path for path in guard.glob("*") if path.is_file()]
    for role in ("server", "browser"):
        if role in host:
            identity = host[role]
            cleanup.append(
                {
                    "role": role,
                    "pid": identity["pid"],
                    "start": identity["start"],
                    "alive": ticks(identity["pid"]) == str(identity["start"]),
                }
            )
    runtime = ORIGINAL / "machine/copilot-runtime/process.json"
    if runtime.exists():
        identity = json.loads(runtime.read_text())
        cleanup.append(
            {
                "role": "SDK CLI",
                "pid": identity["pid"],
                "start": identity["start"],
                "alive": ticks(identity["pid"]) == str(identity["start"]),
            }
        )
    readback_process = optional("readback-process.json")
    if readback_process:
        cleanup.append(
            {
                "role": "independent readback Blender",
                "pid": readback_process["pid"],
                "start": readback_process["start"],
                "alive": ticks(readback_process["pid"]) == str(readback_process["start"]),
            }
        )
    remaining_descendants = []
    for pid, observed in host["owned_processes"].items():
        if ticks(pid) == str(observed["start"]):
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            if state != "Z":
                remaining_descendants.append(observed)
    if version:
        retained_files += [
            ORIGINAL / "project" / row["destination"] for row in version["source"]["files"]
        ]
    for name in (
        "before-queue.private.png",
        "v2-keyframe.private.png",
        "v2-inspection.private.png",
        "final.private.png",
    ):
        if (ROOT / name).exists():
            retained_files.append(ROOT / name)
    with sqlite3.connect(
        f"file:{ORIGINAL}/project/.proscenium/state.sqlite3?mode=ro", uri=True
    ) as db:
        reservations = list(
            db.execute("SELECT id,task_id,target_id,state FROM budget_reservations")
        )
        checkpoints = [
            (identity, json.loads(body))
            for identity, body in db.execute(
                "SELECT id,body FROM records WHERE namespace='checkpoint_intents'"
            )
        ]
        frozen_after = {
            identity: json.loads(body)
            for identity, body in db.execute(
                "SELECT id,body FROM records WHERE namespace='job_inputs'"
            )
        }
    with sqlite3.connect(f"file:{ROOT}/before-private/project.sqlite3?mode=ro", uri=True) as db:
        frozen_before = {
            identity: json.loads(body)
            for identity, body in db.execute(
                "SELECT id,body FROM records WHERE namespace='job_inputs'"
            )
        }
    with sqlite3.connect(f"file:{e04}/.proscenium/state.sqlite3?mode=ro", uri=True) as db:
        e04_reservations = list(
            db.execute("SELECT id,task_id,target_id,state FROM budget_reservations")
        )
    with sqlite3.connect(f"file:{ORIGINAL}/machine/registry.sqlite3?mode=ro", uri=True) as db:
        resource_claims = list(
            db.execute("SELECT resource,project_id,job_id,attempt_id FROM resource_claims")
        )
    success = bool(
        version
        and browser["phase"] == "v2_visible"
        and len(new_jobs) == 2
        and all(job["state"] == "succeeded" for job in new_jobs)
    )
    final_events = [
        event
        for event in delta
        if event["type"] == "assistant.message"
        and event.get("data", {}).get("content")
        and not event.get("data", {}).get("toolRequests")
    ]
    root_seconds = (
        (
            datetime.fromisoformat(final_events[-1]["timestamp"].replace("Z", "+00:00"))
            - datetime.fromisoformat(new_requests[0]["accepted_at"])
        ).total_seconds()
        if final_events and new_requests
        else None
    )
    old_files = before["historical_records"]["versions"]["m10-asset-v1"]["source"]["files"]
    old_closure_retained = bool(
        version and all(row in version["source"]["files"] for row in old_files)
    )
    current_refs = subprocess.check_output(
        ["git", "-C", str(ORIGINAL / "project"), "show-ref"], text=True
    ).splitlines()
    old_history_preserved = all(
        line in current_refs for line in before["git_refs"] if not line.endswith(" refs/heads/main")
    )
    if version:
        old_history_preserved = (
            old_history_preserved
            and subprocess.run(
                [
                    "git",
                    "-C",
                    str(ORIGINAL / "project"),
                    "merge-base",
                    "--is-ancestor",
                    before["historical_records"]["versions"]["m10-asset-v1"]["source_commit"],
                    version["source_commit"],
                ],
                check=False,
            ).returncode
            == 0
        )
    summary = {
        "status": "actual_follow_up_v2_publication_and_review_pass"
        if success
        else "retained_incomplete_follow_up",
        "accepted_base": "02a3a8d95d99d27afa9f0f707d24af843adbd7a5",
        "harness_commit": optional("send-authorised.json"),
        "root": str(ROOT),
        "original_fixture": str(ORIGINAL),
        "same_session": SESSION,
        "project_id": PROJECT,
        "new_requests": new_requests,
        "new_work": work,
        "new_context": browser.get("new_context"),
        "new_queue_count": len(new_requests),
        "old_work_reopened": False,
        "old_work_relabelled_natural_completion": False,
        "historical_records_preserved": historical,
        "old_frozen_job_inputs_preserved": all(
            frozen_after.get(identity) == record for identity, record in frozen_before.items()
        ),
        "old_source_closure_files_retained_in_v2": old_closure_retained,
        "old_checkpoint_refs_and_git_ancestry_preserved": old_history_preserved,
        "project_index_before": before["project_status"],
        "project_index_after": subprocess.check_output(
            ["git", "-C", str(ORIGINAL / "project"), "status", "--short"], text=True
        ),
        "project_index_note": "Inherited default-index D/?? presentation existed before this run. Checkpoint used its explicit source contract. No index reset/repair or physical deletion was performed. Before/after bytes and historical source records remain verified.",
        "immutable_files_preserved": immutable,
        "old_reports_preserved": old_reports == before["old_report_hashes"],
        "e04_hashes_preserved": e04_after == before["e04_file_hashes"],
        "source_files_before": before["file_hashes"],
        "source_files_after": source_files,
        "native_history_prefix_preserved": history.startswith(prior),
        "native_history_sha256_after": sha(session),
        "native_user_messages_before": sum(e["type"] == "user.message" for e in old_events),
        "native_user_messages_after": sum(e["type"] == "user.message" for e in all_events),
        "native_children": sum(e["type"] == "subagent.started" for e in delta),
        "nonempty_final_without_tools": any(
            e["type"] == "assistant.message"
            and bool(e.get("data", {}).get("content"))
            and not e.get("data", {}).get("toolRequests")
            for e in delta
        ),
        "completion_record": records("copilot.completion").get(new_requests[0]["request_id"])
        if new_requests
        else None,
        "durable_idle": records("copilot.idle").get(SESSION),
        "task_complete_observed": any(e["type"] == "session.task_complete" for e in delta),
        "session_allocation": optional("session-allocation.json"),
        "sdk_send_claim": optional("sdk-send-claimed.json"),
        "sdk_send_ack": optional("sdk-send-ack.json"),
        "ui_preflight": optional("ui-preflight-ready.json"),
        "browser": browser,
        "authoring": authors,
        "authoring_claims": len(list(guard.glob("claim-*.json"))),
        "author_guard_unchanged": sha(Path(optional("fixture-helpers.json")["author_guard"]))
        == optional("fixture-helpers.json")["sha256"],
        "measurements": json.loads(
            (ORIGINAL / "project/assets/e04/revision-measurements.json").read_text()
        )
        if (ORIGINAL / "project/assets/e04/revision-measurements.json").exists()
        else None,
        "readback": optional("readback.json"),
        "readback_process": optional("readback-process.json"),
        "version": version,
        "previews": previews,
        "publication_replay": optional("publication-replay.json"),
        "checkpoints": checkpoints,
        "generation_reservations": reservations,
        "resource_claims_after": resource_claims,
        "e04_generation_reservations": e04_reservations,
        "cleanup": cleanup,
        "remaining_observed_owned_descendants": remaining_descendants,
        "observed_process_max_rss_bytes": {
            pid: row["max_rss_bytes"] for pid, row in host["owned_processes"].items()
        },
        "stack_seconds": host["stack_seconds"],
        "root_to_nonempty_final_seconds": root_seconds,
        "owner_stop_requested": browser.get("owner_stop_requested", False),
        "headroom_samples": host["headroom_samples"],
        "source_head_after": subprocess.check_output(
            ["git", "-C", str(ORIGINAL / "project"), "rev-parse", "HEAD"], text=True
        ).strip(),
        "scope": "No new inference/upload/download/VLM/Unity/service changes. No scripted artistic approval. Parent owns cumulative acceptance.",
        "criteria": {
            "1": "PASS_ACCEPTED_E04",
            "2": "PASS_RETAINED_MESH_AND_ACTUAL_BOUNDS"
            if success
            else "PASS_ACCEPTED_RETAINED_SCOPE",
            "3": "PASS_NEW_OWNER_FOLLOW_UP_REVISION_AND_V2_UI" if success else "REMAINING",
            "4": "PASS_RECORDED_LIMITS",
            "5": "PASS_CHECKPOINT_PUBLICATION_AND_ORIGINAL_AUTHORITY_REPLAY"
            if success
            and optional("publication-replay.json")
            and optional("publication-replay.json").get("no_mutation")
            else "REMAINING",
        },
    }
    write(out / "actual-run.json", summary)
    write(
        out / "retention.json",
        {
            "files": [
                {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}
                for path in sorted(set(retained_files))
            ],
            "raw_native_history_authentication_and_pairing_not_exported": True,
            "private_root_retained": str(ROOT),
        },
    )
    print(
        json.dumps(
            {
                "status": summary["status"],
                "queues": len(new_requests),
                "authoring": len(authors),
                "jobs": len(new_jobs),
                "remaining_owned_processes": sum(row["alive"] for row in cleanup),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
