"""Independent host workers. HTTP and browser connections own none of their lifetimes."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from functools import partial
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.db.outbox import has_unsettled_outbox
from proscenium.services import Services


class FeatureLifecycle:
    def __init__(
        self, services: Services, *, interval: float = 0.25, idle_interval: float | None = None
    ):
        self.services, self.interval = services, interval
        self.idle_interval = (
            idle_interval
            if idle_interval is not None
            else (max(interval, 2.0) if interval >= 0.2 else interval * 4)
        )
        self.stop = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.errors: dict[str, str] = {}
        self._ticks: list[asyncio.Event] = []
        self._project_id: str | None = None

    async def start(self) -> None:
        runtime = self.services.extensions.get("copilot")
        if runtime is not None:
            await runtime.start()  # SQLite restoration only. No SDK client/session.
        for name, operation, gated in (
            ("job-cancellations", self.services.jobs.dispatch_cancellations, False),
            ("providers", self.services.extension("providers").pump, True),
            ("vlm-reviews", self.services.extension("providers").pump_reviews, True),
            ("preview-dispatch", self.services.extension("previews").pump, True),
            ("preview-receipts", self.services.extension("previews").reconcile, False),
        ):
            self._watch(name, partial(self._run_sync, operation, gated))
        self.services.extensions["lifecycle"] = self
        workflows = self.services.extension("workflows")
        self._watch("workflow-receipts", workflows.reconcile)
        self.tasks.append(
            asyncio.create_task(self._coordinate(), name="proscenium-feature-coordinator")
        )

    async def _run_sync(
        self, operation: Callable[[str], Any], gated: bool, project_id: str
    ) -> None:
        if gated:
            self.services.gate.require(project_id)
        # Shutdown waits for thread-backed operations to settle before
        # releasing ownership. Cancellation would not stop an HTTP request.
        await asyncio.to_thread(operation, project_id)

    def _watch(self, name: str, operation: Callable[[str], Awaitable[Any]]) -> None:
        tick = asyncio.Event()
        self._ticks.append(tick)
        self.tasks.append(
            asyncio.create_task(self._run(name, operation, tick), name=f"proscenium-{name}")
        )

    async def _run(
        self, name: str, operation: Callable[[str], Awaitable[Any]], tick: asyncio.Event
    ) -> None:
        while not self.stop.is_set():
            await tick.wait()
            tick.clear()
            if self.stop.is_set():
                break
            project_id = self._project_id
            if project_id:
                try:
                    await operation(project_id)
                    self.errors.pop(name, None)
                except DomainError as exc:
                    if exc.code not in {"project_busy", "scope_denied"}:
                        self.errors[name] = exc.code
                except Exception:
                    self.errors[name] = "internal_error"

    def _activity(self) -> tuple[str | None, bool]:
        registry = self.services.registry
        with registry.store.transaction(write=False) as tx:
            state = tx.get("registry", "active", required=False)
            if state is None:
                return None, False
            project_id = state["project_id"]
            # Terminal receipt reconciliation may still need to release a claim
            # after a crash between the job commit and release in the registry.
            claims = tx.connection.execute(
                "SELECT EXISTS(SELECT 1 FROM resource_claims WHERE project_id=?)", (project_id,)
            ).fetchone()[0]
        store = registry.project_store(project_id)
        with store.transaction(write=False) as tx:
            # Held Copilot sends are not jobs to dispatch. Uncertain Copilot
            # intents still use the same settlement predicates as project switches.
            pending = tx.connection.execute(
                "SELECT EXISTS(SELECT 1 FROM outbox WHERE state IN ('pending','dispatching','outcome_uncertain') "
                "AND kind NOT LIKE 'copilot.%') OR EXISTS (SELECT 1 FROM records WHERE "
                "(namespace='jobs' AND json_extract(body,'$.state') NOT IN ('succeeded','failed','cancelled')) "
                "OR (namespace='work' AND json_extract(body,'$.state') NOT IN ('completed','failed','stopped')) "
                "OR (namespace='native_workflows' AND json_extract(body,'$.phase') NOT IN ('failed','launched')))"
            ).fetchone()[0]
            active = bool(pending or claims or has_unsettled_outbox(tx))
        if not active:
            # A terminal supervisor can still be exiting when its last receipt
            # settles. Reap owned children even when we skip workflow DB scans.
            workflows = self.services.extensions.get("workflows")
            if workflows is not None and hasattr(workflows, "reap_idle"):
                workflows.reap_idle()
        return project_id, active

    async def _coordinate(self) -> None:
        with self.services.lifecycle_signal.subscribe() as wakeup:
            was_active = False
            while not self.stop.is_set():
                wakeup.clear()
                try:
                    project_id, active = await asyncio.to_thread(self._activity)
                    self.errors.pop("coordinator", None)
                except DomainError as exc:
                    if exc.code not in {"project_busy", "scope_denied"}:
                        self.errors["coordinator"] = exc.code
                    project_id, active = None, False
                except Exception:
                    self.errors["coordinator"] = "internal_error"
                    project_id, active = None, False
                # Run once on entry and once on settlement for receipt and child
                # reaping; idle safety-net ticks check SQLite but skip all six pumps.
                if project_id and (active or was_active or project_id != self._project_id):
                    for tick in self._ticks:
                        tick.set()
                self._project_id = project_id
                was_active = active
                if self.stop.is_set():
                    break
                with suppress(TimeoutError):
                    if active:
                        # Coalesce commit bursts during active work to preserve cadence.
                        await asyncio.wait_for(self.stop.wait(), self.interval)
                    else:
                        await asyncio.wait_for(wakeup.wait(), self.idle_interval)

    async def close(self) -> None:
        cleanup = asyncio.create_task(self._close(), name="proscenium-feature-settlement")
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError()

    async def _close(self) -> None:
        self.stop.set()
        self.services.lifecycle_signal.notify()
        for tick in self._ticks:
            tick.set()
        runtime = self.services.extensions.get("copilot")
        try:
            # Fence and reap only the owned Copilot runtime, never detached workers.
            if runtime is not None:
                await runtime.close()
        finally:
            try:
                # Cancel/join preparation and async export acknowledgement before
                # waiting on the observer which may itself be queued behind that IO.
                workflows = self.services.extensions.get("workflows")
                if workflows is not None:
                    await workflows.close()
            finally:
                # Never release clients/store ownership beneath a remaining writer.
                await asyncio.gather(*self.tasks, return_exceptions=True)
                self.tasks.clear()
                await self.services.extension("providers").close()
