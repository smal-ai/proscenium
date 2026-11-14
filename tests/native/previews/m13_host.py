"""Allocated M13 host: labelled W03 import, fresh native association, bounded encodes.

No renderer/model-service lifecycle. Stops only this API/browser and its expired root.
"""

import asyncio
import copy
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn
from copilot import CopilotClient
from copilot.tools import ToolInvocation
from proscenium.api.composition import configure_services
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.contracts import canonical, validate
from proscenium.copilot.tools import Tools
from proscenium.previews import runner
from proscenium.services import Services

REPO = Path(__file__).resolve().parents[3]
SOURCE = Path("/tmp/opencode/proscenium-w03-native-a/test_real_progressive_camera_a0/project")
PROOF = Path("/tmp/opencode/proscenium-w03-native-a.json")
ASSEMBLY = Path("/tmp/opencode/proscenium-w03-assembly-a.json")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def import_history(services, root):
    project = root / "project"
    env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
    subprocess.run(
        ["git", "clone", "--no-hardlinks", str(SOURCE), str(project)],
        env=env,
        check=True,
        capture_output=True,
    )
    import shutil

    shutil.copytree(SOURCE / ".git/lfs/objects", project / ".git/lfs/objects", dirs_exist_ok=True)
    subprocess.run(["git", "remote", "remove", "origin"], cwd=project, check=True)
    subprocess.run(["git", "lfs", "checkout"], cwd=project, check=True, capture_output=True)
    config = dict(
        schema_version="1.0",
        repository_id="w03-repo",
        paths={name: name for name in ("brief", "assets", "scenes", "storyboards", "sequences")},
    )
    for name in config["paths"]:
        (project / name).mkdir(exist_ok=True)
    config.update(project_id="m13-cinematic", name="M13 labelled W03 fixture history")
    (project / "project.json").write_bytes(canonical(config))
    record = dict(
        schema_version="1.0",
        project_id=config["project_id"],
        repository_id=config["repository_id"],
        name=config["name"],
        path=str(project),
        native_session_id=None,
        state="inactive",
        state_revision=0,
    )
    services.registry.register(record)
    services.activate_project(record["project_id"])
    services.gate.resume(
        record["project_id"],
        reconciliation_evidence="Owner-authorised labelled fixture import, no model work",
    )
    configure_services(services)
    store = services.registry.project_store(record["project_id"])
    with store.transaction() as tx:
        tx.put(
            "project_identity",
            "identity",
            {"project_id": record["project_id"], "repository_id": record["repository_id"]},
        )
        tx.put(
            "work",
            "work-fixture-import",
            dict(
                schema_version="1.0",
                work_id="work-fixture-import",
                project_id=record["project_id"],
                originating_request_id="fixture-import-not-model-request",
                directive_request_ids=[],
                state="running",
                state_revision=0,
                native_session_id="fixture-history-not-resumable",
                job_ids=[],
                generation_task_id="fixture-import-task",
            ),
            schema="WorkRecord",
        )
        tx.put(
            "invocations",
            "fixture-import",
            {
                "work_id": "work-fixture-import",
                "state": "running",
                "owner_epoch": services.ownership.epoch,
            },
        )
    caller = Caller(
        "tool",
        "labelled-fixture-import",
        record["project_id"],
        "work-fixture-import",
        services.ownership.epoch,
        invocation_id="fixture-import",
        operations=("publishOutputs",),
    )
    source = sqlite3.connect(f"file:{SOURCE / '.proscenium/state.sqlite3'}?mode=ro", uri=True)

    def rows(namespace):
        return [
            json.loads(row[0])
            for row in source.execute(
                "SELECT body FROM records WHERE namespace=? ORDER BY id", (namespace,)
            )
        ]

    versions = rows("versions")
    original = next(row for row in versions if row["version_id"] == "fixture-v1")
    artifact_service = services.extension("artifacts")
    jobs = [
        json.loads(PROOF.read_text())["jobs"][0]["job"],
        json.loads(ASSEMBLY.read_text())["incoming_render"]["job"],
    ]
    selected_ids = {row["job_id"] for row in jobs}
    current_jobs = {job["job_id"]: job for job in rows("jobs")}
    assert all(
        current_jobs[job["job_id"]] == job
        and job["state"] == "succeeded"
        and job.get("native_exit_code") == 0
        for job in jobs
    )
    identities = {identity for job in jobs for identity in job["artifact_ids"]}
    hashes = []
    for artifact in rows("artifacts"):
        if artifact["artifact_id"] not in identities:
            continue
        data = (SOURCE / f".proscenium/artefacts/sha256/{artifact['sha256']}/content").read_bytes()
        assert sha(data) == artifact["sha256"] and len(data) == artifact["bytes"]
        imported = artifact_service.promote(
            record["project_id"],
            artifact["artifact_id"],
            data,
            kind=artifact["kind"],
            purpose=artifact["purpose"],
            media_type=artifact["media_type"],
            derived_from=artifact["derived_from"],
            metadata={
                key: artifact[key]
                for key in ("dimensions", "fps", "frame_count", "source_version_id")
                if key in artifact
            },
        )
        assert imported == {**artifact, "project_id": record["project_id"]}
        with store.transaction() as tx:
            artifact_service.put(tx, imported)
        hashes.append(
            {"artifact_id": artifact["artifact_id"], "sha256": sha(data), "bytes": len(data)}
        )
    publication = {
        key: copy.deepcopy(original[key])
        for key in ("item_id", "kind", "name", "version_id", "source_commit", "source")
    }
    publication.update(
        expected_current_version_id=None,
        artifact_ids=[],
        preview_requests=[],
        description="LABELLED imported actual W03 production history, not newly model-rendered shots",
    )
    services.extension("catalogue").publish(
        caller,
        dict(
            schema_version="1.0",
            publication_id="m13-labelled-fixture-import",
            project_id=record["project_id"],
            items=[publication],
        ),
    )
    pinned_root = services.extension("sources").materialise(record["project_id"], "fixture-v1")
    for row in original["source"]["files"]:
        content = (pinned_root / row["destination"]).read_bytes()
        assert sha(content) == row["origin"]["sha256"]
        hashes.append(
            {"source_path": row["destination"], "sha256": sha(content), "bytes": len(content)}
        )
    with store.transaction() as tx:
        for namespace, definition in (
            ("jobs", "JobRecord"),
            ("attempts", "JobAttempt"),
            ("frames", "FrameReceipt"),
            ("segments", "PreviewSegment"),
        ):
            for row in rows(namespace):
                if row["job_id"] not in selected_ids:
                    continue
                row["project_id"] = record["project_id"]
                if namespace == "jobs":
                    row["work_id"] = "work-fixture-import"
                validate(definition, row)
                identity = next(
                    row[key]
                    for key in (
                        {
                            "jobs": "job_id",
                            "attempts": "attempt_id",
                            "frames": "receipt_id",
                            "segments": "segment_id",
                        }[namespace],
                    )
                )
                # Labelled terminal fixture history preserves original revision/evidence.
                tx.connection.execute(
                    "INSERT INTO records VALUES (?,?,?,?)",
                    (namespace, identity, row.get("state_revision", 0), canonical(row).decode()),
                )
        work = tx.require("work", "work-fixture-import")
        tx.put(
            "work",
            "work-fixture-import",
            {**work, "state": "completed", "state_revision": 1},
            expected_revision=0,
        )
        tx.put(
            "invocations",
            "fixture-import",
            {
                "work_id": "work-fixture-import",
                "state": "completed",
                "owner_epoch": services.ownership.epoch,
            },
            expected_revision=0,
        )
    source.close()
    inputs = dict(
        label="IMPORTED ACTUAL W03 FIXTURE HISTORY, NOT NEW MODEL RENDERS",
        project_id=record["project_id"],
        repository_id=record["repository_id"],
        source_version={"item_id": "fixture-scene", "version_id": "fixture-v1"},
        closure=original["source"],
        shots=[
            {
                "job_id": j["job_id"],
                "shot_id": j["target"]["shot_id"],
                "frames": j["render_spec"]["frames"],
                "dimensions": j["render_spec"]["dimensions"],
                "fps": j["render_spec"]["fps"],
            }
            for j in jobs
        ],
    )
    (project / "m13-inputs.json").write_text(json.dumps(inputs, indent=2))
    for args in (
        ["config", "user.name", "M13 fixture owner"],
        ["config", "user.email", "m13@example.invalid"],
        ["add", "project.json", "m13-inputs.json"],
        ["commit", "-m", "test: label imported W03 source and terminal receipt history"],
    ):
        subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)
    (root / "import.json").write_text(
        json.dumps(
            dict(
                source=str(SOURCE),
                source_proof_sha256=sha(PROOF.read_bytes()),
                assembly_proof_sha256=sha(ASSEMBLY.read_bytes()),
                original_version=original,
                original_jobs=jobs,
                hashes=hashes,
                input_metadata=inputs,
                native_history_imported=False,
            ),
            indent=2,
        )
    )
    brief = """M13 ALLOCATED OWNER BRIEF. Work ONLY in this active project. No children/delegation, Blender, Unity, rendering, image/VLM generation, service/model lifecycle, installation, network downloads or direct FFmpeg. Finish this root within 165 seconds, without asking creative questions. Existing images are imported labelled ACTUAL W03 fixture history, not newly rendered shots. Do not query SQLite or edit .proscenium records. Read m13-inputs.json for this project's real IDs/source provenance.

Maintain canonical SequenceMap in sequences/m13/sequence.json and explicit complete FrameMap in sequences/m13/frame-map.json, using installed proscenium.contracts SCHEMA/validate/canonical. Use exactly the declared frame timing below at 24/1 fps, 640x360 original input dimensions, silent true, blend_space linear_rgb. All choices are agent proposals under this owner brief, not owner visual approval. No owner selection action or finding approval is permitted.

Register the exact FrameMap JSON bytes through register_artifact, copying them to .proscenium/tool-output/staging/<map-id>.json first. ArtifactRegister uses schema_version 1.0, operation_id, project_id, artifact_id <map-id>, staging_path, kind frame_map, purpose metadata, media_type application/json and the REAL bytes' sha256. Put that map ID in every SequenceMap entry.mapping_artifact_id. FrameMap.frames has every sequence ordinal, each contributor with actual shot_id/version_id/job_id/source_frame and a reduced rational weight. The host resolves frame receipts; never invent their hashes.

Use checkpoint_sources for ONLY these two canonical files, with actual git HEAD expected_head. Build SourceClosure from its actual file hashes/commit, files sorted by destination, dependencies [{item_id:fixture-scene,version_id:fixture-v1}], required_runtime {application:sequence,version:1.0,features:[]}, digest sha256(canonical(closure_without_digest)). Git origins use project.json repository_id. Publish a sequence item m13-film with the declared version/head below, name M13 two-shot cinematic, source_commit exact checkpoint commit, artifact_ids [<map-id>], preview_requests []. Validate PublicationSubmit before publish_outputs. No rerender requests. Call assemble_sequence ONCE with {operation_id:<assemble-id>,job_id:<job-id>,version:{item_id:m13-film,version_id:<version>},execution_profile_id:blender-cpu}. Record its actual result and body in ordinary reports/m13-vN.json. The owner harness will test acknowledgement retry separately under the original invocation. Do not call assembly again under a new invocation to simulate retry. You may get_job to inspect progress, but do not wait indefinitely. End with exact source/version/encode IDs and no claim of owner approval.
"""
    first = (
        brief
        + """
V1: version m13-film-v1, expected_current_version_id null, map-id m13-map-v1, assemble-id m13-assemble-v1, job-id m13-encode-v1, frame_count 10.
Opening entry_id opening: first shot m13-inputs.shots[0], source frames 1..3 -> sequence 1..3 identity, weight 1.
Blend entry_id blend: same first shot source 4..6 -> sequence 4..6 transition. contributors are first shot frames 4..6 and second shot m13-inputs.shots[1] frame 1..1. At ordinals 4,5,6 the first/second weights are respectively (1,0), (1/2,1/2), (0,1). Retain BOTH contributors including zero weights, not a fictional source frame.
Arrival entry_id arrival: second shot source 1..1 -> sequence 7..10 retime, hold its real frame 1 with weight 1 for each of those four ordinals.
All source version_id values are fixture-v1. Use exact job/shot IDs in m13-inputs.json. No other footage or fabricated camera/render changes. Proceed now.
"""
    )
    second = (
        brief
        + """
SECOND DECLARED EDIT, V2: publish NEW version m13-film-v2, expected_current_version_id m13-film-v1, map-id m13-map-v2, assemble-id m13-assemble-v2, job-id m13-encode-v2, frame_count 8.
Reorder and trim the previous edit: first arrival (stable entry_id arrival), second shot source 1..1 held as retime over sequence 1..4. Then opening (stable entry_id opening), first shot trimmed to source 3..6 as trim over sequence 5..8, weight 1 on each matching frame. Remove the blend entry for this edit. Both entries use the new explicit map. Keep old source commits, v1, its map/delivery and all owner annotations/selections untouched. Existing visual selection of v1, if shown, is not permission to change that owner record. This v2 is a new agent-proposed edit with declared inputs, not an adoption or invention of private owner choices. No rerenders. Proceed now.
"""
    )
    (root / "first.txt").write_text(first)
    (root / "second.txt").write_text(second)
    services.gate.close(record["project_id"])


