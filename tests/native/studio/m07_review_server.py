"""Production composed app, zero-effect guard, private disposable restoration only."""

import asyncio
import json
import signal
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import uvicorn
from copilot.session import CopilotSession
from proscenium.api.composition import configure_services
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.services import Services


class ReadOnlyGuard:
    def __init__(self, app, audit):
        self.app, self.audit = app, audit

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path, method = scope["path"], scope["method"]
            allowed = method in {"GET", "HEAD", "OPTIONS"} or (
                method == "POST" and (path == "/api/v1/owner/session" or path.endswith("/activate"))
            )
            row = {"path": path, "method": method, "allowed": allowed}
            self.audit["requests"].append(row)
            if not allowed:
                self.audit["forbidden"].append("http_mutation:" + path)
                await send({"type": "http.response.start", "status": 403, "headers": []})
                await send({"type": "http.response.body", "body": b"M07 read-only allocation"})
                return

            async def observed(message):
                if message["type"] == "http.response.start":
                    row["status"] = message["status"]
                await send(message)

            return await self.app(scope, receive, observed)
        return await self.app(scope, receive, send)


def main(root):
    signal.alarm(290)
    audit: dict[str, Any] = {"forbidden": [], "requests": [], "sdk_sends": 0, "provider_sends": 0}

    def blocked(kind):
        def fail(*args, **kwargs):
            audit["forbidden"].append(kind)
            raise RuntimeError("M07 zero-effect fixture guard: " + kind)

        return fail

    settings = Settings.load(root / "config.json")
    assert not settings.services and not settings.execution_profiles
    assert not Path(settings.runtime["cli_path"]).exists()
    services = Services.create(settings)
    configure_services(services)
    app = create_app(settings, services=services, manage_features=True)
    app.add_middleware(ReadOnlyGuard, audit=audit)
    (root / "pair.private").write_text(settings.origin + "/#pair=" + services.auth.issue_pairing())
    (root / "pair.private").chmod(0o600)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18969, log_level="warning"))
    # Uvicorn's ordinary handler re-raises SIGTERM after shutdown. Keep the final
    # audit in this process's finally, including on an externally requested stop.
    signal.signal(signal.SIGTERM, lambda *_: setattr(server, "should_exit", True))
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(server, "capture_signals", ExitStack))
            for target, name, kind in (
                (subprocess, "Popen", "subprocess"),
                (asyncio, "create_subprocess_exec", "async_subprocess"),
                (httpx.Client, "send", "provider_http"),
                (httpx.AsyncClient, "send", "async_provider_http"),
                (CopilotSession, "send", "sdk_send"),
            ):
                stack.enter_context(patch.object(target, name, blocked(kind)))
            server.run()
    finally:
        audit["gate_closed"] = not services.gate._open
        lifecycle = services.extensions.get("lifecycle")
        audit["lifecycle_errors"] = lifecycle.errors if lifecycle else None
        audit["services"] = list(settings.services)
        audit["execution_profiles"] = list(settings.execution_profiles)
        (root / "server-audit.json").write_text(json.dumps(audit, indent=2) + "\n")
        services.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
