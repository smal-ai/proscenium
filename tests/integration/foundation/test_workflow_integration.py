"""Workflow composition/ownership only. No export, Editor, model or browser is run."""

import asyncio
import json
import threading
from pathlib import Path

import pytest
from proscenium.api.composition import configure_services, feature_routers
from proscenium.api.errors import DomainError
from proscenium.api.lifecycle import FeatureLifecycle
from proscenium.auth import Caller
from proscenium.db import Store
from proscenium.db.registry import Registry
from proscenium.projects.service import Projects
from proscenium.workflows import install

from tools.operations import recovery


def settled_work(services):
    with services.registry.project_store("project-test").transaction() as tx:
        work = tx.require("work", "work-test")
        tx.put(
            "work",
            "work-test",
            {**work, "state": "completed", "state_revision": 1},
            expected_revision=0,
        )


def preparation(services, phase):
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "native_workflows",
            "pre-admission",
            {
                "phase": phase,
                "job_id": "not-admitted",
                "kind": "unity",
                "project_id": "project-test",
            },
        )


def test_composition_is_idempotent_lazy_and_idle_close_is_synchronous(services):
    configure_services(services)
    workflows = services.extension("workflows")
    configure_services(services)
    assert workflows is services.extension("workflows")
    assert len(feature_routers()) == 6
    assert not workflows._pool._threads and not workflows._calls and not workflows._io_jobs
    services.close()
    assert workflows._pool._shutdown and workflows._close_complete
    workflows.close_idle()
    asyncio.run(workflows.close())
    assert services.ownership.file.closed


async def until(condition):
    async with asyncio.timeout(3):
        while not condition():
            await asyncio.sleep(0.005)


def test_async_observer_is_awaited_closed_gate_and_follows_project(services, tmp_path, monkeypatch):
    async def run():
        configure_services(services)
        workflows = services.extension("workflows")
        observed = []
        main_thread = threading.get_ident()

        async def reconcile(project_id):
            await asyncio.sleep(0)
            assert threading.get_ident() == main_thread
            observed.append(project_id)
            return []

        monkeypatch.setattr(workflows, "reconcile", reconcile)
        host = FeatureLifecycle(services, interval=0.005)
        await host.start()
        try:
            await until(lambda: "project-test" in observed)
            assert not services.gate._open
            assert services.extension("copilot").native is None
            settled_work(services)
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
            services.activate_project("project-second")
            await until(lambda: "project-second" in observed)
        finally:
            await host.close()
        assert workflows._close_complete and host.tasks == [] and host.errors == {}
        assert not services.ownership.file.closed

    asyncio.run(run())


@pytest.mark.parametrize("public_call", [False, True])
def test_standalone_close_refuses_active_writer_until_awaited_settlement(services, public_call):
    async def run():
        workflows = install(services)
        entered, release = threading.Event(), threading.Event()
        writes = []

        def writer():
            entered.set()
            assert release.wait(5)
            writes.append("settled")

        async def call():
            if public_call:
                async with workflows.operation():
                    await workflows.io(writer)
            else:
                await workflows.io(writer)

        task = asyncio.create_task(call())
        await until(entered.is_set)
        with pytest.raises(RuntimeError, match="Await workflows.close"):
            services.close()
        assert not services.ownership.file.closed and not workflows._closed
        closing = asyncio.create_task(workflows.close())
        await until(lambda: workflows._closed)
        assert not closing.done()
        with pytest.raises(RuntimeError):
            services.close()
        release.set()
        await closing
        await asyncio.gather(task, return_exceptions=True)
        assert writes == ["settled"] and workflows._pool._shutdown
        services.close()
        assert services.ownership.file.closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "phase", ["preparing", "exporting", "prepared", "outcome_uncertain", "failed", "launched"]
)
def test_durable_preparation_settlement_without_installed_extension(services, phase):
    settled_work(services)
    preparation(services, phase)
    registry = Registry(Store(services.registry.store.path))  # Operator/backup construction.
    if phase in {"failed", "launched"}:
        registry.assert_settled("project-test")
    else:
        with pytest.raises(DomainError) as error:
            registry.assert_settled("project-test")
        assert error.value.code == "project_busy"
    assert "workflows" not in services.extensions


