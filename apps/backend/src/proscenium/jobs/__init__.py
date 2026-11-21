import json
from collections.abc import Callable
from importlib.resources import files
from typing import Any
from uuid import uuid4

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.contracts import validate
from proscenium.db import Transaction
from proscenium.db.outbox import dispatch_one
from proscenium.db.registry import Registry
from proscenium.jobs.gate import ExecutionGate
from proscenium.jobs.resources import check_storage

TRANSITIONS = json.loads(files("proscenium.contracts").joinpath("transitions.json").read_text())
TERMINAL = {"succeeded", "failed", "cancelled"}
EARLIER_RENDER_RETRY_DENIED = "This job belongs to an earlier request. No render was started."


def retry_context(
    tx: Transaction,
    caller: Caller,
    work: dict[str, Any],
    *,
    owner_epoch: str,
    require_invocation: bool,
) -> tuple[str, dict[str, Any] | None]:
    """Use the invocation's captured Queue or Steer context, never the latest workspace view."""
    if caller.invocation_id is None and not require_invocation:
        request_id = work["originating_request_id"]
    else:
        invocation = (
            tx.get("invocations", str(caller.invocation_id), required=False)
            if caller.invocation_id is not None
            else None
        )
        if (
            not invocation
            or invocation.get("work_id") != work["work_id"]
            or invocation.get("owner_epoch") != owner_epoch
            or invocation.get("state") not in {"admitted", "running"}
        ):
            raise DomainError("scope_denied", 403, EARLIER_RENDER_RETRY_DENIED)
        request_id = invocation.get("context_request_id") or invocation["request_id"]
    request = tx.get("requests", request_id, required=False)
    context_id = request["submission"].get("context_id") if request else None
    return request_id, tx.require("contexts", context_id) if context_id else None


def transition(
    family: str, old: str, new: str, *, evidence: str | None = None, explicit_resume: bool = False
) -> None:
    if old == new:
        return
    if new not in TRANSITIONS[family].get(old, []):
        raise DomainError("invalid_state_transition")
    if old == "outcome_uncertain" and not evidence:
        raise DomainError("invalid_state_transition", message="Reconciliation requires evidence")
    if (
        family == "work"
        and [old, new] in TRANSITIONS["resume_only_work_edges"]
        and not explicit_resume
    ):
        raise DomainError("invalid_state_transition", message="Explicit Resume required")


