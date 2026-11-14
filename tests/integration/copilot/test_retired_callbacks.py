"""M02 R1: actual retained shutdown metadata plus offline causal SDK delivery."""

import asyncio
import copy
import json
from pathlib import Path

import pytest
from proscenium.api.errors import DomainError
from proscenium.copilot.service import retired_housekeeping
from proscenium.db.store import now
from test_delivery import OWNER, PROJECT, Native, Session, context, submit
from test_delivery import runtime as runtime

CAPTURE = json.loads(Path("docs/evidence/m03/r1-terminal-event.json").read_text())
B = "project-fresh-B"


def rows(store):
    with store.transaction(write=False) as tx:
        return {
            name: [
                tuple(row) for row in tx.connection.execute(f"SELECT * FROM {name} ORDER BY 1,2")
            ]
            for name in ("records", "events", "outbox", "operations")
        }


class ShutdownNative(Native):
    def __init__(self):
        super().__init__()
        self.runtime = None
        self.by_project = {}
        self.disconnect_failure = False

    async def open(self, project_id, session_id, callbacks):
        self.opens.append((project_id, session_id))
        session = Session(self, callbacks)
        session.session_id = (
            CAPTURE["native_session_id"] if project_id == PROJECT else "native-fresh-B"
        )

        async def disconnect():
            owner = self.runtime.session_callback_owners[id(session)]
            assert owner.retired and owner.settled  # Must precede the SDK call.
            assert owner.project_id == project_id and owner.session_id == session.session_id
            callbacks["on_event"](
                copy.deepcopy(CAPTURE["event"])
                if project_id == PROJECT
                else {"id": "fixture-shutdown-B", "type": "session.shutdown", "data": {}}
            )
            if self.disconnect_failure:
                raise ConnectionError("Fixture disconnect ACK failure")

        session.disconnect = disconnect
        self.by_project[project_id] = session
        self.session = session
        return session


async def completed_a(runtime):
    native = ShutdownNative()
    native.runtime = runtime
    runtime.native = native
    context(runtime)
    submit(runtime)
    await runtime.send(PROJECT, "request-one")
    a = native.by_project[PROJECT]
    message = a.event(
        "assistant.message",
        dict(
            messageId="a-final",
            originatingMessageId="native-request-one",
            turnId="0",
            content="A completed",
        ),
    )
    a.event("assistant.turn_end", {"turnId": "0"}, parent=message)
    a.event("session.idle", parent=message)
    await runtime.settle(PROJECT, a)
    with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
        assert tx.require("requests", "request-one")["receipt"]["state"] == "completed"
    return a


def fresh_b(runtime):
    root = Path(runtime.services.registry.get(PROJECT)["path"]).parent / "fresh-B"
    root.mkdir()
    runtime.services.registry.register(
        dict(
            schema_version="1.0",
            project_id=B,
            repository_id="repo-fresh-B",
            name="Fresh B",
            path=str(root),
            native_session_id=None,
            state="inactive",
            state_revision=0,
        )
    )
    runtime.services.activate_project(B)
    runtime.contexts.capture(
        dict(
            schema_version="1.0",
            context_id="context-B",
            project_id=B,
            captured_at=now(),
            item_id=None,
            version_id=None,
            view_mode="none",
            target=None,
            camera=None,
            range=None,
            selected_objects=[],
            attachments=[],
            annotations=[],
            approved_findings=[],
        )
    )
    runtime.state.submit(
        OWNER,
        dict(
            schema_version="1.0",
            request_id="request-original-B",
            project_id=B,
            type="queue",
            text="B's original first request",
            context_id="context-B",
        ),
    )


def test_real_a_shutdown_during_disconnect_does_not_fence_fresh_b(runtime):
    async def run():
        a = await completed_a(runtime)
        a_store = runtime.services.registry.project_store(PROJECT)
        before_a = rows(a_store)
        fresh_b(runtime)
        before_opens = list(runtime.native.opens)
        await runtime.tick(B)
        assert runtime.callback_owners[PROJECT].retired
        assert runtime.callback_owners[PROJECT].failure is None
        await runtime.sends["request-original-B"]
        b = runtime.native.by_project[B]
        assert runtime.native.opens == [*before_opens, (B, None)]
        assert len(a.sent) == len(b.sent) == 1
        assert rows(a_store) == before_a
        with runtime.services.registry.project_store(B).transaction(write=False) as tx:
            assert not runtime.state.control(tx)["fenced"]
            assert (
                tx.require("requests", "request-original-B")["receipt"]["state"] == "acknowledged"
            )
            assert len(tx.all("work")) == 1
            assert all(
                e["native_session_id"] in (None, b.session_id) for e in tx.events(0, 100)["events"]
            )
            assert all(
                e["native_event_id"] != CAPTURE["event"]["id"] for e in tx.events(0, 100)["events"]
            )
        # The reducer's inactive guard remains strict. Only the exact retired callback
        # is allowed to ignore settled lifecycle metadata before reaching it.
        with pytest.raises(DomainError) as rejected:
            runtime.state.reduce(PROJECT, a.session_id, CAPTURE["event"])
        assert rejected.value.code == "scope_denied"
        await runtime.close()

    asyncio.run(run())


