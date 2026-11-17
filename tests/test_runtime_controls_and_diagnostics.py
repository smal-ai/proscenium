"""Offline control/projection agreement, durable diagnostics and owner status reads."""

import asyncio
import copy
import hashlib
import json
from threading import RLock
from types import SimpleNamespace

import httpx
import pytest
from copilot import CopilotClient
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.copilot.eligibility import execution_status
from proscenium.copilot.outcomes import record_tool_outcome
from proscenium.copilot.router import router
from proscenium.copilot.service import Copilot, is_consequential_shell
from proscenium.copilot.state import key, save
from proscenium.db.pagination import Cursors
from proscenium.projects.service import Projects
from test_activity_feedback import BINDING, PROJECT, REQUEST, SESSION, TOOL, WORK, event, start
from test_activity_feedback import runtime as activity_runtime
from test_request_targeting import context, request_record

OWNER = Caller("owner", "fixture-owner")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    async def blocked(*args, **kwargs):
        raise AssertionError("Native/model/provider execution is forbidden in these tests")

    def blocked_http(*args, **kwargs):
        raise AssertionError("External HTTP is forbidden in these tests")

    monkeypatch.setattr(CopilotClient, "start", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked_http)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    state, store, gate = activity_runtime.__wrapped__(tmp_path)
    services = state.services
    project = dict(
        schema_version="1.0",
        project_id=PROJECT,
        repository_id="repository-one",
        name="Offline fixture",
        path=str(tmp_path),
        native_session_id=SESSION,
        state="active",
        state_revision=1,
    )
    services.registry.get = lambda project_id: project
    services.registry.active_id = lambda: PROJECT
    services.settings = SimpleNamespace(
        runtime={}, profiles=[], generation_attempt_limit=3, data_directory=tmp_path / "machine"
    )
    services.registry.store = store
    services.activation_lock = RLock()
    services.reconcile_project_projection = lambda project_id: None
    services.extensions = {}
    services.cursors = Cursors(b"fixture-only-key")
    state.contexts = SimpleNamespace(validate_references=lambda tx, snapshot: None)
    with store.transaction() as tx:
        tx.put("contexts", "context-one", context(), schema="ContextSnapshot")
    return state, store, gate, project


def snapshot(runtime):
    state, _, _, _ = runtime
    return Projects(state.services).snapshot(PROJECT)["execution"]


def set_work(store, status):
    with store.transaction() as tx:
        work = tx.require("work", WORK)
        work.update(state=status, state_revision=tx.revision("work", WORK) + 1)
        save(tx, "work", WORK, work)


@pytest.mark.parametrize(
    "operation,chargeable",
    [
        ({"kind": "mcp", "toolName": tool, "args": {}}, chargeable)
        for tool, chargeable in [
            ("create_generation", True),
            ("generate_3d", True),
            ("generate_gaussian_world", True),
            ("get_addon_status", False),
            ("search_assets", False),
            ("execute_blender_code", False),
        ]
    ]
    + [
        ({"kind": "shell", "fullCommandText": command}, consequential)
        for command, consequential in [
            *[
                (
                    "python -c 'import json,pathlib; "
                    f'pathlib.Path("storyboards/plan.json").write_text(json.dumps({{"motion": "{prose}"}}))\'',
                    False,
                )
                for prose in [
                    "Very slow subtle axial push toward the dusty brushed-aluminium toggle",
                    "Push in toward the relay installation",
                    "install",
                    "installation",
                    "upgrade",
                ]
            ],
            (
                'node -e \'console.log(JSON.stringify({motion: "push in", action: "install upgrade"}))\'',
                False,
            ),
            (
                'cat <<\'EOF\' > storyboards/plan.json\n{"motion": "push in", "scene": "installation upgrade"}\nEOF',
                False,
            ),
            ("git status", False),
            ("git push origin main", True),
            ("pip install foo", True),
            ("uv pip install foo", True),
            ("npm install foo", True),
            ("sudo ls", True),
            ("scp file host:", True),
            ("git reset --hard", True),
            ("rm -rf build", True),
            ("curl -X POST https://example.com -d @file", True),
            ("python -c 'import os; os.system(\"git push origin main\")'", True),
            (
                "python -c 'import subprocess; subprocess.run(\"pip install foo\", shell=True)'",
                True,
            ),
            ("python -c 'import os; os.system(\"rm -rf build\")'", True),
        ]
    ]
    + [
        (
            {
                "kind": "shell",
                "fullCommandText": "echo ready",
                "commandSegments": [{"fullCommandText": "git push origin main"}],
            },
            True,
        ),
        ({"kind": "mcp", "toolName": "purchase", "args": {}}, True),
        ({"kind": "url", "url": "https://example.com/purchase"}, True),
    ],
)
@pytest.mark.parametrize("action", ["approve_once", "decline"])
def test_consequential_operations_wait_for_exact_owner_decision(
    runtime, operation, chargeable, action
):
    state, store, _, _ = runtime
    start(state)
    request = {**operation, "toolCallId": TOOL}
    native_id = "native-permission-one"
    with store.transaction() as tx:
        tx.put(
            "copilot.permissions",
            key(SESSION, native_id),
            {
                **BINDING,
                "session_id": SESSION,
                "tool_call_id": TOOL,
                "native_id": native_id,
                "phase": "pending",
                "fingerprint": hashlib.sha256(canonical(request)).hexdigest(),
            },
        )
    copilot = Copilot.__new__(Copilot)
    copilot.services = state.services
    copilot.state = state
    copilot.closing = False

    async def run():
        task = asyncio.create_task(copilot.permission(PROJECT, request, {"session_id": SESSION}))
        try:
            # Let the callback reach its persisted decision wait without external execution.
            await asyncio.sleep(0)
            with store.transaction(write=False) as tx:
                decisions = tx.all("decisions")
                if not chargeable:
                    assert decisions == []
                else:
                    assert len(decisions) == 1
                    decision = decisions[0]
                    validate("Decision", decision)
                    assert decision["state"] == "pending" and decision["resolved_by"] is None
                    assert decision["options"] == ["approve_once", "decline"]
                    assert tx.require("work", WORK)["state"] == "awaiting_user"
                    revision = tx.revision("decisions", decision["decision_id"])
            if chargeable:
                assert not task.done()
                body = dict(
                    schema_version="1.0",
                    operation_id="answer-one",
                    project_id=PROJECT,
                    expected_revision=revision,
                    fingerprint=decision["fingerprint"],
                    action=action,
                )
                with pytest.raises(DomainError, match="owner session required"):
                    state.answer(Caller("tool", "model"), decision["decision_id"], body)
                state.answer(OWNER, decision["decision_id"], body)
            return await asyncio.wait_for(task, 1)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    result = asyncio.run(run())
    with store.transaction(write=False) as tx:
        stage = tx.require("copilot.permissions", key(SESSION, native_id))
        assert stage["reply"] == ("reject" if chargeable and action == "decline" else "approve")
        assert stage["authority"] == ("owner" if chargeable else "automatic_policy")
        if chargeable:
            decision = tx.all("decisions")[0]
            assert decision["resolved_by"] == {"kind": "user", "id": OWNER.subject_id}
    assert result.kind == ("reject" if chargeable and action == "decline" else "approve-once")


