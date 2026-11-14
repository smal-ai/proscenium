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
    monkeypatch.setattr(launch, "occupied", lambda **kwargs: True)
    path = tmp_path.parent / "other-project/scene.blend" if outside else tmp_path / "scene.blend"

    def query(command, params=None, **kwargs):
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
    monkeypatch.setattr(launch, "occupied", lambda **kwargs: True)

    def fail(*args, **kwargs):
        raise TimeoutError("synthetic bridge timeout")

    monkeypatch.setattr(launch, "query", fail)
    assert launch.doctor(tmp_path)["state"] == "occupied_unverified"


def test_doctor_rejects_generic_code_transport():
    with pytest.raises(ValueError):
        launch.query("execute_code", {"code": "bpy.ops.wm.save_as_mainfile()"})


@pytest.mark.parametrize(
    "host,port",
    [
        ("0.0.0.0", 9877),
        ("192.0.2.1", 9877),
        ("localhost", 9877),
        ("127.0.0.1", 0),
        ("127.0.0.1", 65536),
    ],
)
def test_bridge_endpoint_is_trusted_loopback_only(host, port):
    with pytest.raises(ValueError):
        launch.endpoint(host, port)


def test_custom_endpoint_requires_factory_isolation(tmp_path):
    with pytest.raises(ValueError, match="isolated"):
        launch.start(tmp_path, port=9877)


def test_uncertain_launch_receipt_prevents_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "CLAIM_ROOT", tmp_path / "claims")
    monkeypatch.setattr(launch, "occupied", lambda **kwargs: False)
    destination = tmp_path / ".proscenium/tool-output/blender"
    destination.mkdir(parents=True)
    previous = {"pid": 999999, "process_start": "123", "port": 9877}
    (destination / "launch.json").write_text(json.dumps(previous))
    monkeypatch.setattr(
        launch.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("Never relaunch an uncertain original"),
    )
    result = launch.start(tmp_path, port=9877, isolated=True)
    assert result["state"] == "outcome_uncertain"
    assert result["launched"] == previous


def test_doctor_targets_only_configured_port(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(launch, "occupied", lambda **kwargs: calls.append(kwargs) or False)
    assert launch.doctor(tmp_path, port=9877)["bridge"] == "127.0.0.1:9877"
    assert calls == [{"host": "127.0.0.1", "port": 9877}]


def test_spawn_ack_loss_is_fenced_across_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "CLAIM_ROOT", tmp_path / "claims")
    monkeypatch.setattr(launch, "occupied", lambda **kwargs: False)
    monkeypatch.setattr(launch.shutil, "which", lambda name: f"/installed/{name}")
    monkeypatch.setattr(
        launch.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic lost spawn acknowledgement")),
    )
    with pytest.raises(RuntimeError, match="acknowledgement"):
        launch.start(tmp_path / "first", port=9877, isolated=True)
    result = launch.start(tmp_path / "second", port=9877, isolated=True)
    assert result["state"] == "outcome_uncertain"
    assert result["launched"]["project"] == str(tmp_path / "first")
    assert result["launched"]["state"] == "launch_prepared"
    script = (tmp_path / "first/.proscenium/tool-output/blender/isolated-bootstrap.py").read_text()
    assert "default_set=False" in script and "port=9877" in script
    assert "save_userpref" not in script
