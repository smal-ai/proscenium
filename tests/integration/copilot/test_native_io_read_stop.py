"""A departed rg permission wait needs an explicit evidenced Stop, not a dead-PID guess."""

import asyncio
import copy
import json
from pathlib import Path

import pytest
from proscenium.copilot.state import key, save
from test_delivery import OWNER, PROJECT, context, record, submit
from test_delivery import runtime as runtime

OBSERVED = json.loads(Path("docs/evidence/m11-final/permission-blocker.json").read_text())
TOOL = OBSERVED["request"]["toolCallId"]
NATIVE = OBSERVED["decision"]["native_decision_id"]


async def setup_retained(runtime, *, owner_gone=True):
    context(runtime)
    submit(runtime)
    await runtime.send(PROJECT, "request-one")
    old = runtime.native.session
    sid = old.session_id
    root = Path(runtime.services.registry.get(PROJECT)["path"])
    relative = "unity/M11/Assets/Proscenium/tool-install.json"
    target = root / relative
    target.parent.mkdir(parents=True)
    target.write_text("{}\n")
    request = {
        **OBSERVED["request"],
        "path": str(target),
        "intention": "Search in file: " + str(target),
    }
    history = []
    original = old.callbacks["on_event"]

    def observe(event):
        history.append(copy.deepcopy(event))
        original(event)

    old.callbacks["on_event"] = observe
    old.event(
        "tool.execution_start",
        dict(
            toolCallId=TOOL,
            toolName="rg",
            arguments=dict(
                pattern="proscenium|python|contracts",
                paths=relative,
                output_mode="content",
                head_limit=10,
            ),
        ),
        parent=old.last_event,
        event_id="8fa39d4d-0399-474e-b8a4-1d23ddd7e3c4",
    )
    old.event(
        "permission.requested",
        dict(requestId=NATIVE, permissionRequest=request, ownerSessionId=sid),
        event_id="87b37652-ab81-42e5-a93c-b70166e14562",
    )
    inv_id = key(sid, "tool", TOOL)
    with runtime.services.registry.project_store(PROJECT).transaction() as tx:
        inv = tx.require("invocations", inv_id)
        save(tx, "invocations", inv_id, {**inv, "owner_epoch": "retained-original-epoch"})
        stage = tx.require("copilot.permissions", key(sid, NATIVE))
        save(
            tx,
            "copilot.permissions",
            key(sid, NATIVE),
            {
                **stage,
                "phase": "reply_prepared",
                "reply": "reject",
                "authority": "host",
                "delivery_state": "outcome_uncertain",
            },
        )
        decision = {
            **OBSERVED["decision"],
            "project_id": PROJECT,
            "work_id": inv["work_id"],
            "invocation_id": inv_id,
        }
        tx.put("decisions", decision["decision_id"], decision, schema="Decision")
        runtime.state.work_state(tx, inv["work_id"], "awaiting_user")
    runtime.sessions.clear()
    runtime.native.departed_process = dict(
        session_id=sid,
        pid=123,
        start="old-original-process",
        observed_identity=None if owner_gone else "old-original-process",
    )
    native_open = runtime.native.open

    async def open_metadata(*args):
        session = await native_open(*args)

        async def events():
            return copy.deepcopy(history)

        session.get_events = events
        session.get_raw_events = events
        return session

    runtime.native.open = open_metadata
    return inv_id


@pytest.mark.parametrize("bad", [None, "no_abort_event", "no_idle_event", "live_original_owner"])
def test_original_read_wait_only_interrupts_under_a_verified_stop(runtime, bad, monkeypatch):
    async def run():
        inv_id = await setup_retained(runtime, owner_gone=bad != "live_original_owner")
        evidence = await runtime.reconcile(PROJECT)
        assert evidence["live_invocations"] == 1
        session = runtime.native.session

        async def abort():
            session.aborts += 1
            runtime.native.pending_state = dict(
                items=[], steeringMessages=[], inFlightSteeringCount=0
            )
            if bad != "no_abort_event":
                session.event(
                    "abort", {"reason": "user_initiated"}, event_id="actual-control-abort"
                )
            if bad != "no_idle_event":
                session.event("session.idle", event_id="actual-control-idle")

        session.abort = abort
        # Deterministic fault cases need not spend five wall-clock seconds polling an unchanged fake.
        real_sleep = asyncio.sleep

        async def yield_only(delay):
            await real_sleep(0)

        if bad is not None:
            monkeypatch.setattr(asyncio, "sleep", yield_only)
        work_id = record(runtime)["receipt"]["work_id"]
        stop = runtime.state.submit(
            OWNER,
            dict(
                schema_version="1.0",
                request_id="original-owner-stop",
                project_id=PROJECT,
                type="stop",
                target_work_id=work_id,
            ),
        )[0]
        await runtime.stop(PROJECT, work_id, stop["request_id"])
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            phase = tx.require("copilot.stops", stop["request_id"])
            inv = tx.require("invocations", inv_id)
            decision = tx.require("decisions", OBSERVED["decision"]["decision_id"])
            assert (
                decision["state"] == "cancelled"
                and decision["response"] is None
                and decision["resolved_by"] is None
                and decision["delivery_state"] == "not_sent"
            )
            if bad is None:
                assert phase["phase"] == "confirmed" and inv["state"] == "cancelled"
                assert tx.require("work", work_id)["state"] == "stopped"
                assert inv["recovery"]["native_return_observed"] is False
                assert inv["recovery"]["native_permission_completion_observed"] is False
                assert inv["recovery"]["native_exit_code"] is None
                assert (
                    phase["abort_native_event_id"] == "actual-control-abort"
                    and phase["idle_native_event_id"] == "actual-control-idle"
                )
            else:
                assert phase["phase"] == "outcome_uncertain" and inv["state"] == "running"
                assert tx.all("copilot.permission_outcomes") == []
        assert session.aborts == 1 and len(session.sent) == 0  # Metadata session never sends.
        await runtime.close()

    asyncio.run(run())
