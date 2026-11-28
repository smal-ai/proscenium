"""Offline query, validation and wake-up regressions for performance Wave 2."""

import asyncio
import json
import os
import shutil
import sqlite3
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Barrier, Lock, get_ident
from types import SimpleNamespace

import pytest
from proscenium import auth as auth_module
from proscenium import contracts
from proscenium.api.errors import DomainError
from proscenium.api.lifecycle import FeatureLifecycle
from proscenium.auth import COOKIE_NAME, Auth, digest
from proscenium.catalogue.validation import _issue_validator, sequence_map
from proscenium.contracts import _validator, canonical, validate
from proscenium.copilot import outcomes
from proscenium.db.store import MIGRATIONS, Store
from test_performance_wave1 import project as project
from test_performance_wave1 import transport as transport

ACTOR = {"kind": "system", "id": "wave-two-test"}
SHA256 = "a" * 64
COMMIT = "b" * 40


def version_record(project_id, ref):
    return dict(
        schema_version="1.0",
        project_id=project_id,
        **ref,
        kind="scene",
        name=ref["item_id"],
        publication_id="publication",
        source_commit=COMMIT,
        source=dict(
            entry_path="scene.blend",
            files=[
                dict(
                    destination="scene.blend",
                    origin=dict(
                        kind="git",
                        repository_id="repository",
                        commit=COMMIT,
                        path="scene.blend",
                        sha256=SHA256,
                    ),
                )
            ],
            dependencies=[],
            required_runtime=dict(application="Blender", version="4.5", features=[]),
            digest=SHA256,
        ),
        artifact_ids=[],
        preview_requests=[],
        preview_job_ids=[],
        created_at="2026-11-25T10:00:00Z",
        actor=ACTOR,
    )


def job_record(project_id, job_id, ref, shot=None, spec=None):
    return dict(
        schema_version="1.0",
        project_id=project_id,
        job_id=job_id,
        kind="render",
        state="succeeded",
        state_revision=0,
        target=dict(target_id=job_id, version=ref, shot_id=shot),
        artifact_ids=[],
        work_id=None,
        owner_operation_id="admission",
        attempt_id=f"attempt-{job_id}",
        attempt_number=1,
        availability="complete",
        input_digest=SHA256,
        external_job_id=None,
        provider_id=None,
        owner_epoch="test-epoch",
        completed_frames=None,
        expected_frames=None,
        render_spec=spec,
    )


def test_owner_read_throttle_and_concurrent_refresh(monkeypatch, tmp_path):
    clock = SimpleNamespace(time=lambda: 1000.0)
    monkeypatch.setattr(auth_module, "time", clock)
    store = Store(tmp_path / "registry.sqlite3")
    auth = Auth(store)
    cookie = "owner-cookie"
    identity = digest(cookie)
    row = dict(owner_session_id="owner", csrf_token="token", expires=1000 + Auth.SESSION_TTL)
    with store.transaction() as tx:
        tx.put("auth.sessions", identity, row)

    def persisted():
        with store.transaction(write=False) as tx:
            return tx.revision("auth.sessions", identity), tx.require("auth.sessions", identity)

    original = store.transaction
    writes = []

    @contextmanager
    def record_writes(*, write=True):
        if write:
            writes.append(True)
        with original(write=write) as tx:
            yield tx

    clock.time = lambda: 1060.0  # The threshold is strict.
    with monkeypatch.context() as scoped:
        scoped.setattr(store, "transaction", record_writes)
        for _ in range(3):
            assert auth._owner(cookie, "", False).subject_id == "owner"
        assert not writes
    assert persisted() == (0, row)

    clock.time = lambda: 1061.0
    overlap = Barrier(2, timeout=5)

    @contextmanager
    def overlap_reads(*, write=True):
        with original(write=write) as tx:
            yield tx
        if not write:
            overlap.wait()  # Both callers observed the old expiry.

    with monkeypatch.context() as scoped, ThreadPoolExecutor(2) as executor:
        scoped.setattr(store, "transaction", overlap_reads)
        assert [
            c.subject_id for c in executor.map(lambda _: auth._owner(cookie, "", False), range(2))
        ] == ["owner", "owner"]
    assert persisted() == (1, {**row, "expires": 1061 + Auth.SESSION_TTL})
    clock.time = lambda: 1122.0
    with pytest.raises(DomainError) as error:
        auth._owner(cookie, "bad", True)
    assert (error.value.status, error.value.code) == (403, "permission_denied")
    assert persisted()[0] == 1
    clock.time = lambda: 1061 + Auth.SESSION_TTL
    with pytest.raises(DomainError) as error:
        auth._owner(cookie, "token", True)
    assert (error.value.status, error.value.code) == (401, "owner_session_required")


