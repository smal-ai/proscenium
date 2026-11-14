"""Deterministic heartbeat/backpressure and conservative coordination recovery."""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from proscenium.copilot.launcher import identity
from proscenium.copilot.native import Native
from test_delivery import PROJECT, context, submit
from test_delivery import runtime as runtime


async def setup(runtime):
    context(runtime)
    submit(runtime)
    await runtime.send(PROJECT, "request-one")
    return runtime.native.session


@pytest.mark.parametrize(
    "case,calls",
    [
        ("progress", 2),
        ("no_progress", 1),
        ("dead", 1),
        ("closed", 1),
        ("unattributed", 1),
        ("twice", 2),
        ("duplicate", 1),
    ],
)
def test_bounded_heartbeat_retry_requires_verified_progress_and_live_transport(
    runtime, case, calls
):
    async def run():
        session = await setup(runtime)
        duplicate = dict(
            id="old-progress",
            type="assistant.message_delta",
            data=dict(
                messageId="probe-message",
                originatingMessageId="native-request-one",
                deltaContent="Already received",
            ),
        )
        if case == "duplicate":
            session.callbacks["on_event"](duplicate)
            runtime.callback_owners[PROJECT].progress_at = 0
        count = 0

        async def health():
            nonlocal count
            count += 1
            if count == 1:
                if case == "duplicate":
                    session.callbacks["on_event"](duplicate)
                elif case not in {"no_progress"}:
                    session.event(
                        "assistant.message_delta",
                        dict(
                            messageId="probe-message",
                            originatingMessageId="unknown"
                            if case == "unattributed"
                            else "native-request-one",
                            deltaContent="Verified progress",
                        ),
                    )
                raise TimeoutError("No private payload may be retained")
            if case == "twice":
                raise TimeoutError()

        runtime.native.health = health
        runtime.native.transport_alive = lambda: case not in {"dead", "closed"}
        if case == "progress":
            await runtime.check_health(PROJECT)
        else:
            with pytest.raises(TimeoutError):
                await runtime.check_health(PROJECT)
        assert count == calls
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            diagnostics = tx.all("copilot.runtime_errors")
            assert diagnostics[0]["exception_type"] == "TimeoutError"
            assert "private payload" not in json.dumps(diagnostics)
        await runtime.close()

    asyncio.run(run())


def test_unexpected_python_failure_is_not_classified_as_provider_outage(runtime):
    async def run():
        async def tick(project_id):
            raise ValueError("secret test string must not persist")

        codes = []

        async def failed(project_id, code):
            codes.append(code)
            runtime.closing = True

        runtime.tick = tick
        runtime.failed = failed
        await runtime.run()
        assert codes == ["outcome_uncertain"]
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            row = tx.all("copilot.runtime_errors")[0]
            assert row["boundary"] == "tick" and row["exception_type"] == "ValueError"
            assert "secret" not in json.dumps(row)

    asyncio.run(run())


def test_transport_alive_checks_exact_process_and_closed_pipe(runtime):
    native = object.__new__(Native)
    native.services = runtime.services
    process = SimpleNamespace(pid=os.getpid(), returncode=None, stdin=SimpleNamespace(closed=False))
    native.client = SimpleNamespace(_state="connected", _process=process)
    path = runtime.services.settings.data_directory / "copilot-runtime/process.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(pid=os.getpid(), start=identity(os.getpid()))))
    assert native.transport_alive()
    process.stdin.closed = True
    assert not native.transport_alive()
    process.stdin.closed = False
    process.returncode = 1
    assert not native.transport_alive()
    process.returncode = None
    native.client._state = "disconnected"
    assert not native.transport_alive()


@pytest.mark.parametrize(
    "kind,valid",
    [
        ("ask_user", True),
        ("task", True),
        ("bash", False),
        ("unknown", False),
        ("live_owner", False),
        ("missing_child_terminal", False),
        ("wrong_session", False),
        ("ephemeral_input_event", True),
    ],
)
def test_only_exact_departed_coordinators_can_be_interrupted(runtime, kind, valid):
    async def run():
        session = await setup(runtime)
        name = (
            "task"
            if kind in {"task", "missing_child_terminal"}
            else "ask_user"
            if kind in {"ask_user", "live_owner", "wrong_session", "ephemeral_input_event"}
            else kind
        )
        session.event(
            "tool.execution_start",
            dict(toolCallId="coord-tool", toolName=name),
            parent=session.last_event,
        )
        history = []
        if name == "task":
            e = dict(
                id="child-start",
                type="subagent.started",
                agentId="child-one",
                data=dict(toolCallId="coord-tool", agentName="authoring-specialist"),
            )
            runtime.state.reduce(PROJECT, session.session_id, e)
            history.append(e)
            if kind != "missing_child_terminal":
                e = dict(
                    id="child-end",
                    type="subagent.completed",
                    agentId="child-one",
                    data=dict(toolCallId="coord-tool", cancelled=True),
                )
                runtime.state.reduce(PROJECT, session.session_id, e)
                history.append(e)
        if name == "ask_user":
            event = dict(
                id="input-request",
                type="user_input.requested",
                data=dict(toolCallId="coord-tool", requestId="input-native"),
            )
            runtime.state.reduce(PROJECT, session.session_id, event)
            history.append(event)
            if kind == "ephemeral_input_event":
                history.clear()  # SDK history omits this actual live notification.
            callback = asyncio.create_task(
                runtime.user_input(
                    PROJECT,
                    dict(requestId="input-native", question="Test coordination", choices=["No"]),
                    dict(session_id=session.session_id),
                )
            )
            await asyncio.sleep(0.01)
        proof = dict(
            pid=123,
            start="old-start",
            observed_identity="old-start" if kind == "live_owner" else None,
            session_id="wrong" if kind == "wrong_session" else session.session_id,
        )
        with runtime.services.registry.project_store(PROJECT).transaction() as tx:
            proof["invocation_ids"] = [i["invocation_id"] for i in tx.all("invocations")]
            runtime.state.interrupt_coordinators(tx, session.session_id, history, proof)
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            invocation = next(
                i for i in tx.all("invocations") if i.get("native_id") == "coord-tool"
            )
            assert (invocation["state"] == "cancelled") == valid
            if valid:
                assert invocation["recovery"]["native_return_observed"] is False
            for d in tx.all("decisions"):
                assert d["response"] is None and d["resolved_by"] is None
                assert d["delivery_state"] == "not_sent"
        if name == "ask_user":
            if valid:
                assert (await callback)["answer"] == "Cancelled by owner control"
            else:
                callback.cancel()
                await asyncio.gather(callback, return_exceptions=True)
        await runtime.close()

    asyncio.run(run())
