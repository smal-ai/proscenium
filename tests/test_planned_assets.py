"""E3 shot intent compatibility and E4 parity for the new typed edge."""

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from proscenium.api.errors import DomainError
from proscenium.catalogue.relationship_backfill import rebuild_relationships
from proscenium.catalogue.validation import AUTHORED_PIN_MISSING
from proscenium.contracts import canonical, validate
from proscenium.copilot.capabilities import validate_authored_document
from proscenium.copilot.tools import tool_failure
from proscenium.projects.importer import import_project
from proscenium.projects.service import Projects
from proscenium.reviews import ReviewStore
from proscenium.sources import Sources, closure_digest
from proscenium.sources.common import git
from test_project_importer import (
    FPS,
    IDENTITY,
    OWNER,
    P,
    admitted_tool,
    item,
    pin,
    png,
    revised_source,
    source,
    write,
)
from test_version_relationships import (
    V3,
    V4,
    get_version,
    index,
    manifest,
    page,
    plan,
    publication,
    reset_index,
    services_at,
)
from test_version_relationships import (
    graph as graph,
)
from test_worker_capabilities_and_publication import PROJECT as TOOL_PROJECT
from test_worker_capabilities_and_publication import invoke
from test_worker_capabilities_and_publication import plan as tool_plan
from test_worker_capabilities_and_publication import publication as tool_publication
from test_worker_capabilities_and_publication import runtime as runtime

CLI = Path(__file__).resolve().parents[1] / "agent/skills/storyboard/plan_tools.py"


def host(services, document, **options):
    return validate_authored_document(
        services,
        P,
        dict(
            schema_version="1.0",
            project_id=P,
            document=document,
            document_kind=options.pop("kind", "authored_shot_plan"),
            **options,
        ),
    )


def checkout(*args):
    return json.loads(subprocess.check_output([sys.executable, CLI, *args]))


def four_shots(*, bound=True):
    document = plan("first", scene=pin("landing") if bound else None)
    opening, middle = document["shots"]
    for identity, template in (("third", middle), ("fourth", opening)):
        shot = copy.deepcopy(template)
        shot["shot_id"] = identity
        for frame in shot["frames"]:
            frame["frame_id"] = f"{identity}-{frame['role']}"
        document["shots"].append(shot)
    return document


def committed_board(graph, document, dependencies):
    old = get_version(graph.services, "first-v1")
    updated = write(graph.root, "first.json", document)
    closure, commit = revised_source(graph.services, graph.root, old, updated, "Plan revision")
    closure["dependencies"] = dependencies
    closure["digest"] = closure_digest(closure)
    return (
        dict(
            old,
            version_id="first-v2",
            source=closure,
            source_commit=commit,
            expected_current_version_id="first-v1",
        ),
        dict(
            item("first", "storyboard", source(updated, deps=dependencies, commit=commit)),
            version_id="first-v2",
            expected_current_version_id="first-v1",
        ),
    )


