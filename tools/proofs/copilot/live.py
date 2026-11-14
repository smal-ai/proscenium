"""Tiny, opt-in cloud proof. Native transcripts stay outside the repository."""

import asyncio
import dataclasses
import json
import os
import struct
import uuid
import zlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from copilot.generated.rpc import (
    PermissionDecisionApproveOnce,
    PermissionDecisionReject,
    SandboxConfig,
)
from copilot.tools import Tool, ToolResult
from probe import close_client, make_client


class CloudBudget:
    """Durable pre-send reservation. An uncertain send is never refunded or retried."""

    def __init__(self, path: Path):
        self.path = path

    def reserve(self, label: str) -> None:
        # Exclusive file lock prevents concurrent probes exceeding the allocation.
        import fcntl

        with self.path.open("a+") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            file.seek(0)
            rows = [json.loads(row) for row in file if row.strip()]
            if any(row["label"] == label for row in rows):
                raise RuntimeError(
                    "E01 send identity already reserved; never retry its uncertain outcome"
                )
            if len(rows) >= 8:
                raise RuntimeError("E01 cloud allocation exhausted")
            file.write(
                json.dumps(
                    {"label": label, "reserved_at": datetime.now(UTC).isoformat()}
                )
                + "\n"
            )
            file.flush()
            os.fsync(file.fileno())