@pytest.mark.parametrize(
    "command",
    [
        "git -C workspace push origin main",
        "git --no-pager push",
        "gh pr create",
        "gh release upload release output.zip",
        "sftp host",
        "rsync file host:",
        "su root",
        "pkexec ls",
        "systemctl restart demo",
        "service demo stop",
        "pip3 uninstall foo",
        "pipx install foo",
        "uv tool install foo",
        "uv add foo",
        "uv self update",
        "pnpm add foo",
        "yarn upgrade foo",
        "bun i foo",
        "npm publish",
        "apt install foo",
        "apt-get dist-upgrade",
        "dnf remove foo",
        "yum install foo",
        "apk install foo",
        "zypper purge foo",
        "pacman -S foo",
        "pacman -U foo",
        "cargo install foo",
        "gem install foo",
        "go install foo",
        "brew reinstall foo",
        "mcp-for-blender --host localhost install-addon",
        "git clean -df",
        "git checkout -- .",
        "git restore --worktree .",
        "rm -fr build",
        "rm -r build",
        "rm -f build",
        "rm --recursive --force build",
        "rm --force --recursive build",
        "curl https://example.com --upload-file output",
        "curl --data-binary @file https://example.com",
        "curl -F file=@output https://example.com",
        "curl -X PATCH https://example.com",
        "wget --post-file=output https://example.com",
        "service --status-all",
    ],
)
def test_consequential_shell_operation_signatures(command):
    assert is_consequential_shell(command)


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c 'print(\"No service changes or installations.\")'",
        "echo 'provider service status ok'",
    ],
)
def test_non_consequential_shell_prose_mentions_of_service_do_not_prompt(command):
    assert not is_consequential_shell(command)


@pytest.mark.parametrize(
    "protected", [".env", ".ssh/id_rsa", "credentials", "github_token", "/etc/shadow"]
)
def test_shell_payload_credential_denial_remains_automatic(runtime, protected):
    state, store, _, _ = runtime
    start(state)
    request = {
        "kind": "shell",
        "toolCallId": TOOL,
        "fullCommandText": f"python -c 'from pathlib import Path; print(Path(\"{protected}\").read_text())'",
    }
    native_id = "protected-permission"
    with store.transaction() as tx:
        tx.put(
            "copilot.permissions",
            key(SESSION, native_id),
            {
                **BINDING,
                "session_id": SESSION,
                "tool_call_id": TOOL,
                "native_id": native_id,
                "phase": "pending",
                "fingerprint": hashlib.sha256(canonical(request)).hexdigest(),
            },
        )
    copilot = Copilot.__new__(Copilot)
    copilot.services, copilot.state, copilot.closing = state.services, state, False
    result = asyncio.run(copilot.permission(PROJECT, request, {"session_id": SESSION}))
    assert result.kind == "reject"
    with store.transaction(write=False) as tx:
        assert tx.all("decisions") == []
        stage = tx.require("copilot.permissions", key(SESSION, native_id))
        assert stage["authority"] == "automatic_policy"
        assert "protected credential" in stage["denial_reason"]


@pytest.mark.parametrize(
    ("status", "hold", "steer"),
    [
        ("running", "active_work_running", True),
        ("awaiting_user", "awaiting_user", True),
        ("awaiting_resume", "awaiting_resume", False),
        ("stop_requested", "stop_requested", False),
        ("outcome_uncertain", "uncertain_stop", False),
        ("completed", None, False),
        ("idle", None, False),
    ],
)
def test_execution_snapshot_and_steer_admission_agree(runtime, status, hold, steer):
    state, store, _, project = runtime
    if status == "idle":
        with store.transaction() as tx:
            tx.connection.execute("DELETE FROM records WHERE namespace IN ('work','requests')")
    else:
        set_work(store, status)
    if status in {"stop_requested", "outcome_uncertain"}:
        stop = request_record("stop-one")
        stop["submission"] = dict(
            schema_version="1.0",
            request_id="stop-one",
            project_id=PROJECT,
            type="stop",
            target_work_id=WORK,
        )
        stop["receipt"].update(type="stop", state=status)
        with store.transaction() as tx:
            tx.put("requests", "stop-one", stop, schema="RequestRecord")
            tx.put(
                "copilot.stops",
                "stop-one",
                dict(
                    work_id=WORK,
                    phase="outcome_uncertain" if status == "outcome_uncertain" else "admitted",
                ),
            )
    if status == "awaiting_resume":
        project.update(state="awaiting_resume", state_revision=2)
    execution = snapshot(runtime)
    validate("ExecutionStatus", execution)
    assert execution["hold_reason_code"] == hold
    assert execution["can_steer_work_id"] == (WORK if steer else None)
    assert execution["can_stop_work_id"] == (
        WORK if status in {"running", "awaiting_user", "awaiting_resume"} else None
    )
    with store.transaction(write=False) as tx:
        assert state.control(tx)["fenced"] == execution["fenced"]
        assert execution_status(tx, "epoch", project) == execution
    body = request_record("steer-one", kind="steer")["submission"]
    if steer:
        assert state.submit(OWNER, body)[0]["state"] == "accepted"
    else:
        with pytest.raises(DomainError, match="no active work"):
            state.submit(OWNER, body)
    queue = request_record("new-queue")["submission"]
    assert state.submit(OWNER, queue)[0]["state"] == "held"


@pytest.mark.parametrize("fence", ["gate", "session", "effective", "activation"])
def test_running_work_cannot_be_steered_through_any_fence(runtime, fence):
    state, store, _, project = runtime
    with store.transaction() as tx:
        if fence == "gate":
            state.fence(tx, "native_turn_aborted")
        elif fence == "session":
            tx.put("copilot.session", "native", dict(session_id=SESSION, owner_epoch="old"))
        elif fence == "effective":
            tx.put("copilot.effective", REQUEST, dict(owner_epoch="old"))
        else:
            project.update(state="awaiting_resume", state_revision=2)
    execution = snapshot(runtime)
    assert execution["fenced"] and not execution["queue_released"]
    assert execution["can_steer_work_id"] is None
    with pytest.raises(DomainError, match="no active work"):
        state.submit(OWNER, request_record("steer-one", kind="steer")["submission"])


