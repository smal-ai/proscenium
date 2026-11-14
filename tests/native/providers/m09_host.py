"""M09 actual production lifespan with observation-only bounded acceptance hooks.

No HTTP provider mock, native tool replacement or findings injection. Private runtime
files stay ignored. This host is authorised for two VLM calls and two cloud roots only.
"""

import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from copilot.session import CopilotSession
from PIL import Image
from proscenium.api.composition import configure_services
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.copilot.tools import Tools
from proscenium.services import Services
from proscenium.sources import closure_digest
from proscenium.sources.common import git

REPO = Path(__file__).resolve().parents[3]
ROOT = Path(__file__).with_name("m09_runtime")
PROJECT_ROOT = Path.home() / ".cache/proscenium-development/milestones/m09-20261114/project"
ORIGIN = "http://127.0.0.1:18909"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def append(name: str, value: Any) -> None:
    with (ROOT / name).open("a") as stream:
        stream.write(json.dumps({"at": time.time(), **value}) + "\n")


def memory() -> int:
    fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(fields["MemAvailable"].split()[0]) * 1024


class ObservedHTTP(httpx.BaseTransport):
    def __init__(self) -> None:
        self.delegate = httpx.HTTPTransport(retries=0, trust_env=False)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        is_vlm = request.method == "POST" and request.url.path == "/v1/chat/completions"
        if is_vlm:
            prior = (
                (ROOT / "vlm.jsonl").read_text().splitlines()
                if (ROOT / "vlm.jsonl").exists()
                else []
            )
            count = sum(json.loads(line)["phase"] == "dispatch" for line in prior)
            if count >= 2 or memory() < 4 * 1024**3:
                raise RuntimeError("M09 bounded allocation or protected host-memory floor")
            payload = json.loads(request.content)
            images = [
                base64.b64decode(c["image_url"]["url"].split(",", 1)[1])
                for m in payload["messages"]
                if isinstance(m["content"], list)
                for c in m["content"]
                if c["type"] == "image_url"
            ]
            assert len(images) <= 2 and payload["max_tokens"] <= 512
            for raw in images:
                with Image.open(io.BytesIO(raw)) as image:
                    assert image.size == (640, 360)
            append(
                "vlm.jsonl",
                dict(
                    phase="dispatch",
                    index=count + 1,
                    model=payload["model"],
                    max_tokens=payload["max_tokens"],
                    body_sha256=sha(request.content),
                    image_sha256=[sha(i) for i in images],
                    mem_available=memory(),
                ),
            )
        started = time.monotonic()
        response = self.delegate.handle_request(request)
        response.read()
        if is_vlm:
            append(
                "vlm.jsonl",
                dict(
                    phase="response",
                    status=response.status_code,
                    seconds=time.monotonic() - started,
                    response_sha256=sha(response.content),
                    body=response.json(),
                    mem_available=memory(),
                ),
            )
        return response

    def close(self) -> None:
        self.delegate.close()


def observe_native() -> None:
    original_send = CopilotSession.send
    original_tool = Tools.invoke

    async def send(session, prompt, **kwargs):
        prior = (
            (ROOT / "sdk-send.jsonl").read_text().splitlines()
            if (ROOT / "sdk-send.jsonl").exists()
            else []
        )
        if len(prior) >= 2:
            raise RuntimeError("M09 two cloud-root allocation exhausted")
        append("sdk-send.jsonl", dict(session_id=session.session_id, prompt=prompt, **kwargs))

        async def guard():
            await asyncio.sleep(180)
            append(
                "native-events.jsonl",
                dict(type="owned_request_deadline_abort", session_id=session.session_id),
            )
            await session.abort()

        deadline = asyncio.create_task(guard())

        def event(value):
            data = value.to_dict()
            kind = data.get("type", "")
            if kind == "session.idle":
                deadline.cancel()
            if kind in {
                "session.idle",
                "session.error",
                "user.message",
                "tool.execution_start",
                "tool.execution_complete",
                "assistant.message",
            }:
                # Tool responses are separately captured at the actual bound handler.
                if kind in {"tool.execution_start", "tool.execution_complete"}:
                    inner = data.get("data", {})
                    inner = {
                        k: v
                        for k, v in inner.items()
                        if k in {"toolCallId", "toolName", "tool_name", "success", "error"}
                    }
                    data = {"type": kind, "data": inner}
                append("native-events.jsonl", dict(session_id=session.session_id, event=data))

        session.on(event)
        return await original_send(session, prompt, **kwargs)

    async def invoke(tools, name, invocation):
        result = await original_tool(tools, name, invocation)
        if name == "request_vlm_review":
            text = result.text_result_for_llm
            append(
                "tool-results.jsonl",
                dict(
                    tool=name,
                    result=text,
                    sha256=sha(text.encode()),
                    invocation={
                        k: getattr(invocation, k, None)
                        for k in ("tool_call_id", "session_id", "turn_id")
                    },
                ),
            )
        return result

    setattr(CopilotSession, "send", send)
    setattr(Tools, "invoke", invoke)


