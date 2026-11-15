"""Offline recovery of the original uncertain Stop after all tool calls settled."""

import asyncio
import copy
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from copilot import CopilotClient
from copilot.generated.rpc import SessionRpc
from proscenium.auth import Caller
from proscenium.copilot import cold_stop
from proscenium.copilot.native import Native
from proscenium.copilot.service import CallbackOwner, Copilot
from proscenium.copilot.state import save
from proscenium.db import Store
from proscenium.jobs.gate import ExecutionGate

PROJECT, SESSION, WORK, REQUEST, STOP = "project", "session", "work", "request", "original-stop"
OWNER = Caller("owner", "fixture-owner")


class RpcClient:
    def __init__(self, case, mutate):
        self.case, self.mutate = case, mutate
        self.messages = []

    async def _send_message(self, message, *args, **kwargs):
        self.messages.append(copy.deepcopy(message))

    async def request(self, method, params, **kwargs):
        await self._send_message(dict(id=str(uuid4()), method=method, params=params))
        self.mutate(method)
        if method == "session.queue.pendingItems":
            result = dict(items=[], steeringMessages=[], inFlightSteeringCount=0)
            if self.case == "queued_steer":
                result["steeringMessages"] = ["fixture-only instruction"]
            if self.case == "inflight":
                result["inFlightSteeringCount"] = 1
            return result
        if method == "session.queue.setDrainPaused":
            return None
        if method == "session.tasks.list":
            return {
                "tasks": []
                if self.case != "active_child"
                else [
                    dict(
                        id="child",
                        type="agent",
                        agentType="fixture",
                        description="fixture",
                        prompt="fixture",
                        startedAt="2026-11-15T00:00:00Z",
                        status="running",
                        toolCallId="child-tool",
                    )
                ]
            }
        if method == "session.tools.listPendingRequests":
            return {
                "items": []
                if self.case != "external_tool"
                else [
                    dict(
                        requestId="external",
                        toolCallId="external-call",
                        toolName="bash",
                        arguments={},
                    )
                ]
            }
        if method == "session.abort":
            return {"success": self.case != "abort_failed"}
        if method == "session.interruptMainTurn":
            return {"interrupted": self.case == "active_main"}
        raise AssertionError(f"Unexpected RPC: {method}")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    async def blocked(*args, **kwargs):
        raise AssertionError("This test must not start native execution or send a model request")

    monkeypatch.setattr(CopilotClient, "start", blocked)
    store = Store(tmp_path / "project.sqlite3", PROJECT)
    gate = ExecutionGate()
    registry = SimpleNamespace(
        project_store=lambda project_id: store,
        active_id=lambda: PROJECT,
        get=lambda project_id: {
            "path": str(tmp_path),
            "state": "awaiting_resume",
            "state_revision": 3,
        },
    )
    services = SimpleNamespace(
        registry=registry,
        ownership=SimpleNamespace(epoch="current-epoch"),
        gate=gate,
        settings=SimpleNamespace(data_directory=tmp_path / "machine"),
    )
    native = object.__new__(Native)
    native.services = services
    native.non_continuing_session_id = SESSION
    native.departed_process = {
        "pid": 123,
        "start": "previous-start",
        "observed_identity": None,
        "session_id": SESSION,
        "parent_pid": 122,
        "parent_start": "previous-parent",
    }
    native.transport_alive = lambda: True
    copilot = Copilot(services, native=native)
    copilot.fixture_case = None

    def mutate(method):
        if copilot.fixture_case == "callback_activity":
            copilot.callback_owners[PROJECT].activity_sequence += 1

    client = RpcClient(None, mutate)

    async def events():
        return []

    session = SimpleNamespace(
        session_id=SESSION,
        rpc=SessionRpc(client, SESSION),
        get_events=events,
        get_raw_events=events,
        send=blocked,
    )

    async def open_session(project_id, **kwargs):
        if project_id not in copilot.sessions:
            owner = CallbackOwner(project_id, SESSION)
            copilot.callback_owners[project_id] = owner
            copilot.session_callback_owners[id(session)] = owner
            copilot.sessions[project_id] = session
        return session

    copilot.session = open_session
    with store.transaction() as tx:
        tx.put(
            "copilot.control",
            "gate",
            {
                "fenced": True,
                "epoch": "current-epoch",
                "revision": 5,
                "activation_revision": 3,
                "reason": "awaiting_resume",
            },
        )
        tx.put(
            "copilot.session",
            "native",
            {
                "session_id": SESSION,
                "owner_epoch": "current-epoch",
            },
        )
        tx.put(
            "work",
            WORK,
            {
                "schema_version": "1.0",
                "project_id": PROJECT,
                "work_id": WORK,
                "originating_request_id": REQUEST,
                "native_session_id": SESSION,
                "generation_task_id": "task",
                "state": "awaiting_resume",
                "state_revision": 0,
                "directive_request_ids": [],
                "job_ids": [],
            },
            schema="WorkRecord",
        )
        for identity, kind, status, work_id in (
            (REQUEST, "queue", "acknowledged", WORK),
            (STOP, "stop", "outcome_uncertain", WORK),
            ("held", "queue", "held", "future-work"),
        ):
            submission = {
                "schema_version": "1.0",
                "request_id": identity,
                "project_id": PROJECT,
                "type": kind,
            }
            if kind == "stop":
                submission["target_work_id"] = WORK
            else:
                submission.update(text="Fixture prompt", context_id="fixture-context")
            tx.put(
                "requests",
                identity,
                {
                    "schema_version": "1.0",
                    "request_id": identity,
                    "project_id": PROJECT,
                    "accepted_at": "2026-11-15T00:00:00Z",
                    "effective_profile_id": None,
                    "generation_task_id": "task",
                    "submission": submission,
                    "receipt": {
                        "schema_version": "1.0",
                        "request_id": identity,
                        "project_id": PROJECT,
                        "type": kind,
                        "state": status,
                        "state_revision": 0,
                        "work_id": work_id,
                        "native_message_id": "original-message" if identity == REQUEST else None,
                        "replayed": False,
                    },
                },
                schema="RequestRecord",
            )
        tx.put(
            "copilot.stops",
            STOP,
            {
                "phase": "outcome_uncertain",
                "pause": "acknowledged",
                "abort": "acknowledged",
                "work_id": WORK,
            },
        )
        tx.put("copilot.sends", REQUEST, {"state": "acknowledged", "session_id": SESSION})
        tx.put(
            "invocations",
            "failed-bash",
            {
                "invocation_id": "failed-bash",
                "native_id": "failed-bash-call",
                "session_id": SESSION,
                "work_id": WORK,
                "request_id": REQUEST,
                "kind": "tool",
                "name": "bash",
                "state": "failed",
            },
        )
        tx.enqueue(STOP, "copilot.stop", {"project_id": PROJECT, "request_id": STOP})
        tx.claim_intent(STOP)
    return copilot, store, client