def test_e3_and_e4_same_batch_publish_import_and_rebuild(graph, tmp_path, monkeypatch):
    offline = graph.services
    document = four_shots()
    # Shot 1 plans both wardrobe versions, shot 4 plans the newer one. The bound
    # scene still pins only V3. V3 is explicitly both board-wide and shot-local.
    document["shots"][0]["planned_assets"] = [V4, V3]
    document["shots"][2]["planned_assets"] = []
    document["shots"][3]["planned_assets"] = [V4]
    board, board_import = committed_board(graph, document, [V3, V4, pin("landing")])
    old_hero = get_version(offline, V3["version_id"])
    new_hero = dict(old_hero, **V4, expected_current_version_id=V3["version_id"])
    hero_import = dict(
        graph.manifest["items"][0],
        version_id=V4["version_id"],
        expected_current_version_id=V3["version_id"],
    )
    new_root = tmp_path / "live"
    shutil.copytree(graph.root, new_root, ignore=shutil.ignore_patterns(".proscenium"))
    with services_at(tmp_path / "online") as online:
        Projects(online).open(
            OWNER,
            dict(
                schema_version="1.0",
                path=str(new_root),
                create=True,
                name="Live copy",
                operation_id="open-live",
            ),
            identity=IDENTITY,
            install_lfs_hooks=False,
        )
        online.registry.activate(P)
        catalogue = online.extension("catalogue")
        caller = admitted_tool(online)
        base = [get_version(offline, row["version_id"]) for row in graph.manifest["items"]]
        assert catalogue.publish(caller, publication(*base))[1] is False
        legacy = catalogue.shot_plan(P, "first-v1")
        old_source = catalogue.sources.authored(P, get_version(online, "first-v1"))
        old_bytes = git(new_root, "show", f"{legacy['source_commit']}:first.json")
        assert json.loads(old_bytes) == old_source
        assert all("planned_assets" not in shot for shot in legacy["shots"])

        request = publication(board, new_hero, identity="publish-planned")
        assert host(online, request, kind="publication_submit")["valid"]
        assert catalogue.publish(caller, request)[1] is False
        assert catalogue.publish(caller, request)[1] is True
        assert catalogue.shot_plan(P, "first-v1") == legacy
        assert git(new_root, "show", f"{legacy['source_commit']}:first.json") == old_bytes
        assert get_version(online, "landing-v1")["source"]["dependencies"] == [V3]

        origin_reader = catalogue.sources.git_bytes

        def board_bytes_only(project_id, origin):
            assert origin["path"] == "first.json", "Shot planning hydrated a dependency"
            return origin_reader(project_id, origin)

        with monkeypatch.context() as patch:
            patch.setattr(catalogue.sources, "git_bytes", board_bytes_only)
            assert host(online, document)["valid"]
            projected = catalogue.shot_plan(P, "first-v2")
        assert projected["asset_dependencies"] == [V3]
        assert [shot.get("planned_assets") for shot in projected["shots"]] == [
            [V4, V3],
            None,
            [],
            [V4],
        ]
        assert "planned_assets" not in projected["shots"][1]
        expected = [
            {
                "relation": "planned_asset",
                "from": {"kind": "shot", "board": pin("first", "first-v2"), "shot_id": identity},
                "to": {"kind": "version", "ref": V4},
                "details": {},
            }
            for identity in ("fourth", "z-first")
        ]
        assert page(online, V4["version_id"], direction="used_by")["items"] == expected
        assert [e["relation"] for e in page(online, "first-v2")["items"]] == [
            "contains_shot"
        ] * 4 + ["board_reference"]
        for shot in projected["shots"]:
            uses = page(online, "first-v2", shot_id=shot["shot_id"])["items"]
            assert sorted(
                (e["to"]["ref"] for e in uses if e["relation"] == "planned_asset"),
                key=lambda r: r["version_id"],
            ) == sorted(shot.get("planned_assets", []), key=lambda r: r["version_id"])
        assert any(
            e["relation"] == "planned_asset"
            for e in page(online, V3["version_id"], direction="used_by")["items"]
        )
        # Compare closed contract edges, not JSON fixtures shared by publishers.
        validate("RelationshipEdge", expected[0])
        with pytest.raises(DomainError):
            validate("RelationshipEdge", {**expected[0], "details": {"not_empty": True}})
        online_index = index(online)
        import_project(
            offline, OWNER, graph.root, manifest(board_import, hero_import, name="import-planned")
        )
        assert index(offline) == online_index
        assert page(offline, V4["version_id"], direction="used_by")["items"] == expected
        reset_index(online)
        rebuild_relationships(online, P)
        assert index(online) == online_index
        assert page(online, V4["version_id"], direction="used_by")["items"] == expected


