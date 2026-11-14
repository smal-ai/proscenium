"""Opt-in real native hook and deferred steering tests, under the same budget."""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from copilot.generated.rpc import (
    PermissionDecisionApproveOnce,
    QueueSetDrainPausedRequest,
    QueueWithdrawMessageRequest,
    SandboxConfig,
)
from copilot.tools import Tool, ToolResult
from export_events import export
from live import CloudBudget, serialise
from probe import close_client, make_client


async def probe_advanced(cli: Path, runtime_dir: Path) -> dict:
    client = make_client(cli, runtime_dir)
    budget = CloudBudget(runtime_dir.parent / "cloud-budget.jsonl")
    result = {
        "cloud_root_sends": 0,
        "status": "started",
        "reports": [],
        "prompt_hooks": [],
        "permissions": [],
    }
    events = []
    idle = asyncio.Event()
    hook_entered = asyncio.Event()
    hook_release = asyncio.Event()
    sentinel = "E01_HOOK_" + uuid.uuid4().hex
    result["context_sentinel"] = sentinel
    project = runtime_dir / "project-hook"
    project.mkdir(exist_ok=True)

    def on_event(event):
        events.append(serialise(event))
        if event.type.value == "session.idle":
            idle.set()

    def permission(request, invocation):
        result["permissions"].append(
            {
                "request_keys": sorted(serialise(request)),
                "session_id": invocation.get("session_id"),
                "program_tool_call_id": invocation.get("program_tool_call_id"),
            }
        )
        # Only the read-only proof tool is requested in these synthetic prompts.
        return PermissionDecisionApproveOnce()

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
        return ToolResult(text_result_for_llm="Recorded. Finish immediately.")

    def prompt_hook(data, context):
        result["prompt_hooks"].append(
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
        return {
            "additionalContext": json.dumps(
                {"context_sentinel": sentinel, "project": {"path": str(project)}}
            )
        }

    async def stop_hook(data, context):
        # Hold a real completed root at its supported end-of-turn hook so an
        # immediate human directive deterministically reaches the completion race.
        if not data.get("agentId"):
            hook_entered.set()
            await asyncio.wait_for(hook_release.wait(), 30)

    async def send(session, label, prompt, mode="enqueue"):
        budget.reserve(label)
        result["cloud_root_sends"] += 1
        return await asyncio.wait_for(
            session.send(
                prompt, mode=mode, display_prompt=prompt, client_turn_id=label
            ),
            20,
        )

    try:
        await asyncio.wait_for(client.start(), 60)
        auth = await asyncio.wait_for(client.get_auth_status(), 30)
        if not auth.isAuthenticated:
            result["status"] = "authentication_blocked"
            return result
        settings = {
            "model": "gpt-6.1-sol",
            "working_directory": str(project),
            "memory": {"enabled": False},
            "enable_session_store": False,
            "sandbox_config": SandboxConfig(enabled=False),
            "infinite_sessions": {"enabled": True},
            "mcp_servers": {},
            "enable_config_discovery": False,
            "skip_custom_instructions": True,
            "on_event": on_event,
            "on_permission_request": permission,
        }
        tool = Tool(
            name="report_probe",
            description="Record synthetic E01 context evidence then finish",
            parameters={
                "type": "object",
                "properties": {"sentinel": {"type": "string"}},
                "required": ["sentinel"],
                "additionalProperties": False,
            },
            handler=report,
        )
        hook_settings = {
            **settings,
            "tools": [tool],
            "hooks": {"on_user_prompt_submitted": prompt_hook},
            "custom_agents": [
                {
                    "name": "e01-hook-reader",
                    "description": "Read-only tiny E01 specialist",
                    "model": "gpt-6.1-sol",
                    "model_policy": "required",
                    "tools": ["report_probe"],
                    "prompt": "Call report_probe with the exact context_sentinel in host hook additional context, or unknown if absent. Do not inspect files. Finish.",
                }
            ],
        }
        session = await asyncio.wait_for(client.create_session(**hook_settings), 30)
        result["hook_session_id"] = session.session_id
        idle.clear()
        result["hook_message_id"] = await send(
            session,
            "e01-hook-child",
            "Tiny SDK test. Call report_probe once with the context_sentinel visible in host additional context. Then delegate once to e01-hook-reader to report the context_sentinel it can see. Do not put its value in your delegation prompt. No other tools or file reads. Finish.",
        )
        await asyncio.wait_for(idle.wait(), 100)
        await session.disconnect()
        before = len(events)
        session = await asyncio.wait_for(
            client.resume_session(
                result["hook_session_id"], **hook_settings, continue_pending_work=False
            ),
            30,
        )
        await asyncio.sleep(2)
        result["settled_resume_events"] = export(events[before:])

        # Use a separate owned session so no other history or work can be removed.
        stop_session = await asyncio.wait_for(
            client.create_session(**settings, hooks={"on_agent_stop": stop_hook}), 30
        )
        result["stop_session_id"] = stop_session.session_id
        idle.clear()
        result["stop_root_message_id"] = await send(
            stop_session, "e01-stop-root", "Reply with the single word ready. No tools."
        )
        await asyncio.wait_for(hook_entered.wait(), 60)
        await stop_session.rpc.queue.set_drain_paused(
            QueueSetDrainPausedRequest(paused=True), timeout=10
        )
        result["pause_acknowledged_before_race"] = True
        steer_prompt = "Reply with the single word deferred. No tools."
        # SDK acceptance is real. Delay its application receipt until after Stop.
        steer_id = await send(stop_session, "e01-stop-steer", steer_prompt, "immediate")
        result["steering_before_hook_release"] = (
            await stop_session.rpc.queue.pending_items(timeout=10)
        ).to_dict()
        hook_release.set()
        await asyncio.sleep(1)
        result["deferred_queue"] = (
            await stop_session.rpc.queue.pending_items(timeout=10)
        ).to_dict()
        result["native_idle_before_stop"] = idle.is_set()
        await asyncio.wait_for(stop_session.abort(), 10)
        result["abort_acknowledged"] = True
        result["withdrawal"] = (
            await stop_session.rpc.queue.withdraw_message(
                QueueWithdrawMessageRequest(
                    expected_prompt=steer_prompt, message_id=steer_id
                ),
                timeout=10,
            )
        ).to_dict()
        result["queue_after_stop"] = (
            await stop_session.rpc.queue.pending_items(timeout=10)
        ).to_dict()
        result["barrier_observed_at"] = datetime.now(UTC).isoformat()
        barrier_index = len(events)
        result["late_application_ack"] = {
            "message_id": steer_id,
            "note": "SDK result deliberately bound only after Stop, not a simulated transport failure",
        }
        await asyncio.sleep(3)
        result["after_barrier_events"] = export(events[barrier_index:])
        result["pause_released"] = False
        # Disconnection/termination is part of the proof. A later owner must not
        # assume that an in-memory queue pause survives a cold runtime restart.
        await close_client(client)
        result["owned_runtime_closed_before_resume"] = True
        client = make_client(cli, runtime_dir)
        await asyncio.wait_for(client.start(), 60)
        before = len(events)
        stopped = await asyncio.wait_for(
            client.resume_session(
                result["stop_session_id"], **settings, continue_pending_work=False
            ),
            30,
        )
        await asyncio.sleep(2)
        result["cold_resume_events"] = export(events[before:])
        result["cold_resume_queue"] = (
            await stopped.rpc.queue.pending_items(timeout=10)
        ).to_dict()
        result["status"] = "real_controls_observed_review_invariants"
    except Exception as error:  # noqa: BLE001 - redact arbitrary runtime exception text
        result["status"] = "blocked"
        result["error_type"] = type(error).__name__
    finally:
        hook_release.set()
        await close_client(client)
        result["owned_runtime_closed"] = True
        result["events"] = export(events)
        (runtime_dir / "advanced-result.json").write_text(json.dumps(result, indent=2))
    return result
