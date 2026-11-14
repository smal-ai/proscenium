"""Synthetic faults exercise the production SQLite reducer and independent scheduler."""

import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from proscenium.api.errors import DomainError
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.copilot import install, router
from proscenium.copilot.state import key
from proscenium.db.store import now

PROJECT = "project-test"
OWNER = Caller("owner", "owner-test")


class Session:
    session_id = "synthetic-production-session"

    def __init__(self, native, callbacks):
        self.native, self.callbacks = native, callbacks
        self.sent = []
        self.aborts = 0
        self.before_ack = None
        self.lose_ack = False
        self.release = None

    def event(self, kind, data=None, *, parent=None, agent=None, event_id=None):
        event = dict(id=event_id or uuid4().hex, type=kind, timestamp=now(), data=data or {})
        if parent:
            event["parentId"] = parent
        if agent:
            event["agentId"] = agent
        self.callbacks["on_event"](event)
        self.last_event = event["id"]
        return event["id"]

    async def send(self, text, **kwargs):
        identity = "native-" + kwargs["client_turn_id"]
        self.sent.append((text, kwargs))
        event = self.event(
            "user.message",
            dict(messageId=identity, content=text, clientTurnId=kwargs["client_turn_id"]),
        )
        if self.before_ack:
            self.before_ack(self, event, identity)
        if self.release:
            await self.release.wait()
        if self.lose_ack:
            raise ConnectionError("Synthetic lost acknowledgement")
        return identity

    async def abort(self):
        self.aborts += 1
        self.native.pending_state = dict(items=[], steeringMessages=[], inFlightSteeringCount=0)
        self.event("session.idle", parent=self.last_event)


class Native:
    def __init__(self):
        self.opens = []
        self.closed = False
        self.paused = False
        self.pauses = []
        self.lose_pause = False
        self.pending_state = dict(items=[], steeringMessages=[], inFlightSteeringCount=0)

    async def open(self, project_id, session_id, callbacks):
        self.opens.append((project_id, session_id))
        self.session = Session(self, callbacks)
        return self.session

    async def pending(self, session):
        return copy.deepcopy(self.pending_state)

    async def pause(self, session, paused):
        self.pauses.append(paused)
        if paused and self.paused:
            raise RuntimeError("queue_already_paused")
        self.paused = paused
        if self.lose_pause:
            raise ConnectionError("Synthetic lost pause ACK")

    async def withdraw(self, session, text, message_id):
        for item in self.pending_state["items"]:
            if item["messageId"] == message_id and item["displayText"] == text:
                self.pending_state["items"].remove(item)
                return {"removed": True, "interrupted": False}
        return {"removed": False, "interrupted": False}

    async def health(self):
        pass

    async def close(self):
        self.closed = True


@pytest.fixture
def runtime(services):
    with services.registry.project_store(PROJECT).transaction() as tx:
        work = tx.require("work", "work-test")
        work.update(state="completed", state_revision=1)
        tx.put("work", "work-test", work, expected_revision=0, schema="WorkRecord")
    return install(services, native=Native())


def context(runtime, **fields):
    value = json.loads(Path("docs/contracts/examples/context.json").read_text())
    value.update(project_id=PROJECT, context_id="context-test", **fields)
    runtime.contexts.capture(value)
    return value


def submit(runtime, identity="request-one", kind="queue", **fields):
    body = dict(schema_version="1.0", project_id=PROJECT, request_id=identity, type=kind, **fields)
    if kind != "stop":
        body.update(text="Original human request", context_id="context-test")
    return runtime.state.submit(OWNER, body)[0]


def record(runtime, identity="request-one"):
    with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
        return tx.require("requests", identity)


def test_immutable_admission_replay_conflict_and_no_execution(runtime):
    context(runtime)
    original = submit(runtime)
    assert runtime.native.opens == []
    assert original["state"] == "held"
    assert record(runtime)["submission"]["text"] == "Original human request"
    assert submit(runtime)["replayed"]
    with pytest.raises(DomainError, match="idempotency conflict"):
        runtime.state.submit(OWNER, {**record(runtime)["submission"], "text": "Changed"})