@pytest.mark.parametrize(
    "defect,code",
    [
        ("duplicate", "uniqueItems"),
        ("unknown", "dependency_missing"),
        ("foreign", "dependency_missing"),
        ("mismatch", "scope_denied"),
        ("scene", "invalid_request"),
        ("storyboard", "invalid_request"),
        ("sequence", "invalid_request"),
        ("unity_result", "invalid_request"),
        ("closure", "dependency_missing"),
    ],
)
def test_e3_planned_pin_validation_on_every_surface(graph, tmp_path, defect, code):
    services = graph.services
    document = plan("first", scene=pin("landing"))
    if defect == "duplicate":
        refs = [V3, dict(reversed(list(V3.items())))]
    elif defect == "unknown":
        refs = [pin("unknown")]
    elif defect == "mismatch":
        refs = [pin("wrong-item", V3["version_id"])]
    elif defect == "foreign":
        root = tmp_path / "foreign"
        root.mkdir()
        refs = [pin("foreign")]
        import_project(
            services,
            OWNER,
            root,
            manifest(
                item("foreign", "asset", source(write(root, "asset.txt", b"foreign"))),
                identity={"project_id": "foreign", "repository_id": "foreign-repo"},
            ),
        )
        services.registry.activate(P)
    elif defect in {"scene", "storyboard"}:
        refs = [pin("landing" if defect == "scene" else "second")]
    elif defect in {"sequence", "unity_result"}:
        # Only the version metadata is needed for a rejected kind, not native or media setup.
        ref = pin(defect)
        record = dict(get_version(services, V3["version_id"]), **ref, kind=defect)
        if defect == "unity_result":
            record["unity"] = dict(
                editor_version="6000",
                pipeline="URP",
                scene_path="hero.txt",
                validation={"status": "not_run"},
            )
        with services.registry.project_store(P).transaction() as tx:
            tx.put("versions", ref["version_id"], record, schema="VersionRecord")
        refs = [ref]
    else:
        refs = [V3]
        document["asset_dependencies"] = []
    document["shots"][0]["planned_assets"] = refs
    standalone = host(services, document)
    if defect == "closure":
        assert standalone["valid"]  # A standalone plan has no closure.
    else:
        assert not standalone["valid"]
        assert standalone["issues"][0]["code"] == code
        assert standalone["issues"][0]["path"].startswith("shots[0].planned_assets")
        if code == "invalid_request":
            assert "kind asset" in standalone["issues"][0]["message"]

    board, imported = committed_board(
        graph, document, [pin("landing")] + ([] if defect == "closure" else [V3])
    )
    request = publication(board, identity="reject-planned")
    preflight = host(services, request, kind="publication_submit")
    assert not preflight["valid"] and preflight["issues"][0]["code"] == code
    if defect != "duplicate":
        issue = preflight["issues"][0]
        assert issue["consuming_version"] == pin("first", "first-v2")
        assert issue["reference"] == refs[0]
        assert issue["shot_id"] == "z-first"
        assert issue["path"] == "shots[0].planned_assets[0]"
    catalogue = services.extension("catalogue")
    caller = admitted_tool(services)
    expected_error = "invalid_request" if defect == "duplicate" else code
    unchanged = index(services)
    with pytest.raises(DomainError) as live_error:
        catalogue.publish(caller, request)
    assert live_error.value.code == expected_error
    assert index(services) == unchanged

    # Use an independently opened copy without admitted work for the offline writer.
    offline_root = tmp_path / "offline"
    shutil.copytree(graph.root, offline_root, ignore=shutil.ignore_patterns(".proscenium"))
    with services_at(tmp_path / "offline-registry") as offline:
        boot = copy.deepcopy(graph.manifest)
        for row in boot["items"]:
            row["source"]["source_commit"] = get_version(services, row["version_id"])[
                "source_commit"
            ]
        import_project(offline, OWNER, offline_root, boot)
        offline.registry.activate(P)
        if defect in {"sequence", "unity_result"}:
            with offline.registry.project_store(P).transaction() as tx:
                tx.put("versions", record["version_id"], record, schema="VersionRecord")
        before = index(offline)
        with pytest.raises(DomainError) as offline_error:
            import_project(offline, OWNER, offline_root, manifest(imported, name="reject-planned"))
        assert offline_error.value.code == expected_error
        assert index(offline) == before

    # Simulate sealed historical bytes that did not pass today's writer.
    board.pop("expected_current_version_id")
    with services.registry.project_store(P).transaction() as tx:
        tx.put("versions", board["version_id"], board, schema="VersionRecord")
    with pytest.raises(DomainError) as read_error:
        catalogue.shot_plan(P, board["version_id"])
    assert read_error.value.code == expected_error
    if defect == "closure":
        assert read_error.value.details["reference"] == V3
        assert read_error.value.details["path"] == "shots[0].planned_assets[0]"
    rebuild_relationships(services, P)
    historical = page(services, board["version_id"])
    assert historical["coverage"] == {"state": "partial", "reasons": ["invalid_historical_record"]}
    assert {edge["relation"] for edge in historical["items"]} == {"declared_dependency"}


