"""Pinned typed RPCs and exact uncertain-Stop reconciliation, with no live SDK/model."""

import asyncio
import copy
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from copilot.generated.rpc import SessionRpc
from proscenium.api.errors import DomainError
from proscenium.copilot import cold_stop
from proscenium.copilot.native import Native
from proscenium.copilot.state import key, save
from test_delivery import OWNER, PROJECT, record
from test_delivery import runtime as runtime
from test_native_io_read_stop import OBSERVED, setup_retained


class RpcClient:
    def __init__(self, case=None, mutate=None):
        self.case, self.mutate = case, mutate
        self.messages = []

    async def _send_message(self, message, *args, **kwargs):
        self.messages.append(copy.deepcopy(message))

    async def request(self, method, params, **kwargs):
        await self._send_message(dict(id=str(uuid4()), method=method, params=params))
        if self.mutate:
            self.mutate(method)
        if method == "session.queue.pendingItems":
            value = dict(items=[], steeringMessages=[], inFlightSteeringCount=0)
            if self.case == "queue":
                value["items"] = [
                    dict(
                        agentMode="interactive",
                        displayText="PRIVATE_ONLY_TEST",
                        id="queued",
                        kind="message",
                        messageId="message",
                    )
                ]
            if self.case == "steer":
                value["steeringMessages"] = ["PRIVATE_ONLY_TEST"]
            if self.case == "inflight":
                value["inFlightSteeringCount"] = 1
            if self.case == "incomplete_queue":
                value.pop("inFlightSteeringCount")
            return value
        if method == "session.queue.setDrainPaused":
            return None
        if method == "session.tasks.list":
            tasks = []
            if self.case in {"active_child", "terminal_child", "idle_child"}:
                tasks = [
                    dict(
                        type="agent",
                        agentType="authoring-specialist",
                        description="fixture",
                        id="child",
                        prompt="PRIVATE_ONLY_TEST",
                        startedAt="2026-11-14T18:00:00Z",
                        status={
                            "active_child": "running",
                            "terminal_child": "completed",
                            "idle_child": "idle",
                        }[self.case],
                        toolCallId="child-tool",
                    )
                ]
            return {"tasks": tasks}
        if method == "session.tools.listPendingRequests":
            return {
                "items": []
                if self.case != "external"
                else [
                    dict(
                        requestId="external-request",
                        toolCallId="external-call",
                        toolName="checkpoint_sources",
                        arguments={"private": "PRIVATE_ONLY_TEST"},
                    )
                ]
            }
        if method == "session.abort":
            return {"success": self.case != "abort_failed"}
        if method == "session.interruptMainTurn":
            return (
                {
                    "interrupted": {
                        "true": True,
                        "unknown": None,
                        "malformed": 0,
                        "missing": "absent",
                    }.get(self.case, False)
                }
                if self.case != "missing"
                else {}
            )
        raise AssertionError("No other RPC is authorised in this fixture")


@pytest.mark.parametrize(
    "case",
    [
        None,
        "terminal_child",
        "true",
        "unknown",
        "malformed",
        "missing",
        "active_child",
        "idle_child",
        "external",
        "queue",
        "steer",
        "inflight",
        "incomplete_queue",
        "abort_failed",
    ],
)
def test_pinned_rpc_inactivity_false_is_positive_not_an_unknown_fallback(services, case):
    async def run():
        adapter = object.__new__(Native)
        adapter.services = services
        adapter.non_continuing_session_id = "exact-session"
        client = RpcClient(case)
        session = SimpleNamespace(
            session_id="exact-session", rpc=SessionRpc(client, "exact-session")
        )
        if case in {None, "terminal_child"}:
            snapshot = await adapter.cold_stop_snapshot(session, lambda: True)
            cold_stop.validate(snapshot, "exact-session")
            assert next(r for r in snapshot["rpc"] if r["method"] == "session.interruptMainTurn")[
                "response"
            ] == {"interrupted": False}
            assert all(r["rpc_request_id"] for r in snapshot["rpc"])
            assert "PRIVATE_ONLY_TEST" not in json.dumps(snapshot)
        else:
            with pytest.raises((DomainError, AssertionError, ValueError)):
                await adapter.cold_stop_snapshot(session, lambda: True)
        for message in client.messages:
            assert message["method"] in cold_stop.METHODS
            if message["method"] == "session.interruptMainTurn":
                assert message["params"]["flushQueued"] is False
        if case in {
            "active_child",
            "idle_child",
            "external",
            "queue",
            "steer",
            "inflight",
            "incomplete_queue",
        }:
            assert not any(
                m["method"] in {"session.abort", "session.interruptMainTurn"}
                for m in client.messages
            )

    asyncio.run(run())


async def uncertain_original_stop(runtime, monkeypatch):
    inv_id = await setup_retained(runtime)
    await runtime.reconcile(PROJECT)
    session = runtime.native.session

    async def no_op_abort():
        session.aborts += 1

    session.abort = no_op_abort
    stop_id = "original-uncertain-stop"
    body = dict(
        schema_version="1.0",
        request_id=stop_id,
        project_id=PROJECT,
        type="stop",
        target_work_id=record(runtime)["receipt"]["work_id"],
    )
    runtime.state.submit(OWNER, body)
    real_sleep = asyncio.sleep

    async def yield_only(delay):
        await real_sleep(0)

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "sleep", yield_only)
        await runtime.stop(PROJECT, body["target_work_id"], stop_id)
    with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
        assert tx.require("copilot.stops", stop_id)["phase"] == "outcome_uncertain"
        stage = copy.deepcopy(
            tx.require(
                "copilot.permissions",
                key(session.session_id, OBSERVED["decision"]["native_decision_id"]),
            )
        )
    return inv_id, stop_id, body, stage


