"""Generation identity resolution only. No application planner or creative target renaming."""

import hashlib
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.db import Transaction
from proscenium.services import Services

JSON = dict[str, Any]


def _identity(prefix: str, parts: list[str]) -> str:
    return prefix + hashlib.sha256(canonical(parts)).hexdigest()


def resolve_target(services: Services, caller: Caller, request: JSON) -> JSON:
    """Read immutable authored documents outside the admission transaction; retain CAS inputs."""
    settings = request["settings"]
    if set(settings) - {"target_slot"}:
        raise DomainError("invalid_request", message="Only target_slot is supported")
    slot = settings.get("target_slot")
    if slot is not None:
        validate("SelectionSlot", slot)
    elif "target_slot" in settings:
        raise DomainError("invalid_request")
    store = services.registry.project_store(request["project_id"])
    with store.transaction(write=False) as tx:
        work = tx.require("work", str(caller.work_id))
        if work["generation_task_id"] != request["generation_task_id"]:
            raise DomainError("scope_denied", 403)
        policy = tx.get("providers.tasks", work["generation_task_id"], required=False)
        item_id = (
            slot["item_id"]
            if slot and slot["kind"] == "item"
            else request["target_id"]
            if slot is None
            else None
        )
        if item_id is not None:
            item = tx.get("items", item_id, required=False)
            if slot is not None and item is None:
                raise DomainError("not_found", 404)
            context = None
            version_ref = None
            if item:
                original = tx.get("requests", work["originating_request_id"], required=False)
                context_id = original["submission"].get("context_id") if original else None
                context = tx.require("contexts", context_id) if context_id else None
                if context and context["project_id"] != request["project_id"]:
                    raise DomainError("scope_denied", 403)
                captured = (
                    {"item_id": context["item_id"], "version_id": context["version_id"]}
                    if context and context["item_id"] and context["version_id"]
                    else None
                )
                if captured and captured["item_id"] == item_id:
                    version_ref = captured
                elif item["current_version_id"]:
                    version_ref = {"item_id": item_id, "version_id": item["current_version_id"]}
                if version_ref:
                    version = tx.require("versions", version_ref["version_id"])
                    if (
                        version["item_id"] != item_id
                        or version["project_id"] != request["project_id"]
                    ):
                        raise DomainError("scope_denied", 403)
                else:
                    version = None
            else:
                version = None
                if not (
                    (policy and item_id in policy["targets"])
                    or item_id == work["generation_task_id"]
                ):
                    raise DomainError("scope_denied", 403, "Unstructured target must name its task")
            if item and item["project_id"] != request["project_id"]:
                raise DomainError("scope_denied", 403)
            return {
                "target": {"target_id": item_id, "version": version_ref, "shot_id": None},
                "slot": slot,
                "item": item,
                "version_record": version,
                "context": context,
                "work_id": work["work_id"],
            }
        assert slot is not None
        reference = (
            slot["storyboard_version"]
            if slot["kind"] == "storyboard_frame"
            else slot["sequence_version"]
        )
        item = tx.require("items", reference["item_id"])
        version = tx.require("versions", reference["version_id"])
        if (
            item["project_id"] != request["project_id"]
            or version["project_id"] != request["project_id"]
            or version["item_id"] != item["item_id"]
        ):
            raise DomainError("scope_denied", 403)
    document = services.extension("sources").authored(request["project_id"], version)
    expected_kind = "storyboard" if slot["kind"] == "storyboard_frame" else "sequence"
    if version["kind"] != expected_kind:
        raise DomainError("scope_denied", 403)
    validate("AuthoredShotPlan" if expected_kind == "storyboard" else "SequenceMap", document)
    if document["project_id"] != request["project_id"]:
        raise DomainError("scope_denied", 403)
    if slot["kind"] == "storyboard_frame":
        frame = next(
            (
                f
                for s in document["shots"]
                if s["shot_id"] == slot["shot_id"]
                for f in s["frames"]
                if f["frame_id"] == slot["frame_id"]
            ),
            None,
        )
        if frame is None:
            raise DomainError("not_found", 404)
        identity = _identity(
            "storyboard-", [document["plan_id"], slot["shot_id"], slot["frame_id"]]
        )
        shot_id = slot["shot_id"]
    else:
        if document["sequence_version_id"] != reference["version_id"]:
            raise DomainError("scope_denied", 403)
        entry = next((e for e in document["entries"] if e["entry_id"] == slot["entry_id"]), None)
        if entry is None:
            raise DomainError("not_found", 404)
        identity = _identity("sequence-", [item["item_id"], slot["entry_id"]])
        shot_id = entry["shot_id"]
    return {
        "target": {"target_id": identity, "version": reference, "shot_id": shot_id},
        "slot": slot,
        "item": item,
        "version_record": version,
        "context": None,
        "document_sha256": hashlib.sha256(canonical(document)).hexdigest(),
        "work_id": work["work_id"],
    }


def admit_target(tx: Transaction, work: JSON, resolved: JSON) -> JSON:
    """Recheck identities and lazily bind only the frozen originating Queue allowance."""
    if work["work_id"] != resolved["work_id"]:
        raise DomainError("scope_denied", 403)
    for namespace, record, key in (
        ("items", resolved["item"], "item_id"),
        ("versions", resolved["version_record"], "version_id"),
    ):
        if record and tx.require(namespace, record[key]) != record:
            raise DomainError("source_conflict", 409)
    context = resolved["context"]
    if context and tx.require("contexts", context["context_id"]) != context:
        raise DomainError("source_conflict", 409)
    task_id = work["generation_task_id"]
    policy = tx.get("providers.tasks", task_id, required=False)
    canonical_id = resolved["target"]["target_id"]
    if policy is None:
        effective = tx.get("copilot.effective", work["originating_request_id"], required=False)
        limit = effective.get("generation_attempt_limit") if effective else None
        if type(limit) is not int or limit < 1:
            raise DomainError(
                "capability_unavailable", 501, "Frozen Queue allowance is unavailable"
            )
        policy = {"task_id": task_id, "limit": limit, "targets": [canonical_id]}
        tx.put("providers.tasks", task_id, policy)
    elif canonical_id not in policy["targets"]:
        policy = {**policy, "targets": sorted([*policy["targets"], canonical_id])}
        tx.put(
            "providers.tasks",
            task_id,
            policy,
            expected_revision=tx.revision("providers.tasks", task_id),
        )
    return policy
