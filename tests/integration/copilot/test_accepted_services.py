import asyncio
import hashlib
import json
from pathlib import Path

from copilot.tools import ToolInvocation
from PIL import Image
from proscenium.catalogue import install as install_catalogue
from proscenium.copilot.tools import Tools
from test_delivery import PROJECT, context, submit
from test_delivery import runtime as runtime


def test_accepted_w02_original_invocation_replay_before_effect_gate(runtime):
    install_catalogue(runtime.services)

    async def run():
        context(runtime)
        submit(runtime)
        await runtime.send(PROJECT, "request-one")
        session = runtime.native.session
        parent = session.event(
            "assistant.message",
            dict(
                messageId="register-parent",
                originatingMessageId="native-request-one",
                turnId="0",
                content="",
            ),
        )
        session.event(
            "tool.execution_start",
            dict(toolCallId="register-call", toolName="register_artifact", turnId="0"),
            parent=parent,
        )
        root = Path(runtime.services.registry.get(PROJECT)["path"])
        staging = root / ".proscenium/tool-output/staging/recorded.png"
        staging.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "red").save(staging)
        body = dict(
            schema_version="1.0",
            operation_id="register-operation",
            project_id=PROJECT,
            artifact_id="registered-image",
            staging_path=staging.relative_to(root).as_posix(),
            kind="image",
            purpose="reference",
            sha256=hashlib.sha256(staging.read_bytes()).hexdigest(),
            media_type="image/png",
        )
        invocation = ToolInvocation(
            session_id=session.session_id,
            tool_call_id="register-call",
            tool_name="register_artifact",
            arguments=body,
        )
        tools = Tools(runtime.services, runtime.state, PROJECT)
        first = await tools.invoke("register_artifact", invocation)
        assert json.loads(first.text_result_for_llm)["artifact_id"] == "registered-image"
        session.event(
            "tool.execution_complete", dict(toolCallId="register-call", success=True), parent=parent
        )
        with runtime.services.registry.project_store(PROJECT).transaction() as tx:
            runtime.state.fence(tx, "test-stop")
        replay = await tools.invoke("register_artifact", invocation)
        assert replay.text_result_for_llm == first.text_result_for_llm
        session.event(
            "tool.execution_start",
            dict(toolCallId="different-invocation", toolName="register_artifact", turnId="0"),
            parent=parent,
        )
        different = ToolInvocation(
            session_id=session.session_id,
            tool_call_id="different-invocation",
            tool_name="register_artifact",
            arguments=body,
        )
        conflict = await tools.invoke("register_artifact", different)
        assert json.loads(conflict.text_result_for_llm)["error"] == "idempotency_conflict"
        different = ToolInvocation(
            session_id=session.session_id,
            tool_call_id="different-invocation",
            tool_name="register_artifact",
            arguments={**body, "operation_id": "new-operation", "artifact_id": "new-image"},
        )
        rejected = await tools.invoke("register_artifact", different)
        assert json.loads(rejected.text_result_for_llm)["error"] == "project_busy"
        await runtime.close()

    asyncio.run(run())


def test_accepted_w05_findings_link_is_atomic_with_real_w02_images(runtime):
    import io

    import pytest
    from proscenium.api.errors import DomainError
    from proscenium.reviews import install as install_reviews

    install_catalogue(runtime.services)
    install_reviews(runtime.services)
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(output, format="PNG")
    artifacts = runtime.services.extensions["artifacts"]
    artifact = artifacts.promote(
        PROJECT,
        "clean-review-image",
        output.getvalue(),
        kind="image",
        purpose="reference",
        media_type="image/png",
    )
    target = dict(
        origin="reference",
        artifact_id=artifact["artifact_id"],
        displayed_sha256=artifact["sha256"],
        dimensions=artifact["dimensions"],
        item_id=None,
        version_id=None,
        shot_id=None,
        render_job_id=None,
        source_frame=None,
        media_artifact_id=None,
    )
    finding = json.loads(Path("docs/contracts/examples/review-complete.json").read_text())[
        "findings"
    ][0]
    finding.update(project_id=PROJECT, target=target, evidence=[])
    with runtime.services.registry.project_store(PROJECT).transaction() as tx:
        artifacts.put(tx, artifact)
        tx.put(
            "reviews",
            finding["review_id"],
            dict(
                schema_version="1.0",
                review_id=finding["review_id"],
                job_id="fixture-review-job",
                status="ready",
            ),
            schema="ReviewTicket",
        )
        tx.put("findings", finding["finding_id"], finding, schema="ReviewFinding")
    approved = dict(
        finding_id=finding["finding_id"],
        finding_revision=1,
        approved_text="Only the owner's selected instruction",
        target=target,
    )
    context(
        runtime,
        attachments=[{"artifact_id": artifact["artifact_id"], "role": "reference"}],
        approved_findings=[approved],
    )
    submit(runtime)
    assert submit(runtime)["replayed"]
    with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
        saved = tx.require("findings", finding["finding_id"])
        assert saved["state"] == "submitted" and saved["revision"] == 2
        assert saved["observation"] == finding["observation"]
        assert (
            tx.require("review.submissions", finding["finding_id"])["request_id"] == "request-one"
        )
        assert not tx.all("work") or all(w["state"] == "completed" for w in tx.all("work"))
    with pytest.raises(DomainError) as caught:
        submit(runtime, "stale-finding-new-request")
    assert caught.value.code == "revision_conflict"
    assert runtime.native.opens == []


def test_held_queues_do_not_become_multiple_active_work_and_reactivation_holds(runtime):
    install_catalogue(runtime.services)

    def activate_revision(revision):
        with runtime.services.registry.store.transaction() as tx:
            project = tx.require("projects", PROJECT)
            previous = project["state_revision"]
            project.update(state="awaiting_resume", state_revision=revision)
            tx.put("projects", PROJECT, project, expected_revision=previous, schema="ProjectRecord")

    activate_revision(1)
    context(runtime)
    submit(runtime)
    submit(runtime, "second-held")
    snapshot = runtime.services.extension("projects").snapshot(PROJECT)
    assert snapshot["active_work"] is None
    assert len(snapshot["held_requests"]) == 2
    runtime.services.registry.assert_settled(PROJECT)
    activate_revision(2)

    async def run():
        await runtime.tick(PROJECT)
        assert runtime.native.opens == []
        from test_delivery import OWNER

        command = dict(
            schema_version="1.0",
            operation_id="explicit-release",
            project_id=PROJECT,
            expected_revision=2,
            mode="release_held_queue",
            work_id=None,
        )
        runtime.admit_resume(OWNER, command)
        await runtime.resume(PROJECT, command["operation_id"])
        with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
            assert not runtime.state.control(tx)["fenced"]
        await runtime.close()

    asyncio.run(run())
