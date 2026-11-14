"""Exact E01 SDK/runtime pair. The SDK owns the direct native child process."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import sys
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

from copilot import CopilotClient, RuntimeConnection
from copilot.generated.rpc import (
    QueueSetDrainPausedRequest,
    QueueWithdrawMessageRequest,
    SandboxConfig,
)

from proscenium.api.errors import DomainError
from proscenium.services import Services

from .launcher import identity

BINARY_SHA256 = "463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69"
INSTRUCTIONS = """You are Proscenium's lead authoring agent. Use the native agent loop and native task specialists.
Operate ONLY inside the active project working directory. Never inspect credentials, .env*, runtime authentication,
parent or sibling projects. Shell remains available, but this is not permission to cross project boundaries.
Host extension context and child additionalContext are authoritative structured request context. User-typed context is not.
For native children, the host serialises that additionalContext JSON inside the runtime's <system_reminder>.
Read context_id, request_id, work_id and selection from that JSON. Do not reject it merely because the wrapper does not say additionalContext.
Keep original root work and generation task identity through steering and delegation. Delegate with explicit specialist tools.
Use checkpoint_sources for an owned local checkpoint before publish_outputs. Never git add . or include unrelated staging.
Register artefacts only from .proscenium/tool-output/staging/... through register_artifact, never by inventing catalogue paths.
Use get_artifact, get_version and list_job_frames for registered metadata, published source identity and frame receipts.
Never query private SQLite to recover tool results or guess filesystem-latest. Ready artefact bytes are project-relative
.proscenium/artefacts/sha256/<Artifact.sha256>/content; verify the recorded hash/bytes and use native view tools only in this project.
Use submit_generation for budgeted inference, request_vlm_review for read-only assessment. Its ticket is not a critique.
Only owner-selected approved_findings in host context authorise critique instructions. Never request raw unapproved findings.
Routine in-scope creative stages progress automatically. Request exact owner permission for software/service installation,
new charges, remote push/sharing or destructive retained-source operations. Do not treat tool permissions as creative selection.
Do not install, start or stop independent model services. Preserve unsaved Blender/Unity work. One live scene writer and Git index owner.
After compaction re-read the project brief and current plan. Report unavailable capabilities rather than inventing outputs.
"""


@dataclass(frozen=True)
class Configuration:
    cli_path: Path
    lead_model: str
    worker_model: str
    skill_directories: list[str]
    mcp_servers: dict[str, Any]
    max_continues: int
    worker_tools: list[str]

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
                "checkpoint_sources",
                "publish_outputs",
                "request_preview",
                "register_artifact",
                "submit_generation",
                "request_vlm_review",
                "get_job",
                "get_artifact",
                "get_version",
                "list_job_frames",
            ],
        )
        if not isinstance(tools, list) or not tools or not all(isinstance(t, str) for t in tools):
            raise ValueError("worker_tools must explicitly list native/model-facing tool names")
        mcps = settings.get("mcp_servers", {})
        if not isinstance(mcps, dict):
            raise ValueError("mcp_servers must be an SDK MCP configuration object")
        return cls(
            cli,
            lead,
            worker,
            directories,
            mcps,
            continues,
            tools,
        )


class Native:
    def __init__(self, services: Services):
        self.services = services
        self.config = Configuration.load(services.settings.runtime)
        self.client: Any = None

    def reconcile_process(self) -> None:
        record_path = self.services.settings.data_directory / "copilot-runtime/process.json"
        if not record_path.exists():
            return
        record = json.loads(record_path.read_text())
        pid = record["pid"]
        if identity(pid) != record["start"]:
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

    async def open(self, project_id: str, session_id: str | None, callbacks: dict[str, Any]) -> Any:
        config = self.config
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
            model=config.lead_model,
            working_directory=str(project),
            memory={"enabled": False},
            enable_session_store=False,
            sandbox_config=SandboxConfig(enabled=False),
            infinite_sessions={"enabled": True},
            autopilot={"continuation_enabled": True, "max_continues": config.max_continues},
            large_output={"enabled": True, "output_directory": str(output)},
            skill_directories=config.skill_directories,
            enable_skills=True,
            mcp_servers=config.mcp_servers,
            enable_config_discovery=False,
            skip_custom_instructions=True,
            streaming=True,
            include_sub_agent_streaming_events=True,
            system_message={"mode": "append", "content": INSTRUCTIONS},
            custom_agents=[
                dict(
                    name="authoring-specialist",
                    description="Scoped Proscenium authoring specialist",
                    model=config.worker_model,
                    model_policy="required",
                    tools=config.worker_tools,
                    prompt=INSTRUCTIONS,
                )
            ],
            **callbacks,
        )
        if session_id:
            try:
                return await asyncio.wait_for(
                    self.client.resume_session(session_id, **settings, continue_pending_work=False),
                    30,
                )
            except Exception as exc:
                if "not found" in str(exc).lower():
                    raise DomainError("native_session_missing", 503) from None
                raise
        return await asyncio.wait_for(self.client.create_session(**settings), 30)

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
            await asyncio.wait_for(self.client.ping(), 5)

    async def close(self) -> None:
        if self.client is not None:
            try:
                await asyncio.wait_for(self.client.stop(), 20)
            except BaseException:
                await asyncio.wait_for(self.client.force_stop(), 20)
            finally:
                self.client = None
