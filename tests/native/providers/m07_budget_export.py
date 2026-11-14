"""Export retained admission-only evidence. Never start a runtime or contact a provider."""

import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = Path.home() / ".cache/proscenium-development/milestones/m07-native-budget-20261114"
OUT = REPO / "docs/evidence/m07-native-budget"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name):
    return json.loads((ROOT / name).read_bytes())


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + "\n")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name in (
        "report.json",
        "fixture.json",
        "owner-queue.json",
        "resources.json",
        "cleanup-observation.json",
    ):
        shutil.copyfile(ROOT / name, OUT / name)
    # Full private snapshots remain untouched. Publish no token-stream/model internals.
    for name in ("settled.json", "post-cleanup.json"):
        value = read(name)
        value["private_record_counts"] = {key: len(rows) for key, rows in value["records"].items()}
        value["export_omitted_namespaces"] = ["copilot.native", "copilot.bindings"]
        value["records"] = {
            key: rows
            for key, rows in value["records"].items()
            if key not in value["export_omitted_namespaces"]
        }
        write(name, value)
    event_types = {
        "session.start",
        "session.idle",
        "session.error",
        "user.message",
        "assistant.message",
        "assistant.turn_start",
        "assistant.turn_end",
        "assistant.usage",
        "tool.execution_start",
        "tool.execution_complete",
        "subagent.started",
        "subagent.completed",
        "subagent.failed",
        "subagent.configured",
        "subagent.selected",
        "permission.requested",
        "permission.completed",
        "external_tool.requested",
        "external_tool.completed",
    }
    for name in ("sdk-send", "native-events", "prompt-hooks", "tool-results"):
        rows = [json.loads(row) for row in (ROOT / (name + ".jsonl")).read_text().splitlines()]
        if name == "native-events":
            rows = [row for row in rows if row["event"]["type"] in event_types]
        write(name + ".json", rows)
    for name in (
        "unexpected-decision.json",
        "bounds-violations.jsonl",
        "forbidden-provider-http.jsonl",
    ):
        if (ROOT / name).exists():
            shutil.copyfile(ROOT / name, OUT / name)
    report = read("report.json")
    final = read("post-cleanup.json")
    native = json.loads((OUT / "native-events.json").read_bytes())
    events = [row["event"] for row in native]
    children = [e for e in events if e["type"] == "subagent.started"]
    child_elapsed = None
    if children:
        child = children[0]
        terminal = next(
            (
                e
                for e in events
                if e["type"] in {"subagent.completed", "subagent.failed"}
                and e.get("agentId") == child.get("agentId")
            ),
            None,
        )
        if terminal:
            child_elapsed = (
                datetime.fromisoformat(terminal["timestamp"])
                - datetime.fromisoformat(child["timestamp"])
            ).total_seconds()
    records = final["records"]
    tools = json.loads((OUT / "tool-results.json").read_bytes())
    submissions = [r for r in tools if r["tool"] == "submit_generation"]
    completed = next(
        e
        for e in final["events"]
        if e["event_type"] == "work.state_changed"
        and e["payload"]["state"] == "completed"
        and e["work_id"] == report["request_final"]["receipt"]["work_id"]
    )
    root_sent = json.loads((OUT / "sdk-send.json").read_bytes())[0]["at"]
    root_seconds = datetime.fromisoformat(completed["occurred_at"]).timestamp() - root_sent
    write(
        "verification.json",
        dict(
            scope="Actual native parent/child shared canonical target admission and RESERVED allowance guard ONLY. Parent acceptance pending.",
            sdk_sends=report["sdk_sends"],
            actual_children=len(children),
            native_submit_calls=len(submissions),
            planned_jobs=len(records.get("jobs", [])),
            reserved_rows=sum(r["state"] == "reserved" for r in final["reservations"]),
            generation_budget_exhausted_results=sum(
                json.loads(r["result"]).get("error") == "generation_budget_exhausted"
                for r in submissions
            ),
            consumed_reservations=sum(r["state"] == "consumed" for r in final["reservations"]),
            provider_http_attempts=report["provider_http_attempts"],
            provider_pump_attempts=report["provider_pump_attempts"],
            actual_provider_executions=0,
            new_creative_outputs=0,
            child_seconds=child_elapsed,
            root_send_to_work_completed_seconds=root_seconds,
            retained_native_event_counts=dict(Counter(e["type"] for e in events)),
            executor_boundary="Only W01 delivery was started. ASGI app manage_features=False. No FeatureLifecycle, provider/preview/workflow pump. Provider pump additionally forbidden and ALL provider HTTP independently blocked/counts as failure.",
            remaining_limits="No real executions, consumed exhaustion, uncertain/poll/download behaviour or admission replay is claimed here. Compose with accepted offline ledger tests and prior actual M07 uncertain-to-consumed provider recovery.",
            cleanup="Three unexecuted planned jobs and reserved rows intentionally retained, gate closed. No reset/manual completion or fabricated completion/child authority.",
            blocker=report.get("blocker"),
            private_run=str(ROOT),
        ),
    )
    source = [
        "apps/backend/src/proscenium/copilot/service.py",
        "apps/backend/src/proscenium/copilot/state.py",
        "apps/backend/src/proscenium/copilot/tools.py",
        "apps/backend/src/proscenium/copilot/native.py",
        "apps/backend/src/proscenium/providers/service.py",
        "apps/backend/src/proscenium/providers/targets.py",
        "apps/backend/src/proscenium/jobs/resources.py",
        "apps/backend/src/proscenium/providers/sd_turbo_fp16.py",
    ]
    write(
        "runtime-identities.json",
        dict(
            base=report["base"],
            executed_driver_commit="e5787dc50e6ca3afd053e6cff83e6f69abf7ca0e",
            sdk_version=version("github-copilot-sdk"),
            direct_cli_version="1.0.112",
            model="gpt-6.1-sol",
            child_configured_model_policy="required",
            actual_child_model_selection_source=children[0]["data"]["modelSelectionSource"]
            if children
            else None,
            native_binary_sha256=sha(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            production_source_sha256={name: sha(REPO / name) for name in source},
        ),
    )
    write(
        "manifest.json",
        {
            p.name: {"bytes": p.stat().st_size, "sha256": sha(p)}
            for p in sorted(OUT.iterdir())
            if p.is_file() and p.name != "manifest.json"
        },
    )
    print((OUT / "verification.json").read_text())


if __name__ == "__main__":
    main()
