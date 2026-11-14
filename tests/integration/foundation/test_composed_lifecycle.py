import asyncio
import threading
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from proscenium.api.composition import configure_services
from proscenium.api.lifecycle import FeatureLifecycle
from proscenium.app import create_app


class ManagedRuntime:
    def __init__(self):
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True


class Providers:
    def __init__(self):
        self.calls = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.closed = False

    def pump(self, project_id):
        self.calls.append(project_id)
        self.entered.set()
        assert self.release.wait(5), "Synthetic HTTP settlement was not released"

    async def close(self):
        assert self.release.is_set()
        self.closed = True


class Previews:
    def __init__(self):
        self.dispatches, self.receipts = [], []

    def pump(self, project_id):
        self.dispatches.append(project_id)

    def reconcile(self, project_id):
        self.receipts.append(project_id)


async def until(condition):
    async with asyncio.timeout(3):
        while not condition():
            await asyncio.sleep(0.005)


@pytest.mark.parametrize("cancel_shutdown", [False, True])
def test_workers_follow_active_project_and_wait_for_provider_thread(services, cancel_shutdown):
    async def run():
        configure_services(services)
        runtime, providers, previews = ManagedRuntime(), Providers(), Previews()
        services.extensions.update(copilot=runtime, providers=providers, previews=previews)
        host = FeatureLifecycle(services, interval=0.005)
        await host.start()
        await until(lambda: previews.receipts)
        assert runtime.started and providers.calls == [] and previews.dispatches == []
        services.gate.resume("project-test", reconciliation_evidence="Synthetic explicit Resume")
        await until(lambda: providers.entered.is_set())
        await until(lambda: previews.dispatches)
        before = len(previews.receipts)
        await until(lambda: len(previews.receipts) > before)
        # A blocked provider HTTP thread does not block passive receipts or dispatch.
        closing = asyncio.create_task(host.close())
        await until(lambda: runtime.closed)
        assert not providers.closed and not closing.done()
        if cancel_shutdown:
            closing.cancel()
            await asyncio.sleep(0)
            assert not providers.closed and not closing.done()
        providers.release.set()
        if cancel_shutdown:
            with pytest.raises(asyncio.CancelledError):
                await closing
        else:
            await closing
        assert providers.closed and not services.ownership.file.closed

    asyncio.run(run())


def test_watchers_do_not_capture_the_original_project(services, tmp_path):
    async def run():
        configure_services(services)
        runtime, providers, previews = ManagedRuntime(), Providers(), Previews()
        providers.release.set()
        services.extensions.update(copilot=runtime, providers=providers, previews=previews)
        host = FeatureLifecycle(services, interval=0.005)
        await host.start()
        await until(lambda: previews.receipts)
        services.gate.resume("project-test", reconciliation_evidence="Synthetic first Resume")
        await until(lambda: providers.calls)
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
        services.activate_project("project-second")
        await until(lambda: "project-second" in previews.receipts)
        assert "project-second" not in providers.calls
        services.gate.resume("project-second", reconciliation_evidence="Synthetic second Resume")
        await until(
            lambda: "project-second" in providers.calls and "project-second" in previews.dispatches
        )
        await host.close()
        assert host.errors == {}

    asyncio.run(run())


def test_supplied_container_is_passive_unless_opted_in(services):
    runtime = ManagedRuntime()
    configure_services(services)
    services.extensions["copilot"] = runtime
    with TestClient(create_app(services=services), base_url=services.settings.origin) as browser:
        assert browser.get("/health").status_code == 200
        assert not runtime.started
        assert "lifecycle" not in services.extensions
    assert not runtime.closed and not services.ownership.file.closed


def test_owned_lifespan_is_quiet_and_closes_its_owner(services):
    settings = services.settings
    services.close()
    with TestClient(create_app(settings), base_url=settings.origin) as browser:
        owned = browser.app.state.services
        runtime = owned.extension("copilot")
        assert runtime.worker is not None and runtime.native is None and runtime.sessions == {}
        assert browser.get("/api/v1/configuration").status_code == 401
        assert browser.get("/health").status_code == 200
        assert owned.extension("lifecycle").errors == {}
    assert owned.ownership.file.closed and runtime.worker is None and runtime.native is None


def test_compiled_resources_use_the_same_origin(services, tmp_path):
    dist = tmp_path / "dist"
    for folder in ("assets", "licences"):
        (dist / folder).mkdir(parents=True)
    (dist / "index.html").write_text('<script src="/assets/studio.js"></script>')
    (dist / "assets/studio.js").write_text("/* synthetic compiled application */")
    (dist / "licences/fonts.txt").write_text("Synthetic retained font licence")
    with TestClient(
        create_app(replace(services.settings, frontend_directory=dist), services=services),
        base_url=services.settings.origin,
    ) as browser:
        for path in ("/", "/assets/studio.js", "/licences/fonts.txt"):
            assert browser.get(path).status_code == 200
        assert browser.get("/licences/../../registry.sqlite3").status_code == 404
