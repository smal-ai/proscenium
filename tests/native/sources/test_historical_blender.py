"""Opt-in real resolver/LFS/Blender proof, never part of offline acceptance."""

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from proscenium.api.errors import DomainError
from proscenium.catalogue import install
from proscenium.configuration import Settings
from proscenium.db.store import now
from proscenium.services import Services
from proscenium.sources import closure_digest
from proscenium.sources.common import sha
from proscenium.sources.inspection import BlenderInspectionAllocation, inspect_blender

pytestmark = pytest.mark.native


def test_real_historical_resolver_lfs_reopen_and_undeclared_texture(tmp_path):
    fixture = os.environ.get("PROSCENIUM_W02_NATIVE_FIXTURE")
    if not fixture:
        pytest.skip("Requires an explicitly allocated disposable F01 native source fixture")
    original = Path(fixture).resolve(strict=True)
    metadata = json.loads((original.parent / "fixture-evidence.json").read_bytes())
    root = tmp_path / "project"
    shutil.copytree(
        original,
        root,
        ignore=shutil.ignore_patterns(".env*", ".proscenium", "*.blend1", "*.blend2"),
    )
    services = Services.create(
        Settings(data_directory=tmp_path / "machine", free_space_reserve_bytes=0)
    )
    services.registry.register(
        dict(
            schema_version="1.0",
            project_id="native-project",
            repository_id="native-repository",
            name="Disposable W02 native proof",
            path=str(root),
            native_session_id=None,
            state="inactive",
            state_revision=0,
        )
    )
    services.activate_project("native-project")
    install(services)
    executable = shutil.which("blender")
    assert executable
    allocation = BlenderInspectionAllocation("w02-source-native-2026-11-14", Path(executable))
    manifest = dict(
        entry_path="scenes/fixture/scene.blend",
        files=[],
        dependencies=[],
        required_runtime=dict(
            application="blender",
            version=metadata["blender"],
            features=["linked-libraries", "external-textures"],
        ),
        digest="0" * 64,
    )
    reports = {}
    try:
        for label, key in [("historical", "historical_files"), ("current", "current_files")]:
            revision = (
                metadata["asset_v1_commit"]
                if label == "historical"
                else metadata["asset_v2_commit"]
            )
            manifest["files"] = [
                dict(
                    destination=row["path"],
                    origin=dict(
                        kind="git",
                        repository_id="native-repository",
                        commit=revision,
                        path=row["path"],
                        sha256=row["sha256"],
                    ),
                )
                for row in metadata[key]
            ]
            manifest["digest"] = closure_digest(manifest)
            materialised = services.extension("sources").resolve("native-project", manifest)
            report = inspect_blender(
                services,
                "native-project",
                materialised,
                allocation,
                expected_digest=manifest["digest"],
            )
            reports[label] = report
            assert report["missing"] == []
            assert "assets/cube/asset.blend" in report["dependencies"]
            assert "assets/cube/colour.png" in report["dependencies"]
        historical_z = sorted(
            round(dimensions[2], 4)
            for name, dimensions in reports["historical"]["object_dimensions"].items()
            if "cube" in name.lower()
        )
        current_z = sorted(
            round(dimensions[2], 4)
            for name, dimensions in reports["current"]["object_dimensions"].items()
            if "cube" in name.lower()
        )
        assert historical_z and historical_z != current_z
        manifest["files"] = [
            row for row in manifest["files"] if not row["destination"].endswith("colour.png")
        ]
        manifest["digest"] = closure_digest(manifest)
        materialised = services.extension("sources").resolve("native-project", manifest)
        with pytest.raises(DomainError) as error:
            inspect_blender(services, "native-project", materialised, allocation)
        assert error.value.code == "dependency_missing"
        evidence = dict(
            recorded_at=datetime.fromisoformat(now())
            .astimezone(timezone(timedelta(hours=2)))
            .isoformat(),
            allocation_id=allocation.allocation_id,
            scope="Actual production resolver, local Git LFS hydration and allocated CPU Blender reopen. No rendering, model or GPU claims.",
            original_fixture=str(original),
            disposable_project=str(root),
            fixture_commits={
                "historical": metadata["asset_v1_commit"],
                "current": metadata["asset_v2_commit"],
            },
            inspections=reports,
            missing_texture_error=error.value.code,
            source_hashes={
                name: sha(
                    (
                        Path(__file__).resolve().parents[3]
                        / "apps/backend/src/proscenium/sources"
                        / name
                    ).read_bytes()
                )
                for name in ("common.py", "resolver.py", "inspection.py", "blender_inspect.py")
            },
        )
        destination = os.environ.get("PROSCENIUM_W02_EVIDENCE")
        if destination:
            Path(destination).write_text(json.dumps(evidence, indent=2) + "\n")
    finally:
        services.close()