def test_native_events_before_ack_and_duplicate_user_echo(runtime):
    async def run():
        context(runtime)
        submit(runtime)
        session = await runtime.session(PROJECT, allow_create=True)
        captured = {}

        def before_ack(session, parent, message_id):
            assistant = session.event(
                "assistant.message",
                dict(
                    messageId="assistant-one",
                    originatingMessageId=message_id,
                    turnId="0",
                    content="Done",
                ),
                parent=parent,
                event_id="early-assistant",
            )
            session.event("assistant.turn_end", {"turnId": "0"}, parent=assistant)
            session.event("session.idle", parent=assistant)
            captured["event"] = assistant

        session.before_ack = before_ack
        await runtime.send(PROJECT, "request-one")
        await runtime.settle(PROJECT, session)
        assert record(runtime)["receipt"]["state"] == "completed"
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            events = tx.events(0, 100)["events"]
            assert len([e for e in events if e["event_type"] == "request.accepted"]) == 1
            assert len([e for e in events if e["event_type"] == "assistant.completed"]) == 1
            assert tx.require("copilot.sends", "request-one")["state"] == "acknowledged"
        assert session.sent[0][0] == session.sent[0][1]["display_prompt"]
        assert session.sent[0][1]["attachments"][-1]["type"] == "extension_context"
        await runtime.close()

    asyncio.run(run())


def test_lost_ack_closes_gate_and_never_blind_resubmits(runtime):
    async def run():
        context(runtime)
        submit(runtime)
        session = await runtime.session(PROJECT, allow_create=True)
        session.lose_ack = True
        await runtime.send(PROJECT, "request-one")
        submit(runtime, "request-two")
        await runtime.tick(PROJECT)
        assert record(runtime)["receipt"]["state"] == "outcome_uncertain"
        assert len(session.sent) == 1
        assert record(runtime, "request-two")["receipt"]["state"] == "held"
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("recovery_state", ["awaiting_resume", "outcome_uncertain"])
def test_recovery_stop_records_control_without_an_illegal_work_edge(runtime, recovery_state):
    async def run():
        context(runtime)
        submit(runtime)
        session = await runtime.session(PROJECT, allow_create=True)
        await runtime.send(PROJECT, "request-one")
        work_id = record(runtime)["receipt"]["work_id"]
        store = runtime.services.registry.project_store(PROJECT)
        with store.transaction() as tx:
            runtime.state.work_state(tx, work_id, recovery_state)
            runtime.state.fence(tx, "awaiting_resume")
        accepted = submit(runtime, "recovery-stop", "stop", target_work_id=work_id)
        assert accepted["state"] == "stop_requested"
        with store.transaction(write=False) as tx:
            assert tx.require("work", work_id)["state"] == recovery_state
            assert tx.require("copilot.stops", "recovery-stop")["phase"] == "admitted"
        assert session.aborts == 0 and len(session.sent) == 1
        await runtime.stop(PROJECT, work_id, "recovery-stop")
        assert record(runtime, "recovery-stop")["receipt"]["state"] == "completed"
        assert session.aborts == 1 and len(session.sent) == 1
        with store.transaction(write=False) as tx:
            assert tx.require("work", work_id)["state"] == "stopped"
            assert tx.require("copilot.stops", "recovery-stop")["pending_empty"]
            assert runtime.state.control(tx)["fenced"]
        await runtime.close()

    asyncio.run(run())


def test_recovery_stop_does_not_fabricate_an_unfinished_tool_outcome(runtime, monkeypatch):
    original_sleep = asyncio.sleep

    async def scheduler_tick(_delay):
        await original_sleep(0)

    async def run():
        context(runtime)
        submit(runtime)
        session = await runtime.session(PROJECT, allow_create=True)
        await runtime.send(PROJECT, "request-one")
        work_id = record(runtime)["receipt"]["work_id"]
        store = runtime.services.registry.project_store(PROJECT)
        with store.transaction() as tx:
            runtime.state.work_state(tx, work_id, "awaiting_resume")
            runtime.state.fence(tx, "awaiting_resume")
            tx.put(
                "invocations",
                "unfinished-native-tool",
                {"work_id": work_id, "state": "outcome_uncertain"},
            )
        submit(runtime, "recovery-stop", "stop", target_work_id=work_id)
        monkeypatch.setattr(asyncio, "sleep", scheduler_tick)
        await runtime.stop(PROJECT, work_id, "recovery-stop")
        assert record(runtime, "recovery-stop")["receipt"]["state"] == "outcome_uncertain"
        with store.transaction(write=False) as tx:
            assert tx.require("work", work_id)["state"] == "awaiting_resume"
            assert tx.require("invocations", "unfinished-native-tool") == {
                "work_id": work_id,
                "state": "outcome_uncertain",
            }
            assert runtime.state.control(tx)["fenced"]
        assert session.aborts == 1 and runtime.native.closed
        await runtime.close()

    asyncio.run(run())


