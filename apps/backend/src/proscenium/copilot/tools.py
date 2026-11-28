"""Model tools call accepted host services with persisted native invocation authority."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import inspect
import json
import re
import time
from functools import cache
from typing import Any

from copilot.tools import Tool, ToolBinaryResult, ToolInvocation, ToolResult

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue.schema_paths import SchemaPathScope
from proscenium.catalogue.validation import (
    safe_pin_diagnostics,
    safe_schema_guidance,
    safe_schema_path,
    schema_issues,
    schema_issues_for_schema,
)
from proscenium.contracts import SCHEMA, canonical, validate
from proscenium.guidance import reviewed_reason
from proscenium.services import Services

from .outcomes import get_tool_outcome, record_tool_outcome
from .state import State, key
from .tool_schemas import inline_tool_schema, portable_tool_schema
from .web_search import INVALID_QUERY, clean_search_query, search_web
from .workflows import WORKFLOW_TOOLS
from .workflows import arguments as workflow_arguments
from .workflows import parameters as workflow_parameters
from .workflows import validation_parameters as workflow_validation_parameters

PREPARATION_TOOLS = frozenset(
    {
        "prepare_image_generation",
        "prepare_audio_generation",
        "prepare_video_generation",
        "prepare_mesh_generation",
    }
)

# Extension, method, normative input, normative result, capability operation.
TOOLS = {
    "validate_authored_document": (
        "authoring_validation",
        "validate",
        "AuthoredDocumentValidationRequest",
        "AuthoredDocumentValidationResult",
        "validateAuthoredDocument",
    ),
    "prepare_image_generation": (
        "image_preparation",
        "prepare",
        "ImageGenerationPrepare",
        "ImageGenerationPrepared",
        "prepareImageGeneration",
    ),
    "prepare_audio_generation": (
        "audio_preparation",
        "prepare",
        "AudioGenerationPrepare",
        "AudioGenerationPrepared",
        "prepareAudioGeneration",
    ),
    "prepare_video_generation": (
        "video_preparation",
        "prepare",
        "VideoGenerationPrepare",
        "VideoGenerationPrepared",
        "prepareVideoGeneration",
    ),
    "prepare_mesh_generation": (
        "mesh_preparation",
        "prepare",
        "MeshGenerationPrepare",
        "MeshGenerationPrepared",
        "prepareMeshGeneration",
    ),
    "checkpoint_sources": (
        "sources",
        "checkpoint",
        "CheckpointRequest",
        "CheckpointReceipt",
        "checkpoint_sources",
    ),
    "publish_outputs": (
        "catalogue",
        "publish",
        "PublicationSubmit",
        "PublicationReceipt",
        "publishOutputs",
    ),
    "request_preview": ("previews", "request", "PreviewRequest", "JobRecord", "requestPreview"),
    "set_preview_order": (
        "previews",
        "set_order",
        "PreviewOrderUpdate",
        "VersionRecord",
        "setPreviewOrder",
    ),
    "register_artifact": (
        "artifacts",
        "register",
        "ArtifactRegister",
        "Artifact",
        "registerToolArtifact",
    ),
    "submit_generation": (
        "generation",
        "submit",
        "GenerationSubmit",
        "JobRecord",
        "submitGenerationJob",
    ),
    "request_vlm_review": ("vlm", "request", "ReviewRequest", "ReviewTicket", "requestToolReview"),
    "escalate_vlm_review": (
        "vlm",
        "escalate",
        "ReviewEscalate",
        "ReviewTicket",
        "escalateToolReview",
    ),
    "admit_job": ("jobs", "admit", "JobAdmission", "JobRecord", "admitToolJob"),
    "retry_job": ("jobs", "retry", "JobRetry", "JobRecord", "retryToolJob"),
    "update_job_state": ("jobs", "update", "JobStateUpdate", "JobRecord", "updateJobState"),
}

# Native argument wrappers around existing reads, not additional public wire DTOs.
READ_TOOLS = {
    "get_job": (
        "job_id",
        "JobRecord",
        "Read an admitted job without starting or retrying it. Use wait_seconds (0..30) for bounded waiting instead of busy-polling.",
    ),
    "get_vlm_review": (
        "review_id",
        "ReviewDetail",
        "Read a VLM critique requested by the current work, including validated findings and diagnostics. Use wait_seconds (0..30) for bounded waiting instead of busy-polling.",
    ),
    "get_artifact": (
        "artifact_id",
        "Artifact",
        "Read registered artefact metadata by ID. Use include_image=true for inline image inspection without copying .proscenium/artefacts/.../content.",
    ),
    "get_version": (
        "version_id",
        "VersionRecord",
        "Read an exact published version, pinned source closure and registered output associations. Never infer the latest working source.",
    ),
    "list_job_frames": (
        "job_id",
        "FramePage",
        "Page verified frame receipts for the exact job/input identity in numeric source-frame order. Does not launch rendering.",
    ),
}
DISCOVERY_READS = {
    "search_web": (
        "",
        "WebSearchResult",
        "Discover public web sources with read-only search. Use a public research query, not private project content. Return exact source URLs; do not treat hits as acquired files.",
    ),
    "get_project_capabilities": (
        "",
        "ProjectCapabilities",
        "Read exposed tools, installed skills and observed provider recipes. No inference or service lifecycle actions.",
    ),
    "list_items": (
        "",
        "ItemPage",
        "Discover published catalogue items by name, description, tags or recorded provenance. Optional text query, scoped cursor and limit. Read-only; no vector search or source changes.",
    ),
}


def read_parameters(name: str) -> dict[str, Any]:
    return portable_tool_schema(read_validation_parameters(name))


def read_validation_parameters(name: str) -> dict[str, Any]:
    if name == "search_web":
        return dict(
            type="object",
            properties=dict(
                query={"type": "string", "minLength": 1, "maxLength": 512},
                limit={"type": "integer", "minimum": 1, "maximum": 15, "default": 8},
            ),
            required=["query"],
            additionalProperties=False,
        )
    if name == "get_project_capabilities":
        return dict(
            type="object",
            properties={"project_id": {"$ref": "#/$defs/Id"}},
            additionalProperties=False,
        )
    if name == "list_items":
        return dict(
            type="object",
            properties=dict(
                query={"type": "string", "maxLength": 512},
                cursor={"type": ["string", "null"]},
                limit={"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            ),
            additionalProperties=False,
        )
    identity, _, _ = READ_TOOLS[name]
    assert identity is not None
    properties: dict[str, Any] = {identity: {"$ref": "#/$defs/Id"}}
    if name == "get_artifact":
        properties["include_image"] = {"type": "boolean", "default": False}
    elif name in {"get_job", "get_vlm_review"}:
        properties["wait_seconds"] = {"type": "integer", "minimum": 0, "maximum": 30, "default": 0}
    if name == "list_job_frames":
        properties.update(
            cursor={"type": ["string", "null"]},
            limit={"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
        )
    return {
        "type": "object",
        "properties": properties,
        "required": [identity],
        "additionalProperties": False,
    }


def read_arguments(name: str, arguments: Any) -> dict[str, Any]:
    issues = schema_issues_for_schema(
        read_validation_parameters(name), arguments, exact_integers=True
    )
    if issues:
        code = "invalid_request"
        if (
            name in {"list_items", "list_job_frames"}
            and len(issues) == 1
            and issues[0]["path"] == "cursor"
            and issues[0]["code"] == "type"
        ):
            code = "invalid_cursor"
        raise DomainError(code, issues=issues)
    if name == "search_web":
        try:
            query = clean_search_query(arguments["query"])
        except DomainError as error:
            issue = dict(phase="schema", code="format", path="query", message=INVALID_QUERY)
            raise DomainError(error.code, error.status, error.message, issues=[issue]) from error
        return {"query": query, "limit": arguments.get("limit", 8)}
    return arguments


ERROR_GUIDANCE = {
    "corrupt_provider_output": "The provider returned a corrupted raster image or invalid audio/video. Inspect the failed job record; you may submit a corrected generation within the remaining task budget.",
    "retained_source_missing": "Registered image/media artefacts belong in AuthoredShotPlan frames and PublishedItem.artifact_ids, NOT in CheckpointRequest.retained_files or SourceClosure.files. Omit retained_files on checkpoint_sources (no commit was created) and retry with a fresh operation_id.",
    "scope_denied": "The requested resource or target is outside the active request scope. Check the active project and pinned request target before retrying.",
    "provider_unavailable": "The configured provider is unavailable. Read get_project_capabilities without starting, replacing or changing the service.",
    "capability_unavailable": "The requested capability is unavailable. Check the installed adapter/profile; do not install or repeatedly resubmit a substitute.",
    "source_conflict": "The source identity or base state does not match. Read the pinned item/version and source hashes before making a corrected request.",
    "hash_mismatch": "Registered bytes or the source digest do not match. Reconcile the original source/artefact identity; do not reuse this operation ID with changed input.",
    "frame_conflict": "The frame or its dimensions do not match the registered artefact. Check the original frame receipt before retrying.",
    "unsupported_media_type": "This operation does not support the supplied media type. Check the supported formats before retrying.",
    "dependency_missing": "A pinned source dependency is missing. Check the published source closure and dependency versions.",
    "idempotency_conflict": "This operation ID already binds different input or invocation authority. Inspect its original receipt; do not blindly repeat the effect.",
    "revision_conflict": "The record revision changed. Read the current record before submitting a corrected command.",
    "project_busy": "Execution is held or the project is unsettled. Reconcile the existing work and wait for explicit owner Resume.",
    "attribution_unavailable": "The native invocation cannot be joined safely. Retain the original work and reconcile; do not invent caller IDs.",
    "permission_denied": "This invocation is not authorised for a new effect. Respect the owner decision and active-project scope.",
    "invalid_request": "The request does not match the operation's contract or supported input. Correct the existing input rather than blindly retrying.",
    "not_found": "No record exists under that ID in the active project. Check the immutable selection and original receipt.",
    "invalid_cursor": "This cursor is invalid for the selected project/job or was altered. Restart the read without a cursor.",
}


@cache
def _tool_issue_schemas(name: str) -> tuple[tuple[dict[str, Any], ...], SchemaPathScope]:
    schemas: tuple[dict[str, Any], ...] = ()
    if name in TOOLS:
        definition = TOOLS[name][2]
        schemas = (SCHEMA["$defs"][definition],)
    elif name in WORKFLOW_TOOLS:
        schemas = (workflow_validation_parameters(name),)
    elif name in READ_TOOLS or name in DISCOVERY_READS:
        schemas = (read_validation_parameters(name),)
    if name in {"validate_authored_document", "publish_outputs"}:
        schemas += tuple(
            SCHEMA["$defs"][definition]
            for definition in ("AuthoredShotPlan", "SequenceMap", "PublicationSubmit")
        )
    return schemas, SchemaPathScope(schemas)


def tool_failure(error: DomainError, name: str) -> ToolResult:
    reviewed = reviewed_reason(error.message)
    safe_message = reviewed is not None
    message = reviewed or ERROR_GUIDANCE.get(
        error.code,
        "The operation could not complete. Inspect its original durable receipt before retrying; the external outcome may be uncertain.",
    )
    details: dict[str, Any] = {}
    profile = error.details.get("configured_assembly_profile_id")
    if (
        name == "assemble_sequence"
        and error.code == "invalid_request"
        and error.status == 422
        and type(profile) is str
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", profile)
    ):
        # Render the host's fixed guidance from the contract-shaped profile ID only.
        message = f"Unsupported assembly execution_profile_id; omit it or use '{profile}'"
    if name in PREPARATION_TOOLS and error.code == "scope_denied" and not safe_message:
        message = f"The target or project is outside the active request scope. Omit project_id, generation_task_id and unpublished target_id on {name}, or use an existing published item/slot."
    if (
        name in (PREPARATION_TOOLS | {"validate_authored_document"})
        and error.code == "invalid_request"
        and not safe_message
    ):
        message = (
            "Preflight validation rejected the request before any external job or inference was submitted. "
            + message
        )
    issues = error.details.get("issues", [])
    if isinstance(issues, list) and issues:
        # Share the validator's code/message allow-list, including contains and multipleOf,
        # then restrict the issue paths to this tool's schema family.
        schemas, scope = _tool_issue_schemas(name)
        safe = []
        for issue in issues[:25]:
            try:
                validate("ValidationIssue", issue)
            except DomainError:
                continue
            if (
                issue["phase"] == "schema"
                and safe_schema_guidance(issue["code"], issue["message"])
                and safe_schema_path(issue["path"], schemas, scope=scope)
            ):
                safe.append({key: issue[key] for key in ("phase", "code", "path", "message")})
        if safe:
            details["issues"] = safe
    pin_diagnostic = (
        name in {"publish_outputs", "validate_authored_document"}
        and safe_message
        and error.code in {"dependency_missing", "invalid_request"}
    )
    if pin_diagnostic:
        schemas, scope = _tool_issue_schemas(name)
        details.update(safe_pin_diagnostics(error, schemas, scope=scope))
    if name == "get_unity_result" and error.code == "project_busy" and not safe_message:
        message = "This Unity job has no finished observed result. Read get_job and reconcile the original job; do not launch another."
    revision = error.details.get("current_revision")
    if not pin_diagnostic and type(revision) is int and revision >= 0:
        details["current_revision"] = revision
    if name == "request_preview" and error.code in {"capability_unavailable", "invalid_request"}:
        # Public supported recipe, not private executable paths/configuration/MCP data.
        details.update(
            renderer_revision="proscenium-blender-1",
            engine="CYCLES",
            device="CPU",
            dimensions="positive exact PNG dimensions for keyframes; even width and height for H.264 motion",
            samples="explicit positive sample count",
        )
    return ToolResult(
        text_result_for_llm=canonical(
            {
                "error": error.code,
                "message": message,
                "details": details,
            }
        ).decode(),
        result_type="failure",
    )


def result_text(name: str, result: dict[str, Any]) -> str:
    encoded = canonical(result)
    if name != "checkpoint_sources" or len(encoded) <= 16_384:
        return encoded.decode()

    # Keep the operation identity and closure digest ahead of either potentially huge array.
    keys = (
        "schema_version",
        "operation_id",
        "source_commit",
        "project_id",
        "state",
        "parent_commit",
        "replayed",
    )
    header = {key: result[key] for key in keys}
    closure = result.get("source_closure")
    if isinstance(closure, dict):
        ordered_closure = {
            key: closure[key]
            for key in ("digest", "entry_path", "dependencies", "required_runtime", "files")
        }
        if len(canonical({**header, "source_closure": {"digest": closure["digest"]}})) > 512:
            # Maximum-length IDs and SHA-256 commits cannot all fit before the digest.
            header = {
                **{key: result[key] for key in keys[:3]},
                "source_closure": ordered_closure,
                **{key: result[key] for key in keys[3:]},
            }
        else:
            header["source_closure"] = ordered_closure
    elif "source_closure" in result:
        header["source_closure"] = closure
    header["files"] = result["files"]
    return json.dumps(header, ensure_ascii=False, separators=(",", ":"))


def tool_schema(name: str) -> dict[str, Any]:
    """Project a normative definition into self-contained model parameters."""
    return inline_tool_schema(SCHEMA["$defs"][name])


def _matches_invocation(record: dict[str, Any], invocation: ToolInvocation, name: str) -> bool:
    if (
        record.get("agent_id") != invocation.agent_id
        or record.get("session_id") != invocation.session_id
        or record.get("name") != name
        or invocation.tool_name != name
        or not isinstance(invocation.arguments, dict)
    ):
        return False
    try:
        args_sha = hashlib.sha256(canonical(invocation.arguments)).hexdigest()
    except (ValueError, TypeError):
        return False
    return record.get("arguments_sha256") == args_sha


class Tools:
    def __init__(self, services: Services, state: State, project_id: str):
        self.services, self.state, self.project_id = services, state, project_id

    def caller(self, invocation: ToolInvocation, *, name: str, admit: bool = True) -> Caller:
        with self.services.registry.project_store(self.project_id).transaction(write=False) as tx:
            record = tx.get(
                "invocations",
                key(invocation.session_id, "tool", invocation.tool_call_id),
                required=False,
            )
            if record is None or not _matches_invocation(record, invocation, name):
                raise DomainError("attribution_unavailable", 409)
            work = tx.require("work", record["work_id"])
            if admit and (
                record["owner_epoch"] != self.services.ownership.epoch
                or record["state"] not in {"admitted", "running"}
                or work["state"] not in {"running", "awaiting_user"}
                or self.state.control(tx)["fenced"]
            ):
                raise DomainError("permission_denied", 403)
        if admit:
            self.services.gate.require(self.project_id)
        return Caller(
            "tool",
            invocation.tool_call_id,
            self.project_id,
            record["work_id"],
            record["owner_epoch"],
            invocation_id=record["invocation_id"],
            operations=tuple(item[4] for item in TOOLS.values()),
        )

    def _read(
        self, name: str, invocation: ToolInvocation, caller: Caller | None = None
    ) -> tuple[dict[str, Any], str]:
        args = read_arguments(name, invocation.arguments)
        if name == "get_project_capabilities":
            from .capabilities import get_project_capabilities

            if args.get("project_id", self.project_id) != self.project_id:
                raise DomainError("scope_denied", 403)
            return get_project_capabilities(self.services, self.project_id), "ProjectCapabilities"
        if name == "list_items":
            from proscenium.sources.common import fingerprint

            query = args.get("query", "").casefold().strip()
            catalogue = self.services.extension("catalogue")
            with self.services.registry.project_store(self.project_id).transaction(
                write=False
            ) as tx:
                rows = [catalogue.item_view(tx, item) for item in tx.all("items")]

            def searchable(item):
                provenance = item.get("provenance") or {}
                return " ".join(
                    str(value)
                    for value in (
                        item.get("name", ""),
                        item.get("description", ""),
                        *item.get("tags", []),
                        *(
                            provenance.get(field) or ""
                            for field in ("source_url", "creator", "licence", "note")
                        ),
                    )
                ).casefold()

            for item in rows:
                validate("ItemRecord", item)
                if item["project_id"] != self.project_id:
                    raise DomainError("scope_denied", 403)
            result = self.services.cursors.page(
                f"{self.project_id}:native-items:{fingerprint(query)}",
                [item for item in rows if query in searchable(item)],
                "item_id",
                cursor=args.get("cursor"),
                limit=args.get("limit", 50),
            )
            return result, "ItemPage"
        identity, result_schema, _ = READ_TOOLS[name]
        assert identity is not None
        if name == "get_job":
            result = self.services.jobs.get(self.project_id, args[identity])
        elif name == "get_vlm_review":
            if caller is None or not caller.work_id:
                raise DomainError("scope_denied", 403)
            reviews = self.services.extension("reviews")
            with self.services.registry.project_store(self.project_id).transaction(
                write=False
            ) as tx:
                result = reviews.detail(tx, args[identity])
                job = tx.require("jobs", result["ticket"]["job_id"])
                if job.get("work_id") != caller.work_id:
                    raise DomainError("scope_denied", 403)
            if result.get("project_id") != self.project_id:
                raise DomainError("scope_denied", 403)
            if result["ticket"].get("review_id") != args[identity]:
                raise DomainError("source_conflict", 409)
            return result, result_schema
        elif name == "get_artifact":
            result = self.services.extension("artifacts").get(self.project_id, args[identity])
        elif name == "get_version":
            result = self.services.extension("catalogue").version(self.project_id, args[identity])
        else:
            previews = self.services.extension("previews")
            with self.services.registry.project_store(self.project_id).transaction(
                write=False
            ) as tx:
                job = tx.require("jobs", args[identity])
                rows = previews.records(tx, job, "frames")
            # Same numeric ordering and signed project/job/filter scope as the HTTP read.
            keyed = [{"order": f"{row['source_frame']:012d}", "record": row} for row in rows]
            result = self.services.cursors.page(
                f"{self.project_id}:{args[identity]}:frames:None:None",
                keyed,
                "order",
                cursor=args.get("cursor"),
                limit=args.get("limit", 50),
            )
            result["items"] = [row["record"] for row in result["items"]]
            if any(
                row["project_id"] != self.project_id or row["job_id"] != args[identity]
                for row in result["items"]
            ):
                raise DomainError("scope_denied", 403)
        if name != "list_job_frames":
            if result.get("project_id") != self.project_id:
                raise DomainError("scope_denied", 403)
            if result.get(identity) != args[identity]:
                raise DomainError("source_conflict", 409)
        return result, result_schema

    async def invoke(self, name: str, invocation: ToolInvocation) -> ToolResult:
        try:
            # Accepted W02 services check completed-operation identity before admitting
            # a new effect. Preserve the ORIGINAL invocation/work/epoch for that replay.
            caller = self.caller(
                invocation,
                name=name,
                admit=name
                not in {
                    "checkpoint_sources",
                    "publish_outputs",
                    "register_artifact",
                    "set_preview_order",
                    *PREPARATION_TOOLS,
                    "validate_authored_document",
                    *READ_TOOLS,
                    *(set(DISCOVERY_READS) - {"search_web"}),
                    *WORKFLOW_TOOLS,
                },
            )
            if name == "search_web":
                result = await search_web(
                    self.services, self.project_id, **read_arguments(name, invocation.arguments)
                )
                result_schema = DISCOVERY_READS[name][1]
            elif name in READ_TOOLS or name in DISCOVERY_READS:
                result, result_schema = self._read(name, invocation, caller)
                if (
                    name in {"get_job", "get_vlm_review"}
                    and invocation.arguments.get("wait_seconds", 0) > 0
                ):
                    assert caller.work_id is not None
                    deadline = time.monotonic() + invocation.arguments["wait_seconds"]

                    def settled(payload: dict[str, Any]) -> bool:
                        if name == "get_job":
                            return payload["state"] in {
                                "succeeded",
                                "failed",
                                "cancelled",
                                "outcome_uncertain",
                            }
                        ticket = payload["ticket"]
                        return ticket["status"] in {"ready", "failed"} or (
                            ticket.get("error_code") == "outcome_uncertain"
                        )

                    while not settled(result):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        try:
                            self.services.gate.require(self.project_id)
                        except DomainError:
                            break
                        with self.services.registry.project_store(self.project_id).transaction(
                            write=False
                        ) as tx:
                            work = tx.require("work", caller.work_id)
                            held = self.state.control(tx)["fenced"] or work["state"] in {
                                "stop_requested",
                                "stopped",
                                "awaiting_resume",
                                "outcome_uncertain",
                            }
                        if held:
                            break
                        await asyncio.sleep(min(0.5, remaining))
                        result, result_schema = self._read(name, invocation, caller)
            elif name == "validate_authored_document":
                from .capabilities import validate_authored_document

                result = validate_authored_document(
                    self.services, self.project_id, invocation.arguments
                )
                result_schema = "AuthoredDocumentValidationResult"
            elif name in WORKFLOW_TOOLS:
                method, result_schema, _ = WORKFLOW_TOOLS[name]
                body = workflow_arguments(name, invocation.arguments)
                service = self.services.extension("workflows")
                if name != "get_unity_result" and not await service.replayable(
                    caller, body, method
                ):
                    # Preserve W01's native fence as well as F00's gate. Only a proved
                    # existing effect can be read/reconciled after a callback is held.
                    self.caller(invocation, name=name, admit=True)
                result = await getattr(service, method)(caller, body)
            else:
                extension, method, schema, result_schema, _ = TOOLS[name]
                try:
                    validate(schema, invocation.arguments)
                except DomainError as exc:
                    if exc.code == "invalid_request" and not exc.details.get("issues"):
                        issues = schema_issues(schema, invocation.arguments)
                        if issues:
                            raise DomainError(
                                exc.code, exc.status, exc.message, issues=issues
                            ) from exc
                    raise
                body = copy.deepcopy(invocation.arguments)
                if body.get("project_id", self.project_id) != self.project_id:
                    raise DomainError("scope_denied", 403)
                body.setdefault("project_id", self.project_id)
                if name in PREPARATION_TOOLS and "generation_task_id" not in body:
                    assert caller.work_id is not None
                    with self.services.registry.project_store(self.project_id).transaction(
                        write=False
                    ) as tx:
                        body["generation_task_id"] = tx.require("work", caller.work_id)[
                            "generation_task_id"
                        ]
                service = (
                    self.services.jobs
                    if extension == "jobs"
                    else self.services.extension(extension)
                )
                function = getattr(service, method, None)
                if function is None:
                    raise DomainError("capability_unavailable", 501)
                if name in PREPARATION_TOOLS and not self.services.extension(extension).replayable(
                    caller, body
                ):
                    self.caller(invocation, name=name, admit=True)
                result = function(caller, body)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, tuple):
                    result = result[0]
            # ReviewTicket validation forbids findings in the model response.
            validate(result_schema, result)
            if name == "get_artifact" and invocation.arguments.get("include_image") is True:
                return self._inline_image(result)
            outcome: dict[str, Any] = {}
            if name == "register_artifact":
                outcome["artifact_ids"] = [result["artifact_id"]]
            elif name in PREPARATION_TOOLS:
                outcome["artifact_ids"] = [
                    result["workflow_artifact_id"],
                    result["prompt_artifact_id"],
                ]
            elif name == "publish_outputs":
                outcome.update(
                    publication_id=result["publication_id"],
                    published_versions=result["versions"],
                    job_ids=result["job_ids"],
                    artifact_ids=[
                        aid for item in body["items"] for aid in item.get("artifact_ids", [])
                    ],
                )
            elif name == "set_preview_order":
                outcome.update(job_ids=list(body["job_ids"]))
            elif name in {
                "submit_generation",
                "request_preview",
                "assemble_sequence",
                "run_unity_workflow",
                "retry_job",
            }:
                outcome.update(
                    job_ids=[result["job_id"]], artifact_ids=result.get("artifact_ids", [])
                )
            if outcome:
                self._outcome(invocation, name, status="succeeded", **outcome)
            return ToolResult(text_result_for_llm=result_text(name, result))
        except DomainError as exc:
            failure = tool_failure(exc, name)
            diagnostic = json.loads(failure.text_result_for_llm)
            issues = diagnostic["details"].get("issues", [])
            self._outcome(
                invocation,
                name,
                status="failed",
                error_code=exc.code,
                phase=issues[0]["phase"]
                if issues
                else "json"
                if exc.message
                in {
                    "Storyboard source must be AuthoredShotPlan JSON",
                    "Authored catalogue source is not JSON",
                }
                else "preflight",
                safe_summary=diagnostic["message"],
                issues=issues,
            )
            return failure
        except Exception:
            # Never give SDK callers an upstream exception repr, headers or credentials.
            # Cancellation still propagates, and existing durable effects are not retried.
            return tool_failure(DomainError("internal_error", 500), name)

    def _inline_image(self, result: dict[str, Any]) -> ToolResult:
        if result["state"] != "ready":
            raise DomainError(
                "media_unavailable", 422, "Image artefact is not ready for inline inspection"
            )
        if result["kind"] != "image" or result["media_type"] not in {
            "image/png",
            "image/jpeg",
            "image/webp",
        }:
            raise DomainError(
                "invalid_request",
                message="Inline image inspection supports ready PNG, JPEG or WebP artefacts only",
            )
        dimensions = result.get("dimensions")
        maximum = self.services.settings.upload_max_bytes
        if result["bytes"] > maximum or (
            dimensions
            and dimensions["width"] * dimensions["height"]
            > self.services.settings.upload_max_pixels
        ):
            raise DomainError(
                "invalid_request", message="Image artefact exceeds inline inspection bounds"
            )
        path = self.services.extension("artifacts").ready_path(
            self.project_id, result["artifact_id"]
        )
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
        if len(raw) != result["bytes"] or hashlib.sha256(raw).hexdigest() != result["sha256"]:
            raise DomainError("hash_mismatch", 422)
        return ToolResult(
            text_result_for_llm=canonical(result).decode(),
            binary_results_for_llm=[
                ToolBinaryResult(
                    type="image",
                    mime_type=result["media_type"],
                    data=base64.b64encode(raw).decode("ascii"),
                    description=result["artifact_id"],
                )
            ],
        )

    def _outcome(self, invocation: ToolInvocation, name: str, **values: Any) -> None:
        store = self.services.registry.project_store(self.project_id)
        with store.transaction() as tx:
            record = tx.get(
                "invocations",
                key(invocation.session_id, "tool", invocation.tool_call_id),
                required=False,
            )
            if record is None or not _matches_invocation(record, invocation, name):
                return
            previous = get_tool_outcome(tx, record["invocation_id"])
            if previous and values.get("status") == "failed":
                # A rejected retry cannot erase artefacts already proven by this invocation.
                for field in ("artifact_ids", "job_ids", "publication_id", "published_versions"):
                    values[field] = previous[field]
            record_tool_outcome(
                tx,
                invocation_id=record["invocation_id"],
                session_id=record["session_id"],
                project_id=self.project_id,
                work_id=record["work_id"],
                request_id=record["request_id"],
                tool_name=name,
                **values,
            )

    def definitions(self) -> list[Tool]:
        tools = []
        for name, (_, _, schema, _, _) in TOOLS.items():

            async def handler(invocation: ToolInvocation, name: str = name) -> ToolResult:
                return await self.invoke(name, invocation)

            parameters = tool_schema(schema)
            tools.append(
                Tool(
                    name=name,
                    description=f"Proscenium {name}. Active project only. Returns a durable receipt, not fabricated completion.",
                    parameters=parameters,
                    handler=handler,
                )
            )

        for name, (_, _, description) in {**READ_TOOLS, **DISCOVERY_READS}.items():

            async def read_handler(invocation: ToolInvocation, name: str = name) -> ToolResult:
                return await self.invoke(name, invocation)

            tools.append(
                Tool(
                    name=name,
                    description=description,
                    parameters=read_parameters(name),
                    handler=read_handler,
                )
            )
        for name, (_, _, description) in WORKFLOW_TOOLS.items():

            async def workflow_handler(invocation: ToolInvocation, name: str = name) -> ToolResult:
                return await self.invoke(name, invocation)

            parameters = workflow_parameters(name)
            if "execution_profile_id" in parameters["properties"]:
                profiles = sorted(self.services.settings.execution_profiles)
                previews = self.services.extensions.get("previews")
                if name == "assemble_sequence" and previews is not None:
                    profiles = [value for value in profiles if value == previews.config.profile_id]
                if profiles:
                    parameters["properties"]["execution_profile_id"]["enum"] = profiles
            tools.append(
                Tool(
                    name=name,
                    description=description,
                    parameters=parameters,
                    handler=workflow_handler,
                )
            )
        return tools
