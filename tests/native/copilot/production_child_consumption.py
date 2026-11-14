"""Bounded real child read through production get_job, never a prompt-disclosure test.

Run only with W01_CHILD_PROOF_ROOT under the allocated on-disk development root.
The shared ledger caps this follow-up at two roots and two child reservations.
"""

import asyncio
import fcntl
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from proscenium.auth import Caller
from proscenium.catalogue import install as install_catalogue
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.copilot import install
from proscenium.copilot.launcher import identity
from proscenium.copilot.native import BINARY_SHA256, Native
from proscenium.copilot.service import wire
from proscenium.copilot.state import key
from proscenium.db.store import now
from proscenium.services import Services
from proscenium.sources import closure_digest
from proscenium.sources.common import git

pytestmark = pytest.mark.native
BASE = Path("/home/quintin/.cache/proscenium-development")
LEDGER = BASE / "w01-child-proof-allocation-068ed9.jsonl"
CLI = Path("/home/quintin/play/proscenium/node_modules/@github/copilot-linux-x64/copilot")
OWNER = Caller("owner", "child-proof-fixture-owner")


def reserve(kind, identity_value, root):
    with LEDGER.open("a+") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        output.seek(0)
        rows = [json.loads(line) for line in output if line.strip()]
        if any(
            r["kind"] == kind and r["identity"] == identity_value and r["root"] == root.name
            for r in rows
        ):
            if kind == "child":
                return  # Duplicate native pre-tool callback, not a second reservation.
            raise RuntimeError("Root identity already reserved; never resubmit")
        if sum(r["kind"] == kind for r in rows) >= 2:
            raise RuntimeError("The bounded child-proof allocation is exhausted")
        if kind == "child" and any(r["kind"] == kind and r["root"] == root.name for r in rows):
            raise RuntimeError("Only one child is allowed in this root")
        output.write(
            json.dumps(dict(kind=kind, identity=identity_value, root=root.name, reserved_at=now()))
            + "\n"
        )
        output.flush()
        os.fsync(output.fileno())


