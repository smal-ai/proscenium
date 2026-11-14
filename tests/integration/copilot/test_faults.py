import asyncio
import json

import pytest
from copilot.tools import ToolInvocation
from proscenium.api.errors import DomainError
from proscenium.copilot.state import key, save
from proscenium.copilot.tools import Tools
from test_delivery import OWNER, PROJECT, context, record, submit
from test_delivery import runtime as runtime


def test_out_of_order_before_echo_joins_on_ack_without_duplicate_user(runtime):
    async def run():
        context(runtime)
        root = submit(runtime)
        session = await runtime.session(PROJECT, allow_create=True)
        session.event(
            "assistant.message",
            dict(
                messageId="early-message",
                originatingMessageId="native-request-one",
                turnId="0",
                content="Early complete text",
            ),
            event_id="early-event",
        )
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            assert not tx.all("copilot.pending_events")[0]["resolved"]
        await runtime.send(PROJECT, "request-one")
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            assert tx.all("copilot.pending_events")[0]["resolved"]
            event = [
                e for e in tx.events(0, 100)["events"] if e["event_type"] == "assistant.completed"
            ][0]
            assert event["work_id"] == root["work_id"]
            assert event["payload"]["text"] == "Early complete text"
        await runtime.close()

    asyncio.run(run())


def test_turn_ids_reused_by_later_root_do_not_retag_tools(runtime):
    async def run():
        context(runtime)
        first = submit(runtime)
        await runtime.send(PROJECT, "request-one")
        session = runtime.native.session
        first_message = session.event(
            "assistant.message",
            dict(
                messageId="first-assistant",
                originatingMessageId="native-request-one",
                turnId="0",
                content="First",
            ),
        )
        session.event("assistant.turn_end", {"turnId": "0"}, parent=first_message)
        session.event("session.idle", parent=first_message)
        await runtime.settle(PROJECT, session)
        second = submit(runtime, "request-two")
        await runtime.send(PROJECT, "request-two")
        second_message = session.event(
            "assistant.message",
            dict(
                messageId="second-assistant",
                originatingMessageId="native-request-two",
                turnId="0",
                content="Second",
            ),
        )
        session.event(
            "tool.execution_start",
            dict(toolCallId="second-tool", toolName="bash", turnId="0"),
            parent=second_message,
        )
        session.event(
            "tool.execution_start",
            dict(toolCallId="late-first-tool", toolName="view", turnId="0"),
            parent=first_message,
        )
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            assert (
                tx.require("invocations", key(session.session_id, "tool", "second-tool"))["work_id"]
                == second["work_id"]
            )
            assert (
                tx.require("invocations", key(session.session_id, "tool", "late-first-tool"))[
                    "work_id"
                ]
                == first["work_id"]
            )
        await runtime.close()

    asyncio.run(run())


def test_duplicate_native_identity_conflict_is_fenced(runtime):
    async def run():
        context(runtime)
        submit(runtime)
        await runtime.send(PROJECT, "request-one")
        session = runtime.native.session
        message = dict(
            messageId="output",
            originatingMessageId="native-request-one",
            turnId="0",
            content="Original",
        )
        session.event("assistant.message", message, event_id="duplicate")
        session.event(
            "assistant.message", {**message, "content": "Conflicting"}, event_id="duplicate"
        )
        assert runtime.failure == "attribution_unavailable"
        await runtime.tick(PROJECT)
        assert runtime.native.closed
        assert record(runtime)["receipt"]["state"] == "outcome_uncertain"
        await runtime.close()

    asyncio.run(run())


def test_model_tools_resolve_extensions_on_invocation_and_do_not_leak_findings(runtime):
    async def run():
        context(runtime)
        submit(runtime)
        await runtime.send(PROJECT, "request-one")
        session = runtime.native.session
        parent = session.event(
            "assistant.message",
            dict(
                messageId="tool-assistant",
                originatingMessageId="native-request-one",
                turnId="0",
                content="",
            ),
        )
        session.event(
            "tool.execution_start",
            dict(toolCallId="source-tool", toolName="checkpoint_sources", turnId="0"),
            parent=parent,
        )
        tools = Tools(runtime.services, runtime.state, PROJECT)
        command = dict(
            schema_version="1.0",
            operation_id="checkpoint-op",
            project_id=PROJECT,
            paths=["scene.blend"],
            message="Owned changes only",
            expected_head=None,
        )
        invocation = ToolInvocation(
            session_id=session.session_id,
            tool_call_id="source-tool",
            tool_name="checkpoint_sources",
            arguments=command,
        )
        result = await tools.invoke("checkpoint_sources", invocation)
        assert json.loads(result.text_result_for_llm)["error"] == "capability_unavailable"

        class Sources:
            def checkpoint(self, caller, body):
                assert caller.work_id == record(runtime)["receipt"]["work_id"]
                assert caller.invocation_id == key(session.session_id, "tool", "source-tool")
                raise DomainError("source_conflict", 409)

        runtime.services.extensions["sources"] = Sources()
        result = await tools.invoke("checkpoint_sources", invocation)
        assert json.loads(result.text_result_for_llm)["error"] == "source_conflict"
        session.event(
            "tool.execution_start",
            dict(toolCallId="review-tool", toolName="request_vlm_review", turnId="0"),
            parent=parent,
        )
        from test_contexts import image

        target, _ = image(runtime, "review-target")
        review_command = dict(
            schema_version="1.0",
            review_id="review-one",
            project_id=PROJECT,
            targets=[target],
            criteria="Read only",
            telemetry_artifact_ids=[],
        )

        class Reviews:
            def request(self, caller, body):
                return {"findings": [{"text": "UNAPPROVED SECRET CRITIQUE"}]}

        runtime.services.extensions["vlm"] = Reviews()
        review_invocation = ToolInvocation(
            session_id=session.session_id,
            tool_call_id="review-tool",
            tool_name="request_vlm_review",
            arguments=review_command,
        )
        result = await tools.invoke("request_vlm_review", review_invocation)
        assert "UNAPPROVED" not in result.text_result_for_llm
        assert json.loads(result.text_result_for_llm)["error"] == "invalid_request"
        await runtime.close()

    asyncio.run(run())


