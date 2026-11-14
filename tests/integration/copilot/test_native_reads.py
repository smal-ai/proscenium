"""Offline normative read-model fixtures. No renderer/provider/model is executed."""

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from copilot.tools import ToolInvocation
from jsonschema import Draft202012Validator
from PIL import Image
from proscenium.catalogue import install as install_catalogue
from proscenium.contracts import canonical, validate
from proscenium.copilot.native import Configuration
from proscenium.copilot.tools import READ_TOOLS, Tools, read_parameters
from proscenium.db.store import now
from proscenium.previews import install as install_previews
from test_delivery import PROJECT, context, submit
from test_delivery import runtime as runtime


def snapshot(services):
    with services.registry.project_store(PROJECT).transaction(write=False) as tx:
        return {
            table: [
                tuple(row) for row in tx.connection.execute(f"SELECT * FROM {table} ORDER BY 1,2")
            ]
            for table in ("records", "operations", "events", "outbox")
        }


@pytest.fixture
def reads(runtime):
    catalogue = install_catalogue(runtime.services)
    install_previews(runtime.services)  # Registers capabilities, starts nothing.
    context(runtime)
    submit(runtime)
    asyncio.run(runtime.send(PROJECT, "request-one"))
    services = runtime.services
    artifacts = services.extension("artifacts")
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(output, format="PNG")
    artifact = artifacts.promote(
        PROJECT,
        "artifact-read",
        output.getvalue(),
        kind="image",
        purpose="keyframe",
        media_type="image/png",
    )
    example = json.loads(Path("docs/contracts/examples/publication.json").read_text())["items"][0]
    version = dict(
        schema_version="1.0",
        project_id=PROJECT,
        item_id="item-read",
        version_id="version-read",
        kind="scene",
        name="Synthetic pinned read-model fixture",
        publication_id="publication-read",
        source_commit=example["source_commit"],
        source=example["source"],
        artifact_ids=[],
        preview_requests=[],
        preview_job_ids=[],
        created_at=now(),
        actor={"kind": "user", "id": "fixture-owner"},
    )
    store = services.registry.project_store(PROJECT)
    with store.transaction() as tx:
        artifacts.put(tx, artifact)
        tx.put("versions", version["version_id"], version, schema="VersionRecord")
        tx.put("version_links", version["version_id"], {"artifact_ids": [artifact["artifact_id"]]})
        work_id = tx.require("requests", "request-one")["receipt"]["work_id"]
        for job_id in ("job-read", "job-other"):
            services.jobs.create(
                tx,
                job_id=job_id,
                kind="render",
                target=dict(
                    target_id="item-read",
                    version={"item_id": "item-read", "version_id": "version-read"},
                    shot_id=None,
                ),
                input_digest="a" * 64,
                operation_id="admit-" + job_id,
                work_id=work_id,
            )
        job = tx.require("jobs", "job-read")
        for frame in (10, 1, 2, 99):
            receipt = dict(
                schema_version="1.0",
                receipt_id=f"receipt-{frame}",
                project_id=PROJECT,
                job_id=job["job_id"],
                attempt_id="retained-old-attempt" if frame == 10 else job["attempt_id"],
                version_id=version["version_id"],
                shot_id=None,
                source_frame=frame,
                artifact_id=artifact["artifact_id"],
                output_path=f"frames/{frame:06d}.png",
                sha256=artifact["sha256"],
                dimensions=artifact["dimensions"],
                input_digest="b" * 64 if frame == 99 else job["input_digest"],
            )
            tx.put("frames", receipt["receipt_id"], receipt, schema="FrameReceipt")
    session = runtime.native.session
    parent = session.event(
        "assistant.message",
        dict(
            messageId="read-parent",
            originatingMessageId="native-request-one",
            turnId="0",
            content="",
        ),
    )
    tools = Tools(services, runtime.state, PROJECT)
    counter = 0

    def invocation(name, arguments):
        nonlocal counter
        counter += 1
        identity = f"read-call-{counter}"
        session.event(
            "tool.execution_start",
            dict(toolCallId=identity, toolName=name, turnId="0"),
            parent=parent,
        )
        return ToolInvocation(
            session_id=session.session_id,
            tool_call_id=identity,
            tool_name=name,
            arguments=arguments,
        )

    yield SimpleNamespace(
        runtime=runtime,
        services=services,
        tools=tools,
        invocation=invocation,
        artifact=artifact,
        version=catalogue.version(PROJECT, version["version_id"]),
        session=session,
    )
    asyncio.run(runtime.close())