def test_settled_stop_reconciles_original_identity_then_releases_empty_queue(runtime):
    copilot, store, client = runtime

    async def run():
        with store.transaction(write=False) as tx:
            invocation = tx.require("invocations", "failed-bash")
            original = tx.require("requests", REQUEST)
        evidence = await copilot.reconcile(PROJECT)
        assert evidence["uncertain_stops"] == 0 and evidence["live_invocations"] == 0
        with store.transaction() as tx:
            phase = tx.require("copilot.stops", STOP)
            observation = tx.require("copilot.cold_stop_observations", phase["cold_observation_id"])
            assert phase["phase"] == "confirmed"
            assert tx.require("work", WORK)["state"] == "stopped"
            request = tx.require("requests", REQUEST)
            assert request["submission"] == original["submission"]
            assert request["receipt"]["native_message_id"] == "original-message"
            assert request["receipt"]["state"] == "stopped"
            assert tx.require("invocations", "failed-bash") == invocation
            assert tx.require("requests", "held")["receipt"]["state"] == "held"
            assert (
                tx.require("requests", STOP)["receipt"]["detail_code"]
                == "cold_settled_stop_confirmed"
            )
            assert observation["previous_stop_receipt"]["state"] == "outcome_uncertain"
            assert observation["previous_outbox"]["state"] == "dispatching"
            assert (
                tx.connection.execute("SELECT state FROM outbox WHERE id=?", (STOP,)).fetchone()[0]
                == "acknowledged"
            )
            assert copilot.state.control(tx)["fenced"]
            assert cold_stop.apply(copilot.state, tx, STOP, [], observation) is False
        count = len(client.messages)
        await copilot.reconcile(PROJECT)
        assert all(m["method"] == "session.queue.pendingItems" for m in client.messages[count:])
        copilot.state.cancel(
            OWNER,
            "held",
            {
                "schema_version": "1.0",
                "operation_id": "cancel-held",
                "project_id": PROJECT,
                "expected_revision": 0,
            },
        )
        body = {
            "schema_version": "1.0",
            "operation_id": "release-empty",
            "project_id": PROJECT,
            "mode": "release_held_queue",
            "work_id": None,
            "expected_revision": 3,
        }
        copilot.admit_resume(OWNER, body)
        await copilot.resume(PROJECT, body["operation_id"])
        with store.transaction(write=False) as tx:
            assert tx.require("copilot.resumes", body["operation_id"])["state"] == "completed"
            assert not copilot.state.control(tx)["fenced"]
            assert copilot.state.control(tx)["queue_released"]
            assert tx.require("requests", "held")["receipt"]["state"] == "cancelled"
            assert tx.require("work", WORK)["state"] == "stopped"
        copilot.services.gate.require(PROJECT)
        assert any(m["params"].get("paused") is False for m in client.messages)
        assert all(m["method"] in cold_stop.METHODS for m in client.messages)

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    [
        "live_invocation",
        "unsettled_send",
        "foreign_work",
        "pending_decision",
        "queued_steer",
        "inflight",
        "active_child",
        "external_tool",
        "active_main",
        "abort_failed",
        "callback_activity",
        "callback_before_commit",
        "wrong_session",
        "wrong_owner",
        "departed_missing",
        "departed_still_live",
        "stale",
        "pending_event",
    ],
)
def test_uncertain_stop_is_preserved_without_an_exact_current_barrier(runtime, case):
    copilot, store, client = runtime
    copilot.fixture_case = client.case = case
    if case == "wrong_session":
        copilot.native.non_continuing_session_id = "other-session"
    if case == "wrong_owner":
        copilot.native.transport_alive = lambda: False
    if case == "departed_missing":
        copilot.native.departed_process = None
    if case == "departed_still_live":
        copilot.native.departed_process["observed_identity"] = "previous-start"
    if case == "stale":
        snapshot = copilot.native.cold_stop_snapshot

        async def stale(*args):
            value = await snapshot(*args)
            value["finished_monotonic"] = time.monotonic() - 10
            return value

        copilot.native.cold_stop_snapshot = stale
    if case == "callback_before_commit":
        work_state = copilot.state.work_state

        def mutate(*args, **kwargs):
            copilot.callback_owners[PROJECT].activity_sequence += 1
            return work_state(*args, **kwargs)

        copilot.state.work_state = mutate
    with store.transaction() as tx:
        if case == "live_invocation":
            invocation = tx.require("invocations", "failed-bash")
            save(tx, "invocations", "failed-bash", {**invocation, "state": "running"})
        if case == "unsettled_send":
            save(
                tx, "copilot.sends", REQUEST, {"state": "outcome_uncertain", "session_id": SESSION}
            )
        if case == "foreign_work":
            work = tx.require("work", WORK)
            tx.put("work", "other-work", {**work, "work_id": "other-work"}, schema="WorkRecord")
        if case == "pending_decision":
            tx.put("decisions", "decision", {"state": "pending"})
        if case == "pending_event":
            tx.put(
                "copilot.pending_events",
                "event",
                {"resolved": False, "session_id": "other-session"},
            )
        before = {name: tx.all(name) for name in ("work", "requests", "invocations")}

    async def run():
        evidence = await copilot.reconcile(PROJECT)
        assert evidence["uncertain_stops"] == 1
        with store.transaction(write=False) as tx:
            assert {name: tx.all(name) for name in before} == before
            assert tx.require("copilot.stops", STOP)["phase"] == "outcome_uncertain"
            assert copilot.state.control(tx)["fenced"]
        assert "session.send" not in json.dumps(client.messages)

    asyncio.run(run())
