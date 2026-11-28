"""Published graph contract, writer parity, recovery and page-bounded owner reads."""

import copy
import dataclasses
import json
import os
import shutil
import sqlite3
import stat
import subprocess
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Event
from time import perf_counter
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from proscenium.api.errors import DomainError
from proscenium.api.lifecycle import FeatureLifecycle
from proscenium.app import create_app
from proscenium.auth import COOKIE_NAME, Caller
from proscenium.catalogue import install
from proscenium.catalogue import relationship_backfill as backfill
from proscenium.catalogue.relationship_projector import (
    apply_version_projection,
    encoded,
    project_version_relationships,
    version_endpoint,
)
from proscenium.catalogue.relationship_read import SORT_VERSION, edge_query, read_relationships
from proscenium.configuration import Settings
from proscenium.contracts import validate
from proscenium.db.store import MIGRATIONS, Store, Transaction
from proscenium.projects.importer import import_project
from proscenium.projects.service import Projects
from proscenium.services import Services
from proscenium.sources import closure_digest, composition
from proscenium.sources.checkpoint import Checkpoints
from proscenium.sources.common import fingerprint, git, sha
from proscenium.sources.read_only import read_closure_files
from proscenium.sources.resolver import Resolver
from test_project_importer import FPS, IDENTITY, OWNER, P, admitted_tool, item, pin, source, write
from test_source_composition import (
    COAT3,
    HERO_BLEND,
    LANDING,
    LANDING_BLEND,
    STAIRS,
    STAIRS_BLEND,
    WALK,
    WALK_BLEND,
    checkpoint,
    producer,
    publish,
    retain,
)

from tools.operations import recovery

V3 = pin("hero", "hero-v3")
V4 = pin("hero", "hero-v4")


def plan(board, *, project=P, scene=None):
    def shot(identity, text, binding=None):
        return dict(
            shot_id=identity,
            intent=text,
            camera_intent="",
            motion_beats=["Run"],
            scene_version=binding,
            source_range={"start": 1, "end": 24} if binding else None,
            fps=FPS if binding else None,
            frames=[
                dict(
                    frame_id=f"{identity}-start",
                    role="start",
                    composition="Establish",
                    artifact_id=None,
                    state="missing",
                ),
                dict(
                    frame_id=f"{identity}-end",
                    role="end",
                    composition="",
                    artifact_id=None,
                    state="missing",
                ),
            ],
        )

    return dict(
        schema_version="1.0",
        project_id=project,
        plan_id=board,
        brief_artifact_ids=[],
        asset_dependencies=[V3] if scene else [],
        shots=[shot("z-first", "Run down the stairs", scene), shot("a-next", "")],
        unresolved=["A coat may be needed"],
    )


def manifest(*rows, identity=IDENTITY, name="relationships"):
    return dict(schema_version="1.0", import_id=name, project=identity, items=list(rows))


@contextmanager
def services_at(data):
    services = Services.create(Settings(data_directory=data, free_space_reserve_bytes=0))
    install(services)
    try:
        yield services
    finally:
        services.close()


