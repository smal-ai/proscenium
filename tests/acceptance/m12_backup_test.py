"""Actual recovery defect: source SQLite/WAL/SHM bytes must not be changed."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from proscenium.api.errors import DomainError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.operations.recovery import BackupRegistry, existing_store
from tools.operations.safety import digest


def test_read_only_f00_backup_keeps_committed_wal_and_every_source_byte(tmp_path):
    source = tmp_path / "source.sqlite3"
    code = """
import os,sys
from pathlib import Path
from proscenium.db import Store
s=Store(Path(sys.argv[1]))
c=s.connect()
c.execute("INSERT INTO records VALUES('fixture','wal',0,'{\\"committed\\":true}')")
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", code, str(source)], check=True, timeout=10)
    assert source.with_name(source.name + "-wal").exists()
    before = {path.name: digest(path) for path in tmp_path.iterdir()}
    store = existing_store(source, "project-fixture", read_only=True)
    with store.transaction(write=False) as tx:
        assert tx.require("fixture", "wal") == {"committed": True}
    store.backup(tmp_path / "backup.sqlite3")  # The actual accepted F00 SQLite backup method.
    for name, sha in before.items():
        assert digest(tmp_path / name) == sha
    with closing(sqlite3.connect(tmp_path / "backup.sqlite3")) as db:
        assert json.loads(db.execute("SELECT body FROM records WHERE id='wal'").fetchone()[0]) == {
            "committed": True
        }
    with pytest.raises(DomainError) as error:
        with store.transaction() as tx:
            tx.put("fixture", "forbidden", {})
    assert error.value.code == "storage_unavailable"


def test_registry_settlement_reads_only_its_validated_bound_source(tmp_path):
    from proscenium.db import Store

    source = existing_store(
        Store(tmp_path / "project.sqlite3").path, "project-fixture", read_only=True
    )
    registry = BackupRegistry(
        existing_store(Store(tmp_path / "registry.sqlite3").path, read_only=True)
    )
    registry.project_source = source
    registry.assert_settled("project-fixture")
    with pytest.raises(ValueError, match="identity_mismatch"):
        registry.assert_settled("different-project")