def test_idle_queue_paused_and_default_release_are_distinct(runtime):
    state, store, _, _ = runtime
    set_work(store, "completed")
    assert snapshot(runtime)["queue_released"]
    with store.transaction() as tx:
        save(
            tx,
            "copilot.control",
            "gate",
            dict(fenced=False, epoch="epoch", revision=1, queue_released=False),
        )
    assert snapshot(runtime)["hold_reason_code"] == "queue_paused"
    assert state.submit(OWNER, request_record("held-new")["submission"])[0]["state"] == "held"


def test_historical_uncertain_resume_does_not_override_an_explicitly_released_gate(runtime):
    _, store, _, _ = runtime
    set_work(store, "completed")
    with store.transaction() as tx:
        tx.put("copilot.resumes", "old-resume", dict(state="outcome_uncertain"))
        tx.put("copilot.resumes", "release", dict(state="completed"))
        tx.put(
            "copilot.control",
            "gate",
            dict(
                fenced=False,
                epoch="epoch",
                revision=1,
                reconciled_epoch="epoch",
                queue_released=True,
            ),
        )
    execution = snapshot(runtime)
    assert execution["queue_released"] and execution["hold_reason_code"] is None


@pytest.mark.parametrize("change", ["abort", "fence", "restart", "parent_completed"])
def test_child_activity_is_preserved_and_marked_uncertain(runtime, change):
    state, store, _, _ = runtime
    state.reduce(
        PROJECT, SESSION, event("wrapper", "tool.execution_start", toolCallId=TOOL, toolName="task")
    )
    child = event(
        "child-start",
        "subagent.started",
        toolCallId=TOOL,
        agentName="storyboard",
        agentDisplayName="Layout api_key=private-value",
        model="worker-model",
    )
    child["agentId"] = "native-child"
    state.reduce(PROJECT, SESSION, child)
    agents = snapshot(runtime)["active_agents"]
    assert len(agents) == 1 and agents[0]["native_agent_id"] == "native-child"
    assert agents[0]["name"] == "storyboard" and agents[0]["freshness"] == "live"
    assert agents[0]["display_name"] == "Layout api_key=[redacted]"
    assert agents[0]["model_id"] == "worker-model" and agents[0]["updated_at"]
    if change == "abort":
        state.reduce(PROJECT, SESSION, event("abort", "abort", reason="user_initiated"))
    elif change == "fence":
        with store.transaction() as tx:
            state.fence(tx, "awaiting_resume")
    elif change == "restart":
        state.services.ownership.epoch = "next-epoch"
    else:
        set_work(store, "completed")
    uncertain = snapshot(runtime)["active_agents"]
    assert len(uncertain) == 1 and uncertain[0]["state"] == "running"
    assert uncertain[0]["freshness"] == "uncertain"
    complete = event("child-complete", "subagent.completed", success=True)
    complete["agentId"] = "native-child"
    state.reduce(PROJECT, SESSION, complete)
    assert snapshot(runtime)["active_agents"] == []
    with store.transaction(write=False) as tx:
        inv = tx.require("invocations", agents[0]["invocation_id"])
        assert inv["display_name"] == agents[0]["display_name"]
        assert tx.events()["events"][-1]["payload"]["display_name"] == inv["display_name"]


@pytest.mark.parametrize("exit_code", [127, 1, 0, True, None])
@pytest.mark.parametrize("out_of_order", [False, True])
def test_structured_shell_exit_overrides_transport_success(
    runtime, caplog, exit_code, out_of_order
):
    state, store, _, _ = runtime
    complete = event(
        "complete",
        "tool.execution_complete",
        toolCallId=TOOL,
        success=True,
        result={"content": "Private stdout with password=never-log"},
    )
    if exit_code is not None:
        complete["data"]["shellExecution"] = {"exitCode": exit_code, "stdout": "private"}
    if out_of_order:
        state.reduce(PROJECT, SESSION, complete)
        with store.transaction(write=False) as tx:
            staged = tx.require("copilot.pending_events", key(SESSION, "complete"))["event"]["data"]
            assert "result" not in staged
            assert staged.get("shellExecution") == (
                {"exitCode": exit_code} if type(exit_code) is int else None
            )
    start(state)
    if out_of_order:
        state.drain_pending(PROJECT, SESSION)
    else:
        state.reduce(PROJECT, SESSION, complete)
    failed = type(exit_code) is int and exit_code != 0
    with store.transaction(write=False) as tx:
        payload = tx.events()["events"][-1]["payload"]
        assert payload["state"] == ("failed" if failed else "completed")
        assert payload.get("exit_code") == (exit_code if type(exit_code) is int else None)
        assert tx.require("invocations", key(SESSION, "tool", TOOL))["state"] == payload["state"]
        watermark = tx.watermark()
    if failed:
        assert f"Command exited with status {exit_code}." in payload["summary"]
        logs = [json.loads(r.message) for r in caplog.records if r.name == "proscenium.copilot"]
        assert len(logs) == 1
        assert logs[0]["event"] == "native_activity_problem"
        assert logs[0]["invocation_id"] == payload["invocation_id"]
        assert logs[0]["project_id"] == PROJECT and logs[0]["work_id"] == WORK
        assert logs[0]["exit_code"] == exit_code and "Private stdout" not in caplog.text
    state.replay_event(PROJECT, SESSION, complete)
    with store.transaction(write=False) as tx:
        assert tx.watermark() == watermark


def test_historical_completion_is_not_rewritten_by_new_shell_metadata(runtime):
    state, store, _, _ = runtime
    start(state)
    complete = event("complete", "tool.execution_complete", toolCallId=TOOL, success=True)
    state.reduce(PROJECT, SESSION, complete)
    with store.transaction(write=False) as tx:
        original = tx.require("invocations", key(SESSION, "tool", TOOL))
        watermark = tx.watermark()
    complete["data"]["shellExecution"] = {"exitCode": 127}
    state.replay_event(PROJECT, SESSION, complete)
    with store.transaction(write=False) as tx:
        assert tx.require("invocations", key(SESSION, "tool", TOOL)) == original
        assert tx.watermark() == watermark


@pytest.mark.parametrize("cancelled", [True, False])
def test_staged_child_keeps_cancelled_and_sanitised_display_name(runtime, cancelled):
    state, store, _, _ = runtime
    terminal = event(
        "child-end", "subagent.completed", cancelled=cancelled, agentDisplayName="x" * 300
    )
    terminal["agentId"] = "child-one"
    state.reduce(PROJECT, SESSION, terminal)
    with store.transaction(write=False) as tx:
        staged = tx.require("copilot.pending_events", key(SESSION, "child-end"))["event"]["data"]
        assert staged["cancelled"] is cancelled
        assert len(staged["agentDisplayName"]) == 200
    child = event("child-start", "subagent.started", agentName="worker", toolCallId=TOOL)
    child["agentId"] = "child-one"
    state.reduce(PROJECT, SESSION, child)
    state.drain_pending(PROJECT, SESSION)
    with store.transaction(write=False) as tx:
        inv = tx.require("invocations", key(SESSION, "agent", "child-one"))
        assert inv["state"] == ("cancelled" if cancelled else "completed")
        assert len(inv["display_name"]) == 200