def test_scheduler_survives_request_lifetime_and_fifo(runtime):
    async def run():
        await runtime.start()
        context(runtime)
        submit(runtime, "z-first")
        submit(runtime, "a-second")
        runtime.notify()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if record(runtime, "z-first")["receipt"]["state"] == "acknowledged":
                break
        assert record(runtime, "z-first")["receipt"]["state"] == "acknowledged"
        assert record(runtime, "a-second")["receipt"]["state"] == "held"
        assert len(runtime.native.session.sent) == 1
        await runtime.close()

    asyncio.run(run())


def test_child_context_and_late_tools_bind_original_work(runtime):
    async def run():
        context(runtime)
        root = submit(runtime)
        await runtime.send(PROJECT, "request-one")
        session = runtime.native.session
        assistant = session.event(
            "assistant.message",
            dict(
                messageId="assistant-one",
                originatingMessageId="native-request-one",
                turnId="0",
                content="",
            ),
        )
        tool = session.event(
            "tool.execution_start",
            dict(toolCallId="delegate", toolName="task", turnId="0"),
            parent=assistant,
        )
        session.event(
            "subagent.started",
            dict(toolCallId="delegate", agentName="worker", model="gpt-6.1-sol"),
            parent=tool,
            agent="child-one",
        )
        payload = session.callbacks["hooks"]["on_user_prompt_submitted"](
            {"agentId": "child-one"}, {}
        )
        assert json.loads(payload["additionalContext"])["request_id"] == "request-one"
        assert session.callbacks["hooks"]["on_user_prompt_submitted"]({}, {}) == {}
        with pytest.raises(DomainError):
            session.callbacks["hooks"]["on_user_prompt_submitted"]({"agentId": "unbound"}, {})
        submit(runtime, "steer-one", "steer", target_work_id=root["work_id"])
        session.event("assistant.turn_end", {"turnId": "0"}, parent=assistant)
        session.event("session.idle", parent=assistant)
        await runtime.settle(PROJECT, session)
        assert record(runtime)["receipt"]["state"] != "completed"
        child = session.event(
            "assistant.message",
            dict(
                messageId="child-message",
                originatingMessageId="child-user",
                parentToolCallId="delegate",
                turnId="0",
                content="Child",
            ),
            agent="child-one",
        )
        session.event(
            "tool.execution_start",
            dict(toolCallId="child-tool", toolName="view", turnId="0"),
            parent=child,
            agent="child-one",
        )
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            invocation = tx.require("invocations", key(session.session_id, "tool", "child-tool"))
            assert invocation["request_id"] == "request-one"
            assert invocation["work_id"] == root["work_id"]
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("lose_pause", [False, True])
def test_stop_pause_fence_late_ack_and_no_retry(runtime, lose_pause):
    async def run():
        context(runtime)
        root = submit(runtime)
        await runtime.send(PROJECT, "request-one")
        submit(runtime, "held-other")
        session = runtime.native.session
        session.release = asyncio.Event()
        submit(runtime, "steer-one", "steer", target_work_id=root["work_id"])
        runtime.launch_send(PROJECT, "steer-one")
        await asyncio.sleep(0.01)
        runtime.native.lose_pause = lose_pause
        submit(runtime, "stop-one", "stop", target_work_id=root["work_id"])
        with pytest.raises(DomainError, match="no active work"):
            submit(runtime, "late-steer", "steer", target_work_id=root["work_id"])
        stopping = asyncio.create_task(runtime.stop(PROJECT, root["work_id"], "stop-one"))
        await asyncio.sleep(0.01)
        assert session.aborts == 0  # Pause, then settle the in-flight Send, THEN our one Abort.
        session.release.set()
        await stopping
        assert record(runtime, "held-other")["receipt"]["state"] == "held"
        assert runtime.native.pauses == [True]
        assert session.aborts == (0 if lose_pause else 1)
        assert record(runtime, "stop-one")["receipt"]["state"] == (
            "outcome_uncertain" if lose_pause else "completed"
        )
        await runtime.tick(PROJECT)
        assert runtime.native.pauses == [True]
        await runtime.close()

    asyncio.run(run())


