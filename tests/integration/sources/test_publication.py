import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from proscenium.api.errors import DomainError
from proscenium.db.store import Transaction

from .helpers import closure, commit, publication

EXAMPLES = Path(__file__).resolve().parents[3] / "docs/contracts/examples"


def preview(digest):
    spec = json.loads((EXAMPLES / "preview-authored-camera.json").read_bytes())["spec"]
    spec["source_closure_digest"] = digest
    spec["device"] = "CPU"
    return spec


def test_publication_job_events_atomic_and_replay_before_current_state(
    source_services, source_caller, root
):
    revision = commit(root)
    manifest = closure(revision)
    body = publication(
        revision,
        manifest,
        description="Published description",
        tags=["tag"],
        provenance=dict(source_url=None, creator="Synthetic author", licence="Unknown"),
    )
    spec = preview(manifest["digest"])
    body["items"][0]["preview_requests"] = [spec]
    catalogue = source_services.extension("catalogue")
    result, replayed = catalogue.publish(source_caller, body)
    assert not replayed
    assert len(result["job_ids"]) == 1
    job = source_services.jobs.get("project-test", result["job_ids"][0])
    assert job["target"]["version"] == result["versions"][0]
    assert job["render_spec"] == spec
    assert job["state"] == "planned"
    store = source_services.registry.project_store("project-test")
    with store.transaction(write=False) as tx:
        version = tx.require("versions", "version-test")
        assert version["preview_requests"] == [spec]
        assert version["description"] == "Published description"
        assert version["provenance"]["licence"] == "Unknown"
        assert tx.watermark() == result["event_sequence"]
        assert tx.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    next_body = publication(
        revision,
        manifest,
        publication_id="publication-second",
        version_id="version-second",
        head="version-test",
    )
    catalogue.publish(source_caller, next_body)
    source_services.gate.close("project-test")
    with store.transaction() as tx:
        work = tx.require("work", "work-test")
        work.update(state="completed", state_revision=1)
        tx.put("work", "work-test", work, expected_revision=0)
    retry, replayed = catalogue.publish(source_caller, body)
    assert replayed and retry["job_ids"] == result["job_ids"]
    with store.transaction(write=False) as tx:
        assert tx.require("items", "item-test")["current_version_id"] == "version-second"
        assert tx.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    conflict = deepcopy(body)
    conflict["items"][0]["name"] = "Changed submission"
    with pytest.raises(DomainError) as error:
        catalogue.publish(source_caller, conflict)
    assert error.value.code == "idempotency_conflict"


def test_batch_rollback_includes_jobs_heads_events_and_operation(
    source_services, source_caller, root, monkeypatch
):
    revision = commit(root)
    manifest = closure(revision)
    body = publication(revision, manifest)
    body["items"][0]["preview_requests"] = [preview(manifest["digest"])]
    body["items"].append(
        publication(revision, manifest, item_id="item-other", version_id="version-other")["items"][
            0
        ]
    )
    original = Transaction.emit

    def fail_publication(tx, event_type, payload, **kwargs):
        if event_type == "publication.created":
            raise RuntimeError("synthetic failure after all writes before commit")
        return original(tx, event_type, payload, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Transaction, "emit", fail_publication)
        with pytest.raises(RuntimeError):
            source_services.extension("catalogue").publish(source_caller, body)
    with source_services.registry.project_store("project-test").transaction(write=False) as tx:
        for namespace in (
            "versions",
            "items",
            "jobs",
            "attempts",
            "publications",
            "preview_intents",
        ):
            assert not tx.all(namespace)
        assert tx.watermark() == 0
        assert tx.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
        assert (
            tx.connection.execute(
                "SELECT COUNT(*) FROM operations WHERE scope='catalogue.publish'"
            ).fetchone()[0]
            == 0
        )
    source_services.extension("catalogue").publish(source_caller, body)


@pytest.mark.parametrize("failure", ["head", "missing", "cycle"])
def test_invalid_batch_never_partially_publishes(source_services, source_caller, root, failure):
    from proscenium.sources import closure_digest

    revision = commit(root)
    body = publication(revision, closure(revision))
    row = body["items"][0]
    if failure == "head":
        row["expected_current_version_id"] = "not-current"
    else:
        row["source"]["dependencies"] = [
            {
                "item_id": "item-test",
                "version_id": "version-test" if failure == "cycle" else "missing-version",
            }
        ]
        row["source"]["digest"] = closure_digest(row["source"])
    with pytest.raises(DomainError) as error:
        source_services.extension("catalogue").publish(source_caller, body)
    assert (
        error.value.code
        == {
            "head": "publication_head_conflict",
            "missing": "dependency_missing",
            "cycle": "source_conflict",
        }[failure]
    )
    with source_services.registry.project_store("project-test").transaction(write=False) as tx:
        assert not tx.all("versions")
        assert not tx.all("items")


