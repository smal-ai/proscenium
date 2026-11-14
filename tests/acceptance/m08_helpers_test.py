"""Offline proof-helper checks. No SDK, browser, Blender, provider or generated frames."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from proscenium.contracts import validate
from proscenium.sources.resolver import closure_digest

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "tests/native/previews/m08_contract.py"


def command(root, *args):
    return subprocess.check_output(args, cwd=root, stderr=subprocess.STDOUT)


def repository(root):
    command(root, "git", "init", "-b", "main")
    command(root, "git", "config", "user.name", "Offline M08 fixture")
    command(root, "git", "config", "user.email", "m08@example.invalid")
    (root / "project.json").write_text(json.dumps({"repository_id": "offline-repository"}))


def test_closure_reads_committed_document_not_later_working_bytes(tmp_path):
    repository(tmp_path)
    data = b'{"label":"explicit offline document"}\n'
    (tmp_path / "plan.json").write_bytes(data)
    command(tmp_path, "git", "add", "plan.json")
    command(tmp_path, "git", "commit", "-m", "test: offline document")
    commit = command(tmp_path, "git", "rev-parse", "HEAD").decode().strip()
    (tmp_path / "plan.json").write_bytes(b"changed working bytes")
    closure = json.loads(
        command(
            tmp_path,
            sys.executable,
            str(HELPER),
            commit,
            "plan.json",
            "plan.json",
            "--dependency",
            "scene-item",
            "scene-version",
        )
    )
    validate("SourceClosure", closure)
    assert closure["digest"] == closure_digest(closure)
    assert closure["files"][0]["origin"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert closure["dependencies"] == [{"item_id": "scene-item", "version_id": "scene-version"}]


def test_closure_hydrates_only_own_exact_lfs_object(tmp_path):
    repository(tmp_path)
    data = b"explicit synthetic LFS hydration bytes, not a native scene or frame"
    oid = hashlib.sha256(data).hexdigest()
    target = tmp_path / ".git/lfs/objects" / oid[:2] / oid[2:4] / oid
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    (tmp_path / "source.dat").write_text(
        f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(data)}\n"
    )
    command(tmp_path, "git", "add", "source.dat")
    command(tmp_path, "git", "commit", "-m", "test: explicit offline LFS pointer")
    commit = command(tmp_path, "git", "rev-parse", "HEAD").decode().strip()
    closure = json.loads(
        command(tmp_path, sys.executable, str(HELPER), commit, "source.dat", "source.dat")
    )
    assert closure["files"][0]["origin"]["sha256"] == oid
    target.write_bytes(b"corrupt offline object")
    result = subprocess.run(
        [sys.executable, str(HELPER), commit, "source.dat", "source.dat"],
        cwd=tmp_path,
        capture_output=True,
    )
    assert result.returncode != 0


def test_native_hold_rejects_missing_accepted_fix_before_creating_a_run(tmp_path):
    target = tmp_path / "must-not-be-created"
    result = subprocess.run(
        [sys.executable, str(REPO / "tests/native/previews/m08_host.py"), "run", str(target)],
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO)},
        capture_output=True,
    )
    assert (
        result.returncode != 0
        and b"coordinator must supply the exact accepted W01 permission fix" in result.stderr
    )
    assert not target.exists()