def test_decision_revision_owner_channel_stop_and_native_delivery_separate(runtime):
    async def run():
        context(runtime)
        root = submit(runtime)
        await runtime.send(PROJECT, "request-one")
        binding = dict(request_id="request-one", work_id=root["work_id"], invocation_id=None)
        task = asyncio.create_task(
            runtime.decision(
                PROJECT,
                runtime.native.session.session_id,
                "native-decision",
                binding,
                "user_input",
                "Which colour?",
                ["red", "blue"],
            )
        )
        await asyncio.sleep(0.01)
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            decision = tx.all("decisions")[0]
        answer = dict(
            schema_version="1.0",
            project_id=PROJECT,
            operation_id="answer-one",
            expected_revision=0,
            fingerprint=decision["fingerprint"],
            action="answer",
            answer="red",
        )
        with pytest.raises(DomainError, match="owner session required"):
            runtime.state.answer(Caller("tool", "model"), decision["decision_id"], answer)
        runtime.state.answer(OWNER, decision["decision_id"], answer)
        assert (await task)["answer"] == "red"
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            assert (
                tx.require("decisions", decision["decision_id"])["delivery_state"]
                == "outcome_uncertain"
            )
        with pytest.raises(DomainError, match="stale decision"):
            runtime.state.answer(
                OWNER, decision["decision_id"], {**answer, "operation_id": "answer-two"}
            )
        await runtime.close()

    asyncio.run(run())


def test_restart_is_quiet_and_existing_work_awaits_resume(runtime):
    async def run():
        context(runtime)
        submit(runtime)
        await runtime.send(PROJECT, "request-one")
        await runtime.close()
        from proscenium.copilot.service import Copilot

        other = Copilot(runtime.services, native=Native())
        await other.start()
        await asyncio.sleep(0.01)
        assert other.native.opens == []
        with other.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            assert (
                tx.require("work", record(other)["receipt"]["work_id"])["state"]
                == "awaiting_resume"
            )
        await other.close()

    asyncio.run(run())


def test_http_history_cancel_replay_and_socket_owner_boundary(runtime):
    app = create_app(runtime.services.settings, services=runtime.services, routers=[router])
    with TestClient(app, base_url=runtime.services.settings.origin) as client:
        pair = dict(
            schema_version="1.0",
            operation_id="pair-w01",
            client_nonce="owner-browser",
            pairing_code=runtime.services.auth.issue_pairing(),
        )
        response = client.post(
            "/api/v1/owner/session",
            json=pair,
            headers={"Origin": runtime.services.settings.origin, "Idempotency-Key": "pair-w01"},
        )
        headers = {
            "Origin": runtime.services.settings.origin,
            "X-CSRF-Token": response.json()["csrf_token"],
        }
        value = context(runtime)
        response = client.post(
            f"/api/v1/projects/{PROJECT}/contexts",
            json=value,
            headers={**headers, "Idempotency-Key": value["context_id"]},
        )
        assert response.status_code == 200
        submit(runtime)
        response = client.get(f"/api/v1/projects/{PROJECT}/requests")
        assert response.json()["items"][0]["submission"]["text"] == "Original human request"
        command = dict(
            schema_version="1.0", project_id=PROJECT, operation_id="cancel-one", expected_revision=0
        )
        response = client.post(
            f"/api/v1/projects/{PROJECT}/requests/request-one/cancel",
            json=command,
            headers={**headers, "Idempotency-Key": command["operation_id"]},
        )
        assert response.status_code == 202
        with client.websocket_connect(
            runtime.services.settings.origin.replace("http", "ws") + "/api/v1/events",
            headers={"Origin": runtime.services.settings.origin},
        ) as socket:
            socket.send_json(
                dict(schema_version="1.0", type="subscribe", project_id=PROJECT, after_sequence=0)
            )
            assert socket.receive_json()["type"] == "welcome"
            sequences = []
            while True:
                event = socket.receive_json()
                if event.get("type") == "caught_up":
                    break
                sequences.append(event["sequence"])
            assert sequences == list(range(1, len(sequences) + 1))
        assert client.get(f"/api/v1/projects/{PROJECT}/events?after=999999").status_code == 410
        assert runtime.native.opens == []
