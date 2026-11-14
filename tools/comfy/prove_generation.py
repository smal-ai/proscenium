"""Explicit, durable W07 GPU allocation: one initial job and at most one actual fix.

No pytest discovery executes this program. No VLM calls, installation or service controls.
All large state/output stays in the separately authorised home scratch, never RAM-backed /tmp.
"""

import argparse
import hashlib
import io
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, ImageDraw
from proscenium.auth import Caller
from proscenium.catalogue import install as install_catalogue
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.jobs import TERMINAL
from proscenium.projects.service import Projects
from proscenium.providers import install, register_provider_artifact
from proscenium.providers.klein import graph
from proscenium.providers.service import sha
from proscenium.reviews import install as install_reviews
from proscenium.services import Services
from proscenium.sources.common import git

SCRATCH = Path("/home/quintin/.cache/proscenium-development/providers-w07-20261114")
PROJECT = "w07-live-proof"
TASK = "w07-live-task"
MODELS = ["flux-2-klein-4b.safetensors", "qwen_3_4b.safetensors", "flux2-vae.safetensors"]
# Measured installed files: 7,751,105,712 + 8,044,982,048 + 336,213,556 bytes.
# Reserve 4 GiB host headroom beyond weights, 2 GiB service-reported device headroom.
WEIGHT_BYTES = 16_132_301_316


def memory_available() -> int:
    values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(values["MemAvailable"].split()[0]) * 1024


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2) + "\n")