class JobService:
    def __init__(
        self, registry: Registry, gate: ExecutionGate, settings: Settings, owner_epoch: str
    ):
        self.registry, self.gate, self.settings, self.owner_epoch = (
            registry,
            gate,
            settings,
            owner_epoch,
        )
        # Cancellation adapters register by job kind. Callback must observe only this admitted job.
        # It runs AFTER committing cancel_requested. It must not claim cancellation without evidence.
        self.cancellers: dict[str, Callable[[dict[str, Any]], None]] = {}
        # Host-only per-adapter probes. Binding the callback identity means a later
        # legacy registration never accidentally inherits another adapter's policy.
        self.cancellation_eligibility: dict[
            str,
            tuple[Callable[[dict[str, Any]], None], Callable[[Transaction, dict[str, Any]], bool]],
        ] = {}
        # Host-only probes return the authorising request for cross-work recovery.
        self.retry_eligibility: dict[
            str, Callable[[Transaction, Caller, dict[str, Any]], str | None]
        ] = {}

    def check_caller(
        self, caller: Caller, operation: str, project_id: str, *, admission: bool = False
    ) -> None:
        caller.permits(operation, project_id)
        if project_id != self.registry.active_id():
            raise DomainError("scope_denied", 403)
        if caller.channel == "tool":
            if caller.owner_epoch != self.owner_epoch:
                raise DomainError("owner_conflict", 409)
            if admission:
                self.gate.require(project_id)

    def admit(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        validate("JobAdmission", body)
        project_id = body["project_id"]
        self.check_caller(caller, "admitToolJob", project_id)
        if caller.channel != "tool":
            raise DomainError("scope_denied", 403)
        store = self.registry.project_store(project_id)
        with store.transaction() as tx:

            def effect() -> dict[str, Any]:
                self.gate.require(project_id)
                if body["execution_profile_id"] not in self.settings.execution_profiles:
                    raise DomainError(
                        "capability_unavailable", 501, "Execution profile is not configured"
                    )
                check_storage(store.path.parent, self.settings.free_space_reserve_bytes)
                work = tx.require("work", str(caller.work_id))
                if work["state"] not in {"running", "awaiting_user"}:
                    raise DomainError("scope_denied", 403)
                for artifact_id in body["input_artifact_ids"]:
                    if tx.require("artifacts", artifact_id)["state"] != "ready":
                        raise DomainError("media_unavailable", 422)
                version = body["target"]["version"]
                if version:
                    record = tx.require("versions", version["version_id"])
                    if record["item_id"] != version["item_id"]:
                        raise DomainError("scope_denied", 403)
                job = self.create(
                    tx,
                    job_id=body["job_id"],
                    kind=body["kind"],
                    target=body["target"],
                    input_digest=body["input_digest"],
                    operation_id=body["operation_id"],
                    work_id=caller.work_id,
                )
                # Admission is not launch. Caller-owned CLI receives its capability out of band.
                tx.put(
                    "job_inputs",
                    job["job_id"],
                    {
                        "admission": body,
                        "configuration": self.settings.view(),
                        "owner_epoch": self.owner_epoch,
                        "work_id": caller.work_id,
                        "invocation_id": caller.invocation_id,
                    },
                )
                self.emit(tx, job, caller)
                return job

            return tx.operation(
                "jobs.admit",
                body["operation_id"],
                {"body": body, "work_id": caller.work_id},
                effect,
            )

    def create(
        self,
        tx: Transaction,
        *,
        job_id: str,
        kind: str,
        target: dict[str, Any],
        input_digest: str,
        operation_id: str,
        work_id: str | None = None,
        owner_operation_id: str | None = None,
        provider_id: str | None = None,
        render_spec: dict[str, Any] | None = None,
        expected_frames: int | None = None,
        enqueue_kind: str | None = None,
    ) -> dict[str, Any]:
        """Trusted extension primitive. Must share caller's publication/review/budget transaction."""
        self.gate.require(str(tx.project_id))
        if not work_id and not owner_operation_id:
            raise DomainError("attribution_unavailable", 409)
        job: dict[str, Any] = dict(
            schema_version="1.0",
            job_id=job_id,
            project_id=tx.project_id,
            work_id=work_id,
            owner_operation_id=owner_operation_id,
            target=target,
            attempt_id=f"attempt-{uuid4()}",
            attempt_number=1,
            kind=kind,
            state="planned",
            state_revision=0,
            availability="pending",
            input_digest=input_digest,
            external_job_id=None,
            provider_id=provider_id,
            owner_epoch=self.owner_epoch,
            completed_frames=None,
            expected_frames=expected_frames,
            artifact_ids=[],
            render_spec=render_spec,
        )
        tx.put("jobs", job_id, job, schema="JobRecord")
        self.save_attempt(tx, job, operation_id)
        if enqueue_kind:
            tx.enqueue(
                job["attempt_id"],
                enqueue_kind,
                {"job_id": job_id, "attempt_id": job["attempt_id"], "input_digest": input_digest},
            )
        return job

    @staticmethod
    def freeze_inputs(tx: Transaction, job_id: str, inputs: dict[str, Any]) -> None:
        """Freeze a trusted extension's non-secret execution inputs in its admission transaction."""
        tx.require("jobs", job_id)
        previous = tx.get("job_inputs", job_id, required=False)
        if previous is not None:
            if previous != inputs:
                raise DomainError("idempotency_conflict", 409)
            return
        tx.put("job_inputs", job_id, inputs)

    def inputs(self, project_id: str, job_id: str) -> dict[str, Any]:
        with self.registry.project_store(project_id).transaction(write=False) as tx:
            tx.require("jobs", job_id)
            return tx.require("job_inputs", job_id)

    @staticmethod
    def save_attempt(tx: Transaction, job: dict[str, Any], operation_id: str | None = None) -> None:
        identity = job["attempt_id"]
        old = tx.get("attempts", identity, required=False)
        attempt = {
            key: job[key]
            for key in (
                "schema_version",
                "project_id",
                "job_id",
                "attempt_id",
                "attempt_number",
                "owner_epoch",
                "input_digest",
                "state",
                "external_job_id",
            )
        }
        attempt["native_exit_code"] = job.get("native_exit_code")
        attempt["trigger_operation_id"] = old["trigger_operation_id"] if old else operation_id
        tx.put(
            "attempts",
            identity,
            attempt,
            schema="JobAttempt",
            expected_revision=tx.revision("attempts", identity) if old else None,
        )

    @staticmethod
    def emit(tx: Transaction, job: dict[str, Any], caller: Caller) -> None:
        kind = "user" if caller.channel == "owner" else caller.channel
        tx.emit(
            "job.state_changed",
            job,
            actor={"kind": kind, "id": caller.subject_id},
            work_id=job["work_id"],
        )

    def get(self, project_id: str, job_id: str) -> dict[str, Any]:
        with self.registry.project_store(project_id).transaction(write=False) as tx:
            return tx.require("jobs", job_id)

    def dispatch_cancellations(self, project_id: str) -> list[dict[str, Any]]:
        """Deliver recorded owner cancellations without opening the execution gate.

        Only pending exact-attempt cancellation intents are eligible. An uncertain
        adapter acknowledgement remains uncertain until its own receipt reconciliation.
        Returning from a canceller proves delivery, not a successful native exit.
        """
        if self.registry.active_id() != project_id:
            raise DomainError("scope_denied", 403)
        store = self.registry.project_store(project_id)
        with store.transaction(write=False) as tx:
            pending = [
                row[0]
                for row in tx.connection.execute(
                    "SELECT id FROM outbox WHERE kind='job.cancel' AND state='pending' ORDER BY rowid"
                )
            ]

        def deliver(body: dict[str, Any]) -> dict[str, Any]:
            with store.transaction(write=False) as tx:
                job = tx.require("jobs", body["job_id"])
                attempt = tx.require("attempts", body["attempt_id"])
                if attempt["job_id"] != job["job_id"] or attempt["project_id"] != project_id:
                    raise DomainError("scope_denied", 403)
                if attempt["state"] in TERMINAL:
                    return {
                        "job_id": job["job_id"],
                        "attempt_id": attempt["attempt_id"],
                        "observed_state": attempt["state"],
                        "adapter_called": False,
                    }
                if job["attempt_id"] != body["attempt_id"] or job["state"] != "cancel_requested":
                    raise DomainError("scope_denied", 403)
                canceller = self.cancellers.get(job["kind"])
                if canceller is None:
                    raise DomainError(
                        "capability_unavailable", 501, "Cancellation adapter unavailable"
                    )
            canceller(job)
            with store.transaction(write=False) as tx:
                observed = tx.require("attempts", attempt["attempt_id"])
            return {
                "job_id": job["job_id"],
                "attempt_id": attempt["attempt_id"],
                "observed_state": observed["state"],
                "adapter_called": True,
            }

        results = []
        for identity in pending:
            dispatch_one(store, identity, self.gate, {"job.cancel": deliver})
            results.append({"operation_id": identity})
        return results

    def retry(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        validate("JobRetry", body)
        self.check_caller(caller, "retryToolJob", body["project_id"])
        if caller.channel != "tool" or not caller.work_id:
            raise DomainError("scope_denied", 403)
        with self.registry.project_store(body["project_id"]).transaction() as tx:

            def effect() -> dict[str, Any]:
                self.gate.require(body["project_id"])
                active_work = tx.require("work", str(caller.work_id))
                if active_work["project_id"] != body["project_id"] or active_work["state"] not in {
                    "running",
                    "awaiting_user",
                }:
                    raise DomainError("no_active_work", 409)
                job = tx.require("jobs", body["job_id"])
                if job["project_id"] != body["project_id"]:
                    raise DomainError("scope_denied", 403)
                cross_work = job["work_id"] != caller.work_id
                _, context = retry_context(
                    tx,
                    caller,
                    active_work,
                    owner_epoch=self.owner_epoch,
                    require_invocation=cross_work,
                )
                if context and context.get("execution_scope") == "planning_only":
                    raise DomainError(
                        "scope_denied",
                        403,
                        "Retry is unavailable while execution_scope is planning_only. No render was started.",
                    )
                authorising_request_id = None
                if cross_work:
                    if job["kind"] != "render":
                        raise DomainError(
                            "scope_denied",
                            403,
                            "This job belongs to an earlier request. No job was started.",
                        )
                    denied = EARLIER_RENDER_RETRY_DENIED
                    origin = (
                        tx.get("work", job["work_id"], required=False) if job["work_id"] else None
                    )
                    if (
                        not origin
                        or origin.get("project_id") != body["project_id"]
                        or origin.get("state") not in {"completed", "failed", "stopped"}
                    ):
                        raise DomainError("scope_denied", 403, denied)
                    probe = self.retry_eligibility.get("render")
                    if probe is None or (authorising_request_id := probe(tx, caller, job)) is None:
                        raise DomainError("scope_denied", 403, denied)
                self.cas(job, body)
                if job["kind"] not in {"render", "encode", "unity"} or job["state"] not in {
                    "failed",
                    "cancelled",
                }:
                    raise DomainError("invalid_state_transition")
                evidence = tx.get("job_evidence", job["attempt_id"], required=False)
                if not evidence or not evidence.get("terminal"):
                    raise DomainError(
                        "invalid_state_transition", message="Terminal proof is required"
                    )
                job.update(
                    attempt_id=f"attempt-{uuid4()}",
                    attempt_number=job["attempt_number"] + 1,
                    state="planned",
                    state_revision=job["state_revision"] + 1,
                    external_job_id=None,
                    owner_epoch=self.owner_epoch,
                )
                job.pop("native_exit_code", None)
                job.pop("error_code", None)
                tx.put(
                    "jobs",
                    job["job_id"],
                    job,
                    expected_revision=body["expected_revision"],
                    schema="JobRecord",
                )
                self.save_attempt(tx, job, body["operation_id"])
                if authorising_request_id is not None:
                    tx.put(
                        "job_retry_authorisations",
                        job["attempt_id"],
                        {
                            "schema_version": "1.0",
                            "project_id": body["project_id"],
                            "job_id": job["job_id"],
                            "attempt_id": job["attempt_id"],
                            "attempt_number": job["attempt_number"],
                            "originating_work_id": job["work_id"],
                            "authorising_work_id": caller.work_id,
                            "authorising_request_id": authorising_request_id,
                            "invocation_id": caller.invocation_id,
                            "operation_id": body["operation_id"],
                        },
                    )
                self.emit(tx, job, caller)
                return job

            return tx.operation(
                "jobs.retry",
                body["operation_id"],
                {"body": body, "work_id": caller.work_id},
                effect,
            )

    @staticmethod
    def cas(job: dict[str, Any], body: dict[str, Any]) -> None:
        if job["state_revision"] != body["expected_revision"]:
            raise DomainError("revision_conflict", 409, current_revision=job["state_revision"])

    def update(
        self, caller: Caller, body: dict[str, Any], *, evidence: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        validate("JobStateUpdate", body)
        with self.registry.project_store(body["project_id"]).transaction() as tx:
            return self.update_in_transaction(tx, caller, body, evidence=evidence)

    def update_in_transaction(
        self, tx: Transaction, caller: Caller, body: dict[str, Any], *, evidence: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        """Apply the identical guarded update in the caller's atomic receipt transaction.

        This method never opens or commits a project transaction. The savepoint only
        scopes its local reduction and propagates failures to the enclosing transaction.
        """
        validate("JobStateUpdate", body)
        self.check_caller(caller, "updateJobState", body["project_id"])
        if tx.project_id != body["project_id"]:
            raise DomainError("scope_denied", 403)
        with tx.savepoint():
            admitted = tx.require("attempts", body["attempt_id"])
            if admitted["job_id"] != body["job_id"]:
                raise DomainError("scope_denied", 403)
            if caller.channel == "worker":
                if (
                    caller.job_id != body["job_id"]
                    or caller.attempt_id != body["attempt_id"]
                    or caller.input_digest != admitted["input_digest"]
                ):
                    raise DomainError("scope_denied", 403)
            elif (
                caller.channel != "tool"
                or caller.work_id != tx.require("jobs", body["job_id"])["work_id"]
            ):
                raise DomainError("scope_denied", 403)

            def effect() -> dict[str, Any]:
                job = tx.require("jobs", body["job_id"])
                self.cas(job, body)
                if body["attempt_id"] != job["attempt_id"]:
                    raise DomainError("scope_denied", 403)
                if caller.channel == "worker":
                    if (
                        caller.job_id != job["job_id"]
                        or caller.attempt_id != job["attempt_id"]
                        or caller.input_digest != job["input_digest"]
                    ):
                        raise DomainError("scope_denied", 403)
                elif caller.channel != "tool" or caller.work_id != job["work_id"]:
                    raise DomainError("scope_denied", 403)
                # The authenticated exact-attempt worker update itself is observed evidence.
                proof = evidence or (
                    body["operation_id"]
                    if caller.channel == "worker" or body.get("native_exit_code") is not None
                    else None
                )
                transition("job_attempt", job["state"], body["state"], evidence=proof)
                if body["state"] in TERMINAL and not proof:
                    raise DomainError(
                        "invalid_state_transition", message="Terminal proof is required"
                    )
                if ["pending", "partial", "complete"].index(body["availability"]) < [
                    "pending",
                    "partial",
                    "complete",
                ].index(job["availability"]):
                    raise DomainError("invalid_state_transition")
                for artifact_id in body.get("artifact_ids", []):
                    if tx.require("artifacts", artifact_id)["state"] != "ready":
                        raise DomainError("media_unavailable", 422)
                for field in ("external_job_id", "native_exit_code"):
                    if field in body and job.get(field) is not None and body[field] != job[field]:
                        raise DomainError(
                            "source_conflict", 409, "Observed job identity/evidence is immutable"
                        )
                if "artifact_ids" in body and not set(job["artifact_ids"]).issubset(
                    body["artifact_ids"]
                ):
                    raise DomainError("source_conflict", 409)
                if (
                    body.get("completed_frames") is not None
                    and job["completed_frames"] is not None
                    and body["completed_frames"] < job["completed_frames"]
                ):
                    raise DomainError("invalid_state_transition")
                for key in (
                    "state",
                    "availability",
                    "external_job_id",
                    "completed_frames",
                    "artifact_ids",
                    "native_exit_code",
                    "error_code",
                ):
                    if key in body:
                        job[key] = body[key]
                if (
                    job["completed_frames"] is not None
                    and job["expected_frames"] is not None
                    and job["completed_frames"] > job["expected_frames"]
                ):
                    raise DomainError("invalid_request")
                job["state_revision"] += 1
                tx.put(
                    "jobs",
                    job["job_id"],
                    job,
                    expected_revision=body["expected_revision"],
                    schema="JobRecord",
                )
                self.save_attempt(tx, job)
                if proof:
                    previous = tx.get("job_evidence", job["attempt_id"], required=False)
                    tx.put(
                        "job_evidence",
                        job["attempt_id"],
                        {"evidence": proof, "terminal": job["state"] in TERMINAL},
                        expected_revision=tx.revision("job_evidence", job["attempt_id"])
                        if previous
                        else None,
                    )
                self.emit(tx, job, caller)
                return job

            return tx.operation("jobs.update", body["operation_id"], body, effect)

    def cancel(
        self, caller: Caller, job_id: str, body: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        validate("RevisionCommand", body)
        self.check_caller(caller, "requestJobCancellation", body["project_id"])
        if caller.channel != "owner":
            raise DomainError("scope_denied", 403)
        with self.registry.project_store(body["project_id"]).transaction() as tx:

            def effect() -> dict[str, Any]:
                job = tx.require("jobs", job_id)
                self.cas(job, body)
                if job["state"] not in TERMINAL:
                    if job["kind"] not in self.cancellers:
                        raise DomainError("cancel_unsupported", 501)
                    probe = self.cancellation_eligibility.get(job["kind"])
                    if (
                        probe is not None
                        and probe[0] is self.cancellers[job["kind"]]
                        and not probe[1](tx, job)
                    ):
                        raise DomainError("cancel_unsupported", 501)
                    transition("job_attempt", job["state"], "cancel_requested")
                    job.update(state="cancel_requested", state_revision=job["state_revision"] + 1)
                    tx.put(
                        "jobs",
                        job_id,
                        job,
                        expected_revision=body["expected_revision"],
                        schema="JobRecord",
                    )
                    self.save_attempt(tx, job)
                    tx.enqueue(
                        body["operation_id"],
                        "job.cancel",
                        {"job_id": job_id, "attempt_id": job["attempt_id"]},
                    )
                    self.emit(tx, job, caller)
                return dict(
                    schema_version="1.0",
                    operation_id=body["operation_id"],
                    resource_id=job_id,
                    state=job["state"],
                    state_revision=job["state_revision"],
                    replayed=False,
                )

            result, replayed = tx.operation(
                "jobs.cancel", body["operation_id"], {**body, "job_id": job_id}, effect
            )
            result["replayed"] = replayed
            return result, replayed
