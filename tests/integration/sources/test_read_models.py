import json
from copy import deepcopy
from pathlib import Path

import pytest
from proscenium.api.errors import DomainError
from proscenium.contracts import canonical, validate
from proscenium.sources.common import sha

from .helpers import closure, commit, publication, ready_image

EXAMPLES = Path(__file__).resolve().parents[3] / "docs/contracts/examples"


def test_shot_plan_pins_authored_bytes_and_exact_typed_selection(
    source_services, source_caller, root, client, owner_headers
):
    ready_image(source_services)
    plan = json.loads((EXAMPLES / "authored-shot-plan.json").read_bytes())
    plan["project_id"] = "project-test"
    data = canonical(plan)
    name = "storyboards/plan.json"
    revision = commit(root, name, data)
    body = publication(revision, closure(revision, name, data))
    body["items"][0]["kind"] = "storyboard"
    catalogue = source_services.extension("catalogue")
    catalogue.publish(source_caller, body)
    (root / name).write_bytes(b"not even JSON now")
    before = (root / name).read_bytes()
    projection = catalogue.shot_plan("project-test", "version-test")
    assert projection["shots"][0]["frames"][1]["state"] == "selected"
    assert not projection["shots"][0]["frames"][1]["owner_selected"]
    selection = dict(
        schema_version="1.0",
        selection_id="selection-exact",
        project_id="project-test",
        slot=dict(
            kind="storyboard_frame",
            storyboard_version=dict(item_id="item-test", version_id="version-test"),
            shot_id="shot-fixture",
            frame_id="frame-end",
        ),
        choice=dict(kind="artifact", artifact_id="image-end"),
        state="selected",
        state_revision=1,
        actor=dict(kind="user", id="owner-test"),
    )
    with source_services.registry.project_store("project-test").transaction() as tx:
        # W05 records have wire revision 1 at creation, storage revision 0.
        tx.put(
            "selections",
            selection["selection_id"],
            {k: v for k, v in selection.items() if k != "state_revision"},
        )
    # Test fixture must preserve the normative wire record without asking F00's revisioned
    # put to conflate W05's independent selection revision with the storage revision.
    with source_services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "selections",
            selection["selection_id"],
            selection,
            expected_revision=0,
            schema="SelectionRecord",
        )
    assert catalogue.shot_plan("project-test", "version-test")["shots"][0]["frames"][1][
        "owner_selected"
    ]
    selection["choice"]["artifact_id"] = "another-image"
    selection["state_revision"] = 2
    with source_services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "selections",
            selection["selection_id"],
            selection,
            expected_revision=1,
            schema="SelectionRecord",
        )
    assert not catalogue.shot_plan("project-test", "version-test")["shots"][0]["frames"][1][
        "owner_selected"
    ]
    assert (root / name).read_bytes() == before
    assert projection["source_commit"] == revision
    assert client.get(
        "/api/v1/projects/project-test/versions/version-test/shot-plan"
    ).json() == catalogue.shot_plan("project-test", "version-test")


def test_shot_plan_new_version_does_not_inherit_owner_choice(source_services, source_caller, root):
    ready_image(source_services)
    plan = json.loads((EXAMPLES / "authored-shot-plan.json").read_bytes())
    plan["project_id"] = "project-test"
    data = canonical(plan)
    name = "storyboards/plan.json"
    revision = commit(root, name, data)
    catalogue = source_services.extension("catalogue")
    body = publication(revision, closure(revision, name, data))
    body["items"][0]["kind"] = "storyboard"
    catalogue.publish(source_caller, body)
    selection = dict(
        schema_version="1.0",
        selection_id="selection-old-plan",
        project_id="project-test",
        slot=dict(
            kind="storyboard_frame",
            storyboard_version=dict(item_id="item-test", version_id="old-version"),
            shot_id="shot-fixture",
            frame_id="frame-end",
        ),
        choice=dict(kind="artifact", artifact_id="image-end"),
        state="selected",
        state_revision=1,
        actor=dict(kind="user", id="owner-test"),
    )
    with source_services.registry.project_store("project-test").transaction() as tx:
        stored = {k: v for k, v in selection.items() if k != "state_revision"}
        tx.put("selections", selection["selection_id"], stored)
        tx.put(
            "selections",
            selection["selection_id"],
            selection,
            expected_revision=0,
            schema="SelectionRecord",
        )
    assert not catalogue.shot_plan("project-test", "version-test")["shots"][0]["frames"][1][
        "owner_selected"
    ]


