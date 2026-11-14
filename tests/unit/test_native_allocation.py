"""The native test launcher must obtain an exclusive allocation before any command."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("native_runner", ROOT / "scripts/run_native.py")
assert SPEC is not None and SPEC.loader is not None
native_runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native_runner)


def test_os_claim_is_exclusive_and_released(tmp_path):
    path = tmp_path / "resource.lock"
    with native_runner.claim(path):
        with pytest.raises(BlockingIOError), native_runner.claim(path):
            pytest.fail("Competing allocation acquired an owned resource")
    with native_runner.claim(path):
        pass


def test_unknown_profile_does_not_run_or_create_output(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_native.py"), "unknown"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert not list(tmp_path.iterdir())


def test_profile_catalogue_has_explicit_limits_without_executing():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_native.py"), "--list"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    profiles = json.loads(result.stdout)
    assert profiles["blender-source"]["cloud_root_requests"] == 0
    assert profiles["blender-source"]["gpu"] is False
    assert 0 < profiles["blender-source"]["timeout_seconds"] <= 900
