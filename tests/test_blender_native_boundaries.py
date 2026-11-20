"""Exercise the saved-modifier and stdout boundaries without launching Blender."""

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from proscenium.previews import geometry_nodes
from proscenium.previews.journal import renderer_hashes, sha, verify_renderer
from proscenium.sources.blender_inspect import inspect


def particle_system(*, kind="HAIR", dynamics=False, external=False, disk=False):
    return SimpleNamespace(
        settings=SimpleNamespace(type=kind),
        use_hair_dynamics=dynamics,
        point_cache=SimpleNamespace(use_external=external, use_disk_cache=disk),
    )


def test_static_embedded_hair_is_ordinary():
    modifier = SimpleNamespace(type="PARTICLE_SYSTEM", particle_system=particle_system())
    assert geometry_nodes.modifier_kind(modifier) == "ordinary"
    assert geometry_nodes.reachable_geometry_groups(modifier) == []


@pytest.mark.parametrize(
    "system",
    [
        None,
        particle_system(kind="EMITTER"),
        particle_system(kind="UNKNOWN"),
        particle_system(dynamics=True),
        particle_system(external=True),
        particle_system(disk=True),
        SimpleNamespace(
            settings=None, use_hair_dynamics=False, point_cache=particle_system().point_cache
        ),
        SimpleNamespace(
            settings=SimpleNamespace(type="HAIR"), use_hair_dynamics=False, point_cache=None
        ),
        SimpleNamespace(
            settings=SimpleNamespace(type="HAIR"),
            use_hair_dynamics=False,
            point_cache=SimpleNamespace(use_external=False),
        ),
        particle_system(dynamics=0),
    ],
)
def test_particle_simulation_or_incomplete_evidence_is_refused(system):
    modifier = SimpleNamespace(type="PARTICLE_SYSTEM", particle_system=system)
    assert geometry_nodes.modifier_kind(modifier) == "unsupported_simulation_cache"


@pytest.mark.parametrize(
    "kind", ["PARTICLE_SYSTEM", "CLOTH", "FLUID", "DYNAMIC_PAINT", "SOFT_BODY"]
)
def test_bare_simulation_modifiers_are_refused(kind):
    assert (
        geometry_nodes.modifier_kind(SimpleNamespace(type=kind)) == "unsupported_simulation_cache"
    )


def test_renderer_seal_includes_the_installed_modifier_classifier():
    files = renderer_hashes()
    assert files["geometry_nodes.py"] == sha(Path(geometry_nodes.__file__).read_bytes())
    assert (
        verify_renderer(
            {"renderer_files_sha256": files, "entrypoint_sha256": files["blender_worker.py"]}
        )
        == files
    )


@pytest.mark.parametrize("missing_library", [False, True])
def test_inspection_writes_a_separate_flushed_receipt(tmp_path, monkeypatch, missing_library):
    (tmp_path / ".receipt.json").write_text(
        json.dumps(
            {
                "closure": {"entry_path": "scenes/room.blend"},
                "files": {},
            }
        )
    )
    bpy = SimpleNamespace(
        app=SimpleNamespace(version_string="4.5.16 LTS", build_hash=b"3f6797307da5"),
        data=SimpleNamespace(
            objects=[], images=[], libraries=[SimpleNamespace(is_missing=missing_library)]
        ),
        context=SimpleNamespace(scene=SimpleNamespace(objects=[])),
        utils=SimpleNamespace(blend_paths=lambda **_: []),
    )

    class ObservedStdout(io.StringIO):
        flushed = None

        def flush(self):
            self.flushed = self.getvalue()
            super().flush()

    stdout = ObservedStdout()
    stdout.write("Blender native banner without a newline")
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "bpy", bpy)
        patch.setattr(sys, "argv", ["blender", "--", str(tmp_path), "allocation-test"])
        patch.setattr(sys, "stdout", stdout)
        if missing_library:
            with pytest.raises(SystemExit) as stopped:
                inspect()
            assert stopped.value.code == 2
        else:
            inspect()

    captured = stdout.getvalue()
    assert stdout.flushed == captured and captured.endswith("\n")
    banner, receipt = captured.splitlines()
    assert banner == "Blender native banner without a newline"
    assert receipt.startswith("PROSCENIUM_SOURCE_INSPECTION=")
    result = json.loads(receipt.removeprefix("PROSCENIUM_SOURCE_INSPECTION="))
    assert result["allocation_id"] == "allocation-test"
    assert result["entry_path"] == "scenes/room.blend"
    assert result["missing"] == (["missing_linked_library"] if missing_library else [])
