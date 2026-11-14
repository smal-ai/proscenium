"""Bounded real CLI/ASGI smoke, explicitly run after make build, never a native proof.

Native SDK construction, provider HTTP and preview launch are hard-failed in the
owned test process before they can execute. No browser/model/desktop is started.
"""

import json
import os
import selectors
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[3]


def test_real_cli_owner_pairing_project_snapshot_socket_and_compiled_assets(tmp_path):
    assert (ROOT / "apps/frontend/dist/index.html").is_file(), "Run make build first"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    config = json.loads((ROOT / "config.example.json").read_text())
    config.update(data_directory=str(tmp_path / "machine"), origin=origin)
    config_path = tmp_path / "operator.json"
    config_path.write_text(json.dumps(config))
    config_path.chmod(0o600)
    forbidden = tmp_path / "forbidden-execution"
    guard = f"""
from pathlib import Path
import httpx
from copilot import CopilotClient
from proscenium.copilot.native import Native
from proscenium.previews import runner
from proscenium.__main__ import main
def deny(*args, **kwargs):
    Path({str(forbidden)!r}).write_text('Unexpected native/provider execution')
    raise AssertionError('No native/provider execution is allocated')
async def deny_async(*args, **kwargs):
    deny()
Native.open = deny_async
CopilotClient.__init__ = deny
httpx.Client.request = deny
runner.launch = runner.launch_assembly = deny
main()
"""
    process = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-c", guard, "--config", str(config_path), "--pair"],
        cwd=ROOT,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "CLI did not produce its trusted operator URL"
        private_url = process.stdout.readline().strip().split()[-1]
        code = urlsplit(private_url).fragment.removeprefix("pair=")
        with httpx.Client(base_url=origin, trust_env=False, timeout=3) as browser:
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None, "Owned CLI exited before serving"
                try:
                    if browser.get("/health").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                assert time.monotonic() < deadline, "CLI startup deadline exceeded"
                time.sleep(0.025)
            assert browser.get("/").status_code == 200
            assert browser.get("/licences/fonts.txt").status_code == 200
            assert browser.get("/api/v1/projects").status_code == 401
            paired = browser.post(
                "/api/v1/owner/session",
                json=dict(
                    schema_version="1.0",
                    operation_id="smoke-pair",
                    client_nonce="smoke-browser",
                    pairing_code=code,
                ),
                headers={"Origin": origin, "Idempotency-Key": "smoke-pair"},
            )
            assert paired.status_code == 201
            headers = {"Origin": origin, "X-CSRF-Token": paired.json()["csrf_token"]}
            assert browser.get("/api/v1/configuration").status_code == 200
            opened = browser.post(
                "/api/v1/projects",
                headers={**headers, "Idempotency-Key": "smoke-open"},
                json=dict(
                    schema_version="1.0",
                    operation_id="smoke-open",
                    path=str(tmp_path / "workspace"),
                    create=True,
                    name="Disposable CLI smoke",
                ),
            )
            assert opened.status_code == 201
            project = opened.json()
            active = browser.post(
                f"/api/v1/projects/{project['project_id']}/activate",
                headers={**headers, "Idempotency-Key": "smoke-activate"},
                json=dict(
                    schema_version="1.0",
                    operation_id="smoke-activate",
                    project_id=project["project_id"],
                    expected_revision=project["state_revision"],
                ),
            )
            assert active.status_code == 200
            snapshot = browser.get(f"/api/v1/projects/{project['project_id']}/snapshot")
            assert snapshot.status_code == 200
            assert snapshot.json()["project"]["native_session_id"] is None
            assert snapshot.json()["project"]["state"] == "awaiting_resume"
            cookie = "; ".join(f"{key}={value}" for key, value in browser.cookies.items())
            with connect(
                origin.replace("http", "ws") + "/api/v1/events",
                origin=origin,
                additional_headers={"Cookie": cookie},
                proxy=None,
                open_timeout=3,
            ) as stream:
                stream.send(
                    json.dumps(
                        dict(
                            schema_version="1.0",
                            type="subscribe",
                            project_id=project["project_id"],
                            after_sequence=0,
                        )
                    )
                )
                assert json.loads(stream.recv(timeout=3))["type"] == "welcome"
                assert json.loads(stream.recv(timeout=3))["event_type"] == "project.state_changed"
                assert json.loads(stream.recv(timeout=3))["type"] == "caught_up"
            assert browser.get("/health").status_code == 200
        assert not forbidden.exists(), "Quiet startup crossed an external-execution boundary"
    finally:
        process.terminate()
        try:
            _, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()  # This exact owned API process only. No desktop/worker was allocated.
            process.communicate(timeout=3)
            raise
    assert "Application shutdown complete" in stderr
