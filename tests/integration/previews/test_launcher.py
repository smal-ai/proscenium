import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "w03_launcher", Path(__file__).resolve().parents[3] / "tools/blender/launch.py"
)
assert spec is not None and spec.loader is not None
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


@pytest.mark.parametrize(
    "dirty,outside,expected",
    [
        (False, False, "ready"),
        (True, False, "decision_required"),
        (False, True, "decision_required"),
        (True, True, "decision_required"),
    ],
)
def test_doctor_preserves_occupied_state(tmp_path, monkeypatch, dirty, outside, expected):
    monkeypatch.setattr(launch, "CLAIM_ROOT", tmp_path / "claims")
    monkeypatch.setattr(launch, "occupied", lambda: True)
    path = tmp_path.parent / "other-project/scene.blend" if outside else tmp_path / "scene.blend"

    def query(command, params=None):
        if command == "execute_code":
            assert params == {"code": launch.STATE_CODE}
            return {"result": json.dumps({"filepath": str(path), "is_dirty": dirty})}
        return {"diagnostic": command}

    monkeypatch.setattr(launch, "query", query)
    monkeypatch.setattr(
        launch.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("Occupied bridge must never be replaced"),
    )
    assert launch.start(tmp_path)["state"] == expected


def test_unverified_occupied_bridge_is_not_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "occupied", lambda: True)

    def fail(*args, **kwargs):
        raise TimeoutError("synthetic bridge timeout")

    monkeypatch.setattr(launch, "query", fail)
    assert launch.doctor(tmp_path)["state"] == "occupied_unverified"


def test_doctor_rejects_generic_code_transport():
    with pytest.raises(ValueError):
        launch.query("execute_code", {"code": "bpy.ops.wm.save_as_mainfile()"})