def test_two_board_batch_reports_exact_missing_pin_to_both_model_tools(runtime):
    hero_publication = tool_publication(runtime, b"Hero v3 fixture")
    hero_publication["publication_id"] = "publish-hero"
    hero_publication["items"][0].update(**V3, kind="asset", name="Hero")
    catalogue = runtime.services.extension("catalogue")
    catalogue.sources = Sources(runtime.services)
    hero_receipt, status = invoke(runtime, "publish_outputs", hero_publication, "publish-hero")
    assert status != "failure", hero_receipt

    rows = []
    for identity, dependencies in (("board-one", [V3]), ("board-two", [])):
        document = tool_plan()
        document["plan_id"] = identity
        document["shots"][0]["planned_assets"] = [V3]
        row = tool_publication(runtime, canonical(document))["items"][0]
        row.update(**pin(identity), name=identity)
        row["source"]["dependencies"] = dependencies
        row["source"]["digest"] = closure_digest(row["source"])
        rows.append(row)
    catalogue.sources = Sources(runtime.services)
    batch = dict(
        schema_version="1.0",
        project_id=TOOL_PROJECT,
        publication_id="publish-two-boards",
        items=rows,
    )
    preflight = dict(
        schema_version="1.0",
        project_id=TOOL_PROJECT,
        document_kind="publication_submit",
        document=batch,
    )
    expected = dict(
        path="shots[0].planned_assets[0]",
        consuming_version=pin("board-two"),
        reference=V3,
        shot_id="shot-one",
    )
    checked, status = invoke(
        runtime, "validate_authored_document", preflight, "preflight-two-boards"
    )
    assert status != "failure" and not checked["valid"]
    assert checked["issues"] == [
        dict(phase="semantic", code="dependency_missing", message=AUTHORED_PIN_MISSING, **expected)
    ]
    failed, status = invoke(runtime, "publish_outputs", batch, "publish-two-boards")
    assert status == "failure"
    assert failed == dict(
        error="dependency_missing", message=AUTHORED_PIN_MISSING, details=expected
    )
    with runtime.store.transaction(write=False) as tx:
        assert tx.get("versions", "board-one-v1", required=False) is None
        assert tx.get("versions", "board-two-v1", required=False) is None


@pytest.mark.parametrize("name", ["publish_outputs", "validate_authored_document"])
def test_pin_tool_diagnostics_drop_unvalidated_detail_keys(name):
    details = dict(
        path="shots[0].planned_assets[0]",
        consuming_version=pin("board-two"),
        reference=V3,
        shot_id="shot-one",
    )
    error = DomainError(
        "dependency_missing",
        422,
        AUTHORED_PIN_MISSING,
        **details,
        private_note="/home/private/.env",
        consuming_version_id="other-v1",
        current_revision=6,
    )
    assert json.loads(tool_failure(error, name).text_result_for_llm)["details"] == details
    error.details.update(
        path="/home/private/.env",
        consuming_version={**pin("board-two"), "extra": True},
        reference={"item_id": "hero", "version_id": "./private"},
        shot_id=["shot-one"],
    )
    assert json.loads(tool_failure(error, name).text_result_for_llm)["details"] == {}


def test_owner_panel_selection_is_scoped_to_the_published_board_revision(graph):
    services = graph.services
    catalogue = services.extension("catalogue")
    artifacts = services.extension("artifacts")
    image = artifacts.promote(
        P, "panel-image", png("blue"), kind="image", purpose="keyframe", media_type="image/png"
    )
    with services.registry.project_store(P).transaction() as tx:
        artifacts.put(tx, image)

    document = plan("selection-board")
    shot = document["shots"][0]
    shot["planned_assets"] = [V3]
    frame = shot["frames"][0]
    frame.update(artifact_id=image["artifact_id"], state="available")
    template, _ = committed_board(graph, document, [V3])
    first = pin("selection-board")
    second = pin("selection-board", "selection-board-v2")
    caller = admitted_tool(services)
    catalogue.publish(
        caller,
        publication(
            dict(template, **first, expected_current_version_id=None), identity="selection-v1"
        ),
    )
    ReviewStore(services).save_selection(
        OWNER,
        dict(
            schema_version="1.0",
            project_id=P,
            operation_id="select-v1-panel",
            selection_id="v1-panel",
            expected_revision=0,
            action="select",
            slot=dict(
                kind="storyboard_frame",
                storyboard_version=first,
                shot_id=shot["shot_id"],
                frame_id=frame["frame_id"],
            ),
            choice=dict(kind="artifact", artifact_id=frame["artifact_id"]),
        ),
    )
    catalogue.publish(
        caller,
        publication(
            dict(template, **second, expected_current_version_id=first["version_id"]),
            identity="selection-v2",
        ),
    )
    for ref, selected in ((first, True), (second, False)):
        projected = catalogue.shot_plan(P, ref["version_id"])["shots"][0]
        assert projected["shot_id"] == shot["shot_id"] and projected["planned_assets"] == [V3]
        assert projected["frames"][0] == {**frame, "owner_selected": selected}


