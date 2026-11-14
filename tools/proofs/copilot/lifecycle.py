"""Measure SDK-owned native PIDs, never enumerate or terminate other runtimes."""

import asyncio
from pathlib import Path

from copilot.generated.rpc import QueueSetDrainPausedRequest, SandboxConfig
from probe import close_client, make_client


async def probe_lifecycle(cli: Path, runtime_dir: Path) -> dict:
    if cli.read_bytes()[:4] != b"\x7fELF":
        raise RuntimeError(
            "Lifecycle proof requires the direct ELF binary, not a launcher"
        )
    observations = []
    for action in ("stop", "force_stop"):
        client = make_client(cli, runtime_dir)
        process = None
        try:
            await asyncio.wait_for(client.start(), 60)
            # Inspection only. Production lifecycle uses the public SDK methods.
            process = client._cli_process
            proc = Path(f"/proc/{process.pid}")
            observation = {
                "action": action,
                "pid": process.pid,
                "start_ticks": proc.joinpath("stat")
                .read_text()
                .rsplit(")", 1)[1]
                .split()[19],
                "executable": str(proc.joinpath("exe").resolve()),
                "poll_before": process.poll(),
            }
            await asyncio.wait_for(getattr(client, action)(), 25)
            observation.update(
                {"poll_after": process.poll(), "proc_exists_after": proc.exists()}
            )
            observations.append(observation)
        finally:
            if process is not None and process.poll() is None:
                await close_client(client)
    result = {
        "evidence_kind": "real direct native runtime, zero model requests",
        "observations": observations,
        "unrelated_processes_touched": False,
    }
    # A supported blocking hook is a fail-safe against accidentally spending a
    # ninth cloud request while testing pending-load behaviour.
    prompt_attempts = []
    load_events = []

    def block_prompt(data, context):
        prompt_attempts.append(data["prompt"])
        return {"decision": "block", "reason": "E01 no-inference load guard"}

    project = runtime_dir / "project-pending-load"
    project.mkdir(exist_ok=True)
    settings = {
        "working_directory": str(project),
        "memory": {"enabled": False},
        "enable_session_store": False,
        "sandbox_config": SandboxConfig(enabled=False),
        "enable_config_discovery": False,
        "skip_custom_instructions": True,
        "hooks": {"on_user_prompt_submitted": block_prompt},
        "on_event": lambda event: load_events.append(event.type.value),
    }
    client = make_client(cli, runtime_dir)
    try:
        await asyncio.wait_for(client.start(), 60)
        session = await asyncio.wait_for(client.create_session(**settings), 30)
        sid = session.session_id
        await session.rpc.queue.set_drain_paused(
            QueueSetDrainPausedRequest(paused=True), timeout=10
        )
        message = await asyncio.wait_for(
            session.send("E01 pending-load guard. Never execute.", mode="enqueue"), 10
        )
        pending_before = (await session.rpc.queue.pending_items(timeout=10)).to_dict()
        await asyncio.wait_for(client.force_stop(), 25)
        client = make_client(cli, runtime_dir)
        await asyncio.wait_for(client.start(), 60)
        load_events.clear()
        result["pending_cold_load"] = {
            "session_id": sid,
            "native_message_id": message,
            "pending_before_forced_exit": pending_before,
            "prompt_attempts_blocked_by_guard": prompt_attempts,
            "load_event_types": load_events,
            "cloud_model_requests": 0,
        }
        try:
            resumed = await asyncio.wait_for(
                client.resume_session(sid, **settings, continue_pending_work=False), 30
            )
            await asyncio.sleep(2)
            result["pending_cold_load"]["pending_after_noncontinuing_resume"] = (
                await resumed.rpc.queue.pending_items(timeout=10)
            ).to_dict()
            result["pending_cold_load"]["resume_status"] = "loaded"
        except Exception as error:  # noqa: BLE001 - record redacted failure, never recreate missing history
            result["pending_cold_load"]["resume_status"] = (
                "native_session_missing"
                if "Session not found:" in str(error)
                else "failed"
            )
            result["pending_cold_load"]["error_type"] = type(error).__name__
    finally:
        await close_client(client)
    return result
