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
from proscenium.providers.comfy import ComfyClient, bind_graph, endpoint, preflight
from proscenium.providers.vlm import VLMClient, findings
from proscenium.services import Services

JSON = dict[str, Any]
Registrar = Callable[[Caller, JSON], JSON]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bind_generation_task(tx: Transaction, task_id: str, limit: int, targets: list[str]) -> None:
    """Trusted Queue/host callback, never a native tool. Freeze effective allowance/target IDs.

    Alias resolution belongs to the host planner: all aliases must resolve to one canonical
    target before admission. This function accepts only canonical target IDs.
    """
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
        self._background: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.options = dict(services.settings.provider_options)
        allowed = {"review_provider_id", "minimum_free_vram_bytes", "vlm_max_tokens"}
        if set(self.options) - allowed:
            raise ValueError(
                "Unknown provider_options keys. Credentials must use a parent transport"
            )
        for key in ("minimum_free_vram_bytes", "vlm_max_tokens"):
            value = self.options.get(key, 0 if key == "minimum_free_vram_bytes" else 512)
            if type(value) is not int or value < (0 if key == "minimum_free_vram_bytes" else 1):
                raise ValueError("Invalid provider execution limit")
        if self.options.get("vlm_max_tokens", 512) > 512:
            raise ValueError("VLM output is bounded to 512 tokens")

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
        data = self.artifacts.ready_path(project_id, artifact_id).read_bytes()
        if sha(data) != artifact["sha256"] or len(data) != artifact["bytes"]:
            raise DomainError("hash_mismatch", 422)
        return artifact, data

    def image_bytes(self, project_id: str, artifact_id: str) -> tuple[JSON, bytes]:
        artifact, data = self.artifact_bytes(project_id, artifact_id)
        if artifact["kind"] != "image" or artifact["media_type"] not in {
            "image/png",
            "image/jpeg",
            "image/webp",
        }:
            raise DomainError("unsupported_media_type", 415)
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
        if request["capability"] != "image":
            raise DomainError("capability_unavailable", 501, "Mesh execution requires E04 adapter")
        if request["settings"]:
            raise DomainError(
                "invalid_request", message="Use exact graph inputs, not opaque settings"
            )
        store = self.services.registry.project_store(project_id)
        # Replay before reading changed defaults or contacting a service. Fingerprint includes work.
        with store.transaction() as tx:
            existing = tx.get("providers.submissions", request["job_id"], required=False)
            if existing:
                if existing != {"request": request, "work_id": caller.work_id}:
                    raise DomainError("idempotency_conflict", 409)
                return tx.require("jobs", request["job_id"]), True
        provider = self.provider(request["provider_id"], "image")
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
        for artifact_id in request["input_artifact_ids"]:
            artifact, clean = self.image_bytes(project_id, artifact_id)
            references[artifact_id] = {"sha256": artifact["sha256"], "derived_sha256": sha(clean)}
        try:
            bound = bind_graph(
                graph, prompt, request["seed"], {k: "artifact:" + k for k in references}
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise DomainError("invalid_request", message="Malformed API graph") from exc
        inputs = {
            "request": request,
            "provider": provider,
            "graph": bound,
            "prompt": prompt,
            "workflow_sha256": workflow["sha256"],
            "prompt_sha256": prompt_artifact["sha256"],
            "references": references,
            "minimum_free_vram_bytes": self.options.get("minimum_free_vram_bytes", 256 * 1024**2),
        }
        with store.transaction() as tx:

            def effect() -> JSON:
                self.services.gate.require(project_id)
                work = self._active_work(tx, caller)
                task_id = work["generation_task_id"]
                if request["generation_task_id"] != task_id:
                    raise DomainError("scope_denied", 403)
                policy = tx.get("providers.tasks", task_id, required=False)
                if not policy:
                    raise DomainError(
                        "capability_unavailable", 501, "Host task policy is not bound"
                    )
                if request["target_id"] not in policy["targets"]:
                    raise DomainError("scope_denied", 403, "Target is not admitted for this task")
                check_storage(store.path.parent, self.services.settings.free_space_reserve_bytes)
                reserve_generation(
                    tx, request["job_id"], task_id, request["target_id"], policy["limit"]
                )
                # Resolve an existing item head, but never move its authoring/selection state.
                item = tx.get("items", request["target_id"], required=False)
                version = None
                if item and item.get("current_version_id"):
                    version = {"item_id": item["item_id"], "version_id": item["current_version_id"]}
                job = self.services.jobs.create(
                    tx,
                    job_id=request["job_id"],
                    kind="image_generation",
                    target={"target_id": request["target_id"], "version": version, "shot_id": None},
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
        for target in request["targets"]:
            artifact, clean = self.image_bytes(project_id, target["artifact_id"])
            if (
                artifact["sha256"] != target["displayed_sha256"]
                or artifact.get("dimensions") != target["dimensions"]
                or artifact["purpose"]
                not in {"concept", "reference", "keyframe", "inspection", "final"}
            ):
                raise DomainError("hash_mismatch", 422)
            hashes[target["artifact_id"]] = artifact["sha256"]
            image_hashes[target["artifact_id"]] = sha(clean)
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
            "vlm_max_tokens": self.options.get("vlm_max_tokens", 512),
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
                ticket = reviews.create(tx, request, job["job_id"])
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

    def complete_review(self, caller: Caller, request: JSON) -> tuple[JSON, bool]:
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
        ticket = self.reviews.complete(caller, request)
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
        image: bool = True,
        derived_from: list[JSON] | None = None,
    ) -> str:
        if self.registrar is None:
            raise DomainError("capability_unavailable", 501, "Artifact registrar is not bound")
        digest = sha(data)
        root = Path(self.services.registry.get(job["project_id"])["path"])
        relative = f".proscenium/jobs/{job['job_id']}/{digest}.{'png' if image else 'json'}"
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
            kind="image" if image else "document",
            purpose="concept" if image else "metadata",
            sha256=digest,
            media_type="image/png" if image else "application/json",
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
        artifacts: list[str] = []
        for output in history.get("outputs", {}).values():
            for item in output.get("images", []):
                data = client.download(item)
                with Image.open(io.BytesIO(data)) as image:
                    if (
                        image.format != "PNG"
                        or image.width * image.height > self.services.settings.upload_max_pixels
                    ):
                        raise DomainError("unsupported_media_type", 415)
                    image.verify()
                artifacts.append(self._register(job, data, str(len(artifacts))))
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
        }
        artifacts.append(self._register(job, canonical(provenance), "provenance", image=False))
        job = self.update(job, "succeeded", availability="complete", artifact_ids=artifacts)
        return job

    def _generation(self, job: JSON, inputs: JSON, execution: JSON) -> JSON:
        client = ComfyClient(inputs["provider"]["endpoint"], self.client)
        if execution.get("phase") == "submitting":
            return self._finish_generation(job, inputs, execution, client)
        if execution.get("phase") not in {None, "waiting"}:
            # A crash in preparation cannot have submitted inference. Explicit recovery only.
            return job
        if self.registrar is None:
            raise DomainError("capability_unavailable", 501, "Artifact registrar is not bound")
        catalog = client.capabilities()
        preflight(inputs["graph"], catalog, inputs["request"]["model_ids"])
        if client.busy(inputs["minimum_free_vram_bytes"]) or not self.services.resources.acquire(
            "heavy-gpu", job["project_id"], job["job_id"], job["attempt_id"]
        ):
            self.execution(job["project_id"], job["job_id"], {"phase": "waiting"})
            return self.update(job, "waiting_resource")
        self.execution(job["project_id"], job["job_id"], {"phase": "preparing"})
        references = {}
        derivatives: list[str] = []
        for identity, expected in inputs["references"].items():
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
        graph = bind_graph(inputs["graph"], inputs["prompt"], inputs["request"]["seed"], references)
        preflight(graph, catalog, inputs["request"]["model_ids"])
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
        result = VLMClient(inputs["provider"]["endpoint"], self.client).review(
            inputs["provider"]["model_id"],
            request,
            images,
            telemetry,
            max_tokens=inputs["vlm_max_tokens"],
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
            job["project_id"], job["job_id"], {"phase": "review_complete", "completion": completion}
        )
        self.complete_review(self.worker(job), completion)
        return self.services.jobs.get(job["project_id"], job["job_id"])

    def pump(self, project_id: str, job_id: str | None = None) -> list[JSON]:
        """Explicit execution/resume only. Never called by installation, startup or reads.

        A parent may call repeatedly after explicit Resume. Unknown prompt acceptance is
        reconciled read-only. VLM uncertainty is never replayed.
        """
        self.services.gate.require(project_id)
        store = self.services.registry.project_store(project_id)
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
                if job["state"] in TERMINAL:
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
                    if job["kind"] == "image_generation":
                        result = self._generation(job, inputs, execution)
                    elif job["kind"] == "vlm_review":
                        if execution.get("phase") == "review_complete":
                            self.complete_review(self.worker(job), execution["completion"])
                            result = self.services.jobs.get(project_id, job["job_id"])
                        elif execution:
                            continue
                        else:
                            result = self._review(job, inputs)
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
                    # A received malformed VLM response is a known failed review, not a relaunch.
                    if job["kind"] == "vlm_review" and isinstance(
                        exc,
                        (ValueError, KeyError, DomainError, TypeError, IndexError, AttributeError),
                    ):
                        uncertain = False
                    if job["kind"] == "image_generation" and not uncertain:
                        self._settle(job, executed=False)
                        if execution.get("phase") == "preparing":
                            self._release(job)
                    result = self.update(
                        job, "outcome_uncertain" if uncertain else "failed", error_code=code
                    )
                    if job["kind"] == "vlm_review" and not uncertain:
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