def test_unexpected_retired_execution_stays_scoped_and_never_writes_inactive_a_or_b(runtime):
    async def run():
        a = await completed_a(runtime)
        a_store = runtime.services.registry.project_store(PROJECT)
        before_a = rows(a_store)
        fresh_b(runtime)
        await runtime.tick(B)
        await runtime.sends["request-original-B"]
        b_store = runtime.services.registry.project_store(B)
        before_b = rows(b_store)
        unexpected = dict(
            id="unexpected-retired-tool",
            type="tool.execution_start",
            data=dict(
                toolCallId="old-native-tool",
                toolName="bash",
                arguments={"command": "DO_NOT_COPY_PRIVATE_ARGUMENTS"},
            ),
        )
        a.callbacks["on_event"](unexpected)
        owner = runtime.callback_owners[PROJECT]
        assert owner.failure == "attribution_unavailable"
        assert runtime.callback_owners[B].failure is None
        assert rows(a_store) == before_a
        assert rows(b_store) == before_b
        await runtime.tick(B)
        assert not runtime.native.closed
        with b_store.transaction(write=False) as tx:
            assert not runtime.state.control(tx)["fenced"]
        with runtime.services.registry.store.transaction(write=False) as tx:
            diagnostics = [
                r for r in tx.all("private") if r.get("callback_generation") == owner.generation
            ]
        assert len(diagnostics) == 1
        assert diagnostics[0]["project_id"] == PROJECT
        assert diagnostics[0]["native_session_id"] == CAPTURE["native_session_id"]
        assert diagnostics[0]["native_event_id"] == unexpected["id"]
        assert diagnostics[0]["retired"] and diagnostics[0]["settled_before_retirement"]
        assert "DO_NOT_COPY" not in json.dumps(diagnostics)
        await runtime.close()

    asyncio.run(run())


def test_disconnect_exception_is_not_relabelled_as_b_failure(runtime):
    async def run():
        await completed_a(runtime)
        runtime.native.disconnect_failure = True
        fresh_b(runtime)
        await runtime.tick(B)
        await runtime.sends["request-original-B"]
        assert runtime.callback_owners[PROJECT].failure == "provider_unavailable"
        assert runtime.callback_owners[B].failure is None
        with runtime.services.registry.project_store(B).transaction(write=False) as tx:
            assert (
                tx.require("requests", "request-original-B")["receipt"]["state"] == "acknowledged"
            )
            assert not runtime.state.control(tx)["fenced"]
        await runtime.close()

    asyncio.run(run())


def test_retired_permission_input_and_prompt_callbacks_cannot_authorise_inactive_work(runtime):
    async def run():
        a = await completed_a(runtime)
        a_store = runtime.services.registry.project_store(PROJECT)
        fresh_b(runtime)
        await runtime.tick(B)
        await runtime.sends["request-original-B"]
        before_a = rows(a_store)
        before_b = rows(runtime.services.registry.project_store(B))
        permission = await a.callbacks["on_permission_request"](
            {"kind": "shell"}, {"session_id": a.session_id}
        )
        assert permission.to_dict()["kind"] == "reject"
        with pytest.raises(DomainError) as question:
            await a.callbacks["on_user_input_request"](
                {"requestId": "retired-question"}, {"session_id": a.session_id}
            )
        assert question.value.code == "stale_decision"
        with pytest.raises(DomainError) as prompt:
            a.callbacks["hooks"]["on_user_prompt_submitted"]({"agentId": "retired-child"}, {})
        assert prompt.value.code == "permission_denied"
        assert rows(a_store) == before_a
        assert rows(runtime.services.registry.project_store(B)) == before_b
        await runtime.close()

    asyncio.run(run())


def test_retired_registration_cannot_poison_a_new_registration_of_the_same_session(runtime):
    async def run():
        old_a = await completed_a(runtime)
        old_owner = runtime.callback_owners[PROJECT]
        fresh_b(runtime)
        await runtime.tick(B)
        await runtime.sends["request-original-B"]
        b = runtime.native.by_project[B]
        message = b.event(
            "assistant.message",
            dict(
                messageId="b-final",
                originatingMessageId="native-request-original-B",
                turnId="0",
                content="B completed",
            ),
        )
        b.event("assistant.turn_end", {"turnId": "0"}, parent=message)
        b.event("session.idle", parent=message)
        await runtime.settle(B, b)
        runtime.services.activate_project(PROJECT)
        await runtime.session(PROJECT)  # Non-continuing load; never sends another prompt.
        new_owner = runtime.callback_owners[PROJECT]
        assert new_owner.generation != old_owner.generation
        assert new_owner.session_id == old_owner.session_id
        before = rows(runtime.services.registry.project_store(PROJECT))
        old_a.callbacks["on_event"](copy.deepcopy(CAPTURE["event"]))
        old_a.callbacks["on_event"]({"id": "retired-error", "type": "session.error", "data": {}})
        assert new_owner.failure is None
        assert old_owner.failure == "attribution_unavailable"
        assert rows(runtime.services.registry.project_store(PROJECT)) == before
        await runtime.tick(PROJECT)
        assert not runtime.native.closed
        assert len(old_a.sent) == len(b.sent) == 1
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "event,expected",
    [
        (CAPTURE["event"], True),
        ({"type": "hook.start", "data": {"hookType": "sessionEnd"}}, True),
        ({"type": "hook.end", "data": {"hookType": "preToolUse"}}, False),
        ({"type": "session.error", "data": {}}, False),
        ({"type": "session.task_complete", "data": {}}, False),
        ({"type": "assistant.message", "data": {}}, False),
        ({"type": "assistant.turn_start", "data": {}}, False),
        ({"type": "tool.execution_start", "data": {}}, False),
    ],
)
def test_retirement_filter_is_narrow(event, expected):
    assert retired_housekeeping(event) is expected
