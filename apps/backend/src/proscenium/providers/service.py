"""Durable provider admission and explicitly driven execution against accepted F00 seams."""

import asyncio
import hashlib
import io
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from PIL import Image, ImageOps

from proscenium.api.errors import DomainError
from proscenium.api.interfaces import ArtifactService, ReviewStore
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.db import Transaction
from proscenium.db.paths import project_path
from proscenium.jobs import TERMINAL
from proscenium.jobs.resources import check_storage, reserve_generation, settle_generation
from proscenium.providers import klein, sd_turbo, sd_turbo_fp16, stable_audio
from proscenium.providers.comfy import ComfyClient, endpoint
from proscenium.providers.plugins import IMAGE, METADATA, ArtifactFormat, plugin
from proscenium.providers.preparation import validate_recipes
from proscenium.providers.targets import admit_target, resolve_target
from proscenium.providers.vlm import (
    DEFAULT_VLM_MAX_TOKENS,
    DEFAULT_VLM_REASONING_EFFORT,
    DEFAULT_VLM_TIMEOUT,
    RUBRIC_REVISION,
    VLMClient,
    findings,
)
from proscenium.reviews.references import References
from proscenium.services import Services

JSON = dict[str, Any]
GENERATION_KINDS = frozenset({"image_generation", "mesh_generation", "audio_generation"})
Registrar = Callable[[Caller, JSON], JSON]
REVIEWABLE_IMAGE_PURPOSES = frozenset(
    {"concept", "reference", "keyframe", "draft_motion", "inspection", "final"}
)
DRAFT_MOTION_RECEIPT_REQUIRED = (
    "Draft motion visual review targets must reference receipt-backed render or sequence frames"
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bind_generation_task(tx: Transaction, task_id: str, limit: int, targets: list[str]) -> None:
    """Explicit host binding retained for existing integrations. Production can bind lazily."""
    if type(limit) is not int or limit < 1 or not targets or len(set(targets)) != len(targets):
        raise DomainError("invalid_request")
    policy = {"task_id": task_id, "limit": limit, "targets": sorted(targets)}
    old = tx.get("providers.tasks", task_id, required=False)
    if old is not None:
        if old != policy:
            raise DomainError("idempotency_conflict", 409)
        return
    tx.put("providers.tasks", task_id, policy)


def extend_generation_task(
    tx: Transaction,
    caller: Caller,
    task_id: str,
    limit: int,
    operation_id: str,
    expected_revision: int,
) -> tuple[JSON, bool]:
    """Trusted owner-decision transaction seam. Raises allowance, never resets reservations.

    The parent calls this only from an authenticated owner's recorded instruction/decision,
    in that same transaction. No native tool or provider HTTP route exposes it.
    """
    if caller.channel != "owner" or type(limit) is not int or limit < 1:
        raise DomainError("scope_denied", 403)

    def effect() -> JSON:
        policy = tx.require("providers.tasks", task_id)
        if tx.revision("providers.tasks", task_id) != expected_revision:
            raise DomainError("revision_conflict", 409)
        if limit <= policy["limit"]:
            raise DomainError("invalid_request")
        updated = {**policy, "limit": limit}
        tx.put("providers.tasks", task_id, updated, expected_revision=expected_revision)
        tx.put(
            "providers.policy_extensions",
            operation_id,
            {
                "task_id": task_id,
                "previous_limit": policy["limit"],
                "limit": limit,
                "owner_subject_id": caller.subject_id,
            },
        )
        return updated

    return tx.operation(
        "providers.extend_task",
        operation_id,
        {
            "task_id": task_id,
            "limit": limit,
            "expected_revision": expected_revision,
            "owner_subject_id": caller.subject_id,
        },
        effect,
    )


class ProviderService:
    def __init__(
        self,
        services: Services,
        *,
        registrar: Registrar | None = None,
        client: httpx.Client | None = None,
    ):
        self.services = services
        self.registrar = registrar
        self.client = client or httpx.Client(timeout=120, follow_redirects=False, trust_env=False)
        self.owns_client = client is None
        # A replacement adapter in the same ownership epoch shares the dispatch lock.
        self._lock = services.extensions.setdefault("providers.pump_lock", threading.Lock())
        self._review_claim_lock = services.extensions.setdefault(
            "providers.review_claim_lock", threading.Lock()
        )
        self._inflight_reviews: set[str] = services.extensions.setdefault(
            "providers.inflight_reviews", set()
        )
        self._background: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.options = dict(services.settings.provider_options)
        allowed = {
            "review_provider_id",
            "minimum_free_vram_bytes",
            "vlm_max_tokens",
            "vlm_reasoning_effort",
            "vlm_timeout",
            "image_profiles",
            "image_recipes",
            "mesh_profiles",
            "mesh_recipes",
            "audio_profiles",
            "audio_recipes",
        }
        if set(self.options) - allowed:
            raise ValueError(
                "Unknown provider_options keys. Credentials must use a parent transport"
            )
        for key in ("minimum_free_vram_bytes", "vlm_max_tokens", "vlm_timeout"):
            default_val = (
                0
                if key == "minimum_free_vram_bytes"
                else DEFAULT_VLM_MAX_TOKENS
                if key == "vlm_max_tokens"
                else int(DEFAULT_VLM_TIMEOUT)
            )
            value = self.options.get(key, default_val)
            if type(value) is not int or value < (0 if key == "minimum_free_vram_bytes" else 1):
                raise ValueError("Invalid provider execution limit")
        if self.options.get("vlm_max_tokens", DEFAULT_VLM_MAX_TOKENS) > DEFAULT_VLM_MAX_TOKENS:
            raise ValueError(f"VLM output is bounded to {DEFAULT_VLM_MAX_TOKENS} tokens")
        if self.options.get("vlm_timeout", int(DEFAULT_VLM_TIMEOUT)) > 600:
            raise ValueError("VLM timeout is bounded to 600 seconds")
        if self.options.get("vlm_reasoning_effort", DEFAULT_VLM_REASONING_EFFORT) not in {
            "off",
            "low",
            "medium",
            "high",
        }:
            raise ValueError("vlm_reasoning_effort must be off, low, medium or high")
        profiles = self.options.get("image_profiles", {})
        if not isinstance(profiles, dict):
            raise ValueError(
                "image_profiles must map host provider IDs to installed versioned image profiles"
            )
        for provider_id, revision in profiles.items():
            validate("Id", provider_id)
            if not isinstance(revision, str):
                raise ValueError("image profile revision must be a string")
            plugin("image", revision)
        mesh_profiles = self.options.get("mesh_profiles", {})
        if not isinstance(mesh_profiles, dict):
            raise ValueError(
                "mesh_profiles must map host provider IDs to installed versioned mesh profiles"
            )
        for provider_id, revision in mesh_profiles.items():
            validate("Id", provider_id)
            if not isinstance(revision, str):
                raise ValueError("mesh profile revision must be a string")
            plugin("image_to_3d", revision)
        validate_recipes(self.options)

    @property
    def artifacts(self) -> ArtifactService:
        return cast(ArtifactService, self.services.extension("artifacts"))

    @property
    def reviews(self) -> ReviewStore:
        return cast(ReviewStore, self.services.extension("reviews"))

    def provider(self, identity: str | None, capability: str) -> JSON:
        candidates = [
            s
            for s in self.services.settings.services
            if capability in s["capabilities"]
            and (identity is None or s["provider_id"] == identity)
        ]
        if len(candidates) != 1:
            raise DomainError("capability_unavailable", 501, "Configure one explicit provider")
        result = dict(candidates[0])
        result["endpoint"] = endpoint(result["endpoint"])
        if result["available"] is False:
            raise DomainError("provider_unavailable", 503)
        return result

    def artifact_bytes(self, project_id: str, artifact_id: str) -> tuple[JSON, bytes]:
        artifact = self.artifacts.get(project_id, artifact_id)
        if artifact["project_id"] != project_id or artifact["state"] != "ready":
            raise DomainError("media_unavailable", 422)
        try:
            data = self.artifacts.ready_path(project_id, artifact_id).read_bytes()
        except DomainError as exc:
            if exc.code != "hash_mismatch":
                raise
        else:
            if sha(data) == artifact["sha256"] and len(data) == artifact["bytes"]:
                return artifact, data
        raise DomainError(
            "hash_mismatch", 422, "Stored artefact bytes do not match their recorded SHA-256 digest"
        )

    def image_bytes(self, project_id: str, artifact_id: str) -> tuple[JSON, bytes]:
        artifact, data = self.artifact_bytes(project_id, artifact_id)
        if artifact["kind"] != "image" or artifact["media_type"] not in {
            "image/png",
            "image/jpeg",
            "image/webp",
        }:
            raise DomainError(
                "unsupported_media_type",
                415,
                "Visual review targets must be ready PNG, JPEG or WebP still images",
            )
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > self.services.settings.upload_max_pixels:
                raise DomainError("image_dimensions_exceeded", 413)
            image.load()
            clean = ImageOps.exif_transpose(image).convert("RGB")
            output = io.BytesIO()
            clean.save(output, format="PNG")
        return artifact, output.getvalue()

    def _active_work(self, tx: Transaction, caller: Caller) -> JSON:
        work = tx.require("work", str(caller.work_id))
        if work["state"] not in {"running", "awaiting_user"}:
            raise DomainError("no_active_work", 409)
        return work

    def submit(self, caller: Caller, request: JSON) -> tuple[JSON, bool]:
        validate("GenerationSubmit", request)
        project_id = request["project_id"]
        self.services.jobs.check_caller(caller, "submitGenerationJob", project_id)
        if caller.channel != "tool":
            raise DomainError("scope_denied", 403)
        store = self.services.registry.project_store(project_id)
        # Replay before reading changed defaults or contacting a service. Fingerprint includes work.
        with store.transaction() as tx:
            existing = tx.get("providers.submissions", request["job_id"], required=False)
            if existing:
                if existing != {"request": request, "work_id": caller.work_id}:
                    raise DomainError("idempotency_conflict", 409)
                return tx.require("jobs", request["job_id"]), True
            prepared = tx.get("providers.prepared", request["workflow_artifact_id"], required=False)
        if request["capability"] == "audio" and prepared is None:
            raise DomainError(
                "invalid_request", message="Audio generation requires prepare_audio_generation"
            )
        revision = (
            self.options.get("image_profiles", {}).get(request["provider_id"])
            if request["capability"] == "image"
            else (
                self.options.get("mesh_profiles", {}).get(request["provider_id"])
                or (self.options.get("mesh_recipes", {}).get(request["provider_id"]) or {}).get(
                    "profile_revision"
                )
            )
            if request["capability"] == "image_to_3d"
            else None
        )
        if prepared is not None:
            if request != prepared.get("submit_request"):
                raise DomainError("idempotency_conflict", 409)
            revision = prepared["profile_revision"]
        capability = plugin(request["capability"], revision)
        resolved = resolve_target(self.services, caller, request)
        if (resolved.get("context") or {}).get("execution_scope") == "planning_only":
            raise DomainError(
                "scope_denied",
                403,
                "Generation is unavailable while execution_scope is planning_only",
            )
        provider = self.provider(request["provider_id"], capability.capability)
        workflow, data = self.artifact_bytes(project_id, request["workflow_artifact_id"])
        prompt_artifact, prompt_bytes = self.artifact_bytes(
            project_id, request["prompt_artifact_id"]
        )
        try:
            graph = json.loads(data)
            prompt = prompt_bytes.decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise DomainError("invalid_request") from exc
        if not isinstance(graph, dict):
            raise DomainError("invalid_request")
        references: dict[str, JSON] = {}
        reference_ids = request["input_artifact_ids"]
        if capability.capability == "audio" and reference_ids:
            raise DomainError("invalid_request", message=stable_audio.NO_REFERENCES)
        if len(set(reference_ids)) != len(reference_ids):
            raise DomainError("invalid_request", message="Duplicate conditioning image")
        if (
            capability.revision == "comfy-image-1"
            and len(reference_ids) > klein.MAX_REFERENCE_IMAGES
        ):
            raise DomainError("invalid_request", message=klein.REFERENCE_LIMIT_MESSAGE)
        if (
            capability.revision in {sd_turbo.REVISION, sd_turbo_fp16.REVISION}
            and len(reference_ids) != 1
        ):
            raise DomainError(
                "invalid_request",
                message="Selected image profile requires one exact 512x512 reference",
            )
        for artifact_id in reference_ids:
            artifact, clean = self.image_bytes(project_id, artifact_id)
            required_dimensions = getattr(capability, "reference_dimensions", None)
            if required_dimensions and artifact.get("dimensions") != required_dimensions:
                raise DomainError(
                    "invalid_request",
                    message="Selected image profile requires exact 512x512 reference pixels; no implicit resize",
                )
            references[artifact_id] = {
                "sha256": artifact["sha256"],
                "derived_sha256": sha(clean),
                "dimensions": artifact.get("dimensions"),
            }
        try:
            bound = capability.bind_graph(
                graph,
                prompt,
                request["seed"],
                {k: "artifact:" + k for k in references},
                request["model_ids"],
            )
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise DomainError("invalid_request", message="Malformed API graph") from exc
        inputs = {
            "request": request,
            "plugin": {
                "capability": capability.capability,
                "revision": capability.revision,
                "expected_model_hashes": capability.expected_model_hashes,
                "conditioning_method": getattr(capability, "conditioning_method", None),
                "model_source": getattr(capability, "model_source", None),
            },
            "provider": provider,
            "graph": bound,
            "prompt": prompt,
            "workflow_sha256": workflow["sha256"],
            "prompt_sha256": prompt_artifact["sha256"],
            "references": references,
            "target_resolution": resolved,
            "minimum_free_vram_bytes": self.options.get("minimum_free_vram_bytes", 256 * 1024**2),
        }
        with store.transaction() as tx:

            def effect() -> JSON:
                self.services.gate.require(project_id)
                work = self._active_work(tx, caller)
                task_id = work["generation_task_id"]
                if request["generation_task_id"] != task_id:
                    raise DomainError("scope_denied", 403)
                policy = admit_target(tx, work, resolved)
                check_storage(store.path.parent, self.services.settings.free_space_reserve_bytes)
                reserve_generation(
                    tx, request["job_id"], task_id, resolved["target"]["target_id"], policy["limit"]
                )
                job = self.services.jobs.create(
                    tx,
                    job_id=request["job_id"],
                    kind=capability.job_kind,
                    target=resolved["target"],
                    input_digest=sha(canonical(inputs)),
                    operation_id=request["job_id"],
                    work_id=caller.work_id,
                    provider_id=provider["provider_id"],
                    enqueue_kind="providers.generation",
                )
                self.services.jobs.freeze_inputs(tx, job["job_id"], inputs)
                tx.put(
                    "providers.submissions",
                    job["job_id"],
                    {"request": request, "work_id": caller.work_id},
                )
                tx.put("providers.pending", job["job_id"], {"kind": "generation"})
                self.services.jobs.emit(tx, job, caller)
                return job

            return tx.operation(
                "providers.generation",
                request["job_id"],
                {"request": request, "work_id": caller.work_id},
                effect,
            )

    def request_review(self, caller: Caller, request: JSON) -> tuple[JSON, bool]:
        validate("ReviewRequest", request)
        project_id = request["project_id"]
        operation = "requestOwnerReview" if caller.channel == "owner" else "requestToolReview"
        self.services.jobs.check_caller(caller, operation, project_id)
        if caller.channel not in {"owner", "tool"}:
            raise DomainError("scope_denied", 403)
        reviews = self.reviews  # Absence is an honest capability_unavailable before job admission.
        store = self.services.registry.project_store(project_id)
        identity = request["review_id"]
        binding = {
            "request": request,
            "work_id": caller.work_id if caller.channel == "tool" else None,
        }
        with store.transaction() as tx:
            old = tx.get("providers.review_requests", identity, required=False)
            if old:
                if old["binding"] != binding:
                    raise DomainError("idempotency_conflict", 409)
                return tx.require("providers.tickets", identity), True
        provider = self.provider(self.options.get("review_provider_id"), "vlm")
        if not provider["model_id"]:
            raise DomainError("capability_unavailable", 501, "Configure a vision model ID")
        hashes = {}
        image_hashes = {}
        target_purposes = {}
        for target in request["targets"]:
            artifact, clean = self.image_bytes(project_id, target["artifact_id"])
            if artifact["sha256"] != target["displayed_sha256"]:
                raise DomainError(
                    "hash_mismatch",
                    422,
                    "Target displayed_sha256 does not match the registered image artefact",
                )
            if artifact.get("dimensions") != target["dimensions"]:
                raise DomainError(
                    "frame_conflict",
                    422,
                    "Target dimensions do not match the registered image artefact",
                )
            if artifact["purpose"] not in REVIEWABLE_IMAGE_PURPOSES:
                raise DomainError(
                    "invalid_request",
                    422,
                    "Artefact purpose is not eligible for visual review; supply concept, reference, keyframe, draft_motion, inspection or final still images",
                )
            if artifact["purpose"] == "draft_motion" and target["origin"] not in {
                "blender_render",
                "sequence_render",
            }:
                raise DomainError(
                    "invalid_request",
                    422,
                    DRAFT_MOTION_RECEIPT_REQUIRED,
                )
            hashes[target["artifact_id"]] = artifact["sha256"]
            image_hashes[target["artifact_id"]] = sha(clean)
            target_purposes[target["artifact_id"]] = artifact["purpose"]
        refs = References(self.services)
        with store.transaction(write=False) as tx:
            frames = tx.all("frames") if "draft_motion" in target_purposes.values() else []
            for target in request["targets"]:
                refs.target(tx, target)
                # Sequence maps prove contributor receipts, not the displayed PNG itself.
                if target_purposes[target["artifact_id"]] == "draft_motion" and not any(
                    frame["artifact_id"] == target["artifact_id"]
                    and frame["sha256"] == target["displayed_sha256"]
                    and frame["dimensions"] == target["dimensions"]
                    for frame in frames
                ):
                    raise DomainError("invalid_request", 422, DRAFT_MOTION_RECEIPT_REQUIRED)
        for identity in request["telemetry_artifact_ids"]:
            artifact, data = self.artifact_bytes(project_id, identity)
            if artifact["media_type"] != "application/json" or len(data) > 1024 * 1024:
                raise DomainError("unsupported_media_type", 415)
            json.loads(data)
            hashes[identity] = artifact["sha256"]
        inputs = {
            "request": request,
            "provider": provider,
            "hashes": hashes,
            "clean_image_hashes": image_hashes,
            "target_purposes": target_purposes,
            "vlm_max_tokens": self.options.get("vlm_max_tokens", DEFAULT_VLM_MAX_TOKENS),
            "vlm_reasoning_effort": self.options.get(
                "vlm_reasoning_effort", DEFAULT_VLM_REASONING_EFFORT
            ),
            "vlm_timeout": self.options.get("vlm_timeout", int(DEFAULT_VLM_TIMEOUT)),
            "rubric_revision": RUBRIC_REVISION,
        }
        with store.transaction() as tx:

            def effect() -> JSON:
                if caller.channel == "tool":
                    self._active_work(tx, caller)
                check_storage(store.path.parent, self.services.settings.free_space_reserve_bytes)
                job = self.services.jobs.create(
                    tx,
                    job_id=f"review-job-{uuid4()}",
                    kind="vlm_review",
                    target={"target_id": request["review_id"], "version": None, "shot_id": None},
                    input_digest=sha(canonical(inputs)),
                    operation_id=request["review_id"],
                    work_id=caller.work_id if caller.channel == "tool" else None,
                    owner_operation_id=request["review_id"] if caller.channel == "owner" else None,
                    provider_id=provider["provider_id"],
                    enqueue_kind="providers.review",
                )
                ticket = reviews.create(tx, request, job["job_id"], caller=caller)
                validate("ReviewTicket", ticket)
                if ticket["job_id"] != job["job_id"] or ticket["review_id"] != request["review_id"]:
                    raise DomainError("source_conflict", 409)
                self.services.jobs.freeze_inputs(tx, job["job_id"], inputs)
                tx.put(
                    "providers.review_requests",
                    request["review_id"],
                    {"binding": binding, "job_id": job["job_id"]},
                )
                tx.put("providers.tickets", request["review_id"], ticket)
                tx.put("providers.pending", job["job_id"], {"kind": "review"})
                self.services.jobs.emit(tx, job, caller)
                return ticket

            return tx.operation("providers.review", request["review_id"], binding, effect)

    @staticmethod
    def worker(job: JSON) -> Caller:
        return Caller(
            "worker",
            "provider-worker",
            project_id=job["project_id"],
            job_id=job["job_id"],
            attempt_id=job["attempt_id"],
            input_digest=job["input_digest"],
            operations=("updateJobState", "completeToolReview", "registerToolArtifact"),
        )

    def update(self, job: JSON, state: str, **fields: Any) -> JSON:
        if state in {"running", "succeeded"}:
            fields.setdefault("error_code", None)
        body = dict(
            schema_version="1.0",
            operation_id=f"provider-state-{uuid4()}",
            project_id=job["project_id"],
            job_id=job["job_id"],
            attempt_id=job["attempt_id"],
            expected_revision=job["state_revision"],
            state=state,
            availability=fields.pop("availability", job["availability"]),
            **fields,
        )
        return self.services.jobs.update(self.worker(job), body, evidence="provider observation")[0]

    def complete_review(
        self,
        caller: Caller,
        request: JSON,
        *,
        diagnostics: JSON | None = None,
    ) -> tuple[JSON, bool]:
        validate("ReviewComplete", request)
        project_id = request["project_id"]
        self.services.jobs.check_caller(caller, "completeToolReview", project_id)
        store = self.services.registry.project_store(project_id)
        with store.transaction(write=False) as tx:
            admitted = tx.require("providers.review_requests", request["review_id"])
            job = tx.require("jobs", admitted["job_id"])
        if caller.channel == "worker":
            if (
                caller.job_id != job["job_id"]
                or caller.attempt_id != job["attempt_id"]
                or caller.input_digest != job["input_digest"]
            ):
                raise DomainError("scope_denied", 403)
        elif caller.channel != "tool" or not job["work_id"] or caller.work_id != job["work_id"]:
            raise DomainError("scope_denied", 403)
        original = admitted["binding"]["request"]
        if request["status"] == "failed" and request["findings"]:
            raise DomainError("invalid_request")
        # Validate all findings even for the internal completion protocol. Never trust host fields.
        for finding in request["findings"]:
            if (
                finding["project_id"] != project_id
                or finding["review_id"] != request["review_id"]
                or finding["target"] not in original["targets"]
                or finding["state"] != "unreviewed"
                or finding["revision"] != 1
            ):
                raise DomainError("scope_denied", 403)
            findings(
                original,
                {
                    "findings": [
                        {
                            "target_index": original["targets"].index(finding["target"]),
                            "observation": finding["observation"],
                            "recommendation": finding["recommendation"],
                            "evidence": finding["evidence"],
                        }
                    ]
                },
            )
        # W05 owns atomic finding storage and its own completion idempotency.
        ticket = self.reviews.complete(caller, request, diagnostics=diagnostics)
        validate("ReviewTicket", ticket)
        if (
            ticket["review_id"] != request["review_id"]
            or ticket["job_id"] != job["job_id"]
            or ticket["status"] != request["status"]
        ):
            raise DomainError("source_conflict", 409)
        with store.transaction() as tx:
            prior = tx.require("providers.tickets", request["review_id"])
            replayed = prior == ticket
            if not replayed:
                tx.put(
                    "providers.tickets",
                    request["review_id"],
                    ticket,
                    expected_revision=tx.revision("providers.tickets", request["review_id"]),
                )
        job = self.services.jobs.get(project_id, job["job_id"])
        if job["state"] not in TERMINAL:
            if request["status"] == "ready" and job["state"] in {"planned", "waiting_resource"}:
                job = self.update(job, "starting")
            self.update(
                job,
                "succeeded" if request["status"] == "ready" else "failed",
                availability="complete" if request["status"] == "ready" else job["availability"],
            )
        with store.transaction() as tx:
            row = tx.connection.execute(
                "SELECT state FROM outbox WHERE id=?", (job["attempt_id"],)
            ).fetchone()
            if row and row[0] == "pending":
                tx.claim_intent(job["attempt_id"])
            if row and row[0] in {"pending", "dispatching", "outcome_uncertain"}:
                tx.settle_intent(
                    job["attempt_id"], "acknowledged", {"review_id": request["review_id"]}
                )
        return ticket, replayed

    def execution(self, project_id: str, job_id: str, value: JSON | None = None) -> JSON:
        with self.services.registry.project_store(project_id).transaction() as tx:
            old = tx.get("providers.execution", job_id, required=False)
            if value is not None:
                tx.put(
                    "providers.execution",
                    job_id,
                    value,
                    expected_revision=tx.revision("providers.execution", job_id) if old else None,
                )
                return value
            return old or {}

    def _settle(self, job: JSON, *, executed: bool) -> None:
        with self.services.registry.project_store(job["project_id"]).transaction() as tx:
            settle_generation(
                tx,
                job["job_id"],
                executed=executed,
                evidence="observed execution" if executed else "no prompt submitted",
            )

    def _release(self, job: JSON) -> None:
        with self.services.resources.store.transaction(write=False) as tx:
            row = tx.connection.execute(
                "SELECT project_id,job_id,attempt_id FROM resource_claims WHERE resource='heavy-gpu'"
            ).fetchone()
        if row and tuple(row) == (job["project_id"], job["job_id"], job["attempt_id"]):
            try:
                self.services.resources.release(
                    "heavy-gpu",
                    job["project_id"],
                    job["job_id"],
                    job["attempt_id"],
                    terminal_evidence="provider job settled",
                )
            except DomainError as exc:
                if exc.code != "scope_denied":
                    raise
                # Another reconciliation already released this exact job. Never touch its successor.

    def _register(
        self,
        job: JSON,
        data: bytes,
        index: str,
        *,
        format: ArtifactFormat = IMAGE,
        derived_from: list[JSON] | None = None,
    ) -> str:
        if self.registrar is None:
            raise DomainError("capability_unavailable", 501, "Artifact registrar is not bound")
        digest = sha(data)
        root = Path(self.services.registry.get(job["project_id"])["path"])
        relative = f".proscenium/jobs/{job['job_id']}/{digest}.{format.suffix}"
        path = project_path(root, relative, managed=True, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                import os

                os.fsync(stream.fileno())
            path.chmod(0o400)
        except FileExistsError:
            if sha(path.read_bytes()) != digest:
                raise DomainError("hash_mismatch", 422) from None
        request = dict(
            schema_version="1.0",
            operation_id=f"{job['job_id']}-register-{index}",
            project_id=job["project_id"],
            artifact_id=f"{job['job_id']}-artifact-{index}",
            staging_path=relative,
            kind=format.kind,
            purpose=format.purpose,
            sha256=digest,
            media_type=format.media_type,
        )
        if derived_from:
            request["derived_from"] = derived_from
        validate("ArtifactRegister", request)
        result = self.registrar(self.worker(job), request)
        validate("Artifact", result)
        if (
            result["sha256"] != digest
            or result["state"] != "ready"
            or result["artifact_id"] != request["artifact_id"]
        ):
            raise DomainError("hash_mismatch", 422)
        return str(result["artifact_id"])

    def _finish_generation(
        self, job: JSON, inputs: JSON, execution: JSON, client: ComfyClient
    ) -> JSON:
        prompt_id = execution.get("prompt_id")
        if not prompt_id:
            prompt_id = client.reconcile(execution["client_id"])
            if not prompt_id:
                return job
            execution["prompt_id"] = prompt_id
            self.execution(job["project_id"], job["job_id"], execution)
        if job["external_job_id"] is None or job["state"] == "outcome_uncertain":
            job = self.update(job, "running", external_job_id=prompt_id)
        self._settle(job, executed=True)
        history = client.status(prompt_id)
        if not history:
            return job
        status = history.get("status", {})
        if status.get("status_str") == "error":
            job = self.update(job, "failed", error_code="provider_unavailable")
            self._release(job)
            return job
        if not status.get("completed"):
            return job
        # Provider execution is proved terminal. Download/storage recovery needs no GPU claim.
        self._release(job)
        capability = self._plugin(inputs)
        artifacts: list[str] = []
        inspections = []
        for item in capability.output_descriptors(history):
            try:
                data = client.download(item, maximum_bytes=capability.maximum_output_bytes)
                inspection = capability.inspect_output(
                    data,
                    self.services.settings.upload_max_pixels,
                    rendering=self.services.settings.rendering,
                )
            except DomainError as exc:
                invalid_audio = (
                    capability.capability == "audio" and exc.code == "unsupported_media_type"
                )
                if exc.code != "corrupt_provider_output" and not invalid_audio:
                    raise
                execution["phase"] = "failed"
                self.execution(job["project_id"], job["job_id"], execution)
                return self.update(job, "failed", error_code=exc.code)
            artifacts.append(
                self._register(
                    job,
                    data,
                    str(len(artifacts)),
                    format=capability.output_format,
                )
            )
            inspections.append(
                {"artifact_id": artifacts[-1], "sha256": sha(data), "inspection": inspection}
            )
        if not artifacts:
            raise DomainError("media_unavailable", 422)
        # Allowlist history evidence. Raw node logs/errors can contain sensitive server data.
        provenance = {
            "input_digest": job["input_digest"],
            "inputs": inputs,
            "graph": execution["graph"],
            "graph_sha256": execution["graph_sha256"],
            "prompt_id": prompt_id,
            "client_id": execution["client_id"],
            "derived_images": execution.get("derived_images", []),
            "output_artifact_ids": artifacts,
            "output_inspections": inspections,
        }
        artifacts.append(self._register(job, canonical(provenance), "provenance", format=METADATA))
        job = self.update(job, "succeeded", availability="complete", artifact_ids=artifacts)
        return job

    @staticmethod
    def _plugin(inputs: JSON):
        frozen = inputs.get("plugin")
        if frozen is None:
            # Compatibility for accepted pre-plugin IMAGE admissions only. Never infer a mesh revision.
            if inputs["request"]["capability"] != "image":
                raise DomainError("capability_unavailable", 501, "Missing frozen generation plugin")
            return plugin("image", "comfy-image-1")
        if frozen["capability"] != inputs["request"]["capability"]:
            raise DomainError("source_conflict", 409)
        return plugin(frozen["capability"], frozen["revision"])

    def _generation(self, job: JSON, inputs: JSON, execution: JSON) -> JSON:
        capability = self._plugin(inputs)
        if capability.job_kind != job["kind"]:
            raise DomainError("source_conflict", 409)
        client = ComfyClient(inputs["provider"]["endpoint"], self.client)
        if execution.get("phase") == "submitting":
            return self._finish_generation(job, inputs, execution, client)
        if execution.get("phase") not in {None, "waiting"}:
            # A crash in preparation cannot have submitted inference. Explicit recovery only.
            return job
        if self.registrar is None:
            raise DomainError("capability_unavailable", 501, "Artifact registrar is not bound")
        catalog = client.capabilities()
        capability.preflight(inputs["graph"], catalog, inputs["request"]["model_ids"])
        if client.busy(inputs["minimum_free_vram_bytes"]) or not self.services.resources.acquire(
            "heavy-gpu", job["project_id"], job["job_id"], job["attempt_id"]
        ):
            self.execution(job["project_id"], job["job_id"], {"phase": "waiting"})
            if job["state"] == "waiting_resource":
                return job
            return self.update(job, "waiting_resource")
        self.execution(job["project_id"], job["job_id"], {"phase": "preparing"})
        references = {}
        derivatives: list[str] = []
        # Canonical storage sorts object keys. The request array preserves conditioning order.
        for identity in inputs["request"]["input_artifact_ids"]:
            expected = inputs["references"][identity]
            _, data = self.image_bytes(job["project_id"], identity)
            if sha(data) != expected["derived_sha256"]:
                raise DomainError("hash_mismatch", 422)
            derived_id = self._register(
                job,
                data,
                "input-" + str(len(derivatives)),
                derived_from=[
                    {
                        "kind": "encode",
                        "input_artifact_ids": [identity],
                        "parameters": {"encoding": "RGB PNG", "exif_transposed": True},
                    }
                ],
            )
            derivatives.append(derived_id)
            references[identity] = client.upload(data, sha(data))
        graph = capability.bind_graph(
            inputs["graph"],
            inputs["prompt"],
            inputs["request"]["seed"],
            references,
            inputs["request"]["model_ids"],
        )
        capability.preflight(graph, catalog, inputs["request"]["model_ids"])
        execution = {
            "phase": "submitting",
            "client_id": str(uuid4()),
            "graph": graph,
            "graph_sha256": sha(canonical(graph)),
            "derived_images": derivatives,
        }
        job = self.update(job, "starting")
        self.execution(job["project_id"], job["job_id"], execution)
        # From this durable marker onwards no exception authorises another POST /prompt.
        try:
            execution["prompt_id"] = client.submit(graph, execution["client_id"])
        except httpx.HTTPStatusError as exc:
            if 400 <= exc.response.status_code < 500 and exc.response.status_code != 408:
                execution["phase"] = "rejected"
                self.execution(job["project_id"], job["job_id"], execution)
                self._settle(job, executed=False)
                self._release(job)
                return self.update(job, "failed", error_code="invalid_request")
            raise
        self.execution(job["project_id"], job["job_id"], execution)
        return self._finish_generation(job, inputs, execution, client)

    def _review(self, job: JSON, inputs: JSON) -> JSON:
        request = inputs["request"]
        images = []
        telemetry = {}
        for target in request["targets"]:
            artifact, clean = self.image_bytes(job["project_id"], target["artifact_id"])
            if (
                artifact["sha256"] != inputs["hashes"][target["artifact_id"]]
                or sha(clean) != inputs["clean_image_hashes"][target["artifact_id"]]
            ):
                raise DomainError("hash_mismatch", 422)
            images.append(clean)
        for identity in request["telemetry_artifact_ids"]:
            artifact, data = self.artifact_bytes(job["project_id"], identity)
            if artifact["sha256"] != inputs["hashes"][identity]:
                raise DomainError("hash_mismatch", 422)
            telemetry[identity] = json.loads(data)
        job = self.update(job, "starting")
        self.execution(job["project_id"], job["job_id"], {"phase": "submitting"})
        vlm = VLMClient(inputs["provider"]["endpoint"], self.client)
        result, diagnostics = vlm.review_with_diagnostics(
            inputs["provider"]["model_id"],
            request,
            images,
            telemetry,
            max_tokens=inputs.get("vlm_max_tokens", DEFAULT_VLM_MAX_TOKENS),
            reasoning_effort=inputs.get("vlm_reasoning_effort", DEFAULT_VLM_REASONING_EFFORT),
            target_purposes=inputs.get("target_purposes"),
            timeout=float(inputs.get("vlm_timeout", DEFAULT_VLM_TIMEOUT)),
        )
        completion = dict(
            schema_version="1.0",
            operation_id=request["review_id"] + "-complete",
            project_id=job["project_id"],
            review_id=request["review_id"],
            status="ready",
            findings=result,
        )
        # Persist only validated output privately for safe DB-completion replay after a crash.
        self.execution(
            job["project_id"],
            job["job_id"],
            {"phase": "review_complete", "completion": completion, "diagnostics": diagnostics},
        )
        self.complete_review(self.worker(job), completion, diagnostics=diagnostics)
        return self.services.jobs.get(job["project_id"], job["job_id"])

    def _run_review_job(self, project_id: str, job_id: str) -> JSON | None:
        """Execute a single vlm_review job outside the shared ComfyUI dispatch lock."""
        self.services.gate.require(project_id)
        store = self.services.registry.project_store(project_id)
        with self._review_claim_lock:
            if job_id in self._inflight_reviews:
                return None
            with store.transaction() as tx:
                job = tx.require("jobs", job_id)
                if job["kind"] != "vlm_review" or job["state"] in TERMINAL:
                    return None
                execution = tx.get("providers.execution", job_id, required=False) or {}
                row = tx.connection.execute(
                    "SELECT state FROM outbox WHERE id=?", (job["attempt_id"],)
                ).fetchone()
                if not row:
                    raise DomainError("scope_denied", 403)
                if row[0] == "pending":
                    tx.claim_intent(job["attempt_id"])
                elif row[0] in {"dispatching", "outcome_uncertain"} and not execution:
                    return None
                if execution.get("phase") == "checking":
                    return None
                if execution.get("phase") not in {None, "waiting", "review_complete"}:
                    return None
                if execution.get("phase") in {None, "waiting"}:
                    tx.put(
                        "providers.execution",
                        job_id,
                        {"phase": "checking"},
                        expected_revision=tx.revision("providers.execution", job_id)
                        if execution
                        else None,
                    )
            self._inflight_reviews.add(job_id)
        try:
            self.services.gate.require(project_id)
            inputs = self.services.jobs.inputs(project_id, job_id)
            execution = self.execution(project_id, job_id)
            try:
                check_storage(store.path.parent, self.services.settings.free_space_reserve_bytes)
                if execution.get("phase") == "review_complete":
                    self.complete_review(
                        self.worker(job),
                        execution["completion"],
                        diagnostics=execution.get("diagnostics"),
                    )
                    result = self.services.jobs.get(project_id, job_id)
                else:
                    result = self._review(job, inputs)
            except (
                httpx.HTTPError,
                DomainError,
                ValueError,
                KeyError,
                OSError,
                TypeError,
                IndexError,
                AttributeError,
            ) as exc:
                job = self.services.jobs.get(project_id, job_id)
                execution = self.execution(project_id, job_id)
                uncertain = execution.get("phase") == "submitting"
                code = exc.code if isinstance(exc, DomainError) else "provider_unavailable"
                err_diag = (
                    exc.details.get("diagnostics")
                    if isinstance(exc, DomainError)
                    and isinstance(exc.details.get("diagnostics"), dict)
                    else None
                )
                # A received malformed VLM response is a known failed review, not a relaunch.
                if isinstance(
                    exc,
                    (ValueError, KeyError, DomainError, TypeError, IndexError, AttributeError),
                ):
                    uncertain = False
                if uncertain:
                    self.reviews.record_diagnostics(
                        project_id,
                        inputs["request"]["review_id"],
                        {"error_code": "outcome_uncertain"},
                    )
                result = self.update(
                    job, "outcome_uncertain" if uncertain else "failed", error_code=code
                )
                if not uncertain:
                    self.complete_review(
                        self.worker(job),
                        dict(
                            schema_version="1.0",
                            operation_id=inputs["request"]["review_id"] + "-failed",
                            project_id=project_id,
                            review_id=inputs["request"]["review_id"],
                            status="failed",
                            findings=[],
                            error_code=code,
                        ),
                        diagnostics=err_diag or {"error_code": code},
                    )
            with store.transaction() as tx:
                row = tx.connection.execute(
                    "SELECT state FROM outbox WHERE id=?", (job["attempt_id"],)
                ).fetchone()
                if row and row[0] in {"dispatching", "outcome_uncertain"}:
                    tx.settle_intent(
                        job["attempt_id"],
                        "outcome_uncertain"
                        if result["state"] == "outcome_uncertain"
                        else "acknowledged",
                        {"job_id": job_id, "state": result["state"]},
                    )
                if result["state"] == "outcome_uncertain":
                    review_id = inputs["request"]["review_id"]
                    ticket = tx.get("reviews", review_id, required=False)
                    if ticket is not None:
                        projected = self.reviews.ticket_view(tx, ticket)
                        if projected.get("human_review_state") != "not_requested":
                            tx.emit(
                                "review.changed",
                                projected,
                                actor={"kind": "worker", "id": "provider-worker"},
                                work_id=job.get("work_id"),
                            )
            return result
        finally:
            with self._review_claim_lock:
                self._inflight_reviews.discard(job_id)

    def pump_reviews(self, project_id: str) -> list[JSON]:
        self.services.gate.require(project_id)
        store = self.services.registry.project_store(project_id)
        with store.transaction(write=False) as tx:
            review_job_ids = [
                row[0]
                for row in tx.connection.execute(
                    "SELECT id FROM records WHERE namespace='providers.pending' ORDER BY id"
                ).fetchall()
                if (j := tx.get("jobs", row[0], required=False)) is not None
                and j["kind"] == "vlm_review"
                and j["state"] not in TERMINAL
            ]
        results = []
        for jid in review_job_ids:
            self.services.gate.require(project_id)
            res = self._run_review_job(project_id, jid)
            if res is not None:
                results.append(res)
        return results

    def pump(self, project_id: str, job_id: str | None = None) -> list[JSON]:
        """Explicit execution/resume only. Never called by installation, startup or reads.

        A parent may call repeatedly after explicit Resume. Unknown prompt acceptance is
        reconciled read-only. VLM uncertainty is never replayed, and slow VLM inference
        runs outside the ComfyUI dispatch lock.
        """
        self.services.gate.require(project_id)
        store = self.services.registry.project_store(project_id)
        if job_id is not None:
            with store.transaction(write=False) as tx:
                single = tx.require("jobs", job_id)
            if single["kind"] == "vlm_review":
                res = self._run_review_job(project_id, job_id)
                return [res] if res is not None else []
        results = []
        with self._lock:
            with store.transaction(write=False) as tx:
                jobs = (
                    [tx.require("jobs", job_id)]
                    if job_id
                    else [
                        tx.require("jobs", row[0])
                        for row in tx.connection.execute(
                            "SELECT id FROM records WHERE namespace='providers.pending' ORDER BY id"
                        ).fetchall()
                    ]
                )
            for job in jobs:
                if job["state"] in TERMINAL or job["kind"] == "vlm_review":
                    continue
                self.services.gate.require(project_id)
                inputs = self.services.jobs.inputs(project_id, job["job_id"])
                execution = self.execution(project_id, job["job_id"])
                with store.transaction() as tx:
                    row = tx.connection.execute(
                        "SELECT state FROM outbox WHERE id=?", (job["attempt_id"],)
                    ).fetchone()
                    if not row:
                        raise DomainError("scope_denied", 403)
                    if row[0] == "pending":
                        tx.claim_intent(job["attempt_id"])
                    elif row[0] in {"dispatching", "outcome_uncertain"} and not execution:
                        # Another worker or a crashed preflight owns this intent. Never relaunch.
                        continue
                    current_execution = tx.get("providers.execution", job["job_id"], required=False)
                    if current_execution and current_execution.get("phase") == "checking":
                        continue
                    if execution.get("phase") in {None, "waiting"}:
                        tx.put(
                            "providers.execution",
                            job["job_id"],
                            {"phase": "checking"},
                            expected_revision=tx.revision("providers.execution", job["job_id"])
                            if current_execution
                            else None,
                        )
                try:
                    check_storage(
                        store.path.parent, self.services.settings.free_space_reserve_bytes
                    )
                    if job["kind"] in GENERATION_KINDS:
                        result = self._generation(job, inputs, execution)
                    else:
                        raise DomainError("scope_denied", 403)
                except (
                    httpx.HTTPError,
                    DomainError,
                    ValueError,
                    KeyError,
                    OSError,
                    TypeError,
                    IndexError,
                    AttributeError,
                ) as exc:
                    job = self.services.jobs.get(project_id, job["job_id"])
                    execution = self.execution(project_id, job["job_id"])
                    uncertain = execution.get("phase") == "submitting"
                    code = exc.code if isinstance(exc, DomainError) else "provider_unavailable"
                    if job["kind"] in GENERATION_KINDS and not uncertain:
                        self._settle(job, executed=False)
                        if execution.get("phase") == "preparing":
                            self._release(job)
                    result = self.update(
                        job, "outcome_uncertain" if uncertain else "failed", error_code=code
                    )
                with store.transaction() as tx:
                    row = tx.connection.execute(
                        "SELECT state FROM outbox WHERE id=?", (job["attempt_id"],)
                    ).fetchone()
                    if row and row[0] in {"dispatching", "outcome_uncertain"}:
                        tx.settle_intent(
                            job["attempt_id"],
                            "outcome_uncertain"
                            if result["state"] == "outcome_uncertain"
                            else "acknowledged",
                            {"job_id": job["job_id"], "state": result["state"]},
                        )
                results.append(result)
        if job_id is None and "lifecycle" not in self.services.extensions:
            results.extend(self.pump_reviews(project_id))
        return results

    def start(self, project_id: str, *, interval: float = 1) -> None:
        """Parent calls only after explicit Resume. Installation does not start a worker."""
        self.services.gate.require(project_id)
        if self._background and not self._background.done():
            return
        self._stop.clear()

        async def run() -> None:
            while not self._stop.is_set():
                try:
                    await asyncio.to_thread(self.pump, project_id)
                except DomainError:
                    return  # Closed gate/project switch. No automatic Resume.
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except TimeoutError:
                    pass

        self._background = asyncio.create_task(run())

    async def close(self) -> None:
        self._stop.set()
        if self._background:
            await self._background  # Do not cancel a thread in the middle of provider admission.
        if self.owns_client:
            self.client.close()


def install(
    services: Services, *, registrar: Registrar | None = None, client: httpx.Client | None = None
) -> ProviderService:
    existing = services.extensions.get("providers")
    if existing is not None:
        return cast(ProviderService, existing)
    adapter = ProviderService(services, registrar=registrar, client=client)
    services.extensions["providers"] = adapter
    return adapter