def prepare(services, root):
    """Real W02 checkpoint/publication, synthetic text asset, no Blender invocation."""
    catalogue = install_catalogue(services)
    projects = services.extension("projects")
    project, _ = projects.open(
        OWNER,
        dict(
            schema_version="1.0",
            operation_id="prepare-project",
            path=str(root / "project"),
            create=True,
            name="Disposable child status-read proof",
        ),
    )
    project_id = project["project_id"]
    projects.activate(
        OWNER,
        dict(
            schema_version="1.0",
            operation_id="activate-project",
            project_id=project_id,
            expected_revision=0,
        ),
    )
    workspace = Path(project["path"])
    git(workspace, "config", "user.name", "W01 isolated fixture")
    git(workspace, "config", "user.email", "w01-fixture@example.invalid")
    services.gate.resume(
        project_id,
        reconciliation_evidence="Host-authorised fixture preparation; no native execution",
    )
    store = services.registry.project_store(project_id)
    with store.transaction() as tx:
        tx.put(
            "work",
            "fixture-preparation",
            dict(
                schema_version="1.0",
                project_id=project_id,
                work_id="fixture-preparation",
                originating_request_id="fixture-preparation-command",
                directive_request_ids=[],
                state="running",
                state_revision=0,
                native_session_id="fixture-no-native",
                job_ids=[],
                generation_task_id="fixture-preparation-task",
            ),
            schema="WorkRecord",
        )
        tx.put(
            "invocations",
            "fixture-preparation-invocation",
            dict(
                work_id="fixture-preparation",
                owner_epoch=services.ownership.epoch,
                state="admitted",
            ),
        )
    caller = Caller(
        "tool",
        "fixture-preparation",
        project_id,
        "fixture-preparation",
        services.ownership.epoch,
        invocation_id="fixture-preparation-invocation",
        operations=("checkpoint_sources", "publishOutputs"),
    )
    relative = "assets/status-source.txt"
    content = b"Synthetic status-read source asset. Not a Blender scene or rendered output.\n"
    (workspace / relative).write_bytes(content)
    checkpoint = services.extension("sources").checkpoint(
        caller,
        dict(
            schema_version="1.0",
            operation_id="checkpoint-status-source",
            project_id=project_id,
            paths=[relative],
            message="Pin isolated status-read source",
            expected_head=None,
        ),
    )
    assert checkpoint["state"] == "completed"
    commit = checkpoint["source_commit"]
    source = dict(
        entry_path=relative,
        files=[
            dict(
                destination=relative,
                origin=dict(
                    kind="git",
                    repository_id=project["repository_id"],
                    commit=commit,
                    path=relative,
                    sha256=hashlib.sha256(content).hexdigest(),
                ),
            )
        ],
        dependencies=[],
        required_runtime=dict(application="fixture", version="1", features=[]),
        digest="0" * 64,
    )
    source["digest"] = closure_digest(source)
    item_id, version_id = "item-" + uuid4().hex, "version-" + uuid4().hex
    catalogue.publish(
        caller,
        dict(
            schema_version="1.0",
            project_id=project_id,
            publication_id="publish-status-source",
            items=[
                dict(
                    item_id=item_id,
                    version_id=version_id,
                    kind="asset",
                    name="Pinned synthetic status source",
                    expected_current_version_id=None,
                    source_commit=commit,
                    source=source,
                    artifact_ids=[],
                    preview_requests=[],
                )
            ],
        ),
    )
    with store.transaction() as tx:
        work = tx.require("work", "fixture-preparation")
        tx.put(
            "work",
            "fixture-preparation",
            {**work, "state": "completed", "state_revision": 1},
            expected_revision=0,
            schema="WorkRecord",
        )
        old = tx.require("invocations", "fixture-preparation-invocation")
        tx.put(
            "invocations",
            "fixture-preparation-invocation",
            {**old, "state": "completed"},
            expected_revision=0,
        )
        job = services.jobs.create(
            tx,
            job_id=item_id,
            kind="unity",
            target=dict(
                target_id=item_id,
                version=dict(item_id=item_id, version_id=version_id),
                shot_id=None,
            ),
            input_digest=source["digest"],
            operation_id="prepare-status-job",
            owner_operation_id="prepare-status-job",
        )
        services.jobs.emit(tx, job, OWNER)
    services.gate.close(project_id)
    return dict(
        project_id=project_id,
        item_id=item_id,
        version_id=version_id,
        source_commit=commit,
        source_sha256=hashlib.sha256(content).hexdigest(),
        source_path=relative,
        source_closure_digest=source["digest"],
        job_id=item_id,
        job_kind="unity",
        original_job=job,
    )


