"""Offline allocation/reserve checks. These never start a native process."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "unity_input_proof", Path(__file__).resolve().parents[2] / "native/unity/input_proof.py"
)
assert SPEC is not None and SPEC.loader is not None
PROOF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROOF)


def test_disk_reserve_refuses_below_five_gib(tmp_path, monkeypatch):
    monkeypatch.setattr(
        PROOF.shutil, "disk_usage", lambda root: SimpleNamespace(free=PROOF.RESERVE - 1)
    )
    with pytest.raises(RuntimeError, match="5 GiB"):
        PROOF.reserve(tmp_path)


def test_disk_reserve_reports_actual_free_bytes(tmp_path, monkeypatch):
    free = PROOF.RESERVE + 12345
    monkeypatch.setattr(PROOF.shutil, "disk_usage", lambda root: SimpleNamespace(free=free))
    assert PROOF.reserve(tmp_path) == free


def test_followup_refuses_third_invocation_before_preparation(tmp_path, monkeypatch):
    (tmp_path / "DISPOSABLE-NATIVE-FIXTURE").touch()
    (tmp_path / "allocation.json").write_text(json.dumps({"unity_invocations": 2}))
    monkeypatch.setattr(PROOF, "reserve", lambda root: PROOF.RESERVE * 2)
    monkeypatch.setattr(
        PROOF, "prepare", lambda *a, **k: pytest.fail("No prepare after allocation exhausted")
    )
    monkeypatch.setattr(
        PROOF, "services_for", lambda *a, **k: pytest.fail("No runtime after allocation exhausted")
    )
    with pytest.raises(RuntimeError, match="allocation exhausted"):
        PROOF.run(tmp_path, Path("/not-launched/Unity"))


def test_followup_requires_disposable_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(PROOF, "reserve", lambda root: PROOF.RESERVE * 2)
    with pytest.raises(RuntimeError, match="disposable fixture marker"):
        PROOF.run(tmp_path, Path("/not-launched/Unity"))
