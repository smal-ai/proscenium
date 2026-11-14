"""F00-backed review store. Wire revisions are one-based; storage starts at zero."""

import hashlib
import json
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.db import Transaction
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

    def create(self, tx: Transaction, request: dict[str, Any], job_id: str) -> dict[str, Any]:
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
            ticket = dict(
                schema_version="1.0",
                review_id=request["review_id"],
                job_id=job_id,
                status="pending",
            )
            tx.put("reviews", request["review_id"], ticket, schema="ReviewTicket")
            tx.put("review.requests", request["review_id"], request, schema="ReviewRequest")
            return ticket

        result, _ = tx.operation(
            "reviews.create", request["review_id"], {"request": request, "job_id": job_id}, effect
        )
        return result

    def complete(self, caller: Caller, request: dict[str, Any]) -> dict[str, Any]:
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
                if request["status"] == "failed" and request["findings"]:
                    raise DomainError("invalid_request")
                for finding in request["findings"]:
                    if (
                        finding["project_id"] != tx.project_id
                        or finding["review_id"] != request["review_id"]
                        or canonical(finding["target"]) not in targets
                        or finding["revision"] != 1
                        or finding["state"] != "unreviewed"
                    ):
                        raise DomainError("source_conflict", 409)
                    for evidence in finding["evidence"]:
                        self.refs.artifact(tx, evidence["artifact_id"])
                        if (
                            evidence["kind"] == "measured"
                            and evidence["artifact_id"] not in original["telemetry_artifact_ids"]
                        ):
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
                updated = {**ticket, "status": request["status"]}
                tx.put(
                    "reviews",
                    ticket["review_id"],
                    updated,
                    expected_revision=tx.revision("reviews", ticket["review_id"]),
                    schema="ReviewTicket",
                )
                if request["status"] == "ready":
                    tx.emit(
                        "review.ready",
                        updated,
                        actor={"kind": caller.channel, "id": caller.subject_id},
                        work_id=job["work_id"],
                    )
                return updated

            result, _ = tx.operation("reviews.complete", request["operation_id"], request, effect)
            return result

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
            return {"request_id": request_id}

        tx.operation("reviews.submit", request_id, approved_findings, effect)


def install(services: Services) -> ReviewStore:
    existing = services.extensions.get("reviews")
    if existing is not None:
        if not isinstance(existing, ReviewStore):
            raise ValueError("A different review store is already installed")
        return existing
    store = ReviewStore(services)
    services.extensions["reviews"] = store
    return store
