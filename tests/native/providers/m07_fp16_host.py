"""One owned Comfy child and real production host for the authorised M07 FP16 run.

No inference POST is constructed here. Actual native submit_generation admissions are
executed by the production ProviderService/lifecycle; the transport only observes and
selectively withholds ONE real response, without changing production state.
"""

import hashlib
import io
import json
import os
import signal
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import m09_host as observers
import uvicorn
from PIL import Image, ImageDraw
from proscenium.api.composition import configure_services
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.copilot.tools import Tools
from proscenium.providers.comfy import preflight
from proscenium.providers.sd_turbo_fp16 import BYTES, FILENAME, REVISION, SHA256, bind, graph
from proscenium.services import Services
from proscenium.sources import closure_digest
from proscenium.sources.common import git

from tools.comfy import sd_turbo_instance as instance

REPO = Path(__file__).resolve().parents[3]
ROOT = (
    Path.home()
    / ".cache/proscenium-development/image-generation-light/runs/m07-fp16-execution-20261114"
)
ORIGIN = "http://127.0.0.1:18959"
ENDPOINT = "http://127.0.0.1:18589"
FIT = 12 * 1024**3
STOP = 5 * 1024**3
INITIAL = "m07-fp16-initial"
CORRECTION = "m07-fp16-correction"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(name, value, *, exclusive=False):
    with (ROOT / name).open("x" if exclusive else "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        if exclusive:
            stream.flush()
            os.fsync(stream.fileno())  # Critical request/ACK facts only, never per token.


def ticks(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def observe(pid=None):
    result: dict[str, Any] = {"at": time.time(), "mem_available_bytes": observers.memory()}
    if pid:
        try:
            fields = dict(
                line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines()
            )
            result["owned_comfy_rss_bytes"] = int(fields["VmRSS"].split()[0]) * 1024
        except (OSError, KeyError):
            result["owned_comfy_rss_bytes"] = None
    result["gpu_sysfs"] = {}
    for device in Path("/sys/class/drm").glob("card[0-9]*/device"):
        values = {}
        for field in ("gpu_busy_percent", "mem_info_vram_used", "mem_info_vram_total"):
            try:
                values[field] = int((device / field).read_text().strip())
            except (OSError, ValueError):
                pass
        if values:
            result["gpu_sysfs"][str(device)] = values
    return result


def preserved():
    old = Path.home() / ".cache/proscenium-development/providers-w07-20261114"
    files = [
        old / "w07-live-initial-report.json",
        old / "w07-live-initial-timing.json",
        old / "project/.proscenium/state.sqlite3",
        instance.ROOT / "runs/m07-lightweight-20261114/admission.json",
    ]
    with sqlite3.connect(f"file:{old}/project/.proscenium/state.sqlite3?mode=ro", uri=True) as db:
        job = json.loads(
            db.execute(
                "SELECT body FROM records WHERE namespace='jobs' AND id='w07-live-initial'"
            ).fetchone()[0]
        )
        reservations = db.execute("SELECT * FROM budget_reservations").fetchall()
    assert job["state"] == "planned" and job["external_job_id"] is None
    return {"files": {str(p): sha(p) for p in files}, "job": job, "reservations": reservations}


class Supervisor:
    def __init__(self, process):
        self.process, self.start = process, ticks(process.pid)
        self.started = time.monotonic()
        self.stop_event = threading.Event()
        self.reasons = []
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.thread.start()

    def stop_owned(self, reason):
        if reason not in self.reasons:
            self.reasons.append(reason)
        if reason != "Normal owned fixture cleanup" and not (ROOT / "guard-stop.json").exists():
            save(
                "guard-stop.json",
                {"reason": reason, "at": time.time(), "resources": observe(self.process.pid)},
            )
        try:
            if (
                self.process.poll() is None
                and ticks(self.process.pid) == self.start
                and os.getpgid(self.process.pid) == self.process.pid
            ):
                os.killpg(self.process.pid, signal.SIGTERM)
        except (FileNotFoundError, ProcessLookupError):
            pass

    def sample(self):
        with (ROOT / "resources.jsonl").open("a") as output:
            while not self.stop_event.is_set():
                sample = observe(self.process.pid)
                output.write(json.dumps(sample) + "\n")
                output.flush()
                if sample["mem_available_bytes"] < STOP:
                    self.stop_owned("Five-GiB stopguard preserves four-GiB host headroom")
                if time.monotonic() - self.started >= 1800:
                    self.stop_owned("Owned Comfy 1800-second deadline")
                if self.reasons:
                    return
                self.stop_event.wait(0.5)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
        self.stop_owned("Normal owned fixture cleanup")
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            if self.process.poll() is None and ticks(self.process.pid) == self.start:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=10)


class FixtureServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # Uvicorn normally re-raises SIGTERM after lifespan shutdown, which can
        # bypass this development controller's outer child-cleanup finally.
        # Only this owned fixture host changes that behaviour, not production.
        original = {
            sig: signal.signal(sig, self.handle_exit) for sig in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            yield
        finally:
            for sig, handler in original.items():
                signal.signal(sig, handler)


class ObservedTransport(httpx.BaseTransport):
    def __init__(self, services, supervisor):
        self.services, self.supervisor = services, supervisor
        self.delegate = httpx.HTTPTransport(retries=0, trust_env=False)

    def snapshot(self):
        with self.services.registry.project_store(self.services.registry.active_id()).transaction(
            write=False
        ) as tx:
            return {
                "jobs": tx.all("jobs"),
                "executions": tx.all("providers.execution"),
                "reservations": [
                    dict(r) for r in tx.connection.execute("SELECT * FROM budget_reservations")
                ],
            }

    def handle_request(self, request):
        inference = request.method == "POST" and request.url.path == "/prompt"
        facts = None
        if inference:
            before = self.snapshot()
            body = json.loads(request.content)
            execution = next(
                e for e in before["executions"] if e.get("client_id") == body["client_id"]
            )
            # Match through the exact saved graph and current job input, not a harness Caller.
            jobs = [j for j in before["jobs"] if j["state"] == "starting"]
            if len(jobs) != 1 or jobs[0]["job_id"] not in {INITIAL, CORRECTION}:
                raise httpx.ConnectError("No unique authorised native job", request=request)
            job = jobs[0]
            if (
                observers.memory() < FIT
                or self.supervisor.reasons
                or self.supervisor.process.poll() is not None
            ):
                self.supervisor.stop_owned("Immediate pre-POST twelve-GiB fit failure")
                raise httpx.ConnectError(
                    "Owned process pre-POST memory guard, no inference sent", request=request
                )
            with httpx.Client(timeout=5, trust_env=False) as check:
                external = check.get("http://127.0.0.1:8188/queue").raise_for_status().json()
            if external.get("queue_running") or external.get("queue_pending"):
                self.supervisor.stop_owned("Independent service queue busy; no GPU overlap")
                raise httpx.ConnectError(
                    "External GPU queue busy, no inference sent", request=request
                )
            existing = list(ROOT.glob("prompt-request-*.json"))
            if len(existing) >= 2:
                raise httpx.ConnectError("Two-inference hard bound", request=request)
            facts = {
                "at": time.time(),
                "endpoint": str(request.url),
                "job": job,
                "body": body,
                "request_sha256": hashlib.sha256(request.content).hexdigest(),
                "before": before,
                "pre_post_resources": observe(self.supervisor.process.pid),
            }
            save(f"prompt-request-{job['job_id']}.json", facts, exclusive=True)
            save(
                f"job-window-{job['job_id']}.json",
                {"started_epoch": time.time(), "seconds_max": 600},
                exclusive=True,
            )
            assert execution["phase"] == "submitting"
        response = self.delegate.handle_request(request)
        response.read()
        if inference:
            assert facts is not None
            ack = {
                "at": time.time(),
                "status": response.status_code,
                "body": response.json(),
                "response_sha256": hashlib.sha256(response.content).hexdigest(),
                "client_id": facts["body"]["client_id"],
            }
            save(f"prompt-ack-{facts['job']['job_id']}.json", ack, exclusive=True)
            if (
                facts["job"]["job_id"] == INITIAL
                and response.is_success
                and isinstance(ack["body"].get("prompt_id"), str)
            ):
                # Observe, but NEVER write this out-of-band ID into production state.
                # Inject only after actual matching completed history exists, so the
                # next production client-ID/history reconciliation has genuine evidence.
                deadline = time.monotonic() + 90
                with httpx.Client(timeout=10, trust_env=False) as observation:
                    while time.monotonic() < deadline and not self.supervisor.reasons:
                        history = (
                            observation.get(ENDPOINT + "/history/" + ack["body"]["prompt_id"])
                            .raise_for_status()
                            .json()
                        )
                        own = history.get(ack["body"]["prompt_id"])
                        if own and own.get("status", {}).get("completed"):
                            save(
                                "fault-known-response.json",
                                {
                                    "at": time.time(),
                                    "real_ack": ack,
                                    "real_history": history,
                                    "state_before_fault": self.snapshot(),
                                    "fault": "ONE actual initial response withheld AFTER successful provider acceptance/completion. Not native RPC loss.",
                                },
                                exclusive=True,
                            )
                            raise httpx.ReadTimeout(
                                "M07 selectively withheld one actual provider response; reconcile original client/history",
                                request=request,
                            )
                        time.sleep(0.5)
        if request.method == "GET" and request.url.path.startswith(("/history", "/queue")):
            observers.append(
                "provider-reads.jsonl",
                {"path": request.url.path, "response": response.json(), "state": self.snapshot()},
            )
        return response

    def close(self):
        self.delegate.close()


def fixtures(services):
    owner = Caller("owner", "m07-fp16-scripted-owner")
    projects = services.extension("projects")
    project, _ = projects.open(
        owner,
        dict(
            schema_version="1.0",
            operation_id="m07-fp16-open",
            path=str(ROOT / "project"),
            create=True,
            name="M07 FP16 actual img2img fixture",
        ),
    )
    pid = project["project_id"]
    services.activate_project(pid)
    projects.update_settings(
        owner,
        dict(
            schema_version="1.0",
            operation_id="m07-fp16-policy",
            project_id=pid,
            expected_revision=0,
            generation_attempt_limit=3,
            default_profile_id="authoring",
        ),
    )
    services.gate.resume(
        pid, reconciliation_evidence="Explicit coordinator M07 FP16 native allocation"
    )
    with services.registry.project_store(pid).transaction() as tx:
        tx.put(
            "work",
            "m07-setup-work",
            dict(
                schema_version="1.0",
                work_id="m07-setup-work",
                project_id=pid,
                originating_request_id="fixture-only-no-native-request",
                directive_request_ids=[],
                state="running",
                state_revision=0,
                native_session_id="fixture-only",
                job_ids=[],
                generation_task_id="fixture-only",
            ),
            schema="WorkRecord",
        )
        tx.put(
            "invocations",
            "m07-setup-invocation",
            dict(work_id="m07-setup-work", owner_epoch=services.ownership.epoch, state="running"),
        )
    caller = Caller(
        "tool",
        "labelled-fixture-publisher",
        pid,
        "m07-setup-work",
        services.ownership.epoch,
        operations=("registerToolArtifact", "publishOutputs"),
        invocation_id="m07-setup-invocation",
    )
    artifacts = services.extension("artifacts")
    refs = []
    for name, unrelated in (("m07-owner-reference", False), ("m07-unrelated-reference", True)):
        image = Image.new("RGB", (512, 512), "white")
        draw = ImageDraw.Draw(image)
        if unrelated:
            draw.rounded_rectangle((190, 70, 320, 450), radius=30, fill="green")
        else:
            draw.ellipse((300, 170, 445, 350), fill="red")
            draw.ellipse((325, 195, 420, 325), fill="white")
            draw.rounded_rectangle((110, 150, 350, 410), radius=30, fill="red")
            draw.ellipse((110, 130, 350, 190), fill=(240, 80, 80))
            draw.ellipse((130, 140, 330, 175), fill=(70, 10, 10))
        reference_buffer = io.BytesIO()
        image.save(reference_buffer, format="PNG")
        refs.append(artifacts.upload(owner, pid, name, reference_buffer.getvalue(), "image/png")[0])
    documents = [
        ("m07-graph-initial", canonical(graph(refs[0]["artifact_id"])), "application/json"),
        ("m07-graph-correction", canonical(graph(INITIAL + "-artifact-0")), "application/json"),
        (
            "m07-prompt-initial",
            b"A studio photograph of a red ceramic coffee mug on a white background, matching the reference composition.",
            "text/plain",
        ),
        (
            "m07-prompt-correction",
            b"The same ceramic coffee mug and white background, change only the mug surface to cobalt blue, preserve its shape and composition.",
            "text/plain",
        ),
    ]
    staging = ROOT / "project/.proscenium/tool-output/staging"
    staging.mkdir(parents=True, exist_ok=True)
    for name, raw, media in documents:
        (staging / name).write_bytes(raw)
        artifacts.register(
            caller,
            dict(
                schema_version="1.0",
                operation_id=name + "-register",
                project_id=pid,
                artifact_id=name,
                staging_path=".proscenium/tool-output/staging/" + name,
                kind="document",
                purpose="metadata",
                sha256=hashlib.sha256(raw).hexdigest(),
                media_type=media,
            ),
        )
    plan = dict(
        schema_version="1.0",
        plan_id="m07-fp16-plan",
        project_id=pid,
        brief_artifact_ids=[],
        asset_dependencies=[],
        unresolved=[],
        shots=[
            dict(
                shot_id="shot-mug",
                intent="One bounded img2img target and one unrelated reference",
                camera_intent="Static image fixture",
                motion_beats=[],
                scene_version=None,
                source_range=None,
                fps=None,
                frames=[
                    dict(
                        frame_id="frame-mug",
                        role="start",
                        composition="Mug concept target",
                        artifact_id=refs[0]["artifact_id"],
                        state="available",
                    ),
                    dict(
                        frame_id="frame-unrelated",
                        role="end",
                        composition="Unrelated green reference, do not generate",
                        artifact_id=refs[1]["artifact_id"],
                        state="available",
                    ),
                ],
            )
        ],
    )
    root = ROOT / "project"
    name = "storyboards/m07-fp16-plan.json"
    (root / "storyboards").mkdir(exist_ok=True)
    data = canonical(plan)
    (root / name).write_bytes(data)
    git(root, "config", "user.name", "M07 labelled fixture")
    git(root, "config", "user.email", "m07@example.invalid")
    git(root, "add", "--", name, ".gitattributes")
    git(root, "commit", "-m", "M07 labelled storyboard and reference fixture")
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    closure = dict(
        entry_path=name,
        files=[
            dict(
                destination=name,
                origin=dict(
                    kind="git",
                    repository_id=project["repository_id"],
                    commit=commit,
                    path=name,
                    sha256=hashlib.sha256(data).hexdigest(),
                ),
            )
        ],
        dependencies=[],
        required_runtime=dict(application="fixture", version="1", features=[]),
        digest="0" * 64,
    )
    closure["digest"] = closure_digest(closure)
    publication, _ = services.extension("catalogue").publish(
        caller,
        dict(
            schema_version="1.0",
            publication_id="m07-fp16-fixture-publication",
            project_id=pid,
            items=[
                dict(
                    item_id="m07-fp16-storyboard",
                    version_id="m07-fp16-plan-v1",
                    kind="storyboard",
                    name="M07 FP16 concept storyboard",
                    expected_current_version_id=None,
                    source_commit=commit,
                    source=closure,
                    artifact_ids=[a["artifact_id"] for a in refs],
                    preview_requests=[],
                    description="Labelled reference/source fixture; generation is NOT pre-seeded",
                )
            ],
        ),
    )
    with services.registry.project_store(pid).transaction() as tx:
        w = tx.require("work", "m07-setup-work")
        tx.put(
            "work",
            "m07-setup-work",
            {**w, "state": "completed", "state_revision": 1},
            expected_revision=0,
            schema="WorkRecord",
        )
        assert tx.all("jobs") == [] and tx.all("requests") == []
    prompts = {}
    for stage, job, ref, seed in (
        ("initial", INITIAL, refs[0]["artifact_id"], "42"),
        ("correction", CORRECTION, INITIAL + "-artifact-0", "43"),
    ):
        body = dict(
            schema_version="1.0",
            job_id=job,
            project_id=pid,
            generation_task_id="USE_AUTHORITATIVE_HOST_CONTEXT_GENERATION_TASK_ID",
            target_id="m07-mug-" + stage,
            provider_id="m07-fp16-comfy",
            capability="image",
            workflow_artifact_id="m07-graph-" + stage,
            prompt_artifact_id="m07-prompt-" + stage,
            input_artifact_ids=[ref],
            seed=seed,
            model_ids=[FILENAME],
            settings={
                "target_slot": dict(
                    kind="storyboard_frame",
                    storyboard_version=dict(
                        item_id="m07-fp16-storyboard", version_id="m07-fp16-plan-v1"
                    ),
                    shot_id="shot-mug",
                    frame_id="frame-mug",
                )
            },
        )
        prompts[stage] = (
            "M07 bounded actual generation. No children/delegation, VLM, mesh, Blender, Unity, rendering or source edits. Call submit_generation EXACTLY ONCE with the JSON below after replacing generation_task_id with the exact authoritative HOST extension-context generation_task_id. This is a new explicit owner Queue, not a retry or the old W07 fixture. Return only admitted job/status and stop. Never POST directly or retry inference if uncertain; the host reconciles. Do not select images. "
            + json.dumps(body)
        )
        (ROOT / (stage + "-prompt.txt")).write_text(prompts[stage])
    return dict(
        project=project,
        references=refs,
        plan=plan,
        publication=publication,
        source_commit=commit,
        source_path=str(root / name),
        source_sha256=hashlib.sha256(data).hexdigest(),
    )


def main():
    instance.RUN_ROOT = ROOT
    instance.ALLOCATION = "m07-fp16-execution-20261114"
    planned = instance.plan()
    before = observe()
    old = preserved()
    weight = instance.MODEL_ROOT / "checkpoints" / FILENAME
    assert weight.stat().st_size == BYTES and sha(weight) == SHA256 and not weight.is_symlink()
    ROOT.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = dict(
        instance_plan=planned,
        before_start=before,
        preservation_before=old,
        weights=dict(filename=FILENAME, bytes=BYTES, sha256=SHA256),
        fit_floor_bytes=FIT,
        stopguard_bytes=STOP,
        protected_headroom_bytes=4 * 1024**3,
        actual_generation_started=False,
    )
    save("host-report.json", report)
    if before["mem_available_bytes"] < FIT:
        report["blocker"] = "Immediate pre-start fit failure, no RAM wait"
        save("host-report.json", report)
        return
    for directory in planned["create_before_start"]:
        Path(directory).mkdir(parents=True, exist_ok=True)
    proc = supervisor = services = None
    try:
        with (ROOT / "comfy.private.log").open("wb") as log:
            proc = subprocess.Popen(
                planned["argv"],
                cwd=ROOT,
                env={**os.environ, **planned["environment_overrides"]},
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        supervisor = Supervisor(proc)
        report["owned_comfy"] = {"pid": proc.pid, "start_ticks": supervisor.start}
        save("host-report.json", report)
        with httpx.Client(timeout=5, trust_env=False) as check:
            for _ in range(240):
                if proc.poll() is not None or supervisor.reasons:
                    raise RuntimeError("Owned Comfy stopped before readiness")
                try:
                    status = check.get(ENDPOINT + "/system_stats")
                    if status.status_code == 200:
                        report["owned_system_stats"] = status.json()
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError("Bounded Comfy readiness failed")
            report["after_startup"] = observe(proc.pid)
            if report["after_startup"]["mem_available_bytes"] < FIT:
                raise RuntimeError("Immediate post-start fit failure; no wait")
            catalog = check.get(ENDPOINT + "/object_info").raise_for_status().json()
            preflight(
                bind(graph("fixture"), "Mug", "42", {"fixture": "artifact:fixture"}, [FILENAME]),
                catalog,
                [FILENAME],
            )
            report["actual_node_preflight_passed"] = True
        observers.ROOT = ROOT
        observers.observe_native()
        original = Tools.invoke

        async def invoke(tools, name, invocation):
            result = await original(tools, name, invocation)
            if name in {"submit_generation", "get_job"}:
                observers.append(
                    "generation-tool-results.jsonl",
                    dict(
                        tool=name, arguments=invocation.arguments, result=result.text_result_for_llm
                    ),
                )
            return result

        setattr(Tools, "invoke", invoke)
        settings = Settings(
            data_directory=ROOT / "machine",
            origin=ORIGIN,
            frontend_directory=REPO / "apps/frontend/dist",
            generation_attempt_limit=3,
            profiles=(
                dict(
                    profile_id="authoring",
                    name="M07 FP16 native proof",
                    lead_model_id="gpt-6.1-sol",
                    worker_model_ids=["gpt-6.1-sol"],
                    scope_status="project_scoped",
                ),
            ),
            services=(
                dict(
                    provider_id="m07-fp16-comfy",
                    endpoint=ENDPOINT,
                    capabilities=["image"],
                    model_id=None,
                    available=None,
                ),
            ),
            provider_options={
                "image_profiles": {"m07-fp16-comfy": REVISION},
                "minimum_free_vram_bytes": 1024**3,
            },
            runtime=dict(
                cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
                lead_model="gpt-6.1-sol",
                worker_model="gpt-6.1-sol",
                worker_tools=["submit_generation", "get_job", "get_artifact", "view"],
                profile_id="authoring",
                skill_directories=[],
                mcp_servers={},
                max_continues=5,
            ),
        )
        services = Services.create(settings)
        configure_services(services)
        provider = services.extension("providers")
        provider.client.close()
        provider.client = httpx.Client(
            timeout=120,
            transport=ObservedTransport(services, supervisor),
            trust_env=False,
            follow_redirects=False,
        )
        provider.owns_client = True
        fixture = fixtures(services)
        save("fixture.json", fixture)
        (ROOT / "pair.private").write_text(f"{ORIGIN}/#pair={services.auth.issue_pairing()}")
        (ROOT / "pair.private").chmod(0o600)
        report["host"] = {"pid": os.getpid(), "start_ticks": ticks(os.getpid()), "origin": ORIGIN}
        report["ready_for_native_queues"] = True
        save("host-report.json", report)
        FixtureServer(
            uvicorn.Config(
                create_app(settings, services=services, manage_features=True),
                host="127.0.0.1",
                port=18959,
                log_level="warning",
            )
        ).run()
    except Exception as error:
        report["blocker"] = type(error).__name__ + ": " + str(error)[:512]
    finally:
        if supervisor:
            supervisor.close()
            report["guard_reasons"] = supervisor.reasons
        if proc:
            report["owned_comfy"]["exit_code"] = proc.returncode
        if services:
            pid = services.registry.active_id()
            assert pid is not None
            with services.registry.project_store(pid).transaction(write=False) as tx:
                report["final_jobs"] = tx.all("jobs")
                report["final_attempts"] = tx.all("attempts")
                report["final_inputs"] = tx.all("job_inputs")
                report["final_reservations"] = [
                    dict(r) for r in tx.connection.execute("SELECT * FROM budget_reservations")
                ]
                report["final_effective"] = tx.all("copilot.effective")
                report["final_work"] = tx.all("work")
                report["final_invocations"] = tx.all("invocations")
            with services.resources.store.transaction(write=False) as tx:
                report["remaining_claims"] = [
                    dict(r) for r in tx.connection.execute("SELECT * FROM resource_claims")
                ]
            services.close()
        report["preservation_after"] = preserved()
        assert report["preservation_after"] == old
        report["actual_generation_started"] = bool(list(ROOT.glob("prompt-request-*.json")))
        report["finished_epoch"] = time.time()
        save("host-report.json", report)


if __name__ == "__main__":
    main()
