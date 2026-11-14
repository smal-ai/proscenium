"""Offline composition using real Git LFS, SQLite, catalogue, reviews and W01 state.

Native bytes here are explicit synthetic fixtures, never evidence of SDK resume.
"""

import asyncio
import io
import json
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from integration.copilot.test_delivery import Native, context, submit
from integration.sources.helpers import closure, commit, publication
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue import install as install_catalogue
from proscenium.copilot import install as install_copilot
from proscenium.projects.service import Projects
from proscenium.reviews import install as install_reviews
from proscenium.services import Services
from proscenium.sources.common import git

from tools.operations.recovery import backup, restore, verify
from tools.operations.safety import RecoveryError, digest, locks, still_live

OWNER = Caller("owner", "owner-test")


@pytest.fixture
def saved(source_services, source_caller, root, tmp_path):
    services = source_services
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(output, "PNG")
    image = output.getvalue()
    commit_id = commit(root, "assets/reference.png", image)
    source = closure(commit_id, "assets/reference.png", image)
    catalogue = services.extension("catalogue")
    command = publication(commit_id, source, tags=["recovery"])
    catalogue.publish(source_caller, command)
    artifacts = services.extension("artifacts")
    artifact, _ = artifacts.upload(OWNER, "project-test", "image-private", image, "image/png")
    retained = artifacts.promote(
        "project-test",
        "retained-private",
        image,
        kind="source_blob",
        purpose="reference",
        media_type="image/png",
    )
    with services.registry.project_store("project-test").transaction() as tx:
        artifacts.put(tx, retained)
    artifacts.retain(OWNER, "project-test", retained["artifact_id"], reason="Recovery fixture")
    reviews = install_reviews(services)
    target = dict(
        origin="reference",
        artifact_id=artifact["artifact_id"],
        displayed_sha256=artifact["sha256"],
        dimensions=artifact["dimensions"],
        item_id=None,
        version_id=None,
        shot_id=None,
        render_job_id=None,
        source_frame=None,
        media_artifact_id=None,
    )
    note = dict(
        schema_version="1.0",
        operation_id="save-recovery-note",
        project_id="project-test",
        expected_revision=0,
        annotation=dict(
            annotation_id="annotation-recovery",
            saved_revision=None,
            target=target,
            range=None,
            camera=None,
            markups=[],
            text="Keep this exact blue image",
            spatial_hits=[],
            spatial_status="unavailable",
        ),
    )
    reviews.save_annotation(OWNER, note)
    selection = dict(
        schema_version="1.0",
        operation_id="select-recovery",
        project_id="project-test",
        selection_id="selection-recovery",
        expected_revision=0,
        action="select",
        slot={"kind": "item", "item_id": "item-test"},
        choice={
            "kind": "version",
            "version": {"item_id": "item-test", "version_id": "version-test"},
        },
    )
    reviews.save_selection(OWNER, selection)
    store = services.registry.project_store("project-test")
    with store.transaction() as tx:
        work = tx.require("work", "work-test")
        tx.put(
            "work",
            "work-test",
            {**work, "state": "completed", "state_revision": 1},
            expected_revision=0,
        )
        invocation = tx.require("invocations", "invocation-test")
        tx.put(
            "invocations",
            "invocation-test",
            {**invocation, "state": "completed"},
            expected_revision=0,
        )
        tx.put(
            "copilot.session",
            "native",
            {"session_id": "synthetic-session", "owner_epoch": services.ownership.epoch},
        )
    runtime = install_copilot(services, native=Native())
    context(runtime, annotations=[])
    submit(runtime, "request-held")
    # Capture original complete records for byte/metadata equality after relocation.
    with store.transaction(write=False) as tx:
        records = {
            namespace: tx.all(namespace)
            for namespace in (
                "requests",
                "annotations",
                "selections",
                "versions",
                "copilot.session",
                "contexts",
            )
        }
    native = (
        services.settings.data_directory / "copilot-runtime/home/session-state/synthetic-session"
    )
    (native / "checkpoints").mkdir(parents=True)
    (native / "files/empty-native-directory").mkdir(parents=True)
    (native / "events.jsonl").write_bytes(b'{"synthetic":true,"private":"not model context"}\n')
    (native / "workspace.yaml").write_text("synthetic: true\n")
    (native / "checkpoints/index.md").write_text("Synthetic checkpoint\n")
    other = native.parent / "other-project-session"
    other.mkdir()
    (other / "events.jsonl").write_text("DO NOT BACK UP ANOTHER PROJECT")
    # A private credential and a paired owner must never escape in the bundle.
    (services.settings.data_directory / ".env").write_text("TOKEN=do-not-copy")
    services.auth.issue_pairing()
    settings = services.settings
    services.close()
    return dict(
        settings=settings,
        root=root,
        image=image,
        artifact=artifact,
        records=records,
        note=note,
        selection=selection,
        commit=commit_id,
        native=native,
        bundle=tmp_path / "backup",
        destination=tmp_path / "restored",
        store=store,
    )


