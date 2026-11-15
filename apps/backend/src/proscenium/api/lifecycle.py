"""Independent host workers. HTTP and browser connections own none of their lifetimes."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.services import Services


class FeatureLifecycle:
    def __init__(self, services: Services, *, interval: float = 0.25):
        self.services, self.interval = services, interval
        self.stop = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.errors: dict[str, str] = {}

    async def start(self) -> None:
        runtime = self.services.extensions.get("copilot")
        if runtime is not None:
            await runtime.start()  # SQLite restoration only. No SDK client/session.
        for name, operation, gated in (
            ("job-cancellations", self.services.jobs.dispatch_cancellations, False),
            ("providers", self.services.extension("providers").pump, True),
            ("preview-dispatch", self.services.extension("previews").pump, True),
            ("preview-receipts", self.services.extension("previews").reconcile, False),
        ):
            self.tasks.append(
                asyncio.create_task(self.watch(name, operation, gated), name=f"proscenium-{name}")
            )
        self.services.extensions["lifecycle"] = self
        workflows = self.services.extension("workflows")
        self.tasks.append(
            asyncio.create_task(
                self.watch_async("workflow-receipts", workflows.reconcile),
                name="proscenium-workflow-receipts",
            )
        )

    async def watch_async(self, name: str, operation: Callable[[str], Awaitable[Any]]) -> None:
        """Await async workflow observation directly, including while execution is held."""
        while not self.stop.is_set():
            project_id = self.services.registry.active_id()
            if project_id:
                try:
                    await operation(project_id)
                    self.errors.pop(name, None)
                except DomainError as exc:
                    if exc.code not in {"project_busy", "scope_denied"}:
                        self.errors[name] = exc.code
                except Exception:
                    self.errors[name] = "internal_error"
            try:
                await asyncio.wait_for(self.stop.wait(), self.interval)
            except TimeoutError:
                pass

    async def watch(self, name: str, operation: Callable[[str], Any], gated: bool) -> None:
        while not self.stop.is_set():
            project_id = self.services.registry.active_id()
            if project_id:
                try:
                    if gated:
                        self.services.gate.require(project_id)
                    # Do not cancel this await: to_thread cancellation does not stop
                    # an HTTP submission. Shutdown waits for its bounded settlement.
                    await asyncio.to_thread(operation, project_id)
                    self.errors.pop(name, None)
                except DomainError as exc:
                    if exc.code not in {"project_busy", "scope_denied"}:
                        self.errors[name] = exc.code
                except Exception:
                    self.errors[name] = "internal_error"
            try:
                await asyncio.wait_for(self.stop.wait(), self.interval)
            except TimeoutError:
                pass

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