@pytest.fixture
def graph(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    hero = write(root, "hero.txt", b"Hero v3")
    landing = write(root, "landing.txt", b"Landing")
    stairs = write(root, "stairs.txt", b"Stairs")
    board_a = write(root, "first.json", plan("first", scene=pin("landing")))
    board_b = write(root, "second.json", plan("second", scene=pin("landing")))
    boot = manifest(
        dict(
            item("hero", "asset", source(hero)), version_id=V3["version_id"], name="Hero · old coat"
        ),
        item("landing", "scene", source(landing, deps=[V3])),
        item("stairs", "scene", source(stairs, deps=[V3]), derived_from=[V3]),
        item("first", "storyboard", source(board_a, deps=[V3, pin("landing")])),
        item("second", "storyboard", source(board_b, deps=[V3, pin("landing")])),
    )
    with services_at(tmp_path / "registry") as services:
        import_project(services, OWNER, root, boot)
        services.registry.activate(P)
        yield SimpleNamespace(root=root, services=services, manifest=boot)


def page(services, version, **params):
    return validate("RelationshipPage", read_relationships(services, P, version, **params))


def get_version(services, version_id):
    with services.registry.project_store(P).transaction(write=False) as tx:
        return tx.require("versions", version_id)


def publication(*versions, identity="publish-relationships"):
    omit = {
        "schema_version",
        "project_id",
        "publication_id",
        "created_at",
        "actor",
        "preview_job_ids",
    }
    return dict(
        schema_version="1.0",
        project_id=P,
        publication_id=identity,
        items=[
            dict(
                {k: v for k, v in version.items() if k not in omit},
                expected_current_version_id=version.get("expected_current_version_id"),
            )
            for version in versions
        ],
    )


def publish_hero4(services, caller=None):
    row = get_version(services, V3["version_id"])
    return services.extension("catalogue").publish(
        caller or admitted_tool(services),
        publication(
            dict(row, **V4, name="Hero · new coat", expected_current_version_id=V3["version_id"])
        ),
    )


def index(services):
    with services.registry.project_store(P).transaction(write=False) as tx:
        db = tx.connection
        return tuple(
            sorted(map(tuple, db.execute(f"SELECT * FROM {table}")))
            for table in (
                "relationship_edges",
                "relationship_shots",
                "relationship_panels",
            )
        )


def reset_index(services):
    with services.registry.project_store(P).transaction() as tx:
        for table in (
            "relationship_edges",
            "relationship_shots",
            "relationship_panels",
            "relationship_versions",
        ):
            tx.connection.execute(f"DELETE FROM {table}")
        tx.connection.execute(
            "UPDATE relationship_meta SET relationship_revision=relationship_revision+1"
        )


def browser_for(services):
    return TestClient(
        create_app(services=services, manage_features=False), base_url=services.settings.origin
    )


def pair(services, browser):
    _, cookie, _ = services.auth.pair(
        dict(
            schema_version="1.0",
            operation_id="pair-relationships",
            pairing_code=services.auth.issue_pairing(),
            client_nonce="nonce-relationships-1234567890",
        )
    )
    browser.cookies.set(COOKIE_NAME, cookie)


def url(version, project=P):
    return f"/api/v1/projects/{project}/versions/{version}/relationships"


def seed_consumer_versions(services, item_id, kind, pins_by_version, *, documents=None):
    template = get_version(services, "hero-v3")
    documents = documents or {}
    refs = [pin(item_id, f"{item_id}-v{i + 1}") for i in range(len(pins_by_version))]
    with services.registry.project_store(P).transaction() as tx:
        tx.put(
            "items",
            item_id,
            dict(project_id=P, item_id=item_id, current_version_id=refs[-1]["version_id"]),
            expected_revision=tx.revision("items", item_id)
            if tx.get("items", item_id, required=False) is not None
            else None,
        )
        for i, (ref, pins) in enumerate(zip(refs, pins_by_version, strict=True)):
            version = dict(
                template,
                **ref,
                kind=kind,
                derived_from=[],
                source=dict(template["source"], dependencies=pins),
                # All revisions have the same timestamp; rowid reflects insertion order.
                created_at="2026-11-28T07:00:00" + ("+00:00" if i % 2 == 0 else "Z"),
            )
            tx.put("versions", ref["version_id"], version, schema="VersionRecord")
            apply_version_projection(tx, project_version_relationships(version, documents.get(i)))
    return refs


def consumer_versions(response):
    return {edge["from"]["ref"]["version_id"] for edge in response["items"]}


@pytest.mark.parametrize("count", [3, 55])
def test_latest_per_item_prefers_current_and_paginates_after_deduplication(graph, count):
    services = graph.services
    hero, _ = seed_consumer_versions(services, "hero", "asset", [[], []])
    scenes = seed_consumer_versions(services, "scene-a", "scene", [[hero]] * count)
    seed_consumer_versions(services, "scene-b", "scene", [[hero]])
    params = dict(direction="used_by", limit=50)
    result = page(services, "hero-v1", latest_per_item=True, **params)
    assert consumer_versions(result) == {scenes[-1]["version_id"], "scene-b-v1"}
    assert len(result["items"]) == 2 and result["next_cursor"] is None
    assert all(
        node["is_current"]
        for node in result["nodes"]
        if node["ref"] in [scenes[-1], pin("scene-b")]
    )
    history = page(services, "hero-v1", latest_per_item=False, **params)
    if count == 3:
        assert len(history["items"]) == 4
        assert consumer_versions(history) == {
            row["version_id"] for row in [*scenes, pin("scene-b")]
        }
    else:
        assert len(history["items"]) == 50 and history["next_cursor"] is not None
        assert "scene-b-v1" not in consumer_versions(history)


@pytest.mark.parametrize("count", [2, 12])
def test_latest_per_item_falls_back_to_newest_matching_historical_version(graph, count):
    services = graph.services
    first, second = seed_consumer_versions(services, "hero", "asset", [[], []])
    # In the longer history, v12 beats v9 by rowid despite the version ID sort order.
    refs = seed_consumer_versions(services, "scene-a", "scene", [[first]] * count + [[second]])
    response = page(services, "hero-v1", direction="used_by", latest_per_item=True)
    assert [edge["from"]["ref"] for edge in response["items"]] == [refs[-2]]
    assert (
        next(node for node in response["nodes"] if node["ref"] == refs[-2])["is_current"] is False
    )
    assert consumer_versions(
        page(services, "hero-v2", direction="used_by", latest_per_item=True)
    ) == {refs[-1]["version_id"]}


@pytest.mark.parametrize(
    "namespace, field, wrong_value",
    [
        ("items", "project_id", "other-project"),
        ("items", "item_id", "other-item"),
        ("versions", "project_id", "other-project"),
        ("versions", "item_id", "other-item"),
        ("versions", "version_id", "other-version"),
        ("versions", None, ["not", "an", "object"]),
    ],
)
def test_latest_per_item_ignores_invalid_record_identity(graph, namespace, field, wrong_value):
    services = graph.services
    hero, _ = seed_consumer_versions(services, "hero", "asset", [[], []])
    older, newer = seed_consumer_versions(services, "scene-a", "scene", [[hero], [hero]])
    with services.registry.project_store(P).transaction() as tx:
        item = dict(tx.require("items", "scene-a"), current_version_id=older["version_id"])
        version = dict(
            tx.require("versions", older["version_id"]), created_at="2026-11-29T07:00:00Z"
        )
        if namespace == "items":
            item[field] = wrong_value
            # A foreign item cannot make an otherwise valid, older publication current.
            version = tx.require("versions", older["version_id"])
        elif field is None:
            version = wrong_value
        else:
            version[field] = wrong_value
        # Simulate damaged records without going through the writer's project guard.
        tx.connection.executemany(
            "UPDATE records SET body=? WHERE namespace=? AND id=?",
            [
                (encoded(item), "items", "scene-a"),
                (encoded(version), "versions", older["version_id"]),
            ],
        )
    response = page(services, "hero-v1", direction="used_by", latest_per_item=True)
    assert [edge["from"]["ref"] for edge in response["items"]] == [newer]
    assert next(node for node in response["nodes"] if node["ref"] == newer)["is_current"] is False


@pytest.mark.parametrize(
    "second_stamp, winner",
    [
        ("2026-11-28T07:00:00.123499Z", "scene-a-v2"),
        ("2026-11-28T07:00:00.123401+00:00", "scene-a-v1"),
        ("invalid timestamp", "scene-a-v1"),
    ],
)
def test_latest_per_item_ranks_utc_microseconds_before_rowid(graph, second_stamp, winner):
    services = graph.services
    hero, next_hero = seed_consumer_versions(services, "hero", "asset", [[], []])
    first, second, _ = seed_consumer_versions(
        services, "scene-a", "scene", [[hero], [hero], [next_hero]]
    )
    with services.registry.project_store(P).transaction() as tx:
        for ref, stamp in [(first, "2026-11-28T09:00:00.123499+02:00"), (second, second_stamp)]:
            version_id = ref["version_id"]
            tx.put(
                "versions",
                version_id,
                dict(tx.require("versions", version_id), created_at=stamp),
                expected_revision=tx.revision("versions", version_id),
            )
    response = page(services, "hero-v1", direction="used_by", latest_per_item=True)
    assert consumer_versions(response) == {winner}


def test_latest_per_item_keeps_multiple_sequence_entries_from_winner(graph):
    services = graph.services
    frames = {"start": 1, "end": 24}
    scene = pin("landing")
    document = {
        "entries": [
            dict(
                entry_id=entry_id,
                version_id=scene["version_id"],
                shot_id=None,
                job_id="job-1",
                source_frames=frames,
                sequence_frames=frames,
            )
            for entry_id in ["clip-a", "clip-b"]
        ]
    }
    seed_consumer_versions(
        services, "edit", "sequence", [[scene], [scene]], documents={0: document, 1: document}
    )
    response = page(services, "landing-v1", direction="used_by", latest_per_item=True)
    entries = [edge for edge in response["items"] if edge["relation"] == "sequence_input"]
    assert {edge["from"]["ref"]["version_id"] for edge in entries} == {"edit-v2"}
    assert [edge["details"]["entry_id"] for edge in entries] == ["clip-a", "clip-b"]


def test_latest_per_item_keeps_latest_matching_shot_on_board(graph):
    services = graph.services
    (hero,) = seed_consumer_versions(services, "hero", "asset", [[]])
    document = plan("board")
    document["shots"][0].update(shot_id="shot-1", planned_assets=[hero])
    document["shots"][1]["planned_assets"] = [hero]
    _, latest = seed_consumer_versions(
        services, "board", "storyboard", [[hero], [hero]], documents={0: document, 1: document}
    )
    response = page(services, "hero-v1", direction="used_by", latest_per_item=True)
    assert [edge["from"] for edge in response["items"]] == [
        {"kind": "shot", "board": latest, "shot_id": shot_id} for shot_id in ["a-next", "shot-1"]
    ]


def test_latest_per_item_http_cursors_are_isolated_but_uses_is_unchanged(graph):
    services = graph.services
    with browser_for(services) as browser:
        pair(services, browser)

        def get(version, **params):
            response = browser.get(url(version), params=params)
            assert response.status_code == 200, response.text
            return validate("RelationshipPage", response.json())

        for scoped in [True, False]:
            first = get("hero-v3", direction="used_by", latest_per_item=scoped, limit=1)
            cursor = first["next_cursor"]
            assert cursor
            second = get(
                "hero-v3", direction="used_by", latest_per_item=scoped, limit=1, cursor=cursor
            )
            assert second["items"] != first["items"]
            rejected = browser.get(
                url("hero-v3"),
                params=dict(direction="used_by", latest_per_item=not scoped, cursor=cursor),
            )
            assert rejected.status_code == 400
            assert rejected.json()["error"]["code"] == "invalid_cursor"

        ordinary = get("first-v1", limit=1)
        assert get("first-v1", direction="uses", latest_per_item=True, limit=1) == ordinary
        assert get(
            "first-v1", direction="uses", latest_per_item=True, cursor=ordinary["next_cursor"]
        ) == get("first-v1", cursor=ordinary["next_cursor"])
        assert (
            get("landing-v1", direction="uses", latest_per_item=True)["items"][0]["to"]["ref"] == V3
        )


def test_e1_ordered_structural_read_never_touches_sources_or_writes(graph, monkeypatch):
    services = graph.services
    with browser_for(services) as browser:
        pair(services, browser)
        assert not (graph.root / ".proscenium/snapshots").exists()

        def unexpected(*args, **kwargs):
            pytest.fail("GET touched a native/Git process or walked all stored records")

        writes = []
        connect = Store.connect

        def trace(store, **kwargs):
            db = connect(store, **kwargs)
            db.set_trace_callback(
                lambda statement: (
                    writes.append(statement)
                    if statement.startswith(
                        ("BEGIN IMMEDIATE", "INSERT ", "UPDATE ", "DELETE ", "CREATE ")
                    )
                    else None
                )
            )
            return db

        monkeypatch.setattr(subprocess, "Popen", unexpected)
        monkeypatch.setattr(Transaction, "all", unexpected)
        monkeypatch.setattr(Store, "connect", trace)
        # A normal owner read would refresh this session, but this route is read-only.
        monkeypatch.setattr(services.auth, "SESSION_TTL", 99999)
        result = browser.get(url("first-v1"))
        assert result.status_code == 200, result.text
        result = validate("RelationshipPage", result.json())
        assert result["coverage"] == {"state": "complete", "reasons": []}
        assert [(edge["relation"], edge["details"]) for edge in result["items"]] == [
            ("contains_shot", {"ordinal": 0}),
            ("contains_shot", {"ordinal": 1}),
            ("board_reference", {}),
        ]
        assert [node["shot_id"] for node in result["nodes"] if node["kind"] == "shot"] == [
            "z-first",
            "a-next",
        ]
        unbound = validate(
            "RelationshipPage", browser.get(url("first-v1"), params={"shot_id": "a-next"}).json()
        )
        assert unbound["nodes"][0] == dict(
            kind="shot",
            board=pin("first"),
            shot_id="a-next",
            label="a-next",
            intent="",
            binding_status="unbound",
        )
        assert [edge["to"]["frame_id"] for edge in unbound["items"]] == [
            "a-next-start",
            "a-next-end",
        ]
        assert all(
            node["authored_state"] == "missing"
            and node["artifact_state"] == "missing"
            and not node["owner_selected"]
            for node in unbound["nodes"]
            if node["kind"] == "panel"
        )
        assert any(
            node["kind"] == "version" and node["ref"] == pin("first") for node in unbound["nodes"]
        )
        assert not writes
        assert not (graph.root / ".proscenium/snapshots").exists()


def test_e2_exact_reuse_history_and_live_selection(graph):
    services = graph.services
    assert publish_hero4(services)[1] is False
    slot = {"kind": "item", "item_id": "hero"}
    choice = {"kind": "version", "version": V4}
    with services.registry.project_store(P).transaction() as tx:
        tx.put(
            "selections",
            "hero-owner",
            dict(project_id=P, state="selected", slot=slot, choice=choice),
        )
        tx.put("review.selection_slots", fingerprint(slot), {"selection_id": "hero-owner"})
    v3 = page(services, "hero-v3", direction="used_by")
    assert [(edge["relation"], edge["from"]["ref"]["version_id"]) for edge in v3["items"]] == [
        ("board_reference", "first-v1"),
        ("board_reference", "second-v1"),
        ("declared_dependency", "landing-v1"),
        ("declared_dependency", "stairs-v1"),
        ("derived_from", "stairs-v1"),
    ]
    assert (
        v3["nodes"][0]["label"],
        v3["nodes"][0]["is_current"],
        v3["nodes"][0]["is_selected"],
    ) == (
        "Hero · old coat",
        False,
        False,
    )
    v4 = page(services, "hero-v4", direction="used_by")
    assert not v4["items"] and v4["nodes"][0]["is_current"] and v4["nodes"][0]["is_selected"]
    assert page(services, "stairs-v1")["items"][0]["to"]["ref"] == V3
    landing = page(services, "landing-v1", direction="used_by")
    assert [
        (e["from"]["board"], e["from"]["shot_id"], e["relation"], e["details"])
        for e in landing["items"]
    ] == [
        (
            pin(board),
            "z-first",
            "bound_scene",
            {"source_range": {"start": 1, "end": 24}, "fps": FPS},
        )
        for board in ("first", "second")
    ]


def test_e2_reverse_shots_include_exact_board_revision_badges(graph):
    services = graph.services
    old = get_version(services, "first-v1")
    revised = dict(
        old,
        version_id="first-v2",
        name="Revised storyboard",
        expected_current_version_id="first-v1",
    )
    services.extension("catalogue").publish(admitted_tool(services), publication(revised))
    slot = {"kind": "item", "item_id": "first"}
    choice = {"kind": "version", "version": pin("first", "first-v2")}
    with services.registry.project_store(P).transaction() as tx:
        tx.put(
            "selections",
            "owner-board",
            dict(project_id=P, state="selected", slot=slot, choice=choice),
        )
        tx.put("review.selection_slots", fingerprint(slot), {"selection_id": "owner-board"})
    result = page(services, "landing-v1", direction="used_by")
    assert {edge["from"]["board"]["version_id"] for edge in result["items"]} >= {
        "first-v1",
        "first-v2",
    }
    boards = {
        n["ref"]["version_id"]: n for n in result["nodes"] if n.get("item_kind") == "storyboard"
    }
    assert (
        boards["first-v1"]["label"],
        boards["first-v1"]["is_current"],
        boards["first-v1"]["is_selected"],
    ) == (old["name"], False, False)
    assert (
        boards["first-v2"]["label"],
        boards["first-v2"]["is_current"],
        boards["first-v2"]["is_selected"],
    ) == (revised["name"], True, True)


def test_e4_import_publish_rebuild_transaction_and_replay(graph, tmp_path, monkeypatch):
    offline = graph.services
    expected = index(offline)
    records = [get_version(offline, row["version_id"]) for row in graph.manifest["items"]]
    new_root = tmp_path / "live"
    shutil.copytree(graph.root, new_root, ignore=shutil.ignore_patterns(".proscenium"))
    with services_at(tmp_path / "live-registry") as online:
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
        caller = admitted_tool(online)
        catalogue = online.extension("catalogue")
        request = publication(*records)
        original = Transaction.emit

        def fail_after_writes(self, name, *args, **kwargs):
            if name == "publication.created":
                assert self.get("versions", "first-v1")
                assert self.connection.execute(
                    "SELECT 1 FROM relationship_edges LIMIT 1"
                ).fetchone()
                raise DomainError("storage_full", 507)
            return original(self, name, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Transaction, "emit", fail_after_writes)
            with pytest.raises(DomainError, match="storage full"):
                catalogue.publish(caller, request)
        assert index(online) == ([], [], [])
        with online.registry.project_store(P).transaction(write=False) as tx:
            assert not tx.all("versions")
            assert (
                tx.connection.execute(
                    "SELECT relationship_revision FROM relationship_meta"
                ).fetchone()[0]
                == 0
            )
        assert catalogue.publish(caller, request)[1] is False
        assert index(online) == expected
        stable = page(online, "first-v1")
        assert catalogue.publish(caller, request)[1] is True
        assert page(online, "first-v1") == stable
        assert import_project(offline, OWNER, graph.root, graph.manifest)["replayed"]
        assert index(offline) == expected
        reset_index(online)
        assert page(online, "first-v1")["coverage"]["reasons"] == ["indexing"]
        assert backfill.backfill_relationships(online, P, batch_size=1) == 1
        assert backfill.backfill_relationships(online, P, batch_size=3) == 3
        assert backfill.backfill_relationships(online, P, batch_size=3) == 1
        assert page(online, "first-v1")["coverage"]["reasons"] == ["indexing"]
        assert {edge["relation"] for edge in page(online, "first-v1")["items"]} == {
            "declared_dependency"
        }
        assert backfill.backfill_relationships(online, P, batch_size=3) == 2
        assert backfill.backfill_relationships(online, P) == 0
        assert index(online) == expected
        stable = page(online, "first-v1")
        backfill.rebuild_relationships(online, P)
        assert page(online, "first-v1") == stable


def test_e4_backfill_fence_does_not_replace_concurrent_index_winner(graph, monkeypatch):
    services = graph.services
    reset_index(services)
    assert backfill.backfill_relationships(services, P) == 5
    real = backfill._historical_projection
    won = []

    def race(svc, project_id, identity, raw, *, stage):
        assert stage == 2
        projection = real(svc, project_id, identity, raw, stage=stage)
        # The competing writer lands a complete row while the stale reader is
        # finishing its sealed-source work, under the same writer primitive.
        with services.registry.project_store(P).transaction() as tx:
            apply_version_projection(tx, projection)
            won.append(
                tx.connection.execute(
                    "SELECT relationship_revision FROM relationship_meta"
                ).fetchone()[0]
            )
        return project_version_relationships(json.loads(raw), reason="source_unavailable")

    monkeypatch.setattr(backfill, "_historical_projection", race)
    assert backfill.backfill_relationships(services, P, batch_size=1) == 1
    winner = page(services, "first-v1")
    assert winner["relationship_revision"] == won[0]
    assert winner["coverage"]["state"] == "complete" and len(winner["items"]) == 3


def test_e4_lifecycle_backfills_only_pending_active_versions(graph, monkeypatch):
    services = graph.services
    reset_index(services)
    drained = Event()
    real = backfill.backfill_relationships

    def bounded(svc, project_id):
        real(svc, project_id, batch_size=2)
        with svc.registry.project_store(project_id).transaction(write=False) as tx:
            if not backfill.has_pending_relationships(tx):
                drained.set()

    monkeypatch.setattr("proscenium.api.lifecycle.backfill_relationships", bounded)
    app = create_app(services=services, manage_features=True, enable_control_socket=False)
    with TestClient(app, base_url=services.settings.origin):
        assert drained.wait(5), services.extension("lifecycle").errors
        probe = FeatureLifecycle(services)
        probe._activity()
        assert not probe._relationship_pending
        assert page(services, "landing-v1", direction="used_by")["coverage"]["state"] == "complete"


def test_e4_backfill_git_timeout_does_not_stall_healthy_board_and_recovers(graph, monkeypatch):
    services = graph.services
    read_sealed = backfill.read_authored_json
    timed_out = True
    failures = 0

    def read_with_timeout(svc, project_id, version):
        nonlocal failures
        if timed_out and version["version_id"] == "first-v1":
            failures += 1
            raise subprocess.TimeoutExpired(["git", "cat-file"], 1)
        return read_sealed(svc, project_id, version)

    monkeypatch.setattr(backfill, "read_authored_json", read_with_timeout)
    reset_index(services)
    assert backfill.backfill_relationships(services, P) == 5
    pins_only = page(services, "first-v1")
    assert backfill.backfill_relationships(services, P) == 2
    assert page(services, "second-v1")["coverage"]["state"] == "complete"
    after_timeout = page(services, "first-v1")
    assert after_timeout["coverage"] == {"state": "partial", "reasons": ["indexing"]}
    assert after_timeout["items"] == pins_only["items"]
    assert {edge["relation"] for edge in pins_only["items"]} == {"declared_dependency"}
    timed_out = False
    assert backfill.backfill_relationships(services, P) == 1
    assert page(services, "first-v1")["coverage"]["state"] == "complete"

    # A persistent first-board fault must also yield to fresh work in the next
    # batch, then pause scheduling without claiming complete reverse coverage.
    timed_out = True
    failures = 0
    reset_index(services)
    assert backfill.backfill_relationships(services, P) == 5
    assert backfill.backfill_relationships(services, P, batch_size=1) == 1
    assert backfill.backfill_relationships(services, P, batch_size=1) == 1
    assert failures == 1
    assert page(services, "second-v1")["coverage"]["state"] == "complete"
    revision = page(services, "first-v1")["relationship_revision"]
    for _ in range(backfill.MAX_RETRIES - 1):
        assert backfill.backfill_relationships(services, P) == 1
    assert backfill.backfill_relationships(services, P) == 0
    assert failures == backfill.MAX_RETRIES
    probe = FeatureLifecycle(services)
    probe._activity()
    assert not probe._relationship_pending
    assert page(services, "first-v1")["relationship_revision"] == revision
    assert page(services, "landing-v1", direction="used_by")["coverage"] == {
        "state": "partial",
        "reasons": ["indexing"],
    }
    timed_out = False
    backfill.rebuild_relationships(services, P)
    assert page(services, "first-v1")["coverage"]["state"] == "complete"
    assert backfill.backfill_relationships(services, P) == 0


@pytest.mark.parametrize(
    "operational_error",
    [DomainError("storage_unavailable", 503), sqlite3.OperationalError("database is locked")],
)
def test_e4_backfill_transient_storage_503_recovers_without_invalid_classification(
    graph, monkeypatch, operational_error
):
    services = graph.services
    reset_index(services)
    assert backfill.backfill_relationships(services, P) == 5
    before = page(services, "first-v1")
    validate_sealed = backfill._validate_document

    def validate_while_busy(version, document, tx):
        if version["version_id"] == "first-v1":
            raise operational_error
        validate_sealed(version, document, tx)

    with monkeypatch.context() as patch:
        patch.setattr(backfill, "_validate_document", validate_while_busy)
        assert backfill.backfill_relationships(services, P) == 2
        assert page(services, "second-v1")["coverage"]["state"] == "complete"
        pending = page(services, "first-v1")
        assert pending["coverage"] == {"state": "partial", "reasons": ["indexing"]}
        assert pending["items"] == before["items"]
    assert backfill.backfill_relationships(services, P) == 1
    assert page(services, "first-v1")["coverage"]["state"] == "complete"
    assert backfill.backfill_relationships(services, P) == 0


def test_e4_invalid_authored_record_remains_partial_without_revision_churn(graph, monkeypatch):
    services = graph.services
    reset_index(services)
    sealed = backfill.read_authored_json

    def wrong_project(svc, project_id, version):
        doc = sealed(svc, project_id, version)
        return (
            {**doc, "project_id": "somewhere-else"} if version["version_id"] == "first-v1" else doc
        )

    monkeypatch.setattr(backfill, "read_authored_json", wrong_project)
    assert backfill.backfill_relationships(services, P) == 5
    assert backfill.backfill_relationships(services, P) == 2
    partial = page(services, "first-v1")
    assert partial["coverage"] == {"state": "partial", "reasons": ["invalid_historical_record"]}
    assert {edge["relation"] for edge in partial["items"]} == {"declared_dependency"}
    assert page(services, "landing-v1", direction="used_by")["coverage"] == partial["coverage"]
    backfill.rebuild_relationships(services, P)
    assert page(services, "first-v1") == partial


def test_e4_live_duplicate_destination_rebuild_and_restore_parity(tmp_path):
    root = tmp_path / "duplicate-project"
    root.mkdir()
    data = tmp_path / "machine"
    with services_at(data) as services:
        import_project(
            services,
            OWNER,
            root,
            manifest(
                item("first", "storyboard", source(write(root, "board.json", plan("first")))),
            ),
        )
        services.registry.activate(P)
        original = get_version(services, "first-v1")
        source_row = copy.deepcopy(original["source"])
        source_row["files"].append(copy.deepcopy(source_row["files"][0]))
        source_row["digest"] = closure_digest(source_row)
        revised = dict(
            original,
            version_id="first-v2",
            source=source_row,
            expected_current_version_id="first-v1",
        )
        caller = admitted_tool(services)
        services.extension("catalogue").publish(caller, publication(revised))
        live = page(services, "first-v2")
        assert live["coverage"] == {"state": "complete", "reasons": []}
        assert [edge["relation"] for edge in live["items"]] == ["contains_shot", "contains_shot"]
        backfill.rebuild_relationships(services, P)
        assert page(services, "first-v2") == live
        assert get_version(services, "first-v2")["source"] == source_row
        # Settle the fixture authoring records; no native session was actually spawned.
        with services.registry.project_store(P).transaction() as tx:
            for namespace, identity in (
                ("work", caller.work_id),
                ("invocations", caller.invocation_id),
            ):
                row = tx.require(namespace, identity)
                tx.put(
                    namespace,
                    identity,
                    dict(row, state="completed"),
                    expected_revision=tx.revision(namespace, identity),
                )
        settings = services.settings

    bundle = tmp_path / "duplicate-backup"
    backup = recovery.backup(settings, P, bundle, allow_incomplete=True)
    assert backup["limits"] == [{"code": "native_session_missing", "session_id": "test-session"}]
    bad_manifest = {**backup, "database_revisions": {"project": 999, "registry": 0}}
    (bundle / "manifest.json").write_text(json.dumps(bad_manifest))
    with pytest.raises(recovery.RecoveryError, match="backup_database_revision_mismatch"):
        recovery.verify(bundle)
    target = tmp_path / "restored-duplicate"
    with pytest.raises(recovery.RecoveryError, match="backup_database_revision_mismatch"):
        recovery.restore(bundle, target, rebind=True, allow_incomplete=True)
    assert not target.exists()
    (bundle / "manifest.json").write_text(json.dumps(backup))
    report = recovery.restore(bundle, target, rebind=True, allow_incomplete=True)
    with services_at(Path(report["data_directory"])) as restored:
        assert page(restored, "first-v2") == live
        assert get_version(restored, "first-v2")["source"] == source_row


@pytest.mark.parametrize("prefix", [False, True])
def test_read_only_closure_still_rejects_conflicting_destinations(graph, prefix):
    board = get_version(graph.services, "first-v1")
    asset = get_version(graph.services, "hero-v3")
    closure = copy.deepcopy(board["source"])
    destination = closure["entry_path"] + ("/child.txt" if prefix else "")
    closure["files"].append(
        dict(destination=destination, origin=asset["source"]["files"][0]["origin"])
    )
    closure["digest"] = closure_digest(closure)
    with pytest.raises(DomainError, match="dependency collision"):
        read_closure_files(graph.services, P, closure)


def test_e4_two_pass_restart_isolates_first_malformed_dependency(tmp_path, monkeypatch):
    root = tmp_path / "isolated-project"
    root.mkdir()
    data = tmp_path / "isolated-machine"
    hero = write(root, "hero.txt", b"hero")
    board = write(root, "board.json", plan("board"))
    with services_at(data) as services:
        import_project(
            services,
            OWNER,
            root,
            manifest(
                item("hero", "asset", source(hero)),
                item("board", "storyboard", source(board)),
            ),
        )
        services.registry.activate(P)
        version = get_version(services, "board-v1")
        first = dict(
            version,
            version_id="000-poison",
            item_id="poison",
            source={
                **version["source"],
                "dependencies": [pin("hero"), pin("bad", "zzz-bad")],
            },
        )
        first["source"]["digest"] = closure_digest(first["source"])
        with services.registry.project_store(P).transaction() as tx:
            tx.put("versions", "000-poison", first, schema="VersionRecord")
            tx.put("versions", "zzz-bad", dict(version_id="zzz-bad", item_id="bad", kind="asset"))
        reset_index(services)
        with monkeypatch.context() as patch:

            def no_sealed_read(*args, **kwargs):
                pytest.fail("Pass 1 opened source bytes")

            patch.setattr(backfill, "read_authored_json", no_sealed_read)
            assert backfill.backfill_relationships(services, P, batch_size=2) == 2
        partial = page(services, "000-poison")
        assert partial["coverage"] == {"state": "partial", "reasons": ["invalid_historical_record"]}
        assert [edge["to"]["ref"] for edge in partial["items"]] == [pin("hero")]
        assert page(services, "board-v1")["coverage"]["reasons"] == ["indexing"]

    with services_at(data) as resumed:
        assert backfill.backfill_relationships(resumed, P, batch_size=2) == 2
        assert backfill.backfill_relationships(resumed, P, batch_size=2) == 1
        assert backfill.backfill_relationships(resumed, P, batch_size=2) == 0
        for identity in ("board-v1", "hero-v1"):
            assert page(resumed, identity)["coverage"]["state"] == "complete"
        assert page(resumed, "hero-v1", direction="used_by")["coverage"]["reasons"] == [
            "invalid_historical_record"
        ]


@pytest.mark.parametrize("defect", ["commit", "panel-roles"])
def test_e4_historical_validation_matches_immutable_publication_checks(graph, defect):
    services = graph.services
    version = get_version(services, "first-v1")
    if defect == "commit":
        version["source_commit"] = "0" * 40
    else:
        document = plan("first", scene=pin("landing"))
        document["shots"][0]["frames"].reverse()
        file = write(graph.root, "first.json", document)
        git(graph.root, "add", "first.json")
        git(
            graph.root,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "Invalid historical plan",
        )
        version["source_commit"] = git(graph.root, "rev-parse", "HEAD").decode().strip()
        origin = version["source"]["files"][0]["origin"]
        origin.update(commit=version["source_commit"], sha256=file["sha256"])
        version["source"]["digest"] = closure_digest(version["source"])
    with services.registry.project_store(P).transaction() as tx:
        tx.put(
            "versions",
            "first-v1",
            version,
            expected_revision=tx.revision("versions", "first-v1"),
            schema="VersionRecord",
        )
    backfill.rebuild_relationships(services, P)
    read = page(services, "first-v1")
    assert read["coverage"] == {"state": "partial", "reasons": ["invalid_historical_record"]}
    assert {edge["relation"] for edge in read["items"]} == {"declared_dependency"}
    assert page(services, "first-v1", shot_id="z-first")["items"] == []


def test_e4_missing_local_lfs_preserves_generic_pin_then_repair(tmp_path):
    root = tmp_path / "lfs-project"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "lfs", "track", "first.json")
    file = write(root, "first.json", plan("first", scene=pin("landing")))
    hero = write(root, "hero.txt", b"old coat")
    scene = write(root, "landing.txt", b"Landing")
    with services_at(tmp_path / "machine") as services:
        import_project(
            services,
            OWNER,
            root,
            manifest(
                dict(item("hero", "asset", source(hero)), version_id="hero-v3"),
                item("landing", "scene", source(scene)),
                item("first", "storyboard", source(file, deps=[V3, pin("landing")])),
            ),
        )
        services.registry.activate(P)
        before = index(services)
        record = get_version(services, "first-v1")
        digest = file["sha256"]
        path = root / ".git/lfs/objects" / digest[:2] / digest[2:4] / digest
        sealed = path.read_bytes()
        path.write_bytes(b"corrupt")
        reset_index(services)
        assert page(services, "first-v1", shot_id="a-next")["coverage"]["reasons"] == ["indexing"]
        assert backfill.backfill_relationships(services, P) == 3
        assert page(services, "first-v1")["coverage"]["reasons"] == ["indexing"]
        assert backfill.backfill_relationships(services, P) == 1
        partial = page(services, "first-v1")
        assert partial["coverage"] == {"state": "partial", "reasons": ["source_unavailable"]}
        assert {edge["relation"] for edge in partial["items"]} == {"declared_dependency"}
        missing_shot = page(services, "first-v1", shot_id="a-next")
        assert not missing_shot["items"] and missing_shot["nodes"][0]["binding_status"] == "unknown"
        reverse = page(services, "landing-v1", direction="used_by")
        assert reverse["coverage"] == partial["coverage"]
        assert reverse["items"][0]["relation"] == "declared_dependency"
        path.write_bytes(sealed)
        backfill.rebuild_relationships(services, P)
        assert get_version(services, "first-v1") == record
        assert index(services) == before
        assert page(services, "first-v1")["coverage"]["state"] == "complete"


