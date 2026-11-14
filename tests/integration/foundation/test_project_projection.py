import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from proscenium.api.composition import configure_services
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.copilot import install
from proscenium.db.store import Transaction, now
from proscenium.services import Services

OWNER = Caller("owner", "synthetic-owner")


class FakeNative:
    """Independent, durable synthetic project histories. No SDK client or model calls."""

    def __init__(self, histories=None):
        self.opens = []
        self.histories = histories if histories is not None else {}

    async def open(self, project_id, session_id, callbacks):
        known = "synthetic-native-" + project_id
        assert session_id in {None, known}
        self.opens.append((project_id, session_id))
        history = self.histories.setdefault(known, [])

        class Session:
            async def send(self, text, **kwargs):
                message = "message-" + kwargs["client_turn_id"]
                event = dict(
                    id=uuid4().hex,
                    type="user.message",
                    timestamp=now(),
                    data=dict(
                        messageId=message, content=text, clientTurnId=kwargs["client_turn_id"]
                    ),
                )
                history.append(copy.deepcopy(event))
                callbacks["on_event"](event)
                return message

            async def get_events(self):
                return copy.deepcopy(history)

            async def disconnect(self):
                pass

        session = Session()
        session.session_id = known
        return session

    async def pending(self, session):
        return {"items": [], "steeringMessages": [], "inFlightSteeringCount": 0}

    async def close(self):
        pass

    async def health(self):
        pass


def test_two_project_native_associations_restart_resume_and_activation_fence(services, tmp_path):
    async def run():
        native = FakeNative()
        runtime = install(services, native=native)
        configure_services(services)
        with services.registry.project_store("project-test").transaction() as tx:
            work = tx.require("work", "work-test")
            tx.put(
                "work",
                "work-test",
                {**work, "state": "completed", "state_revision": 1},
                expected_revision=0,
            )
        root = tmp_path / "second"
        root.mkdir()
        services.registry.register(
            {
                **services.registry.get("project-test"),
                "project_id": "project-second",
                "repository_id": "repo-second",
                "path": str(root),
            }
        )
        submissions = {}
        for project_id in ("project-test", "project-second"):
            if project_id != services.registry.active_id():
                services.activate_project(project_id)
                await runtime.tick(project_id)
            context = json.loads(Path("docs/contracts/examples/context.json").read_text())
            context.update(project_id=project_id, context_id="context-" + project_id)
            runtime.contexts.capture(context)
            body = dict(
                schema_version="1.0",
                project_id=project_id,
                request_id="request-" + project_id,
                type="queue",
                text="Synthetic authorised work",
                context_id=context["context_id"],
            )
            receipt, _ = runtime.state.submit(OWNER, body)
            await runtime.send(project_id, body["request_id"])
            record = services.registry.get(project_id)
            assert record["native_session_id"] == "synthetic-native-" + project_id
            assert record["state"] == "active"
            with services.registry.project_store(project_id).transaction() as tx:
                assert not runtime.state.control(tx)["fenced"]
                assert tx.require("requests", body["request_id"])["submission"] == body
                submissions[project_id] = body
                runtime.state.request_state(tx, body["request_id"], "completed")
                runtime.state.work_state(tx, receipt["work_id"], "completed")
        await runtime.close()
        services.close()
        restored = Services.create(services.settings)
        try:
            second_native = FakeNative(native.histories)
            second = install(restored, native=second_native)
            configure_services(restored)
            await second.start()
            assert restored.registry.get("project-second")["state"] == "awaiting_resume"
            assert second_native.opens == []
            assert (
                restored.registry.get("project-test")["native_session_id"]
                == "synthetic-native-project-test"
            )
            command = dict(
                schema_version="1.0",
                operation_id="explicit-resume",
                project_id="project-second",
                expected_revision=restored.registry.get("project-second")["state_revision"],
                mode="release_held_queue",
                work_id=None,
            )
            # With no work target, the real browser resumes using ProjectRecord CAS.
            second.admit_resume(OWNER, command)
            await second.resume("project-second", command["operation_id"])
            assert second_native.opens == [("project-second", "synthetic-native-project-second")]
            assert restored.registry.get("project-second")["state"] == "active"
            assert restored.extension("projects").snapshot("project-second")[
                "project"
            ] == restored.registry.get("project-second")
            with restored.registry.project_store("project-second").transaction(write=False) as tx:
                assert not second.state.control(tx)["fenced"]
                assert (
                    tx.require("requests", "request-project-second")["submission"]
                    == submissions["project-second"]
                )
                before = tx.watermark()
            restored.reconcile_project_projection("project-second")
            with restored.registry.project_store("project-second").transaction(write=False) as tx:
                assert tx.watermark() == before
            await second.close()
        finally:
            restored.close()

    asyncio.run(run())


def test_different_known_native_id_is_never_overwritten(services):
    store = services.registry.project_store("project-test")
    with store.transaction() as tx:
        tx.put(
            "copilot.session",
            "native",
            {
                "session_id": "native-original",
                "owner_epoch": services.ownership.epoch,
                "created_at": now(),
            },
        )
    with pytest.raises(DomainError, match="Native session identity changed"):
        with store.transaction() as tx:
            tx.put(
                "copilot.session",
                "native",
                {
                    "session_id": "native-replacement",
                    "owner_epoch": services.ownership.epoch,
                    "created_at": now(),
                },
                expected_revision=0,
            )
    with store.transaction(write=False) as tx:
        assert tx.require("copilot.session", "native")["session_id"] == "native-original"
    assert services.registry.get("project-test")["native_session_id"] == "native-original"


def test_projection_saga_recovers_lost_project_event_once(services, monkeypatch):
    original = Transaction.emit

    def fail(tx, name, *args, **kwargs):
        if name == "project.state_changed":
            raise RuntimeError("Synthetic project event acknowledgement loss")
        return original(tx, name, *args, **kwargs)

    store = services.registry.project_store("project-test")
    with monkeypatch.context() as patch:
        patch.setattr(Transaction, "emit", fail)
        with pytest.raises(RuntimeError):
            with store.transaction() as tx:
                tx.put(
                    "copilot.session",
                    "native",
                    {
                        "session_id": "native-durable",
                        "owner_epoch": services.ownership.epoch,
                        "created_at": now(),
                    },
                )
    record = services.registry.get("project-test")
    services.reconcile_project_projection("project-test")
    services.reconcile_project_projection("project-test")
    assert services.registry.get("project-test") == record
    with store.transaction(write=False) as tx:
        assert len(tx.events()["events"]) == 1


def test_late_activation_event_repair_cannot_clear_a_known_native_projection(services):
    configure_services(services)
    original = services.registry.get("project-test")
    body = dict(
        schema_version="1.0",
        operation_id="lost-activation",
        project_id="project-test",
        expected_revision=0,
    )
    with services.registry.store.transaction() as machine:
        machine.put(
            "project_commands",
            body["operation_id"],
            {"body": body, "actor_id": OWNER.subject_id, "event_record": original},
        )
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "copilot.session",
            "native",
            {
                "session_id": "native-established-after-activation",
                "owner_epoch": services.ownership.epoch,
                "created_at": now(),
            },
        )
    projects = services.extension("projects")
    current = services.registry.get("project-test")
    projects.project_event("project-test", body, OWNER)
    projects.project_event("project-test", body, OWNER)
    assert services.registry.get("project-test") == current
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        events = tx.events()["events"]
        assert len(events) == 3
        assert events[-2]["payload"]["native_session_id"] is None
        assert events[-1]["payload"] == current
