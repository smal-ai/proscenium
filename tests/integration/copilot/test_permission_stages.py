"""Durable callback correlation using retained M10 payloads, with no native execution."""

import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from proscenium.api.errors import DomainError
from proscenium.copilot.state import key
from test_delivery import OWNER, PROJECT, context, submit
from test_delivery import runtime as runtime

OBSERVED = json.loads(Path("docs/evidence/m10/permission-defect.json").read_text())
ROWS = OBSERVED["requests"]
TOOL = OBSERVED["tool_call_id"]


async def setup(runtime):
    context(runtime)
    submit(runtime)
    await runtime.send(PROJECT, "request-one")
    session = runtime.native.session
    session.event(
        "tool.execution_start", {"toolCallId": TOOL, "toolName": "bash"}, parent=session.last_event
    )
    return session


def requested(session, row, *, payload=None):
    session.callbacks["on_event"](
        {
            "id": row["native_event_id"],
            "type": "permission.requested",
            "data": {
                "requestId": row["request_id"],
                "permissionRequest": payload or row["permission_request"],
            },
        }
    )


def completed(session, row, result="approved", *, event_id=None):
    session.event(
        "permission.completed",
        {"requestId": row["request_id"], "result": {"kind": result}},
        event_id=event_id,
    )


def stage(runtime, session, row):
    with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
        return tx.require("copilot.permissions", key(session.session_id, row["request_id"]))


def fenced(runtime):
    with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
        return runtime.state.control(tx)["fenced"]


def test_sequential_identical_actual_m10_payloads_each_claim_and_complete_once(runtime):
    async def run():
        session = await setup(runtime)
        for row in ROWS:
            requested(session, row)
            requested(session, row)  # Exact event replay cannot create another stage.
            result = await runtime.permission(
                PROJECT, row["permission_request"], {"session_id": session.session_id}
            )
            assert result.kind == "approve-once"
            prepared = stage(runtime, session, row)
            assert prepared["phase"] == "reply_prepared"
            assert prepared["authority"] == "automatic_policy"
            assert prepared["delivery_state"] == "outcome_uncertain"
            completed(session, row)
            completed(session, row)  # Another event ID reporting the same completion is harmless.
            assert stage(runtime, session, row)["phase"] == "completed"
        assert (
            stage(runtime, session, ROWS[0])["claim_id"]
            != stage(runtime, session, ROWS[1])["claim_id"]
        )
        assert not fenced(runtime)
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            assert tx.all("decisions") == []
            assert len(tx.all("copilot.permission_callbacks")) == 2
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    [
        "ambiguous",
        "duplicate",
        "unfinished",
        "restart",
        "stop",
        "wrong_session",
        "wrong_tool",
        "wrong_work",
    ],
)
def test_uncertain_or_unattributed_callback_never_borrows_approval(runtime, case):
    async def run():
        session = await setup(runtime)
        requested(session, ROWS[0])
        payload = copy.deepcopy(ROWS[0]["permission_request"])
        sid = session.session_id
        if case in {"duplicate", "unfinished"}:
            await runtime.permission(PROJECT, payload, {"session_id": sid})
        if case in {"ambiguous", "unfinished"}:
            requested(session, ROWS[1])
        if case == "restart":
            runtime.restore(PROJECT)
        if case == "stop":
            with runtime.services.registry.project_store(PROJECT).transaction() as tx:
                runtime.state.cancel_decisions(tx, stage(runtime, session, ROWS[0])["work_id"])
        if case == "wrong_session":
            sid = "another-session"
        if case == "wrong_tool":
            payload["toolCallId"] = "another-tool"
        if case == "wrong_work":
            with runtime.services.registry.project_store(PROJECT).transaction() as tx:
                identity = key(sid, "tool", TOOL)
                binding = tx.require("copilot.bindings", identity)
                tx.put(
                    "copilot.bindings",
                    identity,
                    {**binding, "work_id": "work-test"},
                    expected_revision=tx.revision("copilot.bindings", identity),
                )
        result = await runtime.permission(PROJECT, payload, {"session_id": sid})
        assert result.kind != "approve-once"
        assert fenced(runtime)
        assert stage(runtime, session, ROWS[0]).get("authority") != "owner"
        await runtime.close()

    asyncio.run(run())


