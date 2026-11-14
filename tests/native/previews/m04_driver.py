"""Explicitly allocated M04 host/browser/native proof. Not collected by offline pytest.

Only imported W03 fixture metadata is seeded. The browser request, SDK invocation,
new preview jobs, frame receipts, annotations and media are production effects.
"""

import argparse
import fcntl
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, Request
from proscenium.api.dependencies import owner
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.catalogue import install as catalogue_install
from proscenium.configuration import Settings
from proscenium.contracts import canonical, validate
from proscenium.previews.journal import identity, read, sha
from proscenium.services import Services
from proscenium.sources.common import git

REPO = Path(__file__).resolve().parents[3]
PROOF = Path("/tmp/opencode/proscenium-w03-native-a.json")
EVIDENCE = REPO / "docs/evidence/w05"
PROJECT = "w03-native"


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fixture_work(tx, services, label):
    work_id, invocation_id = f"m04-harness-{label}", f"m04-harness-invocation-{label}"
    tx.put(
        "work",
        work_id,
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            work_id=work_id,
            originating_request_id=f"m04-labelled-harness-{label}-not-cloud",
            directive_request_ids=[],
            state="running",
            state_revision=0,
            native_session_id=services.registry.get(PROJECT)["native_session_id"]
            or "m04-harness-not-an-sdk-session",
            job_ids=[],
            generation_task_id=f"m04-harness-task-{label}",
        ),
        schema="WorkRecord",
    )
    tx.put(
        "invocations",
        invocation_id,
        {"work_id": work_id, "owner_epoch": services.ownership.epoch, "state": "running"},
    )
    return Caller(
        "tool",
        f"m04-fixture-{label}",
        PROJECT,
        work_id,
        services.ownership.epoch,
        operations=("publishOutputs", "requestPreview"),
        invocation_id=invocation_id,
    )


def source_snapshot(root: Path):
    paths = git(root, "ls-tree", "-r", "--name-only", "HEAD").decode().splitlines()
    return {
        "head": git(root, "rev-parse", "HEAD").decode().strip(),
        "tracked_working_hashes": {name: sha((root / name).read_bytes()) for name in paths},
        "index_sha256": sha((root / ".git/index").read_bytes()),
    }