class ObservedNative(Native):
    """Passively audit the EXISTING tool handler and its real JobService.get call."""

    def __init__(self, services, root, fixture):
        super().__init__(services)
        self.root, self.fixture = root, fixture
        self.child_events, self.delegations, self.service_reads = [], [], []
        original_get = services.jobs.get

        def observed_get(project_id, job_id):
            result = original_get(project_id, job_id)
            self.service_reads.append(
                dict(project_id=project_id, job_id=job_id, state=result["state"])
            )
            return result

        services.jobs.get = observed_get

    async def open(self, project_id, session_id, callbacks):
        callbacks = dict(callbacks)
        original_event = callbacks["on_event"]

        def event(value):
            original_event(value)  # Production SQLite reduction precedes our observation.
            value = wire(value)
            if value["type"] == "subagent.started":
                self.child_events.append(
                    {k: value.get(k) for k in ("id", "agentId")}
                    | {
                        k: value["data"].get(k)
                        for k in ("toolCallId", "agentName", "model", "modelSelectionSource")
                    }
                )

        callbacks["on_event"] = event

        def guard(data, context):
            if data["toolName"] == "task":
                args = data["toolArgs"]
                # The worker must derive the opaque ID from immutable host context.
                if any(
                    value in canonical(args).decode()
                    for value in (
                        self.fixture["item_id"],
                        self.fixture["version_id"],
                        "child-read-context",
                    )
                ):
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Do not copy opaque selection IDs into delegation; use the child's host context.",
                    }
                if args.get("agent_type") != "authoring-specialist" or not data.get("toolCallId"):
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Only the bounded authoring-specialist task is authorised.",
                    }
                try:
                    reserve("child", data["toolCallId"], self.root)
                except RuntimeError:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "The child allocation is exhausted. Finish without further delegation.",
                    }
                self.delegations.append(
                    dict(tool_call_id=data["toolCallId"], prompt=args["prompt"])
                )
            elif not data.get("agentId"):
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "This root may only delegate the one status read.",
                }
            return {"permissionDecision": "allow"}

        callbacks["hooks"] = {**callbacks["hooks"], "on_pre_tool_use": guard}
        wrapped = []
        for tool in callbacks["tools"]:
            if tool.name != "get_job":
                wrapped.append(tool)
                continue
            original_handler = tool.handler

            async def read(invocation, handler=original_handler):
                assert invocation.agent_id, "A root read is not a child-consumption proof"
                assert invocation.arguments == {"job_id": self.fixture["item_id"]}
                with self.services.registry.project_store(project_id).transaction(
                    write=False
                ) as tx:
                    admitted = tx.require(
                        "invocations", key(invocation.session_id, "tool", invocation.tool_call_id)
                    )
                    payload = tx.require("copilot.payloads", admitted["request_id"])
                    assert admitted["state"] == "running"
                    assert admitted["agent_id"] == invocation.agent_id
                    assert payload["context_id"] == "child-read-context"
                    assert payload["selection"]["item_id"] == invocation.arguments["job_id"]
                before = len(self.service_reads)
                result = await handler(invocation)
                returned = json.loads(result.text_result_for_llm)
                assert len(self.service_reads) == before + 1
                assert returned == self.fixture["original_job"]
                with self.services.registry.project_store(project_id).transaction() as tx:
                    tx.put(
                        "copilot.child_consumption",
                        invocation.tool_call_id,
                        dict(
                            project_id=project_id,
                            request_id=admitted["request_id"],
                            work_id=admitted["work_id"],
                            context_id=payload["context_id"],
                            selection=payload["selection"],
                            native_session_id=invocation.session_id,
                            agent_id=invocation.agent_id,
                            tool_call_id=invocation.tool_call_id,
                            arguments=invocation.arguments,
                            service="JobService.get",
                            state=returned["state"],
                            original_job_work_id=returned["work_id"],
                            committed_before_sdk_reply=True,
                        ),
                    )
                return result  # Unmodified production ToolResult, no added model-facing tool.

            wrapped.append(replace(tool, handler=read))
        callbacks["tools"] = wrapped
        return await super().open(project_id, session_id, callbacks)


