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
from proscenium.copilot import service as copilot_service
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
        self.history_calls = []

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
        client.history_calls.append("get_events")
        return []

    async def raw_events():
        client.history_calls.append("get_raw_events")
        return []

    session = SimpleNamespace(
        session_id=SESSION,
        rpc=SessionRpc(client, SESSION),
        get_events=events,
        get_raw_events=raw_events,
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
            assert observation["previous_outbox"]["state"] in {"dispatching", "outcome_uncertain"}
            assert (
                tx.connection.execute("SELECT state FROM outbox WHERE id=?", (STOP,)).fetchone()[0]
                == "acknowledged"
            )
            assert copilot.state.control(tx)["fenced"]
            assert cold_stop.apply(copilot.state, tx, STOP, [], observation) is False
        count = len(client.messages)
        history_before = client.history_calls.copy()
        assert history_before == ["get_events", "get_raw_events"]
        await copilot.reconcile(PROJECT)
        assert all(m["method"] == "session.queue.pendingItems" for m in client.messages[count:])
        assert client.history_calls == history_before
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


@pytest.mark.parametrize("origin_source", ["adapter", "marker", "unknown"])
def test_uncertain_stop_origin_is_pinned_before_failed_session_open(
    runtime, monkeypatch, origin_source
):
    copilot, store, client = runtime
    departed = copilot.native.departed_process.copy()
    original = {"pid": departed["pid"], "start": departed["start"]}
    recovery = {**departed, "pid": 129, "start": "replacement-start"}
    marker = copilot.services.settings.data_directory / "copilot-runtime/process.json"
    marker.parent.mkdir(parents=True)
    if origin_source != "adapter":
        copilot.native.departed_process = None
    if origin_source == "marker":
        marker.write_text(json.dumps(original))
        monkeypatch.setattr(copilot_service, "launcher_identity", lambda pid: None)
    expected = {"unknown": True} if origin_source == "unknown" else original
    assert cold_stop.send_settled_before_stop({"state": "acknowledged"}, expected)
    with store.transaction() as tx:
        copilot.state.request_state(tx, REQUEST, "outcome_uncertain", native_message_id=None)
        save(
            tx,
            "copilot.sends",
            REQUEST,
            {
                "state": "abandoned_on_departure",
                "session_id": SESSION,
                "history_matches": 0,
                "departed_process": departed,
            },
        )

    open_session = copilot.session
    origins_at_open = []

    async def open_with_first_failure(project_id):
        with store.transaction(write=False) as tx:
            origins_at_open.append(tx.get("copilot.stop_origins", STOP, required=False))
        if len(origins_at_open) == 1:
            # The replacement has started, but resume_session failed before returning it.
            marker.write_text(json.dumps(recovery))
            raise RuntimeError("Synthetic replacement session open failed")
        return await open_session(project_id)

    closed = []

    async def close():
        copilot.native.departed_process = json.loads(marker.read_text())
        closed.append(copilot.native.departed_process.copy())

    copilot.session = open_with_first_failure
    copilot.native.close = close

    async def run():
        for operation_id, detail in [
            ("reconcile-first", "provider_unavailable"),
            ("reconcile-second", "uncertain_stop"),
        ]:
            copilot.admit_resume(
                OWNER,
                {
                    "schema_version": "1.0",
                    "operation_id": operation_id,
                    "project_id": PROJECT,
                    "mode": "reconcile_only",
                    "work_id": None,
                    "expected_revision": 3,
                },
            )
            await copilot.resume(PROJECT, operation_id)
            with store.transaction(write=False) as tx:
                assert tx.require("copilot.stop_origins", STOP) == expected
                assert tx.require("copilot.stops", STOP)["phase"] == "outcome_uncertain"
                operation = tx.require("copilot.resumes", operation_id)
                assert (
                    operation["state"] == "outcome_uncertain" and operation["detail_code"] == detail
                )
                assert copilot.state.control(tx)["fenced"]
        assert origins_at_open == [expected, expected]
        assert closed == [recovery, recovery]
        assert [m["method"] for m in client.messages] == ["session.queue.pendingItems"]

    asyncio.run(run())


@pytest.mark.parametrize("state", ["stop_requested", "outcome_uncertain"])
def test_restore_pins_stop_origin_before_native_recovery(runtime, state):
    copilot, store, client = runtime
    if state == "stop_requested":
        with store.transaction() as tx:
            copilot.state.request_state(tx, STOP, "stop_requested")
            save(
                tx,
                "copilot.stops",
                STOP,
                {**tx.require("copilot.stops", STOP), "phase": "aborting"},
            )
    departed = copilot.native.departed_process
    copilot.restore(PROJECT)
    with store.transaction(write=False) as tx:
        assert tx.require("requests", STOP)["receipt"]["state"] == "outcome_uncertain"
        assert tx.require("copilot.stop_origins", STOP) == {
            k: departed[k] for k in ("pid", "start")
        }
    assert not client.messages


@pytest.mark.parametrize("same_process", [False, True])
def test_cold_stop_accepts_abandoned_sends_only_from_an_earlier_process(runtime, same_process):
    copilot, store, client = runtime
    new_work, new_request, new_stop = "next-work", "next-request", "next-stop"
    original_departure = copilot.native.departed_process.copy()

    async def run():
        with store.transaction() as tx:
            copilot.state.request_state(tx, STOP, "completed")
            save(
                tx,
                "copilot.stops",
                STOP,
                {**tx.require("copilot.stops", STOP), "phase": "confirmed"},
            )
            copilot.state.request_state(tx, REQUEST, "outcome_uncertain", native_message_id=None)
            save(
                tx, "copilot.sends", REQUEST, {"state": "outcome_uncertain", "session_id": SESSION}
            )
            tx.put(
                "copilot.continuation_sends",
                "older-continue",
                {
                    "operation_id": "older-continue",
                    "state": "dispatching",
                    "session_id": SESSION,
                },
            )
        first = await copilot.reconcile(PROJECT)
        assert not first["uncertain_request_ids"] and not first["uncertain_continuations"]
        history_before = client.history_calls.copy()
        rpc_before = len(client.messages)
        await copilot.reconcile(PROJECT)
        assert client.history_calls == history_before
        assert [m["method"] for m in client.messages[rpc_before:]] == ["session.queue.pendingItems"]
        with store.transaction() as tx:
            for namespace, identity in [
                ("copilot.sends", REQUEST),
                ("copilot.continuation_sends", "older-continue"),
            ]:
                abandoned = tx.require(namespace, identity)
                assert cold_stop.proven_departed_abandonment(abandoned)
                assert abandoned["departed_process"] == original_departure
            assert tx.require("requests", REQUEST)["receipt"]["state"] == "outcome_uncertain"
            previous = copilot.state.work_state(tx, WORK, "completed")
            tx.put(
                "work",
                new_work,
                {
                    **previous,
                    "work_id": new_work,
                    "originating_request_id": new_request,
                    "state": "awaiting_resume",
                    "state_revision": 0,
                },
                schema="WorkRecord",
            )
            for source_id, target_id, status in [
                (REQUEST, new_request, "acknowledged"),
                (STOP, new_stop, "outcome_uncertain"),
            ]:
                record = tx.require("requests", source_id)
                record["request_id"] = record["submission"]["request_id"] = record["receipt"][
                    "request_id"
                ] = target_id
                record["receipt"].update(work_id=new_work, state=status, state_revision=0)
                if source_id == STOP:
                    record["submission"]["target_work_id"] = new_work
                tx.put("requests", target_id, record, schema="RequestRecord")
            tx.put("copilot.sends", new_request, {"state": "acknowledged", "session_id": SESSION})
            tx.put(
                "copilot.stops",
                new_stop,
                {
                    "phase": "outcome_uncertain",
                    "pause": "acknowledged",
                    "abort": "acknowledged",
                    "work_id": new_work,
                },
            )
            tx.enqueue(new_stop, "copilot.stop", {"project_id": PROJECT, "request_id": new_stop})
            tx.claim_intent(new_stop)

        copilot.native.departed_process = (
            {**original_departure, "observed_identity": "pid-was-reused"}
            if same_process
            else {**original_departure, "pid": 129, "start": "next-start"}
        )
        copilot.sessions.clear()
        count = len(client.messages)
        if same_process:
            closed = []

            async def close():
                closed.append(copilot.native.departed_process)
                copilot.native.departed_process = {
                    **original_departure,
                    "pid": 140 + len(closed),
                    "start": f"recovery-{len(closed)}",
                }

            copilot.native.close = close
            for operation_id in ("reconcile-first", "reconcile-second"):
                copilot.admit_resume(
                    OWNER,
                    {
                        "schema_version": "1.0",
                        "operation_id": operation_id,
                        "project_id": PROJECT,
                        "mode": "reconcile_only",
                        "work_id": None,
                        "expected_revision": 3,
                    },
                )
                await copilot.resume(PROJECT, operation_id)
                with store.transaction(write=False) as tx:
                    operation = tx.require("copilot.resumes", operation_id)
                    assert operation["state"] == "outcome_uncertain"
                    assert operation["detail_code"] == "uncertain_stop"
                    assert tx.require("copilot.stops", new_stop)["phase"] == "outcome_uncertain"
                    assert tx.require("copilot.stop_origins", new_stop) == {
                        "pid": original_departure["pid"],
                        "start": original_departure["start"],
                    }
            assert len(closed) == 2 and closed[1]["start"] == "recovery-1"
        second = await copilot.reconcile(PROJECT)
        with store.transaction(write=False) as tx:
            assert tx.require("work", WORK)["state"] == "completed"
            assert tx.require("requests", REQUEST)["receipt"]["state"] == "outcome_uncertain"
            if same_process:
                assert second["uncertain_stops"] == 1
                assert tx.require("copilot.stops", new_stop)["phase"] == "outcome_uncertain"
                assert tx.require("work", new_work)["state"] == "awaiting_resume"
                assert all(
                    m["method"] == "session.queue.pendingItems" for m in client.messages[count:]
                )
            else:
                assert second["uncertain_stops"] == 0
                assert tx.require("copilot.stops", new_stop)["phase"] == "confirmed"
                assert tx.require("work", new_work)["state"] == "stopped"
                assert (
                    tx.require("requests", new_stop)["receipt"]["detail_code"]
                    == "cold_settled_stop_confirmed"
                )

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


@pytest.mark.parametrize("acknowledged", [True, False])
def test_settled_stop_reconciles_acknowledged_steers_and_rejects_unacknowledged_steer(
    runtime, acknowledged
):
    copilot, store, _ = runtime
    with store.transaction() as tx:
        work = tx.require("work", WORK)
        work.update(
            directive_request_ids=["steer-one"],
            state_revision=tx.revision("work", WORK) + 1,
        )
        save(tx, "work", WORK, work)
        tx.put(
            "requests",
            "steer-one",
            {
                "schema_version": "1.0",
                "request_id": "steer-one",
                "project_id": PROJECT,
                "accepted_at": "2026-11-15T00:00:01Z",
                "effective_profile_id": None,
                "generation_task_id": "task",
                "submission": {
                    "schema_version": "1.0",
                    "request_id": "steer-one",
                    "project_id": PROJECT,
                    "type": "steer",
                    "text": "Follow-up steer",
                    "context_id": "fixture-context",
                    "target_work_id": WORK,
                },
                "receipt": {
                    "schema_version": "1.0",
                    "request_id": "steer-one",
                    "project_id": PROJECT,
                    "type": "steer",
                    "state": "acknowledged" if acknowledged else "dispatching",
                    "state_revision": 0,
                    "work_id": WORK,
                    "native_message_id": "steer-msg" if acknowledged else None,
                    "replayed": False,
                },
            },
            schema="RequestRecord",
        )
        tx.put(
            "copilot.sends",
            "steer-one",
            {
                "state": "acknowledged" if acknowledged else "dispatching",
                "session_id": SESSION,
            },
        )
    evidence = asyncio.run(copilot.reconcile(PROJECT))
    with store.transaction(write=False) as tx:
        if acknowledged:
            assert evidence["uncertain_stops"] == 0
            assert tx.require("copilot.stops", STOP)["phase"] == "confirmed"
            assert tx.require("work", WORK)["state"] == "stopped"
            assert tx.require("requests", "steer-one")["receipt"]["state"] == "stopped"
        else:
            assert evidence["uncertain_stops"] == 1
            assert tx.require("copilot.stops", STOP)["phase"] == "outcome_uncertain"


def test_stop_on_inactive_resumed_session_confirms_via_rpc_without_idle_event(runtime):
    copilot, store, _ = runtime

    async def abort_noop():
        return None

    async def run():
        session = await copilot.session(PROJECT)
        session.abort = abort_noop
        with store.transaction() as tx:
            tx.connection.execute(
                "DELETE FROM records WHERE namespace IN ('copilot.stops') OR (namespace='requests' AND id=?)",
                (STOP,),
            )
            tx.connection.execute("DELETE FROM outbox WHERE id=?", (STOP,))
        copilot.state.submit(
            OWNER,
            {
                "schema_version": "1.0",
                "project_id": PROJECT,
                "request_id": "fresh-stop",
                "type": "stop",
                "target_work_id": WORK,
            },
        )
        await copilot.stop(PROJECT, WORK, "fresh-stop")

    asyncio.run(run())
    with store.transaction(write=False) as tx:
        assert tx.require("copilot.stops", "fresh-stop")["phase"] == "confirmed"
        assert tx.require("requests", "fresh-stop")["receipt"]["state"] == "completed"
        assert tx.require("work", WORK)["state"] == "stopped"