def prepare(root: Path):
    assert not any(root.iterdir()), "Use a fresh disk-backed allocation. Do not overwrite evidence."
    original = read(PROOF)
    source = Path(original["output_project"])
    project = root / "project"
    project.mkdir()
    shutil.copytree(source / ".git", project / ".git")
    for name in git(source, "ls-tree", "-r", "--name-only", "HEAD").decode().splitlines():
        assert not name.startswith((".env", ".proscenium/"))
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    for name in ("brief", "assets", "scenes", "storyboards", "sequences"):
        (project / name).mkdir(exist_ok=True)
    write(
        project / "project.json",
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            repository_id="w03-repo",
            name="M04 imported accepted W03 geometry fixture",
            paths={
                name: name for name in ("brief", "assets", "scenes", "storyboards", "sequences")
            },
        ),
    )
    (project / "AGENTS.md").write_text(
        "# Bounded M04 scripted owner request\n"
        "This is explicitly imported accepted W03 fixture geometry, NOT a scene you authored.\n"
        "Render this view means ONLY request_preview on the EXACT host context version, source frame, camera and intrinsic dimensions.\n"
        "Read get_version for the source digest. Use scene_name Fixture, purpose keyframe, engine CYCLES, device CPU, samples 8, "
        "renderer_revision proscenium-blender-1, fps 24/1, colour_management Standard/None/0/1, "
        "review_output image/png view_transformed_srgb bit_depth 8.\n"
        "The corrected PNG keyframe adapter supports odd dimensions. Preserve EXACT host target dimensions and camera. "
        "Do NOT round/pad/crop to even sizes, despite stale generic skill advice about motion. "
        "The capture is inspection/reference pixels, not a Blender material render.\n"
        "One preview only. Do not change any source, Git HEAD, scene, camera animation or metadata. No checkpoint/publication, "
        "no Blender authoring/MCP startup, no child agents, no provider/model/GPU/service changes. "
        "After admission give the job ID and finish. The independent worker handles rendering.\n"
    )
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
        worker_tools=[
            "request_preview",
            "get_version",
            "get_job",
            "get_artifact",
            "list_job_frames",
        ],
    )
    config["rendering"].update(
        max_frames=1,
        max_pixels=2063 * 1138,
        max_samples=8,
        claim_path="/tmp/opencode/proscenium-native-claims/blender-render.lock",
    )
    write(root / "config.private.json", config)
    (root / "config.private.json").chmod(0o600)
    services = Services.create(Settings.load(root / "config.private.json"))
    try:
        catalogue = catalogue_install(services)
        owner_caller = Caller("owner", "m04-scripted-owner")
        projects = services.extension("projects")
        projects.open(
            owner_caller,
            dict(
                schema_version="1.0",
                operation_id="m04-open-import",
                path=str(project),
                create=False,
            ),
        )
        projects.activate(
            owner_caller,
            dict(
                schema_version="1.0",
                operation_id="m04-activate",
                project_id=PROJECT,
                expected_revision=0,
            ),
        )
        services.gate.resume(
            PROJECT, reconciliation_evidence="Explicit scoped fixture import, no model request"
        )
        with services.registry.project_store(PROJECT).transaction() as tx:
            caller = fixture_work(tx, services, "import")
        proxy = next(
            item for item in original["jobs"] if item["job"]["render_spec"]["purpose"] == "proxy"
        )
        artifacts = services.extension("artifacts")
        for identity_, name, kind, mime in (
            (
                original["proxy_mapping"]["proxy_artifact_id"],
                "proxy.glb",
                "proxy",
                "model/gltf-binary",
            ),
            (
                original["proxy_mapping"]["proxy_artifact_id"] + "-map",
                "proxy-map.json",
                "proxy_map",
                "application/json",
            ),
        ):
            data = (Path(proxy["root"]) / name).read_bytes()
            expected = next(a for a in original["artifacts"] if a["artifact_id"] == identity_)
            assert sha(data) == expected["sha256"]
            artifact = artifacts.promote(
                PROJECT,
                identity_,
                data,
                kind=kind,
                purpose="inspection" if kind == "proxy" else "metadata",
                media_type=mime,
                metadata={"source_version_id": "fixture-v1"},
            )
            with services.registry.project_store(PROJECT).transaction() as tx:
                artifacts.put(tx, artifact)
        publication = dict(
            schema_version="1.0",
            publication_id="m04-labelled-fixture-import",
            project_id=PROJECT,
            items=[
                dict(
                    item_id="fixture-scene",
                    version_id="fixture-v1",
                    kind="scene",
                    name="Imported W03 geometry fixture · source pinned, not newly authored",
                    expected_current_version_id=None,
                    source_commit=original["closure"]["files"][0]["origin"]["commit"],
                    source=original["closure"],
                    artifact_ids=[
                        original["proxy_mapping"]["proxy_artifact_id"],
                        original["proxy_mapping"]["proxy_artifact_id"] + "-map",
                    ],
                    preview_requests=[],
                )
            ],
        )
        catalogue.publish(caller, publication)
        # Exact accepted proxy read-model record, imported as fixture history, never
        # called a new render. Preserve its real historical revision/exit/attempt.
        old_job = proxy["job"]
        validate("JobRecord", old_job)
        with services.registry.project_store(PROJECT).transaction() as tx:
            tx.connection.execute(
                "INSERT INTO records VALUES (?,?,?,?)",
                ("jobs", old_job["job_id"], old_job["state_revision"], canonical(old_job).decode()),
            )
            work = tx.require("work", caller.work_id)
            work.update(state="completed", state_revision=1)
            tx.put("work", caller.work_id, work, expected_revision=0, schema="WorkRecord")
        for dpr in (1, 2):
            capture = read(EVIDENCE / f"camera-selection-dpr{dpr}.json")
            assert capture["glbSha256"] == sha((Path(proxy["root"]) / "proxy.glb").read_bytes())
            assert (
                sha((EVIDENCE / f"camera-capture-dpr{dpr}.png").read_bytes())
                == capture["uploadSha256"]
            )
        write(
            root / "fixture-import.json",
            {
                "label": "Explicitly imported accepted W03 fixture, not model authored",
                "original_proof_sha256": sha(PROOF.read_bytes()),
                "original_root": str(source),
                "source": original["closure"],
                "imported_proxy_job_id": old_job["job_id"],
                "project": str(project),
                "original_snapshot": source_snapshot(source),
                "baseline": source_snapshot(project),
            },
        )
    finally:
        services.close()


