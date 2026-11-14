"""One read/review-only production backend. Explicit allocation, no native/runtime worker."""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi.responses import JSONResponse
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.copilot.service import install as install_copilot
from proscenium.db import Store
from proscenium.services import Services


def prepare_registry(root: Path) -> None:
    """Rehome only the copied location with its real storage/domain CAS revision."""
    config = json.loads((root / "project/project.json").read_text())
    project_id = config["project_id"]
    machine = Store(root / "machine/registry.sqlite3")
    project = Store(root / "project/.proscenium/state.sqlite3", project_id)
    with project.transaction(write=False) as tx:
        association = tx.require("copilot.session", "native")
        projection_ids = [
            row[0]
            for row in tx.connection.execute(
                "SELECT id FROM operations WHERE scope='host.project_projection'"
            )
        ]
    with machine.transaction() as tx:
        previous = tx.require("projects", project_id)
        storage_revision = tx.revision("projects", project_id)
        assert storage_revision == previous["state_revision"]
        assert previous["native_session_id"] == association["session_id"]
        assert previous["repository_id"] == config["repository_id"]
        assert previous["state"] == "awaiting_resume"
        pending = tx.get("host.project_projection", project_id, required=False)
        assert not pending or pending["applied"]
        updated = {
            **previous,
            "path": str(root / "project"),
            "state_revision": storage_revision + 1,
        }
        assert f"{project_id}:{updated['state_revision']}" not in projection_ids
        tx.put(
            "projects",
            project_id,
            updated,
            expected_revision=storage_revision,
            schema="ProjectRecord",
        )
        # Never reuse retained owner cookies/capabilities; values are not read.
        tx.connection.execute("DELETE FROM records WHERE namespace LIKE 'auth.%'")
        proof = {
            "project_id": project_id,
            "storage_revision_before": storage_revision,
            "domain_revision_before": previous["state_revision"],
            "storage_revision_after": tx.revision("projects", project_id),
            "domain_revision_after": updated["state_revision"],
            "native_id_before": previous["native_session_id"],
            "native_id_after": updated["native_session_id"],
            "old_path": previous["path"],
            "new_path": updated["path"],
            "native_association_preserved": True,
            "historical_projection_ids": projection_ids,
            "copied_auth_rows_erased": True,
        }
    (root / "copy-registry-proof.json").write_text(json.dumps(proof, indent=2))


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    if len(sys.argv) > 2 and sys.argv[2] == "--prepare":
        prepare_registry(root)
        return
    repo = Path(__file__).resolve().parents[2]
    audit: dict = {
        "sdk_factories": [],
        "native_calls": [],
        "provider_http": [],
        "blocked_mutations": [],
        "allowed_mutations": [],
        "git_processes": 0,
    }
    (root / "server-audit.json").write_text(json.dumps(audit))

    def blocked_sdk(*args, **kwargs):
        audit["sdk_factories"].append("attempted")
        raise AssertionError("No SDK client/root is authorised")

    import copilot
    import proscenium.copilot.native as native

    copilot.CopilotClient = blocked_sdk  # type: ignore[assignment]
    native.CopilotClient = blocked_sdk  # type: ignore[assignment]

    class BlockedNative:
        def __getattr__(self, name):
            audit["native_calls"].append(name)
            raise AssertionError("Native/runtime factories are hard-blocked")

    def blocked_http(*args, **kwargs):
        audit["provider_http"].append("attempted")
        raise AssertionError("Provider/network clients are hard-blocked")

    httpx.Client.request = blocked_http  # type: ignore[method-assign]
    httpx.AsyncClient.request = blocked_http  # type: ignore[method-assign,assignment]
    original_popen = subprocess.Popen

    def guarded_popen(argv, *args, **kwargs):
        if not isinstance(argv, (list, tuple)) or Path(str(argv[0])).name != "git":
            audit["native_calls"].append("subprocess")
            raise AssertionError("Only scoped Git source reads are authorised")
        audit["git_processes"] += 1
        return original_popen(argv, *args, **kwargs)

    subprocess.Popen = guarded_popen  # type: ignore[misc,assignment]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    settings = Settings(
        data_directory=root / "machine",
        origin=origin,
        frontend_directory=repo / "apps/frontend/dist",
        execution_profiles=frozenset(),
        services=(),
        runtime={},
        provider_options={},
    )
    services = Services.create(settings)
    install_copilot(services, native=BlockedNative())
    active = services.registry.active_id()
    if active:
        services.extensions["copilot"].restore(active)
    app = create_app(settings, services=services, manage_features=False)

    @app.middleware("http")
    async def allocation_guard(request, call_next):
        path = request.url.path
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            allowed = (
                path == "/api/v1/owner/session"
                or path == "/api/v1/projects"
                or (
                    path.startswith("/api/v1/projects/")
                    and (
                        path.endswith("/activate")
                        or path.endswith("/annotations")
                        or "/uploads/" in path
                    )
                )
            )
            if not allowed:
                audit["blocked_mutations"].append({"method": request.method, "path": path})
                return JSONResponse({"error": {"code": "allocation_denied"}}, status_code=403)
            audit["allowed_mutations"].append({"method": request.method, "path": path})
        response = await call_next(request)
        return response

    private = {
        "origin": origin,
        "pairing_url": f"{origin}/#pair={services.auth.issue_pairing()}",
        "pid": os.getpid(),
        "root": str(root),
        "lifecycle_started": False,
    }
    (root / "control.private.json").write_text(json.dumps(private))
    (root / "control.private.json").chmod(0o600)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    def request_exit(*_):
        server.should_exit = True

    # Uvicorn re-raises captured signals after shutdown. Preserve our final audit.
    signal.signal(signal.SIGTERM, request_exit)
    signal.signal(signal.SIGINT, request_exit)
    stop = threading.Event()
    started = time.monotonic()

    def timeout() -> None:
        if not stop.wait(285):
            server.should_exit = True

    threading.Thread(target=timeout, daemon=True).start()
    try:
        server.run()
    finally:
        stop.set()
        audit.update(
            elapsed_seconds=time.monotonic() - started,
            lifecycle_started=False,
            copilot_delivery_task_started=services.extensions["copilot"].worker is not None,
            execution_profiles=[],
            services=[],
        )
        (root / "server-audit.json").write_text(json.dumps(audit, indent=2))
        services.close()


if __name__ == "__main__":
    main()
