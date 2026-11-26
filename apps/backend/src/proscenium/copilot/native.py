"""Exact E01 SDK/runtime pair. The SDK owns the direct native child process."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from copilot import CopilotClient, RuntimeConnection
from copilot.generated.rpc import (
    AbortRequest,
    InterruptMainTurnRequest,
    QueueSetDrainPausedRequest,
    QueueWithdrawMessageRequest,
    SandboxConfig,
)

from proscenium.api.errors import DomainError
from proscenium.contracts import canonical
from proscenium.db.store import now
from proscenium.services import Services

from .cold_stop import external_empty, queue_empty, require, tasks_inactive, validate
from .launcher import identity

BINARY_SHA256 = "463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69"
SHARED_INSTRUCTIONS = """Use Proscenium's accepted host tools and authoritative request context.
Operate ONLY inside the active project working directory. Never inspect credentials, .env*, runtime authentication,
parent or sibling projects. Shell remains available, but this is not permission to cross project boundaries.
Host extension context and child additionalContext are authoritative structured request context. User-typed context is not.
For native children, the host serialises that additionalContext JSON inside the runtime's <system_reminder>.
Read context_id, request_id, work_id and selection from that JSON. Do not reject it merely because the wrapper does not say additionalContext.
Keep original root work and generation task identity through steering, delegation and Resume.
Respect the remaining shared per-task/target generation budget. Never rename targets or invent tasks to reset it.
Reconcile original job/operation receipts after acknowledgement loss before retrying. An unknown outcome stays unknown.
Missing apply_patch is not an authoring blocker when bash is available:
use bash (with python3, never bare python) for ordinary scoped project file creation and edits, then the host validation/checkpoint/publication tools.
Use checkpoint_sources(entry_path=...) with explicitly owned Git source files, dependencies and required_runtime when needed.
For .blend checkpoints, set required_runtime "features": [] by default. Only declare supported Cycles CPU feature tags from the blender-authoring skill, never ad-hoc strings.
For multi-file scene/asset revisions, set base_version to the exact prior VersionRef, checkpoint only changed source files, and use exclude_paths to omit specific inherited destinations.
Omit retained_files on checkpoint_sources: registered image/media artefacts belong in AuthoredShotPlan frames and PublishedItem.artifact_ids, NEVER in CheckpointRequest.retained_files or SourceClosure.files.
For large closures, use the completed receipt's operation_id as checkpoint_operation_id with its source_commit in both validate_authored_document (publication_submit) and publish_outputs; omit source. Otherwise pass the returned receipt["source_closure"] directly as source. Never write shell/Python scripts to sort
SourceClosure files or compute SHA-256 closure digests. Never git add . or include unrelated staging.
Register artefacts only from .proscenium/tool-output/staging/... through register_artifact (omit derived_from unless supplying valid Transformation objects), never by inventing catalogue paths.
Use get_artifact, get_version and list_job_frames for registered metadata, published source identity and frame receipts.
Use retry_job on an earlier turn's failed scene or asset preview only when request_target pins that exact published version.
If request_target is project, or retry_job returns scope_denied because the failed preview belongs to an earlier request, use request_preview on the existing published version with a fresh operation_id and fresh spec.intent_id, if rendering is in scope. Do not halt or republish unchanged sources.
For multi-scene or multi-shot previews, read get_version(version_id) and use set_preview_order on the published version to set pass order without re-rendering.
Use list_items to discover catalogue names, descriptions, tags and known provenance without private SQLite.
Lead and authoring-specialist: use search_web for open-ended public web discovery and web_fetch to inspect selected public URLs.
Keep queries free of private project content and credentials. Include the exact source URL (url) with findings.
Mark URL leads as Discovered, not Acquired. Only verified local bytes are Acquired. Record the exact Provenance.source_url when acquiring or publishing references.
Use run_unity_workflow, then get_job/get_unity_result. Never shell-launch untracked Editors.
Use assemble_sequence for an exact already-published sequence (for standalone scene renders with no storyboard shot, set SequenceEntry.shot_id to null to match job.target.shot_id). The host resolves frame receipts and computes admission hashes.
Never query private SQLite to recover tool results or guess filesystem-latest.
Inspect registered images with get_artifact(artifact_id=..., include_image=true). NEVER bash cp
.proscenium/artefacts/.../content just to call view. The host verifies ready bytes and their hash inline without copying.
Wait for admitted jobs with get_job(job_id=..., wait_seconds=15), not rapid polling loops. Waiting never submits work.
Use submit_generation for budgeted inference, request_vlm_review for read-only assessment, and get_vlm_review(review_id=..., wait_seconds=15) to read the validated findings for your current work.
Include a short human-readable title in request_vlm_review, write concise visual criteria without policy boilerplate, and pass reference/start comparison frames alongside candidate targets when checking continuity.
You may use your active work's get_vlm_review findings for bounded self-correction within the brief and remaining task budget, or call escalate_vlm_review with a concrete question when you need human creative judgement in Critic.
When producing storyboards, keyframes, concept images or renders and get_project_capabilities reports a configured
or ready vlm provider, call request_vlm_review for read-only visual assessment. Only owner-approved findings in host context carry owner instruction authority, creative selection or budget extension.
Routine in-scope creative stages progress automatically. Request exact owner permission for software/service installation,
new charges, remote push/sharing or destructive retained-source operations. Do not treat tool permissions as creative selection.
Do not install, start or stop independent model services. Preserve unsaved Blender/Unity work. One live scene writer and Git index owner.
Never call bpy.ops.wm.read_factory_settings or disable add-ons in blender-execute_blender_code. A factory reset unloads blender_mcp and closes 127.0.0.1:9876. Clear authorised live scene data in place via bpy.data.
After compaction re-read the project brief and current plan. Report unavailable capabilities rather than inventing outputs.
Honour request_target, reference_versions and execution_scope in extension_context and child additionalContext.
planning_only forbids image and audio generation, preview rendering and scene execution. Use empty publication preview_requests.
Call get_project_capabilities first for tool, skill and provider discovery. Never probe python/proscenium imports or private runtime files.
Use validate_authored_document for storyboards, sequences and publications before checkpoint_sources and publish_outputs.
Storyboard source.entry_path MUST be canonical AuthoredShotPlan JSON at storyboards/<plan_id>/plan.json, never HTML, SVG or Markdown.
Use prepare_image_generation then submit_generation with the returned exact submit_request. Never silently substitute vector/SVG drawings for inference.
For music or sound effects, load audio-generation and call prepare_audio_generation then submit_generation with the exact returned submit_request.
For both preparation tools omit project_id and generation_task_id: the host binds them automatically.
For a soundtrack, set SequenceMap.silent to false and add audio_tracks referencing ready audio artefacts. Use silent: true with no tracks for a silent export.
Omit width, height and steps to use get_project_capabilities default_dimensions/default_steps unless the user explicitly
requested valid multiple-of-16 dimensions within the recipe max_pixels. Never guess recipe settings.
Relative workspace links such as storyboards/.../index.html are not browser URLs. Report retained artefact identities instead.
"""

LEAD_INSTRUCTIONS = (
    """You are Proscenium's lead authoring agent. Coordinate bounded native specialists via the task tool.