def test_sequence_map_reads_pinned_authored_document(
    source_services, source_caller, root, client, owner_headers
):
    from .test_publication import preview

    scene_revision = commit(root)
    scene = publication(
        scene_revision,
        closure(scene_revision),
        item_id="scene-item",
        version_id="scene-version",
        publication_id="publication-scene",
    )
    scene["items"][0]["preview_requests"] = [preview(scene["items"][0]["source"]["digest"])]
    receipt, _ = source_services.extension("catalogue").publish(source_caller, scene)
    document = dict(
        schema_version="1.0",
        sequence_version_id="version-test",
        project_id="project-test",
        fps={"numerator": 24, "denominator": 1},
        frame_count=4,
        silent=True,
        entries=[
            dict(
                entry_id="entry-one",
                shot_id="shot-fixture",
                version_id="scene-version",
                job_id=receipt["job_ids"][0],
                source_frames={"start": 1, "end": 4},
                sequence_frames={"start": 1, "end": 4},
                transform="trim",
                mapping_artifact_id=None,
                choice="agent",
            )
        ],
    )
    data = canonical(document)
    name = "sequences/main.json"
    revision = commit(root, name, data)
    body = publication(
        revision,
        closure(
            revision,
            name,
            data,
            dependencies=[{"item_id": "scene-item", "version_id": "scene-version"}],
        ),
    )
    body["items"][0]["kind"] = "sequence"
    catalogue = source_services.extension("catalogue")
    catalogue.publish(source_caller, body)
    (root / name).write_bytes(b"dirty future sequence")
    assert catalogue.sequence_map("project-test", "version-test") == document
    assert (
        client.get("/api/v1/projects/project-test/versions/version-test/sequence-map").json()
        == document
    )


def test_proxy_mapping_validates_bytes_closure_and_pinned_proxy(
    source_services, source_caller, root, client, owner_headers
):
    artifacts = source_services.extension("artifacts")
    revision = commit(root)
    manifest = closure(revision)
    proxy = artifacts.promote(
        "project-test",
        "proxy-test",
        b"synthetic glb bytes, not native evidence",
        kind="proxy",
        purpose="inspection",
        media_type="model/gltf-binary",
    )
    mapping = dict(
        schema_version="1.0",
        project_id="project-test",
        version_id="version-test",
        proxy_artifact_id="proxy-test",
        source_closure_digest=manifest["digest"],
        sample_frame=1,
        space="proxy_world_m",
        nodes=[],
    )
    validate("ProxyMapping", mapping)
    metadata = artifacts.promote(
        "project-test",
        "mapping-test",
        canonical(mapping),
        kind="proxy_map",
        purpose="metadata",
        media_type="application/json",
    )
    with source_services.registry.project_store("project-test").transaction() as tx:
        artifacts.put(tx, proxy)
        artifacts.put(tx, metadata)
    body = publication(revision, manifest)
    body["items"][0]["artifact_ids"] = ["proxy-test", "mapping-test"]
    catalogue = source_services.extension("catalogue")
    catalogue.publish(source_caller, body)
    assert catalogue.proxy_mapping("project-test", "version-test") == mapping
    assert (
        client.get("/api/v1/projects/project-test/versions/version-test/proxy-mapping").json()
        == mapping
    )
    assert artifacts.get("project-test", "mapping-test")["sha256"] == sha(canonical(mapping))


def test_acyclic_batch_dependencies_can_reference_later_item(source_services, source_caller, root):
    from proscenium.sources import closure_digest

    revision = commit(root)
    body = publication(revision, closure(revision))
    dependent = body["items"][0]
    dependent["source"]["dependencies"] = [dict(item_id="asset-item", version_id="asset-version")]
    dependent["source"]["digest"] = closure_digest(dependent["source"])
    asset = deepcopy(
        publication(revision, closure(revision), item_id="asset-item", version_id="asset-version")[
            "items"
        ][0]
    )
    asset["kind"] = "asset"
    body["items"].append(asset)
    receipt, _ = source_services.extension("catalogue").publish(source_caller, body)
    assert len(receipt["versions"]) == 2