def take(saved, **kwargs):
    return backup(saved["settings"], "project-test", saved["bundle"], **kwargs)


def test_private_backup_restores_composed_records_sources_and_closed_startup(saved):
    root = saved["root"]
    # Unrelated staging and dirty files must remain untouched, not reset/stashed.
    (root / "unrelated.txt").write_text("staged unrelated work\n")
    git(root, "add", "--", "unrelated.txt")
    (root / "assets/reference.png").write_bytes(saved["image"] + b"dirty-working-only")
    before_index = (root / ".git/index").read_bytes()
    manifest = take(saved)
    assert manifest["source"]["lfs_oids"]
    assert (root / ".git/index").read_bytes() == before_index
    assert manifest["native_session_ids"] == ["synthetic-session"]
    assert not any("other-project" in p or ".env" in p for p in manifest["files"])
    assert verify(saved["bundle"])["limits"] == []
    report = restore(saved["bundle"], saved["destination"], rebind=True)
    assert report["native_resume_tested"] is False
    restored_settings = replace(saved["settings"], data_directory=Path(report["data_directory"]))
    services = Services.create(restored_settings)
    try:
        install_catalogue(services)
        assert Projects.config(Path(report["path"]))["project_id"] == "project-test"
        reviews = install_reviews(services)
        runtime = install_copilot(services, native=Native())
        with pytest.raises(DomainError, match="Resume"):
            services.gate.require("project-test")
        with services.registry.project_store("project-test").transaction(write=False) as tx:
            for namespace, records in saved["records"].items():
                assert tx.all(namespace) == records
        assert (
            services.extension("artifacts").ready_path("project-test", "image-private").read_bytes()
            == saved["image"]
        )
        # Original durable acknowledgement receipts replay without duplicate mutations.
        assert reviews.save_annotation(OWNER, saved["note"])["replayed"]
        assert reviews.save_selection(OWNER, saved["selection"])["replayed"]
        materialised = services.extension("sources").materialise("project-test", "version-test")
        assert (materialised / "assets/reference.png").read_bytes() == saved["image"]

        async def quiet_start():
            await runtime.start()
            await asyncio.sleep(0.03)
            assert runtime.native.opens == []
            await runtime.close()

        asyncio.run(quiet_start())
        with services.registry.store.transaction(write=False) as tx:
            assert tx.all("auth.sessions") == []
            assert tx.all("auth.capabilities") == []
            assert tx.all("auth.pairing") == []
        restored_native = (
            restored_settings.data_directory
            / "copilot-runtime/home/session-state/synthetic-session"
        )
        assert (restored_native / "files/empty-native-directory").is_dir()
        assert digest(restored_native / "events.jsonl") == digest(saved["native"] / "events.jsonl")
    finally:
        services.close()
    assert saved["bundle"].stat().st_mode & 0o777 == 0o700
    for path in saved["bundle"].rglob("*"):
        if path.is_file():
            assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("category", ["artefacts/sha256", "retained"])
@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_missing_and_corrupt_required_media_are_not_substituted(saved, category, fault):
    path = saved["root"] / ".proscenium" / category / saved["artifact"]["sha256"] / "content"
    if fault == "missing":
        path.unlink()
        code = "retained_source_missing" if category == "retained" else "media_unavailable"
        with pytest.raises(RecoveryError, match=code):
            take(saved)
        manifest = take(saved, allow_incomplete=True)
        assert manifest["limits"][0]["code"] == code
        with pytest.raises(RecoveryError, match="acknowledgement"):
            restore(saved["bundle"], saved["destination"], rebind=True)
        report = restore(saved["bundle"], saved["destination"], rebind=True, allow_incomplete=True)
        assert report["limits"][0]["code"] == code
    else:
        path.chmod(0o600)  # Deliberately corrupt only this disposable immutable fixture.
        path.write_bytes(b"not the retained image")
        with pytest.raises(RecoveryError, match="hash_mismatch"):
            take(saved, allow_incomplete=True)