def fixtures(services: Services) -> dict:
    owner = Caller("owner", "m09-scripted-test-owner")
    projects = services.extension("projects")
    PROJECT_ROOT.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    project, _ = projects.open(
        owner,
        dict(
            schema_version="1.0",
            operation_id="m09-create-external-project",
            path=str(PROJECT_ROOT),
            create=True,
            name="M09 read-only critique fixture",
        ),
    )
    project_id = project["project_id"]
    projects.activate(
        owner,
        dict(
            schema_version="1.0",
            operation_id="m09-activate-project",
            project_id=project_id,
            expected_revision=project["state_revision"],
        ),
    )
    services.gate.resume(
        project_id,
        reconciliation_evidence="explicit coordinator M09 acceptance execution allocation",
    )
    store = services.registry.project_store(project_id)
    with store.transaction() as tx:
        tx.put(
            "work",
            "m09-fixture-work",
            dict(
                schema_version="1.0",
                work_id="m09-fixture-work",
                project_id=project_id,
                originating_request_id="m09-fixture-no-native-request",
                directive_request_ids=[],
                state="running",
                state_revision=0,
                native_session_id="fixture-only-no-native-session",
                job_ids=[],
                generation_task_id="m09-fixture-task",
            ),
            schema="WorkRecord",
        )
        tx.put(
            "invocations",
            "m09-fixture-invocation",
            dict(work_id="m09-fixture-work", owner_epoch=services.ownership.epoch, state="running"),
        )
    caller = Caller(
        "tool",
        "m09-fixture-publisher",
        project_id,
        "m09-fixture-work",
        services.ownership.epoch,
        operations=("publishOutputs", "registerToolArtifact"),
        invocation_id="m09-fixture-invocation",
    )
    artifacts = services.extension("artifacts")
    raw = (REPO / "docs/evidence/m01/clean-v2.png").read_bytes()
    artifact, _ = artifacts.upload(owner, project_id, "m09-clean-target", raw, "image/png")
    root = PROJECT_ROOT
    telemetry = dict(
        image_artifact_id=artifact["artifact_id"],
        width_pixels=artifact["dimensions"]["width"],
        height_pixels=artifact["dimensions"]["height"],
        pixel_count=640 * 360,
        sha256=artifact["sha256"],
        measurement="Pillow decode of the exact registered PNG; these are pixel dimensions, not inferred scene measurements.",
    )
    staging = root / ".proscenium/tool-output/staging"
    staging.mkdir(parents=True, exist_ok=True)
    data = canonical(telemetry)
    (staging / "telemetry.json").write_bytes(data)
    artifacts.register(
        caller,
        dict(
            schema_version="1.0",
            operation_id="m09-register-telemetry",
            project_id=project_id,
            artifact_id="m09-pixel-telemetry",
            staging_path=".proscenium/tool-output/staging/telemetry.json",
            kind="document",
            purpose="metadata",
            sha256=sha(data),
            media_type="application/json",
        ),
    )
    source = canonical(
        dict(
            fixture=True,
            origin="Read-only copy of accepted M01 clean-v2.png",
            artifact_id=artifact["artifact_id"],
            image_sha256=artifact["sha256"],
        )
    )
    name = "assets/m09-fixture.json"
    (root / "assets").mkdir(exist_ok=True)
    (root / name).write_bytes(source)
    git(root, "config", "user.name", "M09 scripted acceptance fixture")
    git(root, "config", "user.email", "m09@example.invalid")
    git(root, "add", "--", name, ".gitattributes")
    git(root, "commit", "-m", "M09 labelled read-only image fixture")
    revision = git(root, "rev-parse", "HEAD").decode().strip()
    closure = dict(
        entry_path=name,
        files=[
            dict(
                destination=name,
                origin=dict(
                    kind="git",
                    repository_id=project["repository_id"],
                    commit=revision,
                    path=name,
                    sha256=sha(source),
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
            publication_id="m09-fixture-publication",
            project_id=project_id,
            items=[
                dict(
                    item_id="m09-image-item",
                    version_id="m09-image-version",
                    kind="asset",
                    name="M09 critique target",
                    description="Labelled M01 image fixture. No new Blender execution.",
                    expected_current_version_id=None,
                    source_commit=revision,
                    source=closure,
                    artifact_ids=[artifact["artifact_id"]],
                    preview_requests=[],
                )
            ],
        ),
    )
    assert caller.work_id is not None
    with store.transaction() as tx:
        work = tx.require("work", caller.work_id)
        tx.put(
            "work",
            caller.work_id,
            {**work, "state": "completed", "state_revision": 1},
            expected_revision=0,
            schema="WorkRecord",
        )
        assert not tx.all("reviews") and not tx.all("jobs") and not tx.all("requests")
    target = dict(
        origin="concept",
        artifact_id=artifact["artifact_id"],
        displayed_sha256=artifact["sha256"],
        dimensions=artifact["dimensions"],
        item_id="m09-image-item",
        version_id="m09-image-version",
        shot_id=None,
        render_job_id=None,
        source_frame=None,
        media_artifact_id=None,
    )
    native_review = dict(
        schema_version="1.0",
        review_id="m09-native-review",
        project_id=project_id,
        targets=[target],
        telemetry_artifact_ids=["m09-pixel-telemetry"],
        criteria="Return exactly two very short findings, under 12 words per observation and recommendation. First use visual evidence: describe the blue object shape/location. Second use measured evidence from the supplied pixel telemetry: state the image width and height in pixels. No hidden reasoning, no scene dimensions, no user approval. Output only structured JSON.",
    )
    prompt = (
        "M09 bounded read-only acceptance. Do not delegate or create children. Do not edit any file, run Blender/Unity, generate images, request extra reviews or access raw findings. "
        "Call request_vlm_review exactly ONCE with the following exact ReviewRequest JSON, then report only its ticket/status and stop. This is the only requested action. "
        + json.dumps(native_review)
    )
    (ROOT / "native-review-prompt.txt").write_text(prompt)
    return dict(
        project=project,
        artifact=artifact,
        telemetry=telemetry,
        target=target,
        publication=publication,
        source_commit=revision,
        source_path=str(root / name),
        source_sha256=sha(source),
        reviews_after_publication=0,
        native_review=native_review,
    )


def main(*, resume: bool = False) -> None:
    if (ROOT / "host-started.json").exists() and not resume:
        raise SystemExit(
            "Original M09 host exists. Reconcile it, do not start a duplicate acceptance run."
        )
    runtime = json.loads(subprocess.check_output(["lms", "ps", "--json"]))
    (ROOT / "loaded.json").write_text(
        json.dumps({"models": runtime, "mem_available": memory()}, indent=2)
    )
    settings = Settings(
        data_directory=ROOT / "machine",
        origin=ORIGIN,
        frontend_directory=REPO / "apps/frontend/dist",
        profiles=(
            dict(
                profile_id="authoring",
                name="M09 read-only acceptance",
                lead_model_id="gpt-6.1-sol",
                worker_model_ids=["gpt-6.1-sol"],
                scope_status="project_scoped",
            ),
        ),
        services=(
            dict(
                provider_id="m09-vlm",
                endpoint="http://127.0.0.1:1234",
                capabilities=["vlm"],
                model_id="google/gemma-4-e4b",
                available=None,
            ),
        ),
        provider_options=dict(review_provider_id="m09-vlm", vlm_max_tokens=512),
        runtime=dict(
            cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            lead_model="gpt-6.1-sol",
            worker_model="gpt-6.1-sol",
            worker_tools=["view", "request_vlm_review", "get_artifact"],
            profile_id="authoring",
            skill_directories=[],
            mcp_servers={},
            max_continues=5,
        ),
    )
    observe_native()
    services = Services.create(settings)
    configure_services(services)
    provider = services.extension("providers")
    provider.client.close()
    provider.client = httpx.Client(
        timeout=120, transport=ObservedHTTP(), follow_redirects=False, trust_env=False
    )
    provider.owns_client = True
    if resume:
        result = json.loads((ROOT / "fixture.json").read_text())
        services.gate.resume(
            result["project"]["project_id"],
            reconciliation_evidence="explicit same-allocation acceptance host restart after concrete VLM truncation defect",
        )
    else:
        result = fixtures(services)
    (ROOT / "fixture.json").write_text(json.dumps(result, indent=2))
    (ROOT / "pair.private").write_text(f"{ORIGIN}/#pair={services.auth.issue_pairing()}")
    os.chmod(ROOT / "pair.private", 0o600)
    (ROOT / "host-started.json").write_text(
        json.dumps({"pid": os.getpid(), "origin": ORIGIN, "at": time.time()})
    )
    try:
        uvicorn.run(
            create_app(settings, services=services, manage_features=True),
            host="127.0.0.1",
            port=18909,
            log_level="warning",
        )
    finally:
        services.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    main(resume=parser.parse_args().resume)
