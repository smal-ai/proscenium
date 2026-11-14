"""Allocated M08/M12 real host. Fixtures are copied before SDK execution, not rendered here."""

import argparse
import fcntl
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from PIL import Image
from proscenium.api.dependencies import owner
from proscenium.api.errors import DomainError
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.previews.journal import alive, identity, read, sha
from proscenium.services import Services
from proscenium.sources.common import git

REPO = Path(__file__).resolve().parents[3]
BASE = Path.home() / ".cache/proscenium-development/milestones"
CLAIM = Path("/tmp/opencode/proscenium-native-claims/blender-render.lock")


def write(path: Path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def headroom():
    available = (
        int(
            next(
                line.split()[1]
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            )
        )
        * 1024
    )
    if available < 4 * 1024**3:
        raise RuntimeError("Allocated four GiB host headroom is unavailable")
    return available


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def snapshot(project):
    return {
        "head": git(project, "rev-parse", "HEAD").decode().strip(),
        "files": {
            name: sha((project / name).read_bytes())
            for name in git(project, "ls-tree", "-r", "--name-only", "HEAD").decode().splitlines()
        },
        "index_sha256": sha((project / ".git/index").read_bytes()),
    }


def prepare(root):
    from proscenium.catalogue import install

    headroom()
    CLAIM.parent.mkdir(parents=True, exist_ok=True)
    with CLAIM.open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = read(REPO / "config.example.json")
    config.update(
        data_directory=str(root / "machine"),
        origin=f"http://127.0.0.1:{free_port()}",
        frontend_directory=str(REPO / "apps/frontend/dist"),
        services=[],
    )
    config["runtime"].update(
        cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
        skill_directories=[str(REPO / "agent/skills")],
        mcp_servers={},
    )
    config["rendering"].update(
        max_frames=12, max_pixels=640 * 360, max_samples=8, claim_path=str(CLAIM)
    )
    write(root / "config.private.json", config)
    (root / "config.private.json").chmod(0o600)
    services = Services.create(Settings.load(root / "config.private.json"))
    try:
        install(services)
        projects = services.extension("projects")
        caller = Caller("owner", "m08-scripted-test-owner")
        project = root / "project"
        projects.open(
            caller,
            dict(
                schema_version="1.0",
                operation_id="m08-create",
                path=str(project),
                create=True,
                name="M08 model-authored motion over labelled F01 fixture",
            ),
        )
        project_id = read(project / "project.json")["project_id"]
        projects.activate(
            caller,
            dict(
                schema_version="1.0",
                operation_id="m08-activate",
                project_id=project_id,
                expected_revision=0,
            ),
        )
        original = Path("/tmp/opencode/proscenium-f01-20261114-a")
        fixture = read(original / "fixture-evidence.json")
        for row in fixture["historical_files"]:
            data = (original / "historical" / row["path"]).read_bytes()
            assert sha(data) == row["sha256"]
            target = project / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (project / "tools").mkdir()
        for source, target in (
            ("m08_contract.py", "m08_contract.py"),
            ("m08_author.py", "m08_author.py"),
        ):
            shutil.copyfile(Path(__file__).with_name(source), project / "tools" / target)
        (project / "contracts").mkdir()
        shutil.copyfile(REPO / "docs/contracts/schema.json", project / "contracts/schema.json")
        (project / "AGENTS.md").write_text(
            "# M08 bounded test owner policy\nImported F01 scene/assets are labelled fixtures, not your authored animation. Write only the requested NEW canonical motion scene, plan and sequence. Never edit imported scene or assets, .env, private SQLite or runtime stores. No children or authoring MCP startup. No GPU/providers/Unity/services. Run model-authored Blender recipes ONLY through python3 tools/m08_author.py, which enforces two processes/four CPU threads/180 seconds and four-GiB headroom. Never render in authoring recipes. Presentation goes ONLY through publish_outputs/request_preview, CPU8samples640x360. Do not insert sleeps, render handlers, synthetic frames or artificial throttling. Canonical published sources stay pinned. Later test-owner texture edits are unrelated working changes, not permission to stage them.\n"
        )
        git(project, "config", "user.name", "M08 labelled fixture import")
        git(project, "config", "user.email", "m08@example.invalid")
        git(
            project,
            "add",
            "--",
            ".gitignore",
            ".gitattributes",
            "project.json",
            "AGENTS.md",
            "tools",
            "contracts",
            *[row["path"] for row in fixture["historical_files"]],
        )
        git(
            project,
            "commit",
            "-m",
            "test fixture: explicitly imported F01 inputs and read-only contract/allocation helpers",
        )
        write(
            root / "fixture.json",
            {
                "project_id": project_id,
                "project": str(project),
                "label": "Imported fixture only. No authored motion/plan/frame/job is preseeded.",
                "files": fixture["historical_files"],
                "baseline": snapshot(project),
                "headroom_bytes": headroom(),
            },
        )
        shutil.copyfile(Path(__file__).with_name("m08_prompt.txt"), root / "prompt.txt")
    finally:
        services.close()


def serve(root):
    import uvicorn
    from proscenium.api.composition import configure_services

    settings = Settings.load(root / "config.private.json")
    services = Services.create(settings)
    configure_services(services)
    fixture = read(root / "fixture.json")
    project_id = fixture["project_id"]
    project = Path(fixture["project"])
    previews = services.extension("previews")
    catalogue = services.extension("catalogue")
    real_publish, real_preview = catalogue.publish, previews.request

    def reserve(specs):
        ledger = BASE / "m08-a2a2097-frames.jsonl"
        with ledger.open("a+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.seek(0)
            rows = [json.loads(line) for line in stream if line.strip()]
            for spec in specs:
                key = {"root": str(root), "intent_id": spec["intent_id"]}
                if any(row["identity"] == key for row in rows):
                    continue
                assert (
                    spec["dimensions"] == {"width": 640, "height": 360}
                    and spec["device"] == "CPU"
                    and spec["samples"] == 8
                )
                count = (
                    0
                    if spec["purpose"] == "proxy"
                    else spec["frames"]["end"] - spec["frames"]["start"] + 1
                )
                assert sum(row["frames"] for row in rows) + count <= 18, (
                    "Allocated 18-frame reservation exhausted"
                )
                assert spec["purpose"] != "proxy" or not any(row["proxy"] for row in rows), (
                    "One proxy export only"
                )
                row = {"identity": key, "frames": count, "proxy": spec["purpose"] == "proxy"}
                rows.append(row)
                stream.write(json.dumps(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def publish(caller, body):
        reserve([spec for item in body["items"] for spec in item["preview_requests"]])
        return real_publish(caller, body)

    def preview(caller, body):
        reserve([body["spec"]])
        return real_preview(caller, body)

    catalogue.publish = publish
    previews.request = preview
    routes = APIRouter()

    def state():
        with services.registry.project_store(project_id).transaction(write=False) as tx:
            result: dict[str, Any] = {
                name: tx.all(name)
                for name in (
                    "requests",
                    "contexts",
                    "work",
                    "jobs",
                    "attempts",
                    "frames",
                    "segments",
                    "annotations",
                    "publications",
                    "versions",
                    "items",
                    "artifacts",
                    "invocations",
                )
            }
            result["watermark"] = tx.watermark()
            result["outbox"] = [
                dict(row) for row in tx.connection.execute("SELECT id,kind,state FROM outbox")
            ]
        result["host_ns"] = time.monotonic_ns()
        result["owner_epoch"] = services.ownership.epoch
        try:
            services.gate.require(project_id)
            result["gate_open"] = True
        except DomainError:
            result["gate_open"] = False
        result["sdk_object_created"] = services.extension("copilot").native is not None
        result["availability"] = {
            job["job_id"]: previews.availability(project_id, job["job_id"])
            for job in result["jobs"]
            if job["render_spec"] and job["render_spec"]["purpose"] != "proxy"
        }
        return result

    @routes.get("/m08-harness/state")
    async def get_state(final: bool = False, caller: Caller = Depends(owner)):
        result = state()
        if final:
            result["native_user_messages"] = []
            result["native_tool_starts"] = []
            result["native_child_events"] = 0
            for session in services.extension("copilot").sessions.values():
                for event in await session.get_raw_events():
                    if event["type"] == "user.message":
                        result["native_user_messages"].append(
                            {"type": event["type"], "data": event["data"]}
                        )
                    elif event["type"] == "tool.execution_start":
                        result["native_tool_starts"].append(
                            {
                                k: v
                                for k, v in event["data"].items()
                                if k in ("toolName", "arguments", "toolCallId")
                            }
                        )
                    elif event["type"].startswith("subagent."):
                        result["native_child_events"] += 1
        return result

    @routes.post("/m08-harness/mark")
    async def mark(request: Request, caller: Caller = Depends(owner)):
        body = await request.json()
        current = state()
        motion = next(
            (
                job
                for job in current["jobs"]
                if job["render_spec"] and job["render_spec"]["intent_id"] == "m08-motion"
            ),
            None,
        )
        record = {
            "label": body["label"],
            "body": body,
            "host_ns": time.monotonic_ns(),
            "watermark": current["watermark"],
            "annotations": current["annotations"],
        }
        if motion:
            job_root = previews.root(motion)
            record["motion_job"] = motion
            record["last_png_exists"] = (job_root / "frames/00000012.png").exists()
            record["last_ready_ns"] = (
                read(job_root / "timing-00000012.json")["ready_ns"]
                if (job_root / "timing-00000012.json").exists()
                else None
            )
        write(root / (body["label"] + ".json"), record)
        return record

    @routes.post("/m08-harness/crash")
    async def crash(request: Request, caller: Caller = Depends(owner)):
        body = await request.json()
        current = state()
        job = services.jobs.get(project_id, body["job_id"])
        job_root = previews.root(job)
        native = read(job_root / "native-process.json")
        supervisor = read(job_root / "process.json")
        if not alive(native) or not alive(supervisor):
            raise DomainError(
                "invalid_request", 409, "Owned worker already finished; no crash was induced"
            )
        if (job_root / f"frames/{job['render_spec']['frames']['end']:08d}.png").exists():
            raise DomainError(
                "invalid_request", 409, "The last frame already exists; no recovery progress claim"
            )
        assert current["annotations"] and current["segments"], (
            "Need actual ready segment and saved annotation first"
        )
        # A genuine independently admitted output is parked behind the active renderer.
        store = services.registry.project_store(project_id)
        with store.transaction() as tx:
            work_id = "m08-labelled-held-work"
            invocation = "m08-labelled-held-invocation"
            tx.put(
                "work",
                work_id,
                dict(
                    schema_version="1.0",
                    project_id=project_id,
                    work_id=work_id,
                    originating_request_id="m08-test-owner-held-output",
                    directive_request_ids=[],
                    state="running",
                    state_revision=0,
                    native_session_id=services.registry.get(project_id)["native_session_id"],
                    job_ids=[],
                    generation_task_id="m08-held-output",
                ),
                schema="WorkRecord",
            )
            tx.put(
                "invocations",
                invocation,
                {"work_id": work_id, "owner_epoch": services.ownership.epoch, "state": "running"},
            )
        spec = {
            **job["render_spec"],
            "intent_id": "m08-held-output",
            "purpose": "keyframe",
            "frames": {"start": 1, "end": 1},
        }
        held = previews.request(
            Caller(
                "tool",
                "m08-explicit-test-owner",
                project_id,
                work_id,
                services.ownership.epoch,
                operations=("requestPreview",),
                invocation_id=invocation,
            ),
            dict(
                schema_version="1.0",
                operation_id="m08-held-output",
                project_id=project_id,
                version=job["target"]["version"],
                spec=spec,
            ),
        )[0]
        with store.transaction() as tx:
            work = tx.require("work", work_id)
            work.update(state="completed", state_revision=1)
            tx.put("work", work_id, work, expected_revision=0, schema="WorkRecord")
        # Change only a live, owned, native texture after admission. The pinned
        # materialisation must continue using the original blue bytes.
        texture = project / "assets/cube/colour.png"
        before = sha(texture.read_bytes())
        Image.new("RGB", (8, 8), (32, 224, 64)).save(texture)
        record = {
            "host_ns": time.monotonic_ns(),
            "job": job,
            "native": native,
            "supervisor": supervisor,
            "task_sha256": sha((job_root / "task.json").read_bytes()),
            "before": current,
            "held_job": held,
            "live_texture_before": before,
            "live_texture_after": sha(texture.read_bytes()),
            "source_head": git(project, "rev-parse", "HEAD").decode().strip(),
        }
        assert alive(native), "Do not claim worker survival if it finished during checkpoint"
        write(root / "crash-checkpoint.json", record)

        def terminate():
            write(
                root / "backend-kill.json",
                {
                    "host_ns": time.monotonic_ns(),
                    "identity": identity(os.getpid()),
                    "signal": "SIGKILL",
                },
            )
            os.kill(os.getpid(), signal.SIGKILL)  # This backend PID only, never its worker group.

        threading.Timer(0.025, terminate).start()
        return {"crash_scheduled": True, "host_ns": record["host_ns"]}

    app = create_app(settings, services=services, routers=[routes], manage_features=True)
    if not (root / "pairing.private.url").exists():
        (root / "pairing.private.url").write_text(
            settings.origin + "/#pair=" + services.auth.issue_pairing()
        )
        (root / "pairing.private.url").chmod(0o600)
    try:
        from urllib.parse import urlsplit

        port = urlsplit(settings.origin).port
        assert port
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        services.close()


def run(root, phase):
    assert root.is_relative_to(BASE)
    if phase == "initial":
        root.mkdir(parents=True, mode=0o700, exist_ok=False)
        prepare(root)
    else:
        assert (root / "passive-recovery.json").is_file(), (
            "Inspect original recovery before explicit continuation"
        )
    temporary = Path.home() / ".cache" / ("m08-" + sha(str(root).encode())[:10])
    temporary.mkdir(mode=0o700, exist_ok=True)
    environment = {**os.environ, "TMPDIR": str(temporary)}
    server = None
    node = None
    restarts = []

    def launch(label):
        with (root / (label + ".private.log")).open("wb") as log:
            child = subprocess.Popen(
                [sys.executable, __file__, "serve", str(root)],
                cwd=REPO,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=environment,
            )
        restarts.append({"label": label, "identity": identity(child.pid)})
        return child

    try:
        server = launch("server-" + phase)
        with (root / ("browser-" + phase + ".log")).open("wb") as log:
            node = subprocess.Popen(
                ["node", "tests/acceptance/m08_browser.mjs", str(root), phase],
                cwd=REPO,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=environment,
            )
        deadline = time.monotonic() + 560
        restarted = False
        while node.poll() is None and time.monotonic() < deadline:
            if server.poll() is not None:
                if (
                    server.returncode == -signal.SIGKILL
                    and (root / "backend-kill.json").exists()
                    and not restarted
                ):
                    restarts[-1]["observed_exit_code"] = server.returncode
                    server = launch("server-passive-restart")
                    restarted = True
                else:
                    raise RuntimeError("Owned server exited unexpectedly")
            time.sleep(0.02)
        if node.poll() is None:
            os.killpg(node.pid, signal.SIGTERM)
            node.wait(timeout=15)
            raise TimeoutError("Bounded browser phase timed out")
        if node.returncode:
            raise RuntimeError(f"Browser phase failed. Inspect {root}")
    finally:
        if server and server.poll() is None:
            actual = identity(server.pid)
            saved = restarts[-1]["identity"]
            if actual and saved and actual["start_ticks"] == saved["start_ticks"]:
                os.kill(server.pid, signal.SIGTERM)
                server.wait(timeout=45)
        # Detached presentation work is not cancelled by backend shutdown. Retain
        # and wait for its own bounded terminal journal, including on UI failure.
        workers = root / "project/.proscenium/jobs/previews"
        deadline = time.monotonic() + 360
        while workers.exists() and time.monotonic() < deadline:
            live = [path for path in workers.glob("*/*/process.json") if alive(read(path))]
            if not live:
                break
            time.sleep(0.05)
        write(
            root / ("cleanup-" + phase + ".json"),
            {
                "servers": restarts,
                "server_exited": server is not None and server.poll() is not None,
                "browser_driver_exited": node is not None and node.poll() is not None,
            },
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "serve"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--phase", choices=("initial", "assembly"), default="initial")
    args = parser.parse_args()
    (serve(args.root.resolve()) if args.action == "serve" else run(args.root.resolve(), args.phase))
