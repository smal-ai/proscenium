"""Allowlisted export of actual partial M10 evidence. No service/model/native effects."""

import hashlib
import json
import shutil
import sqlite3
from collections import Counter
from importlib.metadata import version
from pathlib import Path

from m10_fixture import REPO, ROOT, sha, write
from proscenium.contracts import canonical
from proscenium.providers.mesh import inspect_glb


def main():
    out = REPO / "docs/evidence/m10"
    out.mkdir(parents=True, exist_ok=True)
    fixture = json.loads((ROOT / "fixture.json").read_text())
    native = json.loads((ROOT / "native-request-result.json").read_text())
    public = json.loads((ROOT / "public-blocked.json").read_text())
    cleanup = json.loads((ROOT / "cleanup.json").read_text())
    inspection = json.loads((ROOT / "m10-asset-v1-inspection.json").read_text())
    rejection = json.loads((ROOT / "context-rejection.json").read_text())
    assert (
        cleanup["server_closed"]
        and cleanup["browser_closed"]
        and not cleanup["remaining_observed_owned_processes"]
    )
    history = next((ROOT / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"))
    events = [json.loads(line) for line in history.read_text().splitlines()]
    starts = [event for event in events if event["type"] == "session.start"]
    users = [event for event in events if event["type"] == "user.message"]
    tools = [event for event in events if event["type"] == "tool.execution_start"]
    shell_permissions = [
        event
        for event in events
        if event["type"] == "permission.requested"
        and event["data"]["permissionRequest"].get("toolCallId") == "call_Yo8Pyi1HvNYCI5E6CSQxCamx"
    ]
    assert len(shell_permissions) == 2
    fingerprints = [
        hashlib.sha256(canonical(event["data"]["permissionRequest"])).hexdigest()
        for event in shell_permissions
    ]
    assert fingerprints[0] == fingerprints[1]
    completions = {
        event["data"]["requestId"]: event["data"]["result"]
        for event in events
        if event["type"] == "permission.completed"
    }
    permission_evidence = {
        "tool_call_id": "call_Yo8Pyi1HvNYCI5E6CSQxCamx",
        "canonical_payload_sha256": fingerprints[0],
        "requests": [
            {
                "native_event_id": event["id"],
                "request_id": event["data"]["requestId"],
                "prompt_kind": event["data"]["promptRequest"]["kind"],
                "permission_request": event["data"]["permissionRequest"],
                "completion": completions[event["data"]["requestId"]],
            }
            for event in shell_permissions
        ],
        "host_fence": "attribution_unavailable",
        "human_denial": False,
        "note": "The SDK denial label is a host rejection after correlation fencing, not an actual human decision.",
    }
    with sqlite3.connect(f"file:{ROOT}/project/.proscenium/state.sqlite3?mode=ro", uri=True) as db:

        def all_records(namespace):
            return {
                identity: json.loads(body)
                for identity, body in db.execute(
                    "SELECT id,body FROM records WHERE namespace=?", (namespace,)
                )
            }

        raw_versions = all_records("versions")
        links = all_records("version_links")
        artifacts = all_records("artifacts")
        reservations = list(
            db.execute("SELECT id,task_id,target_id,state FROM budget_reservations")
        )
        control = all_records("copilot.control")
    context_evidence = {
        **rejection,
        "raw_version": raw_versions["m10-asset-v1"],
        "version_links": links["m10-asset-v1"],
        "public_version": public["versions"]["m10-generated-asset"]["items"][0],
        "actual_w03_mapping": inspection["mapping"],
        "root_cause": "Contexts.validate_references checks selected IDs only in stored version.artifact_ids, omitting W03 version_links.",
    }
    write(out / "context-defect.json", context_evidence)
    write(out / "permission-defect.json", permission_evidence)
    write(out / "fixture.json", fixture)
    write(out / "inspection.json", inspection)
    write(out / "cleanup.json", cleanup)
    write(out / "public-blocked.json", public)
    redacted = []
    for event in events:
        kind, data = event["type"], event.get("data", {})
        item = {
            key: event[key]
            for key in ("id", "type", "parentId", "timestamp", "agentId")
            if key in event
        }
        if kind == "session.start":
            item["data"] = {
                key: data[key]
                for key in ("sessionId", "copilotVersion", "selectedModel", "startTime")
                if key in data
            }
        elif kind == "user.message":
            item["data"] = {key: data[key] for key in ("messageId", "clientTurnId") if key in data}
            item["extension_context"] = [
                a.get("payload")
                for a in data.get("attachments", [])
                if a.get("type") == "extension_context"
            ]
        elif kind == "tool.execution_start":
            item["data"] = {
                key: data[key] for key in ("toolCallId", "toolName", "model") if key in data
            }
            item["arguments_sha256"] = hashlib.sha256(
                canonical(data.get("arguments", {}))
            ).hexdigest()
        elif kind == "tool.execution_complete":
            item["data"] = {key: data[key] for key in ("toolCallId", "success") if key in data}
        elif kind == "session.shutdown":
            item["shutdown_type"] = data.get("shutdownType")
        elif kind.startswith("subagent."):
            item["data"] = {
                key: data[key] for key in ("agentId", "agentName", "model") if key in data
            }
        else:
            continue
        redacted.append(item)
    write(out / "native-events-redacted.json", {"events": redacted})
    proxy_root = Path(fixture["job_root"])
    profile = json.loads((proxy_root / "native-profile.json").read_text())
    proxy_processes = []
    for name in ("process.json", "native-process.json"):
        record = json.loads((proxy_root / name).read_text())
        current = Path(f"/proc/{record['pid']}/stat")
        same_live_process = False
        if current.exists():
            fields = current.read_text().rsplit(")", 1)[1].split()
            same_live_process = fields[19] == str(record["start_ticks"]) and fields[0] != "Z"
        proxy_processes.append(
            {
                "receipt": name,
                "pid": record["pid"],
                "start_ticks": record["start_ticks"],
                "same_live_process_remaining": same_live_process,
            }
        )
    assert not any(row["same_live_process_remaining"] for row in proxy_processes)
    model_script = ROOT / "project/assets/e04/revise_asset.py"
    summary = {
        "base": "b1fa32c8a73d5aca8243fa82e65837a8acaba75c",
        "fixture_root": str(ROOT),
        "overall": "BLOCKED",
        "test_user_input": True,
        "human_creative_selection_inferred": False,
        "criteria": {
            "1": {
                "status": "PASS",
                "evidence": "Accepted E04 80ff7bf actual Hunyuan mesh, imported byte-for-byte; no new generation.",
            },
            "2": {
                "status": "PASS",
                "evidence": "Accepted E04 Blender import/bounds/material inventory plus actual M10 W03 proxy and stable-ID triangle picking.",
            },
            "3": {
                "status": "REMAINING",
                "evidence": "Full-HD real proxy inspection passed. Picked-object Queue rejected. Version-only Queue reached real SDK but permission correlation fenced before authoring.",
            },
            "4": {
                "status": "PASS",
                "evidence": "Accepted E04 sampled generation/import resources retained. Current bounds and unchanged geometry limits disclosed; no topology/rigging claim.",
            },
            "5": {
                "status": "REMAINING",
                "evidence": "Original E04 history/count/hash preservation and zero new inference proved. Actual native revision checkpoint/publication/replay/retry were not reached.",
            },
        },
        "accepted_dependencies": {
            "E04": "80ff7bf",
            "M01": "milestone/m01",
            "M04": "not finally accepted",
            "M05": "not finally accepted",
        },
        "fixture_checkpoint": fixture["checkpoint"],
        "fixture_publication": fixture["publication"],
        "project": fixture["project"],
        "proxy_job": fixture["job"],
        "proxy_profile": profile,
        "proxy_geometry": inspect_glb(proxy_root / "proxy.glb"),
        "proxy_artifacts": [artifacts[identity] for identity in fixture["job"]["artifact_ids"]],
        "proxy_process_settlement": proxy_processes,
        "viewport": inspection["viewport"],
        "browser": public["browser"],
        "software_renderer": inspection["renderer"],
        "orbit_pixels_changed": inspection["orbit_before_sha256"]
        != inspection["orbit_after_sha256"],
        "stable_pick": inspection["selected_title"],
        "native_request": native["request"],
        "native_association": native["native_association"],
        "actual_model": starts[0]["data"]["selectedModel"],
        "sdk_version": version("github-copilot-sdk"),
        "cloud_roots": len(users),
        "root_elapsed_seconds": native["root_elapsed_seconds"],
        "observed_host_memory": {
            "proxy_samples": len(fixture["memory_observations"]),
            "root_samples": len(native["samples"]),
            "minimum_available_bytes": min(
                row["mem_available_bytes"]
                for row in fixture["memory_observations"] + native["samples"]
            ),
            "protected_headroom_bytes": 4 * 1024**3,
            "stop_threshold_bytes": 5 * 1024**3,
            "exact_process_or_allocator_peak": False,
        },
        "native_children": sum(event["type"] == "subagent.started" for event in events),
        "tool_counts": dict(Counter(event["data"]["toolName"] for event in tools)),
        "model_authored_script": {
            "path": "assets/e04/revise_asset.py",
            "bytes": model_script.stat().st_size,
            "sha256": sha(model_script),
            "executed": False,
        },
        "native_authoring_processes": 0,
        "native_readbacks": 0,
        "proxy_exports": 1,
        "cpu_stills": 0,
        "new_generation_reservations": reservations,
        "original_e04_consumed_count": len(cleanup["original_e04_after"]["reservations"]),
        "original_e04_preserved": cleanup["original_e04_preserved"],
        "independent_services_mutated": False,
        "gate": control,
        "remaining_owned_processes": cleanup["remaining_observed_owned_processes"],
        "defects": [
            "W01 selected-object validation ignores later W03 proxy-map associations",
            "W01 duplicate identical permission payload with distinct native request IDs collides and fences the root",
        ],
        "harness_deviations": [
            "Initial Chromium start failed before interaction because disk-root TMPDIR exceeded Unix socket path length; only the exited browser was replaced with a short socket TMPDIR.",
            "Initial setup driver assumed activation persisted across host startup and the menu remained open. Corrected through actual UI activation before the cloud request.",
            "First selected-object context was definitively unadmitted (HTTP 409). After preserving a repeatable rejection and zero admitted roots, actual UI Control-click deselected it. A new version-pinned context admitted exactly one root.",
            "The initial no-admission watcher waited unnecessarily and attempted a disabled Stop. It sent no model or Stop request and is retained as harness failure history.",
        ],
        "resume_rule": "Preserve request 927cf238-56c2-454e-a063-014e072f9010, work work-bc19a5da5fcd4aaf9ae552a13d06b20d and native session 5971843f-f216-40f8-b8cb-995675f48161. Reconcile only after parent-approved adapter corrections; do not send a replacement or execute the script manually.",
    }
    write(out / "summary.json", summary)
    for name in (
        "m10-asset-v1-fullhd-inspection.png",
        "m10-selected-object-context-rejected.png",
    ):
        shutil.copyfile(ROOT / name, out / name)
    # Keep post-model conversation screenshots private alongside the raw native history.
    private_chat_copy = out / "m10-blocked-fullhd.png"
    if private_chat_copy.exists():
        private_chat_copy.unlink()
    retained = [
        ROOT / "fixture.json",
        ROOT / "native-request-result.json",
        history,
        ROOT / "m10-blocked-fullhd.png",
        ROOT / "context-rejection.json",
        ROOT / "cleanup.json",
        model_script,
        proxy_root / "proxy.glb",
        proxy_root / "proxy-map.json",
        proxy_root / "native-profile.json",
    ]
    write(
        out / "retained-files.json",
        {
            "files": [
                {"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p)} for p in retained
            ],
            "raw_native_history_exported": False,
            "credentials_or_pairing_exported": False,
        },
    )
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "overall",
                    "cloud_roots",
                    "native_children",
                    "native_authoring_processes",
                    "proxy_exports",
                    "cpu_stills",
                    "original_e04_preserved",
                    "remaining_owned_processes",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
