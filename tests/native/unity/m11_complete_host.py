"""One actual owner stack and SDK continuation. Completed native publication replay only."""

import copy
import json
import os
import socket
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import uvicorn
from copilot import CopilotClient, CopilotSession
from proscenium.app import create_app
from proscenium.catalogue.service import Catalogue
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.copilot.service import wire
from proscenium.services import Services

if TYPE_CHECKING:
    from tests.native.unity.m11_complete_snapshot import (
        BUILD,
        HEAD,
        INSTALLED,
        ORIGINAL,
        REPO,
        REQUEST,
        ROOT,
        SESSION,
        WORK,
        checkpoint,
        headroom,
        identity,
        inspect,
        records,
        snapshot,
        write,
    )
else:
    from m11_complete_snapshot import (
        BUILD,
        HEAD,
        INSTALLED,
        ORIGINAL,
        REPO,
        REQUEST,
        ROOT,
        SESSION,
        WORK,
        checkpoint,
        headroom,
        identity,
        inspect,
        records,
        snapshot,
        write,
    )

EXCLUDED = {
    "task",
    "run_unity_workflow",
    "request_preview",
    "submit_generation",
    "request_vlm_review",
    "assemble_sequence",
    "admit_job",
    "retry_job",
    "update_job_state",
    "register_artifact",
    "checkpoint_sources",
}


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    assert (ROOT / "before.private.json").is_file()
    assert not (ROOT / "processes.json").exists(), "One app/browser stack, never restart"
    inspect()
    started = time.monotonic()
    send_at = None
    sends = 0
    config = json.loads((ORIGINAL / "config.json").read_text())
    config.update(origin=f"http://127.0.0.1:{port()}", frontend_directory=str(BUILD / "dist"))
    config["runtime"].update(
        cli_path=str(INSTALLED / "node_modules/@github/copilot-linux-x64/copilot"),
        skill_directories=[str(REPO / "agent/skills")],
        mcp_servers={},
        lead_model="gpt-6.1-sol",
    )
    assert config["services"] == []
    write(ROOT / "config.private.json", config)
    (ROOT / "config.private.json").chmod(0o600)
    services = Services.create(Settings.load(ROOT / "config.private.json"))
    app = create_app(services.settings, services=services, manage_features=True)
    lifecycle = app.router.lifespan_context

    @asynccontextmanager
    async def audited(application):
        async with lifecycle(application):
            yield
        write(
            ROOT / "lifespan-closed.json",
            dict(feature_lifespan_closed=True, errors=services.extension("lifecycle").errors),
        )

    app.router.lifespan_context = audited
    events = []
    lock = threading.Lock()
    native_resume, native_send, publish = (
        CopilotClient.resume_session,
        CopilotSession.send,
        Catalogue.publish,
    )

    async def no_create(*args, **kwargs):
        raise RuntimeError("No new native conversation authorised")

    async def resume(client, session_id, **kwargs):
        assert session_id == SESSION and kwargs.get("continue_pending_work") is False
        kwargs.update(custom_agents=[], included_builtin_agents=[], excluded_tools=["task"])
        kwargs["tools"] = [t for t in kwargs["tools"] if t.name not in EXCLUDED]
        message = dict(kwargs["system_message"])
        message["content"] += (
            "\nM11 ORIGINAL WORK PUBLICATION COMPLETION ALLOCATION. One continuation only, finish within 230 seconds. "
            "The original work was genuinely stopped and is now explicitly continued. Directly finish its publication as lead. "
            "NO new child/task/conversation/Queue, Unity Editor, Blender, export, render, encode, inference, provider/service change, installation, push or destructive action. "
            "Do not change any saved Unity source/meta/Assets/Packages/ProjectSettings, reports or original generic import result. "
            "The source checkpoint is ALREADY DONE, canonical operation m11-unity-source-checkpoint. Do NOT checkpoint or recommit unchanged sources. "
            f"Use the actual completed receipt below and its source commit {HEAD}. Source paths/hashes are the actual saved verified inputs, not synthetic examples. "
            "Use dynamic read-only Git listing/show or a generic Python hash helper if useful. No private SQLite or credential access. "
            "Publish with normal publish_outputs using publication_id m11-unity-publication, item_id m11-playable, version_id m11-playable-v1, "
            "kind unity_result, name M11 playable F01 Unity scene, expected_current_version_id null, derived_from/dependencies m11-source/m11-source-v1, no previews, "
            "artifact_ids m11-playmode-attestation,m11-playmode-xml,m11-playmode-metrics. Reuse an existing exact publication if already present, never replace history. "
            "Inspect actual saved artefact bytes and existing tool results as needed. The registered operator PlayMode attestation is passed. "
            "The generic import m11-import-job immutable attempt result remains not_run. Published UnityResult validation notes must explicitly state: "
            "Operator F05 PlayMode job m11-playmode-job passed on this saved scene; generic import m11-import-job validation was not_run. Virtual Input System polling tested; physical OS input not tested. "
            "Use the actual saved scene full project-relative path as entry_path, map ALL receipt files to exact Git origins at the receipt commit, "
            "required_runtime Unity 6000.3.27f1 features urp/character_controller. Use normative canonical SourceClosure digest and schema, not a second DTO. "
            "If needed for schema/hashing, the normal shell can import proscenium.contracts from the configured Python environment. "
            "After successful publication, give a non-empty final response naming real commit/publication/version and honest validation. Do not finish after intermediate tool batches. "
            "\nACTUAL COMPLETED CHECKPOINT RECEIPT:\n" + canonical(checkpoint()).decode()
        )
        kwargs["system_message"] = message
        handler = kwargs["on_event"]

        def observed(value):
            event = wire(value)
            if not event.get("ephemeral"):
                with lock:
                    events.append(event)
            handler(value)

        kwargs["on_event"] = observed
        write(
            ROOT / "session-allocation.json",
            dict(
                session_id=session_id,
                model=kwargs.get("model"),
                memory=kwargs.get("memory"),
                enable_session_store=kwargs.get("enable_session_store"),
                continue_pending_work=kwargs.get("continue_pending_work"),
                new_conversations=0,
                children=0,
            ),
        )
        return await native_resume(client, session_id, **kwargs)

    async def send(session, *args, **kwargs):
        nonlocal send_at, sends
        assert session.session_id == SESSION and sends == 0
        assert (ROOT / "ui-preflight.json").is_file()
        write(ROOT / "send-headroom.json", headroom())
        with (ROOT / "sdk-send-claimed.json").open("x") as out:
            json.dump(
                dict(session_id=SESSION, sends=1, work_id=WORK, request_id=REQUEST, at=time.time()),
                out,
            )
        sends += 1
        send_at = time.monotonic()
        result = await native_send(session, *args, **kwargs)
        write(ROOT / "sdk-send-ack.json", dict(message_id=result, session_id=SESSION))
        return result

    def observed_publish(catalogue, caller, body):
        result = publish(catalogue, caller, body)
        if body["publication_id"] == "m11-unity-publication":
            # Only AFTER the native tool's real effect, replay EXACT caller/body once.
            with (ROOT / "replay-claimed.json").open("x") as out:
                json.dump(
                    dict(invocation_id=caller.invocation_id, publication_id=body["publication_id"]),
                    out,
                )
            proof = dict(caller=asdict(caller), body=copy.deepcopy(body), initial=result)
            try:
                names = (
                    "versions",
                    "items",
                    "publications",
                    "jobs",
                    "attempts",
                    "checkpoint_intents",
                )
                before = {n: records(n) for n in names}
                repeated = publish(catalogue, caller, body)
                after = {n: records(n) for n in names}
                assert before == after
                assert repeated[0]["replayed"] is True
                proof.update(replay=repeated, records_unchanged=True)
            except Exception as error:
                proof["replay_error"] = repr(error)
            write(ROOT / "publication-replay.private.json", proof)
        return result

    patch.object(CopilotClient, "create_session", no_create).start()
    patch.object(CopilotClient, "resume_session", resume).start()
    patch.object(CopilotSession, "send", send).start()
    patch.object(Catalogue, "publish", observed_publish).start()
    (ROOT / "pairing.url").write_text(f"{config['origin']}/#pair={services.auth.issue_pairing()}")
    (ROOT / "pairing.url").chmod(0o600)
    cdp = port()
    log = (ROOT / "browser.private.log").open("w")
    browser = subprocess.Popen(
        [
            str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
            "--headless",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            "--window-size=1920,1080",
            f"--remote-debugging-port={cdp}",
            f"--user-data-dir={ROOT / 'browser-profile'}",
            "about:blank",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    write(
        ROOT / "processes.json",
        dict(
            host=identity(os.getpid()),
            browser=identity(browser.pid),
            origin=config["origin"],
            cdp=cdp,
            dependencies=str(INSTALLED / "node_modules"),
            started=time.time(),
            stack_bound_seconds=600,
            root_bound_seconds=240,
        ),
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=int(config["origin"].rsplit(":", 1)[1]), workers=1
        )
    )
    closing = threading.Event()

    def flush():
        with lock:
            pending = events[:]
            events.clear()
        if pending:
            with (ROOT / "events.private.jsonl").open("a") as out:
                for event in pending:
                    out.write(json.dumps(event, default=str) + "\n")

    def monitor():
        while not closing.wait(0.25):
            flush()
            process = ORIGINAL / "machine/copilot-runtime/process.json"
            if process.exists():
                write(ROOT / "native-process.private.json", json.loads(process.read_text()))
            if (
                (ROOT / "CLOSE").exists()
                or time.monotonic() - started > 570
                or send_at is not None
                and time.monotonic() - send_at > 240
            ):
                server.should_exit = True
                return

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        server.run()
    finally:
        closing.set()
        thread.join()
        flush()
        browser.terminate()
        try:
            browser.wait(10)
        except subprocess.TimeoutExpired:
            browser.kill()
            browser.wait(5)
        services.close()
        log.close()
        snapshot("after")
        write(
            ROOT / "cleanup.json",
            dict(
                sdk_sends=sends,
                browser_reaped=browser.returncode is not None,
                services_closed=services.ownership.file.closed,
                stack_seconds=time.monotonic() - started,
            ),
        )


if __name__ == "__main__":
    main()
