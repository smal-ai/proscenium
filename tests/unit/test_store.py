import sqlite3
from pathlib import Path

import pytest
from proscenium.api.errors import DomainError
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.db import Store
from proscenium.db.ownership import Ownership
from proscenium.db.pagination import Cursors
from proscenium.db.paths import project_path
from proscenium.db.store import MIGRATIONS
from proscenium.jobs import transition
from proscenium.jobs.resources import reserve_generation, settle_generation


def test_atomic_operation_event_outbox(services):
    store = services.registry.project_store("project-test")

    def effect(tx):
        tx.put("synthetic", "record-test", {"state_revision": 0})
        tx.enqueue("intent-test", "synthetic.launch", {"input": "fixed"})
        tx.emit(
            "work.state_changed",
            tx.require("work", "work-test"),
            actor={"kind": "system", "id": "synthetic-system"},
        )
        raise RuntimeError("rollback all")

    with pytest.raises(RuntimeError), store.transaction() as tx:
        tx.operation("synthetic", "operation-test", {"a": 1}, lambda: effect(tx))
    with store.transaction() as tx:
        assert tx.get("synthetic", "record-test", required=False) is None
        assert tx.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
        assert tx.watermark() == 0
        first, replayed = tx.operation(
            "synthetic", "operation-test", {"b": 2, "a": 1}, lambda: {"id": "fixed"}
        )
        assert not replayed
        second, replayed = tx.operation(
            "synthetic", "operation-test", {"a": 1, "b": 2}, lambda: pytest.fail("repeated effect")
        )
        assert replayed and first == second
        with pytest.raises(DomainError, match="idempotency conflict"):
            tx.operation("synthetic", "operation-test", {"a": 2}, lambda: {})


def test_cas_and_scope(services):
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put("synthetic", "record-test", {"state_revision": 0})
        tx.put("synthetic", "record-test", {"state_revision": 1}, expected_revision=0)
        with pytest.raises(DomainError) as error:
            tx.put("synthetic", "record-test", {"state_revision": 1}, expected_revision=0)
        assert error.value.code == "revision_conflict"
        with pytest.raises(DomainError):
            tx.put("synthetic", "foreign", {"project_id": "other-project"})


def test_migration_rollback_and_backup(tmp_path: Path, monkeypatch):
    path = tmp_path / "state.sqlite3"
    store = Store(path)
    with store.transaction() as tx:
        tx.put("synthetic", "preserved", {"value": 7})
    monkeypatch.setattr(
        "proscenium.db.store.MIGRATIONS",
        [*MIGRATIONS, "CREATE TABLE rollback_probe (id TEXT); INVALID SQL;"],
    )
    with pytest.raises(sqlite3.OperationalError):
        store.migrate()
    with store.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='rollback_probe'"
            ).fetchone()
            is None
        )
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert path.with_suffix(".pre-v2.sqlite3").exists()
    with store.transaction(write=False) as tx:
        assert tx.require("synthetic", "preserved")["value"] == 7


@pytest.mark.parametrize(
    "relative",
    [
        "../escape",
        "/etc/passwd",
        ".env",
        ".env.local",
        ".direnv/token",
        ".git/config",
        ".proscenium/state.sqlite3",
        "file:///etc/passwd",
        "x\\y",
        ".ssh/id_rsa",
        "private.key",
    ],
)
def test_forbidden_paths(tmp_path: Path, relative):
    with pytest.raises(DomainError):
        project_path(tmp_path, relative, must_exist=False)


def test_symlink_and_directory_paths(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "safe.txt").write_text("synthetic")
    (tmp_path / "outside").write_text("not project content")
    (root / "escape").symlink_to(tmp_path / "outside")
    (root / ".env").write_text("synthetic, not credentials")
    (root / "alias").symlink_to(root / ".env")
    assert project_path(root, "safe.txt") == root / "safe.txt"
    assert project_path(root, ".", directory=True) == root
    for name in ("escape", "alias", "."):
        with pytest.raises(DomainError):
            project_path(root, name)


def test_os_lock_not_timestamp_lease(tmp_path: Path):
    owner = Ownership(tmp_path / "owner.lock")
    try:
        with pytest.raises(DomainError) as error:
            Ownership(tmp_path / "owner.lock")
        assert error.value.code == "owner_conflict"
    finally:
        owner.close()
    Ownership(tmp_path / "owner.lock").close()