def run(*, correction: bool) -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True, mode=0o700)
    retained_id = "w07-live-correction" if correction else "w07-live-initial"
    retained_report = SCRATCH / (retained_id + "-report.json")
    retained_timing = SCRATCH / (retained_id + "-timing.json")
    if retained_report.exists() and retained_timing.exists():
        prior = json.loads(retained_report.read_text())
        expired = time.time() >= json.loads(retained_timing.read_text())["started_epoch"] + 600
        if expired or prior["job"]["state"] in TERMINAL:
            print(
                json.dumps(
                    {
                        "retained_report": str(retained_report),
                        "job": prior["job"],
                        "no_external_call": True,
                    },
                    indent=2,
                )
            )
            if prior["job"]["state"] == "succeeded":
                return
            raise SystemExit(
                "Original allocation expired or settled. Evidence preserved. No replay or new inference."
            )
    root = SCRATCH / "project"
    root.mkdir(exist_ok=True, mode=0o700)
    if not (root / ".git").exists():
        Projects.bootstrap(root, "W07 isolated provider proof")
        git(root, "config", "user.name", "Proscenium bounded native proof")
        git(root, "config", "user.email", "providers-proof@example.invalid")
    settings = Settings(
        data_directory=SCRATCH / "machine",
        free_space_reserve_bytes=1024**3,
        services=(
            dict(
                provider_id="live-comfy",
                endpoint="http://127.0.0.1:8188",
                capabilities=["image"],
                model_id=None,
                available=None,
            ),
        ),
        provider_options={"minimum_free_vram_bytes": WEIGHT_BYTES + 2 * 1024**3},
    )
    services = Services.create(settings)
    client = httpx.Client(timeout=30, trust_env=False, follow_redirects=False)
    try:
        if services.registry.active_id() is None:
            services.registry.register(
                dict(
                    schema_version="1.0",
                    project_id=PROJECT,
                    repository_id="w07-live-repository",
                    name="W07 isolated native proof",
                    path=str(root),
                    native_session_id=None,
                    state="inactive",
                    state_revision=0,
                )
            )
            services.registry.activate(PROJECT)
        services.acquire_project(PROJECT)
        install_catalogue(services)
        install_reviews(services)
        adapter = install(
            services,
            client=client,
            registrar=lambda c, b: register_provider_artifact(services, c, b),
        )
        services.gate.resume(
            PROJECT, reconciliation_evidence="explicit coordinator W07 bounded GPU allocation"
        )
        store = services.registry.project_store(PROJECT)
        with store.transaction() as tx:
            if tx.get("work", "w07-live-work", required=False) is None:
                tx.put(
                    "work",
                    "w07-live-work",
                    dict(
                        schema_version="1.0",
                        work_id="w07-live-work",
                        project_id=PROJECT,
                        originating_request_id="w07-live-owner-request",
                        directive_request_ids=[],
                        state="running",
                        state_revision=0,
                        native_session_id="development-proof-no-native-turn",
                        job_ids=[],
                        generation_task_id=TASK,
                    ),
                    schema="WorkRecord",
                )
                tx.put(
                    "copilot.effective", "w07-live-owner-request", dict(generation_attempt_limit=2)
                )
            old = tx.get("invocations", "w07-live-invocation", required=False)
            tx.put(
                "invocations",
                "w07-live-invocation",
                dict(
                    work_id="w07-live-work", owner_epoch=services.ownership.epoch, state="running"
                ),
                expected_revision=tx.revision("invocations", "w07-live-invocation")
                if old
                else None,
            )
        caller = Caller(
            "tool",
            "w07-live-tool",
            PROJECT,
            "w07-live-work",
            services.ownership.epoch,
            operations=("registerToolArtifact", "submitGenerationJob"),
            invocation_id="w07-live-invocation",
        )
        artifacts = services.extension("artifacts")
        reference = Image.new("RGB", (64, 64), "white")
        drawing = ImageDraw.Draw(reference)
        drawing.rectangle((8, 8, 29, 55), fill="red")
        drawing.rectangle((34, 20, 55, 55), fill="blue")
        image_bytes = io.BytesIO()
        reference.save(image_bytes, format="PNG")
        reference_artifact, _ = artifacts.upload(
            Caller("owner", "w07-live-owner"),
            PROJECT,
            "w07-live-reference",
            image_bytes.getvalue(),
            "image/png",
        )
        workflow = graph(
            unet=MODELS[0],
            clip=MODELS[1],
            vae=MODELS[2],
            width=64,
            height=64,
            reference_artifact_id=reference_artifact["artifact_id"],
            steps=4,
        )
        job_id = "w07-live-correction" if correction else "w07-live-initial"
        if correction:
            initial = services.jobs.get(PROJECT, "w07-live-initial")
            if initial["state"] != "failed":
                raise RuntimeError(
                    "The one correction is permitted only after an actual initial error"
                )
        staging = root / ".proscenium/tool-output/staging"
        staging.mkdir(parents=True, exist_ok=True, mode=0o700)
        prompt = b"A red rectangular block on the left and a blue rectangular block on the right, white background. Use the supplied reference composition."
        for label, data in [("graph", canonical(workflow)), ("prompt", prompt)]:
            identity = "w07-live-" + label
            path = staging / identity
            if path.exists() and path.read_bytes() != data:
                raise RuntimeError(
                    "Retained proof input changed. Do not silently rewrite an admitted graph."
                )
            if not path.exists():
                path.write_bytes(data)
            artifacts.register(
                caller,
                dict(
                    schema_version="1.0",
                    operation_id=identity + "-register",
                    project_id=PROJECT,
                    artifact_id=identity,
                    staging_path=path.relative_to(root).as_posix(),
                    kind="document",
                    purpose="metadata",
                    sha256=sha(data),
                    media_type="application/json" if label == "graph" else "text/plain",
                ),
            )
        command = dict(
            schema_version="1.0",
            job_id=job_id,
            project_id=PROJECT,
            generation_task_id=TASK,
            target_id=TASK,
            provider_id="live-comfy",
            capability="image",
            workflow_artifact_id="w07-live-graph",
            prompt_artifact_id="w07-live-prompt",
            input_artifact_ids=["w07-live-reference"],
            seed="42",
            model_ids=MODELS,
            settings={},
        )
        job, replayed = adapter.submit(caller, command)
        timing = SCRATCH / (job_id + "-timing.json")
        if not timing.exists():
            write(
                timing,
                {
                    "started_epoch": time.time(),
                    "allocation": "one initial plus one actual error correction, maximum two jobs",
                },
            )
        deadline = json.loads(timing.read_text())["started_epoch"] + 600
        observations = []
        while job["state"] not in TERMINAL and time.time() < deadline:
            stats = client.get(settings.services[0]["endpoint"] + "/system_stats").json()
            queue = client.get(settings.services[0]["endpoint"] + "/queue").json()
            available = memory_available()
            observation = dict(
                at=datetime.now(ZoneInfo("Africa/Johannesburg")).isoformat(),
                mem_available_bytes=available,
                devices=stats.get("devices", []),
                queue_running=len(queue.get("queue_running", [])),
                queue_pending=len(queue.get("queue_pending", [])),
            )
            observations.append(observation)
            execution = adapter.execution(PROJECT, job_id)
            # Resource acquisition itself uses the actual F00 claim inside the pump.
            if execution.get("phase") == "submitting" or (
                available >= WEIGHT_BYTES + 4 * 1024**3
                and any(
                    d.get("vram_free", 0) >= WEIGHT_BYTES + 2 * 1024**3
                    for d in stats.get("devices", [])
                )
                and not queue.get("queue_running")
                and not queue.get("queue_pending")
            ):
                adapter.pump(PROJECT, job_id)
            job = services.jobs.get(PROJECT, job_id)
            if job["state"] not in TERMINAL:
                time.sleep(min(2, max(0, deadline - time.time())))
        with store.transaction(write=False) as tx:
            reservations = [
                dict(r) for r in tx.connection.execute("SELECT * FROM budget_reservations")
            ]
        provider_source = Path(__file__).parents[2] / "apps/backend/src/proscenium/providers"
        report = dict(
            measured_at=datetime.now(ZoneInfo("Africa/Johannesburg")).isoformat(),
            job=job,
            replayed=replayed,
            synthetic_scope="Development-owned work/invocation/frozen owner allowance. No native Copilot/VLM call.",
            reference=reference_artifact,
            reference_sha256=sha(image_bytes.getvalue()),
            command=command,
            frozen_inputs=services.jobs.inputs(PROJECT, job_id),
            execution=adapter.execution(PROJECT, job_id),
            observations=observations,
            reservations=reservations,
            system=stats["system"] if observations else None,
            provider_source_sha256={
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in provider_source.glob("*.py")
            },
            application_revision=git(Path(__file__).parents[2], "rev-parse", "HEAD")
            .decode()
            .strip(),
            artifacts=[artifacts.get(PROJECT, identity) for identity in job["artifact_ids"]],
            note="Provider execution and immutable PNG receipts only. No visual-quality conclusion.",
        )
        write(SCRATCH / (job_id + "-report.json"), report)
        print(
            json.dumps(
                {
                    "job": job,
                    "report": str(SCRATCH / (job_id + "-report.json")),
                    "reservations": reservations,
                },
                indent=2,
            )
        )
        if job["state"] != "succeeded":
            raise SystemExit(
                "Proof blocked/failed. Retain the original job/prompt/client identity. Do not blind-resubmit."
            )
    finally:
        client.close()
        services.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--correction", action="store_true")
    args = parser.parse_args()
    run(correction=args.correction)
