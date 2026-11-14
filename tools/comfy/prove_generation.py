"""Explicit, durable W07 GPU allocation: one initial job and at most one actual fix.

No pytest discovery executes this program. No VLM calls, installation or service controls.
All large state/output stays in the separately authorised home scratch, never RAM-backed /tmp.
"""

import argparse
import hashlib
import io
import json
import os
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
from proscenium.providers.comfy import ComfyClient, preflight
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


def write_once(path: Path, value) -> None:
    """Append-only allocation/receipt publication. Never rewrite a previous observation."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


class ContinuationHTTP(httpx.BaseTransport):
    """Actual adapter transport with optional known-acceptance ACK-loss observation."""

    def __init__(self, directory: Path, *, drop_known_ack: bool):
        self.directory, self.drop_known_ack = directory, drop_known_ack
        self.delegate = httpx.HTTPTransport(retries=0, trust_env=False)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/prompt"):
            write_once(
                self.directory / "prompt-dispatch.json",
                {
                    "at": time.time(),
                    "endpoint": str(request.url),
                    "request_sha256": sha(request.content),
                    "client_id": json.loads(request.content).get("client_id"),
                    "limit": "One network submission for the original admitted job. A repeated POST is refused before transport.",
                },
            )
        response = self.delegate.handle_request(request)
        response.read()
        if request.method == "POST" and request.url.path.endswith("/prompt"):
            body = response.json()
            record = {
                "at": time.time(),
                "endpoint": str(request.url),
                "status": response.status_code,
                "request_sha256": sha(request.content),
                "response_sha256": sha(response.content),
                "prompt_id": body.get("prompt_id"),
                "client_id": json.loads(request.content).get("client_id"),
            }
            with (self.directory / "prompt-transport.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            lost = self.directory / "known-ack-loss.json"
            if (
                self.drop_known_ack
                and response.is_success
                and isinstance(body.get("prompt_id"), str)
                and not lost.exists()
            ):
                write_once(
                    lost,
                    {
                        **record,
                        "injection": "Lost transport acknowledgement AFTER actual successful provider acceptance. Not native RPC loss.",
                    },
                )
                raise httpx.ReadTimeout(
                    "M07 injected known provider ACK loss; reconcile original identity",
                    request=request,
                )
        return response

    def close(self) -> None:
        self.delegate.close()


def continue_original(window: str, *, drop_known_ack: bool = False) -> None:
    """Explicit new window for the ORIGINAL job only. No submit/admit/reset/old-file writes."""
    if window != "w07-continuation-b":
        raise ValueError(
            "This code grants no allocation. Only the exact coordinator-authorised window is accepted."
        )
    directory = SCRATCH / "continuations" / window
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_path, receipt_path = directory / "report.json", directory / "window.json"
    if report_path.exists():
        print(
            json.dumps(
                {
                    "retained_report": str(report_path),
                    "job": json.loads(report_path.read_text())["after"]["job"],
                    "no_external_effect": True,
                },
                indent=2,
            )
        )
        return
    old_paths = [SCRATCH / "w07-live-initial-report.json", SCRATCH / "w07-live-initial-timing.json"]
    original_hashes = {p.name: sha(p.read_bytes()) for p in old_paths}
    original = json.loads(old_paths[0].read_text())
    expected = original["job"]
    job_id = "w07-live-initial"
    assert expected["job_id"] == job_id and expected["target"]["target_id"] == TASK
    settings = Settings(data_directory=SCRATCH / "machine", free_space_reserve_bytes=1024**3)
    services = Services.create(settings)
    client = httpx.Client(
        timeout=15,
        transport=ContinuationHTTP(directory, drop_known_ack=drop_known_ack),
        trust_env=False,
        follow_redirects=False,
    )
    try:
        if services.registry.active_id() != PROJECT:
            raise RuntimeError("The original project must remain the active registry identity")
        services.acquire_project(PROJECT)
        install_catalogue(services)
        install_reviews(services)
        adapter = install(
            services,
            client=client,
            registrar=lambda c, b: register_provider_artifact(services, c, b),
        )
        store = services.registry.project_store(PROJECT)

        def snapshot():
            with store.transaction(write=False) as tx:
                return {
                    "job": tx.require("jobs", job_id),
                    "execution": tx.get("providers.execution", job_id, required=False) or {},
                    "inputs": tx.require("job_inputs", job_id),
                    "reservations": [
                        dict(r) for r in tx.connection.execute("SELECT * FROM budget_reservations")
                    ],
                    "outbox": [
                        dict(r)
                        for r in tx.connection.execute(
                            "SELECT id,kind,state FROM outbox WHERE id=?", (expected["attempt_id"],)
                        )
                    ],
                }

        before = snapshot()
        for field in (
            "job_id",
            "attempt_id",
            "attempt_number",
            "work_id",
            "input_digest",
            "provider_id",
            "target",
        ):
            if before["job"][field] != expected[field]:
                raise RuntimeError("Original admitted job/attempt/authority identity changed")
        if before["inputs"] != original["frozen_inputs"] or len(before["reservations"]) != 1:
            raise RuntimeError("Original frozen inputs or reservation identity changed")
        request = before["inputs"]["request"]
        assert request["generation_task_id"] == TASK and request["model_ids"] == MODELS
        frozen_graph = before["inputs"]["graph"]
        assert (
            frozen_graph["latent"]["inputs"]["width"]
            == frozen_graph["latent"]["inputs"]["height"]
            == 64
        )
        assert frozen_graph["sigmas"]["inputs"]["steps"] == 4
        base = before["inputs"]["provider"]["endpoint"]
        if base != "http://127.0.0.1:8188":
            raise RuntimeError("No provider substitution is authorised")
        comfy = ComfyClient(base, client)
        execution = before["execution"]
        known_prompt = execution.get("prompt_id") or before["job"]["external_job_id"]
        if not known_prompt and execution.get("client_id"):
            known_prompt = comfy.reconcile(execution["client_id"])
        history_before = comfy.status(known_prompt) if known_prompt else None
        preflight(frozen_graph, comfy.capabilities(), MODELS)
        if not receipt_path.exists():
            write_once(
                receipt_path,
                {
                    "window": window,
                    "started_epoch": time.time(),
                    "duration_seconds": 600,
                    "origin": "Original W07 harness job, NOT native M07 work. No new job/attempt/task/reservation.",
                    "allocation": "M07 total maximum three actual executions: this original continuation plus two later native jobs.",
                    "original_hashes": original_hashes,
                    "before": before,
                    "known_prompt_before": known_prompt,
                    "history_before": history_before,
                    "drop_known_prompt_ack": drop_known_ack,
                    "application_revision": git(Path(__file__).parents[2], "rev-parse", "HEAD")
                    .decode()
                    .strip(),
                },
            )
        receipt = json.loads(receipt_path.read_text())
        if receipt["original_hashes"] != original_hashes:
            raise RuntimeError("A retained original report/timing changed; no execution")
        deadline = receipt["started_epoch"] + receipt["duration_seconds"]
        services.gate.resume(
            PROJECT,
            reconciliation_evidence=f"explicit coordinator allocation {window}; preserve original admitted identity",
        )
        while time.time() < deadline:
            current = snapshot()
            if current["job"]["state"] in TERMINAL:
                break
            client.timeout = httpx.Timeout(min(15, max(0.25, (deadline - time.time()) / 16)))
            stats = comfy.get("/system_stats")
            queue = comfy.get("/queue")
            available = memory_available()
            execution = current["execution"]
            observation = {
                "at": time.time(),
                "mem_available_bytes": available,
                "devices": stats.get("devices", []),
                "queue_running": len(queue.get("queue_running", [])),
                "queue_pending": len(queue.get("queue_pending", [])),
                "job_state": current["job"]["state"],
                "execution_phase": execution.get("phase"),
                "prompt_id": execution.get("prompt_id") or current["job"]["external_job_id"],
                "client_id": execution.get("client_id"),
            }
            with (directory / "observations.jsonl").open("a") as stream:
                stream.write(json.dumps(observation) + "\n")
            if execution.get("phase") == "submitting":
                # Existing acceptance is reconciled, never subjected to another cold launch.
                adapter.pump(PROJECT, job_id)
            elif (
                execution.get("phase") in {None, "waiting"}
                and current["job"]["external_job_id"] is None
            ):
                if (
                    available >= WEIGHT_BYTES + 4 * 1024**3
                    and not queue.get("queue_running")
                    and not queue.get("queue_pending")
                    and any(
                        d.get("vram_free", 0) >= before["inputs"]["minimum_free_vram_bytes"]
                        for d in stats.get("devices", [])
                    )
                    and deadline - time.time() > 60
                ):
                    adapter.pump(PROJECT, job_id)
            else:
                break  # Ambiguous preparation or inconsistent identity needs explicit reconciliation, not resubmission.
            time.sleep(min(2, max(0, deadline - time.time())))
        after = snapshot()
        for field in (
            "job_id",
            "attempt_id",
            "attempt_number",
            "work_id",
            "input_digest",
            "provider_id",
            "target",
        ):
            assert after["job"][field] == before["job"][field]
        assert after["inputs"] == before["inputs"] and len(after["reservations"]) == 1
        assert {k: after["reservations"][0][k] for k in ("id", "task_id", "target_id")} == {
            k: before["reservations"][0][k] for k in ("id", "task_id", "target_id")
        }
        final_hashes = {p.name: sha(p.read_bytes()) for p in old_paths}
        assert final_hashes == original_hashes
        prompt = after["execution"].get("prompt_id") or after["job"]["external_job_id"]
        history_after = comfy.status(prompt) if prompt else None
        write_once(
            report_path,
            {
                "window": window,
                "finished_epoch": time.time(),
                "before": before,
                "after": after,
                "history_before": history_before,
                "history_after": history_after,
                "original_hashes_before": original_hashes,
                "original_hashes_after": final_hashes,
                "artifacts": [
                    services.extension("artifacts").get(PROJECT, a)
                    for a in after["job"]["artifact_ids"]
                ],
                "provider_source_sha256": {
                    p.name: sha(p.read_bytes())
                    for p in (
                        Path(__file__).parents[2] / "apps/backend/src/proscenium/providers"
                    ).glob("*.py")
                },
                "status": "succeeded"
                if after["job"]["state"] == "succeeded"
                else "blocked_or_failed",
                "note": "No native M07 journey has been executed by this original-job continuation.",
            },
        )
        print(
            json.dumps(
                {
                    "window": window,
                    "report": str(report_path),
                    "job": after["job"],
                    "reservations": after["reservations"],
                },
                indent=2,
            )
        )
        if after["job"]["state"] != "succeeded":
            raise SystemExit(
                "Original continuation did not succeed. Stop before native M07 inference; retain all identities."
            )
    finally:
        client.close()
        services.close()


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
    parser.add_argument("--continuation-window")
    parser.add_argument("--drop-known-prompt-ack", action="store_true")
    args = parser.parse_args()
    if args.continuation_window:
        if args.correction:
            parser.error("The original-job continuation is not a new correction")
        continue_original(args.continuation_window, drop_known_ack=args.drop_known_prompt_ack)
    else:
        if args.drop_known_prompt_ack:
            parser.error("ACK-loss injection requires the explicit continuation window")
        run(correction=args.correction)
