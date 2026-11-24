"""SQLite-only admission and native event reduction. No network effects in transactions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.db import Transaction
from proscenium.db.registry import active_binding_revision
from proscenium.db.store import now
from proscenium.jobs import transition
from proscenium.services import Services

from . import eligibility
from .activity import (
    error_fields,
    failure_summary,
    log_activity_problem,
    safe_text,
    shell_exit_code,
)
from .context import Contexts
from .eligibility import control_status, execution_status
from .outcomes import get_tool_outcome

TERMINAL = {"completed", "failed", "stopped", "cancelled"}
SYSTEM = {"kind": "system", "id": "copilot-host"}


def key(*parts: str) -> str:
    return hashlib.sha256(canonical(list(parts))).hexdigest()


def save(tx: Transaction, namespace: str, identity: str, body: dict[str, Any]) -> None:
    old = tx.get(namespace, identity, required=False)
    tx.put(
        namespace,
        identity,
        body,
        expected_revision=tx.revision(namespace, identity) if old else None,
    )


def receipt(operation_id: str, resource_id: str, state: str, revision: int) -> dict[str, Any]:
    return dict(
        schema_version="1.0",
        operation_id=operation_id,
        resource_id=resource_id,
        state=state,
        state_revision=revision,
        replayed=False,
    )


class State:
    def __init__(
        self,
        services: Services,
        contexts: Contexts,
        policy_resolver: Callable[[str], dict[str, Any]] | None = None,
    ):
        self.services, self.contexts = services, contexts
        self.policy_resolver = policy_resolver

    def control(self, tx: Transaction) -> dict[str, Any]:
        return control_status(
            tx, self.services.ownership.epoch, self.services.registry.get(str(tx.project_id))
        )

    def fence(self, tx: Transaction, reason: str) -> None:
        control = self.control(tx)
        save(
            tx,
            "copilot.control",
            "gate",
            {**control, "fenced": True, "reason": reason, "revision": control["revision"] + 1},
        )
        self.services.gate.close(str(tx.project_id))

    def request_state(
        self, tx: Transaction, identity: str, state: str, *, continuing: bool = False, **fields: Any
    ) -> dict[str, Any]:
        record = tx.require("requests", identity)
        revision = tx.revision("requests", identity)
        current = record["receipt"]["state"]
        if current == state and all(record["receipt"].get(k) == v for k, v in fields.items()):
            return record
        if current in TERMINAL and current != state and not continuing:
            raise DomainError("invalid_state_transition", 409)
        record["receipt"] = {
            **record["receipt"],
            **fields,
            "state": state,
            "state_revision": revision + 1,
        }
        tx.put("requests", identity, record, expected_revision=revision, schema="RequestRecord")
        tx.emit(
            "request.state_changed",
            record,
            actor=SYSTEM,
            request_id=identity,
            work_id=record["receipt"]["work_id"],
        )
        return record

    def work_state(
        self,
        tx: Transaction,
        identity: str,
        state: str,
        *,
        explicit_resume: bool = False,
        evidence: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        work = tx.require("work", identity)
        transition("work", work["state"], state, explicit_resume=explicit_resume, evidence=evidence)
        revision = tx.revision("work", identity)
        work.update(fields, state=state, state_revision=revision + 1)
        tx.put("work", identity, work, expected_revision=revision, schema="WorkRecord")
        tx.emit(
            "work.state_changed",
            work,
            actor=SYSTEM,
            work_id=identity,
            request_id=work["originating_request_id"],
        )
        return work

    def active_binding_revision(self) -> int | None:
        return active_binding_revision(getattr(self.services.registry, "store", None))

    def idle_restart_hold(self, tx: Transaction, control: dict[str, Any]) -> bool:
        return eligibility.idle_restart_hold(tx, control)

    def submit(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if caller.channel != "owner":
            raise DomainError("owner_session_required", 401)
        validate("MessageSubmit", body)
        project_id, identity, kind = body["project_id"], body["request_id"], body["type"]
        with self.services.registry.project_store(project_id).transaction() as tx:

            def effect() -> dict[str, Any]:
                control = self.control(tx)
                auto_release = (
                    kind == "queue"
                    and self.idle_restart_hold(tx, control)
                    and all(r["receipt"]["state"] in TERMINAL for r in tx.all("requests"))
                    and not any(a["state"] == "pending" for a in tx.all("copilot.auto_releases"))
                )
                if kind == "queue":
                    work_id, task_id = "work-" + uuid4().hex, "task-" + uuid4().hex
                    project_policy = (
                        self.policy_resolver(project_id)
                        if self.policy_resolver
                        else (
                            self.services.extension("projects").settings(project_id)
                            if "projects" in self.services.extensions
                            else None
                        )
                    )
                    if project_policy is not None:
                        validate(
                            "ProjectSettings"
                            if "schema_version" in project_policy
                            else "ProjectSettingsRecord",
                            project_policy,
                        )
                        if project_policy.get("project_id", project_id) != project_id:
                            raise DomainError("scope_denied", 403)
                    profile = body.get(
                        "profile_id",
                        project_policy["default_profile_id"]
                        if project_policy and project_policy["default_profile_id"] is not None
                        else self.services.settings.runtime.get("profile_id", "authoring"),
                    )
                    profiles_list = getattr(
                        self.services,
                        "effective_profiles",
                        lambda: list(self.services.settings.profiles),
                    )()
                    by_id = {p["profile_id"]: p for p in profiles_list}
                    if profile in by_id:
                        selected = by_id[profile]
                        if (
                            selected.get("scope_status") != "project_scoped"
                            or not selected.get("lead_model_id")
                            or not selected.get("worker_model_ids")
                        ):
                            raise DomainError("capability_unavailable", 501)
                        lead_model = selected["lead_model_id"]
                        worker_model = selected["worker_model_ids"][0]
                        unity_worker_model = selected["worker_model_ids"][-1]
                    elif (
                        not body.get("profile_id")
                        and not (project_policy and project_policy.get("default_profile_id"))
                        and profile == self.services.settings.runtime.get("profile_id", "authoring")
                    ):
                        lead_model = self.services.settings.runtime.get("lead_model", "gpt-6.1-sol")
                        worker_model = self.services.settings.runtime.get("worker_model")
                        unity_worker_model = self.services.settings.runtime.get(
                            "unity_worker_model", worker_model
                        )
                    else:
                        raise DomainError("capability_unavailable", 501)
                    state = "held"
                    if not tx.all("requests") and not control["fenced"]:
                        project = self.services.registry.get(project_id)
                        save(
                            tx,
                            "copilot.control",
                            "gate",
                            {**control, "activation_revision": project["state_revision"]},
                        )
                else:
                    work_id = body["target_work_id"]
                    target_work = tx.get("work", work_id, required=False)
                    if target_work is None:
                        raise DomainError("no_active_work", 409)
                    work = target_work
                    if kind == "stop" and any(
                        w["work_id"] != work_id and w["state"] not in TERMINAL | {"pending"}
                        for w in tx.all("work")
                    ):
                        raise DomainError("no_active_work", 409)
                    if (
                        kind == "steer"
                        and execution_status(
                            tx,
                            self.services.ownership.epoch,
                            self.services.registry.get(project_id),
                        )["can_steer_work_id"]
                        != work_id
                    ):
                        raise DomainError("no_active_work", 409)
                    task_id = work["generation_task_id"]
                    original = tx.require("requests", work["originating_request_id"])
                    profile = original["effective_profile_id"]
                    state = "stop_requested" if kind == "stop" else "accepted"
                if kind != "stop":
                    context = tx.require("contexts", body["context_id"])
                    self.contexts.validate_references(tx, context)
                    if context["approved_findings"]:
                        clean_images = {
                            a["artifact_id"]
                            for a in context["attachments"]
                            if a["role"] in {"render", "concept", "reference"}
                        }
                        if any(
                            f["target"]["artifact_id"] not in clean_images
                            for f in context["approved_findings"]
                        ):
                            raise DomainError(
                                "invalid_request",
                                message="Approved findings require their clean target images in attachments",
                            )
                        reviews = self.services.extension("reviews")
                        method = getattr(reviews, "validate_and_submit_findings", None)
                        if method is None:
                            raise DomainError("capability_unavailable", 501)
                        method(tx, identity, context["approved_findings"])
                request_receipt = dict(
                    schema_version="1.0",
                    request_id=identity,
                    project_id=project_id,
                    type=kind,
                    state=state,
                    work_id=work_id,
                    native_message_id=None,
                    state_revision=0,
                    replayed=False,
                )
                record = dict(
                    schema_version="1.0",
                    request_id=identity,
                    project_id=project_id,
                    submission=body,
                    receipt=request_receipt,
                    accepted_at=now(),
                    effective_profile_id=profile,
                    generation_task_id=task_id,
                )
                tx.put("requests", identity, record, schema="RequestRecord")
                if kind == "queue":
                    save(
                        tx,
                        "copilot.effective",
                        identity,
                        {
                            "generation_attempt_limit": body.get(
                                "generation_attempt_limit",
                                self.services.settings.generation_attempt_limit
                                if project_policy is None
                                else project_policy["generation_attempt_limit"],
                            ),
                            "profile_id": profile,
                            "lead_model": lead_model,
                            "worker_model": worker_model,
                            "unity_worker_model": unity_worker_model,
                            "owner_epoch": self.services.ownership.epoch,
                            "fifo": tx.watermark() + 1,
                        },
                    )
                elif kind == "steer":
                    self.invalidate_work_completions(tx, work)
                    if work.get("native_session_id"):
                        save(tx, "copilot.idle", work["native_session_id"], {"idle": False})
                    self.work_state(
                        tx,
                        work_id,
                        work["state"],
                        directive_request_ids=[*work["directive_request_ids"], identity],
                    )
                else:
                    self.fence(tx, "stop")
                    save(
                        tx,
                        "copilot.stops",
                        identity,
                        {
                            "phase": "admitted",
                            "pause": "not_sent",
                            "abort": "not_sent",
                            "work_id": work_id,
                        },
                    )
                    self.cancel_decisions(tx, work_id)
                    # The control has its own stop_requested receipt. A recovery
                    # state must not take an undeclared intermediate work edge.
                    if work["state"] not in TERMINAL | {
                        "stop_requested",
                        "awaiting_resume",
                        "outcome_uncertain",
                    }:
                        self.work_state(tx, work_id, "stop_requested")
                tx.enqueue(
                    identity,
                    "copilot.stop" if kind == "stop" else "copilot.send",
                    {
                        "project_id": project_id,
                        "request_id": identity,
                    },
                )
                tx.emit(
                    "request.accepted",
                    record,
                    actor={"kind": "user", "id": caller.subject_id},
                    request_id=identity,
                    work_id=work_id,
                )
                if auto_release:
                    tx.put(
                        "copilot.auto_releases",
                        identity,
                        {
                            "request_id": identity,
                            "watermark": tx.watermark(),
                            "owner_epoch": self.services.ownership.epoch,
                            "active_binding_revision": self.active_binding_revision(),
                            "state": "pending",
                        },
                    )
                return request_receipt

            result, replayed = tx.operation("request.submit", identity, body, effect)
            # A retry returns the CURRENT projection, not a stale accepted receipt.
            return {**tx.require("requests", identity)["receipt"], "replayed": replayed}, replayed

    def cancel(
        self, caller: Caller, request_id: str, body: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        if caller.channel != "owner":
            raise DomainError("owner_session_required", 401)
        with self.services.registry.project_store(body["project_id"]).transaction() as tx:

            def effect() -> dict[str, Any]:
                record = tx.require("requests", request_id)
                if record["receipt"]["state_revision"] != body["expected_revision"]:
                    raise DomainError(
                        "revision_conflict",
                        409,
                        current_revision=record["receipt"]["state_revision"],
                    )
                if (
                    record["receipt"]["state"] not in {"accepted", "held"}
                    or record["submission"]["type"] != "queue"
                ):
                    raise DomainError("invalid_state_transition", 409)
                record = self.request_state(tx, request_id, "cancelled")
                if tx.get("work", record["receipt"]["work_id"], required=False):
                    self.work_state(tx, record["receipt"]["work_id"], "stop_requested")
                    self.work_state(tx, record["receipt"]["work_id"], "stopped")
                return receipt(
                    body["operation_id"],
                    request_id,
                    "cancelled",
                    record["receipt"]["state_revision"],
                )

            return tx.operation(
                "request.cancel", body["operation_id"], {**body, "request_id": request_id}, effect
            )

    def invalidate_work_completions(self, tx: Transaction, work: dict[str, Any]) -> None:
        for req_id in [work["originating_request_id"], *work["directive_request_ids"]]:
            existing = tx.get("copilot.completion", req_id, required=False)
            if existing and (existing.get("candidate") or existing.get("public_terminal_message")):
                save(
                    tx,
                    "copilot.completion",
                    req_id,
                    {**existing, "candidate": False, "public_terminal_message": False},
                )

    def cancel_decisions(self, tx: Transaction, work_id: str) -> None:
        for stage in tx.all("copilot.permissions"):
            if stage["work_id"] == work_id and stage.get("phase") in {"pending", "claimed"}:
                save(
                    tx,
                    "copilot.permissions",
                    key(stage["session_id"], stage["native_id"]),
                    {**stage, "phase": "cancelled", "cancelled_at": now()},
                )
        for decision in tx.all("decisions"):
            if decision["work_id"] == work_id and decision["state"] == "pending":
                revision = tx.revision("decisions", decision["decision_id"])
                decision.update(state="cancelled", state_revision=revision + 1)
                tx.put(
                    "decisions",
                    decision["decision_id"],
                    decision,
                    expected_revision=revision,
                    schema="Decision",
                )
                tx.emit("decision.resolved", decision, actor=SYSTEM, work_id=work_id)

    def answer(
        self, caller: Caller, decision_id: str, body: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        if caller.channel != "owner":
            raise DomainError("owner_session_required", 401)
        validate("DecisionAnswer", body)
        with self.services.registry.project_store(body["project_id"]).transaction() as tx:

            def effect() -> dict[str, Any]:
                decision = tx.require("decisions", decision_id)
                if decision["state"] != "pending" or self.control(tx)["fenced"]:
                    raise DomainError("stale_decision", 409)
                revision = tx.revision("decisions", decision_id)
                if (
                    revision != body["expected_revision"]
                    or decision["fingerprint"] != body["fingerprint"]
                ):
                    raise DomainError("stale_decision", 409)
                allowed = {
                    "permission": {"approve_once", "decline"},
                    "user_input": {"answer", "decline"},
                    "generation_budget": {"extend_budget", "decline"},
                }
                if body["action"] not in allowed[decision["kind"]]:
                    raise DomainError("invalid_request")
                decision.update(
                    state="declined" if body["action"] == "decline" else "answered",
                    state_revision=revision + 1,
                    response=body,
                    resolved_by={"kind": "user", "id": caller.subject_id},
                    delivery_state="pending",
                )
                tx.put(
                    "decisions",
                    decision_id,
                    decision,
                    expected_revision=revision,
                    schema="Decision",
                )
                tx.emit(
                    "decision.resolved",
                    decision,
                    actor=decision["resolved_by"],
                    work_id=decision["work_id"],
                )
                return receipt(body["operation_id"], decision_id, decision["state"], revision + 1)

            return tx.operation(
                "decision.answer",
                body["operation_id"],
                {**body, "decision_id": decision_id},
                effect,
            )

    def binding(
        self, tx: Transaction, session_id: str, kind: str, identity: str | None
    ) -> dict[str, Any] | None:
        return (
            tx.get("copilot.bindings", key(session_id, kind, identity), required=False)
            if identity
            else None
        )

    def bind(
        self, tx: Transaction, session_id: str, kind: str, identity: str, binding: dict[str, Any]
    ) -> None:
        identity = key(session_id, kind, identity)
        old = tx.get("copilot.bindings", identity, required=False)
        if old is not None and old != binding:
            raise DomainError("attribution_unavailable", 409)
        if old is None:
            tx.put("copilot.bindings", identity, binding)

    def reduce(
        self,
        project_id: str,
        session_id: str,
        event: dict[str, Any],
        *,
        original_fingerprint: str | None = None,
    ) -> None:
        """Only allowlisted public output is retained. Private reasoning/arguments are discarded."""
        with self.services.registry.project_store(project_id).transaction() as tx:
            identity, kind, data = event["id"], event["type"], event.get("data", {})
            event_key = key(session_id, identity)
            # Hash the original solely for conflicting duplicate detection, never retain it.
            fingerprint = original_fingerprint or hashlib.sha256(canonical(event)).hexdigest()
            old = tx.get("copilot.native", event_key, required=False)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise DomainError("idempotency_conflict", 409)
                return
            if not event.get("agentId") and (
                kind == "session.idle" or kind == "abort" and data.get("reason") == "user_initiated"
            ):
                controls = []
                for request in tx.all("requests"):
                    if (
                        request["submission"]["type"] != "stop"
                        or request["receipt"]["state"] != "stop_requested"
                    ):
                        continue
                    phase = tx.require("copilot.stops", request["request_id"])
                    work = tx.require("work", phase["work_id"])
                    if (
                        phase["phase"] in {"aborting", "reconciling"}
                        and work["native_session_id"] == session_id
                    ):
                        controls.append((request["request_id"], phase))
                if len(controls) == 1:
                    stop_id, phase = controls[0]
                    field = (
                        "idle_native_event_id"
                        if kind == "session.idle"
                        else "abort_native_event_id"
                    )
                    save(tx, "copilot.stops", stop_id, {**phase, field: identity})
                    if kind == "session.idle":
                        # Session-scoped Stop evidence, not an invented request/turn binding.
                        save(
                            tx,
                            "copilot.idle",
                            session_id,
                            {
                                "idle": True,
                                "session_id": session_id,
                                "stop_request_id": stop_id,
                                "native_event_id": identity,
                            },
                        )
            agent = event.get("agentId")
            binding = self.binding(tx, session_id, "agent", agent)
            if kind == "user.message" and data.get("clientTurnId"):
                continuation = tx.get("copilot.continuations", data["clientTurnId"], required=False)
                request = tx.require(
                    "requests", continuation["request_id"] if continuation else data["clientTurnId"]
                )
                binding = continuation or {
                    "request_id": request["request_id"],
                    "work_id": request["receipt"]["work_id"],
                    "delivery_id": request["request_id"],
                }
                automatic = bool(data.get("isAutopilotContinuation"))
                if (
                    not continuation
                    and not automatic
                    and data.get("content") != request["submission"].get("text")
                ):
                    raise DomainError("attribution_unavailable", 409)
                self.bind(tx, session_id, "message", data["messageId"], binding)
                if not continuation and not automatic:
                    if request["receipt"]["native_message_id"] not in (None, data["messageId"]):
                        self.fence(tx, "duplicate_native_correlation")
                        save(
                            tx,
                            "copilot.duplicates",
                            key(session_id, identity),
                            {
                                "request_id": request["request_id"],
                                "original_message_id": request["receipt"]["native_message_id"],
                                "additional_message_id": data["messageId"],
                            },
                        )
                        return
                    self.request_state(
                        tx,
                        request["request_id"],
                        request["receipt"]["state"],
                        native_message_id=data["messageId"],
                    )
                    save(
                        tx,
                        "copilot.admitted_messages",
                        request["request_id"],
                        {
                            "message_id": data["messageId"],
                            "work_id": request["receipt"]["work_id"],
                            "native_event_id": identity,
                        },
                    )
                    if request["submission"]["type"] == "steer":
                        self.invalidate_work_completions(
                            tx, tx.require("work", request["receipt"]["work_id"])
                        )
            binding = (
                binding
                or self.binding(tx, session_id, "message", data.get("originatingMessageId"))
                or self.binding(tx, session_id, "tool", data.get("parentToolCallId"))
                or self.binding(tx, session_id, "tool", data.get("toolCallId"))
                or (
                    self.binding(
                        tx, session_id, "tool", data.get("permissionRequest", {}).get("toolCallId")
                    )
                    if kind == "permission.requested"
                    else None
                )
                or (
                    self.binding(tx, session_id, "decision", data.get("requestId"))
                    if kind in {"permission.completed", "user_input.completed"}
                    else None
                )
                or self.binding(tx, session_id, "event", event.get("parentId"))
            )
            if binding:
                self.bind(tx, session_id, "event", identity, binding)
                if kind == "user.message" and data.get("messageId"):
                    self.bind(tx, session_id, "message", data["messageId"], binding)
                if kind == "assistant.message" and "turnId" in data:
                    self.bind(
                        tx,
                        session_id,
                        "turn",
                        f"{agent or 'root'}:{data.get('originatingMessageId')}:{data['turnId']}",
                        binding,
                    )
                    self.bind(tx, session_id, "message", data["messageId"], binding)
                if kind == "tool.execution_start":
                    self.bind(tx, session_id, "tool", data["toolCallId"], binding)
                if kind == "subagent.started":
                    if not agent:
                        raise DomainError("attribution_unavailable", 409)
                    self.bind(tx, session_id, "agent", agent, binding)
                if kind == "user_input.requested" and data.get("requestId"):
                    self.bind(
                        tx,
                        session_id,
                        "decision",
                        data["requestId"],
                        {
                            **binding,
                            "invocation_id": key(session_id, "tool", data["toolCallId"])
                            if data.get("toolCallId")
                            else None,
                        },
                    )
                if kind == "permission.requested":
                    permission = data.get("permissionRequest")
                    tool_call = (
                        permission.get("toolCallId") if isinstance(permission, dict) else None
                    )
                    if not isinstance(tool_call, str) or not tool_call:
                        self.fence(tx, "attribution_unavailable")
                        return
                    permission_binding = self.binding(tx, session_id, "tool", tool_call)
                    if permission_binding is None:
                        self.bind(tx, session_id, "tool", tool_call, binding)
                        permission_binding = binding
                    elif any(
                        permission_binding.get(k) != binding.get(k)
                        for k in ("work_id", "request_id", "delivery_id")
                    ):
                        self.fence(tx, "attribution_unavailable")
                        return
                    stage_id = key(session_id, data["requestId"])
                    attribution = {
                        **permission_binding,
                        "native_id": data["requestId"],
                        "session_id": session_id,
                        "tool_call_id": tool_call,
                        "fingerprint": hashlib.sha256(canonical(permission)).hexdigest(),
                        "invocation_id": key(session_id, "tool", tool_call),
                    }
                    stage = tx.get("copilot.permissions", stage_id, required=False)
                    if stage and any(stage.get(k) != v for k, v in attribution.items()):
                        raise DomainError("attribution_unavailable", 409)
                    if not stage:
                        tx.put(
                            "copilot.permissions",
                            stage_id,
                            {
                                **attribution,
                                "phase": "pending",
                                "authority": None,
                                "reply": None,
                                "delivery_state": "not_sent",
                            },
                        )
                    self.bind(
                        tx,
                        session_id,
                        "decision",
                        data["requestId"],
                        {
                            **permission_binding,
                            "invocation_id": key(session_id, "tool", tool_call),
                        },
                    )
                if kind in {
                    "user.message",
                    "assistant.turn_start",
                    "tool.execution_start",
                    "subagent.started",
                }:
                    save(tx, "copilot.idle", session_id, {**binding, "idle": False})
            missing_start = (
                kind == "tool.execution_complete"
                and not tx.get(
                    "invocations", key(session_id, "tool", data["toolCallId"]), required=False
                )
            ) or (
                kind in {"subagent.completed", "subagent.failed"}
                and not tx.get("invocations", key(session_id, "agent", str(agent)), required=False)
            )
            if missing_start or (
                not binding
                and kind.startswith(
                    ("tool.", "subagent.", "user_input.", "permission.", "assistant.message")
                )
            ):
                allowed = {
                    "messageId",
                    "originatingMessageId",
                    "clientTurnId",
                    "turnId",
                    "toolCallId",
                    "parentToolCallId",
                    "toolName",
                    "agentName",
                    "model",
                    "success",
                    "requestId",
                }
                if kind in {"assistant.message", "assistant.message_delta", "user.message"}:
                    allowed.update({"content", "deltaContent"})
                staged = {
                    k: v for k, v in event.items() if k in {"id", "type", "parentId", "agentId"}
                }
                staged["data"] = {k: v for k, v in data.items() if k in allowed}
                if kind == "assistant.message" and "toolRequests" in data:
                    # Retain only the turn classification, never tool arguments/prompts.
                    staged["data"]["toolRequests"] = bool(data["toolRequests"])
                if type(data.get("cancelled")) is bool:
                    staged["data"]["cancelled"] = data["cancelled"]
                if pending_display_name := safe_text(data.get("agentDisplayName"))[:200]:
                    staged["data"]["agentDisplayName"] = pending_display_name
                if (exit_code := shell_exit_code(data)) is not None:
                    staged["data"]["shellExecution"] = {"exitCode": exit_code}
                if kind in {"tool.execution_complete", "subagent.failed"}:
                    staged["data"]["error"] = error_fields(data)
                old_pending = tx.get("copilot.pending_events", event_key, required=False)
                if old_pending and old_pending["fingerprint"] != fingerprint:
                    raise DomainError("idempotency_conflict", 409)
                save(
                    tx,
                    "copilot.pending_events",
                    event_key,
                    {
                        "event": staged,
                        "fingerprint": fingerprint,
                        "session_id": session_id,
                        "resolved": False,
                    },
                )
                return
            pending = tx.get("copilot.pending_events", event_key, required=False)
            if pending:
                save(tx, "copilot.pending_events", event_key, {**pending, "resolved": True})
            save(
                tx,
                "copilot.native",
                event_key,
                {
                    "fingerprint": fingerprint,
                    "type": kind,
                    "attributed": bool(binding),
                    "session_id": session_id,
                },
            )
            if not binding:
                return
            work_id, request_id = binding["work_id"], binding["request_id"]
            actor = {"kind": "subagent" if agent else "lead", "id": agent or "copilot-lead"}
            emit = dict(
                actor=actor,
                request_id=request_id,
                work_id=work_id,
                native_session_id=session_id,
                native_event_id=identity,
            )
            if kind in {"permission.completed", "user_input.completed"} and data.get("requestId"):
                if kind == "permission.completed":
                    stage_id = key(session_id, data["requestId"])
                    stage = tx.get("copilot.permissions", stage_id, required=False)
                    result = data.get("result", {}).get("kind")
                    approved = result in {"approved", "approve-once"}
                    rejected = result in {"rejected", "reject", "denied-interactively-by-user"}
                    matches = bool(
                        stage
                        and stage.get("phase") in {"reply_prepared", "completed"}
                        and stage["work_id"] == work_id
                        and stage["request_id"] == request_id
                        and (
                            stage.get("reply") == "approve"
                            and approved
                            or stage.get("reply") == "reject"
                            and rejected
                        )
                    )
                    if stage:
                        stage = {
                            **stage,
                            "completions": [
                                *stage.get("completions", []),
                                {"native_event_id": identity, "result": result, "matches": matches},
                            ],
                        }
                    if not matches:
                        self.fence(tx, "outcome_uncertain")
                        if stage:
                            save(
                                tx,
                                "copilot.permissions",
                                stage_id,
                                {**stage, "phase": "outcome_uncertain", "native_result": result},
                            )
                        return
                    assert stage is not None
                    save(
                        tx,
                        "copilot.permissions",
                        stage_id,
                        {
                            **stage,
                            "phase": "completed",
                            "delivery_state": "acknowledged",
                            "native_result": result,
                            "completed_at": stage.get("completed_at") or now(),
                        },
                    )
                decision_id = "decision-" + key(session_id, data["requestId"])
                decision = tx.get("decisions", decision_id, required=False)
                if decision and decision["state"] in {"answered", "declined"}:
                    response = decision["response"]
                    matches = (
                        kind == "user_input.completed"
                        and response["action"] == "answer"
                        and data.get("answer") == response["answer"]
                    )
                    result = data.get("result", {}).get("kind")
                    matches = matches or (
                        kind == "permission.completed"
                        and (
                            response["action"] == "approve_once"
                            and result in {"approved", "approve-once"}
                            or response["action"] == "decline"
                            and result in {"rejected", "reject", "denied-interactively-by-user"}
                        )
                    )
                    if matches and decision["delivery_state"] != "acknowledged":
                        revision = tx.revision("decisions", decision_id)
                        decision.update(delivery_state="acknowledged", state_revision=revision + 1)
                        tx.put(
                            "decisions",
                            decision_id,
                            decision,
                            expected_revision=revision,
                            schema="Decision",
                        )
                        tx.emit(
                            "decision.resolved",
                            decision,
                            actor=SYSTEM,
                            work_id=work_id,
                            request_id=request_id,
                        )
                        work = tx.require("work", work_id)
                        if (
                            work["state"] == "awaiting_user"
                            and not self.control(tx)["fenced"]
                            and not any(
                                d["work_id"] == work_id and d["state"] == "pending"
                                for d in tx.all("decisions")
                            )
                        ):
                            self.work_state(tx, work_id, "running")
            if kind in {"assistant.message", "assistant.message_delta"}:
                text = (
                    data.get("content") if kind == "assistant.message" else data.get("deltaContent")
                )
                if (
                    isinstance(text, str)
                    and text != ""
                    and data.get("messageId")
                    and not (
                        kind == "assistant.message"
                        and data.get("toolRequests")
                        and not text.strip()
                    )
                ):
                    tx.emit(
                        "assistant.completed" if kind == "assistant.message" else "assistant.delta",
                        {"message_id": data["messageId"], "text": text, "native_agent_id": agent},
                        **emit,
                    )
            if kind == "assistant.usage":
                tx.emit(
                    "usage.recorded",
                    {
                        "model_id": data.get("model"),
                        "input_tokens": data.get("inputTokens"),
                        "output_tokens": data.get("outputTokens"),
                        "reported_cost": data.get("cost"),
                        "cost_unit": None,
                    },
                    **emit,
                )
            if kind in {
                "tool.execution_start",
                "tool.execution_complete",
                "subagent.started",
                "subagent.completed",
                "subagent.failed",
            }:
                child = kind.startswith("subagent")
                invocation_id = key(
                    session_id,
                    "agent" if child else "tool",
                    str(agent) if child else data["toolCallId"],
                )
                old_invocation = tx.get("invocations", invocation_id, required=False)
                starting = kind.endswith(("_start", ".started"))
                state = (
                    "running"
                    if starting
                    else "cancelled"
                    if data.get("cancelled") is True
                    else "failed"
                    if kind.endswith("failed") or data.get("success") is False
                    else "completed"
                )
                exit_code = shell_exit_code(data)
                if (
                    kind == "tool.execution_complete"
                    and exit_code is not None
                    and exit_code != 0
                    and state == "completed"
                ):
                    state = "failed"
                display_name = (
                    (
                        safe_text(data.get("agentDisplayName"))[:200]
                        or (old_invocation or {}).get("display_name")
                        or None
                    )
                    if child
                    else None
                )
                if not starting and old_invocation is None:
                    self.fence(tx, "attribution_unavailable")
                    return
                invocation = {
                    **binding,
                    "invocation_id": invocation_id,
                    "session_id": session_id,
                    "native_id": str(agent) if child else data["toolCallId"],
                    "agent_id": agent,
                    "owner_epoch": self.services.ownership.epoch,
                    "kind": "child" if child else "tool",
                    "state": state,
                    "name": data.get(
                        "agentName",
                        data.get("toolName", (old_invocation or {}).get("name", "tool")),
                    ),
                    "model_id": data.get("model", (old_invocation or {}).get("model_id")),
                    "updated_at": now(),
                    "display_name": display_name,
                    "arguments_sha256": hashlib.sha256(canonical(data["arguments"])).hexdigest()
                    if starting and isinstance(data.get("arguments"), dict)
                    else (old_invocation or {}).get("arguments_sha256"),
                }
                if old_invocation and old_invocation["state"] in TERMINAL and starting:
                    return
                summary = failure_summary(tx, invocation, data) if state == "failed" else ""
                if summary:
                    invocation["failure_summary"] = summary
                save(tx, "invocations", invocation_id, invocation)
                outcome = get_tool_outcome(tx, invocation_id)
                output_status = (
                    "published"
                    if outcome
                    and outcome["status"] == "succeeded"
                    and outcome["published_versions"]
                    else "publication_blocked"
                    if invocation["name"] == "publish_outputs" and state == "failed"
                    else "retained_unpublished"
                    if outcome and outcome["artifact_ids"] and not outcome["published_versions"]
                    else None
                )
                if (
                    state in {"failed", "outcome_uncertain"}
                    and (old_invocation or {}).get("state") != state
                ):
                    log_activity_problem(tx, invocation, data, summary)
                tx.emit(
                    ("subagent." if child else "tool.") + ("started" if starting else "completed"),
                    {
                        "invocation_id": invocation_id,
                        "name": invocation["name"],
                        "state": state,
                        "summary": summary,
                        "artifact_ids": outcome["artifact_ids"] if outcome else [],
                        "model_id": invocation["model_id"],
                        "published_versions": outcome["published_versions"] if outcome else [],
                        "output_status": output_status,
                        **({"exit_code": exit_code} if exit_code is not None else {}),
                        **({"display_name": display_name} if display_name else {}),
                    },
                    **emit,
                )
            if kind == "abort" and not agent:
                work = tx.require("work", work_id)
                last_delivery = tx.get("copilot.last_delivery", work_id, required=False)
                work_requests = {work["originating_request_id"], *work["directive_request_ids"]}
                current_delivery = bool(
                    last_delivery
                    and (
                        last_delivery.get("delivery_id") == binding.get("delivery_id")
                        or (
                            last_delivery.get("delivery_id") in work["directive_request_ids"]
                            and tx.get(
                                "copilot.admitted_messages",
                                last_delivery["delivery_id"],
                                required=False,
                            )
                            is not None
                            and binding.get("delivery_id") in work_requests
                        )
                    )
                )
                if (
                    work["native_session_id"] == session_id
                    and work["state"] in {"running", "awaiting_user"}
                    and current_delivery
                    and not self.control(tx)["fenced"]
                ):
                    # An unsolicited native abort interrupts work, but is not a confirmed Stop
                    # barrier or proof that external jobs/children have finished.
                    save(
                        tx,
                        "copilot.interruptions",
                        event_key,
                        {**binding, "native_event_id": identity, "reason": "native_turn_aborted"},
                    )
                    self.fence(tx, "native_turn_aborted")
                    self.work_state(tx, work_id, "awaiting_resume")
                    original = tx.require("requests", request_id)
                    self.request_state(
                        tx,
                        request_id,
                        original["receipt"]["state"],
                        detail_code="native_turn_aborted",
                    )
            if not agent and kind == "assistant.turn_start":
                save(
                    tx,
                    "copilot.completion",
                    request_id,
                    {**binding, "candidate": False, "public_terminal_message": False},
                )
            if not agent and kind == "assistant.message":
                old_completion = tx.get("copilot.completion", request_id, required=False) or {}
                if old_completion.get("producer") != "session.task_complete" or old_completion.get(
                    "delivery_id"
                ) != binding.get("delivery_id"):
                    public_terminal = (
                        isinstance(data.get("content"), str)
                        and bool(data["content"].strip())
                        and not data.get("toolRequests")
                    )
                    save(
                        tx,
                        "copilot.completion",
                        request_id,
                        {
                            **binding,
                            "candidate": False,
                            "public_terminal_message": public_terminal,
                            "message_id": data.get("messageId"),
                            "message_event_id": identity,
                        },
                    )
            if not agent and kind == "assistant.turn_end":
                completion = tx.get("copilot.completion", request_id, required=False) or {}
                matching_delivery = completion.get("delivery_id") == binding.get("delivery_id")
                explicit = (
                    matching_delivery and completion.get("producer") == "session.task_complete"
                )
                save(
                    tx,
                    "copilot.completion",
                    request_id,
                    {
                        **completion,
                        **binding,
                        "candidate": bool(
                            explicit
                            or matching_delivery
                            and completion.get("public_terminal_message")
                        ),
                        "producer": "session.task_complete" if explicit else "assistant.turn_end",
                        "producer_event_id": completion.get("producer_event_id")
                        if explicit
                        else identity,
                    },
                )
            if not agent and kind == "session.task_complete":
                save(
                    tx,
                    "copilot.completion",
                    request_id,
                    {**binding, "candidate": True, "producer": kind, "producer_event_id": identity},
                )
            if kind == "session.idle" and not agent:
                save(tx, "copilot.idle", session_id, {**binding, "idle": True})

    def drain_pending(self, project_id: str, session_id: str) -> None:
        store = self.services.registry.project_store(project_id)
        while True:
            with store.transaction(write=False) as tx:
                records = [
                    e
                    for e in tx.all("copilot.pending_events")
                    if not e["resolved"] and e["session_id"] == session_id
                ]
            if not records:
                return
            for record in records:
                self.reduce(
                    project_id,
                    session_id,
                    record["event"],
                    original_fingerprint=record["fingerprint"],
                )
            with store.transaction(write=False) as tx:
                remaining = sum(
                    not e["resolved"] and e["session_id"] == session_id
                    for e in tx.all("copilot.pending_events")
                )
            if remaining >= len(records):
                return

    def replay_event(self, project_id: str, session_id: str, event: dict[str, Any]) -> None:
        """Native load rewrites private system messages. Compare existing public projections,
        not their intentionally discarded private vendor payload. Live duplicates remain strict.
        """
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            old = tx.get("copilot.native", key(session_id, event["id"]), required=False)
            if old:
                if old["type"] != event["type"]:
                    raise DomainError("idempotency_conflict", 409)
                row = tx.connection.execute(
                    "SELECT body FROM events WHERE native_session_id=? AND native_event_id=?",
                    (session_id, event["id"]),
                ).fetchone()
                if row:
                    public = json.loads(row[0])
                    data = event.get("data", {})
                    if public["event_type"].startswith("assistant."):
                        payload = {
                            "message_id": data["messageId"],
                            "text": data.get("content", data.get("deltaContent", "")),
                            "native_agent_id": event.get("agentId"),
                        }
                        if payload != public["payload"]:
                            raise DomainError("idempotency_conflict", 409)
                    if public["event_type"].startswith(("tool.", "subagent.")):
                        child = public["event_type"].startswith("subagent.")
                        invocation_id = key(
                            session_id,
                            "agent" if child else "tool",
                            str(event.get("agentId")) if child else data["toolCallId"],
                        )
                        if invocation_id != public["payload"]["invocation_id"]:
                            raise DomainError("attribution_unavailable", 409)
                if (
                    event["type"] == "user.message"
                    and event.get("data", {}).get("clientTurnId")
                    and not event["data"].get("isAutopilotContinuation")
                ):
                    data = event["data"]
                    if not tx.get("copilot.continuations", data["clientTurnId"], required=False):
                        request = tx.require("requests", data["clientTurnId"])
                        if data["content"] != request["submission"]["text"]:
                            raise DomainError("idempotency_conflict", 409)
                return
        self.reduce(project_id, session_id, event)

    def interrupt_coordinators(
        self,
        tx: Transaction,
        session_id: str,
        history: list[dict[str, Any]],
        departed: dict[str, Any],
    ) -> None:
        """Settle lost *coordination*, never infer external-tool success or side effects."""
        if (
            departed.get("session_id") != session_id
            or departed.get("observed_identity") == departed["start"]
        ):
            return
        for invocation in tx.all("invocations"):
            if (
                invocation.get("session_id") != session_id
                or invocation.get("invocation_id") not in departed.get("invocation_ids", [])
                or invocation["state"] in TERMINAL
                or invocation.get("kind") != "tool"
                or invocation.get("name") not in {"task", "ask_user"}
            ):
                continue
            native_id = invocation["native_id"]
            binding = self.binding(tx, session_id, "tool", native_id)
            if not binding or any(binding[k] != invocation[k] for k in ("work_id", "request_id")):
                continue
            evidence_ids = []
            if invocation["name"] == "task":
                children = [
                    e
                    for e in history
                    if e["type"] == "subagent.started"
                    and e.get("data", {}).get("toolCallId") == native_id
                ]
                terminals = [
                    e
                    for e in history
                    if e["type"] in {"subagent.completed", "subagent.failed"}
                    and e.get("data", {}).get("toolCallId") == native_id
                ]
                if not children or any(
                    not any(t.get("agentId") == c.get("agentId") for t in terminals)
                    for c in children
                ):
                    continue
                if any(
                    not (
                        child := tx.get(
                            "invocations",
                            key(session_id, "agent", str(c.get("agentId"))),
                            required=False,
                        )
                    )
                    or child["state"] not in TERMINAL
                    or child["work_id"] != invocation["work_id"]
                    for c in children
                ):
                    continue
                evidence_ids = [e["id"] for e in children + terminals]
            else:
                decisions = [
                    d
                    for d in tx.all("decisions")
                    if d["invocation_id"] == invocation["invocation_id"]
                ]
                if (
                    len(decisions) != 1
                    or decisions[0]["state"] != "pending"
                    or decisions[0]["response"] is not None
                ):
                    continue
                decision = decisions[0]
                events = [
                    e
                    for e in history
                    if e["type"] == "user_input.requested"
                    and e.get("data", {}).get("toolCallId") == native_id
                    and e["data"].get("requestId") == decision["native_decision_id"]
                ]
                decision_binding = self.binding(
                    tx, session_id, "decision", decision["native_decision_id"]
                )
                if (
                    not decision_binding
                    or decision_binding.get("invocation_id") != invocation["invocation_id"]
                    or any(
                        decision_binding.get(k) != invocation[k] for k in ("work_id", "request_id")
                    )
                ):
                    continue
                evidence_ids = [e["id"] for e in events]
                revision = tx.revision("decisions", decision["decision_id"])
                decision.update(state="cancelled", state_revision=revision + 1)
                tx.put(
                    "decisions",
                    decision["decision_id"],
                    decision,
                    expected_revision=revision,
                    schema="Decision",
                )
                tx.emit(
                    "decision.resolved",
                    decision,
                    actor=SYSTEM,
                    work_id=invocation["work_id"],
                    request_id=invocation["request_id"],
                )
            evidence = {
                "kind": "coordinator_interrupted",
                "session_id": session_id,
                "departed_process": departed,
                "native_event_ids": evidence_ids,
                "native_return_observed": False,
                "at": now(),
                "decision_binding_identity": key(
                    session_id, "decision", decision["native_decision_id"]
                )
                if invocation["name"] == "ask_user"
                else None,
            }
            save(tx, "copilot.recovery_evidence", invocation["invocation_id"], evidence)
            save(
                tx,
                "invocations",
                invocation["invocation_id"],
                {**invocation, "state": "cancelled", "recovery": evidence},
            )
            tx.emit(
                "tool.completed",
                {
                    "invocation_id": invocation["invocation_id"],
                    "name": invocation["name"],
                    "state": "cancelled",
                    "summary": "Native coordination interrupted after exact owner process exit; no successful return observed.",
                    "artifact_ids": [],
                    "model_id": invocation.get("model_id"),
                },
                actor=SYSTEM,
                work_id=invocation["work_id"],
                request_id=invocation["request_id"],
            )

    def native_denied_stage(self, tx: Transaction, stage: dict[str, Any]) -> bool:
        observation = tx.get(
            "copilot.permission_outcomes",
            key(stage["session_id"], stage["native_id"]),
            required=False,
        )
        return bool(
            observation
            and observation.get("kind") == "native_authorization_denied"
            and all(
                observation.get(k) == stage.get(k)
                for k in (
                    "session_id",
                    "native_id",
                    "tool_call_id",
                    "work_id",
                    "request_id",
                    "invocation_id",
                )
            )
        )

    def interrupted_custom_stage(self, tx: Transaction, stage: dict[str, Any]) -> bool:
        observation = tx.get(
            "copilot.permission_outcomes",
            key(stage["session_id"], stage["native_id"]),
            required=False,
        )
        return bool(
            observation
            and observation.get("kind") == "uninvoked_custom_tool_interrupted"
            and all(
                observation.get(k) == stage.get(k)
                for k in (
                    "session_id",
                    "native_id",
                    "tool_call_id",
                    "work_id",
                    "request_id",
                    "invocation_id",
                )
            )
        )

    def interrupt_uninvoked_checkpoint(
        self,
        tx: Transaction,
        session_id: str,
        history: list[dict[str, Any]],
        departed: dict[str, Any],
    ) -> int:
        """Cancel a lost unanswered checkpoint permission wait, never claim a checkpoint effect."""
        if (
            departed.get("session_id") != session_id
            or departed.get("observed_identity") == departed.get("start")
            or departed.get("pending_empty") is not True
            or departed.get("current_send") is not False
            or not departed.get("checkpoint_head", {}).get("verified")
        ):
            return 0
        count = 0
        for invocation in tx.all("invocations"):
            if (
                invocation.get("session_id") != session_id
                or invocation["state"] in TERMINAL
                or invocation.get("invocation_id") not in departed.get("invocation_ids", [])
                or invocation.get("kind") != "tool"
                or invocation.get("name") != "checkpoint_sources"
            ):
                continue
            tool = invocation["native_id"]
            binding = self.binding(tx, session_id, "tool", tool)
            work = tx.get("work", invocation["work_id"], required=False)
            if (
                not binding
                or not work
                or work["native_session_id"] != session_id
                or work["originating_request_id"] != invocation["request_id"]
                or any(
                    binding.get(k) != invocation.get(k)
                    for k in ("work_id", "request_id", "delivery_id")
                )
            ):
                continue
            requests = [
                e
                for e in history
                if e["type"] == "permission.requested"
                and e.get("data", {}).get("permissionRequest", {}).get("toolCallId") == tool
            ]
            starts = [
                e
                for e in history
                if e["type"] == "tool.execution_start"
                and e.get("data", {}).get("toolCallId") == tool
            ]
            if len(requests) != 1 or len(starts) != 1:
                continue
            event = requests[0]
            request = event["data"]["permissionRequest"]
            body = request.get("args")
            if (
                request.get("kind") != "custom-tool"
                or request.get("toolName") != "checkpoint_sources"
            ):
                continue
            try:
                validate("CheckpointRequest", body)
            except DomainError:
                continue
            owner_sessions = {session_id}
            if (
                invocation.get("agent_id")
                and starts[0].get("agentId") == invocation.get("agent_id")
                and event.get("agentId") == invocation.get("agent_id")
            ):
                child_sid = starts[0]["data"].get("ownerNativeSessionId")
                if isinstance(child_sid, str) and child_sid:
                    owner_sessions.add(child_sid)
            if (
                body["project_id"] != tx.project_id
                or body["expected_head"] != departed["checkpoint_head"].get("head")
                or event["data"].get("ownerSessionId") not in owner_sessions
                or starts[0]["data"].get("toolName") != "checkpoint_sources"
                or canonical(starts[0]["data"].get("arguments")) != canonical(body)
            ):
                continue
            native_id = event["data"]["requestId"]
            completions = [
                e
                for e in history
                if e["type"] == "permission.completed"
                and e.get("data", {}).get("requestId") == native_id
            ]
            if (
                len(completions) != 1
                or completions[0]["data"].get("toolCallId") != tool
                or completions[0]["data"].get("result", {}).get("kind")
                != "denied-interactively-by-user"
                or any(
                    e["type"]
                    not in {"tool.execution_start", "permission.requested", "permission.completed"}
                    and e.get("data", {}).get("toolCallId") == tool
                    for e in history
                )
            ):
                continue
            stage = tx.get("copilot.permissions", key(session_id, native_id), required=False)
            decisions = [
                d for d in tx.all("decisions") if d["invocation_id"] == invocation["invocation_id"]
            ]
            if (
                not stage
                or len(decisions) != 1
                or stage.get("reply") not in {None, "reject"}
                or stage.get("session_id") != session_id
                or stage.get("tool_call_id") != tool
                or stage.get("native_id") != native_id
                or any(
                    s.get("native_id") != native_id
                    for s in tx.all("copilot.permissions")
                    if s.get("session_id") == session_id and s.get("tool_call_id") == tool
                )
                or stage.get("authority") not in {None, "host"}
                or stage.get("fingerprint")
                != departed.get("permission_fingerprints", {}).get(event["id"])
                or any(
                    stage.get(k) != invocation.get(k)
                    for k in ("work_id", "request_id", "invocation_id", "delivery_id")
                )
            ):
                continue
            decision = decisions[0]
            decision_binding = self.binding(tx, session_id, "decision", native_id)
            if (
                not decision_binding
                or decision_binding.get("invocation_id") != invocation["invocation_id"]
                or any(
                    decision_binding.get(k) != invocation.get(k)
                    for k in ("work_id", "request_id", "delivery_id")
                )
                or decision["native_decision_id"] != native_id
                or decision["kind"] != "permission"
                or decision["work_id"] != invocation["work_id"]
                or decision["state"] != "pending"
                or decision["response"] is not None
                or decision["resolved_by"] is not None
                or decision["delivery_state"] != "not_sent"
                or tx.get("checkpoint_intents", body["operation_id"], required=False)
                or tx.connection.execute(
                    "SELECT 1 FROM operations WHERE scope=? AND id=?",
                    ("sources.checkpoint", body["operation_id"]),
                ).fetchone()
            ):
                continue
            evidence = {
                **{
                    k: invocation[k]
                    for k in ("work_id", "request_id", "invocation_id", "delivery_id")
                },
                "session_id": session_id,
                "native_id": native_id,
                "tool_call_id": tool,
                "kind": "uninvoked_custom_tool_interrupted",
                "operation_id": body["operation_id"],
                "source_event_ids": [starts[0]["id"], event["id"], completions[0]["id"]],
                "departed_process": departed,
                "native_return_observed": False,
                "checkpoint_effect_observed": False,
                "native_exit_code": None,
                "at": now(),
            }
            revision = tx.revision("decisions", decision["decision_id"])
            decision.update(state="cancelled", state_revision=revision + 1)
            tx.put(
                "decisions",
                decision["decision_id"],
                decision,
                expected_revision=revision,
                schema="Decision",
            )
            tx.emit(
                "decision.resolved",
                decision,
                actor=SYSTEM,
                work_id=invocation["work_id"],
                request_id=invocation["request_id"],
            )
            tx.put("copilot.permission_outcomes", key(session_id, native_id), evidence)
            save(
                tx,
                "invocations",
                invocation["invocation_id"],
                {**invocation, "state": "cancelled", "recovery": evidence},
            )
            tx.emit(
                "tool.completed",
                {
                    "invocation_id": invocation["invocation_id"],
                    "name": invocation["name"],
                    "state": "cancelled",
                    "summary": "Departed checkpoint permission wait interrupted. No checkpoint intent, successful tool return or owner answer observed.",
                    "artifact_ids": [],
                    "model_id": invocation.get("model_id"),
                },
                actor=SYSTEM,
                work_id=invocation["work_id"],
                request_id=invocation["request_id"],
            )
            count += 1
        return count

    def interrupt_departed_invocations(
        self,
        tx: Transaction,
        session_id: str,
        prior_invocation_ids: list[str],
        departed: dict[str, Any],
    ) -> int:
        """Close prior invocations without inferring their native or workspace outcomes."""
        if departed.get("session_id") != session_id or departed.get(
            "observed_identity"
        ) == departed.get("start"):
            return 0
        prior = set(prior_invocation_ids)
        pending = [d for d in tx.all("decisions") if d["state"] == "pending"]
        count = 0
        for invocation in tx.all("invocations"):
            invocation_id = invocation["invocation_id"]
            if (
                invocation.get("session_id") != session_id
                or invocation_id not in prior
                or invocation["state"] in TERMINAL
            ):
                continue
            correlation = {
                "work_id": invocation["work_id"],
                "request_id": invocation["request_id"],
            }
            for decision in pending:
                if decision.get("invocation_id") != invocation_id:
                    continue
                decision_id = decision["decision_id"]
                revision = tx.revision("decisions", decision_id)
                decision.update(state="cancelled", state_revision=revision + 1)
                tx.put(
                    "decisions",
                    decision_id,
                    decision,
                    expected_revision=revision,
                    schema="Decision",
                )
                tx.emit("decision.resolved", decision, actor=SYSTEM, **correlation)
            evidence = {
                "kind": "departed_invocation_interrupted",
                "session_id": session_id,
                "departed_process": departed,
                "native_return_observed": False,
                "outcome_before_exit_unknown": True,
                "at": now(),
            }
            save(tx, "copilot.recovery_evidence", invocation_id, evidence)
            save(
                tx,
                "invocations",
                invocation_id,
                {**invocation, "state": "cancelled", "recovery": evidence},
            )
            prefix = "subagent" if invocation.get("kind") == "child" else "tool"
            tx.emit(
                f"{prefix}.completed",
                {
                    "invocation_id": invocation_id,
                    "name": invocation["name"],
                    "state": "cancelled",
                    "summary": "Native invocation interrupted when previous Copilot runtime exited "
                    "before recording completion; inspect workspace state before repeating mutating steps.",
                    "artifact_ids": [],
                    "model_id": invocation.get("model_id"),
                    **(
                        {"display_name": invocation["display_name"]}
                        if invocation.get("display_name")
                        else {}
                    ),
                },
                actor=SYSTEM,
                **correlation,
            )
            count += 1
        return count

    def interrupt_departed_custom_tools(
        self,
        tx: Transaction,
        session_id: str,
        history: list[dict[str, Any]],
        departed: dict[str, Any],
    ) -> int:
        """Settle departed read-only or uninvoked host custom tools; never infer mutating effects."""
        if (
            departed.get("session_id") != session_id
            or departed.get("observed_identity") == departed.get("start")
            or departed.get("pending_empty") is not True
            or departed.get("current_send") is not False
        ):
            return 0
        from .tools import DISCOVERY_READS, READ_TOOLS, TOOLS
        from .workflows import WORKFLOW_TOOLS

        read_only_tools = frozenset(
            {*READ_TOOLS, *DISCOVERY_READS, "validate_authored_document", "get_unity_result"}
        )
        mutating_tools = frozenset({*TOOLS, *WORKFLOW_TOOLS}) - read_only_tools
        count = 0
        for invocation in tx.all("invocations"):
            tool_name = invocation.get("name")
            if (
                invocation.get("session_id") != session_id
                or invocation["state"] in TERMINAL
                or invocation.get("invocation_id") not in departed.get("invocation_ids", [])
                or invocation.get("kind") != "tool"
                or tool_name not in read_only_tools | mutating_tools
            ):
                continue
            tool = invocation["native_id"]
            binding = self.binding(tx, session_id, "tool", tool)
            work = tx.get("work", invocation["work_id"], required=False)
            if (
                not binding
                or not work
                or work["native_session_id"] != session_id
                or work["originating_request_id"] != invocation["request_id"]
                or work["state"]
                not in {"stop_requested", "awaiting_user", "awaiting_resume", "outcome_uncertain"}
                or any(
                    binding.get(k) != invocation.get(k)
                    for k in ("work_id", "request_id", "delivery_id")
                )
            ):
                continue
            starts = [
                e
                for e in history
                if e["type"] == "tool.execution_start"
                and e.get("data", {}).get("toolCallId") == tool
            ]
            if len(starts) != 1 or starts[0]["data"].get("toolName") != tool_name:
                continue
            args = starts[0]["data"].get("arguments")
            if (
                not isinstance(args, dict)
                or invocation.get("arguments_sha256") != hashlib.sha256(canonical(args)).hexdigest()
            ):
                continue
            if any(
                e["type"] == "tool.execution_complete"
                and e.get("data", {}).get("toolCallId") == tool
                for e in history
            ):
                continue
            perm_requests = [
                e
                for e in history
                if e["type"] == "permission.requested"
                and e.get("data", {}).get("permissionRequest", {}).get("toolCallId") == tool
            ]
            if any(
                e["data"]["permissionRequest"].get("kind") != "custom-tool"
                or e["data"]["permissionRequest"].get("toolName") != tool_name
                for e in perm_requests
            ):
                continue
            perm_ids = {
                e["data"]["requestId"]
                for e in perm_requests
                if isinstance(e.get("data", {}).get("requestId"), str)
            }
            stages = [
                s
                for s in tx.all("copilot.permissions")
                if s.get("session_id") == session_id and s.get("tool_call_id") == tool
            ]
            if any(
                any(
                    s.get(k) != invocation.get(k)
                    for k in ("work_id", "request_id", "invocation_id", "delivery_id")
                )
                for s in stages
            ):
                continue
            outcome = get_tool_outcome(tx, invocation["invocation_id"])
            is_read_only = tool_name in read_only_tools
            if is_read_only:
                if outcome is not None and (
                    outcome.get("status") != "failed"
                    or outcome.get("artifact_ids")
                    or outcome.get("published_versions")
                    or outcome.get("job_ids")
                    or outcome.get("publication_id") is not None
                ):
                    continue
            else:
                if outcome is not None:
                    continue
                if any(
                    (
                        e["type"] == "external_tool.requested"
                        and e.get("data", {}).get("toolCallId") == tool
                    )
                    or (
                        e["type"].startswith("tool.execution_")
                        and e["type"] != "tool.execution_start"
                        and e.get("data", {}).get("toolCallId") == tool
                    )
                    or (
                        e["type"] == "permission.completed"
                        and (
                            e.get("data", {}).get("toolCallId") == tool
                            or e.get("data", {}).get("requestId") in perm_ids
                        )
                        and e.get("data", {}).get("result", {}).get("kind")
                        in {"approved", "approve-once"}
                    )
                    for e in history
                ):
                    continue
                if any(
                    s.get("reply") == "approve"
                    or s.get("native_result") in {"approved", "approve-once"}
                    or any(
                        c.get("result") in {"approved", "approve-once"}
                        for c in s.get("completions", [])
                    )
                    for s in stages
                ):
                    continue
                op_id = args.get("operation_id")
                if isinstance(op_id, str) and op_id:
                    if tx.get("checkpoint_intents", op_id, required=False) or (
                        tx.connection.execute(
                            "SELECT 1 FROM operations WHERE id=?", (op_id,)
                        ).fetchone()
                    ):
                        continue
                job_id = args.get("job_id")
                if (
                    isinstance(job_id, str)
                    and job_id
                    and tool_name
                    in {
                        "admit_job",
                        "submit_generation",
                        "request_preview",
                        "assemble_sequence",
                        "run_unity_workflow",
                    }
                    and tx.get("jobs", job_id, required=False)
                ):
                    continue
            decisions = [
                d for d in tx.all("decisions") if d["invocation_id"] == invocation["invocation_id"]
            ]
            if any(
                d["state"] == "pending"
                and (
                    d["work_id"] != invocation["work_id"]
                    or d["response"] is not None
                    or d["resolved_by"] is not None
                    or d["delivery_state"] != "not_sent"
                )
                for d in decisions
            ):
                continue
            for decision in decisions:
                if decision["state"] == "pending":
                    revision = tx.revision("decisions", decision["decision_id"])
                    decision.update(state="cancelled", state_revision=revision + 1)
                    tx.put(
                        "decisions",
                        decision["decision_id"],
                        decision,
                        expected_revision=revision,
                        schema="Decision",
                    )
                    tx.emit(
                        "decision.resolved",
                        decision,
                        actor=SYSTEM,
                        work_id=invocation["work_id"],
                        request_id=invocation["request_id"],
                    )
            source_event_ids = [starts[0]["id"], *(e["id"] for e in perm_requests)]
            base_fields = {
                k: invocation[k] for k in ("work_id", "request_id", "invocation_id", "delivery_id")
            }
            for stage in stages:
                stage_native_id = stage["native_id"]
                save(
                    tx,
                    "copilot.permission_outcomes",
                    key(session_id, stage_native_id),
                    {
                        **base_fields,
                        "session_id": session_id,
                        "native_id": stage_native_id,
                        "tool_call_id": tool,
                        "kind": "uninvoked_custom_tool_interrupted",
                        "tool_name": tool_name,
                        "read_only": is_read_only,
                        "source_event_ids": source_event_ids,
                        "departed_process": departed,
                        "native_return_observed": False,
                        "at": now(),
                    },
                )
            evidence = {
                **base_fields,
                "session_id": session_id,
                "tool_call_id": tool,
                "tool_name": tool_name,
                "kind": (
                    "departed_read_only_custom_tool_interrupted"
                    if is_read_only
                    else "uninvoked_custom_tool_interrupted"
                ),
                "read_only": is_read_only,
                "source_event_ids": source_event_ids,
                "departed_process": departed,
                "native_return_observed": False,
                "at": now(),
            }
            save(tx, "copilot.recovery_evidence", invocation["invocation_id"], evidence)
            save(
                tx,
                "invocations",
                invocation["invocation_id"],
                {**invocation, "state": "cancelled", "recovery": evidence},
            )
            tx.emit(
                "tool.completed",
                {
                    "invocation_id": invocation["invocation_id"],
                    "name": invocation["name"],
                    "state": "cancelled",
                    "summary": (
                        "Departed read-only host tool call interrupted after owner process exit; no side effects exist."
                        if is_read_only
                        else "Departed uninvoked host tool call interrupted before execution; no host effect observed."
                    ),
                    "artifact_ids": [],
                    "model_id": invocation.get("model_id"),
                },
                actor=SYSTEM,
                work_id=invocation["work_id"],
                request_id=invocation["request_id"],
            )
            count += 1
        return count

    def observe_native_denials(
        self,
        tx: Transaction,
        session_id: str,
        history: list[dict[str, Any]],
        departed: dict[str, Any],
    ) -> int:
        """Observe a terminal CLI 1.0.112 commands-gate denial, never invent a ToolResult/ACK."""
        if (
            departed.get("session_id") != session_id
            or departed.get("observed_identity") == departed.get("start")
            or departed.get("pending_empty") is not True
            or departed.get("current_send") is not False
        ):
            return 0
        events: dict[str, dict[str, Any]] = {}
        for event in history:
            if event["id"] in events and events[event["id"]] != event:
                return 0
            events[event["id"]] = event
        ordered = list(events.values())
        count = 0
        for invocation in tx.all("invocations"):
            if (
                invocation.get("session_id") != session_id
                or invocation["state"] in TERMINAL
                or invocation.get("invocation_id") not in departed.get("invocation_ids", [])
                or invocation.get("kind") != "tool"
                or invocation.get("name") != "bash"
            ):
                continue
            tool = invocation["native_id"]
            binding = self.binding(tx, session_id, "tool", tool)
            work = tx.get("work", invocation["work_id"], required=False)
            if (
                not binding
                or not work
                or work["native_session_id"] != session_id
                or work["originating_request_id"] != invocation["request_id"]
                or any(
                    binding.get(k) != invocation.get(k)
                    for k in ("work_id", "request_id", "delivery_id")
                )
            ):
                continue
            requests = [
                (n, e)
                for n, e in enumerate(ordered)
                if e["type"] == "permission.requested"
                and e.get("data", {}).get("permissionRequest", {}).get("toolCallId") == tool
            ]
            starts = [
                (n, e)
                for n, e in enumerate(ordered)
                if e["type"] == "tool.execution_start"
                and e.get("data", {}).get("toolCallId") == tool
            ]
            if not requests or len(starts) != 1 or starts[0][0] >= requests[0][0]:
                continue
            # Any terminal/partial execution observation contradicts this narrow pre-execution fact.
            if any(
                e["type"]
                not in {"tool.execution_start", "permission.requested", "permission.completed"}
                and e.get("data", {}).get("toolCallId") == tool
                for e in ordered
            ):
                continue
            denied_index, denied = requests[-1]
            data = denied["data"]
            permission, prompt = data.get("permissionRequest", {}), data.get("promptRequest", {})
            command = permission.get("fullCommandText")
            segments = permission.get("commandSegments") or []
            owner_sessions = {session_id}
            if (
                invocation.get("agent_id")
                and starts[0][1].get("agentId") == invocation.get("agent_id")
                and denied.get("agentId") == invocation.get("agent_id")
            ):
                child_sid = starts[0][1]["data"].get("ownerNativeSessionId")
                if isinstance(child_sid, str) and child_sid:
                    owner_sessions.add(child_sid)
            if (
                permission.get("kind") != "shell"
                or permission.get("shellType") != "bash"
                or permission.get("hasParseErrors") is not False
                or len(segments) != 1
                or segments[0].get("fullCommandText") != command
                or prompt.get("kind") != "commands"
                or prompt.get("toolCallId") != tool
                or data.get("ownerSessionId") not in owner_sessions
                or not command
                or prompt.get("fullCommandText") != command
                or starts[0][1]["data"].get("toolName") != "bash"
                or starts[0][1]["data"].get("arguments", {}).get("command") != command
            ):
                continue
            completions = []
            valid = True
            ids = [e["data"]["requestId"] for _, e in requests]
            if len(set(ids)) != len(ids):
                continue
            for position, (n, requested) in enumerate(requests):
                matches = [
                    (m, e)
                    for m, e in enumerate(ordered)
                    if e["type"] == "permission.completed"
                    and e.get("data", {}).get("requestId") == requested["data"]["requestId"]
                ]
                if (
                    len(matches) != 1
                    or matches[0][0] <= n
                    or matches[0][1]["data"].get("toolCallId") != tool
                    or (position + 1 < len(requests) and matches[0][0] >= requests[position + 1][0])
                ):
                    valid = False
                    break
                completed = matches[0][1]
                if position + 1 < len(requests):
                    prior = requested["data"].get("promptRequest", {})
                    if (
                        prior.get("kind") != "path"
                        or prior.get("accessKind") != "shell"
                        or prior.get("toolCallId") != tool
                        or requested["data"].get("ownerSessionId") not in owner_sessions
                        or completed["data"].get("result", {}).get("kind") != "approved"
                    ):
                        valid = False
                        break
                completions.append(completed)
            if (
                not valid
                or completions[-1]["data"].get("result", {}).get("kind")
                != "denied-interactively-by-user"
            ):
                continue
            if any(
                e["type"] == "permission.completed"
                and e.get("data", {}).get("toolCallId") == tool
                and e["data"].get("requestId") not in ids
                for e in ordered
            ):
                continue
            native_id = data["requestId"]
            stage = tx.get("copilot.permissions", key(session_id, native_id), required=False)
            related = [
                s
                for s in tx.all("copilot.permissions")
                if s.get("session_id") == session_id and s.get("tool_call_id") == tool
            ]
            decisions = [
                d for d in tx.all("decisions") if d["invocation_id"] == invocation["invocation_id"]
            ]
            if (
                not stage
                or len(decisions) > 1
                or stage.get("session_id") != session_id
                or stage.get("tool_call_id") != tool
                or stage.get("native_id") != native_id
                or any(s["native_id"] not in ids for s in related)
                or stage.get("reply") not in {None, "reject"}
                or stage.get("authority") not in {None, "host"}
                or stage.get("delivery_state") not in {"not_sent", "outcome_uncertain"}
                or any(
                    stage.get(k) != invocation.get(k)
                    for k in ("work_id", "request_id", "invocation_id", "delivery_id")
                )
                or stage.get("fingerprint")
                != departed.get("permission_fingerprints", {}).get(denied["id"])
            ):
                continue
            if decisions:
                decision = decisions[0]
                if (
                    decision["native_decision_id"] != native_id
                    or decision["kind"] != "permission"
                    or decision["work_id"] != invocation["work_id"]
                    or decision["state"] not in {"pending", "cancelled"}
                    or decision["response"] is not None
                    or decision["resolved_by"] is not None
                    or decision["delivery_state"] != "not_sent"
                ):
                    continue
                if decision["state"] == "pending":
                    revision = tx.revision("decisions", decision["decision_id"])
                    decision.update(state="cancelled", state_revision=revision + 1)
                    tx.put(
                        "decisions",
                        decision["decision_id"],
                        decision,
                        expected_revision=revision,
                        schema="Decision",
                    )
                    tx.emit(
                        "decision.resolved",
                        decision,
                        actor=SYSTEM,
                        work_id=invocation["work_id"],
                        request_id=invocation["request_id"],
                    )
            observation = {
                **{
                    k: invocation[k]
                    for k in ("work_id", "request_id", "invocation_id", "delivery_id")
                },
                "session_id": session_id,
                "native_id": native_id,
                "tool_call_id": tool,
                "kind": "native_authorization_denied",
                "runtime": "1.0.112",
                "source_event_ids": [starts[0][1]["id"], denied["id"], completions[-1]["id"]],
                "departed_process": departed,
                "native_return_observed": False,
                "native_exit_code": None,
                "authority": None,
                "at": now(),
            }
            tx.put("copilot.permission_outcomes", key(session_id, native_id), observation)
            recovered_invocation = {**invocation, "state": "failed", "recovery": observation}
            summary = "Native commands authorisation denied before execution; callback delivery and decision authority remain unproven."
            save(
                tx,
                "invocations",
                invocation["invocation_id"],
                recovered_invocation,
            )
            log_activity_problem(
                tx,
                recovered_invocation,
                {"error": {"code": "native_authorization_denied"}},
                summary,
            )
            tx.emit(
                "tool.completed",
                {
                    "invocation_id": invocation["invocation_id"],
                    "name": invocation["name"],
                    "state": "failed",
                    "summary": summary,
                    "artifact_ids": [],
                    "model_id": invocation.get("model_id"),
                },
                actor=SYSTEM,
                work_id=invocation["work_id"],
                request_id=invocation["request_id"],
            )
            count += 1
        return count
