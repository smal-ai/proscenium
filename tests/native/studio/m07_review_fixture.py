"""Read-only accepted backup and isolated restore of the completed M07 fixture."""

import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from proscenium.db import Store
from proscenium.db.registry import Registry

from tools.operations.recovery import BackupSource, write_json
from tools.operations.safety import assert_stopped, locks

ORIGINAL = (
    Path.home()
    / ".cache/proscenium-development/image-generation-light/runs/m07-fp16-execution-20261114"
)
PROJECT_ID = "project-2d8c0aea-e3de-41cf-9c23-1d0108fd8bb8"
REPO = Path(__file__).resolve().parents[3]
PROTECTED = (
    "jobs",
    "attempts",
    "requests",
    "contexts",
    "job_inputs",
    "job_evidence",
    "providers.execution",
    "providers.submissions",
    "providers.tasks",
    "selections",
    "review.selection_history",
    "review.selection_slots",
    "versions",
    "items",
    "artifacts",
    "publications",
    "work",
    "invocations",
)


def protected(project):
    with locks([project.parent / "machine/owner.lock", project / ".proscenium/owner.lock"]):
        assert_stopped([project, project.parent / "machine"])
        store = BackupSource(project / ".proscenium/state.sqlite3", PROJECT_ID)
        with store.transaction(write=False) as tx:
            rows = {name: tx.all(name) for name in PROTECTED}
            rows["budget_reservations"] = [
                dict(row)
                for row in tx.connection.execute("SELECT * FROM budget_reservations ORDER BY rowid")
            ]
    return {
        name: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        for name, value in rows.items()
    }


def inventory():
    paths = [p for p in ORIGINAL.iterdir() if p.is_file()]
    for directory in ("project", "machine"):
        paths.extend(p for p in (ORIGINAL / directory).rglob("*") if p.is_file())
    result = {}
    for path in sorted(paths):
        with path.open("rb") as stream:
            result[path.relative_to(ORIGINAL).as_posix()] = hashlib.file_digest(
                stream, "sha256"
            ).hexdigest()
    return result


def prepare(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    before = inventory()
    write_json(root / "original-before.private.json", before)
    # The known synthetic setup orphan intentionally remains unchanged. This is
    # an explicitly authorised read-only import, not an ordinary settled backup.
    report = isolated_import(root)
    method = "read_only_sqlite_backup_and_disposable_import"
    config = json.loads((REPO / "config.example.json").read_text())
    config.update(
        data_directory=report["data_directory"],
        frontend_directory=str(REPO / "apps/frontend/dist"),
        origin="http://127.0.0.1:18969",
        services=[],
        execution_profiles=[],
        provider_options={},
        rendering={},
    )
    config["runtime"].update(
        cli_path="/nonexistent/m07-review-native-unavailable",
        skill_directories=[],
        mcp_servers={},
    )
    write_json(root / "config.json", config)
    after = inventory()
    assert before == after, "Original fixture changed during backup/restore"
    identities = protected(ORIGINAL / "project")
    assert identities == protected(Path(report["path"]))
    write_json(root / "protected-before.json", identities)
    write_json(
        root / "preparation.json",
        {
            "project_id": PROJECT_ID,
            "original": str(ORIGINAL),
            "restored": report,
            "method": method,
            "original_files_checked": len(before),
            "original_unchanged": True,
        },
    )


def isolated_import(root):
    source = ORIGINAL / "project"
    machine = ORIGINAL / "machine"
    destination = root / "restored"
    with locks([machine / "owner.lock", source / ".proscenium/owner.lock"]):
        assert_stopped([source, machine])
        store = BackupSource(source / ".proscenium/state.sqlite3", PROJECT_ID)
        with store.transaction(write=False) as tx:
            blockers = [
                row
                for row in tx.all("invocations")
                if row.get("state") not in {"completed", "failed", "cancelled"}
            ]
            assert blockers == [
                {
                    "owner_epoch": "epoch-50a9a590-2b7f-4001-b0c8-2b33ec2daf02",
                    "state": "running",
                    "work_id": "m07-setup-work",
                }
            ]
            assert all(row["state"] == "completed" for row in tx.all("work"))
            assert all(row["state"] == "succeeded" for row in tx.all("jobs"))
        shutil.copytree(
            source,
            destination / "project",
            ignore=shutil.ignore_patterns("state.sqlite3*", "owner.lock"),
        )
        store.backup(destination / "project/.proscenium/state.sqlite3")
        (destination / "machine").mkdir(mode=0o700)
        BackupSource(machine / "registry.sqlite3").backup(destination / "machine/registry.sqlite3")
        native = "copilot-runtime/home/session-state/ebd1aede-27fc-492f-80e1-7c461531bd15"
        shutil.copytree(machine / native, destination / "machine" / native)
        assert_stopped([source, machine])
    # Apply the accepted recovery sanitisation and fresh-registry rebind, never
    # copying active browser credentials or altering historical project records.
    with sqlite3.connect(destination / "machine/registry.sqlite3") as db:
        db.execute(
            "DELETE FROM records WHERE NOT ((namespace='projects' AND id=?) OR "
            "(namespace='private' AND id=?))",
            (PROJECT_ID, f"settings:{PROJECT_ID}"),
        )
        for table in ("operations", "events", "outbox", "budget_reservations", "resource_claims"):
            db.execute(f"DELETE FROM {table}")
    registry = Registry(Store(destination / "machine/registry.sqlite3"))
    project = registry.get(PROJECT_ID)
    with registry.store.transaction() as tx:
        tx.put(
            "projects",
            PROJECT_ID,
            {
                **project,
                "path": str(destination / "project"),
                "state": "inactive",
                "state_revision": project["state_revision"] + 1,
            },
            expected_revision=tx.revision("projects", PROJECT_ID),
            schema="ProjectRecord",
        )
    assert registry.active_id() is None and registry.get(PROJECT_ID)["state"] == "inactive"
    report = {
        "project_id": PROJECT_ID,
        "path": str(destination / "project"),
        "data_directory": str(destination / "machine"),
        "original_path": str(source),
        "execution": "closed_read_only_import_requires_explicit_activation",
        "initial_registry_active_id": None,
        "initial_registry_project_state": "inactive",
        "limits": ["original_scripted_setup_invocation_running"],
        "preserved_blockers": blockers,
    }
    write_json(root / "import-report.json", report)
    return report


if __name__ == "__main__":
    prepare(Path(sys.argv[1]).resolve())
