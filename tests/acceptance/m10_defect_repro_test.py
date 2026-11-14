"""Offline regressions from retained M10 evidence, not a native journey acceptance."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from proscenium.api.errors import DomainError
from proscenium.catalogue import install
from proscenium.contracts import canonical
from proscenium.copilot.context import Contexts
from proscenium.copilot.state import State, key

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m10"


@pytest.mark.parametrize("association", ["raw", "late"])
@pytest.mark.parametrize("invalid", [None, "project", "digest", "instance", "version"])
def test_pinned_proxy_mapping_validates_raw_and_late_associations(services, association, invalid):
    observed = json.loads((EVIDENCE / "context-defect.json").read_text())
    catalogue = install(services)
    artifacts = services.extension("artifacts")
    version = copy.deepcopy(observed["raw_version"])
    version.update(project_id="project-test", artifact_ids=[], preview_job_ids=[])
    mapping = copy.deepcopy(observed["actual_w03_mapping"])
    mapping["project_id"] = "project-test"
    if invalid == "digest":
        mapping["source_closure_digest"] = "0" * 64
    if invalid == "version":
        mapping["version_id"] = "wrong-version"
    artifact = artifacts.promote(
        "project-test",
        "late-mapping",
        canonical(mapping),
        kind="proxy_map",
        purpose="metadata",
        media_type="application/json",
    )
    context = copy.deepcopy(observed["context"])
    context["project_id"] = "project-test"
    if invalid == "project":
        context["project_id"] = "wrong-project"
    if invalid == "instance":
        context["selected_objects"][0]["instance_id"] = "wrong-instance"
    if association == "raw":
        version["artifact_ids"] = ["late-mapping"]
    contexts = Contexts(services)
    with services.registry.project_store("project-test").transaction() as tx:
        artifacts.put(tx, artifact)
        tx.put("versions", version["version_id"], version, schema="VersionRecord")
        tx.put(
            "version_links",
            version["version_id"],
            {"artifact_ids": ["late-mapping"] if association == "late" else []},
        )
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        projected = catalogue.version_view(tx, version)
        assert "late-mapping" in projected["artifact_ids"]
        if invalid:
            with pytest.raises(DomainError) as error:
                contexts.validate_references(tx, context)
            assert error.value.code == (
                "scope_denied" if invalid == "project" else "source_conflict"
            )
        else:
            contexts.validate_references(tx, context)
        assert tx.require("versions", version["version_id"]) == version
        assert tx.revision("versions", version["version_id"]) == 0


def test_identical_payload_distinct_native_permission_ids_preserve_both_stages(services):
    observed = json.loads((EVIDENCE / "permission-defect.json").read_text())
    first, second = observed["requests"]
    fingerprint = hashlib.sha256(canonical(first["permission_request"])).hexdigest()
    assert fingerprint == hashlib.sha256(canonical(second["permission_request"])).hexdigest()
    assert first["request_id"] != second["request_id"]
    state = State(services, Contexts(services))
    base = {
        "work_id": "work-test",
        "request_id": "request-test",
        "invocation_id": "invocation-test",
    }
    with services.registry.project_store("project-test").transaction() as tx:
        state.bind(tx, "session-test", "tool", observed["tool_call_id"], base)
    for row in observed["requests"]:
        event = dict(
            id=row["native_event_id"],
            type="permission.requested",
            data={
                "requestId": row["request_id"],
                "permissionRequest": row["permission_request"],
                "toolCallId": observed["tool_call_id"],
            },
        )
        state.reduce("project-test", "session-test", event)
        state.reduce("project-test", "session-test", event)
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        stages = [
            tx.require("copilot.permissions", key("session-test", r["request_id"]))
            for r in observed["requests"]
        ]
        assert [s["native_id"] for s in stages] == [first["request_id"], second["request_id"]]
        assert all(s["phase"] == "pending" and s["fingerprint"] == fingerprint for s in stages)