def test_child_input_joins_native_request_and_stop_invalidates_late_answer(runtime):
    async def run():
        context(runtime)
        root = submit(runtime)
        await runtime.send(PROJECT, "request-one")
        session = runtime.native.session
        parent = session.event(
            "assistant.message",
            dict(
                messageId="assistant",
                originatingMessageId="native-request-one",
                turnId="0",
                content="",
            ),
        )
        delegate = session.event(
            "tool.execution_start",
            dict(toolCallId="delegate", toolName="task", turnId="0"),
            parent=parent,
        )
        session.event(
            "subagent.started",
            dict(toolCallId="delegate", agentName="child"),
            parent=delegate,
            agent="native-child",
        )
        question_tool = session.event(
            "tool.execution_start",
            dict(toolCallId="ask-call", toolName="ask_user", turnId="0"),
            parent=delegate,
            agent="native-child",
        )
        session.event(
            "user_input.requested",
            dict(requestId="native-question", toolCallId="ask-call", question="Which shot?"),
            parent=question_tool,
            agent="native-child",
        )
        callback = asyncio.create_task(
            runtime.user_input(
                PROJECT,
                {
                    "requestId": "native-question",
                    "question": "Which shot?",
                    "choices": ["wide", "close"],
                },
                {"session_id": session.session_id},
            )
        )
        await asyncio.sleep(0.01)
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            decision = tx.all("decisions")[0]
            assert decision["invocation_id"] == key(session.session_id, "tool", "ask-call")
            assert decision["work_id"] == root["work_id"]
        submit(runtime, "stop-input", "stop", target_work_id=root["work_id"])
        assert (await callback)["answer"] == "Cancelled by owner control"
        answer = dict(
            schema_version="1.0",
            operation_id="late-answer",
            project_id=PROJECT,
            expected_revision=0,
            fingerprint=decision["fingerprint"],
            action="answer",
            answer="wide",
        )
        with pytest.raises(DomainError) as caught:
            runtime.state.answer(OWNER, decision["decision_id"], answer)
        assert caught.value.code == "stale_decision"
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["reconcile_only", "release_held_queue", "continue_work"])
def test_resume_modes_do_not_release_unrelated_queue(runtime, mode):
    async def run():
        context(runtime)
        root = submit(runtime)
        await runtime.send(PROJECT, "request-one")
        submit(runtime, "unrelated-held")
        submit(runtime, "stop-one", "stop", target_work_id=root["work_id"])
        await runtime.stop(PROJECT, root["work_id"], "stop-one")
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            work = tx.require("work", root["work_id"])
        command = dict(
            schema_version="1.0",
            project_id=PROJECT,
            operation_id="resume-one",
            expected_revision=work["state_revision"],
            mode=mode,
            work_id=root["work_id"],
        )
        runtime.admit_resume(OWNER, command)
        await runtime.resume(PROJECT, command["operation_id"])
        assert record(runtime, "unrelated-held")["receipt"]["state"] == "held"
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            control = runtime.state.control(tx)
            if mode == "reconcile_only":
                assert control["fenced"]
            else:
                assert control["queue_released"] == (mode == "release_held_queue")
                assert not control["fenced"]
        if mode == "continue_work":
            sent = runtime.native.session.sent[-1]
            assert sent[0] != "Original human request"
            assert sent[1]["client_turn_id"] == "resume-one"
            assert record(runtime)["generation_task_id"] == work["generation_task_id"]
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("foreign", [False, True])
def test_stop_withdraws_only_owned_exact_message_and_fences_unknowns(runtime, foreign):
    async def run():
        context(runtime)
        root = submit(runtime)
        await runtime.send(PROJECT, "request-one")
        session = runtime.native.session

        async def abort_without_clearing_queue():
            session.aborts += 1
            session.event("session.idle", parent=session.last_event)

        session.abort = abort_without_clearing_queue
        runtime.native.pending_state["items"] = [
            dict(
                id="queue-item-not-message-id",
                messageId="foreign-message" if foreign else "native-request-one",
                displayText="Original human request",
                kind="message",
            )
        ]
        submit(runtime, "stop-withdraw", "stop", target_work_id=root["work_id"])
        await runtime.stop(PROJECT, root["work_id"], "stop-withdraw")
        assert session.aborts == 1
        result = record(runtime, "stop-withdraw")["receipt"]
        if foreign:
            assert result["state"] == "outcome_uncertain"
            assert runtime.native.pending_state["items"][0]["messageId"] == "foreign-message"
            assert runtime.native.closed
        else:
            assert result["state"] == "completed"
            assert runtime.native.pending_state["items"] == []
            with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
                assert tx.all("copilot.withdrawals")[0] == {"removed": True, "interrupted": False}
        await runtime.close()

    asyncio.run(run())


