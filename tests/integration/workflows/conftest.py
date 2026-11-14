"""Offline metadata fixtures only. No Blender, Unity, SDK model or encoder is run."""

import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from copilot.tools import ToolInvocation
from proscenium.catalogue import install as catalogue_install
from proscenium.contracts import validate
from proscenium.copilot.context import Contexts
from proscenium.copilot.state import State, key
from proscenium.copilot.tools import Tools
from proscenium.db.store import now
from proscenium.previews import install as previews_install
from proscenium.workflows import install

from tools.unity.workflow import sha256, write_json


@pytest.fixture
def environment(services, caller, monkeypatch):
    root = Path(services.registry.get("project-test")["path"])
    services.settings = replace(
        services.settings,
        runtime={"worker_model": "offline-worker", "unity_editor": "/bin/false"},
        rendering={"blender": "/bin/false", "profile_id": "offline"},
    )
    catalogue_install(services)
    previews_install(services)
    services.gate.resume(
        "project-test", reconciliation_evidence="offline bounded workflow metadata fixture"
    )
    tools = Tools(services, State(services, Contexts(services)), "project-test")
    identity = key("session-workflows", "tool", "workflow-call")
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "invocations",
            identity,
            dict(
                agent_id=None,
                session_id="session-workflows",
                work_id="work-test",
                owner_epoch=services.ownership.epoch,
                state="running",
                invocation_id=identity,
            ),
        )

    def invocation(name, body):
        return ToolInvocation(
            session_id="session-workflows",
            tool_call_id="workflow-call",
            tool_name=name,
            arguments=body,
        )

    original = tools.caller(invocation("unused", {}))
    workflows = install(services)
    example = json.loads(Path("docs/contracts/examples/publication.json").read_text())["items"][0]
    sealed = root / "sealed"
    source = sealed / "scenes/fixture/scene.blend"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"OFFLINE SOURCE DOUBLE, NOT A NATIVE BLENDER FIXTURE")
    closure = copy.deepcopy(example["source"])
    closure["files"][0]["origin"]["sha256"] = sha256(source)

    def version(identity, kind="scene", **extra):
        result = dict(
            schema_version="1.0",
            project_id="project-test",
            item_id="item-" + identity,
            version_id=identity,
            kind=kind,
            name="Offline metadata " + identity,
            publication_id="publication-fixture",
            source_commit="a" * 40,
            source=closure,
            artifact_ids=[],
            preview_requests=[],
            preview_job_ids=[],
            created_at=now(),
            actor={"kind": "tool", "id": "fixture"},
            **extra,
        )
        validate("VersionRecord", result)
        with services.registry.project_store("project-test").transaction() as tx:
            tx.put("versions", identity, result, schema="VersionRecord")
        return result

    source_version = version("source-v1")
    monkeypatch.setattr(
        services.extension("sources"), "materialise", lambda project_id, version_id: sealed
    )
    body = dict(
        operation_id="workflow-operation",
        job_id="workflow-job",
        execution_profile_id="offline",
        version={"item_id": source_version["item_id"], "version_id": source_version["version_id"]},
        project="unity/Fixture",
        spawn=[0, 0.92, -2],
        camera="perspective",
        pipeline="builtin",
        action="import",
    )
    yield SimpleNamespace(
        services=services,
        root=root,
        tools=tools,
        invocation=invocation,
        caller=original,
        workflows=workflows,
        version=version,
        source=source_version,
        body=body,
        write_json=write_json,
    )
    # Tests explicitly close the async service within the same event loop they used.