def test_budget_uncertainty_holds_reservation(services):
    with services.registry.project_store("project-test").transaction() as tx:
        reserve_generation(tx, "reservation-1", "task-1", "target-1", 1)
        reserve_generation(tx, "reservation-1", "task-1", "target-1", 1)
        with pytest.raises(DomainError) as error:
            reserve_generation(tx, "reservation-2", "task-1", "target-1", 1)
        assert error.value.code == "generation_budget_exhausted"
        settle_generation(
            tx, "reservation-1", executed=False, evidence="provider rejected before execution"
        )
        reserve_generation(tx, "reservation-2", "task-1", "target-1", 1)
        settle_generation(
            tx, "reservation-2", executed=True, evidence="provider observed execution failure"
        )
        with pytest.raises(DomainError):
            reserve_generation(tx, "reservation-3", "task-1", "target-1", 1)


def test_cursors_bound_to_project_and_filter():
    cursors = Cursors(b"synthetic-test-secret")
    token = cursors.encode("project-a:jobs", "job-1")
    assert cursors.decode("project-a:jobs", token) == "job-1"
    for scope, value in [("project-b:jobs", token), ("project-a:jobs", token + "corrupt")]:
        with pytest.raises(DomainError):
            cursors.decode(scope, value)


def test_canonical_json_and_state_guards():
    assert canonical({"z": 1, "a": 2}) == b'{"a":2,"z":1}'
    transition("job_attempt", "cancel_requested", "succeeded", evidence="worker receipt")
    with pytest.raises(DomainError):
        transition("job_attempt", "outcome_uncertain", "succeeded")
    with pytest.raises(DomainError):
        transition("work", "stopped", "running")
    transition("work", "stopped", "running", explicit_resume=True)


def test_managed_paths_still_exclude_database_and_credentials(tmp_path: Path):
    for relative in (
        ".proscenium/state.sqlite3",
        ".proscenium/owner.lock",
        ".proscenium/auth/token",
    ):
        with pytest.raises(DomainError):
            project_path(tmp_path, relative, managed=True, must_exist=False)
    assert project_path(
        tmp_path, ".proscenium/artefacts/sha256/example.png", managed=True, must_exist=False
    ).is_relative_to(tmp_path)


def test_configuration_loading_is_explicit_and_non_secret(tmp_path: Path):
    import json

    config = tmp_path / "synthetic-config.json"
    assert Settings.load(config).generation_attempt_limit == 3
    config.write_text(
        json.dumps(
            {
                "data_directory": str(tmp_path / "private"),
                "generation_attempt_limit": 2,
                "execution_profiles": ["offline"],
            }
        )
    )
    loaded = Settings.load(config)
    assert loaded.generation_attempt_limit == 2 and loaded.execution_profiles == {"offline"}
    config.write_text('{"unknown_setting": true}')
    with pytest.raises(ValueError):
        Settings.load(config)
    with pytest.raises(ValueError):
        Settings(origin="http://0.0.0.0:8765")
    with pytest.raises(ValueError):
        Settings(services=({"endpoint": "http://secret:token@localhost:1234"},))


def test_future_database_version_is_rejected(tmp_path: Path):
    path = tmp_path / "future.sqlite3"
    store = Store(path)
    connection = store.connect()
    try:
        connection.execute("PRAGMA user_version=999")
    finally:
        connection.close()
    with pytest.raises(DomainError) as error:
        Store(path)
    assert error.value.code == "unsupported_contract"


def test_registry_location_and_repository_identity_do_not_drift(services, tmp_path: Path):
    record = services.registry.get("project-test")
    other = tmp_path / "another-project"
    other.mkdir()
    with pytest.raises(DomainError) as error:
        services.registry.register({**record, "path": str(other)})
    assert error.value.code == "project_location_conflict"
    with pytest.raises(DomainError) as error:
        services.registry.register({**record, "repository_id": "different-repository"})
    assert error.value.code == "source_conflict"
    root = Path(record["path"])
    moved = tmp_path / "moved-project"
    root.rename(moved)
    root.symlink_to(moved, target_is_directory=True)
    with pytest.raises(DomainError) as error:
        services.registry.project_store("project-test")
    assert error.value.code == "project_location_conflict"