@pytest.mark.parametrize("status", ["published", "retained_unpublished", "publication_blocked"])
def test_trusted_tool_outputs_join_events_snapshot_and_safe_warning(runtime, caplog, status):
    state, store, _, _ = runtime
    invocation_id = key(SESSION, "tool", TOOL)
    published = status == "published"
    failed = status == "publication_blocked"
    pins = [{"item_id": "item-one", "version_id": "version-one"}] if published else []
    with store.transaction() as tx:
        tx.put("artifacts", "image-one", dict(artifact_id="image-one", state="ready"))
        if published:
            tx.put(
                "versions",
                "version-one",
                dict(version_id="version-one", item_id="item-one", artifact_ids=["image-one"]),
            )
        record_tool_outcome(
            tx,
            invocation_id=invocation_id,
            session_id=SESSION,
            project_id=PROJECT,
            work_id=WORK,
            request_id=REQUEST,
            tool_name="publish_outputs",
            status="failed" if failed else "succeeded",
            artifact_ids=["image-one"],
            published_versions=pins,
            error_code="schema_invalid" if failed else None,
            phase="preflight",
            safe_summary="Storyboard source must be AuthoredShotPlan JSON. secret=private-value",
            issues=[dict(path="items[0].source", phase="schema", message="Use a JSON entry.")],
        )
    state.reduce(
        PROJECT,
        SESSION,
        event("start", "tool.execution_start", toolCallId=TOOL, toolName="publish_outputs"),
    )
    state.reduce(
        PROJECT,
        SESSION,
        event(
            "done",
            "tool.execution_complete",
            toolCallId=TOOL,
            success=not failed,
            error={"code": "failure", "message": '{"error":"invalid","message":"Raw JSON"}'},
        ),
    )
    with store.transaction(write=False) as tx:
        payload = tx.events()["events"][-1]["payload"]
    assert payload["artifact_ids"] == ["image-one"]
    assert payload["published_versions"] == pins and payload["output_status"] == status
    outputs = snapshot(runtime)["work_outputs"]
    assert outputs[0]["status"] == status and outputs[0]["artifact_ids"] == ["image-one"]
    assert outputs[0]["published_versions"] == pins
    if failed:
        assert (
            "AuthoredShotPlan JSON" in payload["summary"]
            and "items[0].source: Use a JSON entry." in payload["summary"]
        )
        assert "Raw JSON" not in payload["summary"] and "(failure)" not in payload["summary"]
        assert "private-value" not in caplog.text and "private-value" not in outputs[0]["summary"]
        record = json.loads(caplog.records[-1].message)
        assert record["phase"] == "preflight" and record["error_code"] == "schema_invalid"
        assert record["request_id"] == REQUEST and record["name"] == "publish_outputs"


def test_later_publication_resolves_only_same_work_failure_and_later_failure_blocks(runtime):
    state, store, _, _ = runtime
    with store.transaction() as tx:
        tx.put(
            "versions",
            "version-one",
            dict(version_id="version-one", item_id="item-one", artifact_ids=[]),
        )
        tx.put(
            "publications",
            "publication-one",
            dict(
                work_id=WORK,
                receipt=dict(versions=[dict(item_id="item-one", version_id="version-one")]),
            ),
        )
    for identity, work_id, status in [
        ("failure-one", WORK, "failed"),
        ("foreign-success", "other-work", "succeeded"),
        ("success-one", WORK, "succeeded"),
        ("failure-two", WORK, "failed"),
    ]:
        record_tool_outcome(
            store,
            invocation_id=identity,
            session_id=SESSION,
            project_id=PROJECT,
            work_id=work_id,
            request_id=REQUEST,
            tool_name="publish_outputs",
            status=status,
            published_versions=[dict(item_id="item-one", version_id="version-one")]
            if status == "succeeded"
            else [],
            error_code="schema_invalid" if status == "failed" else None,
            safe_summary="Repair the source.",
        )
        expected = "published" if identity == "success-one" else "publication_blocked"
        assert snapshot(runtime)["work_outputs"][0]["status"] == expected


def test_jobs_outputs_require_ready_recorded_artefacts_and_keep_work_attribution(runtime):
    state, store, _, project = runtime
    with store.transaction() as tx:
        for identity, status in [("ready", "ready"), ("missing", "pending")]:
            tx.put("artifacts", identity, dict(artifact_id=identity, state=status))
        tx.put(
            "jobs", "job-one", dict(work_id=WORK, artifact_ids=["ready", "missing", "unregistered"])
        )
        tx.put("jobs", "job-other", dict(work_id="foreign", artifact_ids=["ready"]))
        result = execution_status(tx, state.services.ownership.epoch, project)["work_outputs"][0]
    assert result["artifact_ids"] == ["ready"] and result["status"] == "retained_unpublished"


def test_failure_without_completion_event_stays_newer_than_previous_publication(runtime):
    state, store, _, _ = runtime
    start(state)
    with store.transaction() as tx:
        tx.put("versions", "version-one", dict(version_id="version-one", item_id="item-one"))
        record_tool_outcome(
            tx,
            invocation_id=key(SESSION, "tool", TOOL),
            session_id=SESSION,
            project_id=PROJECT,
            work_id=WORK,
            request_id=REQUEST,
            tool_name="publish_outputs",
            status="succeeded",
            published_versions=[dict(item_id="item-one", version_id="version-one")],
        )
    state.reduce(
        PROJECT, SESSION, event("done", "tool.execution_complete", toolCallId=TOOL, success=True)
    )
    record_tool_outcome(
        store,
        invocation_id="later-failure",
        session_id=SESSION,
        project_id=PROJECT,
        work_id=WORK,
        request_id=REQUEST,
        tool_name="publish_outputs",
        status="failed",
        error_code="schema_invalid",
        safe_summary="Repair the later source.",
    )
    result = snapshot(runtime)["work_outputs"][0]
    assert result["status"] == "publication_blocked" and result["reason_code"] == "schema_invalid"


def test_multiple_running_work_has_no_steer_and_uncertain_child_is_not_lost(runtime):
    state, store, _, project = runtime
    with store.transaction() as tx:
        work = tx.require("work", WORK)
        tx.put("work", "other-work", {**work, "work_id": "other-work"})
        tx.put(
            "invocations",
            "uncertain-child",
            dict(
                invocation_id="uncertain-child",
                native_id="child-one",
                name="worker",
                kind="child",
                state="outcome_uncertain",
                work_id=WORK,
                request_id=REQUEST,
                owner_epoch="epoch",
            ),
        )
        execution = execution_status(tx, "epoch", project)
    assert execution["can_steer_work_id"] is None
    assert execution["active_agents"][0]["freshness"] == "uncertain"
    with pytest.raises(DomainError, match="no active work"):
        state.submit(OWNER, request_record("steer-one", kind="steer")["submission"])


