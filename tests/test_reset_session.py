"""Owner-initiated session detachment against real stores and an offline SDK double."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from copilot import CopilotClient
from copilot.generated.rpc import SessionRpc
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from proscenium.api import project_projection
from proscenium.api.dependencies import owner
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.copilot import cold_stop
from proscenium.copilot.eligibility import execution_status
from proscenium.copilot.native import Native
from proscenium.copilot.router import router
from proscenium.copilot.service import Copilot
from proscenium.copilot.state import save
from proscenium.copilot.tools import Tools
from proscenium.db import Transaction
from proscenium.projects.service import Projects
from proscenium.services import Services
from test_activity_feedback import PROJECT, REQUEST, SESSION, WORK
from test_request_targeting import context, request_record
from test_settled_stop_recovery import RpcClient

from tools.operations import recovery

OWNER = Caller("owner", "owner")
FRESH = "fresh-session"
MODELS = ("new-lead", "new-worker", "new-unity-worker")


class SDK:
    def __init__(self):
        self.opens = []
        self.sessions = []
        self.missing = False

    async def open(self, project_id, session_id, callbacks, *, models=None):
        assert project_id == PROJECT
        self.opens.append((session_id, models))
        if session_id == SESSION and self.missing:
            raise DomainError("native_session_missing", 503)
        session = SimpleNamespace(
            session_id=session_id or FRESH,
            callbacks=callbacks,
            disconnect=AsyncMock(),
            send=AsyncMock(side_effect=lambda *args, **kw: "message-" + kw["client_turn_id"]),
            abort=AsyncMock(),
        )
        self.sessions.append(session)
        return session

    async def pending(self, session):
        return {}

    async def pause(self, session, paused):
        pass

    async def inactive(self, session):
        return True


async def post_reset(copilot, body):
    app = FastAPI()
    app.state.services = copilot.services
    app.state.services.extensions["copilot"] = copilot
    app.dependency_overrides[owner] = lambda: OWNER

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse(exc.envelope(), status_code=exc.status)

    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            f"/api/v1/projects/{PROJECT}/resume",
            json=body,
            headers={"Idempotency-Key": body["operation_id"]},
        )


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Live native, model and provider execution is forbidden")

    monkeypatch.setattr(CopilotClient, "start", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    services = Services.create(Settings(data_directory=tmp_path / "machine"))
    try:
        project = tmp_path / "project"
        project.mkdir()
        services.registry.register(
            dict(
                schema_version="1.0",
                project_id=PROJECT,
                repository_id="repository-one",
                name="Reset session fixture",
                path=str(project),
                state="inactive",
                state_revision=0,
                native_session_id=None,
            )
        )
        services.activate_project(PROJECT)
        store = services.registry.project_store(PROJECT)
        with store.transaction() as tx:
            tx.put(
                "copilot.session",
                "native",
                dict(session_id=SESSION, owner_epoch=services.ownership.epoch),
            )
            tx.put("contexts", "context-one", context(), schema="ContextSnapshot")
        sdk = SDK()
        copilot = Copilot(services, native=sdk)
        copilot.checked_projects.add(PROJECT)
        yield copilot, sdk, store
    finally:
        services.close()


def command(copilot, **kwargs):
    return (
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            operation_id="reset",
            mode="reset_session",
            work_id=None,
            expected_revision=copilot.services.registry.get(PROJECT)["state_revision"],
        )
        | kwargs
    )


def model_config(copilot, models):
    copilot.services.settings.runtime.update(
        zip(("lead_model", "worker_model", "unity_worker_model"), models, strict=True)
    )


def seed_work(tx, identity=WORK, state="running", request_id=REQUEST):
    tx.put(
        "work",
        identity,
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            work_id=identity,
            state=state,
            state_revision=0,
            originating_request_id=request_id,
            directive_request_ids=[],
            native_session_id=SESSION,
            generation_task_id="task-one",
            job_ids=[],
        ),
        schema="WorkRecord",
    )


def intent(tx, identity):
    state, evidence = tx.connection.execute(
        "SELECT state, evidence FROM outbox WHERE id=?", (identity,)
    ).fetchone()
    return state, json.loads(evidence) if evidence is not None else None


def assert_detached(copilot, store, old_sid=SESSION):
    services = copilot.services
    record = services.registry.get(PROJECT)
    assert record["native_session_id"] is None and record["state"] == "active"
    with store.transaction(write=False) as tx:
        assert tx.get("copilot.session", "native", required=False) is None
        assert tx.get("copilot.control", "reset_in_progress", required=False) is None
        retired = tx.require("copilot.retired_sessions", "reset")
        assert retired["retired_session_id"] == old_sid
        assert retired["reason"] == "owner_reset_session"
        control = tx.require("copilot.control", "gate")
        assert not control["fenced"] and control["reason"] is None and control["queue_released"]
        assert control["reconciled_epoch"] == services.ownership.epoch
        assert control["activation_revision"] == record["state_revision"]
        execution = execution_status(tx, services.ownership.epoch, record)
        assert execution["fence_reason"] is None and execution["hold_reason_code"] is None
        assert tx.require("copilot.resumes", "reset")["state"] == "completed"
        assert intent(tx, "reset") == (
            "acknowledged",
            dict(mode="reset_session", retired_session_id=old_sid),
        )
    services.gate.require(PROJECT)
    services.registry.assert_settled(PROJECT)


async def deliver(copilot, sdk, store, identity, *, opened=(None, MODELS)):
    await copilot.tick(PROJECT)
    await copilot.sends[identity]
    session = sdk.sessions[-1]
    expected_session = opened[0] or FRESH
    assert session.session_id == expected_session
    assert session.send.await_args.kwargs["client_turn_id"] == identity
    assert sdk.opens[-1] == opened
    assert copilot.services.registry.get(PROJECT)["native_session_id"] == expected_session
    with store.transaction(write=False) as tx:
        receipt = tx.require("requests", identity)["receipt"]
        assert receipt["state"] == "acknowledged"
        assert tx.require("work", receipt["work_id"])["native_session_id"] == expected_session
    for kind in ("session.task_complete", "session.idle"):
        session.callbacks["on_event"](
            dict(
                id=f"{kind}-{identity}",
                type=kind,
                data=dict(originatingMessageId="message-" + identity),
            )
        )
    await copilot.tick(PROJECT)
    with store.transaction(write=False) as tx:
        assert tx.require("requests", identity)["receipt"]["state"] == "completed"
    copilot.services.registry.assert_settled(PROJECT)


@pytest.mark.parametrize("local_association", [True, False])
def test_missing_native_session_releases_queue_with_new_work_identity(runtime, local_association):
    copilot, sdk, store = runtime
    sdk.missing = True
    with store.transaction() as tx:
        if not local_association:
            tx.connection.execute("DELETE FROM records WHERE namespace='copilot.session'")
            tx.changed_namespaces.add("copilot.session")
        copilot.state.fence(tx, "native_session_missing")
    model_config(copilot, MODELS)
    held, _ = copilot.state.submit(OWNER, request_record("held")["submission"])
    with store.transaction() as tx:
        seed_work(tx, held["work_id"], state="pending", request_id="held")

    async def exercise():
        copilot.admit_resume(OWNER, command(copilot))
        await copilot.resume(PROJECT, "reset")
        assert not sdk.opens  # The missing association was never opened or reconciled.
        assert_detached(copilot, store)
        with store.transaction(write=False) as tx:
            receipt = tx.require("requests", "held")["receipt"]
            assert receipt["state"] == "held" and receipt["work_id"] != held["work_id"]
            assert tx.require("work", held["work_id"])["state"] == "stopped"
            assert intent(tx, "held") == ("pending", None)
        await deliver(copilot, sdk, store, "held")

    asyncio.run(exercise())


def test_reset_seals_wedged_state_without_altering_project_history(runtime):
    copilot, sdk, store = runtime
    preserved = (
        "items",
        "versions",
        "artifacts",
        "annotations",
        "reviews",
        "findings",
        "selections",
        "contexts",
        "jobs",
    )
    with store.transaction() as tx:
        copilot.state.fence(tx, "outcome_uncertain")
        seed_work(tx, state="awaiting_resume")
        seed_work(tx, "uncertain-work", "outcome_uncertain", "uncertain-request")
        seed_work(tx, "stopped-work", "stop_requested", "stop-target")
        original = request_record(REQUEST)
        original["receipt"].update(state="outcome_uncertain")
        tx.put("requests", REQUEST, original, schema="RequestRecord")
        stop = request_record("stop-one")
        stop["submission"] = dict(
            schema_version="1.0",
            request_id="stop-one",
            project_id=PROJECT,
            type="stop",
            target_work_id=WORK,
        )
        stop["receipt"].update(type="stop", state="stop_requested")
        tx.put("requests", "stop-one", stop, schema="RequestRecord")
        tx.put(
            "copilot.stops",
            "stop-one",
            dict(phase="aborting", hold_released=False, work_id=WORK, abort="dispatching"),
        )
        tx.put(
            "invocations",
            "inv-one",
            dict(invocation_id="inv-one", state="running", updated_at="previous"),
        )
        tx.put(
            "decisions",
            "decision-one",
            dict(
                schema_version="1.0",
                decision_id="decision-one",
                project_id=PROJECT,
                work_id=WORK,
                kind="permission",
                state="pending",
                state_revision=0,
                fingerprint="0" * 64,
                prompt="Confirm",
                options=[],
            ),
            schema="Decision",
        )
        tx.put("copilot.sends", REQUEST, dict(state="outcome_uncertain", session_id=SESSION))
        tx.put(
            "copilot.continuation_sends",
            "old-resume",
            dict(state="dispatching", session_id=SESSION, operation_id="old-resume"),
        )
        tx.put(
            "copilot.resumes",
            "old-resume",
            dict(
                state="outcome_uncertain",
                body=command(
                    copilot, operation_id="old-resume", mode="continue_work", work_id=WORK
                ),
            ),
        )
        tx.put("copilot.duplicates", "old-duplicate", dict(request_id=REQUEST))
        for identity, kind in (
            (REQUEST, "copilot.send"),
            ("old-resume", "copilot.resume"),
            ("stop-one", "copilot.stop"),
        ):
            tx.enqueue(identity, kind, {})
            tx.claim_intent(identity)
            if kind != "copilot.stop":
                tx.settle_intent(identity, "outcome_uncertain", {"reason": "previous"})
        tx.enqueue("external-job", "providers.review", {})  # Pending, outside reset's authority.
        for namespace in preserved:
            tx.put(
                namespace,
                "retained",
                dict(identity=f"retained-{namespace}", version_id="version-one", state="succeeded"),
            )
        tx.emit(
            "request.state_changed",
            original,
            actor={"kind": "system", "id": "fixture"},
            request_id=REQUEST,
            work_id=WORK,
        )

    def history(tx):
        return [
            tuple(row) for row in tx.connection.execute("SELECT * FROM events ORDER BY sequence")
        ]

    with store.transaction(write=False) as tx:
        before = {ns: tx.all_raw(ns) for ns in preserved}
        events = history(tx)
    copilot.admit_resume(OWNER, command(copilot))
    asyncio.run(copilot.tick(PROJECT))  # Reset takes precedence over an unresolved Stop.
    assert not sdk.opens
    assert_detached(copilot, store)
    with store.transaction(write=False) as tx:
        assert {ns: tx.all_raw(ns) for ns in preserved} == before
        assert history(tx)[: len(events)] == events
        assert {(w["state"], w["state_revision"]) for w in tx.all("work")} == {("stopped", 1)}
        invocation = tx.require("invocations", "inv-one")
        assert invocation["state"] == "cancelled" and invocation["updated_at"] != "previous"
        decision = tx.require("decisions", "decision-one")
        assert (decision["state"], decision["state_revision"], decision["resolved_by"]) == (
            "cancelled",
            1,
            {"kind": "user", "id": "owner"},
        )
        stop = tx.require("copilot.stops", "stop-one")
        assert (
            stop["phase"] == "confirmed"
            and stop["hold_released"]
            and stop["abort"] == "dispatching"
        )
        for ns, identity in (
            ("copilot.sends", REQUEST),
            ("copilot.continuation_sends", "old-resume"),
        ):
            abandoned = tx.require(ns, identity)
            assert (
                abandoned["state"] == "abandoned_on_reset"
                and abandoned["reset_operation_id"] == "reset"
                and abandoned["abandoned_at"]
            )
        for identity, expected in ((REQUEST, "stopped"), ("stop-one", "cancelled")):
            receipt = tx.require("requests", identity)["receipt"]
            assert (receipt["state"], receipt["detail_code"], receipt["state_revision"]) == (
                expected,
                "session_reset",
                1,
            )
        for identity in (REQUEST, "old-resume", "stop-one"):
            assert intent(tx, identity) == (
                "failed",
                dict(reason="owner_reset_session", operation_id="reset"),
            )
        assert intent(tx, "external-job") == ("pending", None)
        event_types = [
            e["event_type"]
            for e in tx.events()["events"]
            if e["actor"] == {"kind": "user", "id": "owner"}
        ]
        assert event_types.count("work.state_changed") == 3
        assert event_types.count("decision.resolved") == 1
        assert event_types.count("request.state_changed") == 2
    # A later restart or activation may fence the detached project again. The old
    # sends and correlation failures are retained evidence, not release blockers.
    copilot.restore(PROJECT)
    copilot.admit_resume(
        OWNER, command(copilot, operation_id="release-again", mode="release_held_queue")
    )
    asyncio.run(copilot.resume(PROJECT, "release-again"))
    with store.transaction(write=False) as tx:
        assert tx.require("copilot.resumes", "release-again")["state"] == "completed"
    assert not sdk.opens


def test_idle_reset_retires_callbacks_and_next_turn_uses_new_models(runtime):
    copilot, sdk, store = runtime

    async def exercise():
        cached = await copilot.session(PROJECT)
        owner = copilot.callback_owners[PROJECT]
        copilot.read_wait_recovery[PROJECT] = {"old": True}
        copilot.pause_owner[PROJECT] = "old-stop"
        cached.disconnect.side_effect = ConnectionError("already disconnected")
        model_config(copilot, MODELS)
        copilot.admit_resume(OWNER, command(copilot))
        await copilot.resume(PROJECT, "reset")
        cached.disconnect.assert_awaited_once()
        assert owner.retired and PROJECT not in copilot.sessions
        assert PROJECT not in copilot.read_wait_recovery and PROJECT not in copilot.pause_owner
        assert_detached(copilot, store)
        copilot.state.submit(OWNER, request_record("new-turn")["submission"])
        await deliver(copilot, sdk, store, "new-turn")
        cached.callbacks["on_event"](dict(id="late-error", type="session.error"))
        copilot.services.gate.require(PROJECT)
        assert copilot.callback_owners[PROJECT].failure is None
        assert sdk.opens == [(SESSION, None), (None, MODELS)]

    asyncio.run(exercise())


def test_running_unfenced_work_blocks_admission_and_late_dispatch(runtime):
    copilot, sdk, store = runtime
    copilot.admit_resume(OWNER, command(copilot, operation_id="queued-reset"))
    with store.transaction() as tx:
        seed_work(tx)
    with pytest.raises(DomainError, match="actively running") as exc:
        copilot.admit_resume(OWNER, command(copilot))
    assert (exc.value.code, exc.value.status) == ("project_busy", 409)
    asyncio.run(copilot.resume(PROJECT, "queued-reset"))
    assert not sdk.opens
    assert copilot.services.registry.get(PROJECT)["native_session_id"] == SESSION
    with store.transaction(write=False) as tx:
        assert tx.require("work", WORK)["state"] == "running"
        assert not copilot.state.control(tx)["fenced"]
        assert tx.require("copilot.resumes", "queued-reset")["detail_code"] == "project_busy"


def test_reset_does_not_disconnect_a_session_with_a_live_send(runtime):
    copilot, _, store = runtime

    async def exercise():
        cached = await copilot.session(PROJECT)
        body = command(copilot, operation_id="blocked-reset")
        copilot.admit_resume(OWNER, body)
        release = asyncio.Event()
        task = asyncio.create_task(release.wait())
        copilot.sends[REQUEST] = task
        copilot.send_projects[REQUEST] = PROJECT
        try:
            denied = await post_reset(copilot, command(copilot))
            assert denied.status_code == 409 and denied.json()["error"]["code"] == "project_busy"
            await copilot.resume(PROJECT, body["operation_id"])
            cached.disconnect.assert_not_awaited()
            assert not task.done()
            assert copilot.services.registry.get(PROJECT)["native_session_id"] == SESSION
            with store.transaction(write=False) as tx:
                assert (
                    tx.require("copilot.resumes", "blocked-reset")["detail_code"] == "project_busy"
                )
        finally:
            release.set()
            await task
        allowed = await post_reset(copilot, command(copilot))
        assert allowed.status_code == 202
        await copilot.resume(PROJECT, "reset")
        cached.disconnect.assert_awaited_once()
        assert not copilot.sends and not copilot.send_projects
        assert_detached(copilot, store)

    asyncio.run(exercise())


def test_revision_work_id_and_idempotent_replay(runtime):
    copilot, sdk, store = runtime
    for overrides, code, status in (
        ({"expected_revision": 0}, "revision_conflict", 409),
        ({"work_id": WORK}, "invalid_request", 422),
    ):
        with pytest.raises(DomainError) as exc:
            copilot.admit_resume(OWNER, command(copilot, **overrides))
        assert (exc.value.code, exc.value.status) == (code, status)
    body = command(copilot)
    accepted, replayed = copilot.admit_resume(OWNER, body)
    assert accepted["state"] == "accepted" and not replayed
    pending, replayed = copilot.admit_resume(OWNER, body)
    assert pending["state"] == "accepted" and replayed and pending["replayed"]
    asyncio.run(copilot.resume(PROJECT, "reset"))
    assert_detached(copilot, store)
    detached = copilot.services.registry.get(PROJECT)
    current, replayed = copilot.admit_resume(OWNER, body)
    assert current["state"] == "completed" and replayed and current["replayed"]
    asyncio.run(copilot.resume(PROJECT, "reset"))
    assert not sdk.opens and copilot.services.registry.get(PROJECT) == detached
    assert copilot.services.registry.clear_native_session(PROJECT, SESSION) == detached
    with store.transaction(write=False) as tx:
        assert len(tx.all("copilot.retired_sessions")) == 1


class SimulatedProcessLoss(BaseException):
    pass


@pytest.mark.parametrize("local_association", [True, False])
@pytest.mark.parametrize("interruption", [SimulatedProcessLoss, RuntimeError])
def test_interrupted_reset_recovers_exact_identity(
    runtime, monkeypatch, local_association, interruption
):
    copilot, sdk, store = runtime
    registry = copilot.services.registry
    if not local_association:
        with store.transaction() as tx:
            tx.connection.execute("DELETE FROM records WHERE namespace='copilot.session'")
            tx.changed_namespaces.add("copilot.session")
    body = command(copilot)
    copilot.admit_resume(OWNER, body)

    def interrupt(*args, **kwargs):
        assert registry.get(PROJECT)["native_session_id"] is None
        raise interruption("between registry detach and project seal")

    monkeypatch.setattr(copilot, "_seal_reset_state", interrupt)
    if interruption is SimulatedProcessLoss:
        with pytest.raises(SimulatedProcessLoss):
            asyncio.run(copilot.resume(PROJECT, "reset"))
        unfinished = "dispatching"
    else:
        asyncio.run(copilot.resume(PROJECT, "reset"))
        unfinished = "outcome_uncertain"
    project_projection.reconcile(copilot.services, PROJECT)
    assert registry.get(PROJECT)["native_session_id"] is None
    with store.transaction(write=False) as tx:
        marker = tx.require("copilot.control", "reset_in_progress")
        archived = tx.require("copilot.retired_sessions", "reset")
        assert marker["operation_id"] == "reset" and marker["retired_session_id"] == SESSION
        assert archived["retired_session_id"] == SESSION and archived["retired_at"]
        assert bool(tx.get("copilot.session", "native", required=False)) == local_association
        assert tx.require("copilot.resumes", "reset")["state"] == unfinished
        assert intent(tx, "reset")[0] == unfinished
    with pytest.raises(DomainError, match="reset is in progress"):
        registry.assert_settled(PROJECT)

    recovered = Copilot(copilot.services, native=sdk)
    recovered.restore(PROJECT)

    async def finish():
        if interruption is RuntimeError:
            replay = await post_reset(recovered, body)
            assert replay.status_code == 200 and replay.json()["replayed"]
            assert recovered.wake.is_set()
        await recovered.tick(PROJECT)  # Automatic recovery also works with no HTTP replay.
        replay = await post_reset(recovered, body)
        assert replay.status_code == 200 and replay.json()["state"] == "completed"

    asyncio.run(finish())
    assert not sdk.opens
    assert_detached(recovered, store)
    with store.transaction(write=False) as tx:
        assert tx.all("copilot.retired_sessions") == [archived]


def inject_session_identity_conflict(store):
    with store.transaction() as tx:
        # Inject invalid on-disk state without invoking the normal write guard.
        tx.connection.execute(
            "UPDATE records SET body=json_set(body, '$.session_id', 'conflicting-session') "
            "WHERE namespace='copilot.session' AND id='native'"
        )


def test_reset_admission_conflict_fences_cached_session_and_holds_queue(runtime):
    copilot, sdk, store = runtime
    model_config(copilot, MODELS)

    async def exercise():
        copilot.state.submit(OWNER, request_record("original-turn")["submission"])
        await deliver(copilot, sdk, store, "original-turn", opened=(SESSION, MODELS))
        cached = copilot.sessions[PROJECT]
        copilot.services.gate.require(PROJECT)
        inject_session_identity_conflict(store)

        response = await post_reset(copilot, command(copilot))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "source_conflict"
        assert copilot.services.registry.get(PROJECT)["native_session_id"] == SESSION
        with pytest.raises(DomainError, match="awaits explicit Resume"):
            copilot.services.gate.require(PROJECT)
        with store.transaction(write=False) as tx:
            assert tx.require("copilot.session", "native")["session_id"] == "conflicting-session"
            control = tx.require("copilot.control", "gate")
            assert control["fenced"] and control["reason"] == "source_conflict"
            assert tx.get("copilot.resumes", "reset", required=False) is None
            assert (
                tx.connection.execute("SELECT id FROM outbox WHERE id='reset'").fetchone() is None
            )

        copilot.state.submit(OWNER, request_record("after-conflict")["submission"])
        await copilot.tick(PROJECT)
        assert copilot.sessions[PROJECT] is cached
        cached.send.assert_awaited_once()
        assert "after-conflict" not in copilot.sends
        with store.transaction(write=False) as tx:
            assert tx.require("requests", "after-conflict")["receipt"]["state"] == "held"
            assert intent(tx, "after-conflict") == ("pending", None)

    asyncio.run(exercise())


def test_conflicting_session_ids_fail_closed_without_escaping_dispatch(runtime):
    copilot, sdk, store = runtime
    copilot.admit_resume(OWNER, command(copilot))
    inject_session_identity_conflict(store)
    asyncio.run(copilot.resume(PROJECT, "reset"))
    assert copilot.services.registry.get(PROJECT)["native_session_id"] == SESSION
    assert not sdk.opens
    with pytest.raises(DomainError) as fenced:
        copilot.services.gate.require(PROJECT)
    assert fenced.value.code == "project_busy"
    with store.transaction(write=False) as tx:
        assert tx.require("copilot.session", "native")["session_id"] == "conflicting-session"
        assert tx.get("copilot.control", "reset_in_progress", required=False) is None
        assert not tx.all("copilot.retired_sessions")
        assert tx.require("copilot.control", "gate")["reason"] == "source_conflict"
        status = tx.require("copilot.resumes", "reset")
        assert status["state"] == "outcome_uncertain" and status["detail_code"] == "source_conflict"
        assert intent(tx, "reset") == ("outcome_uncertain", {"detail_code": "source_conflict"})


def test_live_fenced_tool_handler_blocks_admission_and_dispatch_but_stale_row_does_not(
    runtime, monkeypatch
):
    copilot, _, store = runtime

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        async def handler(*args):
            started.set()
            await release.wait()
            return "finished"

        monkeypatch.setattr(Tools, "invoke", handler)
        session = await copilot.session(PROJECT)
        with store.transaction() as tx:
            seed_work(tx, state="running")
            copilot.state.fence(tx, "outcome_uncertain")
            tx.put(
                "invocations", "stale", dict(invocation_id="stale", state="running", work_id=WORK)
            )
        pending = command(copilot, operation_id="before-handler")
        copilot.admit_resume(
            OWNER, pending
        )  # Durable invocation does not count as a live callback.
        task = asyncio.create_task(session.callbacks["tools"][0].handler(None))
        try:
            await asyncio.wait_for(started.wait(), 2)
            assert copilot.callback_owners[PROJECT].active_handlers == 1
            assert not copilot.sends
            denied = await post_reset(copilot, command(copilot))
            assert denied.status_code == 409 and denied.json()["error"]["code"] == "project_busy"
            await copilot.resume(PROJECT, pending["operation_id"])
            with store.transaction(write=False) as tx:
                assert (
                    tx.require("copilot.resumes", "before-handler")["detail_code"] == "project_busy"
                )
            session.disconnect.assert_not_awaited()
        finally:
            release.set()
            assert await task == "finished"
        assert copilot.callback_owners[PROJECT].active_handlers == 0
        assert (await post_reset(copilot, command(copilot))).status_code == 202
        await copilot.tick(PROJECT)
        session.disconnect.assert_awaited_once()
        assert_detached(copilot, store)
        with store.transaction(write=False) as tx:
            assert tx.require("invocations", "stale")["state"] == "cancelled"

    asyncio.run(exercise())


@pytest.mark.parametrize("cold", [True, False])
def test_replacement_stop_ignores_retired_copilot_state(runtime, cold):
    copilot, sdk, store = runtime
    with store.transaction() as tx:
        tx.put("copilot.sends", REQUEST, dict(state="outcome_uncertain", session_id=SESSION))
        tx.put(
            "copilot.continuation_sends",
            "old-resume",
            dict(state="dispatching", operation_id="old-resume"),
        )
        tx.put("copilot.continuations", "old-resume", dict(work_id=WORK, request_id=REQUEST))
        tx.put("copilot.duplicates", "old-duplicate", dict(request_id=REQUEST))
        tx.put("copilot.pending_events", "old-event", dict(session_id=SESSION, resolved=False))
    copilot.admit_resume(OWNER, command(copilot))

    async def exercise():
        await copilot.resume(PROJECT, "reset")
        with store.transaction(write=False) as tx:
            old = tx.require("copilot.continuation_sends", "old-resume")
            assert old["operation_id"] == "old-resume" and old["reset_operation_id"] == "reset"
            assert old["session_id"] == SESSION
            assert tx.require("copilot.pending_events", "old-event") == dict(
                session_id=SESSION, resolved=True, reset_operation_id="reset"
            )
            assert (
                tx.require("copilot.duplicates", "old-duplicate")["reset_operation_id"] == "reset"
            )
            assert cold_stop.send_settled_before_stop(old, {}, session_id=FRESH)
            assert not cold_stop.send_settled_before_stop(old, {}, session_id=SESSION)
            assert not cold_stop.send_settled_before_stop(old, {})
            assert not cold_stop.send_settled_before_stop(
                {**old, "session_id": None}, {}, session_id=FRESH
            )
        receipt, _ = copilot.state.submit(OWNER, request_record("fresh-turn")["submission"])
        await copilot.tick(PROJECT)
        await copilot.sends["fresh-turn"]
        fresh = sdk.sessions[-1]
        work_id = receipt["work_id"]
        assert fresh.session_id == FRESH
        copilot.state.submit(
            OWNER,
            dict(
                schema_version="1.0",
                project_id=PROJECT,
                request_id="fresh-stop",
                type="stop",
                target_work_id=work_id,
            ),
        )
        if cold:
            # Native's actual snapshot and validation run against the offline RPC client.
            adapter = object.__new__(Native)
            adapter.services = copilot.services
            adapter.non_continuing_session_id = FRESH
            adapter.transport_alive = lambda: True
            rpc = RpcClient(None, lambda method: None)
            fresh.rpc = SessionRpc(rpc, FRESH)
            copilot.native = adapter
            departed = dict(pid=123, start="old-process", observed_identity=None, session_id=FRESH)
            with store.transaction() as tx:
                tx.claim_intent("fresh-stop")
                tx.settle_intent("fresh-stop", "outcome_uncertain", dict(reason="interrupted"))
                copilot.state.request_state(tx, "fresh-stop", "outcome_uncertain")
                phase = tx.require("copilot.stops", "fresh-stop")
                save(tx, "copilot.stops", "fresh-stop", {**phase, "phase": "outcome_uncertain"})
                tx.put("copilot.stop_origins", "fresh-stop", departed)
            callback = copilot.callback_owners[PROJECT]
            copilot.read_wait_recovery[PROJECT] = dict(
                session_id=FRESH,
                departed_process=departed,
                callback_generation=callback.generation,
                activity_sequence=callback.activity_sequence,
                proofs=[],
            )
            await copilot.reconcile_cold_stop(PROJECT, fresh, "fresh-stop")
            assert tuple(m["method"] for m in rpc.messages) == cold_stop.METHODS
        else:
            await copilot.tick(PROJECT)
            fresh.abort.assert_awaited_once()
        with store.transaction(write=False) as tx:
            assert tx.require("work", work_id)["state"] == "stopped"
            stopped = tx.require("requests", "fresh-stop")["receipt"]
            assert stopped["state"] == "completed"
            assert stopped["detail_code"] == ("cold_settled_stop_confirmed" if cold else None)
            assert intent(tx, "fresh-stop")[0] == "acknowledged"
        copilot.services.registry.assert_settled(PROJECT)

    asyncio.run(exercise())


def test_backup_discovers_retired_idle_session(runtime):
    copilot, _, store = runtime
    copilot.admit_resume(OWNER, command(copilot))
    asyncio.run(copilot.resume(PROJECT, "reset"))
    assert_detached(copilot, store)
    with store.transaction(write=False) as tx:
        assert not tx.all("work")
        assert all(e["native_session_id"] is None for e in tx.events()["events"])
    assert recovery.session_ids(store, copilot.services.registry.get(PROJECT)) == [SESSION]


def test_reset_settles_obsolete_pending_intents_but_preserves_held_send(runtime):
    copilot, sdk, store = runtime
    model_config(copilot, MODELS)
    aborted, _ = copilot.state.submit(OWNER, request_record("cancelled-a")["submission"])
    copilot.state.cancel(
        OWNER,
        "cancelled-a",
        dict(
            schema_version="1.0",
            operation_id="cancel-a",
            project_id=PROJECT,
            expected_revision=aborted["state_revision"],
        ),
    )
    copilot.state.submit(OWNER, request_record("held-b")["submission"])
    with store.transaction() as tx:
        for kind in ("copilot.stop", "copilot.auto_release", "copilot.resume"):
            tx.enqueue("obsolete-" + kind, kind, {})
        assert intent(tx, "cancelled-a") == ("pending", None)
    copilot.admit_resume(OWNER, command(copilot))

    async def exercise():
        await copilot.resume(PROJECT, "reset")
        with store.transaction(write=False) as tx:
            assert intent(tx, "held-b") == ("pending", None)
            assert tx.require("requests", "cancelled-a")["receipt"]["state"] == "cancelled"
            for identity in (
                "cancelled-a",
                "obsolete-copilot.stop",
                "obsolete-copilot.auto_release",
                "obsolete-copilot.resume",
            ):
                assert intent(tx, identity) == (
                    "failed",
                    dict(reason="owner_reset_session", operation_id="reset"),
                )
        await deliver(copilot, sdk, store, "held-b")

    asyncio.run(exercise())


def test_reset_retains_held_profile_and_new_request_uses_new_default(runtime, monkeypatch):
    copilot, sdk, store = runtime
    projects = Projects(copilot.services)
    copilot.services.extensions["projects"] = projects
    models_b = ("other-lead", "other-worker", "other-unity-worker")
    monkeypatch.setattr(
        copilot.services,
        "effective_profiles",
        lambda: [
            dict(
                profile_id=identity,
                name=identity,
                scope_status="project_scoped",
                lead_model_id=models[0],
                worker_model_ids=list(models[1:]),
            )
            for identity, models in (("profile-a", MODELS), ("profile-b", models_b))
        ],
    )
    with copilot.services.registry.store.transaction() as tx:
        tx.put(
            "private",
            "settings:" + PROJECT,
            dict(default_profile_id="profile-a", state_revision=0, generation_attempt_limit=3),
            schema="ProjectSettingsRecord",
        )
    copilot.state.submit(OWNER, request_record("held-a")["submission"])
    projects.update_settings(
        OWNER,
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            operation_id="switch-profile",
            expected_revision=0,
            generation_attempt_limit=3,
            default_profile_id="profile-b",
        ),
    )
    copilot.admit_resume(OWNER, command(copilot))

    async def exercise():
        await copilot.resume(PROJECT, "reset")
        await deliver(copilot, sdk, store, "held-a")
        copilot.state.submit(OWNER, request_record("new-b")["submission"])
        await deliver(copilot, sdk, store, "new-b", opened=(FRESH, models_b))
        with store.transaction(write=False) as tx:
            assert tx.require("requests", "held-a")["effective_profile_id"] == "profile-a"
            assert tx.require("requests", "new-b")["effective_profile_id"] == "profile-b"
        assert sdk.opens == [(None, MODELS), (FRESH, models_b)]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "recovery_path",
    ["cold-start", "lazy-activation", "changed-on-restart", "changed-before-dispatch"],
)
def test_reset_claim_rollback_only_retries_the_admitted_session(
    runtime, monkeypatch, recovery_path
):
    copilot, sdk, store = runtime
    body = command(copilot)
    copilot.admit_resume(OWNER, body)
    claim_intent = Transaction.claim_intent

    def lose_process_after_claim(tx, identity):
        assert identity == "reset"
        claim_intent(tx, identity)
        assert intent(tx, identity)[0] == "dispatching"
        assert tx.require("copilot.control", "reset_in_progress")["retired_session_id"] == SESSION
        raise SimulatedProcessLoss("after claim, before project commit")

    with monkeypatch.context() as fault:
        fault.setattr(Transaction, "claim_intent", lose_process_after_claim)
        with pytest.raises(SimulatedProcessLoss):
            asyncio.run(copilot.resume(PROJECT, "reset"))
    with store.transaction(write=False) as tx:
        assert tx.require("copilot.resumes", "reset") == dict(
            body=body, state="pending", admitted_session_id=SESSION
        )
        assert intent(tx, "reset") == ("pending", None)
        assert tx.get("copilot.control", "reset_in_progress", required=False) is None
        assert not tx.all("copilot.retired_sessions")

    original = copilot.services
    replacement = recovery_path.startswith("changed-")
    if replacement:
        original.registry.clear_native_session(PROJECT, SESSION)
        with store.transaction() as tx:
            save(
                tx,
                "copilot.session",
                "native",
                dict(session_id=FRESH, owner_epoch=original.ownership.epoch),
            )
        assert original.registry.get(PROJECT)["native_session_id"] == FRESH

    async def finish(recovered, project_store):
        if recovery_path != "changed-before-dispatch":
            assert recovered.services.ownership.epoch != original.ownership.epoch
            assert (
                recovered.services.registry.get(PROJECT)["state_revision"]
                != body["expected_revision"]
            )
            if recovery_path == "lazy-activation":
                recovered._restore(PROJECT, activation=True)
            else:
                recovered.restore(PROJECT)
            with project_store.transaction(write=False) as tx:
                status = tx.require("copilot.resumes", "reset")["state"]
                assert status == ("outcome_uncertain" if replacement else "pending")
                assert intent(tx, "reset")[0] == status
        reply = await post_reset(recovered, body)
        assert reply.status_code == 200 and reply.json()["replayed"]
        assert reply.json()["state"] == (
            "outcome_uncertain" if recovery_path == "changed-on-restart" else "accepted"
        )
        await recovered.tick(PROJECT)
        if replacement:
            assert recovered.services.registry.get(PROJECT)["native_session_id"] == FRESH
            with project_store.transaction(write=False) as tx:
                assert tx.require("copilot.session", "native")["session_id"] == FRESH
                operation = tx.require("copilot.resumes", "reset")
                assert operation["state"] == "outcome_uncertain"
                assert operation["detail_code"] == "source_conflict"
                assert operation["admitted_session_id"] == SESSION
                assert intent(tx, "reset") == (
                    "outcome_uncertain",
                    {"detail_code": "source_conflict"},
                )
                assert not tx.all("copilot.retired_sessions")
                assert tx.get("copilot.control", "reset_in_progress", required=False) is None
        else:
            assert_detached(recovered, project_store)
        assert not sdk.opens

    if recovery_path == "changed-before-dispatch":
        asyncio.run(finish(copilot, store))
    else:
        original.close()
        reopened = Services.create(original.settings)
        try:
            recovered = Copilot(reopened, native=sdk)
            asyncio.run(finish(recovered, reopened.registry.project_store(PROJECT)))
        finally:
            reopened.close()


def test_reset_admission_rejects_unfenced_work_even_with_an_unresolved_stop(runtime):
    copilot, _, store = runtime
    with store.transaction() as tx:
        seed_work(tx, state="running")
        save(
            tx,
            "copilot.control",
            "gate",
            dict(
                fenced=False,
                epoch=copilot.services.ownership.epoch,
                revision=0,
            ),
        )
        tx.put("copilot.stops", "old-stop", dict(work_id=WORK, phase="outcome_uncertain"))
        assert not copilot.state.control(tx)["fenced"]
    response = asyncio.run(post_reset(copilot, command(copilot)))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_busy"
    with store.transaction(write=False) as tx:
        assert tx.get("copilot.resumes", "reset", required=False) is None
        assert tx.connection.execute("SELECT id FROM outbox WHERE id='reset'").fetchone() is None
        assert tx.require("work", WORK)["state"] == "running"
