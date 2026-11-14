"""Retained actual native admission proof. No provider/model/process work."""

import hashlib
import json
from pathlib import Path

EVIDENCE = Path(__file__).parents[2] / "docs/evidence/m07-native-budget"


def load(name):
    return json.loads((EVIDENCE / name).read_bytes())


def test_actual_one_root_one_required_sol_child_and_trusted_context():
    report = load("report.json")
    assert "blocker" not in report
    assert report["sdk_sends"] == report["guarded_task_calls"] == 1
    assert not report["violations"]
    assert report["request_final"]["receipt"]["state"] == "completed"
    events = [r["event"] for r in load("native-events.json")]
    started = [e for e in events if e["type"] == "subagent.started"]
    assert len(started) == 1
    child = started[0]
    usage = [e for e in events if e["type"] == "assistant.usage"]
    assert usage and all(e["data"]["model"] == "gpt-6.1-sol" for e in usage)
    assert {e.get("agentId") for e in usage} == {None, child["agentId"]}
    assert child["data"]["agentName"] == "authoring-specialist"
    assert child["data"]["model"] == "gpt-6.1-sol"
    assert child["data"]["modelSelectionSource"] == "explicit_override"
    delegation = next(
        e
        for e in events
        if e["type"] == "tool.execution_start" and e["data"].get("toolName") == "task"
    )
    assert delegation["data"]["arguments"]["model"] == "gpt-6.1-sol"
    assert any(
        e["type"] == "subagent.completed" and e["agentId"] == child["agentId"] for e in events
    )
    record = load("post-cleanup.json")["records"]
    request = record["requests"][0]
    work = next(w for w in record["work"] if w["work_id"] == request["receipt"]["work_id"])
    hooks = [r for r in load("prompt-hooks.json") if r["input"].get("agentId") == child["agentId"]]
    assert hooks
    payload = json.loads(hooks[0]["result"]["additionalContext"])
    assert (
        payload["generation_task_id"] == request["generation_task_id"] == work["generation_task_id"]
    )
    assert payload["work_id"] == work["work_id"]
    assert payload["request_id"] == request["request_id"]
    assert len(record["copilot.hooks"]) == 1
    assert len(load("sdk-send.json")) == 1


def test_actual_parent_positive_child_positive_and_child_negative_admissions():
    results = [r for r in load("tool-results.json") if r["tool"] == "submit_generation"]
    assert len(results) == 4
    by_id = {r["arguments"]["job_id"]: r for r in results}
    for number in (1, 2, 3):
        result = json.loads(by_id[f"m07-budget-{number}"]["result"])
        assert result["state"] == "planned" and result["external_job_id"] is None
    assert json.loads(by_id["m07-budget-4"]["result"])["error"] == "generation_budget_exhausted"
    assert by_id["m07-budget-1"]["invocation"]["agent_id"] is None
    assert by_id["m07-budget-2"]["invocation"]["agent_id"] is None
    assert (
        by_id["m07-budget-3"]["invocation"]["agent_id"]
        == by_id["m07-budget-4"]["invocation"]["agent_id"]
    )
    assert by_id["m07-budget-3"]["invocation"]["agent_id"] is not None
    final = load("post-cleanup.json")
    invocations = final["records"]["invocations"]
    for row in results:
        authority = next(
            i for i in invocations if i.get("native_id") == row["invocation"]["tool_call_id"]
        )
        assert authority["agent_id"] == row["invocation"]["agent_id"]
        assert authority["session_id"] == row["invocation"]["session_id"]
    assert len({r["arguments"]["generation_task_id"] for r in results}) == 1
    assert (
        len(
            {json.dumps(r["arguments"]["settings"]["target_slot"], sort_keys=True) for r in results}
        )
        == 1
    )
    assert len({r["arguments"]["target_id"] for r in results}) == 4


def test_production_ledger_three_reserved_default_policy_no_fourth_job_or_reset():
    settled, final = load("settled.json"), load("post-cleanup.json")
    jobs = final["records"]["jobs"]
    assert {j["job_id"] for j in jobs} == {"m07-budget-1", "m07-budget-2", "m07-budget-3"}
    assert all(
        j["state"] == "planned" and j["attempt_number"] == 1 and not j["artifact_ids"] for j in jobs
    )
    assert len({j["target"]["target_id"] for j in jobs}) == 1
    rows = final["reservations"]
    assert len(rows) == 3 and all(r["state"] == "reserved" for r in rows)
    assert len({r["task_id"] for r in rows}) == len({r["target_id"] for r in rows}) == 1
    policy = final["records"]["providers.tasks"]
    assert len(policy) == 1 and policy[0]["limit"] == 3
    assert final["records"]["copilot.effective"][0]["generation_attempt_limit"] == 3
    assert "generation_attempt_limit" not in load("owner-queue.json")["body"]
    assert settled["reservations"] == final["reservations"]
    assert settled["records"]["jobs"] == final["records"]["jobs"]
    assert not final["records"].get("providers.policy_extensions")
    assert not final["records"].get("providers.execution")


def test_zero_execution_closed_gate_cleanup_bounded_resources_and_manifest():
    report = load("report.json")
    assert (
        report["execution_disabled"] and report["gate_closed"] and report["native_process_exited"]
    )
    assert report["provider_http_attempts"] == report["provider_pump_attempts"] == []
    assert load("post-cleanup.json")["resource_claims"] == []
    assert report["input_original_unchanged"]
    assert report["elapsed_seconds"] <= 300 and report["minimum_mem_available"] >= 4 * 1024**3
    assert load("verification.json")["child_seconds"] <= 120
    assert 0 < load("verification.json")["root_send_to_work_completed_seconds"] <= 180
    assert load("verification.json")["consumed_reservations"] == 0
    assert load("verification.json")["actual_provider_executions"] == 0
    observed = load("cleanup-observation.json")
    assert observed["host"]["observed_start"] is None
    assert observed["sdk"]["observed_start"] is None
    for name, metadata in load("manifest.json").items():
        raw = (EVIDENCE / name).read_bytes()
        assert (
            len(raw) == metadata["bytes"] and hashlib.sha256(raw).hexdigest() == metadata["sha256"]
        )