def test_conflicting_native_request_identity_cannot_rewrite_stage(runtime):
    async def run():
        session = await setup(runtime)
        requested(session, ROWS[0])
        original = stage(runtime, session, ROWS[0])
        changed = copy.deepcopy(ROWS[0])
        changed["native_event_id"] = uuid4().hex
        changed["permission_request"]["intention"] = "Different operation"
        with pytest.raises(DomainError):
            runtime.state.reduce(
                PROJECT,
                session.session_id,
                {
                    "id": changed["native_event_id"],
                    "type": "permission.requested",
                    "data": {
                        "requestId": changed["request_id"],
                        "permissionRequest": changed["permission_request"],
                    },
                },
            )
        assert stage(runtime, session, ROWS[0]) == original
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("owner_action", ["approve_once", "decline", "stop"])
def test_owner_decision_and_host_stop_are_distinct_authorities(runtime, owner_action):
    async def run():
        session = await setup(runtime)
        payload = {**ROWS[0]["permission_request"], "fullCommandText": "git push"}
        requested(session, ROWS[0], payload=payload)
        callback = asyncio.create_task(
            runtime.permission(PROJECT, payload, {"session_id": session.session_id})
        )
        await asyncio.sleep(0.01)
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            decision = tx.all("decisions")[0]
        if owner_action == "stop":
            with runtime.services.registry.project_store(PROJECT).transaction() as tx:
                runtime.state.cancel_decisions(tx, decision["work_id"])
        else:
            runtime.state.answer(
                OWNER,
                decision["decision_id"],
                {
                    "schema_version": "1.0",
                    "project_id": PROJECT,
                    "operation_id": str(uuid4()),
                    "expected_revision": 0,
                    "fingerprint": decision["fingerprint"],
                    "action": owner_action,
                },
            )
        result = await callback
        assert (result.kind == "approve-once") == (owner_action == "approve_once")
        assert stage(runtime, session, ROWS[0])["authority"] == (
            "host" if owner_action == "stop" else "owner"
        )
        completed(
            session,
            ROWS[0],
            "approved" if owner_action == "approve_once" else "denied-interactively-by-user",
        )
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            decision = tx.require("decisions", decision["decision_id"])
            assert (
                decision["state"]
                == {"approve_once": "answered", "decline": "declined", "stop": "cancelled"}[
                    owner_action
                ]
            )
            assert decision["delivery_state"] == (
                "not_sent" if owner_action == "stop" else "acknowledged"
            )
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["pending", "claimed", "reply_prepared"])
def test_restart_never_reissues_an_unsettled_stage(runtime, phase):
    async def run():
        session = await setup(runtime)
        requested(session, ROWS[0])
        if phase == "reply_prepared":
            await runtime.permission(
                PROJECT, ROWS[0]["permission_request"], {"session_id": session.session_id}
            )
        elif phase == "claimed":
            with runtime.services.registry.project_store(PROJECT).transaction() as tx:
                identity = key(session.session_id, ROWS[0]["request_id"])
                value = tx.require("copilot.permissions", identity)
                tx.put(
                    "copilot.permissions",
                    identity,
                    {**value, "phase": "claimed", "claim_id": "crashed-callback"},
                    expected_revision=tx.revision("copilot.permissions", identity),
                )
        runtime.restore(PROJECT)
        assert stage(runtime, session, ROWS[0])["phase"] == "outcome_uncertain"
        result = await runtime.permission(
            PROJECT, ROWS[0]["permission_request"], {"session_id": session.session_id}
        )
        assert result.kind == "reject"
        assert stage(runtime, session, ROWS[0])["phase"] == "outcome_uncertain"
        await runtime.close()

    asyncio.run(run())


def test_duplicate_inflight_callback_fences_both_without_owner_consent(runtime):
    async def run():
        session = await setup(runtime)
        payload = {**ROWS[0]["permission_request"], "fullCommandText": "git push"}
        requested(session, ROWS[0], payload=payload)
        first = asyncio.create_task(
            runtime.permission(PROJECT, payload, {"session_id": session.session_id})
        )
        await asyncio.sleep(0.01)
        second = await runtime.permission(PROJECT, payload, {"session_id": session.session_id})
        assert second.kind == (await first).kind == "reject"
        assert stage(runtime, session, ROWS[0])["authority"] == "host"
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            decision = tx.all("decisions")[0]
            assert decision["state"] == "pending" and decision["response"] is None
            assert len(tx.all("copilot.permission_callbacks")) == 2
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("prepared", [False, True])
def test_unclaimed_or_mismatched_completion_cannot_acknowledge_stage(runtime, prepared):
    async def run():
        session = await setup(runtime)
        requested(session, ROWS[0])
        if prepared:
            await runtime.permission(
                PROJECT, ROWS[0]["permission_request"], {"session_id": session.session_id}
            )
        completed(session, ROWS[0], "denied-interactively-by-user" if prepared else "approved")
        value = stage(runtime, session, ROWS[0])
        assert value["phase"] == "outcome_uncertain"
        assert value["delivery_state"] != "acknowledged"
        assert value["authority"] != "owner"
        assert fenced(runtime)
        await runtime.close()

    asyncio.run(run())
