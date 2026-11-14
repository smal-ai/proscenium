"""Disposable composed product server for the explicitly allocated SSH proof only."""

import argparse
import json
import signal
import subprocess
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import uvicorn
from proscenium.api.composition import configure_services
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.copilot import install
from proscenium.services import Services

from .recovery import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root
    spec = json.loads((root / "allocation.json").read_text())
    signal.alarm(280)
    counters = {"native": 0, "provider_http": 0, "non_git_process": 0, "sdk_factory": 0}
    audit = []

    class TransportAudit:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] not in {"http", "websocket"}:
                return await self.app(scope, receive, send)
            headers = dict(scope["headers"])
            record = {
                "type": scope["type"],
                "path": scope["path"],
                "method": scope.get("method"),
                "exact_png_range": headers.get(b"range") == b"bytes=0-15",
            }

            async def observed(message):
                if message["type"] in {"http.response.start", "websocket.accept"}:
                    record["status"] = message.get("status", 101)
                await send(message)

            try:
                await self.app(scope, receive, observed)
            finally:
                audit.append(record)

    def forbidden(kind):
        def fail(*_args, **_kwargs):
            counters[kind] += 1
            raise AssertionError("SSH proof execution boundary")

        return fail

    class NoNative:
        open = forbidden("native")
        health = forbidden("native")
        reconcile_process = forbidden("native")

        async def close(self):
            pass

    original_popen = subprocess.Popen

    def git_only(command, *args, **kwargs):
        if not isinstance(command, list | tuple) or Path(command[0]).name != "git":
            return forbidden("non_git_process")()
        return original_popen(command, *args, **kwargs)

    settings = Settings(
        data_directory=root / "machine",
        origin=spec["origin"],
        frontend_directory=Path(spec["frontend"]),
        services=(),
        runtime={},
    )
    services = Services.create(settings)
    install(services, native=NoNative())
    configure_services(services)
    write_json(root / "pairing-private.json", {"code": services.auth.issue_pairing()})
    with ExitStack() as stack:
        stack.enter_context(patch("httpx.Client.send", forbidden("provider_http")))
        stack.enter_context(patch("httpx.AsyncClient.send", forbidden("provider_http")))
        stack.enter_context(
            patch("proscenium.copilot.native.CopilotClient", forbidden("sdk_factory"))
        )
        stack.enter_context(patch("subprocess.Popen", git_only))
        try:
            app = create_app(settings, services=services, manage_features=True)
            app.add_middleware(TransportAudit)
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=spec["backend_port"],
                workers=1,
                access_log=False,
                log_level="critical",
            )
        finally:
            project = services.registry.active_id()
            empty = {}
            if project:
                with services.registry.project_store(project).transaction(write=False) as tx:
                    empty = {
                        name: len(tx.all(name))
                        for name in ("jobs", "work", "requests", "copilot.session")
                    }
                assert project not in services.gate._open
            services.close()
            write_json(
                root / "execution-guards.json",
                {
                    "attempts": counters,
                    "records": empty,
                    "gate_closed": True,
                    "transport_audit": audit,
                },
            )
            assert not any(counters.values())


if __name__ == "__main__":
    main()
