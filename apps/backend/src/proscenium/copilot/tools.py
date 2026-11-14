"""Model tools call accepted host services with persisted native invocation authority."""

from __future__ import annotations

import copy
import inspect
from typing import Any

from copilot.tools import Tool, ToolInvocation, ToolResult

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import SCHEMA, canonical, validate
from proscenium.services import Services

from .state import State, key

# Extension, method, normative input, normative result, capability operation.
TOOLS = {
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
    "admit_job": ("jobs", "admit", "JobAdmission", "JobRecord", "admitToolJob"),
    "retry_job": ("jobs", "retry", "JobRetry", "JobRecord", "retryToolJob"),
    "update_job_state": ("jobs", "update", "JobStateUpdate", "JobRecord", "updateJobState"),
}


def tool_schema(name: str) -> dict[str, Any]:
    """The normative definition and its transitive local refs, not 119 defs per tool."""
    definitions: dict[str, Any] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference:
                prefix = "#/$defs/"
                if not reference.startswith(prefix):
                    raise ValueError("Unsupported non-local normative tool reference")
                dependency = reference[len(prefix) :]
                if dependency not in definitions:
                    definitions[dependency] = copy.deepcopy(SCHEMA["$defs"][dependency])
                    visit(definitions[dependency])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    root = copy.deepcopy(SCHEMA["$defs"][name])
    visit(root)
    if definitions:
        root["$defs"] = definitions
    return root


class Tools:
    def __init__(self, services: Services, state: State, project_id: str):
        self.services, self.state, self.project_id = services, state, project_id

    def caller(self, invocation: ToolInvocation, *, admit: bool = True) -> Caller:
        with self.services.registry.project_store(self.project_id).transaction(write=False) as tx:
            record = tx.require(
                "invocations", key(invocation.session_id, "tool", invocation.tool_call_id)
            )
            if (
                record["agent_id"] != invocation.agent_id
                or record["session_id"] != invocation.session_id
            ):
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

    async def invoke(self, name: str, invocation: ToolInvocation) -> ToolResult:
        try:
            # Accepted W02 services check completed-operation identity before admitting
            # a new effect. Preserve the ORIGINAL invocation/work/epoch for that replay.
            caller = self.caller(
                invocation,
                admit=name
                not in {"checkpoint_sources", "publish_outputs", "register_artifact", "get_job"},
            )
            if name == "get_job":
                args = invocation.arguments
                if set(args) != {"job_id"}:
                    raise DomainError("invalid_request")
                result = self.services.jobs.get(self.project_id, args["job_id"])
                result_schema = "JobRecord"
            else:
                extension, method, schema, result_schema, _ = TOOLS[name]
                body = validate(schema, invocation.arguments)
                if body.get("project_id") != self.project_id:
                    raise DomainError("scope_denied", 403)
                service = (
                    self.services.jobs
                    if extension == "jobs"
                    else self.services.extension(extension)
                )
                function = getattr(service, method, None)
                if function is None:
                    raise DomainError("capability_unavailable", 501)
                result = function(caller, body)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, tuple):
                    result = result[0]
            # ReviewTicket validation forbids findings in the model response.
            validate(result_schema, result)
            return ToolResult(text_result_for_llm=canonical(result).decode())
        except DomainError as exc:
            return ToolResult(
                text_result_for_llm=canonical({"error": exc.code}).decode(), result_type="failure"
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

        async def get_job(invocation: ToolInvocation) -> ToolResult:
            return await self.invoke("get_job", invocation)

        tools.append(
            Tool(
                name="get_job",
                description="Read an existing job without starting or retrying it.",
                parameters={
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
                handler=get_job,
            )
        )
        return tools
