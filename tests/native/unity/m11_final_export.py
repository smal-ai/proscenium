"""Read-only export of the actual final attempt. Never resumes or authors a result."""

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from proscenium.contracts import canonical, validate

from tools.unity.jobs import attempt_directory

if TYPE_CHECKING:
    from tests.native.unity.m11_publish_export import alive
    from tests.native.unity.m11_publish_snapshot import (
        REQUEST_ID,
        SESSION_ID,
        WORK_ID,
        records,
        sha,
    )
else:
    from m11_publish_export import alive
    from m11_publish_snapshot import REQUEST_ID, SESSION_ID, WORK_ID, records, sha

REPO = Path(__file__).resolve().parents[3]


def keyed(database, namespace):
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        return dict(db.execute("SELECT id,body FROM records WHERE namespace=?", (namespace,)))


def children(rows):
    return {i["native_id"] for i in rows if i.get("kind") == "child"}


def main(control):
    original, project = control.parent, control.parent / "project"
    before = json.loads((control / "before.json").read_text())
    after = json.loads((control / "after.json").read_text())
    before_db, after_db = (
        control / f"private-{label}/project.sqlite3" for label in ("before", "after")
    )
    events = [
        json.loads(line) for line in (control / "events.private.jsonl").read_text().splitlines()
    ]
    out = REPO / "docs/evidence/m11-final"
    out.mkdir(parents=True, exist_ok=True)

    def write(name, value):
        (out / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")

    for key in ("source_files", "old_reports", "jobs", "versions", "git_index"):
        assert before[key] == after[key], key
    namespaces = (
        "artifacts",
        "attempts",
        "contexts",
        "job_inputs",
        "job_evidence",
        "native_workflows",
        "versions",
        "items",
        "publications",
        "copilot.sends",
        "copilot.payloads",
    )
    assert all(keyed(before_db, n) == keyed(after_db, n) for n in namespaces)
    for version in before["versions"]:
        for source_file in version["source"]["files"]:
            assert sha(project / source_file["destination"]) == source_file["origin"]["sha256"]
    bindings_before, bindings_after = (
        keyed(db, "copilot.bindings") for db in (before_db, after_db)
    )
    assert all(bindings_after[k] == value for k, value in bindings_before.items())
    assert {r["request_id"] for r in before["requests"]} == {
        r["request_id"] for r in after["requests"]
    }
    assert children(before["invocations"]) == children(after["invocations"])
    intent = next(
        i
        for i in records(after_db, "checkpoint_intents")
        if i.get("receipt", {}).get("operation_id") == "m11-unity-source-checkpoint"
    )
    receipt = validate("CheckpointReceipt", intent["receipt"])
    assert receipt["state"] == "completed" and receipt["replayed"] is False
    commit = receipt["source_commit"]
    assert after["head"] == commit and receipt["parent_commit"] == before["head"]
    for row in receipt["files"]:
        assert before["source_files"][row["path"]] == row["sha256"]
        blob = subprocess.check_output(["git", "show", f"{commit}:{row['path']}"], cwd=project)
        assert hashlib.sha256(blob).hexdigest() == row["sha256"]
    assert {r["path"] for r in receipt["files"]} == set(before["source_files"])
    changed = subprocess.check_output(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit], cwd=project, text=True
    ).splitlines()
    assert set(changed) == set(before["source_files"])
    event = next(
        e
        for e in events
        if e["type"] == "tool.execution_complete"
        and e.get("data", {}).get("toolCallId") == "call_0KgFTlXlYrinFSJ3axs84711"
    )
    assert event["data"]["success"] is True
    assert json.loads(event["data"]["result"]["content"]) == receipt
    invocation = next(
        i for i in after["invocations"] if i.get("native_id") == "call_0KgFTlXlYrinFSJ3axs84711"
    )
    assert invocation["state"] == "completed" and invocation["work_id"] == WORK_ID
    write(
        "checkpoint.json",
        dict(
            receipt=receipt,
            invocation=invocation,
            native_completion_event_id=event["id"],
            verified_git_blobs=len(receipt["files"]),
        ),
    )
    pending = next(d for d in records(after_db, "decisions") if d["state"] == "pending")
    assert (
        pending["response"] is None
        and pending["resolved_by"] is None
        and pending["delivery_state"] == "not_sent"
    )
    request = json.loads(pending["prompt"])
    assert request["kind"] == "read" and request["path"].endswith("/tool-install.json")
    match = re.search(r"\binstall\b", canonical(request).decode())
    assert match
    assert all(
        keyed(after_db, "decisions")[k] == v for k, v in keyed(before_db, "decisions").items()
    )
    write(
        "permission-blocker.json",
        dict(
            decision=pending,
            request=request,
            matched_word=match.group(),
            production_location="copilot/service.py:1597-1640",
            owner_response_supplied=False,
        ),
    )
    send = next(
        s
        for s in records(after_db, "copilot.continuation_sends")
        if s["operation_id"] == "d962dbe1-279e-44f0-9afc-dae52d4a9e41"
    )
    assert send["state"] == "acknowledged"
    assert (
        len(records(after_db, "copilot.continuation_sends"))
        == len(records(before_db, "copilot.continuation_sends")) + 1
    )
    write(
        "continuation.json",
        dict(
            send=send,
            continuation=next(
                c
                for c in records(after_db, "copilot.continuations")
                if c["delivery_id"] == send["operation_id"]
            ),
            work=next(w for w in after["work"] if w["work_id"] == WORK_ID),
            live_invocations=[
                i
                for i in after["invocations"]
                if i["state"] not in {"completed", "failed", "cancelled", "stopped"}
            ],
            owner_resumes=[
                row
                for row in records(after_db, "copilot.resumes")
                if row["body"]["operation_id"]
                in {
                    json.loads((control / "owner-reconcile.json").read_text())["body"][
                        "operation_id"
                    ],
                    send["operation_id"],
                }
            ],
        ),
    )
    process = json.loads((control / "processes.json").read_text())
    native = json.loads((original / "machine/copilot-runtime/process.json").read_text())
    assert not any(alive(p) for p in (process["host"], process["browser"], native))
    cleanup = json.loads((control / "cleanup.json").read_text())
    assert cleanup == dict(browser_reaped=True, services_closed=True, sdk_sends=1)
    elapsed = (control / "cleanup.json").stat().st_mtime - (
        control / "processes.json"
    ).stat().st_mtime
    assert elapsed < 300, elapsed
    write(
        "cleanup.json",
        cleanup
        | dict(
            host=process["host"],
            browser=process["browser"],
            sdk=native,
            recorded_identities_gone=True,
            stack_seconds=elapsed,
        ),
    )
    write(
        "preservation.json",
        dict(
            source_files=before["source_files"],
            source_files_identical=True,
            old_reports=before["old_reports"],
            old_reports_identical=True,
            jobs_unchanged=True,
            original_versions_unchanged=True,
            original_bindings_unchanged=True,
            original_binding_count=len(bindings_before),
            unchanged_namespaces=list(namespaces),
            git_index_unchanged=True,
            head_before=before["head"],
            head_after=after["head"],
            refs_before=before["refs"],
            refs_after=after["refs"],
            old_decisions_unchanged=True,
            new_children=0,
            new_requests=0,
        ),
    )
    import_job = next(j for j in after["jobs"] if j["job_id"] == "m11-import-job")
    generic_path = attempt_directory(project, import_job["attempt_id"]) / "unity-result.json"
    generic = json.loads(generic_path.read_text())
    attestation = json.loads((project / "unity/M11/M11-operator-playmode.json").read_text())
    assert generic["validation"]["status"] == "not_run"
    assert attestation["unity"]["validation"]["status"] == "passed"
    write(
        "validation-separation.json",
        dict(
            generic=generic,
            generic_path=str(generic_path),
            generic_sha256=sha(generic_path),
            operator_attestation=attestation,
            note="The retained generated source unity-result.json reflects the later operator PlayMode fixture. The generic import's immutable attempt result remains not_run.",
        ),
    )
    write(
        "private-snapshots.json",
        {
            label: dict(
                path=str(control / f"private-{label}"),
                copies=json.loads((control / f"{label}.json").read_text())["copies"],
            )
            for label in ("before", "blocked", "after")
        },
    )
    write(
        "runtime-identities.json",
        dict(
            sdk=json.loads((control / "preflight.json").read_text())["sdk"],
            cli_version="1.0.112",
            cli_sha256=sha(Path(native["binary"])),
            model=invocation["model_id"],
            compiled_frontend={
                p.relative_to(control / "compiled-frontend").as_posix(): sha(p)
                for p in (control / "compiled-frontend").rglob("*")
                if p.is_file()
            },
        ),
    )
    for name in (
        "owner-reconcile.json",
        "owner-continue.json",
        "public-preflight.json",
        "public-before-continue.json",
        "public-progress.json",
        "continue-error.json",
        "send-headroom.json",
        "browser-preflight.png",
        "continue-failure.png",
    ):
        shutil.copyfile(control / name, out / name)
    write(
        "manifest.json",
        dict(
            status="blocked_read_permission_after_checkpoint",
            application_base="904f73383d6e9a3e0a5daa411f3628efd90bc2cf",
            proof_driver_commit="ed3232c",
            control_root=str(control),
            request_id=REQUEST_ID,
            work_id=WORK_ID,
            session_id=SESSION_ID,
            source_commit=commit,
            checkpoint_operation="m11-unity-source-checkpoint",
            checkpoint_effect=True,
            unity_publication=False,
            publication_id=None,
            version_id=None,
            requested_publication_id="m11-unity-publication",
            requested_version_id="m11-playable-v1",
            sdk_sends=1,
            new_queue_requests=0,
            new_children=0,
            new_unity=0,
            new_blender=0,
            new_exports=0,
            new_provider_work=0,
            publication_replay="not_run_no_publication",
            checkpoint_replay="not_run_attempt_stopped_at_new_permission",
            criterion_6="partial_checkpoint_complete_publication_blocked",
            cleanup_complete=True,
        ),
    )
    write(
        "files.json",
        {
            p.name: sha(p)
            for p in sorted(out.iterdir())
            if p.is_file() and p.name not in {"files.json", "README.md"}
        },
    )
    print(json.dumps(json.loads((out / "manifest.json").read_text()), indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
