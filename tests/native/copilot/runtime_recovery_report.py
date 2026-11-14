"""Read retained evidence and exact M13 candidate objects. No dispatch or model calls."""

import hashlib
import json
import shutil
import subprocess

from PIL import Image
from proscenium.copilot.launcher import identity
from runtime_recovery_history import OUT, sha
from runtime_recovery_normal import REPO, ROOT

M13 = "209448a9d10287b777b005cd8e03e888d5e15cc8"


def load(path):
    return json.loads(path.read_text())


def export():
    destination = REPO / "docs/evidence/runtime-recovery"
    destination.mkdir(parents=True, exist_ok=True)
    blob = subprocess.check_output(
        ["git", "show", f"{M13}:docs/evidence/m13/result.json"], cwd=REPO
    )
    m13 = json.loads(blob)
    original = m13["requests"][0]
    continuation = m13["continuations"][0]
    invocation = next(
        i for i in m13["workflow_invocations"] if i["delivery_id"] == continuation["operation_id"]
    )
    assert (
        invocation["work_id"] == original["receipt"]["work_id"]
        and invocation["request_id"] == original["request_id"]
    )
    assert (
        continuation["session_id"]
        == invocation["session_id"]
        == m13["native_association"]["session_id"]
    )
    assert original["receipt"]["state"] == "completed" and continuation["state"] == "acknowledged"
    changes = subprocess.check_output(
        ["git", "diff", "--name-only", "e3cddf8", M13, "--", "apps/backend/src/proscenium/copilot"],
        text=True,
        cwd=REPO,
    )
    assert not changes
    dependency = dict(
        commit=M13,
        source="docs/evidence/m13/result.json",
        source_sha256=hashlib.sha256(blob).hexdigest(),
        base_revision=m13["base_revision"],
        viewport=m13["viewport"],
        browser=m13["browser"],
        native_association=m13["native_association"],
        continuation=continuation,
        original_request={k: original[k] for k in ["request_id", "receipt", "generation_task_id"]},
        continued_invocation=invocation,
        publication={
            k: m13["publications"][0][k]
            for k in ["invocation_id", "native_session_id", "receipt", "request_id", "work_id"]
        },
        source_commit=m13["edits"][0]["version"]["source_commit"],
        product_copilot_changed_in_candidate=False,
        dispatches=m13["cloud_dispatches"],
        boundary="Inspected candidate evidence. Parent acceptance remains authoritative. No new M13 execution.",
    )
    (destination / "m13-continuation.json").write_text(json.dumps(dependency, indent=2) + "\n")
    history = {}
    for label, relative in [
        ("m03-a-first", "m03-a"),
        ("m03-a", "m03-a/binding-recovery"),
        ("m03-b", "m03-b"),
        ("m11", "m11"),
    ]:
        run = OUT / relative
        data = load(run / "result.json")
        before = load(run / "before.private.json")
        after = load(run / "after.private.json")
        data["source_and_job_records_unchanged"] = all(
            before["records"].get(ns) == after["records"].get(ns)
            for ns in ["versions", "items", "jobs", "attempts", "publications", "checkpoints"]
        )
        assert data["source_and_job_records_unchanged"]
        hashes = load(run / "before-hashes.private.json")
        assert all(sha(run / "before" / name) == digest for name, digest in hashes.items())
        process = data["metadata_process"]
        process["parent_same_identity_alive"] = (
            identity(process["parent_pid"]) == process["parent_start"]
        )
        assert not process["same_identity_alive"] and not process["parent_same_identity_alive"]
        data["decisions_after"] = [
            {
                k: d[k]
                for k in [
                    "decision_id",
                    "native_decision_id",
                    "invocation_id",
                    "work_id",
                    "state",
                    "state_revision",
                    "response",
                    "resolved_by",
                    "delivery_state",
                    "fingerprint",
                ]
            }
            for d in data["decisions_after"]
        ]
        history[label] = data
    (destination / "historical-reconciliation.json").write_text(
        json.dumps(history, indent=2) + "\n"
    )
    normal = load(ROOT / "ui-failed.json")
    data = load(ROOT / "export.private.json")
    native = []
    for path in (ROOT / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"):
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event["type"] in {
                "abort",
                "tool.execution_start",
                "tool.execution_complete",
                "assistant.turn_end",
            }:
                native.append(
                    {
                        **{
                            k: event[k]
                            for k in ["id", "type", "timestamp", "parentId"]
                            if k in event
                        },
                        "data": {
                            k: v
                            for k, v in event.get("data", {}).items()
                            if k in {"reason", "toolCallId", "toolName", "success"}
                        },
                    }
                )
    helper = [
        json.loads(line)
        for line in (ROOT / "project/.proscenium/tool-output/normal-helper.jsonl")
        .read_text()
        .splitlines()
    ]
    sends = [json.loads(line) for line in (ROOT / "sends.jsonl").read_text().splitlines()]
    clean = load(ROOT / "cleanup.json")
    normal_result = dict(
        application_base="0b8e9ba",
        status="BLOCKED: native Bash terminal event absent; Continue not sent",
        viewport=normal["viewport"],
        browser=normal["browser"],
        original_work=normal["original_work"],
        queue=normal["queue"],
        stop=normal["stop"],
        work_after=data["records"]["work"],
        invocations=data["records"]["invocations"],
        stop_record=data["records"]["copilot.stops"],
        native_evidence=native,
        helper_observations=helper,
        sends=sends,
        continuation_sends=0,
        children=0,
        cleanup=clean,
    )
    assert len(sends) == 1 and not data["records"].get("copilot.continuation_sends")
    assert not any(e["type"] == "tool.execution_complete" for e in native)
    assert not any(h["event"] == "natural-completion" for h in helper)
    assert all(not clean[k]["same_identity_alive"] for k in ["server", "browser", "native"])
    assert all(not h["same_identity_alive"] for h in clean["helpers"])
    (destination / "normal-probe.json").write_text(json.dumps(normal_result, indent=2) + "\n")
    for original, target in [
        ("01-active-helper.png", "normal-active-helper.png"),
        ("FAILED.png", "normal-unconfirmed-stop.png"),
    ]:
        shutil.copyfile(ROOT / original, destination / target)
        with Image.open(destination / target) as image:
            assert image.size == (1920, 1080)
    (destination / "manifest.json").write_text(
        json.dumps(
            {
                p.name: sha(p)
                for p in destination.iterdir()
                if p.is_file() and p.name != "manifest.json"
            },
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            dict(
                m13_continuation=continuation["operation_id"],
                historical_sends=0,
                normal_sends=len(sends),
                m11_live=history["m11"]["reconciliation"]["live_invocations"],
                m03_bash_live=history["m03-b"]["reconciliation"]["live_invocations"],
            )
        )
    )


if __name__ == "__main__":
    export()
