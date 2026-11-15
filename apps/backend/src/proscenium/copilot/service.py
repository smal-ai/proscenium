"""One host-owned asynchronous delivery service, independent of browser lifetimes."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.db.store import now
from proscenium.services import Services

from . import cold_stop
from .context import Contexts
from .io_policy import file_io
from .native import Native
from .read_recovery import interrupt as interrupt_read_wait
from .read_recovery import probe as probe_read_wait
from .read_recovery import retired_stage as retired_read_stage
from .state import SYSTEM, TERMINAL, State, key, receipt, save
from .tools import DISCOVERY_READS, READ_TOOLS, TOOLS, Tools, read_arguments
from .workflows import WORKFLOW_TOOLS
from .workflows import arguments as workflow_arguments

CHARGEABLE_MCP_TOOLS = frozenset({"create_generation", "generate_3d", "generate_gaussian_world"})

CONTINUATION_TEXT = "Continue the previously authorised work from its recorded state. Do not repeat completed operations."


@dataclass
class CallbackOwner:
    """One exact SDK callback registration, including cold resumes of the same ID."""

    project_id: str
    session_id: str | None
    generation: str = field(default_factory=lambda: uuid4().hex)
    retired: bool = False
    settled: bool = False
    failure: str | None = None
    failure_handled: bool = False
    progress_at: float = 0
    progress_sequence: int = 0
    activity_sequence: int = 0


def retired_housekeeping(event: dict[str, Any]) -> bool:
    # Never use a broad session/assistant/tool prefix here. Completion of a model
    # turn is not proof of settlement, and execution/content must not be discarded.
    return event.get("type") in {"session.shutdown", "session.idle", "assistant.idle"} or (
        event.get("type") in {"hook.start", "hook.end"}
        and event.get("data", {}).get("hookType") == "sessionEnd"
    )


def wire(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, list):
        return [wire(v) for v in value]
    if hasattr(value, "value"):
        return value.value
    return value


def empty(pending: dict[str, Any]) -> bool:
    return not (
        pending.get("items")
        or pending.get("steeringMessages")
        or pending.get("inFlightSteeringCount")
    )


class Copilot:
    def __init__(
        self,
        services: Services,
        *,
        native: Any = None,
        policy_resolver: Callable[[str], dict[str, Any]] | None = None,
    ):
        self.services = services
        self.contexts = Contexts(services)
        self.state = State(services, self.contexts, policy_resolver=policy_resolver)
        # Validation and process creation are deferred until explicit execution authority.
        self.native = native
        self.sessions: dict[str, Any] = {}
        self.sends: dict[str, asyncio.Task[None]] = {}
        self.worker: asyncio.Task[None] | None = None
        self.wake = asyncio.Event()
        self.closing = False
        self.callback_owners: dict[str, CallbackOwner] = {}
        self.session_callback_owners: dict[int, CallbackOwner] = {}
        self.send_projects: dict[str, str] = {}
        self.send_callback_owners: dict[str, CallbackOwner] = {}
        self.pause_owner: dict[str, str] = {}
        self.checked_projects: set[str] = set()
        self.read_wait_recovery: dict[str, dict[str, Any]] = {}

    def restore(self, project_id: str) -> None:
        if project_id:
            with self.services.registry.project_store(project_id).transaction() as tx:
                if tx.all("requests") or tx.get("copilot.session", "native", required=False):
                    self.state.fence(tx, "awaiting_resume")
                    for request in tx.all("requests"):
                        if request["receipt"]["state"] == "dispatching":
                            self.state.request_state(
                                tx,
                                request["request_id"],
                                "outcome_uncertain",
                                detail_code="awaiting_resume",
                            )
                        elif request["receipt"]["state"] == "stop_requested":
                            stop = tx.require("copilot.stops", request["request_id"])
                            save(
                                tx,
                                "copilot.stops",
                                request["request_id"],
                                {**stop, "phase": "outcome_uncertain"},
                            )
                            self.state.request_state(
                                tx,
                                request["request_id"],
                                "outcome_uncertain",
                                detail_code="awaiting_resume",
                            )
                    for operation in tx.all("copilot.resumes"):
                        if operation["state"] in {"pending", "dispatching"}:
                            save(
                                tx,
                                "copilot.resumes",
                                operation["body"]["operation_id"],
                                {**operation, "state": "outcome_uncertain"},
                            )
                    for work in tx.all("work"):
                        if work["state"] not in TERMINAL and work["state"] != "pending":
                            self.state.work_state(
                                tx,
                                work["work_id"],
                                "awaiting_resume",
                                evidence="new backend epoch with closed execution gate",
                            )
                    for decision in tx.all("decisions"):
                        if (
                            decision.get("delivery_state") == "pending"
                            and decision["state"] != "pending"
                        ):
                            decision["delivery_state"] = "outcome_uncertain"
                            self.save_decision(tx, decision)
                    for stage in tx.all("copilot.permissions"):
                        if stage.get("phase") in {"pending", "claimed", "reply_prepared"}:
                            save(
                                tx,
                                "copilot.permissions",
                                key(stage["session_id"], stage["native_id"]),
                                {**stage, "phase": "outcome_uncertain", "interrupted_at": now()},
                            )
        self.checked_projects.add(project_id)

    async def start(self) -> None:
        if self.worker is not None:
            return
        project_id = self.services.registry.active_id()
        if project_id:
            self.restore(project_id)
        self.worker = asyncio.create_task(self.run(), name="proscenium-copilot-delivery")

    def notify(self) -> None:
        self.wake.set()

    def callback_failure(
        self, owner: CallbackOwner, code: str, event: dict[str, Any] | None = None
    ) -> None:
        """Keep old-project incidents private and scoped. Never write an inactive Store.

        Only this registration's current project may consume its failure. The
        diagnostic retains original identity without native payloads/reasoning.
        """
        if not owner.failure_handled and not self.closing:
            owner.failure = code
        current = self.callback_owners.get(owner.project_id) is owner
        if current:
            self.services.gate.close(owner.project_id)
        event = event or {}
        try:
            digest = hashlib.sha256(canonical(event)).hexdigest()
        except Exception:
            digest = None
        diagnostic = {
            "project_id": owner.project_id,
            "native_session_id": owner.session_id,
            "callback_generation": owner.generation,
            "native_event_id": event.get("id"),
            "event_type": event.get("type"),
            "event_sha256": digest,
            "code": code,
            "retired": owner.retired,
            "settled_before_retirement": owner.settled,
            "observed_at": now(),
        }
        try:
            with self.services.registry.store.transaction() as tx:
                identity = "copilot.callback." + key(
                    owner.project_id,
                    owner.session_id or "unbound",
                    owner.generation,
                    str(event.get("id", code)),
                )
                if tx.get("private", identity, required=False) is None:
                    tx.put("private", identity, diagnostic)
            if current and not owner.retired and not self.closing:
                with self.services.activation_lock:
                    if self.services.registry.active_id() == owner.project_id:
                        with self.services.registry.project_store(
                            owner.project_id
                        ).transaction() as tx:
                            self.state.fence(tx, code)
        except Exception:
            # The in-memory gate/failure still fences its original owner if storage
            # itself fails. Never convert that exception into a different project fault.
            pass
        self.notify()

    def retire_callback(self, project_id: str, session: Any, *, settled: bool) -> None:
        owner = self.session_callback_owners.get(id(session))
        if owner is not None:
            if owner.project_id != project_id or owner.session_id != session.session_id:
                raise DomainError("attribution_unavailable", 409)
            owner.retired = True
            owner.settled = owner.settled or settled

    def retire_open_callbacks(self) -> None:
        # Called before owned client teardown. Already retired registrations keep
        # their settlement proof and any separately scoped unresolved incident.
        for owner in self.callback_owners.values():
            if not owner.retired:
                owner.retired = True
                owner.failure_handled = True

    async def close(self) -> None:
        self.closing = True
        self.wake.set()
        project_id = self.services.registry.active_id()
        try:
            if project_id:
                self.services.gate.close(project_id)
                with self.services.registry.project_store(project_id).transaction() as tx:
                    if tx.all("requests"):
                        self.state.fence(tx, "awaiting_resume")
        finally:
            if self.worker:
                self.worker.cancel()
                await asyncio.gather(self.worker, return_exceptions=True)
                self.worker = None
            try:
                if self.native:
                    self.retire_open_callbacks()
                    await self.native.close()  # Fence/reap BEFORE discarding in-flight tasks.
            finally:
                for task in self.sends.values():
                    task.cancel()
                await asyncio.gather(*self.sends.values(), return_exceptions=True)
                self.sends.clear()
                self.send_projects.clear()
                self.send_callback_owners.clear()
                self.sessions.clear()
                self.pause_owner.clear()

    async def failed(self, project_id: str, code: str) -> None:
        owner = self.callback_owners.get(project_id)
        if self.services.registry.active_id() != project_id:
            if owner is not None:
                self.callback_failure(owner, code)
            else:
                self.services.gate.close(project_id)
            return
        if owner is not None:
            owner.failure = None
            owner.failure_handled = True
        self.services.gate.close(project_id)
        try:
            self.persist_failure(project_id, code)
        finally:
            if self.native:
                self.retire_open_callbacks()
                await self.native.close()
            self.sessions.clear()
            self.pause_owner.clear()

    def persist_failure(self, project_id: str, code: str) -> None:
        with self.services.registry.project_store(project_id).transaction() as tx:
            self.state.fence(tx, code)
            for record in tx.all("requests"):
                if record["receipt"]["state"] in {
                    "dispatching",
                    "acknowledged",
                    "running",
                    "deferred",
                }:
                    self.state.request_state(
                        tx, record["request_id"], "outcome_uncertain", detail_code=code
                    )
            for work in tx.all("work"):
                if work["state"] in {"running", "awaiting_user"}:
                    self.state.work_state(tx, work["work_id"], "awaiting_resume")

    async def run(self) -> None:
        while not self.closing:
            self.wake.clear()
            project_id = self.services.registry.active_id()
            if project_id:
                boundary = "tick"
                started = time.monotonic()
                try:
                    await self.tick(project_id)
                    if self.native and self.sessions:
                        boundary, started = "health", time.monotonic()
                        await self.check_health(project_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.runtime_error(project_id, boundary, exc, started)
                    await self.failed(
                        project_id,
                        exc.code
                        if isinstance(exc, DomainError)
                        else "provider_unavailable"
                        if isinstance(exc, (ConnectionError, OSError, TimeoutError))
                        else "outcome_uncertain",
                    )
            try:
                await asyncio.wait_for(self.wake.wait(), 0.1 if self.sessions else 1)
            except TimeoutError:
                pass

    def runtime_error(self, project_id: str, boundary: str, exc: Exception, started: float) -> None:
        # Private structured diagnostics. Never persist exception text, prompts or auth data.
        with self.services.registry.project_store(project_id).transaction() as tx:
            tx.put(
                "copilot.runtime_errors",
                uuid4().hex,
                {
                    "at": now(),
                    "boundary": boundary,
                    "exception_type": type(exc).__qualname__,
                    "exception_module": type(exc).__module__,
                    "elapsed_seconds": time.monotonic() - started,
                    "code": exc.code if isinstance(exc, DomainError) else None,
                },
            )

    async def check_health(self, project_id: str) -> None:
        owner = self.callback_owners.get(project_id)
        before = owner.progress_sequence if owner else 0
        started = time.monotonic()
        try:
            await self.native.health()
        except TimeoutError as exc:
            self.runtime_error(project_id, "health_timeout", exc, started)
            if (
                owner is None
                or owner.retired
                or owner.failure
                or owner is not self.callback_owners.get(project_id)
                or owner.progress_sequence <= before
                or time.monotonic() - owner.progress_at > 5
                or not hasattr(self.native, "transport_alive")
                or not self.native.transport_alive()
            ):
                raise
            # One additional five-second probe, never indefinite timeout forgiveness.
            await self.native.health()

    async def session(self, project_id: str, *, allow_create: bool = False) -> Any:
        if project_id in self.sessions:
            cached = self.sessions[project_id]
            previous = self.session_callback_owners.get(id(cached))
            if previous is None or not previous.retired:
                return cached
            del self.sessions[project_id]
            if hasattr(cached, "disconnect"):
                try:
                    await cached.disconnect()
                except Exception:
                    self.callback_failure(previous, "provider_unavailable")
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            association = tx.get("copilot.session", "native", required=False)
        if not association and not allow_create:
            raise DomainError("native_session_missing", 503)
        if self.native is None:
            self.native = Native(self.services)
        session_id = association["session_id"] if association else None
        owner = CallbackOwner(project_id, session_id)
        self.callback_owners[project_id] = owner
        tools = Tools(self.services, self.state, project_id)
        # Native session events can precede create/resume ACK. Session ID is bound by the SDK
        # callback context below once known, or by its event's sessionId on session.start.
        holder: dict[str, str] = {"session_id": session_id} if session_id else {}

        def on_event(event: Any) -> None:
            owner.activity_sequence += 1
            value = wire(event)
            sid = holder.get("session_id") or value.get("data", {}).get("sessionId")
            if sid:
                holder["session_id"] = sid
                owner.session_id = sid
            if self.closing:
                owner.retired = True
                owner.failure_handled = True
            if not owner.retired and self.services.registry.active_id() != project_id:
                # Activation may precede the delivery task's disconnect pass. Read
                # settlement only; this does not relax the reducer's inactive guard.
                owner.retired = True
                try:
                    self.services.registry.assert_settled(project_id)
                    owner.settled = True
                except DomainError:
                    pass
            if owner.retired:
                if owner.settled and retired_housekeeping(value):
                    return
                self.callback_failure(owner, "attribution_unavailable", value)
                return
            if not sid:
                # session.start is emitted before create returns; execution must not occur yet.
                if value.get("type", "").startswith(("assistant.", "tool.", "user.", "subagent.")):
                    self.callback_failure(owner, "attribution_unavailable", value)
                return
            holder["session_id"] = sid
            try:
                progress_candidate = (
                    value["type"]
                    in {
                        "assistant.message_delta",
                        "assistant.message",
                        "tool.execution_start",
                        "tool.execution_complete",
                        "subagent.started",
                        "subagent.completed",
                        "subagent.failed",
                        "assistant.usage",
                    }
                    and time.monotonic() - owner.progress_at >= 0.25
                )
                previous = None
                if progress_candidate:
                    with self.services.registry.project_store(project_id).transaction(
                        write=False
                    ) as tx:
                        previous = tx.get("copilot.native", key(sid, value["id"]), required=False)
                self.state.reduce(project_id, sid, value)
                self.state.drain_pending(project_id, sid)
                if progress_candidate and previous is None:
                    with self.services.registry.project_store(project_id).transaction(
                        write=False
                    ) as tx:
                        reduced = tx.get("copilot.native", key(sid, value["id"]), required=False)
                    if reduced and reduced["attributed"]:
                        owner.progress_at = time.monotonic()
                        owner.progress_sequence += 1
                if value["type"] == "session.error":
                    self.callback_failure(owner, "provider_unavailable", value)
            except Exception:
                self.callback_failure(owner, "attribution_unavailable", value)
            self.notify()

        async def permission(request: Any, invocation: Any) -> Any:
            owner.activity_sequence += 1
            if owner.retired or self.closing:
                return PermissionDecisionReject()
            return await self.permission(project_id, wire(request), invocation)

        async def user_input(request: Any, invocation: Any) -> Any:
            owner.activity_sequence += 1
            if owner.retired or self.closing:
                raise DomainError("stale_decision", 409)
            return await self.user_input(project_id, wire(request), invocation)

        def prompt_hook(data: dict[str, Any], context: Any) -> dict[str, str]:
            owner.activity_sequence += 1
            if owner.retired or self.closing:
                raise DomainError("permission_denied", 403)
            agent = data.get("agentId")
            if not agent:
                return {}  # Root uses immutable per-message extension_context. No global prompt.
            sid = holder.get("session_id")
            with self.services.registry.project_store(project_id).transaction() as tx:
                if self.state.control(tx)["fenced"]:
                    raise DomainError("permission_denied", 403)
                binding = self.state.binding(tx, str(sid), "agent", agent)
                if binding is None:
                    raise DomainError("attribution_unavailable", 409)
                record = tx.require(
                    "requests", binding.get("context_request_id") or binding["request_id"]
                )
                payload = tx.require("copilot.payloads", record["request_id"])
                save(
                    tx,
                    "copilot.hooks",
                    key(str(sid), agent),
                    {
                        **binding,
                        "agent_id": agent,
                        "context_id": payload["context_id"],
                        "payload_sha256": hashlib.sha256(canonical(payload)).hexdigest(),
                    },
                )
            return {"additionalContext": canonical(payload).decode()}

        native_tools = []
        for tool in tools.definitions():

            async def observed_handler(invocation: Any, handler: Any = tool.handler) -> Any:
                owner.activity_sequence += 1
                return await handler(invocation)

            native_tools.append(replace(tool, handler=observed_handler))
        session = await self.native.open(
            project_id,
            session_id,
            dict(
                on_event=on_event,
                on_permission_request=permission,
                on_user_input_request=user_input,
                hooks={"on_user_prompt_submitted": prompt_hook},
                tools=native_tools,
            ),
        )
        holder["session_id"] = session.session_id
        owner.session_id = session.session_id
        self.session_callback_owners[id(session)] = owner
        with self.services.registry.project_store(project_id).transaction() as tx:
            save(
                tx,
                "copilot.session",
                "native",
                {
                    "session_id": session.session_id,
                    "owner_epoch": self.services.ownership.epoch,
                    "created_at": (association or {}).get("created_at", now()),
                },
            )
        self.sessions[project_id] = session
        return session

    async def tick(self, project_id: str) -> None:
        if project_id not in self.checked_projects:
            with self.services.registry.project_store(project_id).transaction(write=False) as tx:
                previous = self.state.control(tx)
            if previous.get("reason") == "awaiting_resume":
                self.restore(project_id)
            self.checked_projects.add(project_id)
        for old_project, old_session in list(self.sessions.items()):
            if old_project != project_id:
                self.services.registry.assert_settled(old_project)
                self.retire_callback(old_project, old_session, settled=True)
                del self.sessions[old_project]
                if hasattr(old_session, "disconnect"):
                    try:
                        await old_session.disconnect()
                    except Exception:
                        owner = self.session_callback_owners.get(id(old_session))
                        if owner is not None:
                            self.callback_failure(owner, "provider_unavailable")
        owner = self.callback_owners.get(project_id)
        if owner is not None and owner.failure:
            code, owner.failure = owner.failure, None
            await self.failed(project_id, code)
            return
        for identity, task in list(self.sends.items()):
            if task.done():
                # send() persists all uncertainty itself.
                with suppress(asyncio.CancelledError):
                    try:
                        task.result()
                    except Exception as exc:
                        original_project = self.send_projects.get(identity)
                        original_owner = self.send_callback_owners.get(identity)
                        if original_owner is not None:
                            self.callback_failure(
                                original_owner,
                                exc.code
                                if isinstance(exc, DomainError)
                                else "provider_unavailable",
                            )
                        elif original_project == project_id:
                            raise
                        elif original_project is not None:
                            self.callback_failure(
                                CallbackOwner(
                                    original_project, None, generation=key("send", identity)
                                ),
                                exc.code
                                if isinstance(exc, DomainError)
                                else "provider_unavailable",
                            )
                del self.sends[identity]
                self.send_projects.pop(identity, None)
                self.send_callback_owners.pop(identity, None)
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            stops = [
                (r["request_id"], tx.require("copilot.stops", r["request_id"]))
                for r in tx.all("requests")
                if r["submission"]["type"] == "stop" and r["receipt"]["state"] == "stop_requested"
            ]
            resumes = [r for r in tx.all("copilot.resumes") if r["state"] == "pending"]
        if stops:
            await self.stop(project_id, stops[0][1]["work_id"], stops[0][0])
            return
        if resumes:
            await self.resume(project_id, resumes[0]["body"]["operation_id"])
            return
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            if self.state.control(tx)["fenced"]:
                return
            records = tx.all("requests")
            active = [w for w in tx.all("work") if w["state"] not in TERMINAL | {"pending"}]
            steers = [
                r
                for r in records
                if r["submission"]["type"] == "steer" and r["receipt"]["state"] == "accepted"
            ]
            queued = sorted(
                [
                    r
                    for r in records
                    if r["submission"]["type"] == "queue" and r["receipt"]["state"] == "held"
                ],
                key=lambda r: tx.require("copilot.effective", r["request_id"])["fifo"],
            )
            queue_released = self.state.control(tx).get("queue_released", True)
        if steers:
            for record in steers:
                self.launch_send(project_id, record["request_id"])
        elif not active and not self.sends and queued and queue_released:
            self.launch_send(project_id, queued[0]["request_id"])
        elif active and not self.sends and project_id in self.sessions:
            await self.settle(project_id, self.sessions[project_id])

    def launch_send(self, project_id: str, request_id: str) -> None:
        if request_id not in self.sends:
            self.send_projects[request_id] = project_id
            self.sends[request_id] = asyncio.create_task(
                self.send(project_id, request_id), name=f"copilot-send:{request_id}"
            )

    async def send(self, project_id: str, request_id: str) -> None:
        try:
            session = await self.session(project_id, allow_create=True)
            owner = self.session_callback_owners.get(id(session))
            if owner is not None:
                self.send_callback_owners[request_id] = owner
            with self.services.registry.project_store(project_id).transaction() as tx:
                if self.state.control(tx)["fenced"]:
                    return
                record = tx.require("requests", request_id)
                if record["receipt"]["state"] not in {"held", "accepted"}:
                    return
                payload = self.contexts.payload(tx, record)
                tx.put("copilot.payloads", request_id, payload)
                tx.claim_intent(request_id)
                self.state.request_state(tx, request_id, "dispatching")
                work = tx.get("work", record["receipt"]["work_id"], required=False)
                if work is None:
                    work = dict(
                        schema_version="1.0",
                        work_id=record["receipt"]["work_id"],
                        project_id=project_id,
                        originating_request_id=request_id,
                        directive_request_ids=[],
                        state="pending",
                        state_revision=0,
                        native_session_id=session.session_id,
                        job_ids=[],
                        generation_task_id=record["generation_task_id"],
                    )
                    tx.put("work", work["work_id"], work, schema="WorkRecord")
                if record["submission"]["type"] == "queue":
                    self.state.work_state(
                        tx, work["work_id"], "running", native_session_id=session.session_id
                    )
                save(
                    tx,
                    "copilot.sends",
                    request_id,
                    {"state": "dispatching", "session_id": session.session_id},
                )
                save(tx, "copilot.idle", session.session_id, {"idle": False})
                save(tx, "copilot.last_delivery", work["work_id"], {"delivery_id": request_id})
            self.services.gate.resume(
                project_id, reconciliation_evidence="explicit owner request in fresh live project"
            )
            project = Path(self.services.registry.get(project_id)["path"])
            attachments: list[dict[str, Any]] = [
                {"type": "file", "path": str(project / f["path"])}
                for f in payload["attached_files"]
            ]
            context = self.contexts.get(project_id, record["submission"]["context_id"])
            attachments.append(
                {
                    "type": "extension_context",
                    "extensionId": "proscenium",
                    "title": "Turn context",
                    "capturedAt": context["captured_at"],
                    "payload": payload,
                }
            )
            text = record["submission"]["text"]
            message_id = await asyncio.wait_for(
                session.send(
                    text,
                    display_prompt=text,
                    mode="immediate" if record["submission"]["type"] == "steer" else "enqueue",
                    client_turn_id=request_id,
                    attachments=attachments,
                ),
                30,
            )
            with self.services.registry.project_store(project_id).transaction() as tx:
                self.state.bind(
                    tx,
                    session.session_id,
                    "message",
                    message_id,
                    {
                        "request_id": request_id,
                        "work_id": record["receipt"]["work_id"],
                        "delivery_id": request_id,
                    },
                )
                current = tx.require("requests", request_id)["receipt"]
                if current["native_message_id"] not in (None, message_id):
                    raise DomainError("attribution_unavailable", 409)
                self.state.request_state(
                    tx,
                    request_id,
                    "acknowledged" if current["state"] == "dispatching" else current["state"],
                    native_message_id=message_id,
                )
                save(
                    tx,
                    "copilot.sends",
                    request_id,
                    {
                        "state": "acknowledged",
                        "session_id": session.session_id,
                        "message_id": message_id,
                    },
                )
                tx.settle_intent(request_id, "acknowledged", {"native_message_id": message_id})
            self.state.drain_pending(project_id, session.session_id)
        except BaseException as exc:
            with self.services.registry.project_store(project_id).transaction() as tx:
                record = tx.require("requests", request_id)
                if record["receipt"]["state"] not in TERMINAL:
                    self.state.request_state(
                        tx,
                        request_id,
                        "outcome_uncertain",
                        detail_code=exc.code
                        if isinstance(exc, DomainError)
                        else "provider_unavailable",
                    )
                self.state.fence(tx, "outcome_uncertain")
                send = tx.get("copilot.sends", request_id, required=False)
                if send:
                    save(tx, "copilot.sends", request_id, {**send, "state": "outcome_uncertain"})
                    tx.settle_intent(
                        request_id,
                        "outcome_uncertain",
                        {"detail": "send outcome requires reconciliation"},
                    )
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self.notify()

    async def settle(self, project_id: str, session: Any) -> None:
        with self.services.registry.project_store(project_id).transaction() as tx:
            if any(
                not e["resolved"] and e["session_id"] == session.session_id
                for e in tx.all("copilot.pending_events")
            ):
                self.state.fence(tx, "attribution_unavailable")
                return
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            idle = tx.get("copilot.idle", session.session_id, required=False)
            if not idle or not idle.get("idle") or not idle.get("work_id"):
                return
            work = tx.require("work", idle["work_id"])
            last_delivery = tx.get("copilot.last_delivery", work["work_id"], required=False)
            if last_delivery and idle.get("delivery_id") != last_delivery["delivery_id"]:
                return
            requests = [
                tx.require("requests", r)
                for r in [work["originating_request_id"], *work["directive_request_ids"]]
            ]
            if work["state"] not in {"running", "awaiting_user"}:
                return
            continuation = (
                tx.get("copilot.continuations", last_delivery["delivery_id"], required=False)
                if last_delivery
                else None
            )
            continued = bool(
                continuation
                and continuation.get("work_id") == work["work_id"]
                and (
                    completion := tx.get(
                        "copilot.completion", continuation["request_id"], required=False
                    )
                )
                and completion.get("candidate")
                and completion.get("delivery_id") == continuation["delivery_id"]
            )
            if not continued and any(
                r["receipt"]["state"] not in TERMINAL
                and not (tx.get("copilot.completion", r["request_id"], required=False) or {}).get(
                    "candidate"
                )
                for r in requests
            ):
                return
            if any(
                i["work_id"] == work["work_id"] and i["state"] not in TERMINAL
                for i in tx.all("invocations")
            ):
                return
            if any(
                d["work_id"] == work["work_id"] and d["state"] == "pending"
                for d in tx.all("decisions")
            ):
                return
        if not empty(await self.native.pending(session)):
            return
        with self.services.registry.project_store(project_id).transaction() as tx:
            if self.state.control(tx)["fenced"]:
                return
            for record in requests:
                if record["receipt"]["state"] not in TERMINAL:
                    self.state.request_state(tx, record["request_id"], "completed")
            if work["state"] == "awaiting_user":
                self.state.work_state(tx, work["work_id"], "running")
            self.state.work_state(tx, work["work_id"], "completed")
        self.notify()

    async def stop(self, project_id: str, work_id: str, operation_id: str) -> None:
        store = self.services.registry.project_store(project_id)
        try:
            with store.transaction() as tx:
                phase = tx.require("copilot.stops", operation_id)
                was_terminal = tx.require("work", work_id)["state"] in TERMINAL
                if phase["phase"] in {"confirmed", "outcome_uncertain"}:
                    return
                self.state.fence(tx, "stop")
                if phase["phase"] != "admitted":
                    raise DomainError("invalid_state_transition", 409)
                tx.claim_intent(operation_id)
                phase.update(phase="pausing", pause="dispatching")
                save(tx, "copilot.stops", operation_id, phase)
            session = await self.session(project_id)
            # Exclusive, non-idempotent: NEVER retry a pause whose ACK is uncertain.
            await self.native.pause(session, True)
            self.pause_owner[project_id] = operation_id
            with store.transaction() as tx:
                phase.update(phase="settling_sends", pause="acknowledged")
                save(tx, "copilot.stops", operation_id, phase)
            # A send must not reach the immediate lane AFTER our one Abort. Settle admitted
            # transport calls while holding the queued lane, then cancel exactly once.
            if self.sends:
                await asyncio.wait_for(asyncio.gather(*list(self.sends.values())), 35)
            with store.transaction() as tx:
                phase.update(phase="aborting", abort="dispatching")
                save(tx, "copilot.stops", operation_id, phase)
                if not was_terminal:
                    save(tx, "copilot.idle", session.session_id, {"idle": False})
            await asyncio.wait_for(session.abort(), 10)
            with store.transaction() as tx:
                phase = tx.require("copilot.stops", operation_id)
                phase.update(phase="reconciling", abort="acknowledged")
                save(tx, "copilot.stops", operation_id, phase)
            pending = await self.native.pending(session)
            with store.transaction(write=False) as tx:
                records = [
                    r
                    for r in tx.all("requests")
                    if r["receipt"]["work_id"] == work_id and r["submission"]["type"] != "stop"
                ]
                sends = [tx.get("copilot.sends", r["request_id"], required=False) for r in records]
            if any(s and s["state"] == "outcome_uncertain" for s in sends):
                raise DomainError("attribution_unavailable", 409)
            with store.transaction(write=False) as tx:
                if any(
                    not e["resolved"] and e["session_id"] == session.session_id
                    for e in tx.all("copilot.pending_events")
                ):
                    raise DomainError("attribution_unavailable", 409)
            owned = {
                r["receipt"]["native_message_id"]: r["submission"]["text"]
                for r in records
                if r["receipt"]["native_message_id"]
            }
            with store.transaction(write=False) as tx:
                for continuation in tx.all("copilot.continuation_sends"):
                    binding = tx.require("copilot.continuations", continuation["operation_id"])
                    if binding["work_id"] == work_id:
                        if continuation["state"] != "acknowledged":
                            raise DomainError("attribution_unavailable", 409)
                        owned[continuation["message_id"]] = CONTINUATION_TEXT
            for item in pending.get("items", []):
                native_id = item.get("messageId")
                if native_id not in owned:
                    raise DomainError("attribution_unavailable", 409)
                result = await self.native.withdraw(session, owned[native_id], native_id)
                with store.transaction() as tx:
                    save(tx, "copilot.withdrawals", key(operation_id, native_id), result)
                # removed=False is NOT a successful withdrawal. Re-observe the actual queue.
            for _ in range(50):
                pending = await self.native.pending(session)
                recovery = self.read_wait_recovery.get(project_id)
                owner = self.callback_owners.get(project_id)
                if (
                    empty(pending)
                    and recovery
                    and owner
                    and not owner.retired
                    and recovery["session_id"] == session.session_id
                    and recovery["callback_generation"] == owner.generation
                ):
                    with store.transaction() as tx:
                        interrupt_read_wait(
                            tx,
                            session.session_id,
                            operation_id,
                            recovery["proofs"],
                            self.services.ownership.epoch,
                            pending,
                        )
                with store.transaction(write=False) as tx:
                    live = [
                        i
                        for i in tx.all("invocations")
                        if i["work_id"] == work_id and i["state"] not in TERMINAL
                    ]
                    idle = tx.get("copilot.idle", session.session_id, required=False) or {}
                if empty(pending) and not live and (was_terminal or idle.get("idle")):
                    break
                await asyncio.sleep(0.1)
            else:
                raise DomainError("provider_unavailable", 503)
            with store.transaction() as tx:
                phase = tx.require("copilot.stops", operation_id)
                for record in records:
                    current = tx.require("requests", record["request_id"])
                    if current["receipt"]["state"] not in TERMINAL:
                        self.state.request_state(tx, record["request_id"], "stopped")
                work = tx.require("work", work_id)
                if work["state"] not in TERMINAL:
                    self.state.work_state(
                        tx,
                        work_id,
                        "stopped",
                        evidence="Verified Stop barrier: pause and Abort acknowledged, sends and invocations settled, native queue empty",
                    )
                self.state.request_state(
                    tx,
                    operation_id,
                    "completed",
                    detail_code="already_idle" if work["state"] == "completed" else None,
                )
                phase.update(phase="confirmed", barrier_at=now(), pending_empty=True)
                save(tx, "copilot.stops", operation_id, phase)
                tx.settle_intent(operation_id, "acknowledged", {"barrier": phase["barrier_at"]})
        except Exception as exc:
            with store.transaction() as tx:
                phase = tx.require("copilot.stops", operation_id)
                phase["phase"] = "outcome_uncertain"
                save(tx, "copilot.stops", operation_id, phase)
                detail_code = exc.code if isinstance(exc, DomainError) else "provider_unavailable"
                self.state.request_state(
                    tx,
                    operation_id,
                    "outcome_uncertain",
                    detail_code=detail_code,
                )
                if tx.connection.execute(
                    "SELECT 1 FROM outbox WHERE id=? AND state IN ('dispatching','outcome_uncertain')",
                    (operation_id,),
                ).fetchone():
                    tx.settle_intent(
                        operation_id, "outcome_uncertain", {"detail_code": detail_code}
                    )
                self.state.fence(tx, "outcome_uncertain")
            # Terminating the owned runtime is an execution fence, not invented cancellation evidence.
            if self.native:
                self.retire_open_callbacks()
                await self.native.close()
            self.sessions.clear()

    def admit_resume(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if caller.channel != "owner":
            raise DomainError("owner_session_required", 401)
        validate("ResumeCommand", body)
        with self.services.registry.project_store(body["project_id"]).transaction() as tx:

            def effect() -> dict[str, Any]:
                resource = (
                    tx.require("work", body["work_id"])
                    if body["work_id"]
                    else self.services.registry.get(body["project_id"])
                )
                if resource["state_revision"] != body["expected_revision"]:
                    raise DomainError(
                        "revision_conflict", 409, current_revision=resource["state_revision"]
                    )
                if body["mode"] == "continue_work" and (
                    not body["work_id"]
                    or resource["state"] not in {"stopped", "failed", "awaiting_resume"}
                ):
                    raise DomainError("invalid_state_transition", 409)
                tx.put("copilot.resumes", body["operation_id"], {"body": body, "state": "pending"})
                tx.enqueue(body["operation_id"], "copilot.resume", body)
                return receipt(
                    body["operation_id"],
                    body["work_id"] or body["project_id"],
                    "accepted",
                    body["expected_revision"],
                )

            result, replayed = tx.operation("copilot.resume", body["operation_id"], body, effect)
            current = tx.require("copilot.resumes", body["operation_id"])
            return {
                **result,
                "state": "accepted"
                if current["state"] in {"pending", "dispatching"}
                else current["state"],
                "replayed": replayed,
            }, replayed

    async def reconcile(self, project_id: str) -> dict[str, Any]:
        cold_resume = project_id not in self.sessions
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            prior_invocations = [
                i["invocation_id"]
                for i in tx.all("invocations")
                if i["state"] not in TERMINAL and i.get("invocation_id")
            ]
        session = await self.session(project_id)
        history: list[dict[str, Any]] = []
        if hasattr(session, "get_events"):
            history = [wire(event) for event in await session.get_events()]
            for event in history:
                self.state.replay_event(project_id, session.session_id, event)
            self.state.drain_pending(project_id, session.session_id)
            # Exactly one persisted native correlation proves admission, not successful execution.
            with self.services.registry.project_store(project_id).transaction() as tx:
                for record in tx.all("requests"):
                    if (
                        record["receipt"]["state"] not in {"dispatching", "outcome_uncertain"}
                        or record["submission"]["type"] == "stop"
                    ):
                        continue
                    matches = [
                        e
                        for e in history
                        if e["type"] == "user.message"
                        and not e.get("data", {}).get("isAutopilotContinuation")
                        and e.get("data", {}).get("clientTurnId") == record["request_id"]
                    ]
                    if len(matches) == 1:
                        native_id = matches[0]["data"]["messageId"]
                        self.state.request_state(
                            tx, record["request_id"], "acknowledged", native_message_id=native_id
                        )
                        send = tx.require("copilot.sends", record["request_id"])
                        save(
                            tx,
                            "copilot.sends",
                            record["request_id"],
                            {**send, "state": "acknowledged", "message_id": native_id},
                        )
                        intent = tx.connection.execute(
                            "SELECT state FROM outbox WHERE id=?", (record["request_id"],)
                        ).fetchone()
                        if intent and intent[0] in {"dispatching", "outcome_uncertain"}:
                            tx.settle_intent(
                                record["request_id"],
                                "acknowledged",
                                {"history_message_id": native_id},
                            )
                for continuation in tx.all("copilot.continuation_sends"):
                    if continuation["state"] not in {"dispatching", "outcome_uncertain"}:
                        continue
                    matches = [
                        e
                        for e in history
                        if e["type"] == "user.message"
                        and not e.get("data", {}).get("isAutopilotContinuation")
                        and e.get("data", {}).get("clientTurnId") == continuation["operation_id"]
                    ]
                    if len(matches) == 1:
                        save(
                            tx,
                            "copilot.continuation_sends",
                            continuation["operation_id"],
                            {
                                **continuation,
                                "state": "acknowledged",
                                "message_id": matches[0]["data"]["messageId"],
                            },
                        )
        pending = await self.native.pending(session)
        departed = getattr(self.native, "departed_process", None)
        if cold_resume and departed and empty(pending):
            raw_history = (
                [wire(e) for e in await session.get_raw_events()]
                if hasattr(session, "get_raw_events")
                else []
            )
            validated_targets: dict[str, str] = {}
            root = Path(self.services.registry.get(project_id)["path"])
            for event in raw_history:
                permission = event.get("data", {}).get("permissionRequest", {})
                if event["type"] != "permission.requested" or permission.get("kind") != "read":
                    continue
                starts = [
                    e
                    for e in raw_history
                    if e["type"] == "tool.execution_start"
                    and e.get("data", {}).get("toolCallId") == permission.get("toolCallId")
                ]
                if len(starts) != 1 or starts[0]["data"].get("toolName") != "rg":
                    continue
                try:
                    targets, special = file_io(root, permission)
                    args = starts[0]["data"].get("arguments", {})
                    declared = {**permission, "path": args.get("paths")}
                    declared.pop("resolvedPath", None)
                    argument_targets, _ = file_io(root, declared)
                    if (
                        not special
                        and targets == argument_targets
                        and len(targets) == 1
                        and not targets[0].is_dir()
                    ):
                        validated_targets[event["id"]] = str(targets[0])
                except DomainError:
                    pass
            checkpoint_head: dict[str, Any] = {"verified": False}
            for event in raw_history:
                request = event.get("data", {}).get("permissionRequest", {})
                if (
                    event["type"] == "permission.requested"
                    and request.get("kind") == "custom-tool"
                    and request.get("toolName") == "checkpoint_sources"
                ):
                    try:
                        self.custom_tool_arguments(project_id, request)
                        from proscenium.sources.common import git

                        head = (
                            git(
                                Path(self.services.registry.get(project_id)["path"]),
                                "rev-parse",
                                "--verify",
                                "HEAD",
                                required=False,
                            )
                            .decode()
                            .strip()
                            or None
                        )
                        checkpoint_head = {"verified": True, "head": head}
                    except DomainError:
                        pass
                    break
            with self.services.registry.project_store(project_id).transaction() as tx:
                current_send = any(not task.done() for task in self.sends.values()) or any(
                    s.get("state") in {"dispatching", "outcome_uncertain"}
                    for s in tx.all("copilot.sends") + tx.all("copilot.continuation_sends")
                )
                uncertain_stop_ids = [
                    r["request_id"]
                    for r in tx.all("requests")
                    if r["submission"]["type"] == "stop"
                    and r["receipt"]["state"] == "outcome_uncertain"
                    and tx.require("copilot.stops", r["request_id"])["phase"] == "outcome_uncertain"
                ]
                uncertain_stop_id = uncertain_stop_ids[0] if len(uncertain_stop_ids) == 1 else None
                owner = self.callback_owners.get(project_id)
                if owner and not owner.retired:
                    self.read_wait_recovery[project_id] = {
                        "session_id": session.session_id,
                        "departed_process": departed,
                        "callback_generation": owner.generation,
                        "activity_sequence": owner.activity_sequence,
                        "proofs": probe_read_wait(
                            self.state,
                            tx,
                            session.session_id,
                            raw_history,
                            {
                                "departed": departed,
                                "prior_invocations": prior_invocations,
                                "current_send": current_send,
                                "uncertain_stop_id": uncertain_stop_id,
                                "validated_targets": validated_targets,
                                "pending_empty": {
                                    "items",
                                    "steeringMessages",
                                    "inFlightSteeringCount",
                                }
                                <= pending.keys()
                                and empty(pending),
                                "permission_fingerprints": {
                                    e["id"]: hashlib.sha256(
                                        canonical(e["data"]["permissionRequest"])
                                    ).hexdigest()
                                    for e in history
                                    if e["type"] == "permission.requested"
                                },
                            },
                        ),
                    }
                self.state.observe_native_denials(
                    tx,
                    session.session_id,
                    raw_history,
                    {
                        **departed,
                        "invocation_ids": prior_invocations,
                        "pending_empty": True,
                        "current_send": current_send,
                        "permission_fingerprints": {
                            e["id"]: hashlib.sha256(
                                canonical(e["data"]["permissionRequest"])
                            ).hexdigest()
                            for e in history
                            if e["type"] == "permission.requested"
                        },
                    },
                )
                self.state.interrupt_uninvoked_checkpoint(
                    tx,
                    session.session_id,
                    raw_history,
                    {
                        **departed,
                        "invocation_ids": prior_invocations,
                        "pending_empty": True,
                        "current_send": current_send,
                        "checkpoint_head": checkpoint_head,
                        "permission_fingerprints": {
                            e["id"]: hashlib.sha256(
                                canonical(e["data"]["permissionRequest"])
                            ).hexdigest()
                            for e in history
                            if e["type"] == "permission.requested"
                        },
                    },
                )
                self.state.interrupt_coordinators(
                    tx,
                    session.session_id,
                    history,
                    {**departed, "invocation_ids": prior_invocations},
                )
            if uncertain_stop_id:
                await self.reconcile_cold_stop(project_id, session, uncertain_stop_id)
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            uncertain = [
                r["request_id"]
                for r in tx.all("requests")
                if r["receipt"]["state"] in {"dispatching", "outcome_uncertain"}
            ]
            live = [i for i in tx.all("invocations") if i["state"] not in TERMINAL]
            stops = [s for s in tx.all("copilot.stops") if s["phase"] == "outcome_uncertain"]
            continuations = [
                c for c in tx.all("copilot.continuation_sends") if c["state"] != "acknowledged"
            ]
            duplicates = tx.all("copilot.duplicates")
        # Absence is never proof a send did not happen. Conservative fence until exact history join.
        return {
            "pending_empty": empty(pending),
            "uncertain_request_ids": uncertain,
            "live_invocations": len(live),
            "uncertain_stops": len(stops),
            "uncertain_continuations": len(continuations),
            "duplicate_correlations": len(duplicates),
        }

    async def reconcile_cold_stop(self, project_id: str, session: Any, stop_id: str) -> None:
        sid = session.session_id
        recovery = self.read_wait_recovery.get(project_id)
        owner = self.callback_owners.get(project_id)
        if (
            not recovery
            or not owner
            or recovery["session_id"] != sid
            or len(recovery["proofs"]) > 1
            or any(p.get("existing_stop_request_id") != stop_id for p in recovery["proofs"])
            or not hasattr(self.native, "cold_stop_snapshot")
            or getattr(self.native, "non_continuing_session_id", None) != session.session_id
        ):
            return
        epoch = self.services.ownership.epoch
        observation_id = key(epoch, stop_id)
        store = self.services.registry.project_store(project_id)
        with store.transaction() as tx:
            if tx.get("copilot.cold_stop_observations", observation_id, required=False):
                return  # Non-idempotent pause is never retried in this owner epoch.
            phase = tx.require("copilot.stops", stop_id)
            stop = tx.require("requests", stop_id)
            work = tx.require("work", phase["work_id"])
            outbox = tx.connection.execute(
                "SELECT state,evidence FROM outbox WHERE id=?", (stop_id,)
            ).fetchone()
            observation = {
                "id": observation_id,
                "epoch": epoch,
                "session_id": session.session_id,
                "departed_process": recovery["departed_process"],
                "callback_generation": owner.generation,
                "activity_sequence": recovery["activity_sequence"],
                "status": "collecting",
                "started_at": now(),
                "previous_stop_phase": phase,
                "previous_stop_receipt": stop["receipt"],
                "stop_payload": stop["submission"],
                "previous_work": work,
                "previous_outbox": dict(state=outbox[0], evidence=outbox[1]) if outbox else None,
            }
            observation["candidate_records"] = [
                {
                    "namespace": namespace,
                    "id": identity,
                    "sha256": hashlib.sha256(
                        canonical(tx.require(namespace, identity))
                    ).hexdigest(),
                }
                for proof in recovery["proofs"]
                for namespace, identity in (
                    ("invocations", proof["invocation_id"]),
                    ("decisions", proof["decision_id"]),
                    ("copilot.permissions", key(session.session_id, proof["native_id"])),
                )
            ]
            tx.put("copilot.cold_stop_observations", observation_id, observation)

        def owned() -> bool:
            if (
                self.closing
                or self.services.registry.active_id() != project_id
                or self.services.ownership.epoch != epoch
                or owner.retired
                or owner.failure
                or self.callback_owners.get(project_id) is not owner
                or self.sessions.get(project_id) is not session
                or self.session_callback_owners.get(id(session)) is not owner
                or owner.generation != recovery["callback_generation"]
                or owner.activity_sequence != recovery["activity_sequence"]
                or any(not task.done() for task in self.sends.values())
                or getattr(self.native, "non_continuing_session_id", None) != sid
                or getattr(session, "session_id", None) != sid
                or not self.native.transport_alive()
            ):
                return False
            return True

        def stable() -> bool:
            if not owned():
                return False
            with store.transaction(write=False) as tx:
                allowed = {p["invocation_id"] for p in recovery["proofs"]}
                return (
                    self.state.control(tx)["fenced"]
                    and tx.require("copilot.stops", stop_id) == observation["previous_stop_phase"]
                    and tx.require("requests", stop_id)["receipt"]
                    == observation["previous_stop_receipt"]
                    and tx.require("work", work["work_id"]) == observation["previous_work"]
                    and all(
                        hashlib.sha256(canonical(tx.require(r["namespace"], r["id"]))).hexdigest()
                        == r["sha256"]
                        for r in observation["candidate_records"]
                    )
                    and {
                        i.get("invocation_id")
                        for i in tx.all("invocations")
                        if i["state"] not in TERMINAL
                    }
                    == allowed
                    and not any(
                        w["work_id"] != work["work_id"] and w["state"] not in TERMINAL | {"pending"}
                        for w in tx.all("work")
                    )
                    and not any(
                        s.get("state") != "acknowledged"
                        for s in tx.all("copilot.sends") + tx.all("copilot.continuation_sends")
                    )
                    and not any(
                        r["submission"]["type"] == "steer" and r["receipt"]["state"] not in TERMINAL
                        for r in tx.all("requests")
                    )
                    and not any(d["state"] == "pending" for d in tx.all("decisions"))
                )

        try:
            cold_stop.require(stable(), "wrong_owner_or_live_work")
            snapshot = await asyncio.wait_for(self.native.cold_stop_snapshot(session, stable), 30)
            cold_stop.require(stable(), "callback_or_owner_changed")
            observation["snapshot"] = snapshot
            with store.transaction() as tx:
                cold_stop.require(owned(), "callback_or_owner_changed")
                cold_stop.apply(self.state, tx, stop_id, recovery["proofs"], observation)
                observation.update(status="applied", finished_at=now())
                save(tx, "copilot.cold_stop_observations", observation_id, observation)
                cold_stop.require(owned(), "callback_or_owner_changed")
            self.pause_owner[project_id] = stop_id
        except Exception as exc:
            observation.update(
                status="uncertain",
                finished_at=now(),
                error_type=type(exc).__qualname__,
                error_reason=exc.details.get("reason") if isinstance(exc, DomainError) else None,
                snapshot=getattr(self.native, "latest_cold_stop_snapshot", {}),
            )
            with store.transaction() as tx:
                save(tx, "copilot.cold_stop_observations", observation_id, observation)

    async def resume(self, project_id: str, operation_id: str) -> None:
        store = self.services.registry.project_store(project_id)
        with store.transaction() as tx:
            operation = tx.require("copilot.resumes", operation_id)
            if operation["state"] != "pending":
                return
            body = operation["body"]
            operation["state"] = "dispatching"
            save(tx, "copilot.resumes", operation_id, operation)
            tx.claim_intent(operation_id)
        try:
            with store.transaction(write=False) as tx:
                association = tx.get("copilot.session", "native", required=False)
                attempted = bool(tx.all("copilot.sends"))
            if not association and not attempted and body["mode"] != "continue_work":
                evidence = {
                    "pending_empty": True,
                    "uncertain_request_ids": [],
                    "live_invocations": 0,
                    "uncertain_stops": 0,
                }
            else:
                evidence = await self.reconcile(project_id)
            if (
                not evidence["pending_empty"]
                or evidence["uncertain_request_ids"]
                or evidence["live_invocations"]
                or evidence["uncertain_stops"]
                or evidence.get("uncertain_continuations")
                or evidence.get("duplicate_correlations")
            ):
                raise DomainError("attribution_unavailable", 409)
            if body["mode"] != "reconcile_only":
                session = self.sessions.get(project_id)
                with store.transaction(write=False) as tx:
                    owned_pauses = [
                        s
                        for s in tx.all("copilot.stops")
                        if s["pause"] == "acknowledged" and s["phase"] == "confirmed"
                    ]
                if session and owned_pauses and project_id in self.pause_owner:
                    # Release only this adapter's proven pause and persist release intent first.
                    with store.transaction() as tx:
                        save(tx, "copilot.pause_release", operation_id, {"state": "dispatching"})
                    await self.native.pause(session, False)
                    self.pause_owner.pop(project_id, None)
                    with store.transaction() as tx:
                        save(tx, "copilot.pause_release", operation_id, {"state": "acknowledged"})
                        for stop_id_record in tx.all("requests"):
                            if stop_id_record["submission"]["type"] == "stop":
                                stop_id = stop_id_record["request_id"]
                                stop_record = tx.require("copilot.stops", stop_id)
                                if stop_record["pause"] == "acknowledged":
                                    save(
                                        tx,
                                        "copilot.stops",
                                        stop_id,
                                        {**stop_record, "pause": "released"},
                                    )
                with store.transaction() as tx:
                    if body["mode"] == "release_held_queue" and any(
                        w["state"] not in TERMINAL | {"pending"} for w in tx.all("work")
                    ):
                        raise DomainError("project_busy", 409)
                    control = self.state.control(tx)
                    save(
                        tx,
                        "copilot.control",
                        "gate",
                        {
                            **control,
                            "fenced": False,
                            "epoch": self.services.ownership.epoch,
                            "reconciled_epoch": self.services.ownership.epoch,
                            "activation_revision": self.services.registry.get(project_id)[
                                "state_revision"
                            ],
                            "revision": control["revision"] + 1,
                            "queue_released": body["mode"] == "release_held_queue",
                        },
                    )
                    if body["mode"] == "continue_work":
                        work = tx.require("work", body["work_id"])
                        request_ids = [
                            work["originating_request_id"],
                            *work["directive_request_ids"],
                        ]
                        active_payload = next(
                            (
                                (req_id, saved)
                                for req_id in reversed(request_ids)
                                if tx.require("requests", req_id)["receipt"]["state"]
                                not in {"cancelled", "failed"}
                                and (saved := tx.get("copilot.payloads", req_id, required=False))
                                is not None
                            ),
                            None,
                        )
                        if active_payload is None:
                            raise DomainError("attribution_unavailable", 409)
                        context_request_id, _ = active_payload
                        self.state.work_state(
                            tx,
                            work["work_id"],
                            "running",
                            explicit_resume=True,
                            evidence="explicit owner Resume after native reconciliation",
                        )
                        self.state.request_state(
                            tx, work["originating_request_id"], "running", continuing=True
                        )
                        save(
                            tx,
                            "copilot.completion",
                            work["originating_request_id"],
                            {"candidate": False},
                        )
                        binding = {
                            "request_id": work["originating_request_id"],
                            "context_request_id": context_request_id,
                            "work_id": work["work_id"],
                            "delivery_id": operation_id,
                        }
                        tx.put("copilot.continuations", operation_id, binding)
                        save(
                            tx,
                            "copilot.last_delivery",
                            work["work_id"],
                            {"delivery_id": operation_id},
                        )
                        save(tx, "copilot.idle", work["native_session_id"], {"idle": False})
                self.services.gate.resume(
                    project_id, reconciliation_evidence=canonical(evidence).decode()
                )
                if body["mode"] == "continue_work":
                    session = await self.session(project_id)
                    # This is an explicit continuation, never the original request body or a new task.
                    with store.transaction() as tx:
                        work = tx.require("work", body["work_id"])
                        payload = tx.require("copilot.payloads", binding["context_request_id"])
                        snapshot = tx.require("contexts", payload["context_id"])
                        self.contexts.validate_references(tx, snapshot)
                        tx.put(
                            "copilot.continuation_sends",
                            operation_id,
                            {
                                "operation_id": operation_id,
                                "state": "dispatching",
                                "session_id": session.session_id,
                            },
                        )
                    message_id = await asyncio.wait_for(
                        session.send(
                            CONTINUATION_TEXT,
                            mode="enqueue",
                            client_turn_id=operation_id,
                            attachments=[
                                {
                                    "type": "extension_context",
                                    "extensionId": "proscenium",
                                    "title": "Turn context",
                                    "capturedAt": snapshot["captured_at"],
                                    "payload": payload,
                                }
                            ],
                        ),
                        30,
                    )
                    with store.transaction() as tx:
                        self.state.bind(tx, session.session_id, "message", message_id, binding)
                        save(
                            tx,
                            "copilot.continuation_sends",
                            operation_id,
                            {
                                "operation_id": operation_id,
                                "state": "acknowledged",
                                "session_id": session.session_id,
                                "message_id": message_id,
                            },
                        )
            with store.transaction() as tx:
                operation["state"] = "completed"
                save(tx, "copilot.resumes", operation_id, operation)
                tx.settle_intent(operation_id, "acknowledged", evidence)
        except Exception as exc:
            with store.transaction() as tx:
                operation.update(
                    state="outcome_uncertain",
                    detail_code=exc.code
                    if isinstance(exc, DomainError)
                    else "provider_unavailable",
                )
                save(tx, "copilot.resumes", operation_id, operation)
                self.state.fence(tx, operation["detail_code"])
                if body["work_id"]:
                    work = tx.require("work", body["work_id"])
                    original = tx.require("requests", work["originating_request_id"])
                    self.state.request_state(
                        tx,
                        original["request_id"],
                        original["receipt"]["state"],
                        detail_code=operation["detail_code"],
                    )
                    continuation = tx.get(
                        "copilot.continuation_sends", operation_id, required=False
                    )
                    if continuation and continuation["state"] == "dispatching":
                        save(
                            tx,
                            "copilot.continuation_sends",
                            operation_id,
                            {**continuation, "state": "outcome_uncertain"},
                        )
                        if work["state"] == "running":
                            self.state.work_state(tx, work["work_id"], "awaiting_resume")
                tx.settle_intent(
                    operation_id, "outcome_uncertain", {"detail_code": operation["detail_code"]}
                )
            if self.native:
                self.retire_open_callbacks()
                await self.native.close()
            self.sessions.clear()
            self.pause_owner.clear()
        self.notify()

    def save_decision(self, tx: Any, decision: dict[str, Any]) -> None:
        revision = tx.revision("decisions", decision["decision_id"])
        decision["state_revision"] = revision + 1
        tx.put(
            "decisions",
            decision["decision_id"],
            decision,
            expected_revision=revision,
            schema="Decision",
        )
        tx.emit("decision.resolved", decision, actor=SYSTEM, work_id=decision["work_id"])

    async def decision(
        self,
        project_id: str,
        session_id: str,
        native_id: str,
        binding: dict[str, Any],
        kind: str,
        prompt: str,
        options: list[str],
    ) -> dict[str, Any] | None:
        decision_id = "decision-" + key(session_id, native_id)
        store = self.services.registry.project_store(project_id)
        fingerprint = hashlib.sha256(
            canonical(
                {
                    "kind": kind,
                    "prompt": prompt,
                    "options": options,
                    "native_id": native_id,
                    "binding": binding,
                }
            )
        ).hexdigest()
        with store.transaction() as tx:
            old = tx.get("decisions", decision_id, required=False)
            if self.state.control(tx)["fenced"]:
                return None
            if old:
                if old["fingerprint"] != fingerprint or old["delivery_state"] != "not_sent":
                    self.state.fence(tx, "outcome_uncertain")
                    return None
            else:
                decision = dict(
                    schema_version="1.0",
                    decision_id=decision_id,
                    project_id=project_id,
                    work_id=binding["work_id"],
                    kind=kind,
                    state="pending",
                    state_revision=0,
                    fingerprint=fingerprint,
                    prompt=prompt,
                    options=options,
                    invocation_id=binding.get("invocation_id"),
                    native_decision_id=native_id,
                    response=None,
                    resolved_by=None,
                    delivery_state="not_sent",
                )
                tx.put("decisions", decision_id, decision, schema="Decision")
                tx.emit(
                    "decision.requested",
                    decision,
                    actor=SYSTEM,
                    work_id=binding["work_id"],
                    request_id=binding["request_id"],
                )
                self.state.work_state(tx, binding["work_id"], "awaiting_user")
        while not self.closing:
            with store.transaction() as tx:
                decision = tx.require("decisions", decision_id)
                if kind == "permission" and self.state.control(tx)["fenced"]:
                    return None
                if decision["state"] != "pending":
                    if decision["state"] not in {"answered", "declined"}:
                        return None
                    if decision["delivery_state"] != "pending":
                        self.state.fence(tx, "outcome_uncertain")
                        return None
                    # Callback reply has no delivery acknowledgement. Do not claim acknowledged.
                    decision["delivery_state"] = "outcome_uncertain"
                    self.save_decision(tx, decision)
                    return decision["response"]
            await asyncio.sleep(0.05)
        return None

    def custom_tool_arguments(self, project_id: str, request: dict[str, Any]) -> bool:
        """Classify registered operation semantics, never prose/path words as shell commands."""
        name, arguments = request.get("toolName"), request.get("args")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise DomainError("invalid_request")
        if name in TOOLS:
            validate(TOOLS[name][2], arguments)
            if arguments.get("project_id", project_id) != project_id:
                raise DomainError("scope_denied", 403)
        elif name in READ_TOOLS or name in DISCOVERY_READS:
            read_arguments(name, arguments)
        elif name in WORKFLOW_TOOLS:
            workflow_arguments(name, arguments)
        else:
            return False
        return True

    async def permission(self, project_id: str, request: dict[str, Any], invocation: Any) -> Any:
        try:
            self.services.gate.require(project_id)
        except DomainError:
            return PermissionDecisionReject()
        sid = invocation.get("session_id")
        tool_id = request.get("toolCallId")
        fingerprint = hashlib.sha256(canonical(request)).hexdigest()
        claim_id = uuid4().hex
        with self.services.registry.project_store(project_id).transaction() as tx:
            binding = self.state.binding(tx, str(sid), "tool", tool_id)
            matches = [
                s
                for s in tx.all("copilot.permissions")
                if s.get("session_id") == sid
                and s.get("tool_call_id") == tool_id
                and s.get("fingerprint") == fingerprint
                and s.get("phase") != "completed"
                and not self.state.native_denied_stage(tx, s)
                and not self.state.interrupted_custom_stage(tx, s)
                and not retired_read_stage(tx, s)
            ]
            save(
                tx,
                "copilot.permission_callbacks",
                claim_id,
                {
                    "session_id": sid,
                    "tool_call_id": tool_id,
                    "fingerprint": fingerprint,
                    "state": "rejected_correlation",
                    "authority": "host",
                },
            )
            if (
                not binding
                or self.state.control(tx)["fenced"]
                or len(matches) != 1
                or matches[0].get("phase") != "pending"
                or any(matches[0].get(k) != v for k, v in binding.items())
                or tx.require("work", binding["work_id"])["state"]
                not in {"running", "awaiting_user"}
            ):
                self.state.fence(tx, "attribution_unavailable")
                return PermissionDecisionReject()
            binding = {**binding, "invocation_id": key(str(sid), "tool", str(tool_id))}
            stage = matches[0]
            native_id = stage["native_id"]
            stage_id = key(str(sid), native_id)
            stage = {**stage, "phase": "claimed", "claim_id": claim_id, "claimed_at": now()}
            save(tx, "copilot.permissions", stage_id, stage)
            save(
                tx,
                "copilot.permission_callbacks",
                claim_id,
                {
                    **stage,
                    "state": "claimed",
                    "authority": None,
                },
            )

        def reply(approve: bool, authority: str, denial_reason: str | None = None) -> Any:
            # Commit the exact claim/result before handing control back to the SDK.
            # Completion, not callback return, acknowledges native delivery.
            with self.services.registry.project_store(project_id).transaction() as tx:
                current = tx.require("copilot.permissions", stage_id)
                if (
                    current.get("claim_id") != claim_id
                    or current["phase"] != "claimed"
                    or self.state.control(tx)["fenced"]
                ):
                    approve, authority = False, "host"
                    denial_reason = (
                        "Execution is paused or the original invocation is no longer current."
                    )
                prepared = {
                    **current,
                    "phase": "reply_prepared",
                    "authority": authority,
                    "reply": "approve" if approve else "reject",
                    "reply_prepared_at": now(),
                    "delivery_state": "outcome_uncertain",
                    "denial_reason": denial_reason if not approve else None,
                }
                save(tx, "copilot.permissions", stage_id, prepared)
                save(
                    tx,
                    "copilot.permission_callbacks",
                    claim_id,
                    {**prepared, "state": "reply_prepared"},
                )
            return PermissionDecisionApproveOnce() if approve else PermissionDecisionReject()

        known_custom = False
        if request.get("kind") == "custom-tool":
            try:
                known_custom = self.custom_tool_arguments(project_id, request)
                if known_custom:
                    with self.services.registry.project_store(project_id).transaction(
                        write=False
                    ) as tx:
                        original = tx.require("invocations", binding["invocation_id"])
                        work = tx.require("work", original["work_id"])
                        if (
                            original.get("name") != request.get("toolName")
                            or original.get("native_id") != tool_id
                            or original.get("session_id") != sid
                            or original.get("owner_epoch") != self.services.ownership.epoch
                            or original.get("state") not in {"admitted", "running"}
                            or work["native_session_id"] != sid
                            or any(
                                original.get(k) != binding.get(k)
                                for k in ("work_id", "request_id", "delivery_id")
                            )
                            or original.get("arguments_sha256")
                            != hashlib.sha256(canonical(request["args"])).hexdigest()
                        ):
                            raise DomainError("attribution_unavailable", 409)
            except DomainError:
                return reply(False, "host")
            if known_custom:
                # This grants entry only. The handler retains path/source/job/budget/caller guards.
                return reply(True, "automatic_policy")

        is_io = request.get("kind") in {"read", "write"}
        if is_io:
            try:
                _, special_approval = file_io(
                    Path(self.services.registry.get(project_id)["path"]), request
                )
                with self.services.registry.project_store(project_id).transaction(
                    write=False
                ) as tx:
                    original = tx.require("invocations", binding["invocation_id"])
                    work = tx.require("work", original["work_id"])
                    record = tx.require("requests", binding["request_id"])
                    if (
                        original.get("session_id") != sid
                        or original.get("native_id") != tool_id
                        or original.get("owner_epoch") != self.services.ownership.epoch
                        or original.get("state") not in {"admitted", "running"}
                        or work["native_session_id"] != sid
                        or record["receipt"]["work_id"] != work["work_id"]
                        or any(
                            original.get(k) != binding.get(k)
                            for k in ("work_id", "request_id", "delivery_id")
                        )
                    ):
                        raise DomainError("attribution_unavailable", 409)
            except DomainError as error:
                reason = (
                    "The requested path is outside the allowed project files or inside protected runtime storage."
                    if error.code == "scope_denied"
                    else "The file permission request could not be validated against the current work."
                )
                return reply(False, "automatic_policy", reason)
            if not special_approval:
                return reply(True, "automatic_policy")

        if request.get("kind") == "shell" and (
            not isinstance(request.get("fullCommandText"), str)
            or not request["fullCommandText"].strip()
        ):
            return reply(False, "host")

        text = canonical(request).decode()
        # This is the owner's pragmatic tool policy, not an OS/network sandbox. Shell remains enabled.
        denied = (
            None
            if is_io
            else re.search(
                r"(?i)(\.env(?:\W|$)|\.ssh|credentials|credential[_ -]?store|github_token|/etc/shadow)",
                text,
            )
        )
        consequential = (
            True
            if is_io
            or (request.get("kind") == "mcp" and request.get("toolName") in CHARGEABLE_MCP_TOOLS)
            else re.search(
                r"(?i)(\b(push|purchase|install|upgrade|sudo|scp)\b|reset\s+--hard|rm\s+-[rf]|curl\s+.*(?:--upload-file|--data|-[dT]|-X\s*(?:POST|PUT|DELETE)))",
                request.get("fullCommandText", "") if request.get("kind") == "shell" else text,
            )
        )
        if denied:
            return reply(
                False,
                "automatic_policy",
                "The request matched a protected credential or environment-file rule. Review its paths and filters before continuing.",
            )
        if not consequential and request.get("kind") in {
            "shell",
            "mcp",
            "url",
        }:
            return reply(True, "automatic_policy")
        answer = await self.decision(
            project_id,
            str(sid),
            native_id,
            binding,
            "permission",
            text,
            ["approve_once", "decline"],
        )
        return reply(
            bool(answer and answer["action"] == "approve_once"), "owner" if answer else "host"
        )

    async def user_input(self, project_id: str, request: dict[str, Any], invocation: Any) -> Any:
        sid, native_id = invocation.get("session_id"), request.get("requestId")
        with self.services.registry.project_store(project_id).transaction() as tx:
            binding = self.state.binding(tx, str(sid), "decision", native_id)
            if not binding or not binding.get("invocation_id"):
                self.state.fence(tx, "attribution_unavailable")
        if not binding or not binding.get("invocation_id"):
            self.notify()
            raise DomainError("attribution_unavailable", 409)
        answer = await self.decision(
            project_id,
            str(sid),
            str(native_id),
            binding,
            "user_input",
            request.get("question", "Owner input required"),
            request.get("choices", []),
        )
        return {
            "answer": answer.get("answer", "Declined") if answer else "Cancelled by owner control",
            "wasFreeform": True,
        }


def install(
    services: Services,
    *,
    native: Any = None,
    policy_resolver: Callable[[str], dict[str, Any]] | None = None,
) -> Copilot:
    if services.runtime is not None or "contexts" in services.extensions:
        raise ValueError("Copilot/context services are already installed")
    runtime = Copilot(services, native=native, policy_resolver=policy_resolver)
    services.runtime = runtime
    services.extensions.update(contexts=runtime.contexts, copilot=runtime, execution=runtime)
    return runtime