def test_legacy_failure_envelope_is_human_readable_not_raw_json(runtime):
    state, store, _, _ = runtime
    start(state)
    envelope = dict(
        error="schema_invalid",
        message="Storyboard source must be JSON.",
        details=dict(issues=[dict(path="source", message="Use JSON.")]),
    )
    state.reduce(
        PROJECT,
        SESSION,
        event(
            "failure",
            "tool.execution_complete",
            toolCallId=TOOL,
            success=False,
            error=dict(code="failure", message=json.dumps(envelope)),
        ),
    )
    with store.transaction(write=False) as tx:
        summary = tx.events()["events"][-1]["payload"]["summary"]
    assert summary == "Storyboard source must be JSON. source: Use JSON."


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_tool_turn_bubbles_are_suppressed_without_losing_completion(runtime, text):
    state, store, _, _ = runtime
    state.reduce(PROJECT, SESSION, event("turn-start", "assistant.turn_start"))
    state.reduce(
        PROJECT,
        SESSION,
        event(
            "empty",
            "assistant.message",
            messageId="empty-message",
            turnId="turn-one",
            content=text,
            toolRequests=[{"toolCallId": TOOL}],
        ),
    )
    state.reduce(
        PROJECT,
        SESSION,
        event("delta", "assistant.message_delta", messageId="empty-message", deltaContent=""),
    )
    with store.transaction(write=False) as tx:
        assert not tx.events()["events"]
        assert tx.require("copilot.completion", REQUEST)["public_terminal_message"] is False
    state.reduce(
        PROJECT,
        SESSION,
        event(
            "reply",
            "assistant.message",
            messageId="reply-one",
            turnId="turn-one",
            content="Completed the ordinary reply.",
        ),
    )
    state.reduce(PROJECT, SESSION, event("turn-end", "assistant.turn_end"))
    with store.transaction(write=False) as tx:
        assert len(tx.events()["events"]) == 1
        assert tx.require("copilot.completion", REQUEST)["candidate"] is True


@pytest.mark.parametrize("text", ["", "   "])
def test_staged_tool_turn_keeps_classification_without_private_tool_arguments(runtime, text):
    state, store, _, _ = runtime
    message = event(
        "staged-message",
        "assistant.message",
        messageId="message-one",
        turnId="turn-one",
        content=text,
        toolRequests=[dict(arguments=dict(prompt="Private native tool prompt"))],
    )
    message["parentId"] = "unbound-parent"
    state.reduce(PROJECT, SESSION, message)
    with store.transaction() as tx:
        pending = tx.require("copilot.pending_events", key(SESSION, "staged-message"))
        assert pending["event"]["data"]["toolRequests"] is True
        assert "Private native tool prompt" not in json.dumps(pending)
        state.bind(
            tx,
            SESSION,
            "event",
            "unbound-parent",
            dict(work_id=WORK, request_id=REQUEST, delivery_id=REQUEST),
        )
    state.drain_pending(PROJECT, SESSION)
    with store.transaction(write=False) as tx:
        assert tx.events()["events"] == []
        assert tx.require("copilot.completion", REQUEST)["public_terminal_message"] is False


@pytest.fixture
def client(runtime):
    state, _, _, _ = runtime
    app = FastAPI()
    app.state.services = state.services

    def owner(request):
        if request.headers.get("fixture-owner") != "yes":
            raise DomainError("owner_session_required", 401)
        return OWNER

    state.services.auth = SimpleNamespace(owner=owner)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse(exc.envelope(), status_code=exc.status)

    app.include_router(router)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("status", ["pending", "dispatching", "completed", "outcome_uncertain"])
def test_resume_status_read_is_owner_authenticated_and_accurate(runtime, client, status):
    _, store, _, _ = runtime
    with store.transaction() as tx:
        tx.put(
            "copilot.resumes",
            "resume-one",
            dict(
                body=dict(project_id=PROJECT, mode="continue_work", work_id=WORK),
                state=status,
                detail_code="provider_unavailable" if status == "outcome_uncertain" else None,
            ),
        )
    path = f"/api/v1/projects/{PROJECT}/resumes/resume-one"
    assert client.get(path).status_code == 401
    response = client.get(path, headers={"fixture-owner": "yes"})
    assert response.status_code == 200 and response.json()["state"] == status
    validate("ResumeOperationStatus", response.json())
    assert (
        client.get(
            path.replace("resume-one", "missing"), headers={"fixture-owner": "yes"}
        ).status_code
        == 404
    )
    with store.transaction() as tx:
        operation = tx.require("copilot.resumes", "resume-one")
        operation["body"]["project_id"] = "foreign-project"
        save(tx, "copilot.resumes", "resume-one", operation)
    assert client.get(path, headers={"fixture-owner": "yes"}).status_code == 403


