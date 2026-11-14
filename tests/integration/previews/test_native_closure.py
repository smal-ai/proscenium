"""Dependency traversal guards with explicit offline bpy doubles, not native evidence."""

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from proscenium.previews.journal import sha


@pytest.mark.parametrize("fault", ["outside", "undeclared", "cache", "missing_geometry_group"])
def test_native_entrypoint_rejects_unadmitted_dependencies(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(sys, "path", list(sys.path))
    module = runpy.run_path(
        str(
            Path(__file__).resolve().parents[3]
            / "apps/backend/src/proscenium/previews/blender_worker.py"
        )
    )
    entry = tmp_path / "scene.blend"
    entry.write_bytes(b"OFFLINE DOUBLE ONLY")
    closure = {
        "files": [{"destination": "scene.blend", "origin": {"sha256": sha(entry.read_bytes())}}]
    }
    data = NS(
        images=[],
        node_groups=[],
        materials=[],
        worlds=[],
        lights=[],
        scenes=[],
        libraries=[],
        cache_files=[object()] if fault == "cache" else [],
        volumes=[],
    )
    paths = (
        [str(tmp_path.parent / "outside.png")]
        if fault == "outside"
        else [str(tmp_path / "undeclared.png")]
        if fault == "undeclared"
        else []
    )
    fake = NS(
        data=data,
        utils=NS(blend_paths=lambda **kwargs: paths),
        path=NS(abspath=lambda raw, **kwargs: raw),
    )
    scene = NS(
        objects=[NS(modifiers=[NS(type="NODES")])] if fault == "missing_geometry_group" else []
    )
    with pytest.raises(
        ValueError,
        match="unsupported_geometry_nodes"
        if fault == "missing_geometry_group"
        else "dependency_missing",
    ):
        module["dependencies"](fake, tmp_path, closure, scene)
