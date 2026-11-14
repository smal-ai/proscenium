"""M10 native commands-gate denial, not generic missing Bash-result recovery."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from copilot.session import session_event_from_dict
from proscenium.contracts import canonical
from proscenium.copilot.context import Contexts
from proscenium.copilot.service import wire
from proscenium.copilot.state import State, key

CAPTURE = json.loads(Path("docs/evidence/m10/permission-defect.json").read_text())
SID = "5971843f-f216-40f8-b8cb-995675f48161"
TOOL = CAPTURE["tool_call_id"]
INV = key(SID, "tool", TOOL)


def captured_history():
    first, last = CAPTURE["requests"]
    command = last["permission_request"]["fullCommandText"]
    # Exact captured IDs/timestamps; only isolated project/work attribution is rebased.
    events = [
        dict(
            id="dbfcd230-d244-4c69-8a2a-cfd39e1d10fc",
            type="tool.execution_start",
            timestamp="2026-11-14T14:37:09.355Z",
            data=dict(toolCallId=TOOL, toolName="bash", arguments={"command": command}),
        )
    ]
    for row, stamp, completion_id, completion_stamp in [
        (first, ".412", "7f9a2af3-7669-4305-899d-d75a332be969", ".427"),
        (last, ".429", "43d539c1-3a3a-4173-abc4-8e09db265c51", ".453"),
    ]:
        prompt = (
            dict(
                kind="path",
                accessKind="shell",
                paths=["/home/quintin/.local/bin/blender"],
                toolCallId=TOOL,
            )
            if row is first
            else dict(
                kind="commands",
                fullCommandText=command,
                commandIdentifiers=["timeout"],
                toolCallId=TOOL,
                intention=row["permission_request"]["intention"],
                canOfferSessionApproval=False,
            )
        )
        events += [
            dict(
                id=row["native_event_id"],
                type="permission.requested",
                timestamp=f"2026-11-14T14:37:09{stamp}Z",
                data=dict(
                    requestId=row["request_id"],
                    permissionRequest=row["permission_request"],
                    promptRequest=prompt,
                    ownerSessionId=SID,
                ),
            ),
            dict(
                id=completion_id,
                type="permission.completed",
                timestamp=f"2026-11-14T14:37:09{completion_stamp}Z",
                data=dict(requestId=row["request_id"], toolCallId=TOOL, result=row["completion"]),
            ),
        ]
    return events


def seed(services):
    state = State(services, Contexts(services))
    history = captured_history()
    binding = dict(work_id="work-test", request_id="request-test", delivery_id="request-test")
    stage_id = CAPTURE["requests"][1]["request_id"]
    fingerprint = hashlib.sha256(
        canonical(wire(session_event_from_dict(history[-2]))["data"]["permissionRequest"])
    ).hexdigest()
    assert fingerprint == "f8367e60aed7c1990976ffeb013490623ff615dbca0aaf7010978f6c56ca9192"
    with services.registry.project_store("project-test").transaction() as tx:
        work = tx.require("work", "work-test")
        tx.put(
            "work",
            "work-test",
            {**work, "native_session_id": SID, "state_revision": 1},
            expected_revision=0,
            schema="WorkRecord",
        )
        state.bind(tx, SID, "tool", TOOL, binding)
        state.bind(tx, SID, "decision", stage_id, {**binding, "invocation_id": INV})
        tx.put(
            "invocations",
            INV,
            {
                **binding,
                "invocation_id": INV,
                "native_id": TOOL,
                "session_id": SID,
                "kind": "tool",
                "name": "bash",
                "state": "running",
                "model_id": "gpt-6.1-sol",
            },
        )
        tx.put(
            "copilot.permissions",
            key(SID, stage_id),
            {
                **binding,
                "session_id": SID,
                "tool_call_id": TOOL,
                "invocation_id": INV,
                "native_id": stage_id,
                "fingerprint": fingerprint,
                "phase": "outcome_uncertain",
                "reply": None,
                "authority": None,
                "delivery_state": "not_sent",
                "completions": [
                    dict(
                        matches=False,
                        native_event_id=history[-1]["id"],
                        result="denied-interactively-by-user",
                    )
                ],
            },
        )
    departed = dict(
        session_id=SID,
        pid=123,
        start="old-owner",
        observed_identity=None,
        invocation_ids=[INV],
        pending_empty=True,
        current_send=False,
        permission_fingerprints={history[-2]["id"]: fingerprint},
    )
    return state, history, departed


def test_actual_native_commands_denial_settles_failure_without_ack_or_owner_answer(services):
    state, history, departed = seed(services)
    with services.registry.project_store("project-test").transaction() as tx:
        stage_before = tx.require(
            "copilot.permissions", key(SID, CAPTURE["requests"][1]["request_id"])
        )
        assert state.observe_native_denials(tx, SID, history, departed) == 1
        invocation = tx.require("invocations", INV)
        assert invocation["state"] == "failed"
        observation = tx.require(
            "copilot.permission_outcomes", key(SID, CAPTURE["requests"][1]["request_id"])
        )
        assert observation["kind"] == "native_authorization_denied"
        assert observation["source_event_ids"] == [
            history[0]["id"],
            history[-2]["id"],
            history[-1]["id"],
        ]
        assert observation["native_return_observed"] is False
        assert observation["native_exit_code"] is None
        assert (
            tx.require("copilot.permissions", key(SID, CAPTURE["requests"][1]["request_id"]))
            == stage_before
        )
        assert state.native_denied_stage(tx, stage_before)
        assert not state.native_denied_stage(
            tx, {**stage_before, "native_id": "fresh-independent-id"}
        )
        assert tx.all("decisions") == []
        revision = tx.revision("invocations", INV)
        assert state.observe_native_denials(tx, SID, history, departed) == 0
        assert tx.revision("invocations", INV) == revision


@pytest.mark.parametrize(
    "bad",
    [
        "wrong_tool",
        "wrong_session",
        "wrong_work",
        "live_owner",
        "pending",
        "sending",
        "later_approval",
        "later_pending",
        "concurrent",
        "contradictory_success",
        "generic_bash",
        "path_denial",
        "command_changed",
        "unknown_stage",
    ],
)
def test_denial_observation_keeps_ambiguous_live_or_generic_bash_uncertain(services, bad):
    state, history, departed = seed(services)
    with services.registry.project_store("project-test").transaction() as tx:
        if bad == "wrong_tool":
            history[-1]["data"]["toolCallId"] = "wrong-tool"
        elif bad == "wrong_session":
            departed["session_id"] = "another-session"
        elif bad == "wrong_work":
            i = tx.require("invocations", INV)
            tx.put("invocations", INV, {**i, "work_id": "another-work"}, expected_revision=0)
        elif bad == "live_owner":
            departed["observed_identity"] = departed["start"]
        elif bad == "pending":
            departed["pending_empty"] = False
        elif bad == "sending":
            departed["current_send"] = True
        elif bad in {"later_approval", "later_pending"}:
            requested = copy.deepcopy(history[-2])
            requested["id"] = "later-request"
            requested["data"]["requestId"] = "later-native"
            history.append(requested)
            if bad == "later_approval":
                completed = copy.deepcopy(history[-2])
                completed["id"] = "later-completion"
                completed["data"]["requestId"] = "later-native"
                completed["data"]["result"] = {"kind": "approved"}
                history.append(completed)
        elif bad == "concurrent":
            history[2], history[3] = history[3], history[2]
        elif bad == "contradictory_success":
            history.append(
                dict(
                    id="success",
                    type="tool.execution_complete",
                    data=dict(toolCallId=TOOL, success=True),
                )
            )
        elif bad == "generic_bash":
            history = history[:1]
        elif bad == "path_denial":
            history[-2]["data"]["promptRequest"]["kind"] = "path"
        elif bad == "command_changed":
            history[0]["data"]["arguments"]["command"] = "another-command"
        elif bad == "unknown_stage":
            stage = tx.require(
                "copilot.permissions", key(SID, CAPTURE["requests"][1]["request_id"])
            )
            tx.put("copilot.permissions", "unknown", {**stage, "native_id": "unknown-native"})
        assert state.observe_native_denials(tx, SID, history, departed) == 0
        assert tx.require("invocations", INV)["state"] == "running"
        assert tx.all("copilot.permission_outcomes") == []
