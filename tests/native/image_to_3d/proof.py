"""ONE exact named allocation. Refuses an existing run, never re-submits inference.

This is a native test harness, not a product tool or alternative admission service.
All generation effects run through the actual ProviderService and accepted W02 bridge.
"""

import argparse
import asyncio
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageDraw
from proscenium.auth import Caller
from proscenium.catalogue import install as install_catalogue
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.providers import install, register_provider_artifact
from proscenium.providers.hunyuan import FILENAME, graph
from proscenium.services import Services
from proscenium.sources import closure_digest
from proscenium.sources.common import git

from tools.image_to_3d.candidate import MODEL_ROOT
from tools.image_to_3d.instance import plan
from tools.image_to_3d.weights import inspect_weights

ALLOCATION = "e04-live-b"
ROOT = MODEL_ROOT.parent / "runs" / ALLOCATION
REPO = Path(__file__).resolve().parents[3]
ENDPOINT = "http://127.0.0.1:18588"
FIT_BYTES = 12 * 1024**3
HEADROOM = 4 * 1024**3
TERMINAL = {"succeeded", "failed", "cancelled"}


def allocation_root(name: str) -> Path:
    if name != ALLOCATION:
        raise ValueError("Only the explicitly authorised e04-live-b allocation is accepted")
    return MODEL_ROOT.parent / "runs" / name


def remaining_owned_processes(report: dict) -> list[dict]:
    remaining = []
    for key in ("owned_server", "blender_process"):
        identity = report.get(key)
        if identity:
            try:
                if start_ticks(identity["pid"]) == identity["start_ticks"]:
                    remaining.append({"role": key, **identity})
            except (FileNotFoundError, ProcessLookupError):
                pass
    return remaining