@pytest.mark.parametrize("mutation", [None, "weights", "source"])
def test_sequence_weighted_map_semantics_and_untrusted_owner_label(
    source_services, source_caller, root, mutation
):
    from .test_publication import preview

    revision = commit(root)
    scene = publication(
        revision,
        closure(revision),
        version_id="scene-version",
        item_id="scene-item",
        publication_id="publication-scene",
    )
    first = preview(scene["items"][0]["source"]["digest"])
    second = deepcopy(first)
    second.update(intent_id="second-intent", shot_id="second-shot")
    scene["items"][0]["preview_requests"] = [first, second]
    catalogue = source_services.extension("catalogue")
    receipt, _ = catalogue.publish(source_caller, scene)
    sources = [
        dict(
            shot_id=spec["shot_id"],
            version_id="scene-version",
            job_id=identity,
            source_frames={"start": 1, "end": 4},
        )
        for spec, identity in zip([first, second], receipt["job_ids"], strict=True)
    ]
    mapping = dict(
        schema_version="1.0",
        project_id="project-test",
        sequence_version_id="sequence-version",
        fps={"numerator": 24, "denominator": 1},
        frame_count=4,
        blend_space="linear_rgb",
        frames=[
            dict(
                sequence_frame=frame,
                contributors=[
                    {k: v for k, v in source.items() if k != "source_frames"}
                    | {"source_frame": frame, "weight": {"numerator": 1, "denominator": 2}}
                    for source in sources
                ],
            )
            for frame in range(1, 5)
        ],
    )
    if mutation == "weights":
        mapping["frames"][0]["contributors"][0]["weight"]["denominator"] = 3
    elif mutation == "source":
        mapping["frames"][0]["contributors"][0]["source_frame"] = 5
    artifacts = source_services.extension("artifacts")
    artifact = artifacts.promote(
        "project-test",
        "sequence-frame-map",
        canonical(mapping),
        kind="frame_map",
        purpose="metadata",
        media_type="application/json",
    )
    with source_services.registry.project_store("project-test").transaction() as tx:
        artifacts.put(tx, artifact)
    document = dict(
        schema_version="1.0",
        project_id="project-test",
        sequence_version_id="sequence-version",
        fps={"numerator": 24, "denominator": 1},
        frame_count=4,
        silent=True,
        entries=[
            dict(
                entry_id="transition-one",
                **sources[0],
                sequence_frames={"start": 1, "end": 4},
                transform="transition",
                mapping_artifact_id="sequence-frame-map",
                contributors=sources,
                choice="owner",
            )
        ],
    )
    data = canonical(document)
    name = "sequences/transition.json"
    revision = commit(root, name, data)
    body = publication(
        revision,
        closure(
            revision,
            name,
            data,
            dependencies=[{"item_id": "scene-item", "version_id": "scene-version"}],
        ),
        publication_id="publication-sequence",
        item_id="sequence-item",
        version_id="sequence-version",
    )
    body["items"][0]["kind"] = "sequence"
    if mutation:
        with pytest.raises(DomainError) as error:
            catalogue.publish(source_caller, body)
        assert error.value.code == "invalid_frame_mapping"
        with source_services.registry.project_store("project-test").transaction(write=False) as tx:
            assert tx.get("versions", "sequence-version", required=False) is None
    else:
        catalogue.publish(source_caller, body)
        assert (
            catalogue.sequence_map("project-test", "sequence-version")["entries"][0]["choice"]
            == "agent"
        )
        assert (root / name).read_bytes() == data


def test_item_selection_can_remain_older_than_head_without_source_record_writes(
    source_services, source_caller, root, client, owner_headers
):
    revision = commit(root)
    source = closure(revision)
    catalogue = source_services.extension("catalogue")
    catalogue.publish(source_caller, publication(revision, source))
    catalogue.publish(
        source_caller,
        publication(
            revision,
            source,
            publication_id="publication-later",
            version_id="version-later",
            head="version-test",
        ),
    )
    selection = dict(
        schema_version="1.0",
        selection_id="selection-item",
        project_id="project-test",
        slot={"kind": "item", "item_id": "item-test"},
        choice={
            "kind": "version",
            "version": {"item_id": "item-test", "version_id": "version-test"},
        },
        state="selected",
        state_revision=1,
        actor={"kind": "user", "id": "owner-test"},
    )
    with source_services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "selections",
            "selection-item",
            {k: v for k, v in selection.items() if k != "state_revision"},
        )
        tx.put(
            "selections", "selection-item", selection, expected_revision=0, schema="SelectionRecord"
        )
    item = client.get("/api/v1/projects/project-test/items").json()["items"][0]
    assert item["current_version_id"] == "version-later"
    assert item["selected_version_id"] == "version-test"
    assert client.get("/api/v1/projects/project-test/snapshot").json()["items"][0] == item
    with source_services.registry.project_store("project-test").transaction() as tx:
        assert tx.require("items", "item-test")["selected_version_id"] is None
        selection.update(state="withdrawn", choice=None, state_revision=2)
        tx.put(
            "selections", "selection-item", selection, expected_revision=1, schema="SelectionRecord"
        )
    assert (
        client.get("/api/v1/projects/project-test/items").json()["items"][0]["selected_version_id"]
        is None
    )
