"""Real local runtime checks. Paused synthetic prompts never reach a model."""

import asyncio
import uuid
from pathlib import Path

from copilot.generated.rpc import (
    PermissionDecisionReject,
    QueueSetDrainPausedRequest,
    QueueWithdrawMessageRequest,
    SandboxConfig,
)
from probe import close_client, make_client


async def probe_controls(cli: Path, runtime_dir: Path) -> dict:
    client = make_client(cli, runtime_dir)
    result = {
        "evidence_kind": "real local runtime, no model requests",
        "cloud_sends": 0,
    }
    try:
        await asyncio.wait_for(client.start(), 60)
        status = await asyncio.wait_for(client.get_auth_status(), 30)
        result["isolated_auth"] = {
            "is_authenticated": status.isAuthenticated,
            "auth_type": status.authType,
        }
        if status.isAuthenticated:
            models = await asyncio.wait_for(client.list_models(), 30)
            result["available_model_ids"] = [model.id for model in models]
        project = runtime_dir / "project-controls"
        project.mkdir(exist_ok=True)
        settings = {
            "working_directory": str(project),
            "memory": {"enabled": False},
            "enable_session_store": False,
            "sandbox_config": SandboxConfig(enabled=False),
            "on_permission_request": lambda _req, _inv: PermissionDecisionReject(),
            "enable_config_discovery": False,
            "skip_custom_instructions": True,
            "mcp_servers": {},
        }
        session = await asyncio.wait_for(client.create_session(**settings), 30)
        result["session_id"] = session.session_id
        result["settings"] = {
            key: value
            for key, value in settings.items()
            if key not in {"on_permission_request", "sandbox_config"}
        }
        result["settings"]["sandbox_config"] = {"enabled": False}
        await session.rpc.queue.set_drain_paused(
            QueueSetDrainPausedRequest(paused=True), timeout=10
        )
        result["pause_acquired"] = True
        prompt = "E01_QUEUE_ONLY_" + uuid.uuid4().hex + ": do not execute"
        message_id = await asyncio.wait_for(
            session.send(
                prompt,
                mode="enqueue",
                display_prompt=prompt,
                client_turn_id="e01-control-queue",
            ),
            10,
        )
        before = await session.rpc.queue.pending_items(timeout=10)
        result["paused_queue"] = before.to_dict()
        withdrawn = await session.rpc.queue.withdraw_message(
            QueueWithdrawMessageRequest(expected_prompt=prompt, message_id=message_id),
            timeout=10,
        )
        result["withdrawal"] = withdrawn.to_dict()
        result["after_withdrawal"] = (
            await session.rpc.queue.pending_items(timeout=10)
        ).to_dict()
        await asyncio.wait_for(session.abort(), 10)
        result["abort_acknowledged"] = True
        sid = session.session_id
        await session.disconnect()
        # This is an empty/withdrawn session, NOT proof about interrupted live turns.
        resumed_events = []
        resumed = await asyncio.wait_for(
            client.resume_session(
                sid,
                **settings,
                continue_pending_work=False,
                on_event=lambda event: resumed_events.append(event.type.value),
            ),
            30,
        )
        await asyncio.sleep(1)
        result["resume_empty_session_events"] = resumed_events
        result["resume_empty_queue"] = (
            await resumed.rpc.queue.pending_items(timeout=10)
        ).to_dict()
        result["status"] = "local_controls_observed"
        result["limitations"] = [
            "No active model turn",
            "No completion-race deferred Steer",
            "No cross-project model retrieval test",
        ]
    except Exception as error:  # noqa: BLE001 - redact arbitrary runtime exception text
        # No raw runtime errors or request dumps: they can contain sensitive data.
        result["status"] = "blocked"
        result["error_type"] = type(error).__name__
    finally:
        await close_client(client)
        result["owned_runtime_closed"] = True
    return result
