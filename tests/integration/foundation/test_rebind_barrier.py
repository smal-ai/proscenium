import io
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image
from proscenium.api.composition import configure_services
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical
from proscenium.db.store import now
from proscenium.projects.service import Projects


def prepare(services):
    root = Path(services.registry.get("project-test")["path"])
    Projects.bootstrap(root, "Synthetic coordination test")
    (root / "project.json").write_bytes(
        canonical(
            dict(
                schema_version="1.0",
                project_id="project-test",
                repository_id="repo-test",
                name="Synthetic coordination test",
                paths={
                    name: name for name in ("brief", "assets", "scenes", "storyboards", "sequences")
                },
            )
        )
    )
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "project_identity",
            "identity",
            {"project_id": "project-test", "repository_id": "repo-test"},
        )
        work = tx.require("work", "work-test")
        tx.put(
            "work",
            "work-test",
            {**work, "state": "completed", "state_revision": 1},
            expected_revision=0,
        )
    configure_services(services)
    return root


def copy_consistently(store, root, target):
    shutil.copytree(root, target, ignore=shutil.ignore_patterns("state.sqlite3*", "owner.lock"))
    store.backup(target / ".proscenium/state.sqlite3")


def test_real_rebind_excludes_concurrent_feedback_and_whole_upload(services, tmp_path, monkeypatch):
    root = prepare(services)
    old_store = services.registry.project_store("project-test")
    target = tmp_path / "copied"
    copy_consistently(old_store, root, target)
    projects = services.extension("projects")
    artifacts = services.extension("artifacts")
    entered, release, feedback_started, upload_started = (threading.Event() for _ in range(4))
    original_digest = Projects.store_digest

    def paused_digest(store):
        if store.path == old_store.path:
            entered.set()
            assert release.wait(5)
        return original_digest(store)

    monkeypatch.setattr(Projects, "store_digest", staticmethod(paused_digest))
    owner = Caller("owner", "owner-coordination")
    body = dict(
        schema_version="1.0",
        operation_id="coordinated-rebind",
        project_id="project-test",
        expected_revision=services.registry.get("project-test")["state_revision"],
        new_path=str(target),
    )
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, format="PNG")

    def feedback():
        feedback_started.set()
        with old_store.transaction() as tx:
            tx.put("annotations", "late-owner-feedback", {"synthetic": True})

    def upload():
        upload_started.set()
        return artifacts.upload(
            owner, "project-test", "coordinated-upload", buffer.getvalue(), "image/png"
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        rebound = pool.submit(projects.rebind, owner, body)
        assert entered.wait(3)
        writing, uploading = pool.submit(feedback), pool.submit(upload)
        assert feedback_started.wait(3) and upload_started.wait(3)
        assert not writing.done() and not uploading.done()
        # Reads still work while the reentrant mutation barrier is held elsewhere.
        with old_store.transaction(write=False) as tx:
            assert tx.all("artifacts") == [] and tx.all("annotations") == []
        release.set()
        assert rebound.result(timeout=5)[0]["state"] == "rebound"
        with pytest.raises(DomainError, match="Project location changed"):
            writing.result(timeout=5)
        artifact, replayed = uploading.result(timeout=5)
        assert artifact["state"] == "ready" and not replayed
    assert services.registry.get("project-test")["path"] == str(target)
    with old_store.transaction(write=False) as tx:
        assert tx.all("artifacts") == [] and tx.all("annotations") == []
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        assert len(tx.all("artifacts")) == 1 and tx.all("annotations") == []


def test_moved_native_association_cannot_bypass_history_verification(services, tmp_path):
    root = prepare(services)
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "copilot.session",
            "native",
            {
                "session_id": "real-association-fixture",
                "owner_epoch": services.ownership.epoch,
                "created_at": now(),
            },
        )
    # Model a legacy accepted baseline whose registry had not projected W01 yet.
    with services.registry.store.transaction() as tx:
        record = tx.require("projects", "project-test")
        tx.put(
            "projects",
            "project-test",
            {**record, "native_session_id": None, "state_revision": record["state_revision"] + 1},
            expected_revision=record["state_revision"],
        )
    target = tmp_path / "moved"
    root.rename(target)
    record = services.registry.get("project-test")
    with pytest.raises(DomainError) as error:
        services.extension("projects").rebind(
            Caller("owner", "owner-test"),
            dict(
                schema_version="1.0",
                operation_id="native-rebind",
                project_id="project-test",
                expected_revision=record["state_revision"],
                new_path=str(target),
            ),
        )
    assert error.value.code == "native_session_missing"
    assert services.registry.get("project-test")["path"] == str(root)