@pytest.mark.parametrize("missing_contributor", [False, True])
def test_sequence_preflight_reports_exact_missing_pin(graph, missing_contributor):
    landing = pin("landing")
    stairs = pin("stairs")
    cut = pin("cut")
    frames = {"start": 1, "end": 1}
    with graph.services.registry.project_store(P).transaction() as tx:
        tx.put(
            "jobs",
            "test-render",
            dict(
                kind="render",
                target=dict(version=landing, shot_id="landing-shot"),
                render_spec=dict(frames=frames, fps=FPS),
            ),
        )
    entry = dict(
        entry_id="cut-entry",
        shot_id="landing-shot",
        version_id=landing["version_id"],
        job_id="test-render",
        source_frames=frames,
        sequence_frames=frames,
        transform="identity",
        mapping_artifact_id=None,
    )
    if missing_contributor:
        entry["contributors"] = [
            dict(shot_id="stairs-shot", version_id=stairs["version_id"], source_frames=frames)
        ]
    document = dict(
        schema_version="1.0",
        project_id=P,
        sequence_version_id=cut["version_id"],
        fps=FPS,
        frame_count=1,
        silent=True,
        entries=[entry],
    )
    row, _ = committed_board(graph, document, [landing] if missing_contributor else [])
    request = publication(dict(row, **cut, kind="sequence", expected_current_version_id=None))
    result = host(graph.services, request, kind="publication_submit")
    assert not result["valid"]
    assert result["issues"] == [
        dict(
            phase="semantic",
            code="dependency_missing",
            message=AUTHORED_PIN_MISSING,
            path=f"entries[0]{'.contributors[0]' if missing_contributor else ''}.version_id",
            consuming_version=cut,
            reference=stairs if missing_contributor else landing,
            shot_id="stairs-shot" if missing_contributor else "landing-shot",
        )
    ]


def test_e3_host_checkout_impact_and_compare_parity(graph):
    services = graph.services
    document = four_shots(bound=False)
    # This historical board-wide non-asset ref remains supported.
    document["asset_dependencies"] = [pin("landing")]
    document["shots"][0]["planned_assets"] = [V3]
    document["shots"][3]["planned_assets"] = [V3]
    before_path = graph.root / "before.json"
    before_path.write_bytes(canonical(document))
    assert checkout("validate", before_path)["shot_count"] == 4
    local = ["z-first", "fourth"]
    for identity, affected in [
        ("hero-v3", local),
        ("hero", local),
        ("landing", [s["shot_id"] for s in document["shots"]]),
        ("Run", []),
    ]:
        result = host(services, document, changed_ids=[identity])
        assert result["valid"]
        cli = checkout("impact", before_path, "--changed-id", identity)
        assert cli.pop("conservative_plan_dependencies") is True
        assert cli["affected_shot_ids"] == affected
        assert cli == {key: result["impact"][key] for key in cli}

    from test_version_relationships import publish_hero4

    publish_hero4(services)
    document["shots"][0]["planned_assets"].append(V4)
    before_path.write_bytes(canonical(document))
    after_path = graph.root / "after.json"
    for variant, affected in [
        ("permute-and-empty", []),
        ("remove-pin", ["z-first"]),
        ("replace-pin", ["fourth"]),
        ("add-pin", ["a-next"]),
        ("reorder", [s["shot_id"] for s in document["shots"]]),
        ("remove-shot", [s["shot_id"] for s in document["shots"]]),
        ("global", [s["shot_id"] for s in document["shots"]]),
    ]:
        after = copy.deepcopy(document)
        if variant == "permute-and-empty":
            after["shots"][0]["planned_assets"].reverse()
            after["shots"][1]["planned_assets"] = []
        elif variant == "remove-pin":
            del after["shots"][0]["planned_assets"]
        elif variant == "replace-pin":
            after["shots"][3]["planned_assets"] = [V4]
        elif variant == "add-pin":
            after["shots"][1]["planned_assets"] = [V4]
        elif variant == "reorder":
            after["shots"].reverse()
        elif variant == "remove-shot":
            after["shots"].pop()
        else:
            after["asset_dependencies"] = [pin("stairs")]
        after_path.write_bytes(canonical(after))
        result = host(services, after, compare_to_path=before_path.name)
        assert result["valid"], result
        cli = checkout("compare", before_path, after_path)
        assert cli == result["impact"]
        assert cli["affected_shot_ids"] == cli["changed_shot_ids"] == sorted(affected)
        assert cli["shot_order_changed"] == (variant in {"reorder", "remove-shot"})
        assert cli["removed_shot_ids"] == (["fourth"] if variant == "remove-shot" else [])