def test_backup_refuses_pre_admission_writer_without_workflow_callbacks(
    services, tmp_path, monkeypatch
):
    settled_work(services)
    preparation(services, "exporting")
    settings = services.settings
    services.close()
    # R1 process attribution is a separate parent fix. Isolate this durable guard
    # from that earlier OS-process check, not from Registry or SQLite ownership.
    monkeypatch.setattr(recovery, "assert_stopped", lambda roots: None)
    destination = tmp_path / "backup"
    with pytest.raises(DomainError) as error:
        recovery.backup(settings, "project-test", destination)
    assert error.value.code == "project_busy" and not destination.exists()


def test_moved_project_rebind_checks_target_preparation_rows(services, tmp_path):
    settled_work(services)
    root = Path(services.registry.get("project-test")["path"])
    Projects.bootstrap(root, "Synthetic workflow rebind")
    (root / "project.json").write_text(
        json.dumps(
            dict(
                schema_version="1.0",
                project_id="project-test",
                repository_id="repo-test",
                name="Synthetic workflow rebind",
                paths={
                    name: name for name in ("brief", "assets", "scenes", "storyboards", "sequences")
                },
            )
        )
    )
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "project_identity",
            "identity",
            {"project_id": "project-test", "repository_id": "repo-test"},
        )
    preparation(services, "prepared")
    moved = tmp_path / "moved"
    root.rename(moved)
    with pytest.raises(DomainError) as error:
        Projects(services).rebind(
            Caller("owner", "owner-test"),
            dict(
                schema_version="1.0",
                operation_id="rebind-preparation",
                project_id="project-test",
                expected_revision=0,
                new_path=str(moved),
            ),
        )
    assert error.value.code == "project_busy"
    assert services.registry.get("project-test")["path"] == str(root)


def make_job(services, caller, admission, *, known=False):
    services.gate.resume("project-test", reconciliation_evidence="Synthetic workflow admission")
    job = services.jobs.admit(caller, admission)[0]
    if known:
        with services.registry.project_store("project-test").transaction() as tx:
            tx.put(
                "native_workflow_jobs",
                job["job_id"],
                {"kind": "unity", "identity": "owned-workflow"},
            )
            tx.put(
                "native_workflows",
                "owned-workflow",
                {
                    "kind": "unity",
                    "job_id": job["job_id"],
                    "project_id": job["project_id"],
                    "phase": "launched",
                    "admission": admission,
                },
            )
    services.gate.close("project-test")
    return job


def cancel_body(job):
    return dict(
        schema_version="1.0",
        operation_id="owner-cancel",
        project_id=job["project_id"],
        expected_revision=job["state_revision"],
    )


def test_unowned_unity_cancellation_rejected_before_receipt_or_mutation(
    services, caller, admission
):
    install(services)
    job = make_job(services, caller, admission)
    with pytest.raises(DomainError) as error:
        services.jobs.cancel(Caller("owner", "owner-test"), job["job_id"], cancel_body(job))
    assert error.value.code == "cancel_unsupported"
    assert services.jobs.get("project-test", job["job_id"]) == job
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        assert not tx.connection.execute("SELECT 1 FROM outbox WHERE kind='job.cancel'").fetchone()


def test_known_workflow_cancellation_delivers_with_closed_root_gate(
    services, caller, admission, monkeypatch
):
    install(services)
    job = make_job(services, caller, admission, known=True)
    markers = []

    def marker(s, actor, identity):
        markers.append((actor.attempt_id, identity))
        return {"job": s.jobs.get("project-test", identity)}

    monkeypatch.setattr("proscenium.workflows.service.cancel_unity_job", marker)
    receipt, _ = services.jobs.cancel(
        Caller("owner", "owner-test"), job["job_id"], cancel_body(job)
    )
    assert receipt["state"] == "cancel_requested"
    services.jobs.dispatch_cancellations("project-test")
    assert markers == [(job["attempt_id"], job["job_id"])]
    assert services.jobs.get("project-test", job["job_id"])["state"] == "cancel_requested"
    assert not services.gate._open


@pytest.mark.parametrize("register_first", [False, True])
def test_legacy_unity_canceller_keeps_its_own_authority(
    services, caller, admission, register_first
):
    markers = []

    def legacy(job):
        markers.append(job["job_id"])

    if register_first:
        services.jobs.cancellers["unity"] = legacy
    install(services)
    if not register_first:
        services.jobs.cancellers["unity"] = legacy
    job = make_job(services, caller, admission)
    services.jobs.cancel(Caller("owner", "owner-test"), job["job_id"], cancel_body(job))
    services.jobs.dispatch_cancellations("project-test")
    assert services.jobs.cancellers["unity"] is legacy
    assert markers == [job["job_id"]]