def test_admitted_invocation_and_epoch_are_not_model_fields(source_services, source_caller, root):
    revision = commit(root)
    body = publication(revision, closure(revision))
    for caller in (
        replace(source_caller, owner_epoch="stale"),
        replace(source_caller, invocation_id=None),
        replace(source_caller, operations=()),
        replace(source_caller, work_id="another-work"),
    ):
        with pytest.raises(DomainError):
            source_services.extension("catalogue").publish(caller, body)


def test_http_publication_and_version_metadata_round_trip(
    client, owner_headers, source_services, source_caller, root
):
    revision = commit(root)
    body = publication(revision, closure(revision), description="Historical metadata", tags=["v1"])
    token = source_services.auth.issue_capability(source_caller)
    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": body["publication_id"]}
    response = client.post("/internal/v1/publications", json=body, headers=headers)
    assert response.status_code == 201, response.text
    assert client.post("/internal/v1/publications", json=body, headers=headers).status_code == 200
    records = client.get("/api/v1/projects/project-test/items/item-test/versions").json()["items"]
    assert records[0]["description"] == "Historical metadata"
    assert records[0]["tags"] == ["v1"]
    assert "provenance" not in records[0]
    current = client.get("/api/v1/projects/project-test/items").json()["items"][0]
    assert current["description"] == "Historical metadata"
    assert current["current_version_id"] == "version-test"
    assert (
        client.post(
            "/internal/v1/publications",
            json=body,
            headers={"Idempotency-Key": body["publication_id"], **owner_headers},
        ).status_code
        == 401
    )


def test_concurrent_publications_cannot_overwrite_same_head(source_services, source_caller, root):
    from concurrent.futures import ThreadPoolExecutor

    revision = commit(root)
    source = closure(revision)

    def publish(identity):
        try:
            source_services.extension("catalogue").publish(
                source_caller,
                publication(
                    revision,
                    source,
                    publication_id="publication-" + identity,
                    version_id="version-" + identity,
                ),
            )
            return "published"
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ["one", "two"]))
    assert sorted(results) == ["publication_head_conflict", "published"]
    with source_services.registry.project_store("project-test").transaction(write=False) as tx:
        assert len(tx.all("versions")) == len(tx.all("publications")) == 1


def test_unity_provenance_ancestry_and_omissions_survive_service_reload(
    source_services, source_caller, root
):
    from proscenium.catalogue.service import Catalogue
    from proscenium.sources import closure_digest
    from proscenium.sources.common import sha

    catalogue = source_services.extension("catalogue")
    initial = commit(root)
    catalogue.publish(source_caller, publication(initial, closure(initial)))
    script_path, script = (
        "unity/Assets/Player.cs",
        b"// Synthetic metadata fixture, not an executed controller.\n",
    )
    commit(root, script_path, script)
    scene_path, scene = (
        "unity/Assets/Scenes/Main.unity",
        b"%YAML 1.1\n# Synthetic metadata fixture.\n",
    )
    revision = commit(root, scene_path, scene)
    source = closure(revision, scene_path, scene)
    source["files"].append(
        dict(
            destination=script_path,
            origin=dict(
                kind="git",
                repository_id="repo-test",
                commit=revision,
                path=script_path,
                sha256=sha(script),
            ),
        )
    )
    source["digest"] = closure_digest(source)
    body = publication(
        revision,
        source,
        publication_id="publication-unity",
        item_id="unity-item",
        version_id="unity-version",
        description="Exact synthetic Unity metadata",
        tags=["unity", "fixture"],
        provenance={"source_url": None, "creator": "Synthetic author", "licence": None},
        derived_from=[{"item_id": "item-test", "version_id": "version-test"}],
    )
    row = body["items"][0]
    row["kind"] = "unity_result"
    row["unity"] = dict(
        editor_version="6000.3.27f1",
        pipeline="URP",
        scene_path=scene_path,
        player_script=script_path,
        validation={"status": "not_run", "notes": "Metadata persistence only, not Unity execution"},
    )
    catalogue.publish(source_caller, body)
    reloaded = Catalogue(
        source_services,
        source_services.extension("sources"),
        source_services.extension("artifacts"),
    )
    version = reloaded.version("project-test", "unity-version")
    for field in (
        "kind",
        "name",
        "description",
        "tags",
        "provenance",
        "derived_from",
        "unity",
        "source",
        "preview_requests",
    ):
        assert version[field] == row[field]
    assert version["unity"]["validation"]["status"] == "not_run"