def test_missing_native_history_is_distinct_and_never_replaced(saved):
    (saved["native"] / "events.jsonl").unlink()
    with pytest.raises(RecoveryError, match="native_session_missing"):
        take(saved)
    manifest = take(saved, allow_incomplete=True)
    assert manifest["limits"] == [
        {"code": "native_session_missing", "session_id": "synthetic-session"}
    ]
    report = restore(saved["bundle"], saved["destination"], rebind=True, allow_incomplete=True)
    assert not (
        Path(report["data_directory"]) / "copilot-runtime/home/session-state/synthetic-session"
    ).exists()


def test_missing_or_corrupt_lfs_refuses_offline_without_fetch(saved):
    objects = list((saved["root"] / ".git/lfs/objects").glob("*/*/*"))
    assert objects
    objects[0].write_bytes(b"corrupt-lfs")
    with pytest.raises(RecoveryError, match="hash_mismatch:lfs"):
        take(saved)
    objects[0].unlink()
    # Prior failed directory remains untouched, not automatically deleted/reused.
    saved["bundle"] = saved["bundle"].with_name("backup-second")
    with pytest.raises(RecoveryError, match="lfs_object_missing"):
        take(saved)
    assert not objects[0].exists()


def test_hash_inventory_traversal_symlinks_and_overwrite_refused(saved):
    take(saved)
    with pytest.raises(RecoveryError, match="destination_exists"):
        take(saved)
    with pytest.raises(RecoveryError, match="explicit_location_rebind"):
        restore(saved["bundle"], saved["destination"], rebind=False)
    file = saved["bundle"] / "native/synthetic-session/events.jsonl"
    original = file.read_bytes()
    file.write_bytes(b"tampered")
    with pytest.raises(RecoveryError, match="hash_mismatch"):
        verify(saved["bundle"])
    file.unlink()
    file.symlink_to(saved["native"] / "events.jsonl")
    with pytest.raises(RecoveryError, match="symlink"):
        verify(saved["bundle"])
    file.unlink()
    file.write_bytes(original)
    manifest_path = saved["bundle"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["../escape"] = {"sha256": "0" * 64, "bytes": 0}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(RecoveryError):
        restore(saved["bundle"], saved["destination"], rebind=True)
    assert not saved["destination"].exists()


def test_service_locks_processes_and_unsettled_work_refused(saved):
    services = Services.create(saved["settings"])
    try:
        with pytest.raises(RecoveryError, match="owned"):
            take(saved)
    finally:
        services.close()
    process = subprocess.Popen(["sleep", "30"], cwd=saved["native"])
    try:
        with pytest.raises(RecoveryError, match="active_or_ambiguous"):
            take(saved)
        assert process.poll() is None
    finally:
        process.terminate()  # Test owns this harmless sleep, not a product service.
        process.wait()
    with saved["store"].transaction() as tx:
        tx.enqueue("uncertain", "synthetic", {})
        tx.claim_intent("uncertain")
    with pytest.raises(DomainError) as error:
        take(saved)
    assert error.value.code == "project_busy"


def test_secret_and_symlink_source_paths_refused(saved):
    (saved["root"] / "leak").symlink_to(saved["native"] / "events.jsonl")
    with pytest.raises(RecoveryError, match="symlink"):
        take(saved)


def test_secret_path_anywhere_in_git_history_is_refused(saved):
    root = saved["root"]
    (root / ".env").write_text("SYNTHETIC_SECRET_DO_NOT_BACK_UP=true\n")
    git(root, "add", "--force", "--", ".env")
    git(root, "commit", "-m", "Disposable secret-path rejection fixture")
    (root / ".env").unlink()
    git(root, "add", "--update", "--", ".env")
    git(root, "commit", "-m", "Removal must not hide a historical secret path")
    with pytest.raises(RecoveryError, match="secret_path_in_git_history"):
        take(saved)


def test_sqlite_backup_includes_committed_wal_not_only_main_file(saved):
    store = saved["store"]
    reader = store.connect()
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM records").fetchone()
        with store.transaction() as tx:
            tx.put("recovery.fixture", "wal-only", {"saved": "committed after reader began"})
        assert store.path.with_name(store.path.name + "-wal").stat().st_size > 0
        take(saved)
        path = saved["bundle"] / "project/.proscenium/state.sqlite3"
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute(
                "SELECT body FROM records WHERE namespace='recovery.fixture' AND id='wal-only'"
            ).fetchone()
            assert json.loads(row[0])["saved"] == "committed after reader began"
    finally:
        reader.close()


def test_native_session_directory_cannot_escape_through_parent_symlink(saved):
    native = saved["native"]
    original = native.parent
    moved = original.with_name("moved-native")
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=True)
    with pytest.raises(RecoveryError, match="native_storage_symlink_refused"):
        take(saved)