def call(reads, name, args):
    invocation = reads.invocation(name, args)
    before = snapshot(reads.services)  # Native admission is already durable.
    native_opens = list(reads.runtime.native.opens)
    definition = next(tool for tool in reads.tools.definitions() if tool.name == name)
    result = asyncio.run(definition.handler(invocation))
    assert snapshot(reads.services) == before
    assert reads.runtime.native.opens == native_opens
    return json.loads(result.text_result_for_llm)


def test_artifact_and_version_are_exact_accepted_read_models(reads):
    artifact = call(reads, "get_artifact", {"artifact_id": "artifact-read"})
    assert artifact == reads.artifact
    assert validate("Artifact", artifact) == artifact
    path = reads.services.extension("artifacts").ready_path(PROJECT, artifact["artifact_id"])
    project = Path(reads.services.registry.get(PROJECT)["path"])
    assert path == project / ".proscenium/artefacts/sha256" / artifact["sha256"] / "content"
    version = call(reads, "get_version", {"version_id": "version-read"})
    assert version == reads.version
    assert version["source_commit"] == "a" * 40
    assert version["artifact_ids"] == [
        "artifact-read"
    ]  # Accepted projection includes derived links.
    assert set(version["preview_job_ids"]) == {"job-read", "job-other"}
    assert validate("VersionRecord", version) == version


def test_frames_use_preview_filtering_numeric_order_and_f00_scoped_pages(reads):
    first = call(reads, "list_job_frames", {"job_id": "job-read", "limit": 2})
    assert validate("FramePage", first) == first
    assert [row["source_frame"] for row in first["items"]] == [1, 2]
    assert first["next_cursor"]
    # Identical HTTP/F00 scope, not an unsigned native offset or a new cursor format.
    assert (
        reads.services.cursors.decode(f"{PROJECT}:job-read:frames:None:None", first["next_cursor"])
        == "000000000002"
    )
    second = call(
        reads, "list_job_frames", {"job_id": "job-read", "limit": 2, "cursor": first["next_cursor"]}
    )
    assert [row["source_frame"] for row in second["items"]] == [10]
    assert second["items"][0]["attempt_id"] == "retained-old-attempt"
    assert second["next_cursor"] is None
    assert all("order" not in row for row in first["items"] + second["items"])
    assert call(reads, "list_job_frames", {"job_id": "job-other"})["items"] == []
    assert (
        call(reads, "list_job_frames", {"job_id": "job-other", "cursor": first["next_cursor"]})[
            "error"
        ]
        == "invalid_cursor"
    )
    assert (
        call(
            reads,
            "list_job_frames",
            {"job_id": "job-read", "cursor": first["next_cursor"] + "altered"},
        )["error"]
        == "invalid_cursor"
    )
    foreign_cursor = reads.services.cursors.encode(
        "another-project:job-read:frames:None:None", "000000000002"
    )
    assert (
        call(reads, "list_job_frames", {"job_id": "job-read", "cursor": foreign_cursor})["error"]
        == "invalid_cursor"
    )


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_artifact", {"artifact_id": "../.proscenium/state.sqlite3"}),
        ("get_artifact", {"artifact_id": 1}),
        ("get_version", {"version_id": "version-read", "project_id": "another-project"}),
        ("get_version", {}),
        ("get_version", ["version-read"]),
        ("list_job_frames", {"job_id": "job-read", "limit": True}),
        ("list_job_frames", {"job_id": "job-read", "limit": 0}),
        ("list_job_frames", {"job_id": "job-read", "limit": 101}),
        ("list_job_frames", {"job_id": "job-read", "limit": 1.5}),
        ("list_job_frames", {"job_id": "job-read", "work_id": "forged-work"}),
    ],
)
def test_reads_reject_invalid_ids_limits_and_caller_selected_scope(reads, name, args):
    assert call(reads, name, args)["error"] == "invalid_request"


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_artifact", {"artifact_id": "missing"}),
        ("get_version", {"version_id": "missing"}),
        ("list_job_frames", {"job_id": "missing"}),
    ],
)
def test_missing_records_do_not_manufacture_success(reads, name, args):
    assert call(reads, name, args)["error"] == "not_found"