def test_batched_read_routes_keep_order_and_query_count(project, transport, monkeypatch):
    _, record, base, services = project
    project_id = record["project_id"]
    store = services.registry.project_store(project_id)

    def seed(start, end):
        with store.transaction() as tx:
            for i in range(start, end):
                item_id, version_id = f"item-{i:03}", f"version-{i:03}"
                tx.put(
                    "items",
                    item_id,
                    dict(
                        schema_version="1.0",
                        project_id=project_id,
                        item_id=item_id,
                        kind="scene",
                        name=item_id,
                        current_version_id=version_id,
                        selected_version_id=None,
                        state_revision=0,
                    ),
                    schema="ItemRecord",
                )
                tx.put("selections", item_id, {"reserved": True})
                tx.put(
                    "selections",
                    item_id,
                    dict(
                        schema_version="1.0",
                        project_id=project_id,
                        selection_id=item_id,
                        state_revision=1,
                        actor=ACTOR,
                        slot=dict(kind="item", item_id=item_id),
                        state="selected",
                        choice=dict(
                            kind="version", version=dict(item_id=item_id, version_id=version_id)
                        ),
                    ),
                    expected_revision=0,
                    schema="SelectionRecord",
                )
                for item, version in ((item_id, version_id), ("item-000", f"extra-{i:03}")):
                    ref = dict(item_id=item, version_id=version)
                    tx.put(
                        "versions", version, version_record(project_id, ref), schema="VersionRecord"
                    )
                    tx.put("version_links", version, {"artifact_ids": [version]})
                    for shot in ("shot10", "shot2"):
                        job_id = f"render-{version}-{shot}"
                        tx.put(
                            "jobs",
                            job_id,
                            job_record(project_id, job_id, ref, shot),
                            schema="JobRecord",
                        )
                # ID order is the reverse of durable creation order.
                review_id = f"review-{999 - i:03}"
                ticket = dict(
                    schema_version="1.0",
                    review_id=review_id,
                    job_id=f"render-{version_id}-shot2",
                    status="ready",
                )
                tx.put("reviews", review_id, ticket, schema="ReviewTicket")
                tx.put(
                    "findings",
                    review_id,
                    dict(
                        schema_version="1.0",
                        project_id=project_id,
                        finding_id=review_id,
                        review_id=review_id,
                        state="unreviewed",
                        revision=3,
                        observation="Test",
                        recommendation="Test",
                        evidence=[],
                        target=dict(
                            origin="reference",
                            artifact_id="reference",
                            displayed_sha256=SHA256,
                            dimensions=dict(width=16, height=16),
                            item_id=None,
                            version_id=None,
                            shot_id=None,
                            render_job_id=None,
                            source_frame=None,
                            media_artifact_id=None,
                        ),
                    ),
                    schema="ReviewFinding",
                )
                tx.emit("review.ready", ticket, actor=ACTOR)
                if i % 2:
                    tx.put(
                        "review.meta",
                        review_id,
                        dict(title="Owner review", revision=7, human_requested=False),
                    )

    seed(0, 1)
    sql = []
    connect = Store.connect

    def trace(self, **kwargs):
        connection = connect(self, **kwargs)
        connection.set_trace_callback(sql.append)
        return connection

    monkeypatch.setattr(Store, "connect", trace)
    routes = ["/items", "/items/item-000/versions", "/reviews", "/snapshot"]

    def read(path):
        sql.clear()
        response = transport.get(base + path)
        assert response.status_code == 200, response.text
        return response.json(), sum(s.lstrip().startswith("SELECT") for s in sql)

    small = [read(path)[1] for path in routes]
    seed(1, 31)
    (items, *pages), counts = zip(*(read(path) for path in routes), strict=True)
    assert list(counts) == small
    versions, reviews, snapshot = pages
    assert [item["selected_version_id"] for item in items["items"]] == [
        f"version-{i:03}" for i in range(31)
    ]
    assert snapshot["items"] == items["items"]
    assert snapshot["reviews"] == reviews["items"]
    for schema, payload in (
        ("ItemPage", items),
        ("VersionPage", versions),
        ("ProjectSnapshot", snapshot),
        ("ReviewPage", reviews),
        ("JobPage", read("/jobs")[0]),
    ):
        validate(schema, payload)
    assert [r["review_id"] for r in reviews["items"]] == [
        f"review-{i:03}" for i in range(969, 1000)
    ]
    with store.transaction(write=False) as tx:
        legacy = tx.require("reviews", "review-999")
        expected = services.extension("reviews").ticket_view(tx, legacy)
        lookup = services.extension("catalogue")
        assert versions["items"] == [
            lookup.version_view(tx, v) for v in tx.all("versions") if v["item_id"] == "item-000"
        ]
    assert reviews["items"][-1] == expected
    assert expected["revision"] == 3 and expected["created_at"] and expected["awaiting_human"]
    assert versions["items"][0]["preview_job_ids"] == [
        "render-extra-000-shot2",
        "render-extra-000-shot10",
    ]
    first, _ = read("/reviews?filter=pending&limit=1")
    second, _ = read(f"/reviews?filter=pending&limit=1&cursor={first['next_cursor']}")
    assert [p["items"][0]["review_id"] for p in (first, second)] == ["review-969", "review-971"]
    with store.transaction() as tx:
        tx.connection.execute(
            "INSERT INTO records SELECT namespace, 'duplicate', revision, body FROM records "
            "WHERE namespace='selections' AND id='item-000'"
        )
    response = transport.get(base + "/items")
    assert response.status_code == 409 and response.json()["error"]["code"] == "source_conflict"
    sql.clear()
    response = transport.get(base + "/reviews/missing/findings")
    assert response.status_code == 404 and not any("namespace='findings'" in s for s in sql)