def main():
    os.environ["PATH"] = f"{REPO / '.venv/bin'}:{os.environ['PATH']}"
    root = Path(sys.argv[1]).resolve()
    restarting = "--resume-host" in sys.argv
    if not restarting:
        root.mkdir(parents=True, mode=0o700, exist_ok=False)
    config = json.loads(
        (root / "config.json" if restarting else REPO / "config.example.json").read_text()
    )
    config.update(
        data_directory=str(root / "machine"),
        origin=config["origin"] if restarting else f"http://127.0.0.1:{port()}",
        frontend_directory=str(REPO / "apps/frontend/dist"),
        services=[],
        provider_options={},
        execution_profiles=["blender-cpu"],
    )
    config["runtime"].update(
        cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
        mcp_servers={},
        skill_directories=[],
        worker_model="gpt-6.1-sol",
        lead_model="gpt-6.1-sol",
    )
    config["rendering"].update(
        blender="/home/quintin/.local/opt/blender-4.5.14-linux-x64/blender",
        encode_timeout=60,
        max_frames=12,
        max_pixels=640 * 360,
        claim_path=str(root / "render-unused.lock"),
    )
    (root / "config.json").write_text(json.dumps(config, indent=2))
    (root / "config.json").chmod(0o600)
    services = Services.create(Settings.load(root / "config.json"))
    if not restarting:
        import_history(services, root)
    # Allocation-only SDK fence: no task/delegation tool and no custom children.
    original_create = CopilotClient.create_session

    async def create(client, **settings):
        settings.update(custom_agents=[], excluded_tools=["task"])
        return await original_create(client, **settings)

    CopilotClient.create_session = create
    original_resume = CopilotClient.resume_session

    async def resume(client, session_id, **settings):
        settings.update(custom_agents=[], excluded_tools=["task"])
        return await original_resume(client, session_id, **settings)

    CopilotClient.resume_session = resume
    original_launch = runner.launch_assembly
    encodes = (
        json.loads((root / "encode-dispatches.json").read_text())
        if (root / "encode-dispatches.json").exists()
        else []
    )

    def launch(service, project_id, body):
        if len(encodes) >= 3:
            raise RuntimeError("Three-encode allocation exhausted")
        encodes.append(body)
        (root / "encode-dispatches.json").write_text(json.dumps(encodes, indent=2))
        return original_launch(service, project_id, body)

    runner.launch_assembly = launch

    def forbidden(*args, **kwargs):
        raise RuntimeError("No rerender/Blender allocation")

    runner.launch = forbidden
    app = create_app(services.settings, services=services, manage_features=True)
    (root / "pairing.url").write_text(f"{config['origin']}/#pair={services.auth.issue_pairing()}")
    (root / "pairing.url").chmod(0o600)
    cdp = port()
    browser = subprocess.Popen(
        [
            str(Path.home() / ".cache/ms-playwright/chromium-1255/chrome-linux64/chrome"),
            "--headless",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            f"--remote-debugging-port={cdp}",
            f"--user-data-dir={root / 'browser-profile'}",
            "about:blank",
        ],
        stdout=(root / "browser.log").open("wb"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (root / "processes.json").write_text(
        json.dumps(
            dict(origin=config["origin"], cdp=cdp, server_pid=os.getpid(), browser_pid=browser.pid),
            indent=2,
        )
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=int(config["origin"].rsplit(":", 1)[1]), workers=1
        )
    )
    stop = threading.Event()

    async def replay():
        with services.registry.project_store("m13-cinematic").transaction(write=False) as tx:
            row = next(r for r in tx.all("native_workflows") if r["job_id"] == "m13-encode-v1")
            invocation = tx.require("invocations", row["binding"]["invocation_id"])
            before = len(tx.all("jobs"))
        services.gate.close("m13-cinematic")
        runtime = services.extension("copilot")
        result = await Tools(services, runtime.state, "m13-cinematic").invoke(
            "assemble_sequence",
            ToolInvocation(
                session_id=invocation["session_id"],
                agent_id=invocation["agent_id"],
                tool_call_id=invocation["native_id"],
                tool_name="assemble_sequence",
                arguments=row["binding"]["body"],
            ),
        )
        value = json.loads(result.text_result_for_llm)
        assert value["job_id"] == "m13-encode-v1"
        with services.registry.project_store("m13-cinematic").transaction(write=False) as tx:
            assert len(tx.all("jobs")) == before
        return {
            "body": row["binding"]["body"],
            "invocation_id": invocation["invocation_id"],
            "result": value,
            "job_count": before,
            "dispatches": len(encodes),
            "root_gate_closed": True,
        }

    def watch():
        deadline = time.monotonic() + 1200
        active_since = {}
        while not stop.wait(0.1):
            if (root / "STOP").exists() or time.monotonic() > deadline:
                server.should_exit = True
                return
            with services.registry.project_store("m13-cinematic").transaction(write=False) as tx:
                records = tx.all("requests")
                continuation_count = sum(
                    r["body"]["mode"] == "continue_work" for r in tx.all("copilot.resumes")
                )
            if (root / "REPLAY").exists() and not (root / "replay.json").exists():
                future = asyncio.run_coroutine_threadsafe(
                    replay(), services.extension("copilot").worker.get_loop()
                )
                try:
                    result = future.result(15)
                except Exception as error:
                    result = {"error": str(error)}
                (root / "replay.json").write_text(json.dumps(result, indent=2))
            roots = [r for r in records if r["submission"]["type"] == "queue"]
            for record in roots:
                if record["receipt"]["state"] in {"dispatching", "acknowledged", "running"}:
                    active_since.setdefault(record["request_id"], time.monotonic())
                else:
                    active_since.pop(record["request_id"], None)
            if len(roots) + continuation_count > 3 or any(
                time.monotonic() - started > 125 for started in active_since.values()
            ):
                (root / "ALLOCATION_EXPIRED").write_text(
                    "Root deadline/count exceeded; closing owned API/runtime"
                )
                server.should_exit = True
                return

    monitor = threading.Thread(target=watch, daemon=True)
    monitor.start()
    try:
        server.run()
    finally:
        stop.set()
        monitor.join(3)
        browser.terminate()
        try:
            browser.wait(15)
        except subprocess.TimeoutExpired:
            browser.kill()
            browser.wait()
        services.close()
        (root / "cleanup.json").write_text(
            json.dumps(
                {
                    "browser_reaped": browser.returncode is not None,
                    "services_closed": services.ownership.file.closed,
                    "encodes": len(encodes),
                }
            )
        )


if __name__ == "__main__":
    main()