def write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".new")
    with temporary.open("w") as output:
        json.dump(value, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def start_ticks(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def observe(pid: int | None = None) -> dict:
    memory = {
        line.split(":")[0]: int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith(("MemAvailable:", "MemTotal:"))
    }
    result = {"monotonic_ns": time.monotonic_ns(), "mem_available_bytes": memory["MemAvailable"]}
    if pid:
        try:
            status = Path(f"/proc/{pid}/status").read_text().splitlines()
            result["owned_process_rss_bytes"] = next(
                int(line.split()[1]) * 1024 for line in status if line.startswith("VmRSS:")
            )
        except (OSError, StopIteration):
            result["owned_process_rss_bytes"] = None
    gpu = {}
    for device in Path("/sys/class/drm").glob("card[0-9]*/device"):
        metrics = {}
        for name in ("gpu_busy_percent", "mem_info_vram_used", "mem_info_vram_total"):
            try:
                metrics[name] = int((device / name).read_text().strip())
            except (OSError, ValueError):
                pass
        if metrics:
            gpu[str(device)] = metrics
    result["gpu_sysfs"] = gpu
    return result


def reference_image() -> bytes:
    """Explicitly synthetic 2D reference, not a claimed generated model result."""
    image = Image.new("RGB", (512, 512), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((140, 400, 390, 433), fill=(225, 225, 225))
    draw.ellipse((315, 185, 449, 340), fill=(169, 30, 30))
    draw.ellipse((338, 205, 423, 318), fill="white")
    draw.rounded_rectangle((128, 164, 355, 410), radius=38, fill=(205, 43, 43))
    draw.ellipse((128, 139, 355, 201), fill=(236, 88, 88))
    draw.ellipse((143, 149, 340, 188), fill=(91, 24, 24))
    draw.rounded_rectangle((148, 212, 171, 363), radius=10, fill=(235, 83, 83))
    result = io.BytesIO()
    image.save(result, format="PNG")
    return result.getvalue()


def prepare_services(report: dict):
    services = Services.create(
        Settings(
            data_directory=ROOT / "machine",
            free_space_reserve_bytes=10 * 1024**3,
            generation_attempt_limit=1,
            profiles=(
                {
                    "profile_id": "e04-native",
                    "name": "Explicit bounded E04 native proof",
                    "lead_model_id": None,
                    "worker_model_ids": [],
                    "scope_status": "project_scoped",
                },
            ),
            services=(
                {
                    "provider_id": "e04-isolated-comfy",
                    "endpoint": ENDPOINT,
                    "capabilities": ["image_to_3d"],
                    "model_id": None,
                    "available": None,
                },
            ),
            provider_options={"minimum_free_vram_bytes": 10 * 1024**3},
        )
    )
    catalogue = install_catalogue(services)
    owner = Caller("owner", "e04-allocation-owner")
    projects = services.extension("projects")
    project, _ = projects.open(
        owner,
        {
            "schema_version": "1.0",
            "operation_id": "e04-open-project",
            "path": str(ROOT / "project"),
            "create": True,
            "name": "Disposable E04 image-to-mesh proof",
        },
    )
    project_id = project["project_id"]
    services.activate_project(project_id)
    projects.update_settings(
        owner,
        {
            "schema_version": "1.0",
            "operation_id": "e04-owner-profile",
            "project_id": project_id,
            "expected_revision": 0,
            "generation_attempt_limit": 1,
            "default_profile_id": "e04-native",
        },
    )
    services.gate.resume(
        project_id, reconciliation_evidence=f"Explicit {ALLOCATION} one-attempt allocation"
    )
    caller = Caller(
        "tool",
        "e04-native-harness",
        project_id,
        "e04-work",
        services.ownership.epoch,
        operations=("submitGenerationJob", "checkpoint_sources", "publishOutputs"),
        invocation_id="e04-invocation",
    )
    with services.registry.project_store(project_id).transaction() as tx:
        tx.put(
            "work",
            "e04-work",
            {
                "schema_version": "1.0",
                "project_id": project_id,
                "work_id": "e04-work",
                "originating_request_id": "e04-human-allocation",
                "directive_request_ids": [],
                "state": "running",
                "state_revision": 0,
                "native_session_id": "e04-harness-not-an-sdk-session",
                "job_ids": [],
                "generation_task_id": "e04-generation-task",
            },
            schema="WorkRecord",
        )
        tx.put(
            "invocations",
            caller.invocation_id,
            {"work_id": caller.work_id, "owner_epoch": caller.owner_epoch, "state": "running"},
        )
        # Trusted harness context models the ONE explicit human allocation, not an actual SDK turn.
        # The provider consumes the accepted W01 frozen-allowance record, never a project file.
        tx.put(
            "copilot.effective",
            "e04-human-allocation",
            {"generation_attempt_limit": 1, "profile_id": "e04-native"},
        )
    root = Path(project["path"])
    git(root, "config", "user.name", "Proscenium E04 Native Proof")
    git(root, "config", "user.email", "e04-native@example.invalid")
    artifacts = services.extension("artifacts")
    reference = artifacts.upload(
        owner, project_id, "e04-conditioning", reference_image(), "image/png"
    )[0]
    for identity, data in (
        ("e04-workflow", canonical(graph(reference["artifact_id"]))),
        ("e04-prompt", b"Synthetic reference: a red mug on white. Text is provenance only."),
    ):
        artifact = artifacts.promote(
            project_id,
            identity,
            data,
            kind="document",
            purpose="metadata",
            media_type="application/json" if identity == "e04-workflow" else "text/plain",
        )
        with services.registry.project_store(project_id).transaction() as tx:
            artifacts.put(tx, artifact)
    adapter = install(
        services, registrar=lambda worker, body: register_provider_artifact(services, worker, body)
    )
    command = {
        "schema_version": "1.0",
        "job_id": "e04-mesh-job",
        "project_id": project_id,
        "generation_task_id": "e04-generation-task",
        "target_id": "e04-generation-task",
        "provider_id": "e04-isolated-comfy",
        "capability": "image_to_3d",
        "workflow_artifact_id": "e04-workflow",
        "prompt_artifact_id": "e04-prompt",
        "input_artifact_ids": [reference["artifact_id"]],
        "seed": "1",
        "model_ids": [FILENAME],
        "settings": {},
    }
    job, _ = adapter.submit(caller, command)
    report.update(
        project=project,
        caller={
            "work_id": caller.work_id,
            "invocation_id": caller.invocation_id,
            "owner_epoch": caller.owner_epoch,
        },
        command=command,
        reference=reference,
        job=job,
        harness_context="Synthetic trusted admission context and 2D image. Actual production services and native effects.",
    )
    write(ROOT / "report.json", report)
    return services, catalogue, caller, adapter


def import_publish(services, catalogue, caller, report):
    project = report["project"]
    project_id, root = project["project_id"], Path(project["path"])
    artifacts = services.extension("artifacts")
    source = next(a for a in report["artifacts"] if a["kind"] == "source_blob")
    directory = root / "assets/e04"
    directory.mkdir()
    for name, data in (
        ("original.glb", artifacts.ready_path(project_id, source["artifact_id"]).read_bytes()),
        (
            "conditioning.png",
            artifacts.ready_path(project_id, report["reference"]["artifact_id"]).read_bytes(),
        ),
        ("workflow.json", canonical(report["execution"]["graph"])),
        (
            "generation-provenance.json",
            artifacts.ready_path(project_id, report["job"]["artifact_ids"][-1]).read_bytes(),
        ),
        ("model-licence.txt", (REPO / "docs/evidence/w09/model-licence.txt").read_bytes()),
    ):
        (directory / name).write_bytes(data)
    executable = shutil.which("blender")
    if not executable:
        raise RuntimeError("Allocated Blender executable is unavailable")
    script = Path(__file__).with_name("blender_import.py")
    argv = [
        executable,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--threads",
        "4",
        "--python-exit-code",
        "17",
        "--python",
        str(script),
        "--",
        str(directory / "original.glb"),
        str(directory / "asset.blend"),
        str(ROOT / "blender-report.json"),
    ]
    environment = {
        **os.environ,
        "OMP_NUM_THREADS": "4",
        "MKL_NUM_THREADS": "4",
        "BLENDER_USER_CONFIG": str(ROOT / "user/blender/config"),
        "BLENDER_USER_SCRIPTS": str(ROOT / "user/blender/scripts"),
        "BLENDER_USER_DATAFILES": str(ROOT / "user/blender/datafiles"),
    }
    report["blender_start"] = {
        "argv": argv,
        "script_sha256": digest(script),
        "monotonic_ns": time.monotonic_ns(),
    }
    write(ROOT / "report.json", report)
    code = run_import(argv, environment, root, report)
    report["blender_exit_code"] = code
    if code:
        raise RuntimeError("Allocated Blender import failed, inspect retained log")
    report["blender"] = json.loads((ROOT / "blender-report.json").read_text())
    report["blender_end_monotonic_ns"] = time.monotonic_ns()
    (directory / "inspection.json").write_bytes(canonical(report["blender"]))
    paths = ["project.json", ".gitignore", ".gitattributes"] + sorted(
        str(p.relative_to(root)) for p in directory.iterdir()
    )
    checkpoint = services.extension("sources").checkpoint(
        caller,
        {
            "schema_version": "1.0",
            "operation_id": "e04-source-checkpoint",
            "project_id": project_id,
            "paths": paths,
            "message": "feat: retain actual E04 image-conditioned mesh and editable Blender asset",
            "expected_head": git(root, "rev-parse", "--verify", "HEAD", required=False)
            .decode()
            .strip()
            or None,
        },
    )
    report["checkpoint"] = checkpoint
    if not checkpoint.get("source_commit"):
        raise RuntimeError("Source checkpoint remains uncertain")
    closure = {
        "entry_path": "assets/e04/asset.blend",
        "files": [
            {
                "destination": path,
                "origin": {
                    "kind": "git",
                    "repository_id": project["repository_id"],
                    "commit": checkpoint["source_commit"],
                    "path": path,
                    "sha256": digest(root / path),
                },
            }
            for path in paths
        ],
        "dependencies": [],
        "required_runtime": {
            "application": "blender",
            "version": report["blender"]["blender_version"],
            "features": [],
        },
        "digest": "0" * 64,
    }
    closure["digest"] = closure_digest(closure)
    report["closure"] = closure
    publication, replayed = catalogue.publish(
        caller,
        {
            "schema_version": "1.0",
            "publication_id": "e04-publish-asset",
            "project_id": project_id,
            "items": [
                {
                    "item_id": "e04-generated-asset",
                    "version_id": "e04-asset-v1",
                    "kind": "asset",
                    "name": "E04 image-conditioned mug shape",
                    "expected_current_version_id": None,
                    "source_commit": checkpoint["source_commit"],
                    "source": closure,
                    "artifact_ids": report["job"]["artifact_ids"],
                    "preview_requests": [],
                    "description": "Actual Hunyuan image-conditioned untextured shape. Synthetic 2D input. Scale not calibrated.",
                    "provenance": {
                        "source_url": "https://huggingface.co/tencent/Hunyuan3D-2",
                        "creator": "Tencent",
                        "licence": "Tencent Hunyuan Community Licence",
                        "note": "Pinned model and generation hashes retained. No topology or texture claim.",
                    },
                }
            ],
        },
    )
    report.update(
        publication=publication,
        publication_replayed=replayed,
        published_version=catalogue.version(project_id, "e04-asset-v1"),
    )


def run_import(argv, environment, root, report):
    with (ROOT / "blender.log").open("wb") as log:
        proc = subprocess.Popen(
            argv,
            cwd=root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    ticks = start_ticks(proc.pid)
    report["blender_process"] = {
        "pid": proc.pid,
        "start_ticks": ticks,
        "process_group": os.getpgid(proc.pid),
    }
    write(ROOT / "report.json", report)
    try:
        return proc.wait(timeout=180)
    except subprocess.TimeoutExpired:
        if (
            proc.poll() is None
            and start_ticks(proc.pid) == ticks
            and os.getpgid(proc.pid) == proc.pid
        ):
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        raise


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allocation", required=True, choices=[ALLOCATION])
    args = parser.parse_args()
    ROOT = allocation_root(args.allocation)
    instance = plan(
        ROOT, 18588
    )  # refuses existing root, changed source, occupied port or hidden extra paths
    before = observe()
    with httpx.Client(timeout=10, trust_env=False) as read_client:
        queue = read_client.get("http://127.0.0.1:8188/queue").raise_for_status().json()
        independent_stats = (
            read_client.get("http://127.0.0.1:8188/system_stats").raise_for_status().json()
        )
    ROOT.mkdir(parents=True)
    report = {
        "allocation": args.allocation,
        "recorded_at": datetime.now(timezone(timedelta(hours=2))).isoformat(),
        "code_revision": git(REPO, "rev-parse", "HEAD").decode().strip(),
        "instance_plan": instance,
        "harness_sha256": digest(Path(__file__)),
        "baseline": before,
        "independent_queue_before": queue,
        "independent_stats_before": independent_stats,
        "fit_threshold_bytes": FIT_BYTES,
        "protected_host_headroom_bytes": HEADROOM,
        "maximum_server_seconds": 1800,
        "sampling_interval_seconds": 0.5,
        "e04_passed": False,
        "m10_passed": False,
        "gpu_inference_submitted": False,
        "existing_services_mutated": False,
    }
    write(ROOT / "report.json", report)
    if (
        before["mem_available_bytes"] < FIT_BYTES
        or shutil.disk_usage(ROOT).free < 14 * 1024**3
        or queue.get("queue_running")
        or queue.get("queue_pending")
    ):
        report["blocker"] = "Immediate host fit/disk/independent queue admission guard"
        write(ROOT / "report.json", report)
        print(json.dumps(report, indent=2), flush=True)
        return
    report["verified_weights"] = inspect_weights()
    for directory in instance["create_before_authorised_start"]:
        Path(directory).mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, **instance["environment_overrides"]}
    proc = None
    services = adapter = None
    stopping = threading.Event()
    guard_reason = []
    samples = []
    sampler = None
    try:
        services, catalogue, caller, adapter = prepare_services(report)
        # Recheck after hashing/bootstrap and immediately before native process creation.
        report["before_server_start"] = observe()
        if report["before_server_start"]["mem_available_bytes"] < FIT_BYTES:
            report["blocker"] = "Host memory dropped below fit floor before server start"
            return
        with (ROOT / "comfy.log").open("wb") as log:
            proc = subprocess.Popen(
                instance["argv"],
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        ticks = start_ticks(proc.pid)
        started = time.monotonic()
        report["owned_server"] = {
            "pid": proc.pid,
            "start_ticks": ticks,
            "process_group": os.getpgid(proc.pid),
            "started_monotonic_ns": time.monotonic_ns(),
        }
        report["instance_plan"]["executed"] = True
        write(ROOT / "report.json", report)

        def terminate_owned():
            try:
                if (
                    proc.poll() is None
                    and start_ticks(proc.pid) == ticks
                    and os.getpgid(proc.pid) == proc.pid
                ):
                    os.killpg(proc.pid, signal.SIGTERM)
            except (FileNotFoundError, ProcessLookupError):
                pass

        def sample():
            with (ROOT / "resource-samples.ndjson").open("w") as output:
                while not stopping.is_set():
                    observation = observe(proc.pid)
                    samples.append(observation)
                    output.write(json.dumps(observation) + "\n")
                    output.flush()
                    if observation["mem_available_bytes"] < HEADROOM + 1024**3:
                        guard_reason.append(
                            "Protect four-GiB host headroom: available RAM fell below five GiB"
                        )
                    if time.monotonic() - started >= 1800:
                        guard_reason.append("Owned server allocation expired")
                    if guard_reason:
                        terminate_owned()
                        return
                    stopping.wait(0.5)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        with httpx.Client(timeout=10, trust_env=False) as read_client:
            while time.monotonic() - started < 180:
                if proc.poll() is not None or guard_reason:
                    raise RuntimeError("Owned ComfyUI process stopped before readiness")
                try:
                    ready = read_client.get(ENDPOINT + "/system_stats")
                    if ready.status_code == 200:
                        report["owned_system_stats"] = ready.json()
                        break
                except httpx.HTTPError:
                    pass
                stopping.wait(0.5)
            else:
                raise RuntimeError("Owned ComfyUI startup exceeded bounded readiness interval")
        report["before_inference"] = observe()
        if report["before_inference"]["mem_available_bytes"] < FIT_BYTES:
            report["blocker"] = (
                "Host memory dropped below fit floor after server startup, before inference"
            )
            return
        project_id = report["project"]["project_id"]
        job_id = report["command"]["job_id"]
        report["inference_dispatch_started_monotonic_ns"] = time.monotonic_ns()
        write(ROOT / "report.json", report)
        while time.monotonic() - started < 1800:
            if guard_reason or proc.poll() is not None:
                raise RuntimeError("Owned process stopped, retain original job for reconciliation")
            # Only the existing pump may upload/submit. A submitting marker is always reconciled.
            adapter.pump(project_id, job_id)
            report["job"] = services.jobs.get(project_id, job_id)
            report["execution"] = adapter.execution(project_id, job_id)
            report["gpu_inference_submitted"] = report["execution"].get("phase") == "submitting"
            write(ROOT / "report.json", report)
            print(
                json.dumps(
                    {
                        "job": job_id,
                        "state": report["job"]["state"],
                        "prompt_id": report["job"]["external_job_id"],
                    }
                ),
                flush=True,
            )
            if report["job"]["state"] in TERMINAL:
                break
            if (
                report["job"]["state"] == "waiting_resource"
                and not report["gpu_inference_submitted"]
            ):
                report["blocker"] = "Immediate provider/resource pre-submit wait, no waiting loop"
                break
            stopping.wait(1)
        report["inference_dispatch_ended_monotonic_ns"] = time.monotonic_ns()
        if report["job"]["state"] == "failed" and report.get("execution", {}).get("prompt_id"):
            from proscenium.providers.comfy import ComfyClient

            history = ComfyClient(ENDPOINT, adapter.client).status(report["execution"]["prompt_id"])
            if history:
                errors = [
                    value
                    for kind, value in history.get("status", {}).get("messages", [])
                    if kind == "execution_error"
                ]
                report["provider_terminal_errors"] = [
                    {
                        key: str(error.get(key, ""))[:512]
                        for key in ("node_id", "node_type", "exception_type", "exception_message")
                    }
                    for error in errors
                ]
        if report["job"]["state"] not in TERMINAL:
            report.setdefault(
                "blocker", "Original provider job is nonterminal/uncertain, never resubmit"
            )
            return
        terminate_owned()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            if proc.poll() is None and start_ticks(proc.pid) == ticks:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
        report["owned_server"]["exit_code"] = proc.returncode
        if report["job"]["state"] == "succeeded":
            artifacts = services.extension("artifacts")
            report["artifacts"] = [
                artifacts.get(project_id, identity) for identity in report["job"]["artifact_ids"]
            ]
            import_publish(services, catalogue, caller, report)
            report["hardware_mesh_import_checkpoint_publication_proven"] = True
            report["e04_passed"] = True
    except Exception as exc:
        report["blocker"] = f"{type(exc).__name__}: {exc}"
    finally:
        stopping.set()
        if sampler:
            sampler.join(timeout=5)
        if proc and proc.poll() is None:
            # Exact owned group only. No queue clearing, global interrupt or service stop API.
            if (
                start_ticks(proc.pid) == report["owned_server"]["start_ticks"]
                and os.getpgid(proc.pid) == proc.pid
            ):
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=10)
        if proc:
            report["owned_server"]["exit_code"] = proc.returncode
        report["resource_guard_reasons"] = guard_reason
        if samples:
            report["sampled_resources"] = {
                "samples": len(samples),
                "minimum_mem_available_bytes": min(o["mem_available_bytes"] for o in samples),
                "maximum_owned_rss_bytes": max(
                    o.get("owned_process_rss_bytes") or 0 for o in samples
                ),
                "exact_allocator_peak": False,
            }
        if services and "project" in report:
            project_id = report["project"]["project_id"]
            if adapter:
                report["job"] = services.jobs.get(project_id, report["command"]["job_id"])
                report["execution"] = adapter.execution(project_id, report["job"]["job_id"])
                asyncio.run(adapter.close())
            with services.registry.project_store(project_id).transaction(write=False) as tx:
                report["reservations"] = [
                    dict(row) for row in tx.connection.execute("SELECT * FROM budget_reservations")
                ]
            with services.resources.store.transaction(write=False) as tx:
                report["remaining_resource_claims"] = [
                    dict(row) for row in tx.connection.execute("SELECT * FROM resource_claims")
                ]
            services.close()
        report["finished_at"] = datetime.now(timezone(timedelta(hours=2))).isoformat()
        report["remaining_owned_processes"] = remaining_owned_processes(report)
        write(ROOT / "report.json", report)
        print(
            json.dumps(
                {
                    "report": str(ROOT / "report.json"),
                    "job": report.get("job"),
                    "blocker": report.get("blocker"),
                },
                indent=2,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