def test_sequence_frame_map_caches_records_but_rechecks_each_frame(tmp_path):
    store = Store(tmp_path / "project.sqlite3", "project")
    fps = dict(numerator=24, denominator=1)
    ref = dict(item_id="item", version_id="scene")
    frames = dict(start=1, end=300)
    header = dict(
        schema_version="1.0",
        project_id="project",
        sequence_version_id="sequence",
        fps=fps,
        frame_count=300,
    )
    sequence = dict(
        **header,
        silent=True,
        entries=[
            dict(
                entry_id="entry",
                version_id="scene",
                job_id="render",
                shot_id=None,
                source_frames=frames,
                sequence_frames=frames,
                transform="identity",
                mapping_artifact_id="mapping",
            )
        ],
    )
    mapping = dict(
        **header,
        blend_space="linear_rgb",
        frames=[
            dict(
                sequence_frame=i,
                contributors=[
                    dict(
                        version_id="scene",
                        job_id="render",
                        shot_id=None,
                        source_frame=i,
                        weight=dict(numerator=1, denominator=1),
                    )
                ],
            )
            for i in range(1, 301)
        ],
    )
    with store.transaction() as tx:
        tx.put("versions", "scene", version_record("project", ref), schema="VersionRecord")
        tx.put(
            "jobs",
            "render",
            job_record(
                "project",
                "render",
                ref,
                spec=dict(
                    intent_id="intent",
                    shot_id=None,
                    purpose="proxy",
                    source_closure_digest=SHA256,
                    scene_name="Scene",
                    camera=None,
                    frames=frames,
                    fps=fps,
                    dimensions=dict(width=16, height=16),
                    engine="cycles",
                    device="cpu",
                    samples=1,
                    renderer_revision="test",
                    colour_management=dict(
                        view_transform="Standard", look="None", exposure=0, gamma=1
                    ),
                ),
            ),
            schema="JobRecord",
        )
    with store.transaction(write=False) as tx:
        sql = []
        tx.connection.set_trace_callback(sql.append)
        sequence_map(tx, sequence, "sequence", {"mapping": mapping})
        assert len(sql) == 2 and all(s.startswith("SELECT body FROM records") for s in sql)
        mapping["frames"][-1]["contributors"][0]["source_frame"] = 301
        with pytest.raises(DomainError) as error:
            sequence_map(tx, sequence, "sequence", {"mapping": mapping})
        assert (
            error.value.message
            == "Sequence source_frames exceed the source render job's admitted frame range"
        )