def serve(root: Path):
    import uvicorn

    settings = Settings.load(root / "config.private.json")
    services = Services.create(settings)
    from proscenium.api.composition import configure_services

    configure_services(services)
    previews = services.extension("previews")
    real_request = previews.request

    def bounded_request(caller, body):
        # Test-allocation guard only. It never rewrites model arguments or creates a job.
        # Reserve durably before calling the real production admission method.
        ledger = root.parent / "m04-camera-7b1f837-native.jsonl"
        with ledger.open("a+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.seek(0)
            rows = [json.loads(line) for line in stream if line.strip()]
            key = {"root": str(root), "intent_id": body["spec"]["intent_id"]}
            if not any(row["identity"] == key for row in rows):
                assert len(rows) < 3, "M04's total three-frame native allocation is exhausted"
                assert body["spec"]["purpose"] == "keyframe"
                assert body["spec"]["frames"] == {"start": 1, "end": 1}
                assert body["spec"]["samples"] == 8
                assert body["spec"]["dimensions"] in (
                    {"width": 1031, "height": 569},
                    {"width": 2063, "height": 1138},
                    {"width": 640, "height": 360},
                )
                stream.write(
                    json.dumps(
                        {
                            "identity": key,
                            "dimensions": body["spec"]["dimensions"],
                            "label": "Reserved bounded test frame, not completion evidence",
                        }
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
        return real_request(caller, body)

    previews.request = bounded_request
    routes = APIRouter()

    @routes.post("/m04-harness/dpr2")
    def dpr2(request: Request, authenticated: Caller = Depends(owner)):
        capture = read(EVIDENCE / "camera-selection-dpr2.json")
        artifacts = services.extension("artifacts")
        artifact = artifacts.upload(
            authenticated,
            PROJECT,
            "m04-harness-accepted-dpr2-capture",
            (EVIDENCE / "camera-capture-dpr2.png").read_bytes(),
            "image/png",
        )[0]
        assert artifact["sha256"] == capture["uploadSha256"]
        store = services.registry.project_store(PROJECT)
        with store.transaction() as tx:
            caller = fixture_work(tx, services, "dpr2")
        from proscenium.previews.config import REVISION

        version = services.extension("catalogue").version(PROJECT, "fixture-v1")
        spec = dict(
            intent_id="m04-harness-dpr2",
            shot_id="fixture-shot",
            purpose="keyframe",
            source_closure_digest=version["source"]["digest"],
            scene_name="Fixture",
            camera=capture["selection"]["camera"],
            frames=capture["selection"]["range"],
            fps={"numerator": 24, "denominator": 1},
            dimensions=capture["intrinsic"],
            engine="CYCLES",
            device="CPU",
            samples=8,
            colour_management={
                "view_transform": "Standard",
                "look": "None",
                "exposure": 0,
                "gamma": 1,
            },
            renderer_revision=REVISION,
            review_output={
                "media_type": "image/png",
                "encoding": "view_transformed_srgb",
                "bit_depth": 8,
            },
        )
        job = services.extension("previews").request(
            caller,
            dict(
                schema_version="1.0",
                operation_id="m04-harness-dpr2-operation",
                project_id=PROJECT,
                version={"item_id": "fixture-scene", "version_id": "fixture-v1"},
                spec=spec,
            ),
        )[0]
        write(
            root / "dpr2-labelled-harness.json",
            {
                "label": "Authenticated scripted test-owner call, NOT a second model turn",
                "capture_sha256": artifact["sha256"],
                "job": job,
            },
        )
        return job

    @routes.get("/m04-harness/state")
    async def state(final: bool = False, authenticated: Caller = Depends(owner)):
        with services.registry.project_store(PROJECT).transaction(write=False) as tx:
            result: dict[str, Any] = {
                name: tx.all(name)
                for name in (
                    "requests",
                    "contexts",
                    "jobs",
                    "frames",
                    "segments",
                    "annotations",
                    "invocations",
                    "artifacts",
                )
            }
            result["events"] = [
                json.loads(row[0])
                for row in tx.connection.execute("SELECT body FROM events ORDER BY sequence")
            ]
        if final:
            runtime = services.extension("copilot")
            result["native_user_messages"] = []
            result["native_tool_starts"] = []
            result["native_child_events"] = 0
            for session in runtime.sessions.values():
                events = await session.get_raw_events()
                for event in events:
                    if event["type"] == "user.message":
                        result["native_user_messages"].append(
                            {"type": event["type"], "data": event["data"]}
                        )
                    elif event["type"] == "tool.execution_start":
                        data = event["data"]
                        result["native_tool_starts"].append(
                            {
                                k: data[k]
                                for k in ("toolName", "toolCallId", "arguments")
                                if k in data
                            }
                        )
                    elif event["type"].startswith("subagent."):
                        result["native_child_events"] += 1
        return result

    app = create_app(settings, services=services, routers=[routes], manage_features=True)
    pairing = services.auth.issue_pairing()
    (root / "pairing.private.url").write_text(settings.origin + "/#pair=" + pairing)
    (root / "pairing.private.url").chmod(0o600)
    try:
        from urllib.parse import urlsplit

        port = urlsplit(settings.origin).port
        assert port is not None
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        services.close()


def run(root: Path):
    assert root.is_relative_to(Path.home() / ".cache/proscenium-development/milestones")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    prepare(root)
    config = read(root / "config.private.json")
    # Chromium's Unix SingletonSocket cannot fit below the long retained run path.
    temporary = Path.home() / ".cache" / ("m04-" + sha(str(root).encode())[:10])
    temporary.mkdir(mode=0o700, exist_ok=False)
    write(root / "temporary-directory.json", {"owned_disk_backed_path": str(temporary)})
    environment = {**os.environ, "TMPDIR": str(temporary)}
    server = None
    observed: dict[str, dict] = {}
    try:
        with (root / "server.private.log").open("wb") as log:
            server = subprocess.Popen(
                [sys.executable, __file__, "serve", str(root)],
                cwd=REPO,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=environment,
            )
        server_identity = identity(server.pid)
        assert server_identity is not None
        observed["server"] = server_identity
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if httpx.get(config["origin"] + "/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise TimeoutError("Owned M04 server did not start")
        with (root / "browser-driver.log").open("wb") as log:
            subprocess.run(
                ["node", "tests/acceptance/m04_browser.mjs", str(root)],
                cwd=REPO,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=520,
                check=True,
                env=environment,
            )
    finally:
        if server is not None:
            runtime_path = root / "machine/copilot-runtime/process.json"
            if runtime_path.exists():
                observed["sdk"] = read(runtime_path)
            current = identity(server.pid)
            if (
                current
                and current["start_ticks"] == observed["server"]["start_ticks"]
                and server.poll() is None
            ):
                os.killpg(server.pid, signal.SIGTERM)
                server.wait(timeout=45)
        write(
            root / "cleanup.json",
            {
                "server_exited": server is not None and server.poll() is not None,
                "owned_identities": observed,
                "independent_blender_bridge": "9876 untouched; no authoring MCP configured",
            },
        )
        write(root / "source-after.json", source_snapshot(root / "project"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "serve"))
    parser.add_argument(
        "root",
        type=Path,
        nargs="?",
        default=Path.home()
        / ".cache/proscenium-development/milestones"
        / datetime.now(ZoneInfo("Africa/Johannesburg")).strftime("m04-%Y%m%dT%H%M%S%z"),
    )
    args = parser.parse_args()
    (run if args.action == "run" else serve)(args.root.resolve())
