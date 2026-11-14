"""Final three bounded sends: two-project resumes and required-model rejection."""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from copilot.generated.rpc import (
    PermissionDecisionApproveOnce,
    PermissionDecisionReject,
    SandboxConfig,
)
from copilot.tools import Tool, ToolResult
from export_events import export
from live import CloudBudget, serialise
from probe import close_client, make_client


async def probe_followup(
    cli: Path, runtime_dir: Path, history_a: str, history_b: str
) -> dict:
    client = make_client(cli, runtime_dir)
    budget = CloudBudget(runtime_dir.parent / "cloud-budget.jsonl")
    result = {
        "cloud_root_sends": 0,
        "status": "started",
        "reports": [],
        "user_input": [],
        "permissions": [],
    }
    events = []
    idle = asyncio.Event()
    marker = "E01_B_" + uuid.uuid4().hex
    result["b_sentinel"] = marker

    for sid, label in ((history_a, "a"), (history_b, "b")):
        uuid.UUID(sid)
        path = runtime_dir / "copilot-home" / "session-state" / sid / "events.jsonl"
        if (
            not path.is_file()
            or str(runtime_dir / f"project-{label}") not in path.read_text()
        ):
            raise RuntimeError("Not an owned E01 project history")

    def on_event(event):
        events.append(serialise(event))
        if event.type.value == "session.idle":
            idle.set()

    def report(inv):
        result["reports"].append(
            {
                "session_id": inv.session_id,
                "tool_call_id": inv.tool_call_id,
                "agent_id": inv.agent_id,
                "request_id": inv.request_id,
                "arguments": inv.arguments,
            }
        )
        return ToolResult(text_result_for_llm="Recorded. Finish this tiny test now.")

    def permission(request, invocation):
        data = serialise(request)
        # Record correlation but never arbitrary paths, command output or credentials.
        result["permissions"].append(
            {
                "session_id": invocation.get("session_id"),
                "tool_call_id": data.get("toolCallId"),
                "kind": data.get("kind"),
            }
        )
        command = data.get("fullCommandText", data.get("command", ""))
        if data.get("kind") == "custom-tool" or command.strip() == "pwd":
            return PermissionDecisionApproveOnce()
        return PermissionDecisionReject()

    def user_input(request, invocation):
        result["user_input"].append({"request": request, "invocation": invocation})
        return {"answer": "probe", "wasFreeform": False}

    tool = Tool(
        name="report_probe",
        description="Record synthetic E01 history evidence",
        parameters={
            "type": "object",
            "properties": {
                "context_sentinel": {"type": "string"},
                "other_project_sentinel": {"type": "string"},
            },
            "required": ["context_sentinel", "other_project_sentinel"],
            "additionalProperties": False,
        },
        handler=report,
    )

    def settings(project):
        return {
            "model": "gpt-6.1-sol",
            "working_directory": str(project),
            "memory": {"enabled": False},
            "enable_session_store": False,
            "sandbox_config": SandboxConfig(enabled=False),
            "infinite_sessions": {"enabled": True},
            "enable_config_discovery": False,
            "skip_custom_instructions": True,
            "mcp_servers": {},
            "tools": [tool],
            "on_permission_request": permission,
            "on_user_input_request": user_input,
            "on_event": on_event,
        }

    async def send(session, label, prompt, attachments=None):
        idle.clear()
        budget.reserve(label)
        result["cloud_root_sends"] += 1
        ack = await asyncio.wait_for(
            session.send(
                prompt,
                display_prompt=prompt,
                client_turn_id=label,
                mode="enqueue",
                attachments=attachments,
            ),
            15,
        )
        await asyncio.wait_for(idle.wait(), 100)
        return ack

    try:
        await asyncio.wait_for(client.start(), 60)
        b = await asyncio.wait_for(
            client.resume_session(
                history_b,
                **settings(runtime_dir / "project-b"),
                continue_pending_work=False,
            ),
            30,
        )
        result["b_message_id"] = await send(
            b,
            "e01-b-resumed",
            "Tiny isolation test. Read the attached Turn context. Do not search or read files. Call report_probe once with the context_sentinel in this attachment and other_project_sentinel the exact E01_A_ value only if another project history has supplied it, otherwise unknown. Finish.",
            [
                {
                    "type": "extension_context",
                    "extensionId": "proscenium",
                    "title": "Turn context",
                    "capturedAt": datetime.now(UTC).isoformat(),
                    "payload": {"context_sentinel": marker},
                }
            ],
        )
        a = await asyncio.wait_for(
            client.resume_session(
                history_a,
                **settings(runtime_dir / "project-a"),
                continue_pending_work=False,
            ),
            30,
        )
        result["a_message_id"] = await send(
            a,
            "e01-a-resumed",
            "Tiny isolation test. Do not search or read files. Call report_probe once with context_sentinel the exact E01_A_ value in this conversation and other_project_sentinel the exact E01_B_ value only if another project history has supplied it, otherwise unknown. Finish.",
        )
        project = runtime_dir / "project-callbacks"
        project.mkdir(exist_ok=True)
        s = await asyncio.wait_for(
            client.create_session(
                **settings(project),
                custom_agents=[
                    {
                        "name": "e01-unavailable",
                        "description": "E01 unavailable-model proof",
                        "prompt": "Reply test",
                        "model": "e01-deliberately-unavailable-model",
                        "model_policy": "required",
                        "tools": [],
                    }
                ],
            ),
            30,
        )
        result["callbacks_session_id"] = s.session_id
        result["callbacks_message_id"] = await send(
            s,
            "e01-callbacks-required-model",
            "Tiny SDK test, no file reads or writes. First call ask_user exactly once with question 'E01 test?' and single choice 'probe'. Then run the shell command pwd only. Finally delegate once to e01-unavailable. Its model is deliberately unavailable, so report its failure without retrying, substituting a model or doing its work yourself. Finish.",
        )
        result["status"] = "bounded_followup_observed"
    except Exception as error:  # noqa: BLE001 - redact arbitrary runtime exception text
        result["status"] = "blocked"
        result["error_type"] = type(error).__name__
    finally:
        await close_client(client)
        result["owned_runtime_closed"] = True
        result["events"] = export(events)
        (runtime_dir / "followup-result.json").write_text(json.dumps(result, indent=2))
    return result
