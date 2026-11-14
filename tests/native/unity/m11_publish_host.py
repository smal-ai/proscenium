"""Real production continuation host. One send, same work/session, no native/child allocation."""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import uvicorn
from copilot import CopilotClient
from copilot.session import CopilotSession
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.copilot.service import wire
from proscenium.services import Services

if TYPE_CHECKING:
    from tests.native.unity.m11_publish_snapshot import SESSION_ID, WORK_ID, snapshot
else:
    from m11_publish_snapshot import SESSION_ID, WORK_ID, snapshot

REPO = Path(__file__).resolve().parents[3]
INSTALLED = Path("/home/quintin/play/proscenium")
ORIGINAL = Path("/home/quintin/.cache/proscenium-development/milestones/m11-20261114-native-a")
NO_NEW_WORK = {
    "run_unity_workflow",
    "request_preview",
    "submit_generation",
    "request_vlm_review",
    "assemble_sequence",
    "admit_job",
    "retry_job",
    "update_job_state",
}


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def process_identity(pid):
    row = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return dict(pid=pid, start=row[19])


def main(control):
    control.mkdir(mode=0o700, parents=True, exist_ok=False)
    before = snapshot(ORIGINAL, control, "before")
    live = [
        row
        for row in before["invocations"]
        if row.get("state") not in {"completed", "failed", "cancelled", "stopped"}
    ]
    assert not live, f"Unresolved original invocations; no send authorised: {live}"
    work = next(w for w in before["work"] if w["work_id"] == WORK_ID)
    assert work["state"] == "awaiting_resume" and work["native_session_id"] == SESSION_ID
    shutil.copytree(INSTALLED / "apps/frontend/dist", control / "compiled-frontend")
    config = json.loads((ORIGINAL / "config.json").read_text())
    config.update(
        origin=f"http://127.0.0.1:{port()}", frontend_directory=str(control / "compiled-frontend")
    )
    config["runtime"].update(
        cli_path=str(INSTALLED / "node_modules/@github/copilot-linux-x64/copilot"),
        skill_directories=[str(REPO / "agent/skills")],
        mcp_servers={},
    )
    (control / "config.json").write_text(json.dumps(config, indent=2))
    (control / "config.json").chmod(0o600)
    services = Services.create(Settings.load(control / "config.json"))
    app = create_app(services.settings, services=services, manage_features=True)
    buffered = []
    lock = threading.Lock()
    started = {}
    send_count = 0
    original_resume = CopilotClient.resume_session
    original_send = CopilotSession.send

    async def no_create(*args, **kwargs):
        raise RuntimeError("No fallback conversation is allocated; original session must resume")

    async def resume(client, session_id, **settings):
        assert session_id == SESSION_ID and settings.get("continue_pending_work") is False
        settings.update(custom_agents=[], excluded_tools=["task"])
        settings["tools"] = [tool for tool in settings["tools"] if tool.name not in NO_NEW_WORK]
        message = dict(settings["system_message"])
        message["content"] += (
            "\nPUBLICATION-ONLY ALLOCATION: Resume the ORIGINAL publication request/work. The original Unity specialist already completed inspection and verification. Its parent task was interrupted, not successful. The lead must directly finish the pending source checkpoint and unity_result publication under this existing work/session. No new task/delegation/children or new Queue. No Unity/Blender/export/authoring/render/provider/model-service/installation actions. Existing import validation remains not_run; only the separately registered operator F05 attestation supports passed PlayMode. Do not change saved scene, meta, Assets, Packages or ProjectSettings bytes just to publish. Verify the existing attestation and GUIDs, use production checkpoint_sources/publish_outputs and the original requested item/version/manifest. No private SQLite reads. One continuation only, finish within 290 seconds."
        )
        settings["system_message"] = message
        event = settings["on_event"]

        def observed(value):
            value = wire(value)
            if not value.get("ephemeral"):
                with lock:
                    buffered.append(value)
            event(value)

        settings["on_event"] = observed
        return await original_resume(client, session_id, **settings)

    async def send(session, *args, **kwargs):
        nonlocal send_count
        assert session.session_id == SESSION_ID
        assert send_count == 0, (
            "One SDK continuation send exhausted; never retry uncertain delivery"
        )
        send_count += 1
        started["at"] = time.monotonic()
        (control / "continuation-send.json").write_text(
            json.dumps(
                dict(session_id=session.session_id, args=args, kwargs=kwargs, at=time.time()),
                default=str,
                indent=2,
            )
        )
        return await original_send(session, *args, **kwargs)

    patch.object(CopilotClient, "create_session", no_create).start()
    patch.object(CopilotClient, "resume_session", resume).start()
    patch.object(CopilotSession, "send", send).start()
    (control / "pairing.url").write_text(
        f"{config['origin']}/#pair={services.auth.issue_pairing()}"
    )
    (control / "pairing.url").chmod(0o600)
    cdp = port()
    log = (control / "browser.private.log").open("w")
    browser = subprocess.Popen(
        [
            str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
            "--headless",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            f"--remote-debugging-port={cdp}",
            f"--user-data-dir={control / 'browser-profile'}",
            "about:blank",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (control / "processes.json").write_text(
        json.dumps(
            dict(
                root=str(control),
                original=str(ORIGINAL),
                origin=config["origin"],
                cdp=cdp,
                dependencies=str(INSTALLED / "node_modules"),
                host=process_identity(os.getpid()),
                browser=process_identity(browser.pid),
            ),
            indent=2,
        )
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=int(config["origin"].rsplit(":", 1)[1]), workers=1
        )
    )
    stopping = threading.Event()

    def monitor():
        deadline = time.monotonic() + 1800
        while not stopping.wait(0.25):
            with lock:
                pending = buffered[:]
                buffered.clear()
            if pending:
                with (control / "events.private.jsonl").open("a") as output:
                    for event in pending:
                        output.write(json.dumps(event, default=str) + "\n")
            if (
                (control / "STOP").exists()
                or time.monotonic() > deadline
                or started
                and time.monotonic() - started["at"] > 300
            ):
                server.should_exit = True
                return

    thread = threading.Thread(target=monitor, name="m11-publication-observer", daemon=True)
    thread.start()
    try:
        server.run()
    finally:
        stopping.set()
        thread.join()
        browser.terminate()
        try:
            browser.wait(15)
        except subprocess.TimeoutExpired:
            browser.kill()
            browser.wait()
        services.close()
        log.close()
        (control / "cleanup.json").write_text(
            json.dumps(
                dict(
                    browser_reaped=browser.returncode is not None,
                    services_closed=services.ownership.file.closed,
                    sdk_sends=send_count,
                ),
                indent=2,
            )
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