def tiny_image(path: Path) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload))
        )

    pixels = (b"\x00" + b"\xff\x00\x00" * 16) * 16
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def serialise(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    return value


async def probe_live(cli: Path, runtime_dir: Path) -> dict:
    budget = CloudBudget(runtime_dir.parent / "cloud-budget.jsonl")
    result = {"cloud_root_sends": 0, "status": "started", "turns": [], "callbacks": []}
    client = make_client(cli, runtime_dir)
    raw_events = []
    event_counts = Counter()
    reports = []
    hooks = []
    idle = asyncio.Event()

    def on_event(event):
        raw_events.append(serialise(event))
        event_counts[event.type.value] += 1
        if event.type.value == "session.idle":
            idle.set()

    def on_permission(request, invocation):
        # The proof does not grant arbitrary shell or filesystem authoring.
        data = serialise(request)
        result["callbacks"].append(
            {
                "kind": "permission",
                "request_keys": sorted(data),
                "invocation": {
                    key: invocation.get(key)
                    for key in ("session_id", "program_tool_call_id")
                },
            }
        )
        if data.get("kind") == "custom-tool":
            return PermissionDecisionApproveOnce()
        return PermissionDecisionReject()

    def on_prompt(data, context):
        hooks.append(
            {
                key: data.get(key)
                for key in (
                    "sessionId",
                    "workingDirectory",
                    "prompt",
                    "agentId",
                    "parentAgentId",
                )
            }
        )

    def report(invocation):
        record = {
            "session_id": invocation.session_id,
            "tool_call_id": invocation.tool_call_id,
            "agent_id": invocation.agent_id,
            "request_id": invocation.request_id,
            "arguments": invocation.arguments,
        }
        reports.append(record)
        return ToolResult(
            text_result_for_llm="Report recorded. Finish this tiny test now."
        )

    tool = Tool(
        name="report_probe",
        description="Record the tiny E01 capability report. No side effects except recording this report.",
        parameters={
            "type": "object",
            "properties": {
                "context_sentinel": {"type": "string"},
                "image_colour": {"type": "string"},
                "other_project_sentinel": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": [
                "context_sentinel",
                "image_colour",
                "other_project_sentinel",
                "notes",
            ],
            "additionalProperties": False,
        },
        handler=report,
    )
    try:
        await asyncio.wait_for(client.start(), 60)
        auth = await asyncio.wait_for(client.get_auth_status(), 30)
        result["auth"] = {
            "is_authenticated": auth.isAuthenticated,
            "auth_type": auth.authType,
        }
        if not auth.isAuthenticated:
            result["status"] = "authentication_blocked"
            return result
        models = await asyncio.wait_for(client.list_models(), 30)
        ids = [model.id for model in models]
        model = (
            "gpt-6.1-sol"
            if "gpt-6.1-sol" in ids
            else next(value for value in ids if value != "auto")
        )
        result["model"] = model
        projects = []
        for label in ("a", "b"):
            path = runtime_dir / f"project-{label}"
            path.mkdir(exist_ok=True)
            projects.append(path)
        image = projects[0] / "red.png"
        tiny_image(image)
        sentinel = "E01_A_" + uuid.uuid4().hex
        result["context_sentinel"] = sentinel

        def settings(project):
            return {
                "model": model,
                "working_directory": str(project),
                "memory": {"enabled": False},
                "enable_session_store": False,
                "sandbox_config": SandboxConfig(enabled=False),
                "infinite_sessions": {"enabled": True},
                "enable_config_discovery": False,
                "skip_custom_instructions": True,
                "mcp_servers": {},
                "tools": [tool],
                "on_permission_request": on_permission,
                "on_event": on_event,
                "hooks": {"on_user_prompt_submitted": on_prompt},
                "custom_agents": [
                    {
                        "name": "e01-reader",
                        "description": "E01 tiny context-reading specialist",
                        "prompt": "Read only the supplied E01 synthetic turn context. Never inspect files or credentials. Call report_probe once with the context_sentinel you can actually see, image_colour if visible else unknown, other_project_sentinel unknown, notes child. Do not invent missing context. Finish immediately.",
                        "model": model,
                        "model_policy": "required",
                        "tools": ["report_probe"],
                    }
                ],
            }

        async def send(session, label, prompt, attachments=None):
            budget.reserve(label)
            result["cloud_root_sends"] += 1
            idle.clear()
            start = len(raw_events)
            ack = await asyncio.wait_for(
                session.send(
                    prompt,
                    mode="enqueue",
                    display_prompt=prompt,
                    client_turn_id=label,
                    attachments=attachments,
                ),
                30,
            )
            await asyncio.wait_for(idle.wait(), 115)
            result["turns"].append(
                {
                    "request_id": label,
                    "native_message_id": ack,
                    "prompt": prompt,
                    "events_seen": len(raw_events) - start,
                }
            )

        session_a = await asyncio.wait_for(
            client.create_session(**settings(projects[0])), 30
        )
        result["session_a"] = session_a.session_id
        attachment = {
            "type": "extension_context",
            "extensionId": "proscenium",
            "title": "Turn context",
            "capturedAt": datetime.now(UTC).isoformat(),
            "payload": {
                "context_sentinel": sentinel,
                "project": {"path": str(projects[0])},
            },
        }
        await send(
            session_a,
            "e01-context-image-child",
            "Tiny SDK test. Read the attached Turn context and image. Call report_probe with its context_sentinel and the image colour, other_project_sentinel unknown, notes root. Then delegate once to the e01-reader native specialist to report which Turn context it can actually see. Do not supply the sentinel value in the delegation prompt. No other tools or file reads. Finish in one sentence.",
            [{"type": "file", "path": str(image)}, attachment],
        )
        result["reports_a"] = list(reports)
        session_b = await asyncio.wait_for(
            client.create_session(**settings(projects[1])), 30
        )
        result["session_b"] = session_b.session_id
        await send(
            session_b,
            "e01-history-isolation",
            "Tiny SDK isolation test. Without reading files or searching anything, call report_probe once: context_sentinel unknown, image_colour unknown, other_project_sentinel the exact E01_A_ value from a different project's history only if it was already supplied to you, otherwise unknown, notes isolation. Finish.",
        )
        result["reports_after_b"] = list(reports)
        await session_a.disconnect()
        before = dict(event_counts)
        session_a = await asyncio.wait_for(
            client.resume_session(
                result["session_a"],
                **settings(projects[0]),
                continue_pending_work=False,
            ),
            30,
        )
        await asyncio.sleep(2)
        result["settled_resume_event_delta"] = dict(event_counts - Counter(before))
        result["status"] = "bounded_cloud_observed"
        result["limitations"] = [
            "No active-turn Stop race tested yet",
            "No interrupted native child recovery tested",
            "Working directory is a pragmatic instruction boundary, not OS isolation",
        ]
    except Exception as error:  # noqa: BLE001 - redact arbitrary runtime exception text
        result["status"] = "blocked"
        result["error_type"] = type(error).__name__
    finally:
        result["reports"] = reports
        result["prompt_hooks"] = hooks
        result["event_counts"] = dict(event_counts)
        (runtime_dir / "live-result.json").write_text(json.dumps(result, indent=2))
        # Only private scratch output retains full native event details.
        (runtime_dir / "live-events.json").write_text(
            json.dumps(raw_events, default=str, indent=2)
        )
        await close_client(client)
        result["owned_runtime_closed"] = True
    return result