Delegate multi-step authoring, storyboard creation, image/mesh/audio generation waves and Blender scene builds to
authoring-specialist, and Unity workflows to unity-specialist. Do not run long authoring or generation loops inline
in the root turn. Give each specialist an explicit slice, pinned inputs, expected outputs and remaining budget.
Keep one live scene writer and one Git index owner. Reconcile specialist receipts before progressing dependencies.
Once a synchronous task specialist has completed, delegate follow-up work with a new task call rather than write_agent.
When pitching or refining a storyboard or hero-assets breakdown in Conversation, relay the full pitch/shortlist or single-item revision card to the owner and never submit image generation without explicit generation instruction.
"""
    + SHARED_INSTRUCTIONS
)

SPECIALIST_INSTRUCTIONS = (
    """You are a bounded authoring specialist invoked by the lead.
Complete your assigned slice directly with exposed tools without unnecessary nested subagent delegation.
Load the matching domain skill first (storyboard, hero-assets, image-generation, image-to-3d, audio-generation, blender-authoring,
sequence-assembly, research or unity) when "skill" is in exposed_tools. Report missing capabilities instead of inventing outputs.
"""
    + SHARED_INSTRUCTIONS
)

UNITY_INSTRUCTIONS = (
    SPECIALIST_INSTRUCTIONS
    + """