def test_cached_validators_and_invalid_ingress(project, transport):
    _, _, base, _ = project
    assert _validator("Artifact") is _validator("Artifact")
    assert _validator("Artifact") is not _validator("ReviewTicket")
    assert _issue_validator("SequenceMap", True) is _issue_validator("SequenceMap", True)
    assert _issue_validator("SequenceMap", True) is not _issue_validator("SequenceMap", False)
    with pytest.raises(DomainError) as error:
        validate("Artifact", {"schema_version": "1.0"})
    assert (error.value.status, error.value.code) == (400, "invalid_request")
    response = transport.post(base + "/requests", json={"schema_version": "1.0"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("warm", [False, True], ids=["cold-insert", "cached-row-edited"])
def test_snapshot_and_jobs_reject_unproven_stored_bytes(project, transport, monkeypatch, warm):
    _, record, base, services = project
    project_id = record["project_id"]
    store = services.registry.project_store(project_id)
    job = job_record(project_id, "edited-job", None)
    if warm:
        with store.transaction() as tx:
            tx.put("jobs", job["job_id"], job, schema="JobRecord")
        checked = []

        def track_validation(name, value):
            checked.append(name)
            return validate(name, value)

        monkeypatch.setattr(contracts, "validate", track_validation)
        for endpoint in ("/snapshot", "/jobs"):
            assert transport.get(base + endpoint).status_code == 200
        assert checked.count("JobRecord") == 1  # Only the first successful read proves the bytes.
        with store.transaction() as tx:
            tx.connection.execute(
                "UPDATE records SET body=json_remove(body, '$.availability') "
                "WHERE namespace='jobs' AND id=?",
                (job["job_id"],),
            )
    else:
        job.pop("availability")
        with store.transaction() as tx:
            tx.connection.execute(
                "INSERT INTO records VALUES ('jobs', ?, 0, ?)",
                (job["job_id"], canonical(job).decode()),
            )
    for endpoint in ("/snapshot", "/jobs"):
        rejected = transport.get(base + endpoint)
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "invalid_request"
    if warm:
        assert checked.count("JobRecord") == 3


def test_integer_round_trip_is_rejected_with_or_without_cached_proofs(project, transport):
    _, record, base, services = project
    project_id = record["project_id"]
    store = services.registry.project_store(project_id)
    job = {**job_record(project_id, "wide-frame-count", None), "completed_frames": 1e20}
    with store.transaction() as tx:
        tx.put("jobs", job["job_id"], job, schema="JobRecord")
    with store.transaction(write=False) as tx:
        [(raw, parsed)] = tx.all_raw("jobs")
    assert '"completed_frames":100000000000000000000' in raw
    assert type(parsed["completed_frames"]) is int
    first = transport.get(base + "/jobs")
    with contracts._VALIDATED_JSON_LOCK:
        contracts._VALIDATED_JSON.clear()
    cold = transport.get(base + "/jobs")
    assert first.status_code == cold.status_code == 400
    assert first.json() == cold.json()
    assert cold.json()["error"]["code"] == "invalid_request"


def test_artefact_attribution_only_projects_filtered_page(project, transport, monkeypatch):
    _, record, base, services = project
    store = services.registry.project_store(record["project_id"])
    artefact = dict(
        schema_version="1.0",
        project_id=record["project_id"],
        kind="document",
        purpose="metadata",
        sha256="a" * 64,
        bytes=1,
        media_type="text/plain",
        state="ready",
        derived_from=[],
    )
    with store.transaction() as tx:
        for name, purpose in (("a", "metadata"), ("b", "metadata"), ("c", "reference")):
            tx.put(
                "artifacts",
                name,
                {**artefact, "artifact_id": name, "purpose": purpose},
                schema="Artifact",
            )
        outcomes.record_tool_outcome(
            tx,
            invocation_id="invocation",
            session_id=None,
            project_id=record["project_id"],
            work_id="work",
            request_id="request",
            tool_name="register",
            status="succeeded",
            artifact_ids=["a", "b"],
            published_versions=[dict(item_id="item", version_id="version")],
        )
    compute = outcomes.artifact_attributions
    with store.transaction(write=False) as tx:
        expected = compute(tx)
    calls = []

    def spy(tx, *, artifact_ids):
        calls.append(artifact_ids)
        result = compute(tx, artifact_ids=artifact_ids)
        assert result == {key: expected[key] for key in artifact_ids}
        return result

    monkeypatch.setattr(outcomes, "artifact_attributions", spy)
    query = dict(limit=1, kind="document", purpose="metadata")
    response = transport.get(base + "/artifacts", params=query)
    assert response.status_code == 200, response.text
    first = response.json()
    validate("RetainedArtifactPage", first)
    assert first["items"][0]["artifact"]["artifact_id"] == "a"
    second = transport.get(base + "/artifacts", params={**query, "cursor": first["next_cursor"]})
    assert second.status_code == 200 and second.json()["items"][0]["artifact"]["artifact_id"] == "b"
    assert calls == [{"a"}, {"b"}]
    monkeypatch.setattr(
        outcomes,
        "artifact_attributions",
        lambda tx, *, artifact_ids: {"a": {**expected["a"], "attribution_status": "bad"}},
    )
    assert transport.get(base + "/artifacts", params=query).status_code == 400
    with store.transaction() as tx:
        tx.connection.execute(
            "UPDATE records SET body=json_set(body, '$.project_id', 'wrong') WHERE namespace='artifacts' AND id='c'"
        )
    rejected = transport.get(base + "/artifacts", params=query)
    assert rejected.status_code == 403 and rejected.json()["error"]["code"] == "scope_denied"


@pytest.mark.parametrize("mode", [0o644, 0o4600, 0o2600, 0o1600])
def test_deferred_store_repairs_mode_without_opening_sqlite(monkeypatch, tmp_path, mode):
    path = tmp_path / "state.sqlite3"
    Store(path)
    opens = count_opens(monkeypatch)
    Store(path)
    assert len(opens) == 1  # Direct callers check the file immediately.
    path.chmod(mode)
    assert stat.S_IMODE(path.stat().st_mode) == mode
    deferred = Store(path, defer_migrate=True)
    assert len(opens) == 1 and stat.S_IMODE(path.stat().st_mode) == 0o600
    with deferred.transaction(write=False) as tx:
        assert len(opens) == 2  # Schema and WAL checks use the transaction connection.
        for pragma, expected in (
            ("journal_mode", "wal"),
            ("synchronous", 2),
            ("foreign_keys", 1),
            ("busy_timeout", 5000),
        ):
            assert tx.connection.execute(f"PRAGMA {pragma}").fetchone()[0] == expected


def test_registry_project_store_verifies_on_one_project_connection(project, monkeypatch):
    _, record, _, services = project
    connect = Store.connect
    opened = []

    def counted(self, **kwargs):
        opened.append(self.path.name)
        return connect(self, **kwargs)

    monkeypatch.setattr(Store, "connect", counted)
    store = services.registry.project_store(record["project_id"])
    assert opened == ["registry.sqlite3"]
    with store.transaction(write=False) as tx:
        assert tx.watermark() > 0
    assert opened == ["registry.sqlite3", "state.sqlite3"]


def test_store_construction_preserves_live_posix_locks(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = Store(path)
    info = path.stat()
    identity = (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino)

    def locks():
        result = set()
        for line in Path("/proc/locks").read_text().splitlines():
            fields = line.split()
            if fields[1] != "POSIX" or fields[4] != str(os.getpid()):
                continue
            major, minor, inode = fields[5].split(":")
            if (int(major, 16), int(minor, 16), int(inode)) == identity:
                result.add(tuple(fields[1:]))
        return result

    with store.transaction():  # BEGIN IMMEDIATE holds the main database's shared lock in WAL mode.
        original = locks()
        assert original
        Store(path)
        assert locks() == original
        with Store(path, defer_migrate=True).transaction(write=False):
            assert locks() == original
        assert locks() == original


@pytest.mark.parametrize("defer_migrate", [False, True], ids=["direct", "deferred"])
def test_in_place_rollback_journal_restore_repairs_wal(monkeypatch, tmp_path, defer_migrate):
    path, replacement = tmp_path / "state.sqlite3", tmp_path / "incoming.sqlite3"
    Store(path)
    inode = path.stat().st_ino
    shutil.copyfile(path, replacement)
    calls = []
    migrate = Store.migrate

    def spy(self):
        calls.append(self.path)
        migrate(self)

    monkeypatch.setattr(Store, "migrate", spy)
    with closing(sqlite3.connect(replacement, isolation_level=None)) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    shutil.copyfile(replacement, path)
    assert path.stat().st_ino == inode
    restored = Store(path, defer_migrate=defer_migrate)
    assert calls == ([] if defer_migrate else [path])
    with restored.transaction(write=False) as tx:
        assert tx.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert calls == [path]


@pytest.mark.parametrize("version", [99, 1], ids=["newer-schema", "rollback-journal"])
def test_reused_store_rechecks_in_place_restore(tmp_path, version):
    path, replacement = tmp_path / "state.sqlite3", tmp_path / "incoming.sqlite3"
    Store(path)
    reused = Store(path, defer_migrate=True)
    with reused.transaction(write=False) as tx:
        assert tx.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    inode = path.stat().st_ino
    if version != 1:
        shutil.copyfile(path, replacement)
    with closing(sqlite3.connect(replacement, isolation_level=None)) as connection:
        if version == 1:
            connection.executescript(MIGRATIONS[0])
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
        connection.execute(f"PRAGMA user_version={version}")
    shutil.copyfile(replacement, path)  # All SQLite handles are closed before the offline copy.
    assert path.stat().st_ino == inode
    if version == 99:
        with pytest.raises(DomainError) as error, reused.transaction(write=False):
            pytest.fail("Newer schema accepted on a reused handle")
        assert (error.value.status, error.value.code) == (415, "unsupported_contract")
    else:
        with reused.transaction(write=False) as tx:
            assert tx.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert tx.connection.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_concurrent_store_construction_serialises_fresh_migrations(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        for trial in range(10):
            path = tmp_path / f"fresh-{trial}.sqlite3"
            start = Barrier(8, timeout=5)

            def construct():
                start.wait()
                return Store(path)

            stores = [pool.submit(construct) for _ in range(8)]
            for pending in stores:
                with pending.result(timeout=10).transaction(write=False) as tx:
                    assert tx.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                    assert tx.connection.execute("PRAGMA user_version").fetchone()[0] == len(
                        MIGRATIONS
                    )


def test_wal_setup_and_migration_retry_transient_busy(tmp_path, monkeypatch):
    open_connection = Store._open_connection
    apply_migrations = Store._apply_migrations
    attempts = dict(wal=0, migrate=0)

    def open_once_busy(self, *, set_wal):
        if set_wal:
            attempts["wal"] += 1
            if attempts["wal"] == 1:
                raise sqlite3.OperationalError("database is locked")
        return open_connection(self, set_wal=set_wal)

    def apply_once_busy(connection):
        attempts["migrate"] += 1
        if attempts["migrate"] == 1:
            raise sqlite3.OperationalError("database is busy")
        apply_migrations(connection)

    monkeypatch.setattr(Store, "_open_connection", open_once_busy)
    monkeypatch.setattr(Store, "_apply_migrations", staticmethod(apply_once_busy))
    with Store(tmp_path / "retry.sqlite3").transaction(write=False) as tx:
        assert tx.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert tx.connection.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert attempts == dict(wal=2, migrate=2)


@pytest.mark.parametrize("defer_migrate", [False, True], ids=["direct", "deferred"])
def test_store_rejects_newer_uncheckpointed_wal_schema(tmp_path, defer_migrate):
    path = tmp_path / "state.sqlite3"
    store = Store(path)
    with closing(store.connect()) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("PRAGMA user_version=99")
        assert path.with_name(path.name + "-wal").stat().st_size > 32
        if defer_migrate:
            deferred = Store(path, defer_migrate=True)
            with pytest.raises(DomainError) as error, deferred.transaction(write=False):
                pytest.fail("Newer schema accepted")
        else:
            with pytest.raises(DomainError) as error:
                Store(path)
        assert (error.value.status, error.value.code) == (415, "unsupported_contract")


def count_opens(monkeypatch):
    connect = Store.connect
    lock = Lock()
    threads = []

    def counted(self, **kwargs):
        with lock:
            threads.append(get_ident())
        return connect(self, **kwargs)

    monkeypatch.setattr(Store, "connect", counted)
    return threads


def test_websocket_idle_offload_commit_wake_resubscribe_and_disconnect(
    project, transport, monkeypatch
):
    _, record, _, services = project
    project_id = record["project_id"]
    store = services.registry.project_store(project_id)
    with store.transaction(write=False) as tx:
        watermark = tx.watermark()
    threads = count_opens(monkeypatch)
    origin = str(transport.base_url).rstrip("/")
    cookie = transport.cookies[COOKIE_NAME]

    async def scenario():
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        subscription = dict(
            schema_version="1.0", type="subscribe", project_id=project_id, after_sequence=watermark
        )

        async def receive_payload(timeout=1.0):
            message = await asyncio.wait_for(outgoing.get(), timeout)
            assert message["type"] == "websocket.send", message
            return json.loads(message["text"])

        task = asyncio.create_task(
            transport.app(
                {
                    "type": "websocket",
                    "path": "/api/v1/events",
                    "root_path": "",
                    "scheme": "ws",
                    "query_string": b"",
                    "headers": [
                        (b"origin", origin.encode()),
                        (b"host", services.settings.host.encode()),
                        (b"cookie", f"{COOKIE_NAME}={cookie}".encode()),
                    ],
                },
                incoming.get,
                outgoing.put,
            )
        )
        try:
            await incoming.put({"type": "websocket.connect"})
            assert (await asyncio.wait_for(outgoing.get(), 1))["type"] == "websocket.accept"
            await incoming.put({"type": "websocket.receive", "text": json.dumps(subscription)})
            assert (await receive_payload())["type"] == "welcome"
            assert (await receive_payload())["type"] == "caught_up"
            opens = len(threads)
            started = time.monotonic()
            await asyncio.sleep(2.1)  # Cross a safety-net poll.
            assert (len(threads) - opens) / (time.monotonic() - started) < 3
            assert get_ident() not in threads

            def emit():
                with store.transaction() as tx:
                    return tx.emit("project.state_changed", record, actor=ACTOR)

            started = time.monotonic()
            expected = await asyncio.to_thread(emit)
            assert await receive_payload(timeout=0.2) == expected
            assert time.monotonic() - started < 0.2
            await incoming.put({"type": "websocket.receive", "text": json.dumps(subscription)})
            assert (await receive_payload())["last_sequence"] == expected["sequence"]
            assert await receive_payload() == expected
            assert (await receive_payload())["type"] == "caught_up"
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(task, 1)
            assert not services.event_signals._waiters
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_lifecycle_uses_shared_idle_check_and_wakes_for_outbox(project, monkeypatch):
    _, record, _, services = project
    project_id = record["project_id"]
    store = services.registry.project_store(project_id)
    threads = count_opens(monkeypatch)
    monkeypatch.setitem(services.extensions, "copilot", None)

    async def scenario():
        observed = asyncio.Queue()

        async def close():
            pass

        async def reconcile(identity):
            await observed.put(identity)

        with monkeypatch.context() as scoped:
            scoped.setitem(
                services.extensions, "workflows", SimpleNamespace(reconcile=reconcile, close=close)
            )
            lifecycle = FeatureLifecycle(services)
            await lifecycle.start()
            try:
                assert await asyncio.wait_for(observed.get(), 1) == project_id
                await asyncio.sleep(0.05)  # Let the initial observations settle.
                opens = len(threads)
                started = time.monotonic()
                await asyncio.sleep(2.1)
                assert (len(threads) - opens) / (time.monotonic() - started) < 8
                assert observed.empty() and get_ident() not in threads

                def enqueue():
                    with store.transaction() as tx:
                        tx.enqueue("preview", "preview.render", {})

                started = time.monotonic()
                await asyncio.to_thread(enqueue)
                assert await asyncio.wait_for(observed.get(), 0.2) == project_id
                assert time.monotonic() - started < 0.2
            finally:
                await asyncio.wait_for(lifecycle.close(), 2)
            assert not lifecycle.tasks and not services.lifecycle_signal._waiters

    asyncio.run(scenario())