def test_native_session_missing_never_manufactures_new_conversation(runtime):
    async def run():
        context(runtime)
        root = submit(runtime)
        await runtime.send(PROJECT, "request-one")
        await runtime.close()
        from proscenium.copilot.service import Copilot
        from test_delivery import Native

        class Missing(Native):
            async def open(self, project_id, session_id, callbacks):
                self.opens.append((project_id, session_id))
                raise DomainError("native_session_missing", 503)

        other = Copilot(runtime.services, native=Missing())
        await other.start()
        with other.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            work = tx.require("work", root["work_id"])
        other.admit_resume(
            OWNER,
            dict(
                schema_version="1.0",
                project_id=PROJECT,
                operation_id="resume-missing",
                expected_revision=work["state_revision"],
                mode="continue_work",
                work_id=root["work_id"],
            ),
        )
        await other.resume(PROJECT, "resume-missing")
        assert other.native.opens == [(PROJECT, "synthetic-production-session")]
        with other.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            operation = tx.require("copilot.resumes", "resume-missing")
            assert operation["detail_code"] == "native_session_missing"
            assert other.state.control(tx)["fenced"]
        await other.close()

    asyncio.run(run())


def test_restart_never_retries_uncertain_exclusive_pause(runtime):
    async def run():
        context(runtime)
        root = submit(runtime)
        await runtime.send(PROJECT, "request-one")
        submit(runtime, "interrupted-stop", "stop", target_work_id=root["work_id"])
        with runtime.services.registry.project_store(PROJECT).transaction() as tx:
            phase = tx.require("copilot.stops", "interrupted-stop")
            save(
                tx,
                "copilot.stops",
                "interrupted-stop",
                {**phase, "phase": "pausing", "pause": "dispatching"},
            )
        from proscenium.copilot.service import Copilot
        from test_delivery import Native

        other = Copilot(runtime.services, native=Native())
        await other.start()
        await asyncio.sleep(0.01)
        assert other.native.opens == []
        assert other.native.pauses == []
        assert record(other, "interrupted-stop")["receipt"]["state"] == "outcome_uncertain"
        await other.close()
        await runtime.close()

    asyncio.run(run())


def test_stop_requires_observed_native_idle_not_just_empty_queue(runtime):
    async def run():
        context(runtime)
        root = submit(runtime)
        await runtime.send(PROJECT, "request-one")
        session = runtime.native.session

        async def abort():
            session.aborts += 1

        session.abort = abort
        reads = 0

        async def pending(_session):
            nonlocal reads
            reads += 1
            if reads >= 3:
                session.event("session.idle", parent=session.last_event)
            return dict(items=[], steeringMessages=[], inFlightSteeringCount=0)

        runtime.native.pending = pending
        submit(runtime, "stop-wait-idle", "stop", target_work_id=root["work_id"])
        await runtime.stop(PROJECT, root["work_id"], "stop-wait-idle")
        assert reads >= 3
        assert record(runtime, "stop-wait-idle")["receipt"]["state"] == "completed"
        await runtime.close()

    asyncio.run(run())


def test_project_policy_and_owner_override_freeze_once_across_steer(runtime):
    policy = dict(
        schema_version="1.0",
        operation_id="project-policy",
        project_id=PROJECT,
        expected_revision=0,
        generation_attempt_limit=7,
        default_profile_id="authoring",
    )
    runtime.state.policy_resolver = lambda _: policy

    async def run():
        context(runtime)
        root = submit(runtime)
        policy["generation_attempt_limit"] = 99
        await runtime.send(PROJECT, "request-one")
        steer = submit(runtime, "same-generation-task", "steer", target_work_id=root["work_id"])
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            assert tx.require("copilot.effective", "request-one")["generation_attempt_limit"] == 7
            assert (
                tx.require("requests", "request-one")["generation_task_id"]
                == tx.require("requests", "same-generation-task")["generation_task_id"]
            )
        assert steer["work_id"] == root["work_id"]
        await runtime.close()

    asyncio.run(run())