You are unity-specialist. Complete the assigned pinned-source Unity workflow directly through run_unity_workflow.
Wait with get_job(job_id=..., wait_seconds=15), then read get_unity_result for observed results.
Playable walkthroughs need the Escape Pause Screen (Resume, Reset Position, Quit). Pair unlocked with Cursor.visible = true
on pause, focus loss and teardown, and locked with Cursor.visible = false only while focused and actively playing.
Use Linux-compatible VP8/VP9 .webm and explicit audio routing for video. Verify ColorSpace.Linear in owned URP projects
and respect the OpenGL Core Forward+ 32-light cap. Preserve user-owned settings unless explicitly authorised.
Before claiming a verified walkthrough, observe zero-error live Play Mode and complete VLM review against Blender references.
Scope live CLI commands with --project-path. Close only the active inspection Editor before batch validate/build.
Report validation.status as observed. Report checks you could not run separately.
"""
)

# Backward import compatibility. Root and specialist prompts deliberately differ.
INSTRUCTIONS = LEAD_INSTRUCTIONS


@dataclass(frozen=True)
class Configuration:
    cli_path: Path
    lead_model: str
    worker_model: str
    skill_directories: list[str]
    mcp_servers: dict[str, Any]
    max_continues: int
    worker_tools: list[str]
    unity_worker_model: str | None = None
    unity_editor: Path | None = None

    @classmethod
    def load(cls, settings: dict[str, Any]) -> Configuration:
        allowed = {
            "cli_path",
            "lead_model",
            "worker_model",
            "skill_directories",
            "mcp_servers",
            "max_continues",
            "worker_tools",
            "profile_id",
            "unity_worker_model",
            "unity_editor",
        }
        if set(settings) - allowed:
            raise ValueError("Unknown private Copilot runtime setting")
        default = (
            Path(__file__).resolve().parents[5] / "node_modules/@github/copilot-linux-x64/copilot"
        )
        cli = Path(settings.get("cli_path", default)).expanduser()
        if not cli.is_absolute() or cli.name != "copilot":
            raise ValueError("cli_path must be the absolute direct native binary")
        worker = settings.get("worker_model")
        if not isinstance(worker, str) or not worker:
            raise ValueError("worker_model must explicitly select an available required model")
        lead = settings.get("lead_model", "gpt-6.1-sol")
        if not isinstance(lead, str) or not lead:
            raise ValueError("lead_model must name an explicit model")
        continues = settings.get("max_continues", 5)
        if not isinstance(continues, int) or continues < 5:
            raise ValueError("Autopilot max_continues must be at least five")
        directories = settings.get(
            "skill_directories", [str(Path(__file__).resolve().parents[5] / "agent/skills")]
        )
        if not isinstance(directories, list) or not all(
            isinstance(p, str) and Path(p).is_absolute() for p in directories
        ):
            raise ValueError("skill_directories must contain absolute adapted skill roots")
        tools = settings.get(
            "worker_tools",
            [
                "view",
                "bash",
                "skill",
                "get_project_capabilities",
                "search_web",
                "web_fetch",
                "validate_authored_document",
                "prepare_image_generation",
                "prepare_audio_generation",
                "checkpoint_sources",
                "publish_outputs",
                "request_preview",
                "set_preview_order",
                "register_artifact",
                "submit_generation",
                "request_vlm_review",
                "get_vlm_review",
                "escalate_vlm_review",
                "get_job",
                "get_artifact",
                "get_version",
                "list_job_frames",
                "list_items",
                "assemble_sequence",
                "run_unity_workflow",
                "get_unity_result",
            ],
        )
        if not isinstance(tools, list) or not tools or not all(isinstance(t, str) for t in tools):
            raise ValueError("worker_tools must explicitly list native/model-facing tool names")
        mcps = settings.get("mcp_servers", {})
        if not isinstance(mcps, dict):
            raise ValueError("mcp_servers must be an SDK MCP configuration object")
        for name, server in mcps.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(server, dict):
                raise ValueError("MCP server names and configuration objects are required")
            if "headers" in server and (
                not isinstance(server["headers"], dict)
                or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in server["headers"].items()
                )
            ):
                raise ValueError("MCP headers must contain string names and values")
            if any(k.lower() == "authorization" for k in server.get("headers", {})):
                raise ValueError("Inline Authorization headers are forbidden; use bearer_token_env")
            if "bearer_token_env" in server:
                env_name = server["bearer_token_env"]
                if (
                    server.get("type") not in {"http", "sse"}
                    or not isinstance(env_name, str)
                    or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env_name)
                ):
                    raise ValueError(
                        "bearer_token_env requires HTTP/SSE and a valid environment name"
                    )
        unity_worker = settings.get("unity_worker_model", worker)
        if not isinstance(unity_worker, str) or not unity_worker.strip():
            raise ValueError(
                "unity_worker_model must explicitly select an available required model"
            )
        editor = settings.get("unity_editor")
        if editor is not None and (
            not isinstance(editor, str) or not editor.strip() or not Path(editor).is_absolute()
        ):
            raise ValueError("unity_editor must be a configured absolute executable path")
        return cls(
            cli,
            lead,
            worker,
            directories,
            mcps,
            continues,
            tools,
            unity_worker,
            Path(editor) if editor is not None else None,
        )


def resolve_mcp_servers(
    mcp_servers: dict[str, Any], lookup: Callable[[str], str | None]
) -> dict[str, Any]:
    """Resolve optional authentication only on the private wire copy."""
    resolved = deepcopy(mcp_servers)
    for name, server in list(resolved.items()):
        if "bearer_token_env" in server:
            token = lookup(server.pop("bearer_token_env"))
            if not token:
                del resolved[name]
                continue
            server["headers"] = {
                **(server.get("headers") or {}),
                "Authorization": f"Bearer {token}",
            }
    return resolved


def is_blender_mcp_configured(mcp_servers: dict[str, Any]) -> bool:
    markers = ("mcp-for-blender", "blender_mcp", "blender-mcp")
    for name, server in mcp_servers.items():
        if name.lower() in {"blender", "blender-mcp", "mcp-for-blender"}:
            return True
        if server.get("type", "stdio") not in {"stdio", "local"}:
            continue
        args = server.get("args", [])
        strings = [server.get("command", ""), *([args] if isinstance(args, str) else args)]
        if any(
            isinstance(value, str) and any(marker in value.lower() for marker in markers)
            for value in strings
        ):
            return True
    return False


def is_blendswap_mcp_configured(mcp_servers: dict[str, Any]) -> bool:
    for name, server in mcp_servers.items():
        if name.lower() == "blendswap":
            return True
        if server.get("type") not in {"http", "sse"}:
            continue
        host = urlsplit(server.get("url", "")).hostname or ""
        if host == "blendswap.com" or host.endswith(".blendswap.com"):
            return True
    return False


UNITY_TOOLS = [
    "get_project_capabilities",
    "validate_authored_document",
    "view",
    "bash",
    "run_unity_workflow",
    "get_unity_result",
    "request_vlm_review",
    "get_vlm_review",
    "escalate_vlm_review",
    "get_job",
    "get_version",
    "get_artifact",
    "list_items",
    "list_job_frames",
    "checkpoint_sources",
    "publish_outputs",
    "register_artifact",
]


def unity_agent(config: Configuration, *, model: str | None = None) -> dict[str, Any]:
    skill = Path(__file__).resolve().parents[5] / "agent/skills/unity/SKILL.md"
    return dict(
        name="unity-specialist",
        description="Scoped pinned-source Unity scene import, controls and validation",
        model=model or config.unity_worker_model or config.worker_model,
        model_policy="required",
        tools=list(UNITY_TOOLS),
        prompt=UNITY_INSTRUCTIONS + "\n" + skill.read_text(),
    )


class Native:
    def __init__(self, services: Services):
        self.services = services
        self.config = Configuration.load(services.settings.runtime)
        self.client: Any = None
        self.departed_process: dict[str, Any] | None = None
        self.non_continuing_session_id: str | None = None
        self.latest_cold_stop_snapshot: dict[str, Any] = {}

    def reconcile_process(self) -> None:
        record_path = self.services.settings.data_directory / "copilot-runtime/process.json"
        if not record_path.exists():
            return
        record = json.loads(record_path.read_text())
        pid = record["pid"]
        if self.observe_departure(record):
            return
        # Refuse to attach over an original live backend. OS ownership should also prevent this.
        if identity(record["parent_pid"]) == record["parent_start"]:
            raise DomainError("owner_conflict", 409)
        fd = os.pidfd_open(pid)
        try:
            if identity(pid) != record["start"]:
                return
            if Path(f"/proc/{pid}/exe").resolve() != Path(record["binary"]):
                raise DomainError("owner_conflict", 409)
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        finally:
            os.close(fd)
        if identity(pid) is not None:
            raise DomainError("owner_conflict", 409, "Original runtime exit not yet observed")

    def observe_departure(self, record: dict[str, Any]) -> bool:
        observed = identity(record["pid"])
        if observed == record["start"]:
            return False
        self.departed_process = {
            "pid": record["pid"],
            "start": record["start"],
            "observed_identity": observed,
            "parent_pid": record["parent_pid"],
            "parent_start": record["parent_start"],
        }
        return True

    async def open(
        self,
        project_id: str,
        session_id: str | None,
        callbacks: dict[str, Any],
        *,
        models: tuple[str, str, str] | None = None,
    ) -> Any:
        config = self.config
        lead_model, worker_model, unity_worker_model = models or (
            config.lead_model,
            config.worker_model,
            config.unity_worker_model or config.worker_model,
        )
        if version("github-copilot-sdk") != "1.0.34":
            raise DomainError("capability_unavailable", 501)
        with config.cli_path.open("rb") as binary:
            if hashlib.file_digest(binary, "sha256").hexdigest() != BINARY_SHA256:
                raise DomainError(
                    "capability_unavailable",
                    501,
                    "Native binary differs from the accepted runtime lock",
                )
        runtime = self.services.settings.data_directory / "copilot-runtime"
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        allowed = {
            "HOME",
            "USER",
            "LOGNAME",
            "PATH",
            "LANG",
            "LC_ALL",
            "TERM",
            "SHELL",
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
        }
        environment = {k: v for k, v in os.environ.items() if k in allowed}
        environment.update(
            COPILOT_DISABLE_LOGIN_SHELL_ENV="1",
            COPILOT_PLUGIN_DIR_ONLY="true",
            COPILOT_CACHE_HOME=str(runtime / "cache"),
        )
        if self.client is None:
            self.reconcile_process()
            self.client = CopilotClient(
                connection=RuntimeConnection.for_stdio(
                    path=sys.executable,
                    args=[
                        str(Path(__file__).with_name("launcher.py")),
                        "--parent",
                        str(os.getpid()),
                        "--parent-start",
                        str(identity(os.getpid())),
                        "--record",
                        str(runtime / "process.json"),
                        str(config.cli_path),
                        "--no-auto-update",
                        "--disable-builtin-mcps",
                        "--no-custom-instructions",
                    ],
                ),
                env=environment,
                working_directory=str(runtime),
                base_directory=str(runtime / "home"),
                use_logged_in_user=True,
                log_level="error",
            )
            await asyncio.wait_for(self.client.start(), 30)
        project = Path(self.services.registry.get(project_id)["path"]).resolve()
        output = project / ".proscenium/tool-output"
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        settings = dict(
            model=lead_model,
            working_directory=str(project),
            memory={"enabled": False},
            enable_session_store=False,
            sandbox_config=SandboxConfig(enabled=False),
            infinite_sessions={"enabled": True},
            autopilot={"continuation_enabled": True, "max_continues": config.max_continues},
            large_output={"enabled": True, "output_directory": str(output)},
            skill_directories=config.skill_directories,
            enable_skills=True,
            mcp_servers=resolve_mcp_servers(config.mcp_servers, self.services.settings.secret_env),
            enable_config_discovery=False,
            skip_custom_instructions=True,
            streaming=True,
            include_sub_agent_streaming_events=True,
            system_message={"mode": "append", "content": LEAD_INSTRUCTIONS},
            custom_agents=[
                dict(
                    name="authoring-specialist",
                    description="Scoped Proscenium authoring specialist",
                    model=worker_model,
                    model_policy="required",
                    tools=config.worker_tools,
                    prompt=SPECIALIST_INSTRUCTIONS,
                ),
                unity_agent(config, model=unity_worker_model),
            ],
            **callbacks,
        )
        if session_id:
            if self.departed_process is not None:
                self.departed_process = {**self.departed_process, "session_id": session_id}
            try:
                resumed = await asyncio.wait_for(
                    self.client.resume_session(session_id, **settings, continue_pending_work=False),
                    30,
                )
                self.non_continuing_session_id = session_id
                return resumed
            except Exception as exc:
                if "not found" in str(exc).lower():
                    raise DomainError("native_session_missing", 503) from None
                raise
        self.non_continuing_session_id = None
        return await asyncio.wait_for(self.client.create_session(**settings), 30)

    async def _cold_rpc(
        self,
        session: Any,
        method: str,
        parameters: dict[str, Any],
        call: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Capture the real pinned transport request ID without logging params/prompts/auth."""
        client = session.rpc._client
        original_send = client._send_message
        ids: list[str] = []
        record: dict[str, Any] = {
            "observation_id": uuid4().hex,
            "session_id": session.session_id,
            "method": method,
            "parameters": parameters,
            "started_at": now(),
            "status": "dispatching",
        }
        self.latest_cold_stop_snapshot["rpc"].append(record)

        async def send(message: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            if (
                message.get("method") == method
                and message.get("params", {}).get("sessionId") == session.session_id
            ):
                require(
                    message.get("params") == {**parameters, "sessionId": session.session_id},
                    "unexpected_rpc_parameters",
                )
                ids.append(message["id"])
            return await original_send(message, *args, **kwargs)

        client._send_message = send
        try:
            result = await call()
            data = result.to_dict() if hasattr(result, "to_dict") else result
            summary: Any
            require(len(ids) == 1 and isinstance(ids[0], str), "unobserved_rpc_request_id")
            if method == "session.tasks.list" and isinstance(data, dict):
                summary = {
                    "tasks": [
                        {k: t[k] for k in ("id", "type", "status") if k in t}
                        for t in data.get("tasks", [])
                    ]
                }
            elif method == "session.tools.listPendingRequests" and isinstance(data, dict):
                summary = {
                    "items": [
                        {k: t[k] for k in ("requestId", "toolCallId", "toolName") if k in t}
                        for t in data.get("items", [])
                    ]
                }
            elif method == "session.queue.pendingItems" and not queue_empty(data):
                summary = {
                    "items_count": len(data.get("items", [])),
                    "steering_count": len(data.get("steeringMessages", [])),
                    "inFlightSteeringCount": data.get("inFlightSteeringCount"),
                }
            else:
                summary = data
            record.update(
                status="succeeded",
                rpc_request_id=ids[0],
                response=summary,
                response_sha256=hashlib.sha256(canonical(data)).hexdigest(),
                summary_sha256=hashlib.sha256(canonical(summary)).hexdigest(),
            )
            return data
        except Exception as exc:
            record.update(
                status="uncertain",
                error_type=type(exc).__qualname__,
                error_code=getattr(exc, "code", None),
                rpc_request_id=ids[0] if len(ids) == 1 else None,
            )
            raise
        finally:
            record["finished_at"] = now()
            client._send_message = original_send

    async def cold_stop_snapshot(self, session: Any, stable: Callable[[], bool]) -> dict[str, Any]:
        require(
            self.non_continuing_session_id == session.session_id, "not_non_continuing_cold_session"
        )
        self.latest_cold_stop_snapshot = {
            "session_id": session.session_id,
            "non_continuing": True,
            "started_monotonic": time.monotonic(),
            "started_at": now(),
            "rpc": [],
            "complete": False,
        }
        record_path = self.services.settings.data_directory / "copilot-runtime/process.json"
        if record_path.exists():
            process = json.loads(record_path.read_text())
            self.latest_cold_stop_snapshot["current_process"] = {
                k: process[k] for k in ("pid", "start", "parent_pid", "parent_start")
            }

        async def rpc(
            method: str, parameters: dict[str, Any], call: Callable[[], Awaitable[Any]]
        ) -> Any:
            require(stable(), "callback_or_owner_changed")
            value = await self._cold_rpc(session, method, parameters, call)
            require(stable(), "callback_or_owner_changed")
            return value

        async def pending() -> Any:
            result = await rpc(
                "session.queue.pendingItems", {}, lambda: session.rpc.queue.pending_items(timeout=5)
            )
            require(queue_empty(result), "queued_or_inflight_work")
            return result

        await pending()
        await rpc(
            "session.queue.setDrainPaused",
            {"paused": True},
            lambda: session.rpc.queue.set_drain_paused(
                QueueSetDrainPausedRequest(paused=True), timeout=5
            ),
        )
        await pending()
        tasks = await rpc("session.tasks.list", {}, lambda: session.rpc.tasks.list(timeout=5))
        require(tasks_inactive(tasks), "background_task_active_or_unknown")
        external = await rpc(
            "session.tools.listPendingRequests",
            {},
            lambda: session.rpc.tools.list_pending_requests(timeout=5),
        )
        require(external_empty(external), "external_tool_pending")
        await pending()
        aborted = await rpc(
            "session.abort", {}, lambda: session.rpc.abort(AbortRequest(), timeout=5)
        )
        require(
            isinstance(aborted, dict)
            and aborted.get("success") is True
            and not aborted.get("error"),
            "no_abort_ack",
        )
        await pending()
        interrupted = await rpc(
            "session.interruptMainTurn",
            {"flushQueued": False},
            lambda: session.rpc.interrupt_main_turn(
                InterruptMainTurnRequest(flush_queued=False), timeout=5
            ),
        )
        require(
            isinstance(interrupted, dict) and interrupted.get("interrupted") is False,
            "main_loop_not_proven_inactive",
        )
        await pending()
        self.latest_cold_stop_snapshot.update(
            complete=True, finished_monotonic=time.monotonic(), finished_at=now()
        )
        validate(self.latest_cold_stop_snapshot, session.session_id)
        return self.latest_cold_stop_snapshot

    async def inactive(self, session: Any) -> bool:
        """Positive RPC proof that a paused, aborted session has no active turn, task or tool wait."""
        if not hasattr(session, "rpc"):
            return False
        raw_tasks = await session.rpc.tasks.list(timeout=5)
        tasks = raw_tasks.to_dict() if hasattr(raw_tasks, "to_dict") else raw_tasks
        if not tasks_inactive(tasks):
            return False
        raw_external = await session.rpc.tools.list_pending_requests(timeout=5)
        external = raw_external.to_dict() if hasattr(raw_external, "to_dict") else raw_external
        if not external_empty(external):
            return False
        raw_interrupted = await session.rpc.interrupt_main_turn(
            InterruptMainTurnRequest(flush_queued=False), timeout=5
        )
        interrupted = (
            raw_interrupted.to_dict() if hasattr(raw_interrupted, "to_dict") else raw_interrupted
        )
        if not (isinstance(interrupted, dict) and interrupted.get("interrupted") is False):
            return False
        raw_after = await session.rpc.queue.pending_items(timeout=5)
        after = raw_after.to_dict() if hasattr(raw_after, "to_dict") else raw_after
        return queue_empty(after)

    async def pause(self, session: Any, paused: bool) -> None:
        await session.rpc.queue.set_drain_paused(
            QueueSetDrainPausedRequest(paused=paused), timeout=10
        )

    async def pending(self, session: Any) -> dict[str, Any]:
        return (await session.rpc.queue.pending_items(timeout=10)).to_dict()

    async def withdraw(self, session: Any, text: str, message_id: str) -> dict[str, Any]:
        return (
            await session.rpc.queue.withdraw_message(
                QueueWithdrawMessageRequest(expected_prompt=text, message_id=message_id), timeout=10
            )
        ).to_dict()

    async def health(self) -> None:
        if self.client:
            await asyncio.wait_for(self.client.ping(), 15)

    def transport_alive(self) -> bool:
        """Positive exact-process/connected-transport evidence, not absence of an error."""
        if self.client is None or getattr(self.client, "_state", None) != "connected":
            return False
        process = getattr(self.client, "_process", None)
        if process is None or process.returncode is not None:
            return False
        stdin = getattr(process, "stdin", None)
        if stdin is not None and (
            getattr(stdin, "closed", False) or (hasattr(stdin, "is_closing") and stdin.is_closing())
        ):
            return False
        record_path = self.services.settings.data_directory / "copilot-runtime/process.json"
        if not record_path.exists():
            return False
        record = json.loads(record_path.read_text())
        return record["pid"] == process.pid and identity(record["pid"]) == record["start"]

    async def close(self) -> None:
        record_path = self.services.settings.data_directory / "copilot-runtime/process.json"
        record = None
        # A launcher interrupted during startup may not have finished writing the marker.
        with suppress(FileNotFoundError, json.JSONDecodeError):
            record = json.loads(record_path.read_text())
            self.observe_departure(record)
        if self.client is not None:
            try:
                await asyncio.wait_for(self.client.stop(), 20)
            except BaseException:
                await asyncio.wait_for(self.client.force_stop(), 20)
            finally:
                self.client = None
                self.non_continuing_session_id = None
                if record is not None:
                    self.observe_departure(record)
                else:
                    with suppress(FileNotFoundError, json.JSONDecodeError):
                        self.observe_departure(json.loads(record_path.read_text()))