def test_requests_use_canonical_utc_order_ties_pagination_and_reject_v1_cursor(runtime, client):
    state, store, _, _ = runtime
    stamps = [
        ("request-z", "2026-11-15T10:00:00+02:00"),
        ("request-a", "2026-11-15T08:00:00Z"),
        ("request-b", "2026-11-15T08:00:00+00:00"),
        ("request-earlier", "2026-11-15T07:59:59.999999Z"),
        ("request-later", "2026-11-15T08:00:00.000001+00:00"),
    ]
    with store.transaction() as tx:
        tx.connection.execute("DELETE FROM records WHERE namespace='requests'")
        for identity, stamp in stamps:
            record = request_record(identity)
            record["accepted_at"] = stamp
            tx.put("requests", identity, record, schema="RequestRecord")
    path = f"/api/v1/projects/{PROJECT}/requests"
    ids, cursor = [], None
    while True:
        params = dict(limit=2)
        if cursor:
            params["cursor"] = cursor
        response = client.get(path, params=params, headers={"fixture-owner": "yes"})
        assert response.status_code == 200
        page = response.json()
        ids.extend(r["request_id"] for r in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert ids == ["request-earlier", "request-a", "request-b", "request-z", "request-later"]
    old = state.services.cursors.encode(f"{PROJECT}:requests", "request-b")
    response = client.get(path, params=dict(cursor=old), headers={"fixture-owner": "yes"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_cursor"


def test_continue_resume_attaches_latest_acknowledged_steer_without_resetting_task_or_policy(
    runtime,
):
    state, store, _, _ = runtime
    copilot = Copilot(state.services)
    copilot.contexts.validate_references = lambda tx, snapshot: validate(
        "ContextSnapshot", snapshot
    )
    set_work(store, "awaiting_resume")
    latest_context = context(
        "context-steer",
        request_target=dict(kind="new_item", item_kind="storyboard", intent_id="intent-one"),
        reference_versions=[dict(item_id="asset-one", version_id="asset-version-one")],
    )
    original_payload = dict(
        context_id="context-one",
        generation_task_id="task-one",
        generation_attempt_limit=3,
        request_target=dict(kind="project"),
    )
    latest_payload = dict(
        context_id="context-steer",
        generation_task_id="task-one",
        generation_attempt_limit=3,
        request_target=latest_context["request_target"],
        reference_versions=latest_context["reference_versions"],
    )
    with store.transaction() as tx:
        tx.put("contexts", "context-steer", latest_context, schema="ContextSnapshot")
        tx.put("copilot.payloads", REQUEST, original_payload)
        for identity, status, payload in [
            ("steer-one", "acknowledged", latest_payload),
            ("cancelled-steer", "cancelled", original_payload),
            ("failed-steer", "failed", original_payload),
            ("unsent-steer", "accepted", None),
        ]:
            record = request_record(identity, kind="steer")
            record["receipt"]["state"] = status
            tx.put("requests", identity, record, schema="RequestRecord")
            if payload:
                tx.put("copilot.payloads", identity, payload)
        work = tx.require("work", WORK)
        work.update(
            directive_request_ids=["steer-one", "cancelled-steer", "failed-steer", "unsent-steer"],
            state_revision=tx.revision("work", WORK) + 1,
        )
        save(tx, "work", WORK, work)
        tx.put("copilot.effective", REQUEST, dict(generation_attempt_limit=3, owner_epoch="epoch"))
    sent = []

    async def send(*args, **kwargs):
        sent.append(copy.deepcopy(kwargs))
        return "continuation-message"

    session = SimpleNamespace(session_id=SESSION, send=send)

    async def open_session(project_id):
        return session

    async def reconcile(project_id):
        return dict(
            pending_empty=True, uncertain_request_ids=[], live_invocations=0, uncertain_stops=0
        )

    copilot.session, copilot.reconcile = open_session, reconcile
    body = dict(
        schema_version="1.0",
        project_id=PROJECT,
        operation_id="resume-one",
        mode="continue_work",
        work_id=WORK,
        expected_revision=work["state_revision"],
    )
    copilot.admit_resume(OWNER, body)
    asyncio.run(copilot.resume(PROJECT, "resume-one"))
    assert len(sent) == 1
    assert sent[0]["attachments"][0]["payload"] == latest_payload
    with store.transaction(write=False) as tx:
        assert tx.require("work", WORK)["generation_task_id"] == "task-one"
        assert tx.require("copilot.effective", REQUEST)["generation_attempt_limit"] == 3
        assert tx.require("copilot.resumes", "resume-one")["state"] == "completed"


def test_uncertain_stop_settles_outbox_to_same_unknown_outcome(runtime):
    state, store, _, _ = runtime
    copilot = Copilot(state.services)
    body = dict(
        schema_version="1.0",
        project_id=PROJECT,
        request_id="stop-one",
        type="stop",
        target_work_id=WORK,
    )
    copilot.state.submit(OWNER, body)

    async def uncertain(project_id):
        raise DomainError("provider_unavailable", 503)

    copilot.session = uncertain
    asyncio.run(copilot.stop(PROJECT, WORK, "stop-one"))
    with store.transaction(write=False) as tx:
        assert tx.require("requests", "stop-one")["receipt"]["state"] == "outcome_uncertain"
        assert tx.require("copilot.stops", "stop-one")["phase"] == "outcome_uncertain"
        row = tx.connection.execute(
            "SELECT state,evidence FROM outbox WHERE id='stop-one'"
        ).fetchone()
        assert row[0] == "outcome_uncertain"
        assert json.loads(row[1])["detail_code"] == "provider_unavailable"


@pytest.mark.parametrize("decision_state", ["pending", "cancelled"])
def test_observe_native_denials_recovers_subagent_shell_rejected_on_graceful_shutdown(
    runtime, decision_state
):
    state, store, _, _ = runtime
    child = event(
        "child-start", "subagent.started", agentName="authoring-specialist", toolCallId="task-1"
    )
    child["agentId"] = "subagent-1"
    state.reduce(PROJECT, SESSION, child)
    cmd = "python3 -c 'print(1)'"
    start_ev = event(
        "bash-start",
        "tool.execution_start",
        toolCallId=TOOL,
        toolName="bash",
        arguments={"command": cmd},
        ownerNativeSessionId="subagent-session-1",
    )
    start_ev["agentId"] = "subagent-1"
    state.reduce(PROJECT, SESSION, start_ev)
    perm_payload = {
        "kind": "shell",
        "toolCallId": TOOL,
        "shellType": "bash",
        "hasParseErrors": False,
        "fullCommandText": cmd,
        "commandSegments": [{"fullCommandText": cmd, "identifier": "python3"}],
    }
    sdk_perm_payload = {
        k: v for k, v in perm_payload.items() if k not in {"shellType", "hasParseErrors"}
    }
    fp = hashlib.sha256(canonical(sdk_perm_payload)).hexdigest()
    req_ev = event(
        "perm-req",
        "permission.requested",
        requestId="native-perm-1",
        ownerSessionId="subagent-session-1",
        permissionRequest=perm_payload,
        promptRequest={"kind": "commands", "toolCallId": TOOL, "fullCommandText": cmd},
    )
    req_ev["agentId"] = "subagent-1"
    comp_ev = event(
        "perm-done",
        "permission.completed",
        requestId="native-perm-1",
        toolCallId=TOOL,
        result={"kind": "denied-interactively-by-user"},
    )
    comp_ev["agentId"] = "subagent-1"
    inv_id = key(SESSION, "tool", TOOL)
    dec_id = "decision-" + key(SESSION, "native-perm-1")
    with store.transaction() as tx:
        tx.put(
            "copilot.permissions",
            key(SESSION, "native-perm-1"),
            {
                **BINDING,
                "invocation_id": inv_id,
                "session_id": SESSION,
                "tool_call_id": TOOL,
                "native_id": "native-perm-1",
                "phase": "outcome_uncertain",
                "reply": "reject",
                "authority": "host",
                "delivery_state": "outcome_uncertain",
                "fingerprint": fp,
            },
        )
        tx.put(
            "decisions",
            dec_id,
            {
                "schema_version": "1.0",
                "decision_id": dec_id,
                "project_id": PROJECT,
                "work_id": WORK,
                "invocation_id": inv_id,
                "native_decision_id": "native-perm-1",
                "kind": "permission",
                "state": decision_state,
                "state_revision": 0,
                "prompt": canonical(sdk_perm_payload).decode(),
                "options": ["approve_once", "decline"],
                "fingerprint": "f" * 64,
                "response": None,
                "resolved_by": None,
                "delivery_state": "not_sent",
            },
            schema="Decision",
        )
        recovered = state.observe_native_denials(
            tx,
            SESSION,
            [start_ev, req_ev, comp_ev],
            {
                "session_id": SESSION,
                "pid": 99999,
                "start": "100",
                "observed_identity": None,
                "invocation_ids": [inv_id],
                "pending_empty": True,
                "current_send": False,
                "permission_fingerprints": {"perm-req": fp},
            },
        )
        assert recovered == 1
        assert tx.require("invocations", inv_id)["state"] == "failed"
        assert tx.require("decisions", dec_id)["state"] == "cancelled"


def test_check_health_tolerates_busy_rpc_when_transport_alive_and_fails_when_dead(runtime):
    state, _, _, _ = runtime
    pings = 0
    alive = True

    async def slow_ping():
        nonlocal pings
        pings += 1
        raise TimeoutError

    native = SimpleNamespace(
        client=object(),
        health=slow_ping,
        transport_alive=lambda: alive,
    )
    copilot = Copilot(state.services, native=native)
    from proscenium.copilot.service import CallbackOwner

    copilot.callback_owners[PROJECT] = CallbackOwner(PROJECT, SESSION)
    asyncio.run(copilot.check_health(PROJECT))
    assert pings == 1
    # Throttled inside the 15-second window while transport remains alive.
    asyncio.run(copilot.check_health(PROJECT))
    assert pings == 1

    alive = False
    with pytest.raises(ConnectionError):
        asyncio.run(copilot.check_health(PROJECT))


def test_admit_resume_rejects_busy_release_and_continue_work_releases_empty_queue(runtime):
    state, store, _, project = runtime
    set_work(store, "awaiting_resume")
    with store.transaction() as tx:
        state.fence(tx, "awaiting_resume")
        work = tx.require("work", WORK)
        tx.put(
            "copilot.payloads",
            REQUEST,
            {"context_id": "ctx-1", "attached_files": [], "request_target": {"kind": "project"}},
        )
        tx.put(
            "contexts",
            "ctx-1",
            {
                "schema_version": "1.0",
                "context_id": "ctx-1",
                "project_id": PROJECT,
                "captured_at": "2026-11-17T10:00:00Z",
                "item_id": None,
                "version_id": None,
                "view_mode": "none",
                "target": None,
                "camera": None,
                "range": None,
                "selected_objects": [],
                "attachments": [],
                "annotations": [],
                "approved_findings": [],
                "request_target": {"kind": "project"},
                "reference_versions": [],
                "execution_scope": "default",
            },
        )
        save(
            tx,
            "copilot.control",
            "gate",
            {
                "fenced": True,
                "epoch": "epoch",
                "revision": 2,
                "queue_released": False,
                "reason": "awaiting_resume",
            },
        )
    copilot = Copilot(state.services)
    copilot.contexts.validate_references = lambda tx, snapshot: validate(
        "ContextSnapshot", snapshot
    )
    with pytest.raises(DomainError) as busy:
        copilot.admit_resume(
            OWNER,
            {
                "schema_version": "1.0",
                "project_id": PROJECT,
                "operation_id": "bad-release",
                "mode": "release_held_queue",
                "work_id": None,
                "expected_revision": state.services.registry.get(PROJECT)["state_revision"],
            },
        )
    assert busy.value.code == "project_busy"
    with store.transaction(write=False) as tx:
        assert tx.get("copilot.resumes", "bad-release", required=False) is None

    async def send(*args, **kwargs):
        return "continued-msg"

    session = SimpleNamespace(session_id=SESSION, send=send)

    async def open_session(pid, **kwargs):
        copilot.sessions[pid] = session
        return session

    copilot.session = open_session
    copilot.native = SimpleNamespace(
        pending=lambda s: asyncio.sleep(
            0, result={"items": [], "steeringMessages": [], "inFlightSteeringCount": 0}
        )
    )
    copilot.admit_resume(
        OWNER,
        {
            "schema_version": "1.0",
            "project_id": PROJECT,
            "operation_id": "continue-ok",
            "mode": "continue_work",
            "work_id": WORK,
            "expected_revision": work["state_revision"],
        },
    )
    asyncio.run(copilot.resume(PROJECT, "continue-ok"))
    with store.transaction(write=False) as tx:
        gate = copilot.state.control(tx)
        assert not gate["fenced"] and gate["queue_released"] is True


def test_interrupt_departed_custom_tools_and_cold_stop_recovery(runtime):
    state, store, _, _ = runtime
    set_work(store, "awaiting_resume")
    cap_call = "call-cap"
    prep_uninvoked = "call-prep-uninvoked"
    prep_invoked = "call-prep-invoked"
    cap_id = key(SESSION, "tool", cap_call)
    prep_uninvoked_id = key(SESSION, "tool", prep_uninvoked)
    prep_invoked_id = key(SESSION, "tool", prep_invoked)
    empty_sha = hashlib.sha256(canonical({})).hexdigest()
    prep_args = {"operation_id": "prep-op-1", "prompt": "Relay dish", "seed": 42}
    prep_sha = hashlib.sha256(canonical(prep_args)).hexdigest()
    perm_req = {
        "kind": "custom-tool",
        "toolCallId": cap_call,
        "toolName": "get_project_capabilities",
        "args": {},
    }
    perm_fp = hashlib.sha256(canonical(perm_req)).hexdigest()
    binding = {"work_id": WORK, "request_id": REQUEST, "delivery_id": REQUEST}
    with store.transaction() as tx:
        for tc, inv_id, name, sha_val in (
            (cap_call, cap_id, "get_project_capabilities", empty_sha),
            (prep_uninvoked, prep_uninvoked_id, "prepare_image_generation", prep_sha),
            (prep_invoked, prep_invoked_id, "prepare_image_generation", prep_sha),
        ):
            state.bind(tx, SESSION, "tool", tc, binding)
            save(
                tx,
                "invocations",
                inv_id,
                {
                    **binding,
                    "invocation_id": inv_id,
                    "session_id": SESSION,
                    "native_id": tc,
                    "agent_id": None,
                    "owner_epoch": "prior-epoch",
                    "kind": "tool",
                    "state": "running",
                    "name": name,
                    "model_id": "gpt-6.1-sol",
                    "updated_at": "2026-11-17T10:23:52Z",
                    "display_name": None,
                    "arguments_sha256": sha_val,
                },
            )
        save(
            tx,
            "copilot.permissions",
            key(SESSION, "perm-cap"),
            {
                **binding,
                "invocation_id": cap_id,
                "session_id": SESSION,
                "native_id": "perm-cap",
                "tool_call_id": cap_call,
                "fingerprint": perm_fp,
                "phase": "outcome_uncertain",
                "reply": None,
                "authority": None,
                "delivery_state": "not_sent",
            },
        )
    raw_history = [
        {
            "id": "ev-1",
            "type": "tool.execution_start",
            "data": {
                "toolCallId": cap_call,
                "toolName": "get_project_capabilities",
                "arguments": {},
            },
        },
        {
            "id": "ev-2",
            "type": "permission.requested",
            "data": {"requestId": "perm-cap", "permissionRequest": perm_req},
        },
        {
            "id": "ev-3",
            "type": "tool.execution_start",
            "data": {
                "toolCallId": prep_uninvoked,
                "toolName": "prepare_image_generation",
                "arguments": prep_args,
            },
        },
        {
            "id": "ev-4",
            "type": "tool.execution_start",
            "data": {
                "toolCallId": prep_invoked,
                "toolName": "prepare_image_generation",
                "arguments": prep_args,
            },
        },
        {
            "id": "ev-5",
            "type": "external_tool.requested",
            "data": {
                "toolCallId": prep_invoked,
                "toolName": "prepare_image_generation",
                "arguments": prep_args,
            },
        },
    ]
    with store.transaction() as tx:
        recovered = state.interrupt_departed_custom_tools(
            tx,
            SESSION,
            raw_history,
            {
                "session_id": SESSION,
                "pid": 12345,
                "start": "old-start",
                "observed_identity": None,
                "invocation_ids": [cap_id, prep_uninvoked_id, prep_invoked_id],
                "pending_empty": True,
                "current_send": False,
            },
        )
        assert recovered == 2
        assert tx.require("invocations", cap_id)["state"] == "cancelled"
        assert (
            tx.require("invocations", cap_id)["recovery"]["kind"]
            == "departed_read_only_custom_tool_interrupted"
        )
        assert tx.require("invocations", prep_uninvoked_id)["state"] == "cancelled"
        assert (
            tx.require("invocations", prep_uninvoked_id)["recovery"]["kind"]
            == "uninvoked_custom_tool_interrupted"
        )
        # Invoked mutating tool without execution_complete must NOT be guessed cancelled.
        assert tx.require("invocations", prep_invoked_id)["state"] == "running"
        stage = tx.require("copilot.permissions", key(SESSION, "perm-cap"))
        assert state.interrupted_custom_stage(tx, stage) is True

    # Also verify Copilot.stop() on a cold session settles a departed read-only custom tool
    # instead of timing out into outcome_uncertain.
    with store.transaction() as tx:
        tx.connection.execute(
            "DELETE FROM records WHERE namespace='invocations' AND id=?",
            (prep_invoked_id,),
        )
        cap_inv = tx.require("invocations", cap_id)
        save(tx, "invocations", cap_id, {**cap_inv, "state": "running"})
        save(tx, "copilot.sends", REQUEST, {"state": "acknowledged", "session_id": SESSION})
    copilot = Copilot(state.services)
    copilot.native = SimpleNamespace(
        departed_process={
            "session_id": SESSION,
            "pid": 12345,
            "start": "old-start",
            "observed_identity": None,
        },
        pause=lambda s, p: asyncio.sleep(0),
        pending=lambda s: asyncio.sleep(
            0, result={"items": [], "steeringMessages": [], "inFlightSteeringCount": 0}
        ),
        inactive=lambda s: asyncio.sleep(0, result=True),
    )

    async def get_empty():
        return []

    async def get_raw():
        return raw_history[:2]

    cold_sess = SimpleNamespace(
        session_id=SESSION,
        abort=lambda: asyncio.sleep(0),
        get_events=get_empty,
        get_raw_events=get_raw,
    )

    async def open_cold(pid, **kwargs):
        from proscenium.copilot.service import CallbackOwner

        owner = CallbackOwner(pid, SESSION)
        copilot.callback_owners[pid] = owner
        copilot.session_callback_owners[id(cold_sess)] = owner
        copilot.sessions[pid] = cold_sess
        return cold_sess

    copilot.session = open_cold
    copilot.state.submit(
        OWNER,
        {
            "schema_version": "1.0",
            "project_id": PROJECT,
            "request_id": "cold-stop-ok",
            "type": "stop",
            "target_work_id": WORK,
        },
    )
    asyncio.run(copilot.stop(PROJECT, WORK, "cold-stop-ok"))
    with store.transaction(write=False) as tx:
        assert tx.require("invocations", cap_id)["state"] == "cancelled"
        assert tx.require("copilot.stops", "cold-stop-ok")["phase"] == "confirmed"
        assert tx.require("work", WORK)["state"] == "stopped"


def test_admit_resume_guards_running_work_and_uncertain_stops(runtime):
    state, store, _, _ = runtime
    copilot = Copilot(state.services)
    set_work(store, "running")
    with store.transaction() as tx:
        save(
            tx,
            "copilot.control",
            "gate",
            {
                "fenced": False,
                "epoch": state.services.ownership.epoch,
                "revision": 1,
                "queue_released": True,
            },
        )
    with store.transaction(write=False) as tx:
        running_rev = tx.require("work", WORK)["state_revision"]
    # Reject reconcile_only synchronously while unfenced work is actively running.
    with pytest.raises(DomainError) as running_err:
        copilot.admit_resume(
            OWNER,
            {
                "schema_version": "1.0",
                "project_id": PROJECT,
                "operation_id": "reconcile-while-running",
                "mode": "reconcile_only",
                "work_id": WORK,
                "expected_revision": running_rev,
            },
        )
    assert running_err.value.code == "project_busy"

    # Reject release_held_queue and continue_work while an uncertain Stop awaits reconciliation.
    set_work(store, "awaiting_resume")
    with store.transaction() as tx:
        state.fence(tx, "outcome_uncertain")
        save(
            tx,
            "copilot.stops",
            "uncertain-stop-1",
            {
                "phase": "outcome_uncertain",
                "pause": "acknowledged",
                "abort": "acknowledged",
                "work_id": WORK,
            },
        )
        work_rev = tx.require("work", WORK)["state_revision"]
    for mode, wid, rev in (
        ("continue_work", WORK, work_rev),
        ("release_held_queue", None, state.services.registry.get(PROJECT)["state_revision"]),
    ):
        with pytest.raises(DomainError) as stop_err:
            copilot.admit_resume(
                OWNER,
                {
                    "schema_version": "1.0",
                    "project_id": PROJECT,
                    "operation_id": f"blocked-{mode}",
                    "mode": mode,
                    "work_id": wid,
                    "expected_revision": rev,
                },
            )
        assert stop_err.value.code == "project_busy"