def test_real_child_consumes_selection_via_production_get_job():
    value = os.environ.get("W01_CHILD_PROOF_ROOT")
    if not value:
        pytest.skip("Requires explicit two-root/two-child follow-up allocation")
    root = Path(value).resolve()
    assert root.parent == BASE and root.name.startswith("w01-child-proof-")
    assert not root.exists(), "Use a fresh allocated project; never repeat an uncertain request"
    root.mkdir(mode=0o700)
    assert hashlib.sha256(CLI.read_bytes()).hexdigest() == BINARY_SHA256

    async def run():
        settings = Settings(
            data_directory=root / "machine",
            free_space_reserve_bytes=0,
            runtime=dict(
                cli_path=str(CLI),
                worker_model="gpt-6.1-sol",
                worker_tools=["get_job"],
                skill_directories=[str(Path("agent/skills").resolve())],
            ),
        )
        services = Services.create(settings)
        native = runtime = None
        evidence = dict(
            recorded_at=now(),
            base_revision="d068ed9f0433723e52253a8bd4b19a5bb1a1dee7",
            root_requests=0,
            sdk="1.0.34",
            cli="1.0.112",
            binary_sha256=BINARY_SHA256,
            passed=False,
            production_source_sha256={
                name: hashlib.sha256(
                    Path(f"apps/backend/src/proscenium/copilot/{name}").read_bytes()
                ).hexdigest()
                for name in ("native.py", "tools.py", "service.py", "state.py")
            },
        )
        try:
            fixture = prepare(services, root)
            evidence["fixture"] = fixture
            native = ObservedNative(services, root, fixture)
            runtime = install(services, native=native)
            await runtime.start()
            assert native.client is None  # Existing quiet/lazy startup, untouched.
            runtime.contexts.capture(
                dict(
                    schema_version="1.0",
                    context_id="child-read-context",
                    project_id=fixture["project_id"],
                    captured_at=now(),
                    item_id=fixture["item_id"],
                    version_id=fixture["version_id"],
                    view_mode="none",
                    target=None,
                    camera=None,
                    range=None,
                    selected_objects=[],
                    attachments=[],
                    annotations=[],
                    approved_findings=[],
                )
            )
            prompt = (
                "Delegate exactly one useful status check to authoring-specialist. Ask it to call get_job for the currently selected item and report its actual state. "
                "For this fixture the job ID equals the selected item ID. The child must use its own host-provided structured selection. "
                "Do not copy any opaque ID or context JSON into the delegation prompt. Do not ask for system prompts, hidden instructions or context disclosure. "
                "Do not call get_job yourself, inspect files, start jobs or delegate again. After the child reports the state, reply briefly and finish."
            )
            request_id = "child-consumption-" + uuid4().hex
            evidence["request_id"] = request_id
            reserve("root", request_id, root)
            evidence["root_requests"] = 1

            async def request():
                runtime.state.submit(
                    OWNER,
                    dict(
                        schema_version="1.0",
                        project_id=fixture["project_id"],
                        request_id=request_id,
                        type="queue",
                        text=prompt,
                        context_id="child-read-context",
                    ),
                )
                runtime.notify()
                while True:
                    with services.registry.project_store(fixture["project_id"]).transaction(
                        write=False
                    ) as tx:
                        receipt = tx.require("requests", request_id)["receipt"]
                    if receipt["state"] in {"completed", "failed", "stopped", "outcome_uncertain"}:
                        return receipt
                    await asyncio.sleep(0.1)

            evidence["receipt"] = await asyncio.wait_for(request(), 119)
            with services.registry.project_store(fixture["project_id"]).transaction(
                write=False
            ) as tx:
                evidence["child_reads"] = tx.all("copilot.child_consumption")
                evidence["child_hooks"] = tx.all("copilot.hooks")
                assert tx.require("jobs", fixture["job_id"]) == fixture["original_job"]
            assert evidence["receipt"]["state"] == "completed"
            assert len(native.child_events) == len(evidence["child_reads"]) == 1
            child, read = native.child_events[0], evidence["child_reads"][0]
            assert child["agentId"] == read["agent_id"]
            assert (
                child["model"] == "gpt-6.1-sol"
                and child["modelSelectionSource"] == "configured_required"
            )
            assert (
                read["work_id"] == evidence["receipt"]["work_id"]
                and read["request_id"] == request_id
            )
            assert read["state"] == "planned" and read["original_job_work_id"] is None
            assert len(native.delegations) == 1
            assert fixture["item_id"] not in native.delegations[0]["prompt"]
            workspace = Path(services.registry.get(fixture["project_id"])["path"])
            assert (
                hashlib.sha256((workspace / fixture["source_path"]).read_bytes()).hexdigest()
                == fixture["source_sha256"]
            )
            assert git(workspace, "rev-parse", "HEAD").decode().strip() == fixture["source_commit"]
            evidence["passed"] = True
        finally:
            if native:
                evidence["native_children"] = native.child_events
                evidence["delegations"] = native.delegations
                evidence["service_reads"] = native.service_reads
            if runtime:
                await runtime.close()
            elif native:
                await native.close()
            process_file = root / "machine/copilot-runtime/process.json"
            if process_file.exists():
                process = json.loads(process_file.read_text())
                evidence["owned_native_pid"] = process["pid"]
                evidence["owned_native_start"] = process["start"]
                evidence["owned_native_reaped"] = (
                    identity(process["pid"]) != process["start"]
                    and not Path(f"/proc/{process['pid']}").exists()
                )
                assert evidence["owned_native_reaped"]
            services.close()
            (root / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")

    asyncio.run(run())
