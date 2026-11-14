"""Read-only export of the single real publication continuation and exact blocker."""

import json
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from proscenium.contracts import canonical

if TYPE_CHECKING:
    from tests.native.unity.m11_publish_snapshot import REQUEST_ID, SESSION_ID, WORK_ID, records
else:
    from m11_publish_snapshot import REQUEST_ID, SESSION_ID, WORK_ID, records

REPO = Path(__file__).resolve().parents[3]


def alive(record):
    try:
        value = Path(f"/proc/{record['pid']}/stat").read_text().rsplit(")", 1)[1].split()
        return value[19] == str(record["start"]) and value[0] != "Z"
    except FileNotFoundError:
        return False


def main(control):
    original = Path(json.loads((control / "processes.json").read_text())["original"])
    project = original / "project"
    out = REPO / "docs/evidence/m11-publication"
    out.mkdir(parents=True, exist_ok=True)
    before = json.loads((control / "before.json").read_text())
    after = json.loads((control / "after.json").read_text())
    events = [
        json.loads(line) for line in (control / "events.private.jsonl").read_text().splitlines()
    ]
    permission = next(
        e["data"]["permissionRequest"]
        for e in reversed(events)
        if e["type"] == "permission.requested"
    )
    pattern = r"(?i)(\b(push|purchase|install|upgrade|sudo|scp)\b|reset\s+--hard|rm\s+-[rf]|curl\s+.*(?:--upload-file|--data|-[dT]|-X\s*(?:POST|PUT|DELETE)))"
    text = canonical(permission).decode()
    match = re.search(pattern, text)
    assert match and match.group() == "install"
    assert before["source_files"] == after["source_files"]
    assert before["old_reports"] == after["old_reports"]
    assert before["jobs"] == after["jobs"]
    assert before["git_index"] == after["git_index"] and before["head"] == after["head"]
    assert before["refs"] == after["refs"]
    assert {r["request_id"] for r in before["requests"]} == {
        r["request_id"] for r in after["requests"]
    }
    before_children = {r["native_id"] for r in before["invocations"] if r.get("kind") == "child"}
    after_children = {r["native_id"] for r in after["invocations"] if r.get("kind") == "child"}
    assert before_children == after_children and len(after_children) == 2
    process = json.loads((control / "processes.json").read_text())
    native = json.loads((original / "machine/copilot-runtime/process.json").read_text())
    assert not alive(process["host"]) and not alive(process["browser"]) and not alive(native)
    send = json.loads((control / "continuation-send.json").read_text())
    assert send["session_id"] == SESSION_ID

    def write(name, data):
        (out / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    database = project / ".proscenium/state.sqlite3"
    decisions = records(database, "decisions")
    decision = next(d for d in decisions if d["kind"] == "permission" and d["work_id"] == WORK_ID)
    assert (
        decision["state"] == "pending"
        and decision["response"] is None
        and decision["resolved_by"] is None
    )
    write(
        "permission-blocker.json",
        dict(
            request=permission,
            decision=decision,
            matched_text=match.group(),
            matched_context=text[match.start() - 80 : match.end() + 100],
            production_location="copilot/service.py: permission() applies consequential regex to canonical(request), including path data",
        ),
    )
    write(
        "preservation.json",
        dict(
            source_files=before["source_files"],
            source_files_identical=True,
            old_reports=before["old_reports"],
            old_reports_identical=True,
            jobs_before=before["jobs"],
            jobs_after=after["jobs"],
            git_index_before=before["git_index"],
            git_index_after=after["git_index"],
            head_before=before["head"],
            head_after=after["head"],
            refs_before=before["refs"],
            refs_after=after["refs"],
        ),
    )
    write(
        "runtime-state.json",
        {
            ns: records(database, ns)
            for ns in (
                "requests",
                "work",
                "invocations",
                "copilot.resumes",
                "copilot.continuation_sends",
                "copilot.runtime_errors",
                "copilot.recovery_evidence",
                "versions",
                "items",
                "checkpoint_intents",
            )
        },
    )
    safe = []
    for event in events:
        if event.get("ephemeral"):
            continue
        data = event.get("data", {})
        safe.append(
            {k: event[k] for k in ("id", "type", "timestamp", "agentId", "parentId") if k in event}
            | {
                "data": {
                    k: data[k]
                    for k in (
                        "toolCallId",
                        "toolName",
                        "arguments",
                        "success",
                        "content",
                        "model",
                        "modelId",
                        "messageId",
                        "originatingMessageId",
                        "parentToolCallId",
                        "hookType",
                    )
                    if k in data
                }
            }
        )
    write("events-redacted.json", safe)
    for name in (
        "owner-reconcile.json",
        "owner-continue.json",
        "continue-result.json",
        "continuation-send.json",
        "public-before-continue.json",
        "public-progress.json",
        "public-reconcile.json",
        "cleanup.json",
    ):
        if (control / name).is_file():
            shutil.copyfile(control / name, out / name)
    for name in ("browser-pair.png", "browser-reconcile.png", "continue-failure.png"):
        if (control / name).is_file():
            shutil.copyfile(control / name, out / name)
    snapshot_info = dict(
        before_private_path=str(control / "private-before"),
        before_copy_hashes=before["copies"],
        after_private_path=str(control / "private-after"),
        after_copy_hashes=after["copies"],
    )
    write("private-snapshot-identities.json", snapshot_info)
    write(
        "process-identities.json",
        dict(
            host=process["host"] | {"original_alive": False},
            browser=process["browser"] | {"original_alive": False},
            sdk=native | {"original_alive": False},
        ),
    )
    write(
        "manifest.json",
        dict(
            status="blocked_permission_false_positive",
            application_base="3cdefb240cc241560a948a3b41767766bd78875a",
            control_root=str(control),
            original_project=str(project),
            request_id=REQUEST_ID,
            work_id=WORK_ID,
            session_id=SESSION_ID,
            sdk_continuation_sends=1,
            new_queue_requests=0,
            new_product_children=0,
            new_unity=0,
            new_blender=0,
            new_renderers=0,
            scene_meta_all_source_bytes_unchanged=True,
            old_reports_unchanged=True,
            source_git_refs_unchanged=True,
            unity_publication=False,
            checkpoint_effect=False,
            decision_id=decision["decision_id"],
            blocking_tool_call=permission["toolCallId"],
            matched_path_data="tool-install.json",
            private_snapshots=snapshot_info,
            cleanup_complete=True,
        ),
    )
    print(json.dumps(json.loads((out / "manifest.json").read_text()), indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