@pytest.mark.parametrize(
    "case",
    [
        None,
        "true",
        "unknown",
        "malformed",
        "active_child",
        "external",
        "queue",
        "steer",
        "wrong_session",
        "wrong_owner",
        "wrong_generation",
        "callback_activity",
        "callback_before_commit",
        "foreign_work",
        "bash",
        "stale",
    ],
)
def test_exact_original_uncertain_stop_is_observed_not_reset_or_replaced(
    runtime, monkeypatch, case
):
    async def run():
        inv_id, stop_id, body, stage_before = await uncertain_original_stop(runtime, monkeypatch)
        native = runtime.native
        sid = native.session.session_id
        adapter = object.__new__(Native)
        adapter.services = runtime.services
        adapter.non_continuing_session_id = sid

        def mutate(method):
            owner = runtime.callback_owners[PROJECT]
            if method == "session.queue.pendingItems":
                if case == "wrong_generation":
                    owner.generation = "not-the-captured-generation"
                if case == "callback_activity":
                    owner.activity_sequence += 1

        client = RpcClient(case, mutate)
        open_original = native.open

        async def open_metadata(*args):
            session = await open_original(*args)
            session.rpc = SessionRpc(client, sid)
            native.non_continuing_session_id = "foreign-session" if case == "wrong_session" else sid
            return session

        native.open = open_metadata
        native.transport_alive = lambda: case != "wrong_owner"

        async def snapshot(session, stable):
            try:
                value = await adapter.cold_stop_snapshot(session, stable)
                if case == "stale":
                    value["finished_monotonic"] = time.monotonic() - 10
                return value
            finally:
                native.latest_cold_stop_snapshot = adapter.latest_cold_stop_snapshot

        native.cold_stop_snapshot = snapshot
        if case == "callback_before_commit":
            work_state = runtime.state.work_state

            def callback_during_apply(*args, **kwargs):
                runtime.callback_owners[PROJECT].activity_sequence += 1
                return work_state(*args, **kwargs)

            runtime.state.work_state = callback_during_apply
        if case in {"foreign_work", "bash"}:
            with runtime.services.registry.project_store(PROJECT).transaction() as tx:
                if case == "bash":
                    inv = tx.require("invocations", inv_id)
                    save(tx, "invocations", inv_id, {**inv, "name": "bash"})
                else:
                    work = tx.require("work", body["target_work_id"])
                    tx.put(
                        "work",
                        "foreign-work",
                        {**work, "work_id": "foreign-work", "state_revision": 0},
                        schema="WorkRecord",
                    )
        result = await runtime.reconcile(PROJECT)
        with runtime.services.registry.project_store(PROJECT).transaction() as tx:
            phase = tx.require("copilot.stops", stop_id)
            inv = tx.require("invocations", inv_id)
            question = tx.require("decisions", OBSERVED["decision"]["decision_id"])
            assert (
                question["state"] == "cancelled"
                and question["response"] is None
                and question["resolved_by"] is None
                and question["delivery_state"] == "not_sent"
            )
            assert (
                tx.require(
                    "copilot.permissions", key(sid, OBSERVED["decision"]["native_decision_id"])
                )
                == stage_before
            )
            if case is None:
                assert result["uncertain_stops"] == result["live_invocations"] == 0
                assert phase["phase"] == "confirmed" and inv["state"] == "cancelled"
                assert tx.require("work", body["target_work_id"])["state"] == "stopped"
                assert "idle_native_event_id" not in phase
                assert phase["completion_basis"] == "cold_inactive_main_loop_rpc"
                observation = tx.require(
                    "copilot.cold_stop_observations", phase["cold_observation_id"]
                )
                assert observation["previous_stop_receipt"]["state"] == "outcome_uncertain"
                assert observation["previous_stop_phase"]["phase"] == "outcome_uncertain"
                assert observation["previous_outbox"]["state"] in {
                    "dispatching",
                    "outcome_uncertain",
                }
                assert (
                    inv["recovery"]["native_return_observed"] is False
                    and inv["recovery"]["native_exit_code"] is None
                )
                assert (
                    tx.connection.execute(
                        "select state from outbox where id=?", (stop_id,)
                    ).fetchone()[0]
                    == "acknowledged"
                )
                assert cold_stop.apply(runtime.state, tx, stop_id, [], observation) is False
            else:
                assert phase["phase"] == "outcome_uncertain" and inv["state"] == "running"
                assert tx.require("requests", stop_id)["receipt"]["state"] == "outcome_uncertain"
        if case is None:
            count = len(client.messages)
            await runtime.reconcile(PROJECT)
            assert len(client.messages) == count
            assert runtime.state.submit(OWNER, body)[0]["state"] == "completed"
        await runtime.close()

    asyncio.run(run())
