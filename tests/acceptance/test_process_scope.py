"""Deterministic inventories, not assumptions about the host's protected daemons."""

import json
import os
from pathlib import Path

import pytest

from tools.operations.safety import RecoveryError, assert_stopped


@pytest.fixture
def inventory(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    proc = tmp_path / "proc"
    process = proc / "999989"
    (process / "fd").mkdir(parents=True)
    (process / "stat").write_text("999989 (fixture) " + " ".join(["S"] + ["0"] * 18 + ["123"]))
    (process / "cmdline").write_bytes(b"sshd-session: fixture@pts/15\0")
    (process / "cwd").symlink_to(tmp_path)
    return root, proc, process


def protect(process, monkeypatch):
    original = os.readlink
    original_iterdir = Path.iterdir

    def readlink(path, *args, **kwargs):
        if Path(path).is_relative_to(process):
            raise PermissionError("Synthetic protected metadata")
        return original(path, *args, **kwargs)

    def iterdir(path):
        if path == process / "fd":
            raise PermissionError("Synthetic protected descriptors")
        return original_iterdir(path)

    monkeypatch.setattr(os, "readlink", readlink)
    monkeypatch.setattr(Path, "iterdir", iterdir)


def test_unassociated_protected_session_does_not_veto_backup(inventory, monkeypatch):
    root, proc, process = inventory
    protect(process, monkeypatch)
    assert_stopped([root], proc_root=proc)
    # No daemon allowlist: the same decision applies to an unknown unassociated OS process.
    (process / "cmdline").write_bytes(b"unrecognised-system-helper\0")
    assert_stopped([root], proc_root=proc)


def test_journal_owned_uninspectable_process_still_blocks(inventory, monkeypatch):
    root, proc, process = inventory
    record = root / "copilot-runtime/process.json"
    record.parent.mkdir()
    record.write_text(json.dumps({"pid": 999989, "start": "123"}))
    protect(process, monkeypatch)
    with pytest.raises(RecoveryError, match="owned_process"):
        assert_stopped([root], proc_root=proc)
    original = Path.read_text

    def denied(path, *args, **kwargs):
        if path == process / "stat":
            raise PermissionError("Known owned identity cannot be inspected")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(RecoveryError, match="owned_process_inspection_denied"):
        assert_stopped([root], proc_root=proc)


@pytest.mark.parametrize("kind", ["native", "preview", "unity"])
def test_corrupt_scoped_identity_is_not_silently_discarded(inventory, kind):
    root, proc, _ = inventory
    paths = {
        "native": "copilot-runtime/process.json",
        "preview": ".proscenium/jobs/previews/job/attempt/process.json",
        "unity": ".proscenium/jobs/unity/attempt/worker.json",
    }
    path = root / paths[kind]
    path.parent.mkdir(parents=True)
    identity = {"pid": 999989, "start_ticks": "corrupt"}
    path.write_text(json.dumps({"supervisor": identity} if kind == "unity" else identity))
    with pytest.raises(RecoveryError, match="corrupt_scoped_process_journal"):
        assert_stopped([root], proc_root=proc)


@pytest.mark.parametrize("association", ["argv", "cwd", "fd"])
def test_positive_project_use_still_blocks(inventory, association, monkeypatch):
    root, proc, process = inventory
    if association == "argv":
        (process / "cmdline").write_bytes(b"writer\0--root=" + bytes(root) + b"\0")
        protect(process, monkeypatch)
    elif association == "cwd":
        (process / "cwd").unlink()
        (process / "cwd").symlink_to(root)
    else:
        (process / "fd/4").symlink_to(root / "source.blend")
    with pytest.raises(RecoveryError, match="active_or_ambiguous"):
        assert_stopped([root], proc_root=proc)


def test_native_home_check_is_not_weakened_by_protected_process_rule(inventory):
    root, proc, process = inventory
    (process / "cmdline").write_bytes(b"copilot\0")
    for value in (b"", b"COPILOT_HOME=" + bytes(root) + b"\0"):
        (process / "environ").write_bytes(value)
        with pytest.raises(RecoveryError, match="active_or_ambiguous"):
            assert_stopped([root], proc_root=proc)
    (process / "environ").write_bytes(b"COPILOT_HOME=" + bytes(root.parent) + b"\0")
    with pytest.raises(RecoveryError):
        assert_stopped([root], proc_root=proc)
