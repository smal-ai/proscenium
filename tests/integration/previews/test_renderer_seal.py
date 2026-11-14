"""Private renderer seal regressions. No Blender or model process is launched."""

import ast
from pathlib import Path

import pytest
from proscenium.previews import journal, worker


def test_seal_covers_entrypoint_and_all_direct_local_imports():
    directory = Path(journal.__file__).parent
    tree = ast.parse((directory / "blender_worker.py").read_text())
    local = {
        node.module + ".py"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and (directory / (node.module + ".py")).is_file()
    }
    assert set(journal.RENDERER_FILES) == local | {"blender_worker.py"}


@pytest.mark.parametrize("changed", journal.RENDERER_FILES)
def test_changed_local_dependency_refuses_native_spawn(tmp_path, monkeypatch, changed):
    installed = tmp_path / "installed"
    installed.mkdir()
    for name in journal.RENDERER_FILES:
        (installed / name).write_bytes((Path(journal.__file__).parent / name).read_bytes())
    hashes = journal.renderer_hashes(installed)
    (installed / changed).write_bytes(b"# changed after preparation\n")
    root = tmp_path / "attempt"
    root.mkdir()
    journal.atomic(
        root / "task.json",
        {
            "config": {"claim_path": str(tmp_path / "render.lock")},
            "renderer_files_sha256": hashes,
            "entrypoint_sha256": hashes["blender_worker.py"],
        },
    )
    original = journal.renderer_hashes
    monkeypatch.setattr(journal, "renderer_hashes", lambda directory=None: original(installed))
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))
    worker.supervise(root)
    terminal = journal.read(root / "terminal.json")
    assert terminal["state"] == "failed" and terminal["native_exit_code"] is None
    assert not (root / "native-process.json").exists()
    assert not (root / "renderer-verified.json").exists()


@pytest.mark.parametrize("seal", [None, {}, {"blender_worker.py": "old-entry-only"}])
def test_missing_legacy_or_incomplete_seal_is_not_backfilled(tmp_path, monkeypatch, seal):
    task = {
        "config": {"claim_path": str(tmp_path / "render.lock")},
        "entrypoint_sha256": "historical",
    }
    if seal is not None:
        task["renderer_files_sha256"] = seal
    journal.atomic(tmp_path / "task.json", task)
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))
    worker.supervise(tmp_path)
    assert journal.read(tmp_path / "task.json") == task
    terminal = journal.read(tmp_path / "terminal.json")
    assert terminal["state"] == "failed" and terminal["native_exit_code"] is None


def test_unchanged_complete_renderer_seal_verifies():
    hashes = journal.renderer_hashes()
    assert (
        journal.verify_renderer(
            {"renderer_files_sha256": hashes, "entrypoint_sha256": hashes["blender_worker.py"]}
        )
        == hashes
    )
