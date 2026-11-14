"""Captured M10 empty tool batch is not evidence that the full request finished."""

import asyncio
import json
import subprocess

import pytest
from test_delivery import PROJECT, context, record, submit
from test_delivery import runtime as runtime

CAPTURE = json.loads(
    subprocess.check_output(
        [
            "git",
            "show",
            "e18310c6872c166a6a7b3989730d5f3f23e85ca1:docs/evidence/m10-continuation/final/native-delta-redacted.json",
        ]
    )
)["events"]


@pytest.mark.parametrize("terminal", ["none", "public_final", "task_complete"])
def test_captured_empty_batch_and_turn_end_require_a_real_terminal_producer(runtime, terminal):
    async def run():
        context(runtime)
        submit(runtime)
        await runtime.send(PROJECT, "request-one")
        session = runtime.native.session
        session.event(
            "assistant.turn_start", {"turnId": "captured-turn"}, parent=session.last_event
        )
        empty = next(e for e in CAPTURE if e["type"] == "assistant.message")
        assert empty["public_content_empty"] and empty["tool_requests"] == 2
        parent = session.event(
            "assistant.message",
            dict(
                messageId="empty-captured-batch",
                originatingMessageId="native-request-one",
                turnId="captured-turn",
                content="",
                toolRequests=[{"toolCallId": "view-call"}, {"toolCallId": "status-call"}],
            ),
            parent=session.last_event,
        )
        for event in CAPTURE:
            if (
                event["type"] in {"tool.execution_start", "tool.execution_complete"}
                and event.get("data", {}).get("toolCallId") != "call_Yo8Pyi1HvNYCI5E6CSQxCamx"
            ):
                session.event(event["type"], event["data"], parent=parent, event_id=event["id"])
        session.event("assistant.turn_end", {"turnId": "captured-turn"}, parent=session.last_event)
        session.event("session.idle", parent=session.last_event)
        await runtime.settle(PROJECT, session)
        assert record(runtime)["receipt"]["state"] != "completed"
        if terminal == "public_final":
            session.event(
                "assistant.turn_start", {"turnId": "final-turn"}, parent=session.last_event
            )
            session.event(
                "assistant.message",
                dict(
                    messageId="real-final-message",
                    originatingMessageId="native-request-one",
                    turnId="final-turn",
                    content="Actual final response after inspecting the failed operation.",
                ),
                parent=session.last_event,
            )
            session.event("assistant.turn_end", {"turnId": "final-turn"}, parent=session.last_event)
        elif terminal == "task_complete":
            session.event("session.task_complete", {}, parent=session.last_event)
        if terminal != "none":
            session.event("session.idle", parent=session.last_event)
            await runtime.settle(PROJECT, session)
            assert record(runtime)["receipt"]["state"] == "completed"
        await runtime.close()

    asyncio.run(run())