@pytest.mark.parametrize(
    "name,arguments,extension",
    [
        ("get_artifact", {"artifact_id": "artifact-read"}, "artifacts"),
        ("get_version", {"version_id": "version-read"}, "catalogue"),
        ("list_job_frames", {"job_id": "job-read"}, "previews"),
    ],
)
def test_reads_report_missing_capabilities_without_installing_them(
    reads, name, arguments, extension
):
    reads.services.extensions.pop(extension)
    assert call(reads, name, arguments)["error"] == "capability_unavailable"


def test_read_requires_original_invocation_and_active_project(reads, monkeypatch):
    args = {"artifact_id": "artifact-read"}
    invocation = reads.invocation("get_artifact", args)
    forged = ToolInvocation(
        session_id=invocation.session_id,
        tool_call_id=invocation.tool_call_id,
        tool_name="get_artifact",
        arguments=args,
        agent_id="unrelated-child",
    )
    result = asyncio.run(reads.tools.invoke("get_artifact", forged))
    assert json.loads(result.text_result_for_llm)["error"] == "attribution_unavailable"
    missing = ToolInvocation(
        session_id=invocation.session_id,
        tool_call_id="not-admitted",
        tool_name="get_artifact",
        arguments=args,
    )
    assert (
        json.loads(asyncio.run(reads.tools.invoke("get_artifact", missing)).text_result_for_llm)[
            "error"
        ]
        == "not_found"
    )
    monkeypatch.setattr(reads.services.registry, "active_id", lambda: "another-project")
    assert (
        json.loads(asyncio.run(reads.tools.invoke("get_artifact", invocation)).text_result_for_llm)[
            "error"
        ]
        == "scope_denied"
    )


def test_read_does_not_open_execution_gate(reads):
    with reads.services.registry.project_store(PROJECT).transaction() as tx:
        reads.runtime.state.fence(tx, "owner-stop")
    assert call(reads, "get_artifact", {"artifact_id": "artifact-read"}) == reads.artifact
    with reads.services.registry.project_store(PROJECT).transaction(write=False) as tx:
        assert reads.runtime.state.control(tx)["fenced"]


@pytest.mark.parametrize(
    "corruption,error",
    [("project", "scope_denied"), ("identity", "source_conflict"), ("extra", "invalid_request")],
)
def test_read_validates_service_scope_identity_and_closed_output(
    reads, monkeypatch, corruption, error
):
    result = dict(reads.artifact)
    if corruption == "project":
        result["project_id"] = "another-project"
    elif corruption == "identity":
        result["artifact_id"] = "different-artifact"
    else:
        result["private_token"] = "DO_NOT_FORWARD"
    monkeypatch.setattr(
        reads.services.extension("artifacts"), "get", lambda project_id, artifact_id: result
    )
    response = call(reads, "get_artifact", {"artifact_id": "artifact-read"})
    assert response["error"] == error
    assert "DO_NOT_FORWARD" not in json.dumps(response)


def test_read_parameter_schemas_and_default_worker_scope():
    expected = {"get_artifact", "get_version", "list_job_frames"}
    config = Configuration.load({"worker_model": "explicit-worker"})
    assert expected <= set(config.worker_tools)
    assert Configuration.load(
        {"worker_model": "explicit-worker", "worker_tools": ["get_job"]}
    ).worker_tools == ["get_job"]
    for name in READ_TOOLS:
        schema = read_parameters(name)
        Draft202012Validator.check_schema(schema)
        identity = READ_TOOLS[name][0]
        validator = Draft202012Validator(schema)
        assert validator.is_valid({identity: "valid-id"})
        assert not validator.is_valid({identity: "../private.sqlite3"})
        assert not validator.is_valid({identity: "valid-id", "project_id": "another-project"})
        assert "$defs" not in schema  # Uses the exact normative Id inline, no catalogue bloat.
        canonical(schema)
