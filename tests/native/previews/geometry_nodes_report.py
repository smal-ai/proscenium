"""Retain metadata for the blocked single-root proof without executing native work."""

import argparse
import json
from pathlib import Path

from proscenium.previews.journal import read, sha
from proscenium.sources.common import git


def main(root: Path, export: bool):
    result = read(root / "native-result.json")
    state = read(root / "public-latest.json")
    project = root / "project"
    history = next((root / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"))
    events = [json.loads(line) for line in history.read_text().splitlines()]
    started = next(event for event in events if event["type"] == "session.start")
    denied = [
        event
        for event in events
        if event["type"] == "permission.completed"
        and event["data"]["result"]["kind"].startswith("denied")
    ]
    assert len(denied) == 1
    native_id = denied[0]["data"]["requestId"]
    requested = next(
        event
        for event in events
        if event["type"] == "permission.requested" and event["data"]["requestId"] == native_id
    )
    tool_id = denied[0]["data"]["toolCallId"]
    call = next(
        event
        for event in events
        if event["type"] == "tool.execution_start" and event["data"]["toolCallId"] == tool_id
    )
    failed = next(
        event
        for event in events
        if event["type"] == "tool.execution_complete" and event["data"]["toolCallId"] == tool_id
    )
    assert not failed["data"]["success"]
    assert len(state["requests"]) == 1 and not state["decisions"]
    assert not any(
        state[name]
        for name in ("versions", "jobs", "frames", "segments", "artifacts", "publications")
    )
    assert not (root / "authoring-reservation.json").exists()
    assert not (project / "assets/procedural/asset.blend").exists()
    assert git(project, "rev-parse", "HEAD").decode().strip() == result["fixture_head"]
    assert git(project, "status", "--porcelain") == b""
    assert not any(event["type"].startswith("subagent.") for event in events)
    users = [
        event
        for event in events
        if event["type"] == "user.message" and not event["data"].get("isAutopilotContinuation")
    ]
    assert len(users) == 1
    report = {
        "status": "blocked_no_native_compatibility_claim",
        "implementation_commit": result["implementation_commit"],
        "retained_root": str(root),
        "fixture_head": result["fixture_head"],
        "session_id": started["data"]["sessionId"],
        "selected_model": started["data"]["selectedModel"],
        "sdk_version": "1.0.34",
        "cli_version": started["data"]["copilotVersion"],
        "request_id": state["requests"][0]["request_id"],
        "request_state": state["requests"][0]["receipt"]["state"],
        "native_permission_id": native_id,
        "tool_call_id": tool_id,
        "permission_request": requested["data"]["permissionRequest"],
        "permission_result": denied[0]["data"]["result"],
        "tool": {key: call["data"][key] for key in ("toolName", "arguments")},
        "tool_error": failed["data"]["error"],
        "permission_completed_at": denied[0]["timestamp"],
        "owner_decision_count": 0,
        "allocation": {
            "roots": 1,
            "children": 0,
            "authoring_processes": 0,
            "proxy_exports": 0,
            "cpu_stills": 0,
        },
        "initial_headroom_bytes": result["initial_headroom_bytes"],
        "cleanup": result["cleanup"],
        "source_worktree_unchanged": True,
        "harness_defect": "Only pending owner decisions were monitored. The automatic native rejection and terminal root with no publication were missed until the 180-second polling deadline. Driver now fails immediately on failed invocations or completed root without publication. No new root executed.",
        "remaining_requirement": "Actual model-authored editable graph, checkpoint/publication, production GLB triangle mapping, evaluated bounds, CPU still and source preservation remain unproven.",
        "coordinator_question": "May one replacement root be allocated after this protected-runtime read refusal, with no permission relaxation and all original Blender/proxy/still limits unchanged? No restart or second root has been attempted.",
        "retained_sha256": {
            name: sha((root / name).read_bytes())
            for name in ("root-reservation.json", "public-latest.json", "native-result.json")
        },
        "native_history_sha256": sha(history.read_bytes()),
    }
    if export:
        target = (
            Path(__file__).resolve().parents[3]
            / "docs/evidence/geometry-nodes/blocked-initial.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    main(args.root.resolve(strict=True), args.export)
