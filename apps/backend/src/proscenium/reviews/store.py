"""F00-backed review store. Wire revisions are one-based; storage starts at zero."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.db import Transaction
from proscenium.db.store import now
from proscenium.reviews.references import References
from proscenium.services import Services


def receipt(body: dict[str, Any], identity: str, state: str, revision: int) -> dict[str, Any]:
    return dict(
        schema_version="1.0",
        operation_id=body["operation_id"],
        resource_id=identity,
        state=state,
        state_revision=revision,
        replayed=False,
    )


def cas(expected: int, actual: int) -> None:
    if expected != actual:
        raise DomainError("revision_conflict", 409, current_revision=actual)


def default_review_title(request: dict[str, Any]) -> str:
    explicit = request.get("title")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()[:160]
    targets = request.get("targets") or []
    subject = next((t for t in targets if t.get("origin") != "reference"), None) or (
        targets[0] if targets else {}
    )
    prefix = subject.get("shot_id") or subject.get("version_id") or subject.get("item_id") or ""
    raw_criteria = str(request.get("criteria") or "").strip()
    cleaned = re.sub(
        r"^(?:read-only\s+assessment\s+of\s+|assess\s+|evaluate\s+)",
        "",
        raw_criteria,
        flags=re.IGNORECASE,
    ).strip()
    clause = re.split(r"[.;\n]", cleaned, maxsplit=1)[0].strip()
    if len(clause) > 84:
        clause = clause[:81].rstrip() + "…"
    if prefix and clause:
        title = f"{prefix} · {clause}"
    else:
        title = clause or prefix or str(request.get("review_id", "Visual review"))
    return title[:160]


@dataclass(frozen=True)
class TicketContext:
    findings_by_review: dict[str, list[dict[str, Any]]]
    meta_by_review: dict[str, dict[str, Any]]
    requests_by_review: dict[str, dict[str, Any]]
    jobs_by_id: dict[str, dict[str, Any]]
    diag_by_review: dict[str, dict[str, Any]]
    review_revisions: dict[str, int]
    legacy_review_events: dict[str, tuple[int, Any]]
    legacy_job_events: dict[str, tuple[int, Any]]

    def legacy_created_at(self, review_id: str, job_id: str) -> str | None:
        matches = [
            event
            for event in (
                self.legacy_review_events.get(review_id),
                self.legacy_job_events.get(job_id),
            )
            if event is not None
        ]
        occurred_at = min(matches, key=lambda event: event[0])[1] if matches else None
        return occurred_at if isinstance(occurred_at, str) else None


class ReviewStore:
    def __init__(self, services: Services):
        self.services = services
        self.refs = References(services)

    def owner_selected(
        self,
        tx: Transaction,
        storyboard_version: dict[str, Any],
        shot_id: str,
        frame: dict[str, Any],
    ) -> bool:
        """W02 read-model helper. Derives the exact slot without editing authored JSON."""
        slot = {
            "kind": "storyboard_frame",
            "storyboard_version": storyboard_version,
            "shot_id": shot_id,
            "frame_id": frame["frame_id"],
        }
        key = hashlib.sha256(canonical(slot)).hexdigest()
        index = tx.get("review.selection_slots", key, required=False)
        if not index or not frame["artifact_id"]:
            return False
        record = tx.require("selections", index["selection_id"])
        if record["state"] != "selected":
            return False
        choice = record["choice"]
        if choice["kind"] == "artifact":
            return bool(choice["artifact_id"] == frame["artifact_id"])
        version = self.refs.version(tx, choice["version"])
        images = [
            a
            for a in version["artifact_ids"]
            if self.refs.artifact(tx, a, ready=False)["kind"] == "image"
        ]
        return images == [frame["artifact_id"]]

    def owner(self, caller: Caller, project_id: str) -> None:
        if caller.channel != "owner" or project_id != self.services.registry.active_id():
            raise DomainError("scope_denied", 403)

    @staticmethod
    def _legacy_created_at(tx: Transaction, review_id: str, job_id: str) -> str | None:
        row = tx.connection.execute(
            "SELECT json_extract(body, '$.occurred_at') FROM events "
            "WHERE (json_extract(body, '$.event_type')='job.state_changed' "
            "AND json_extract(body, '$.payload.job_id')=?) "
            "OR (json_extract(body, '$.event_type') IN ('review.ready', 'review.changed') "
            "AND json_extract(body, '$.payload.review_id')=?) "
            "ORDER BY sequence ASC LIMIT 1",
            (job_id, review_id),
        ).fetchone()
        return row[0] if row and isinstance(row[0], str) else None

    def _ensure_meta(self, tx: Transaction, review_id: str) -> dict[str, Any]:
        existing = tx.get("review.meta", review_id, required=False)
        if existing is not None:
            return dict(existing)
        ticket = tx.require("reviews", review_id)
        request = tx.get("review.requests", review_id, required=False)
        job = tx.get("jobs", ticket["job_id"], required=False) or {}
        unreviewed_count = sum(
            1
            for f in tx.all("findings")
            if f["review_id"] == review_id and f["state"] == "unreviewed"
        )
        is_owner = bool(job.get("owner_operation_id"))
        human_requested = is_owner
        return dict(
            channel="owner" if is_owner else "tool",
            work_id=job.get("work_id"),
            title=default_review_title(request) if request else review_id[:160],
            created_at=self._legacy_created_at(tx, review_id, ticket["job_id"]),
            human_requested=human_requested,
            question=None,
            resolved=not human_requested or (ticket["status"] == "ready" and unreviewed_count == 0),
            revision=max(1, tx.revision("reviews", review_id) + 1),
        )

    @staticmethod
    def _save_meta(tx: Transaction, review_id: str, meta: dict[str, Any]) -> None:
        old = tx.get("review.meta", review_id, required=False)
        tx.put(
            "review.meta",
            review_id,
            meta,
            expected_revision=tx.revision("review.meta", review_id) if old is not None else None,
        )

    def record_diagnostics(
        self, project_id: str, review_id: str, diagnostics: dict[str, Any]
    ) -> None:
        clean = validate("ReviewDiagnostics", diagnostics)
        store = self.services.registry.project_store(project_id)
        with store.transaction() as tx:
            old = tx.get("review.diagnostics", review_id, required=False)
            tx.put(
                "review.diagnostics",
                review_id,
                clean,
                expected_revision=tx.revision("review.diagnostics", review_id)
                if old is not None
                else None,
                schema="ReviewDiagnostics",
            )

    @staticmethod
    def preload_ticket_context(
        tx: Transaction, *, jobs: list[dict[str, Any]] | None = None
    ) -> TicketContext:
        def records(namespace: str) -> dict[str, dict[str, Any]]:
            return {
                identity: json.loads(body)
                for identity, body in tx.connection.execute(
                    "SELECT id, body FROM records WHERE namespace=?", (namespace,)
                )
            }

        findings: dict[str, list[dict[str, Any]]] = {}
        for finding in tx.all("findings"):
            findings.setdefault(finding["review_id"], []).append(finding)
        meta = records("review.meta")
        legacy = dict(
            tx.connection.execute(
                "SELECT r.id, r.revision FROM records r WHERE r.namespace='reviews' "
                "AND NOT EXISTS (SELECT 1 FROM records m WHERE m.namespace='review.meta' AND m.id=r.id)"
            )
        )
        review_events: dict[str, tuple[int, Any]] = {}
        job_events: dict[str, tuple[int, Any]] = {}
        if legacy:
            # The earliest matching event wins, even if its timestamp is missing.
            for sequence, event_type, review_id, job_id, occurred_at in tx.connection.execute(
                "SELECT sequence, json_extract(body, '$.event_type'), "
                "json_extract(body, '$.payload.review_id'), json_extract(body, '$.payload.job_id'), "
                "json_extract(body, '$.occurred_at') FROM events WHERE "
                "json_extract(body, '$.event_type') IN "
                "('job.state_changed', 'review.ready', 'review.changed') ORDER BY sequence"
            ):
                if event_type == "job.state_changed":
                    job_events.setdefault(job_id, (sequence, occurred_at))
                elif review_id in legacy:
                    review_events.setdefault(review_id, (sequence, occurred_at))
        return TicketContext(
            findings,
            meta,
            records("review.requests"),
            records("jobs") if jobs is None else {job["job_id"]: job for job in jobs},
            records("review.diagnostics"),
            legacy,
            review_events,
            job_events,
        )

    def ticket_view(
        self,
        tx: Transaction,
        ticket: dict[str, Any],
        *,
        override_error_code: str | None = None,
        context: TicketContext | None = None,
        validate_output: bool = True,
    ) -> dict[str, Any]:
        review_id = ticket["review_id"]
        if context is None:
            meta = tx.get("review.meta", review_id, required=False)
            request = tx.get("review.requests", review_id, required=False)
            job = tx.get("jobs", ticket["job_id"], required=False)
            diag = tx.get("review.diagnostics", review_id, required=False)
            review_findings = [f for f in tx.all("findings") if f["review_id"] == review_id]
        else:
            meta = context.meta_by_review.get(review_id)
            request = context.requests_by_review.get(review_id)
            job = context.jobs_by_id.get(ticket["job_id"])
            diag = context.diag_by_review.get(review_id)
            review_findings = context.findings_by_review.get(review_id, [])
        finding_count = len(review_findings)
        unreviewed_count = sum(1 for f in review_findings if f["state"] == "unreviewed")

        status = ticket["status"]
        error_code = override_error_code or (diag or {}).get("error_code")
        job_failed_or_uncertain = status == "failed"
        if job is not None:
            if job["state"] in {"failed", "cancelled"}:
                status = "failed"
                error_code = (
                    error_code
                    or job.get("error_code")
                    or ("cancelled" if job["state"] == "cancelled" else "failed")
                )
                job_failed_or_uncertain = True
            elif job["state"] == "outcome_uncertain":
                error_code = error_code or job.get("error_code") or "outcome_uncertain"
                job_failed_or_uncertain = True
            elif job["state"] in {"starting", "running"} and status == "pending":
                status = "running"
        if status == "failed" and not error_code:
            error_code = "failed"

        if meta is not None:
            title = meta.get("title") or (
                default_review_title(request) if request else review_id[:160]
            )
            created_at = meta.get("created_at")
            question = meta.get("question")
            human_requested = bool(meta.get("human_requested"))
            resolved = bool(meta.get("resolved"))
            revision = int(meta.get("revision", 1))
        else:
            title = default_review_title(request) if request else review_id[:160]
            created_at = (
                self._legacy_created_at(tx, review_id, ticket["job_id"])
                if context is None
                else context.legacy_created_at(review_id, ticket["job_id"])
            )
            question = None
            is_owner = bool((job or {}).get("owner_operation_id"))
            human_requested = is_owner
            resolved = not human_requested or (status == "ready" and unreviewed_count == 0)
            revision = max(
                1,
                (
                    tx.revision("reviews", review_id)
                    if context is None
                    else context.review_revisions[review_id]
                )
                + 1
                + sum(max(0, f["revision"] - 1) for f in review_findings),
            )

        if not human_requested or resolved:
            awaiting_human = False
        elif job_failed_or_uncertain:
            awaiting_human = bool(question)
        elif status in {"pending", "running"}:
            awaiting_human = True
        elif status == "ready":
            awaiting_human = unreviewed_count > 0 or bool(question)
        else:
            awaiting_human = False

        human_review_state = (
            "pending" if awaiting_human else ("resolved" if human_requested else "not_requested")
        )
        result = dict(
            schema_version="1.0",
            review_id=review_id,
            job_id=ticket["job_id"],
            status=status,
            title=title,
            created_at=created_at,
            revision=revision,
            human_review_state=human_review_state,
            awaiting_human=awaiting_human,
            question=question,
            finding_count=finding_count,
            unreviewed_count=unreviewed_count,
            error_code=error_code,
        )
        if validate_output:
            validate("ReviewTicket", result)
        return result

    def detail(self, tx: Transaction, review_id: str) -> dict[str, Any]:
        ticket = self.ticket_view(tx, tx.require("reviews", review_id), validate_output=False)
        request = tx.require("review.requests", review_id)
        review_findings = sorted(
            (f for f in tx.all("findings") if f["review_id"] == review_id),
            key=lambda f: f["finding_id"],
        )
        diagnostics = tx.get("review.diagnostics", review_id, required=False)
        return validate(
            "ReviewDetail",
            dict(
                schema_version="1.0",
                project_id=str(tx.project_id),
                ticket=ticket,
                criteria=request["criteria"],
                targets=request["targets"],
                telemetry_artifact_ids=request["telemetry_artifact_ids"],
                findings=review_findings,
                diagnostics=diagnostics,
            ),
        )

    def save_annotation(self, caller: Caller, body: dict[str, Any]) -> dict[str, Any]:
        validate("AnnotationSave", body)
        self.owner(caller, body["project_id"])
        with self.services.registry.project_store(body["project_id"]).transaction() as tx:

            def effect() -> dict[str, Any]:
                incoming = body["annotation"]
                identity = incoming["annotation_id"]
                old = tx.get("annotations", identity, required=False)
                revision = old["saved_revision"] if old else 0
                cas(body["expected_revision"], revision)
                if incoming["saved_revision"] != (revision or None):
                    raise DomainError("revision_conflict", 409, current_revision=revision)
                if old and canonical(old["target"]) != canonical(incoming["target"]):
                    raise DomainError(
                        "source_conflict", 409, message="Annotation target is immutable"
                    )
                if old and old.get("derived_from") != incoming.get("derived_from"):
                    raise DomainError("source_conflict", 409)
                self.refs.annotation(tx, incoming)
                saved = {**incoming, "saved_revision": revision + 1}
                tx.put(
                    "annotations",
                    identity,
                    saved,
                    schema="AnnotationSnapshot",
                    expected_revision=revision - 1 if old else None,
                )
                result = receipt(body, identity, "saved", revision + 1)
                tx.put(
                    "review.annotation_history",
                    f"{identity}:{revision + 1}",
                    saved,
                    schema="AnnotationSnapshot",
                )
                tx.emit("annotation.saved", result, actor={"kind": "user", "id": caller.subject_id})
                return result

            result, replayed = tx.operation(
                "reviews.annotation", body["operation_id"], body, effect
            )
            return {**result, "replayed": replayed}

    def save_selection(self, caller: Caller, body: dict[str, Any]) -> dict[str, Any]:
        validate("SelectionSave", body)
        self.owner(caller, body["project_id"])
        store = self.services.registry.project_store(body["project_id"])
        document = None
        pinned = None
        if body["slot"]["kind"] != "item":
            # Inspect F00's durable operation before source I/O. Old retries must
            # return their original result even if the historical media is offline.
            with store.transaction(write=False) as tx:
                previous = tx.connection.execute(
                    "SELECT fingerprint,result FROM operations WHERE scope=? AND id=?",
                    ("reviews.selection", body["operation_id"]),
                ).fetchone()
                if previous:
                    if previous[0] != hashlib.sha256(canonical(body)).hexdigest():
                        raise DomainError("idempotency_conflict", 409)
                    return {**json.loads(previous[1]), "replayed": True}
                key = (
                    "storyboard_version"
                    if body["slot"]["kind"] == "storyboard_frame"
                    else "sequence_version"
                )
                pinned = self.refs.version(tx, body["slot"][key])
            document = self.refs.selection_document(
                body["project_id"], pinned, body["slot"]["kind"]
            )
        with store.transaction() as tx:

            def effect() -> dict[str, Any]:
                slot_key = hashlib.sha256(canonical(body["slot"])).hexdigest()
                index = tx.get("review.selection_slots", slot_key, required=False)
                old = tx.get("selections", body["selection_id"], required=False)
                if index and index["selection_id"] != body["selection_id"]:
                    raise DomainError("revision_conflict", 409, current_revision=index["revision"])
                if old and canonical(old["slot"]) != canonical(body["slot"]):
                    raise DomainError("source_conflict", 409)
                revision = old["state_revision"] if old else 0
                cas(body["expected_revision"], revision)
                if pinned and tx.require("versions", pinned["version_id"]) != pinned:
                    raise DomainError("source_conflict", 409)
                self.refs.selection(tx, body["slot"], body["choice"], document)
                state = "selected" if body["action"] == "select" else "withdrawn"
                record = dict(
                    schema_version="1.0",
                    project_id=body["project_id"],
                    selection_id=body["selection_id"],
                    slot=body["slot"],
                    choice=body["choice"],
                    state=state,
                    state_revision=revision + 1,
                    actor={"kind": "user", "id": caller.subject_id},
                )
                # F00 enforces state_revision == storage revision. Reserve revision zero internally.
                if not old:
                    tx.put("selections", body["selection_id"], {"reserved": True})
                tx.put(
                    "selections",
                    body["selection_id"],
                    record,
                    expected_revision=revision,
                    schema="SelectionRecord",
                )
                tx.put(
                    "review.selection_slots",
                    slot_key,
                    {"selection_id": body["selection_id"], "revision": revision + 1},
                    expected_revision=tx.revision("review.selection_slots", slot_key)
                    if index
                    else None,
                )
                tx.put(
                    "review.selection_history",
                    f"{body['selection_id']}:{revision + 1}",
                    {"selection": record},
                )
                result = receipt(body, body["selection_id"], state, revision + 1)
                tx.emit("selection.changed", result, actor=record["actor"])
                return result

            result, replayed = tx.operation("reviews.selection", body["operation_id"], body, effect)
            return {**result, "replayed": replayed}

    def create(
        self,
        tx: Transaction,
        request: dict[str, Any],
        job_id: str,
        *,
        caller: Caller | None = None,
    ) -> dict[str, Any]:
        """Trusted W07 primitive inside its authorised, idempotent job transaction.

        This method does not admit a job or infer owner authority. W07 must authenticate
        the owner/tool and create the matching vlm_review job in this same transaction.
        """
        validate("ReviewRequest", request)
        if request["project_id"] != tx.project_id:
            raise DomainError("scope_denied", 403)

        def effect() -> dict[str, Any]:
            for target in request["targets"]:
                self.refs.target(tx, target)
            for identity in request["telemetry_artifact_ids"]:
                self.refs.artifact(tx, identity)
            job = tx.require("jobs", job_id)
            if job["kind"] != "vlm_review" or job["target"]["target_id"] != request["review_id"]:
                raise DomainError("scope_denied", 403)
            is_owner = (
                caller.channel == "owner"
                if caller is not None
                else bool(job.get("owner_operation_id"))
            )
            raw_question = request.get("question")
            question = (
                raw_question.strip()
                if isinstance(raw_question, str) and raw_question.strip()
                else None
            )
            human_requested = (
                bool(request["human_review"])
                if "human_review" in request
                else (is_owner or bool(question))
            )
            ticket = dict(
                schema_version="1.0",
                review_id=request["review_id"],
                job_id=job_id,
                status="pending",
            )
            tx.put("reviews", request["review_id"], ticket, schema="ReviewTicket")
            tx.put("review.requests", request["review_id"], request, schema="ReviewRequest")
            self._save_meta(
                tx,
                request["review_id"],
                dict(
                    channel="owner" if is_owner else "tool",
                    work_id=job["work_id"],
                    title=default_review_title(request),
                    created_at=now(),
                    human_requested=human_requested,
                    question=question,
                    resolved=False,
                    revision=1,
                ),
            )
            projected = self.ticket_view(tx, ticket)
            if human_requested:
                actor_kind = "user" if is_owner else (caller.channel if caller else "tool")
                actor_id = caller.subject_id if caller else (job.get("work_id") or "review-host")
                tx.emit(
                    "review.changed",
                    projected,
                    actor={"kind": actor_kind, "id": actor_id},
                    work_id=job["work_id"],
                )
            return projected

        result, _ = tx.operation(
            "reviews.create", request["review_id"], {"request": request, "job_id": job_id}, effect
        )
        return result

    def complete(
        self,
        caller: Caller,
        request: dict[str, Any],
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate("ReviewComplete", request)
        self.services.jobs.check_caller(caller, "completeToolReview", request["project_id"])
        with self.services.registry.project_store(request["project_id"]).transaction() as tx:
            ticket = tx.require("reviews", request["review_id"])
            job = tx.require("jobs", ticket["job_id"])
            if caller.channel == "worker":
                allowed = (
                    caller.job_id == job["job_id"]
                    and caller.attempt_id == job["attempt_id"]
                    and caller.input_digest == job["input_digest"]
                )
            else:
                allowed = caller.channel == "tool" and caller.work_id == job["work_id"]
            if not allowed:
                raise DomainError("scope_denied", 403)

            def effect() -> dict[str, Any]:
                if ticket["status"] not in {"pending", "running"}:
                    raise DomainError("invalid_state_transition", 409)
                original = tx.require("review.requests", request["review_id"])
                targets = {canonical(t) for t in original["targets"]}
                has_non_ref_candidate = any(t["origin"] != "reference" for t in original["targets"])
                permitted_visual = {t["artifact_id"] for t in original["targets"]}
                if request["status"] == "failed" and request["findings"]:
                    raise DomainError("invalid_request")
                for finding in request["findings"]:
                    if (
                        finding["project_id"] != tx.project_id
                        or finding["review_id"] != request["review_id"]
                        or canonical(finding["target"]) not in targets
                        or (has_non_ref_candidate and finding["target"]["origin"] == "reference")
                        or finding["revision"] != 1
                        or finding["state"] != "unreviewed"
                    ):
                        raise DomainError("source_conflict", 409)
                    for evidence in finding["evidence"]:
                        self.refs.artifact(tx, evidence["artifact_id"])
                        if evidence["kind"] == "visual":
                            if evidence["artifact_id"] not in permitted_visual:
                                raise DomainError("scope_denied", 403)
                        elif evidence["artifact_id"] not in original["telemetry_artifact_ids"]:
                            raise DomainError(
                                "source_conflict",
                                409,
                                message="Measurement lacks supplied telemetry",
                            )
                    tx.put("findings", finding["finding_id"], finding, schema="ReviewFinding")
                    tx.put(
                        "review.finding_history",
                        f"{finding['finding_id']}:1",
                        finding,
                        schema="ReviewFinding",
                    )
                if diagnostics is not None or request.get("error_code"):
                    existing_diag = (
                        tx.get("review.diagnostics", request["review_id"], required=False) or {}
                    )
                    merged_diag = validate(
                        "ReviewDiagnostics",
                        {
                            **existing_diag,
                            **(diagnostics or {}),
                            **(
                                {"error_code": request["error_code"]}
                                if request.get("error_code")
                                else {}
                            ),
                        },
                    )
                    tx.put(
                        "review.diagnostics",
                        request["review_id"],
                        merged_diag,
                        expected_revision=tx.revision("review.diagnostics", request["review_id"])
                        if tx.get("review.diagnostics", request["review_id"], required=False)
                        is not None
                        else None,
                        schema="ReviewDiagnostics",
                    )
                updated = {**ticket, "status": request["status"]}
                tx.put(
                    "reviews",
                    ticket["review_id"],
                    updated,
                    expected_revision=tx.revision("reviews", ticket["review_id"]),
                    schema="ReviewTicket",
                )
                meta = self._ensure_meta(tx, request["review_id"])
                meta["revision"] = int(meta.get("revision", 1)) + 1
                if (
                    request["status"] == "ready"
                    and not request["findings"]
                    and not meta.get("question")
                ):
                    meta["resolved"] = True
                self._save_meta(tx, request["review_id"], meta)
                projected = self.ticket_view(
                    tx, updated, override_error_code=request.get("error_code")
                )
                if request["status"] == "ready":
                    tx.emit(
                        "review.ready",
                        projected,
                        actor={"kind": caller.channel, "id": caller.subject_id},
                        work_id=job["work_id"],
                    )
                elif meta.get("human_requested"):
                    tx.emit(
                        "review.changed",
                        projected,
                        actor={"kind": caller.channel, "id": caller.subject_id},
                        work_id=job["work_id"],
                    )
                return projected

            result, _ = tx.operation("reviews.complete", request["operation_id"], request, effect)
            return result

    def escalate(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        validate("ReviewEscalate", body)
        project_id = body["project_id"]
        self.services.jobs.check_caller(caller, "escalateToolReview", project_id, admission=True)
        if caller.channel != "tool" or not caller.work_id:
            raise DomainError("scope_denied", 403)
        question = body["question"].strip()
        if not question:
            raise DomainError("invalid_request")
        new_title = (
            body["title"].strip()[:160]
            if isinstance(body.get("title"), str) and body["title"].strip()
            else None
        )
        store = self.services.registry.project_store(project_id)
        immutable = {"body": body, "work_id": caller.work_id}
        with store.transaction() as tx:

            def effect() -> dict[str, Any]:
                work = tx.require("work", str(caller.work_id))
                if work["state"] not in {"running", "awaiting_user"}:
                    raise DomainError("no_active_work", 409)
                review_id = body["review_id"]
                ticket = tx.require("reviews", review_id)
                job = tx.require("jobs", ticket["job_id"])
                if job["work_id"] != caller.work_id:
                    raise DomainError("scope_denied", 403)
                meta = self._ensure_meta(tx, review_id)
                if (
                    meta.get("human_requested")
                    and not meta.get("resolved")
                    and meta.get("question") is not None
                ):
                    if meta["question"] == question and (
                        new_title is None or meta.get("title") == new_title
                    ):
                        return self.ticket_view(tx, ticket)
                    raise DomainError("idempotency_conflict", 409)
                meta["human_requested"] = True
                meta["resolved"] = False
                meta["question"] = question
                if new_title is not None:
                    meta["title"] = new_title
                meta["revision"] = int(meta.get("revision", 1)) + 1
                self._save_meta(tx, review_id, meta)
                projected = self.ticket_view(tx, ticket)
                if tx.get("providers.tickets", review_id, required=False) is not None:
                    tx.put(
                        "providers.tickets",
                        review_id,
                        projected,
                        expected_revision=tx.revision("providers.tickets", review_id),
                    )
                tx.emit(
                    "review.changed",
                    projected,
                    actor={"kind": "tool", "id": caller.subject_id},
                    work_id=job["work_id"],
                )
                return projected

            return tx.operation("reviews.escalate", body["operation_id"], immutable, effect)

    def resolve(self, caller: Caller, review_id: str, body: dict[str, Any]) -> dict[str, Any]:
        validate("RevisionCommand", body)
        self.owner(caller, body["project_id"])
        with self.services.registry.project_store(body["project_id"]).transaction() as tx:

            def effect() -> dict[str, Any]:
                ticket = tx.require("reviews", review_id)
                current = self.ticket_view(tx, ticket)
                cas(body["expected_revision"], current["revision"])
                meta = self._ensure_meta(tx, review_id)
                meta["human_requested"] = True
                meta["resolved"] = True
                meta["revision"] = current["revision"] + 1
                self._save_meta(tx, review_id, meta)
                projected = self.ticket_view(tx, ticket)
                if tx.get("providers.tickets", review_id, required=False) is not None:
                    tx.put(
                        "providers.tickets",
                        review_id,
                        projected,
                        expected_revision=tx.revision("providers.tickets", review_id),
                    )
                tx.emit(
                    "review.changed",
                    projected,
                    actor={"kind": "user", "id": caller.subject_id},
                )
                return receipt(body, review_id, "resolved", projected["revision"])

            result, replayed = tx.operation(
                "reviews.resolve",
                body["operation_id"],
                {**body, "review_id": review_id},
                effect,
            )
            return {**result, "replayed": replayed}

    def reject(
        self, caller: Caller, review_id: str, finding_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        validate("RevisionCommand", body)
        self.owner(caller, body["project_id"])
        with self.services.registry.project_store(body["project_id"]).transaction() as tx:

            def effect() -> dict[str, Any]:
                finding = tx.require("findings", finding_id)
                if finding["review_id"] != review_id:
                    raise DomainError("not_found", 404)
                cas(body["expected_revision"], finding["revision"])
                if finding["state"] == "submitted":
                    raise DomainError("invalid_state_transition", 409)
                updated = {**finding, "state": "rejected", "revision": finding["revision"] + 1}
                self._finding(tx, updated)
                ticket = tx.require("reviews", review_id)
                meta = self._ensure_meta(tx, review_id)
                remaining_unreviewed = sum(
                    1
                    for f in tx.all("findings")
                    if f["review_id"] == review_id and f["state"] == "unreviewed"
                )
                if remaining_unreviewed == 0 and not meta.get("question"):
                    meta["resolved"] = True
                meta["revision"] = int(meta.get("revision", 1)) + 1
                self._save_meta(tx, review_id, meta)
                projected = self.ticket_view(tx, ticket)
                if tx.get("providers.tickets", review_id, required=False) is not None:
                    tx.put(
                        "providers.tickets",
                        review_id,
                        projected,
                        expected_revision=tx.revision("providers.tickets", review_id),
                    )
                tx.emit(
                    "review.changed",
                    projected,
                    actor={"kind": "user", "id": caller.subject_id},
                )
                return receipt(body, finding_id, "rejected", updated["revision"])

            result, replayed = tx.operation(
                "reviews.reject",
                body["operation_id"],
                {**body, "review_id": review_id, "finding_id": finding_id},
                effect,
            )
            return {**result, "replayed": replayed}

    @staticmethod
    def _finding(tx: Transaction, finding: dict[str, Any]) -> None:
        identity = finding["finding_id"]
        tx.put(
            "findings",
            identity,
            finding,
            expected_revision=tx.revision("findings", identity),
            schema="ReviewFinding",
        )
        tx.put(
            "review.finding_history",
            f"{identity}:{finding['revision']}",
            finding,
            schema="ReviewFinding",
        )

    def validate_and_submit_findings(
        self, tx: Transaction, request_id: str, approved_findings: list[dict[str, Any]]
    ) -> None:
        """W01 calls ONLY within acceptance of an authenticated owner's immutable message.

        Shares its transaction with request/context/event writes. The original observation
        is retained. The approved text and exact pre-submission revision are linked once.
        A rejected finding may be explicitly submitted at its current revision.
        """

        def effect() -> dict[str, Any]:
            seen = set()
            touched_reviews: list[str] = []
            for approved in approved_findings:
                validate("ApprovedFinding", approved)
                identity = approved["finding_id"]
                if identity in seen:
                    raise DomainError("invalid_request")
                seen.add(identity)
                finding = tx.require("findings", identity)
                if finding["project_id"] != tx.project_id:
                    raise DomainError("scope_denied", 403)
                cas(approved["finding_revision"], finding["revision"])
                if canonical(approved["target"]) != canonical(finding["target"]):
                    raise DomainError("source_conflict", 409)
                if (
                    finding["state"] == "submitted"
                    or tx.require("reviews", finding["review_id"])["status"] != "ready"
                ):
                    raise DomainError("invalid_state_transition", 409)
                self.refs.target(tx, finding["target"])
                self._finding(
                    tx, {**finding, "state": "submitted", "revision": finding["revision"] + 1}
                )
                tx.put(
                    "review.submissions", identity, {"request_id": request_id, "approved": approved}
                )
                if finding["review_id"] not in touched_reviews:
                    touched_reviews.append(finding["review_id"])
            for review_id in touched_reviews:
                ticket = tx.require("reviews", review_id)
                meta = self._ensure_meta(tx, review_id)
                remaining_unreviewed = sum(
                    1
                    for f in tx.all("findings")
                    if f["review_id"] == review_id and f["state"] == "unreviewed"
                )
                if remaining_unreviewed == 0:
                    meta["resolved"] = True
                meta["revision"] = int(meta.get("revision", 1)) + 1
                self._save_meta(tx, review_id, meta)
                projected = self.ticket_view(tx, ticket)
                if tx.get("providers.tickets", review_id, required=False) is not None:
                    tx.put(
                        "providers.tickets",
                        review_id,
                        projected,
                        expected_revision=tx.revision("providers.tickets", review_id),
                    )
                tx.emit(
                    "review.changed",
                    projected,
                    actor={"kind": "system", "id": "review-host"},
                    request_id=request_id,
                )
            return {"request_id": request_id}

        tx.operation("reviews.submit", request_id, approved_findings, effect)


def install(services: Services) -> ReviewStore:
    existing = services.extensions.get("reviews")
    if existing is not None:
        if not isinstance(existing, ReviewStore):
            raise RuntimeError("Incompatible reviews extension installed")
        return existing
    store = ReviewStore(services)
    services.extensions["reviews"] = store
    return store