def test_all_git_refs_survive_restore_without_branch_name_collision(saved):
    root = saved["root"]
    git(root, "branch", "recovery", "HEAD")
    git(root, "tag", "source-v1", "HEAD")
    git(root, "symbolic-ref", "refs/remotes/offline/HEAD", "refs/heads/main")
    commit(root, "brief/later.txt", b"Later source, old publication stays pinned\n")
    manifest = take(saved)
    report = restore(saved["bundle"], saved["destination"], rebind=True)
    restored_root = Path(report["path"])
    actual = (
        git(restored_root, "for-each-ref", "--format=%(refname) %(objectname)")
        .decode()
        .splitlines()
    )
    assert actual == manifest["source"]["refs"]
    assert git(restored_root, "rev-parse", "HEAD").decode().strip() == manifest["source"]["head"]
    assert (
        git(restored_root, "symbolic-ref", "refs/remotes/offline/HEAD").decode().strip()
        == "refs/heads/main"
    )


def test_missing_git_object_refused_without_substituting_working_source(saved):
    root = saved["root"]
    pointer = git(root, "rev-parse", "HEAD:assets/reference.png").decode().strip()
    (root / ".git/objects" / pointer[:2] / pointer[2:]).unlink()
    with pytest.raises(RecoveryError, match="git_failed:fsck"):
        take(saved)


def test_future_database_refused_without_migration(saved):
    with closing(saved["store"].connect()) as db:
        db.execute("PRAGMA user_version=999")
    before = digest(saved["store"].path)
    with pytest.raises(RecoveryError, match="unsupported_or_corrupt_database"):
        take(saved)
    assert digest(saved["store"].path) == before


def test_git_object_directory_symlink_refused_before_git_reads_it(saved):
    objects = saved["root"] / ".git/objects"
    relocated = objects.with_name("relocated-objects")
    objects.rename(relocated)
    objects.symlink_to(relocated, target_is_directory=True)
    with pytest.raises(RecoveryError, match="git_metadata_symlink_refused"):
        take(saved)


def test_lock_parent_directory_symlink_never_opens_external_lock(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(RecoveryError, match="symlink_lock_refused"):
        with locks([alias / "owner.lock"]):
            pytest.fail("Lock through symlink must not be acquired")
    assert not (real / "owner.lock").exists()


def test_process_exit_race_only_ignores_observed_terminal_kernel_state(tmp_path):
    process = tmp_path / "123"
    process.mkdir()
    stat = process / "stat"
    for state in ("Z", "X", "x"):
        stat.write_text(f"123 (process name with ) parentheses) {state} 0 0\n")
        assert not still_live(process)
    stat.write_text("123 (uninspectable living worker) S 0 0\n")
    assert still_live(process)
    stat.write_text("malformed status is not proof of exit")
    assert still_live(process)
    stat.unlink()
    assert not still_live(process)


def test_cli_round_trip_and_refusal_exit_codes(saved):
    command = ["uv", "run", "python", "-m", "tools.operations.recovery"]
    result = subprocess.run(
        command
        + [
            "backup",
            "--data-directory",
            str(saved["settings"].data_directory),
            "--project-id",
            "project-test",
            "--target",
            str(saved["bundle"]),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    verified = subprocess.run(
        command + ["verify", str(saved["bundle"])], capture_output=True, text=True
    )
    assert verified.returncode == 0, verified.stderr
    restored = subprocess.run(
        command
        + [
            "restore",
            str(saved["bundle"]),
            "--target",
            str(saved["destination"]),
            "--rebind-fresh-registry",
        ],
        capture_output=True,
        text=True,
    )
    assert restored.returncode == 0, restored.stderr
    repeated = subprocess.run(
        command
        + [
            "restore",
            str(saved["bundle"]),
            "--target",
            str(saved["destination"]),
            "--rebind-fresh-registry",
        ],
        capture_output=True,
        text=True,
    )
    assert repeated.returncode == 2
    assert "destination_exists" in repeated.stderr
