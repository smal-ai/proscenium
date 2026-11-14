"""Bounded official Copilot compatibility probe. Never imports host credentials.

Run with the isolated SDK environment. Default performs no model sends.
Only allowlisted, synthetic evidence is written. Native history stays in runtime_dir.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import inspect
import json
import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from copilot import CopilotClient
from copilot.client import RuntimeConnection
from copilot.generated.rpc import QueueApi
from copilot.session import (
    CopilotSession,
    CustomAgentConfig,
    ExtensionContextAttachment,
)

SDK_VERSION = "1.0.34"
CLI_VERSION = "1.0.112"


def clean_environment(runtime_dir: Path) -> dict[str, str]:
    # Do not forward OpenCode/provider/GitHub credentials or login-shell imports.
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
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(
        {
            "COPILOT_DISABLE_LOGIN_SHELL_ENV": "1",
            "COPILOT_CACHE_HOME": str(runtime_dir / "cache"),
            "COPILOT_PLUGIN_DIR_ONLY": "true",
        }
    )
    return env


def make_client(
    cli: Path, runtime_dir: Path, *, existing_login: bool = False
) -> CopilotClient:
    return CopilotClient(
        connection=RuntimeConnection.for_stdio(
            path=str(cli),
            args=[
                "--no-auto-update",
                "--disable-builtin-mcps",
                "--no-custom-instructions",
                "--log-dir",
                str(runtime_dir / "logs"),
            ],
        ),
        env=clean_environment(runtime_dir),
        working_directory=str(runtime_dir),
        # Existing-login mode is auth.getStatus only: it never creates a session.
        base_directory=None if existing_login else str(runtime_dir / "copilot-home"),
        use_logged_in_user=True,
        log_level="error",
    )


async def close_client(client: CopilotClient) -> None:
    try:
        await asyncio.wait_for(client.stop(), timeout=25)
    except Exception:  # noqa: BLE001 - cleanup must cover transport and SDK failures
        # Public API kills/reaps only the process this client spawned.
        await asyncio.wait_for(client.force_stop(), timeout=25)


def surface() -> dict:
    targets = {
        "CopilotClient": CopilotClient,
        "RuntimeConnection.for_stdio": RuntimeConnection.for_stdio,
        "create_session": CopilotClient.create_session,
        "resume_session": CopilotClient.resume_session,
        "send": CopilotSession.send,
        "abort": CopilotSession.abort,
        "disconnect": CopilotSession.disconnect,
        "stop": CopilotClient.stop,
        "force_stop": CopilotClient.force_stop,
        "queue.set_drain_paused": QueueApi.set_drain_paused,
        "queue.pending_items": QueueApi.pending_items,
        "queue.withdraw_message": QueueApi.withdraw_message,
        "queue.remove_at": QueueApi.remove_at,
    }
    return {
        "signatures": {
            name: str(inspect.signature(obj)) for name, obj in targets.items()
        },
        "custom_agent_fields": sorted(CustomAgentConfig.__annotations__),
        "extension_context_fields": sorted(ExtensionContextAttachment.__annotations__),
        "queue_api_stability": "experimental, explicitly labelled in released generated RPC",
    }


async def run(args: argparse.Namespace) -> dict:
    runtime_dir = args.runtime_dir.resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    cli = args.cli.absolute()
    version = await asyncio.to_thread(
        subprocess.run,
        [str(cli), "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    result = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "evidence_kind": "installed SDK inspection and real local runtime, not model proof",
        "python": platform.python_version(),
        "sdk_version": importlib.metadata.version("github-copilot-sdk"),
        "cli_version_output": version.stdout.strip(),
        "cli_path": str(cli),
        "runtime_dir": str(runtime_dir),
        "surface": surface(),
        "cloud_root_sends": 0,
        "max_cloud_root_sends": 8,
        "live_capabilities": "not_run",
    }
    if (
        result["sdk_version"] != SDK_VERSION
        or f"CLI {CLI_VERSION}." not in version.stdout
    ):
        raise RuntimeError("Installed version differs from this probe's reviewed lock")
    if args.auth_status:
        client = make_client(cli, runtime_dir, existing_login=True)
        try:
            await asyncio.wait_for(client.start(), timeout=60)
            status = await asyncio.wait_for(client.get_auth_status(), timeout=30)
            # Deliberately omit login name, host, status text and any token API.
            result["existing_cli_login"] = {
                "is_authenticated": status.isAuthenticated,
                "auth_type": status.authType,
                "environment_tokens_forwarded": False,
            }
            if not status.isAuthenticated:
                result["blocker"] = (
                    "No existing supported CLI login. No login flow or model send attempted."
                )
        finally:
            await close_client(client)
    if args.native_controls:
        from native_controls import probe_controls

        result["native_controls"] = await probe_controls(cli, runtime_dir)
    if args.live:
        from live import probe_live

        result["live"] = await probe_live(cli, runtime_dir)
        result["cloud_root_sends"] = result["live"]["cloud_root_sends"]
        result["live_capabilities"] = result["live"]["status"]
    if args.advanced:
        from advanced import probe_advanced

        result["advanced"] = await probe_advanced(cli, runtime_dir)
        result["cloud_root_sends"] = result["advanced"]["cloud_root_sends"]
        result["live_capabilities"] = result["advanced"]["status"]
    if args.followup:
        from followup import probe_followup

        result["followup"] = await probe_followup(
            cli, runtime_dir, args.history_a, args.history_b
        )
        result["cloud_root_sends"] = result["followup"]["cloud_root_sends"]
        result["live_capabilities"] = result["followup"]["status"]
    if args.lifecycle:
        from lifecycle import probe_lifecycle

        result["lifecycle"] = await probe_lifecycle(cli, runtime_dir)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--auth-status",
        action="store_true",
        help="Read existing CLI login without model execution",
    )
    parser.add_argument(
        "--native-controls",
        action="store_true",
        help="Test isolated native queue RPC without cloud sends",
    )
    model_actions = parser.add_mutually_exclusive_group()
    model_actions.add_argument(
        "--live",
        action="store_true",
        help="Use bounded cloud allocation, never retry uncertain sends",
    )
    model_actions.add_argument(
        "--advanced",
        action="store_true",
        help="Prove hook context and deferred-Steer queue barrier",
    )
    model_actions.add_argument(
        "--followup",
        action="store_true",
        help="Resume owned A/B histories and check callback/model failure",
    )
    parser.add_argument("--history-a")
    parser.add_argument("--history-b")
    parser.add_argument(
        "--lifecycle",
        action="store_true",
        help="Test direct owned native process exit, without model sends",
    )
    args = parser.parse_args()
    result = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "surface"}, indent=2
        )
    )
    if result.get("blocker") or any(
        isinstance(value, dict)
        and value.get("status") in {"blocked", "authentication_blocked"}
        for value in result.values()
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