def test_e4_pre_migration_bundle_is_untouched_and_restore_rebuilds(tmp_path):
    root = tmp_path / "restore-project"
    root.mkdir()
    with services_at(tmp_path / "machine") as services:
        import_project(
            services,
            OWNER,
            root,
            manifest(
                item("quiet", "storyboard", source(write(root, "quiet.json", plan("quiet")))),
            ),
        )
        services.registry.activate(P)
        expected = index(services)
        settings = services.settings

    # Construct genuine v1 databases from the immutable v1 migration and base
    # records, rather than setting a version pragma on a newer schema.
    for db_file in (
        settings.data_directory / "registry.sqlite3",
        root / ".proscenium/state.sqlite3",
    ):
        v1 = db_file.with_suffix(".original-v1.sqlite3")
        with closing(sqlite3.connect(v1)) as db:
            db.executescript(MIGRATIONS[0] + "; PRAGMA user_version=1;")
            db.execute("ATTACH DATABASE ? AS newer", (str(db_file),))
            for table in (
                "records",
                "operations",
                "events",
                "outbox",
                "budget_reservations",
                "resource_claims",
            ):
                db.execute(f"INSERT INTO main.{table} SELECT * FROM newer.{table}")
            db.commit()
        v1.chmod(0o600)
        v1.replace(db_file)

    bundle = tmp_path / "backup"
    assert recovery.backup(settings, P, bundle)["database_revisions"] == {
        "project": 1,
        "registry": 1,
    }

    def hashes():
        return {
            file.relative_to(bundle): recovery.digest(file)
            for file in bundle.rglob("*")
            if file.is_file()
        }

    before = hashes()
    assert recovery.verify(bundle)["database_revisions"] == {"project": 1, "registry": 1}
    assert before == hashes()
    target = tmp_path / "restored"
    report = recovery.restore(bundle, target, rebind=True)
    assert before == hashes()
    for file in (
        Path(report["data_directory"]) / "registry.sqlite3",
        target / "project/.proscenium/state.sqlite3",
    ):
        with closing(sqlite3.connect(file)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        sidecar = file.with_suffix(".pre-v2.sqlite3")
        assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
        with closing(sqlite3.connect(sidecar.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    with services_at(Path(report["data_directory"])) as restored:
        assert index(restored) == expected
        assert page(restored, "quiet-v1")["coverage"]["state"] == "complete"
    assert not (target / "project/.proscenium/snapshots").exists()


@pytest.mark.parametrize("revision", [0, len(MIGRATIONS) + 1])
def test_e4_read_only_recovery_rejects_unsupported_versions(tmp_path, revision):
    file = tmp_path / "foreign.sqlite3"
    with closing(sqlite3.connect(file)) as db:
        db.executescript(MIGRATIONS[0] + f"; PRAGMA user_version={revision};")
    before = file.read_bytes()
    with pytest.raises(recovery.RecoveryError, match="unsupported_or_corrupt_database"):
        recovery.existing_store(file, read_only=True)
    assert file.read_bytes() == before


def test_e5_owner_scope_cursor_binding_and_revision(graph, tmp_path):
    services = graph.services
    other = {"project_id": "second-project", "repository_id": "second-repo"}
    other_root = tmp_path / "second-project"
    other_root.mkdir()
    import_project(
        services,
        OWNER,
        other_root,
        manifest(
            item(
                "first",
                "storyboard",
                source(write(other_root, "plan.json", plan("first", project=other["project_id"]))),
            ),
            identity=other,
        ),
    )
    with browser_for(services) as browser:

        def error(status, code, version="first-v1", *, project=P, headers=None, **params):
            response = browser.get(url(version, project), params=params, headers=headers)
            assert (response.status_code, response.json()["error"]["code"]) == (status, code), (
                response.text
            )
            if "cursor" in params:
                assert params["cursor"] not in response.text
            return response.json()["error"]

        error(401, "owner_session_required")
        tool = services.auth.issue_capability(
            Caller(
                "tool",
                "read-tool",
                P,
                "work-a",
                services.ownership.epoch,
                operations=("publishOutputs",),
                invocation_id="inv-a",
            )
        )
        error(403, "scope_denied", headers={"Authorization": f"Bearer {tool}"})
        pair(services, browser)
        error(403, "scope_denied", project=other["project_id"])
        error(403, "scope_denied", project="not-a-project")
        error(404, "not_found", "not-a-version")
        error(404, "not_found", shot_id="not-a-shot")
        error(400, "invalid_request", "hero-v3", shot_id="z-first")
        error(400, "invalid_request", shot_id="bad id")
        error(400, "invalid_request", direction="up")
        error(400, "invalid_request", limit=101)
        error(400, "invalid_request", limit=0)
        error(400, "invalid_request", limit="bad")
        first = browser.get(url("first-v1"), params={"limit": 1}).json()
        cursor = first["next_cursor"]
        assert cursor and first["items"][0]["to"]["shot_id"] == "z-first"
        second = browser.get(url("first-v1"), params={"cursor": cursor, "limit": 1}).json()
        assert second["items"][0]["to"]["shot_id"] == "a-next"
        error(400, "invalid_cursor", cursor="!!not-a-token!!")
        error(400, "invalid_cursor", cursor=("A" if cursor[0] != "A" else "B") + cursor[1:])
        error(400, "invalid_cursor", "second-v1", cursor=cursor)
        error(400, "invalid_cursor", cursor=cursor, shot_id="z-first")
        error(400, "invalid_cursor", cursor=cursor, direction="used_by")
        wrong_sort = services.cursors.encode(
            encoded(["relationships", SORT_VERSION + 1, P, "uses", version_endpoint(pin("first"))]),
            encoded([first["relationship_revision"], 0, 0, "x"]),
        )
        error(400, "invalid_cursor", cursor=wrong_sort)
        services.registry.activate(other["project_id"])
        error(400, "invalid_cursor", project=other["project_id"], cursor=cursor)
        services.registry.activate(P)
        publish_hero4(services)
        stale = error(409, "relationship_revision_changed", cursor=cursor)
        assert "Restart at page one" in stale["message"]


def test_e5_expired_owner_cookie_is_not_refreshed(graph, monkeypatch):
    services = graph.services
    monkeypatch.setattr(services.auth, "SESSION_TTL", -1)
    with browser_for(services) as browser:
        pair(services, browser)
        response = browser.get(url("first-v1"))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "owner_session_required"


def test_sequence_contributor_shot_identity_survives_both_cursor_directions(graph):
    services = graph.services
    template = get_version(services, "stairs-v1")
    version = dict(template, kind="sequence", item_id="edit", version_id="edit-v1", derived_from=[])
    frames = {"start": 1, "end": 20}
    first = dict(shot_id="unscoped-a", version_id="hero-v3", job_id=None, source_frames=frames)
    second = dict(first, shot_id="unscoped-b")
    entry = dict(
        first,
        shot_id=None,
        entry_id="entry-a",
        job_id="job-1",
        sequence_frames=frames,
        contributors=[first, second],
    )
    with services.registry.project_store(P).transaction() as tx:
        tx.put("versions", "edit-v1", version, schema="VersionRecord")
        apply_version_projection(tx, project_version_relationships(version, dict(entries=[entry])))
    for root, direction in [("edit-v1", "uses"), ("hero-v3", "used_by")]:
        cursors = None
        occurrences = []
        page_i = 0
        while True:
            result = page(services, root, direction=direction, limit=1, cursor=cursors)
            assert len(result["items"]) == 1
            edge = result["items"][0]
            if edge["relation"] == "sequence_input" and edge["details"]["role"] == "contributor":
                occurrences.append((page_i, edge["details"]))
            page_i += 1
            cursors = result["next_cursor"]
            if cursors is None:
                break
        assert len(occurrences) == 2 and occurrences[0][0] != occurrences[1][0]
        details = [copy.copy(d) for _, d in occurrences]
        assert {d.pop("shot_id") for d in details} == {"unscoped-a", "unscoped-b"}
        assert details[0] == details[1]


def test_sequence_roles_and_typed_occurrences(graph):
    services = graph.services
    base = get_version(services, "stairs-v1")
    version = dict(base, kind="sequence", item_id="edit", version_id="edit-v1")
    frames = {"start": 1, "end": 20}
    contributor = dict(
        shot_id="unscoped-shot", version_id="hero-v3", job_id="job-1", source_frames=frames
    )
    entry = dict(
        entry_id="entry-a",
        shot_id=None,
        version_id="hero-v3",
        job_id="job-1",
        source_frames=frames,
        sequence_frames=frames,
        contributors=[contributor, copy.deepcopy(contributor), dict(contributor, job_id="job-2")],
    )
    projection = project_version_relationships(
        version, dict(entries=[entry, dict(entry, entry_id="entry-b")])
    )
    kinds = [(edge[-4], json.loads(edge[-1])) for edge in projection.edges]
    assert sum(relation == "sequence_input" for relation, _ in kinds) == 6
    assert sum(relation == "derived_from" for relation, _ in kinds) == 1
    assert not any(relation == "declared_dependency" for relation, _ in kinds)
    clip = dict(
        entry_id="clip-1", clip_artifact_id="video-1", source_frames=frames, sequence_frames=frames
    )
    audio = dict(track_id="music-1", artifact_id="audio-1", start_seconds=0.25)
    version = dict(version, source=dict(version["source"], dependencies=[]), derived_from=[])
    with services.registry.project_store(P).transaction() as tx:
        tx.put("versions", version["version_id"], version, schema="VersionRecord")
        tx.put(
            "artifacts",
            "video-1",
            dict(
                artifact_id="video-1", project_id=P, kind="video", purpose="final", state="corrupt"
            ),
        )
        apply_version_projection(
            tx, project_version_relationships(version, dict(entries=[clip], audio_tracks=[audio]))
        )
    read = page(services, "edit-v1")
    assert [edge["details"]["role"] for edge in read["items"]] == ["clip", "audio"]
    assert [node["state"] for node in read["nodes"] if node["kind"] == "artifact"] == [
        "corrupt",
        "missing",
    ]
    with pytest.raises(DomainError, match="Invalid RelationshipEdge"):
        validate(
            "RelationshipEdge",
            dict(read["items"][0], details={**read["items"][0]["details"], "job_id": "x"}),
        )


@contextmanager
def measured_relationship_pages(data, monkeypatch):
    # Fresh Services after seeding closes the writer and resets application state.
    # SQLite connections are request scoped; this does not flush the OS page cache.
    with services_at(data) as services, browser_for(services) as browser:
        pair(services, browser)
        sql, payloads, opens = [], [], []
        connect = Store.connect

        def traced(store, **kwargs):
            db = connect(store, **kwargs)
            opens.append(store.path)
            db.set_trace_callback(sql.append)

            def measure(cursor, row):
                payloads.append(
                    sum(
                        len(v.encode("utf-8"))
                        if isinstance(v, str)
                        else len(v)
                        if isinstance(v, bytes)
                        else 0
                        if v is None
                        else 8
                        for v in row
                    )
                )
                return sqlite3.Row(cursor, row)

            db.row_factory = measure
            return db

        def request(root, direction, cursor=None, *, latest_per_item):
            sql.clear()
            payloads.clear()
            opens.clear()
            start = perf_counter()
            response = browser.get(
                url(root),
                params={
                    "direction": direction,
                    "latest_per_item": latest_per_item,
                    "limit": 50,
                    **({"cursor": cursor} if cursor else {}),
                },
            )
            ms = (perf_counter() - start) * 1000
            assert response.status_code == 200, response.text
            queries = [stmt for stmt in sql if stmt.startswith("SELECT")]
            assert len(queries) <= 16 and len(opens) <= 4 and len(payloads) <= 220
            edges = [stmt for stmt in queries if "FROM relationship_edges" in stmt]
            assert len(edges) == 1 and edges[0].count("FROM relationship_edges") == 1
            assert edges[0].endswith("LIMIT 51")
            return response.json(), {
                "ms": round(ms, 3),
                "selects": len(queries),
                "opens": len(opens),
                "sqlite_rows": len(payloads),
                "sqlite_returned_bytes": sum(payloads),
                "http_bytes": len(response.content),
            }

        with monkeypatch.context() as patch:
            patch.setattr(Store, "connect", traced)
            yield request


def open_performance_project(services, root):
    root.mkdir()
    Projects(services).open(
        OWNER,
        dict(
            schema_version="1.0",
            path=str(root),
            create=True,
            name="Relationship performance",
            operation_id="open-relationship-perf",
        ),
        identity=IDENTITY,
        install_lfs_hooks=False,
    )
    services.registry.activate(P)


@pytest.mark.parametrize("latest_per_item", [False, True])
def test_studio_shaped_relationship_performance(graph, tmp_path, monkeypatch, latest_per_item):
    original = get_version(graph.services, "hero-v3")
    data = tmp_path / "studio-registry"
    with services_at(data) as services:
        open_performance_project(services, tmp_path / "studio-project")
        with services.registry.project_store(P).transaction() as tx:
            for i in range(40):
                kind = "storyboard" if i == 39 else "scene" if i >= 29 else "asset"
                item_id = f"studio-{i:02}"
                tx.put(
                    "items",
                    item_id,
                    dict(project_id=P, item_id=item_id, current_version_id=f"{item_id}-v2"),
                )
                for revision in (1, 2):
                    ref = pin(item_id, f"{item_id}-v{revision}")
                    deps, doc = [], None
                    if kind == "scene":
                        deps = [pin(f"studio-{j:02}") for j in range(4)]
                    elif kind == "storyboard":
                        doc = plan(item_id, scene=pin("studio-29"))
                        doc["asset_dependencies"] = [pin("studio-00")]
                        deps = doc["asset_dependencies"] + [pin("studio-29")]
                    version = dict(
                        original,
                        **ref,
                        kind=kind,
                        source=dict(original["source"], dependencies=deps),
                    )
                    tx.put("versions", ref["version_id"], version)
                    apply_version_projection(tx, project_version_relationships(version, doc))
            assert len(tx.all("items")) == 40 and len(tx.all("versions")) == 80
    with measured_relationship_pages(data, monkeypatch) as request:
        cold, cold_stats = request("studio-29-v1", "used_by", latest_per_item=latest_per_item)
        warm, warm_stats = request("studio-29-v1", "used_by", latest_per_item=latest_per_item)
        assert cold == warm and cold["next_cursor"] is None
        assert [edge["from"]["board"]["version_id"] for edge in cold["items"]] == (
            ["studio-39-v2"] if latest_per_item else ["studio-39-v1", "studio-39-v2"]
        )
        assert {n["item_kind"] for n in cold["nodes"] if n["kind"] == "version"} == {
            "storyboard",
            "scene",
        }
        print(
            f"Studio 40 items / 80 versions ({latest_per_item=}), one reverse page: cold={cold_stats}, warm={warm_stats}"
        )


@pytest.mark.parametrize("latest_per_item", [False, True])
def test_relationship_keyset_performance(graph, tmp_path, monkeypatch, latest_per_item):
    original = get_version(graph.services, "hero-v3")
    data = tmp_path / "synthetic-registry"
    refs = [pin(f"perf-{i:04d}") for i in range(1000)]
    with services_at(data) as services:
        open_performance_project(services, tmp_path / "synthetic-project")
        seed_keyset_performance(services, original, refs)
    with measured_relationship_pages(data, monkeypatch) as request:
        root = refs[0]["version_id"]
        cold, cold_stats = request(root, "used_by", latest_per_item=latest_per_item)
        warm, warm_stats = request(root, "used_by", latest_per_item=latest_per_item)
        assert cold == warm
        seen = []
        response = cold
        requests = 1
        while True:
            assert response["relationship_revision"] == cold["relationship_revision"]
            assert response["coverage"] == {"state": "complete", "reasons": []}
            assert len(response["items"]) <= 50 and len(response["nodes"]) <= 51
            seen.extend(edge["from"]["ref"]["version_id"] for edge in response["items"])
            if not response["next_cursor"]:
                break
            requests += 1
            response, _ = request(
                root, "used_by", response["next_cursor"], latest_per_item=latest_per_item
            )
        expected = {ref["version_id"] for ref in refs[1:286]}
        assert len(seen) == len(set(seen)) == len(expected) and set(seen) == expected
        assert requests == 6
        print(
            f"Synthetic 1,000 versions / 10,000 edges ({latest_per_item=}), {requests} reverse pages: cold={cold_stats}, warm={warm_stats}"
        )


def seed_keyset_performance(services, original, refs):
    with services.registry.project_store(P).transaction() as tx:
        for i, ref in enumerate(refs):
            dependencies = list(refs[max(0, i - 10) : i])
            if 1 <= i <= 285 and refs[0] not in dependencies:
                dependencies[0] = refs[0]
            if 11 <= i <= 65:
                dependencies.append(next(r for r in refs[1:i] if r not in dependencies))
            version = dict(
                original,
                **ref,
                kind="asset",
                name=ref["item_id"],
                source=dict(original["source"], dependencies=dependencies),
            )
            tx.put("versions", ref["version_id"], version)
            apply_version_projection(tx, project_version_relationships(version))
        assert (
            tx.connection.execute("SELECT COUNT(*) FROM relationship_edges").fetchone()[0] == 10000
        )
        for direction, index_name in [
            ("uses", "relationship_outgoing"),
            ("used_by", "relationship_incoming"),
        ]:
            plan = [
                row[3]
                for row in tx.connection.execute(
                    "EXPLAIN QUERY PLAN " + edge_query(direction),
                    ("version", refs[0]["version_id"], "", 0, 0, "x", 51),
                )
            ]
            print(f"{direction} EXPLAIN {plan}")
            assert any(
                f"SEARCH relationship_edges USING INDEX {index_name}" in line for line in plan
            )
            assert not any(
                "SCAN relationship_edges" in line or "SCAN records" in line for line in plan
            )


def test_offline_import_preserves_single_commit_and_rejects_multi_commit(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    producer_file = write(root, "hero.txt", b"Hero")
    consumer_file = write(root, "landing.txt", b"Landing")
    with services_at(tmp_path / "registry") as services:
        info = manifest(
            {**item("hero", "asset", source(producer_file)), **V3},
            {
                **item(
                    "landing",
                    "scene",
                    {**source(consumer_file, producer_file, deps=[V3]), "included_sources": [V3]},
                ),
                **LANDING,
            },
        )
        import_project(services, OWNER, root, info)
        services.registry.activate(P)
        edges = page(services, LANDING["version_id"])["items"]
        assert [edge["relation"] for edge in edges] == ["includes_source"]
        backfill.rebuild_relationships(services, P)
        assert page(services, LANDING["version_id"])["items"] == edges

        other_file = write(root, "stairwell.txt", b"Would acquire a new commit")
        new = manifest(
            item(
                "stairwell",
                "scene",
                {
                    **source(other_file, producer_file, deps=[V3]),
                    "included_sources": [V3],
                },
            ),
            name="multi-commit-refused",
        )
        refs = git(root, "show-ref")
        with pytest.raises(DomainError, match="Offline import cannot represent multi-commit"):
            import_project(services, OWNER, root, new)
        assert git(root, "show-ref") == refs
        with services.registry.project_store(P).transaction(write=False) as tx:
            assert tx.get("versions", "stairwell-v1", required=False) is None


@pytest.mark.parametrize(
    "fault", ["missing_file", "missing_pin", "missing_version", "changed_file"]
)
def test_offline_import_rejects_bad_inclusion_before_installing_refs(tmp_path, fault):
    root = tmp_path / "project"
    root.mkdir()
    producer_file = write(root, "hero.txt", b"Hero")
    consumer_file = write(root, "landing.txt", b"Landing")
    included = pin("missing") if fault == "missing_version" else V3
    rows = [consumer_file]
    if fault != "missing_file":
        rows.append(
            {**producer_file, "sha256": sha(b"Wrong bytes")}
            if fault == "changed_file"
            else producer_file
        )
    manifest_body = manifest(
        {**item("hero", "asset", source(producer_file)), **V3},
        {
            **item(
                "landing",
                "scene",
                {
                    **source(*rows, deps=[] if fault == "missing_pin" else [included]),
                    "included_sources": [included],
                },
            ),
            **LANDING,
        },
    )
    with services_at(tmp_path / "registry") as services:
        with pytest.raises(DomainError) as error:
            import_project(services, OWNER, root, manifest_body)
        assert error.value.code == "dependency_missing"
        assert (
            git(
                root,
                "for-each-ref",
                "refs/proscenium/checkpoints/",
                "refs/proscenium/import-sources/",
            )
            == b""
        )


@pytest.mark.parametrize(
    "fault, code, message",
    [
        ("cycle", "source_conflict", "Dependency cycle"),
        ("byte_limit", "invalid_request", "exceeds 512 MiB"),
    ],
)
def test_offline_import_cannot_pin_refs_before_full_admission(
    tmp_path, monkeypatch, fault, code, message
):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    producer_file = write(root, "hero.txt", b"Hero")
    scene_file = write(root, "landing.txt", b"Landing")
    producer = {**item("hero", "asset", source(producer_file)), **V3}
    if fault == "cycle":
        producer["derived_from"] = [LANDING]
    else:
        # Both individual files fit. The sealed composition does not.
        monkeypatch.setattr(composition, "MAX_BYTES", 8)
        read_bytes = Path.read_bytes

        def guarded_read(path):
            if path.is_relative_to(root):
                pytest.fail(f"Composed admission must precede whole-file reads: {path.name}")
            return read_bytes(path)

        def no_prepare(*args, **kwargs):
            pytest.fail("Composed admission must precede Git object writes")

        monkeypatch.setattr(Path, "read_bytes", guarded_read)
        monkeypatch.setattr(Checkpoints, "prepare", no_prepare)
    scene = {
        **item(
            "landing",
            "scene",
            {
                **source(scene_file, producer_file, deps=[V3]),
                "included_sources": [V3],
            },
        ),
        **LANDING,
    }
    refs = git(root, "for-each-ref")
    with services_at(tmp_path / "registry") as services:
        with pytest.raises(DomainError, match=message) as error:
            import_project(services, OWNER, root, manifest(producer, scene))
        assert error.value.code == code
        assert git(root, "for-each-ref") == refs
        with services.registry.project_store(P, active=False).transaction(write=False) as tx:
            assert tx.all("versions") == []


def test_offline_import_checks_declared_git_lfs_size_before_any_new_checkpoint(
    tmp_path, monkeypatch
):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Offline fixture")
    git(root, "config", "user.email", "fixture@proscenium.invalid")
    payload_hash = sha(b"Hero payload")
    # Deliberately omit the LFS object. Aggregate admission must fail from
    # declared sizes before it attempts to hydrate or hash anything.
    producer_file = write(
        root,
        "hero.txt",
        (
            f"version https://git-lfs.github.com/spec/v1\noid sha256:{payload_hash}\nsize 12\n"
        ).encode(),
    )
    producer_file["sha256"] = payload_hash
    scene_file = write(root, "landing.txt", b"Landing")
    git(root, "add", "--", "hero.txt", "landing.txt")
    git(root, "commit", "-m", "Pinned Git and LFS metadata")
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    body = manifest(
        {**item("hero", "asset", source(producer_file, commit=commit)), **V3},
        {
            **item(
                "landing",
                "scene",
                {
                    **source(scene_file, producer_file, deps=[V3], commit=commit),
                    "included_sources": [V3],
                },
            ),
            **LANDING,
        },
        item("unrelated", "asset", source(write(root, "unrelated.txt", b"Working changes"))),
    )
    monkeypatch.setattr(composition, "MAX_BYTES", 16)

    def no_io(*args, **kwargs):
        pytest.fail("Composed admission must precede hydration and writes")

    monkeypatch.setattr(Resolver, "lfs_object", no_io)
    monkeypatch.setattr(Checkpoints, "prepare", no_io)
    refs = git(root, "for-each-ref")
    with services_at(tmp_path / "registry") as services:
        with pytest.raises(DomainError, match="exceeds 512 MiB") as error:
            import_project(services, OWNER, root, body)
    assert error.value.code == "invalid_request"
    assert git(root, "for-each-ref") == refs


def wardrobe_sequence(services, store, version):
    # Persist a sealed observation. No encoder or player runs in this fixture.
    clip = b"Offline clip fixture"
    digest = sha(clip)
    clip_path = services.extension("artifacts").path(P, digest)
    clip_path.parent.mkdir(parents=True)
    clip_path.write_bytes(clip)
    with store.transaction() as tx:
        services.extension("artifacts").put(
            tx,
            dict(
                schema_version="1.0",
                artifact_id="landing-edit",
                project_id=P,
                kind="video",
                purpose="final",
                sha256=digest,
                bytes=len(clip),
                media_type="video/mp4",
                state="ready",
                derived_from=[],
                source_version_id=LANDING["version_id"],
                dimensions={"width": 16, "height": 16},
                fps=FPS,
                frame_count=24,
            ),
        )
    frames = {"start": 1, "end": 24}
    return dict(
        schema_version="1.0",
        project_id=P,
        sequence_version_id=version,
        fps=FPS,
        frame_count=24,
        silent=True,
        entries=[
            dict(
                entry_id="landing-edit",
                shot_id=None,
                clip_artifact_id="landing-edit",
                source_frames=frames,
                sequence_frames=frames,
                transform="identity",
                mapping_artifact_id=None,
                include_audio=False,
                choice="agent",
            )
        ],
    )


def test_wardrobe_revision_and_restore_keep_both_outliner_histories(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    stairwell2, stairwell3 = pin("stairwell", "stairwell-v2"), pin("stairwell", "stairwell-v3")
    board1, board2, seq1 = pin("escape"), pin("escape", "escape-v2"), pin("sequence")
    coat4 = "assets/hero/source/coat_v4.txt"
    stairwell_blend = "scenes/stairwell/stairwell.blend"
    plan_path, sequence_path = "storyboards/escape/plan.json", "sequences/escape/cut.json"
    retained_name = "assets/walk/source/timing.motion"
    with services_at(tmp_path / "registry") as services:
        starter = item("starter", "asset", source(write(root, "starter.txt", b"Bootstrap")))
        import_project(services, OWNER, root, manifest(starter))
        services.registry.activate(P)
        git(root, "config", "user.name", "Wardrobe test")
        git(root, "config", "user.email", "fixture@proscenium.invalid")
        caller = admitted_tool(services)
        caller = dataclasses.replace(caller, operations=(*caller.operations, "checkpoint_sources"))
        r = SimpleNamespace(
            root=root,
            store=services.registry.project_store(P),
            services=services,
            caller=caller,
            sources=services.extension("sources"),
            catalogue=services.extension("catalogue"),
        )
        producer(r, V3, {HERO_BLEND: b"LFS v3", COAT3: b"Coat v3"}, name="Hero v3")
        retained = retain(r, "retained-motion", b"Retained motion source")
        producer(
            r,
            WALK,
            {WALK_BLEND: b"Walk"},
            retained_files=[dict(destination=retained_name, origin=retained)],
        )
        producer(r, STAIRS, {STAIRS_BLEND: b"Staircase"})
        for ref, entry, includes in [
            (LANDING, LANDING_BLEND, [V3, WALK]),
            (stairwell2, stairwell_blend, [V3, WALK, STAIRS]),
        ]:
            write(root, entry, ref["version_id"].encode())
            name = "Landing v2" if ref == LANDING else "Stairwell v2"
            publish(
                r,
                ref,
                checkpoint(r, ref, [entry], include_sources=includes),
                kind="scene",
                name=name,
            )

        board = plan("escape", scene=LANDING)
        board["shots"][1].update(
            scene_version=stairwell2,
            source_range={"start": 1, "end": 24},
            fps=FPS,
            planned_assets=[V3],
        )
        write(root, plan_path, board)
        publish(
            r,
            board1,
            checkpoint(r, board1, [plan_path], dependencies=[V3, LANDING, stairwell2]),
            kind="storyboard",
        )
        sequence = wardrobe_sequence(services, r.store, seq1["version_id"])
        write(root, sequence_path, sequence)
        publish(
            r,
            seq1,
            checkpoint(r, seq1, [sequence_path], dependencies=[V3, LANDING]),
            kind="sequence",
        )

        (root / COAT3).unlink()
        git(root, "add", "-u", COAT3)
        git(root, "commit", "-m", "Remove the old coat from the working revision")
        write(root, HERO_BLEND, b"LFS v4")
        write(root, coat4, b"Coat v4")
        new = checkpoint(r, V4, [HERO_BLEND, coat4], base_version=V3, exclude_paths=[COAT3])
        publish(r, V4, new, previous=V3["version_id"], name="Hero v4")
        timing = "scenes/stairwell/timing.txt"
        write(root, timing, b"Stairwell-specific timing revision")
        adopted = checkpoint(
            r,
            stairwell3,
            [timing],
            entry=stairwell_blend,
            base_version=stairwell2,
            include_sources=[STAIRS, V4, WALK],
        )
        publish(
            r,
            stairwell3,
            adopted,
            kind="scene",
            previous=stairwell2["version_id"],
            name="Stairwell v3",
        )
        revised = copy.deepcopy(board)
        revised["shots"][1].update(scene_version=stairwell3, planned_assets=[V4])
        write(root, plan_path, revised)
        publish(
            r,
            board2,
            checkpoint(r, board2, [plan_path], dependencies=[V3, V4, LANDING, stairwell3]),
            kind="storyboard",
            previous=board1["version_id"],
        )

        expected = index(services)
        backfill.rebuild_relationships(services, P)
        assert index(services) == expected
        old_edges = page(services, V3["version_id"], direction="used_by")["items"]
        new_edges = page(services, V4["version_id"], direction="used_by")["items"]

        def consumers(edges, relation):
            return {
                edge["from"]["ref"]["version_id"] for edge in edges if edge["relation"] == relation
            }

        old_scenes = {LANDING["version_id"], stairwell2["version_id"]}
        assert consumers(old_edges, "includes_source") == old_scenes
        assert consumers(new_edges, "includes_source") == {stairwell3["version_id"]}
        assert consumers(old_edges, "declared_dependency").isdisjoint(old_scenes)
        assert get_version(services, seq1["version_id"])["source"]["dependencies"] == [V3, LANDING]
        preserved = {
            ref["version_id"]: get_version(services, ref["version_id"])["source"]
            for ref in (V3, V4, LANDING, stairwell2, stairwell3, board1, board2, seq1)
        }
        with r.store.transaction() as tx:
            for ns, identity in (("work", caller.work_id), ("invocations", caller.invocation_id)):
                tx.put(
                    ns,
                    identity,
                    dict(tx.require(ns, identity), state="completed"),
                    expected_revision=tx.revision(ns, identity),
                )
        # Archive an inert fixture history to run the ordinary complete backup path.
        write(
            services.settings.data_directory,
            "copilot-runtime/home/session-state/test-session/events.jsonl",
            b"{}\n",
        )
        settings = services.settings

    (root / WALK_BLEND).unlink()
    bundle, target = tmp_path / "backup", tmp_path / "restored"
    assert recovery.backup(settings, P, bundle)["limits"] == []
    assert recovery.verify(bundle)["limits"] == []
    report = recovery.restore(bundle, target, rebind=True)
    with services_at(target / "machine") as restored:
        assert index(restored) == expected
        assert {
            identity: get_version(restored, identity)["source"] for identity in preserved
        } == preserved
        assert page(restored, V3["version_id"], direction="used_by")["items"] == old_edges
        assert page(restored, V4["version_id"], direction="used_by")["items"] == new_edges
        with browser_for(restored) as browser:
            pair(restored, browser)
            pages = {}
            for ref, edges in ((V3, old_edges), (V4, new_edges)):
                identity = ref["version_id"]
                pages[identity] = {}
                for direction in ("uses", "used_by"):
                    response = browser.get(url(identity), params={"direction": direction})
                    assert response.status_code == 200
                    pages[identity][direction] = response.json()
                assert pages[identity]["used_by"]["items"] == edges
        if export := os.environ.get("PROSCENIUM_E9_RELATIONSHIPS_FIXTURE"):
            Path(export).write_text(
                json.dumps(
                    {
                        "pages": pages,
                        "versions": [get_version(restored, identity) for identity in preserved],
                    }
                )
            )
        restored_root = Path(report["path"])
        assert (restored_root / HERO_BLEND).read_bytes() == b"LFS v4"
        assert not (restored_root / WALK_BLEND).exists()
        restored_sources = restored.extension("sources")
        landing = restored_sources.materialise(P, LANDING["version_id"])
        stairs = restored_sources.materialise(P, stairwell3["version_id"])
        assert (landing / HERO_BLEND).read_bytes() == b"LFS v3"
        assert (landing / COAT3).read_bytes() == b"Coat v3"
        assert not (landing / coat4).exists()
        assert (stairs / HERO_BLEND).read_bytes() == b"LFS v4"
        assert (stairs / coat4).read_bytes() == b"Coat v4"
        assert not (stairs / COAT3).exists()
        assert (
            (stairs / retained_name).read_bytes()
            == (landing / retained_name).read_bytes()
            == b"Retained motion source"
        )
        board_snapshot = restored_sources.materialise(P, board1["version_id"])
        seq_snapshot = restored_sources.materialise(P, seq1["version_id"])
        assert json.loads((board_snapshot / plan_path).read_bytes()) == board
        assert {
            p.relative_to(board_snapshot).as_posix()
            for p in board_snapshot.rglob("*")
            if p.is_file()
        } == {plan_path, ".receipt.json"}
        assert json.loads((seq_snapshot / sequence_path).read_bytes()) == sequence
        assert restored.registry.get(P)["path"] == report["path"]
