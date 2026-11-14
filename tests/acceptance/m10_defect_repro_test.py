"""Isolated diagnostic reproducers of the two real blockers, not product PASS tests.

When the integration owner fixes either seam, its observed-error assertion must be
replaced by the corresponding desired-success regression. No shared implementation edits.
"""

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


def test_late_valid_proxy_mapping_is_visible_to_catalogue_but_rejected_by_context(services):
    observed = json.loads((EVIDENCE / "context-defect.json").read_text())
    catalogue = install(services)
    artifacts = services.extension("artifacts")
    version = copy.deepcopy(observed["raw_version"])
    version.update(project_id="project-test", artifact_ids=[], preview_job_ids=[])
    mapping = copy.deepcopy(observed["actual_w03_mapping"])
    mapping["project_id"] = "project-test"
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
    contexts = Contexts(services)
    with services.registry.project_store("project-test").transaction() as tx:
        artifacts.put(tx, artifact)
        tx.put("versions", version["version_id"], version, schema="VersionRecord")
        tx.put("version_links", version["version_id"], {"artifact_ids": ["late-mapping"]})
        projected = catalogue.version_view(tx, version)
        assert "late-mapping" in projected["artifact_ids"]
        with pytest.raises(DomainError) as error:
            contexts.validate_references(tx, context)
        assert error.value.code == "source_conflict"
        # Isolate the defect: the identical mapping is accepted when initially attached.
        version["artifact_ids"] = ["late-mapping"]
        tx.put(
            "versions",
            version["version_id"],
            version,
            expected_revision=tx.revision("versions", version["version_id"]),
            schema="VersionRecord",
        )
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        contexts.validate_references(tx, context)


def test_identical_payload_distinct_native_permission_ids_collide_in_current_binding(services):
    observed = json.loads((EVIDENCE / "permission-defect.json").read_text())
    first, second = observed["requests"]
    fingerprint = hashlib.sha256(canonical(first["permission_request"])).hexdigest()
    assert fingerprint == hashlib.sha256(canonical(second["permission_request"])).hexdigest()
    assert first["request_id"] != second["request_id"]
    state = State(services, Contexts(services))
    identity = key(observed["tool_call_id"], fingerprint)
    base = {
        "work_id": "work-test",
        "request_id": "request-test",
        "invocation_id": "invocation-test",
    }
    with services.registry.project_store("project-test").transaction() as tx:
        state.bind(
            tx, "session-test", "permission", identity, {**base, "native_id": first["request_id"]}
        )
        with pytest.raises(DomainError) as error:
            state.bind(
                tx,
                "session-test",
                "permission",
                identity,
                {**base, "native_id": second["request_id"]},
            )
        assert error.value.code == "attribution_unavailable"
