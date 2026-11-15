"""Synthetic native events exercise persisted failure reporting without starting a model."""

from types import SimpleNamespace

import pytest
from proscenium.api.errors import DomainError
from proscenium.copilot.activity import MAX_MESSAGE, failure_summary, safe_text
from proscenium.copilot.state import State, key, save
from proscenium.db import Store
from proscenium.jobs.gate import ExecutionGate

PROJECT, SESSION, WORK, REQUEST, TOOL = (
    "project-one",
    "session-one",
    "work-one",
    "request-one",
    "tool-one",
)
BINDING = {"work_id": WORK, "request_id": REQUEST, "delivery_id": REQUEST}


@pytest.fixture
def runtime(tmp_path):
    store = Store(tmp_path / "project.sqlite3", PROJECT)
    gate = ExecutionGate()
    gate.resume(PROJECT, reconciliation_evidence="Synthetic offline fixture")
    registry = SimpleNamespace(
        project_store=lambda project_id: store,
        get=lambda project_id: {"state": "active", "state_revision": 1},
    )
    services = SimpleNamespace(
        registry=registry, ownership=SimpleNamespace(epoch="epoch"), gate=gate
    )
    state = State(services, SimpleNamespace())
    with store.transaction() as tx:
        tx.put(
            "work",
            WORK,
            {
                "schema_version": "1.0",
                "work_id": WORK,
                "project_id": PROJECT,
                "originating_request_id": REQUEST,
                "native_session_id": SESSION,
                "generation_task_id": "task-one",
                "state": "running",
                "state_revision": 0,
                "directive_request_ids": [],
                "job_ids": [],
            },
            schema="WorkRecord",
        )
        tx.put(
            "requests",
            REQUEST,
            {
                "schema_version": "1.0",
                "request_id": REQUEST,
                "project_id": PROJECT,
                "accepted_at": "2026-11-15T00:00:00Z",
                "effective_profile_id": None,
                "generation_task_id": "task-one",
                "submission": {
                    "schema_version": "1.0",
                    "request_id": REQUEST,
                    "project_id": PROJECT,
                    "type": "queue",
                    "text": "Synthetic offline request",
                    "context_id": "context-one",
                },
                "receipt": {
                    "schema_version": "1.0",
                    "request_id": REQUEST,
                    "project_id": PROJECT,
                    "type": "queue",
                    "state": "acknowledged",
                    "state_revision": 0,
                    "work_id": WORK,
                    "native_message_id": "native-message-one",
                    "replayed": False,
                },
            },
            schema="RequestRecord",
        )
        state.bind(tx, SESSION, "event", "root", BINDING)
        tx.put("copilot.last_delivery", WORK, {"delivery_id": REQUEST})
    return state, store, gate


def event(identity, kind, **data):
    return {"id": identity, "type": kind, "parentId": "root", "data": data}


def start(state):
    state.reduce(
        PROJECT, SESSION, event("start", "tool.execution_start", toolCallId=TOOL, toolName="bash")
    )


def rejection_stage(store, authority="automatic_policy", **changes):
    stage = {
        **BINDING,
        "session_id": SESSION,
        "tool_call_id": TOOL,
        "phase": "completed",
        "reply": "reject",
        "authority": authority,
        "delivery_state": "acknowledged",
        **changes,
    }
    with store.transaction() as tx:
        tx.put("copilot.permissions", "permission-one", stage)


@pytest.mark.parametrize(
    ("authority", "expected"),
    [
        ("automatic_policy", "Blocked automatically"),
        ("owner", "declined by the owner"),
        ("host", "application held"),
    ],
)
def test_native_user_rejection_uses_confirmed_authority(runtime, authority, expected):
    state, store, _ = runtime
    start(state)
    rejection_stage(store, authority)
    state.reduce(
        PROJECT,
        SESSION,
        event(
            "failure",
            "tool.execution_complete",
            toolCallId=TOOL,
            success=False,
            error={"code": "rejected", "message": "The user rejected this tool call."},
        ),
    )
    with store.transaction(write=False) as tx:
        latest = tx.events()["events"][-1]
        assert latest["payload"]["state"] == "failed"
        assert expected in latest["payload"]["summary"]
        assert "The user rejected" not in latest["payload"]["summary"]
        assert (
            tx.require("invocations", key(SESSION, "tool", TOOL))["failure_summary"]
            == latest["payload"]["summary"]
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"session_id": "other-session"},
        {"work_id": "other-work"},
        {"request_id": "other-request"},
        {"delivery_id": "other-delivery"},
        {"tool_call_id": "other-tool"},
        {"phase": "reply_prepared"},
        {"delivery_state": "outcome_uncertain"},
        {"reply": "approve"},
    ],
)
def test_wrong_or_unconfirmed_denial_never_blames_owner(runtime, changes):
    state, store, _ = runtime
    start(state)
    rejection_stage(store, "owner", **changes)
    with store.transaction(write=False) as tx:
        invocation = tx.require("invocations", key(SESSION, "tool", TOOL))
        summary = failure_summary(
            tx,
            invocation,
            {"error": {"code": "rejected", "message": "The user rejected this tool call."}},
        )
    assert "authority was not confirmed" in summary
    assert "declined by the owner" not in summary


def test_policy_reason_survives_without_copying_command_output(runtime):
    state, store, _ = runtime
    start(state)
    rejection_stage(store, denial_reason="The request matched a protected environment-file rule.")
    state.reduce(
        PROJECT,
        SESSION,
        event(
            "failure",
            "tool.execution_complete",
            toolCallId=TOOL,
            success=False,
            error={"code": "rejected", "message": "The user rejected this tool call."},
            result={"content": "Private tool output must not become a diagnostic"},
        ),
    )
    with store.transaction(write=False) as tx:
        summary = tx.events()["events"][-1]["payload"]["summary"]
    assert "protected environment-file rule" in summary
    assert "Private tool output" not in summary


def test_out_of_order_failure_keeps_bounded_error_for_later_attribution(runtime):
    state, store, _ = runtime
    failure = event(
        "failure",
        "tool.execution_complete",
        toolCallId=TOOL,
        success=False,
        error={"message": "Python module is missing", "code": "command_failed"},
    )
    state.reduce(PROJECT, SESSION, failure)
    start(state)
    state.drain_pending(PROJECT, SESSION)
    with store.transaction(write=False) as tx:
        summary = tx.events()["events"][-1]["payload"]["summary"]
        assert summary == "Python module is missing (command_failed)"
        assert tx.require("copilot.pending_events", key(SESSION, "failure"))["resolved"]


def test_error_text_is_bounded_and_common_credentials_are_redacted():
    token = "ghp_" + "A" * 36
    raw = f"\x1b[31mFailure\x1b[0m {token} api_key=fixture-only-value https://name:password@example.invalid/"
    text = safe_text(raw)
    assert "Failure" in text and "\x1b" not in text
    assert token not in text and "fixture-only-value" not in text and "name:password" not in text
    assert safe_text("a" * (MAX_MESSAGE + 10)) == "a" * MAX_MESSAGE + "…"
    assert safe_text({"message": "not a string"}) == ""


def test_unsolicited_abort_holds_original_work_without_claiming_stop_or_completion(runtime):
    state, store, gate = runtime
    aborted = event("abort", "abort", reason="user_initiated")
    state.reduce(PROJECT, SESSION, aborted)
    with store.transaction(write=False) as tx:
        work = tx.require("work", WORK)
        receipt = tx.require("requests", REQUEST)["receipt"]
        watermark = tx.watermark()
        assert work["state"] == "awaiting_resume"
        assert (
            receipt["state"] == "acknowledged"
            and receipt["native_message_id"] == "native-message-one"
        )
        assert receipt["detail_code"] == "native_turn_aborted"
        assert tx.all("copilot.stops") == []
        assert not tx.get("copilot.idle", SESSION, required=False)
    with pytest.raises(DomainError):
        gate.require(PROJECT)
    state.reduce(PROJECT, SESSION, aborted)
    with store.transaction(write=False) as tx:
        assert tx.watermark() == watermark
        assert tx.require("work", WORK) == work


@pytest.mark.parametrize(
    "condition", ["later_delivery", "stop_requested", "completed", "child", "unattributed"]
)
def test_abort_cannot_override_stop_completed_work_or_another_turn(runtime, condition):
    state, store, _ = runtime
    aborted = event("abort", "abort", reason="user_initiated")
    with store.transaction() as tx:
        if condition == "later_delivery":
            save(tx, "copilot.last_delivery", WORK, {"delivery_id": "new-delivery"})
        elif condition in {"stop_requested", "completed"}:
            state.work_state(tx, WORK, condition, evidence="Synthetic prior control outcome")
        elif condition == "child":
            aborted["agentId"] = "child-one"
        else:
            aborted.pop("parentId")
        before = tx.require("work", WORK)
    state.reduce(PROJECT, SESSION, aborted)
    with store.transaction(write=False) as tx:
        assert tx.require("work", WORK) == before
        assert tx.all("copilot.interruptions") == []
