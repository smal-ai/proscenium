"""Offline native-harness contract checks. Native process launch is replaced explicitly."""

import asyncio
import json

from native.image_to_3d import proof


def test_native_harness_uses_actual_project_profile_and_one_shared_reservation(
    tmp_path, monkeypatch
):
    root = tmp_path / "run"
    root.mkdir()
    monkeypatch.setattr(proof, "ROOT", root)
    report = {}
    services, _, caller, adapter = proof.prepare_services(report)
    try:
        assert report["job"]["kind"] == "mesh_generation"
        assert report["job"]["state"] == "planned"
        assert report["caller"]["invocation_id"] == caller.invocation_id == "e04-invocation"
        settings = services.extension("projects").settings(report["project"]["project_id"])
        assert settings["default_profile_id"] == "e04-native"
        assert settings["generation_attempt_limit"] == 1
        with services.registry.project_store(report["project"]["project_id"]).transaction(
            write=False
        ) as tx:
            rows = tx.connection.execute("SELECT * FROM budget_reservations").fetchall()
            assert len(rows) == 1 and rows[0]["state"] == "reserved"
            policy = tx.require("providers.tasks", "e04-generation-task")
            assert policy["limit"] == 1 and policy["targets"] == ["e04-generation-task"]
        assert report["reference"]["dimensions"] == {"width": 512, "height": 512}
        assert adapter.execution(report["project"]["project_id"], "e04-mesh-job") == {}
    finally:
        asyncio.run(adapter.close())
        services.close()


def test_harness_checkpoint_publication_contract_with_explicitly_fake_blender(
    tmp_path, monkeypatch
):
    from integration.image_to_3d.test_preparation import synthetic_glb

    root = tmp_path / "run"
    root.mkdir()
    monkeypatch.setattr(proof, "ROOT", root)
    report = {}
    services, catalogue, caller, adapter = proof.prepare_services(report)
    project_id = report["project"]["project_id"]
    artifacts = services.extension("artifacts")
    records = []
    for identity, data, kind, mime in (
        ("synthetic-model-output", synthetic_glb(), "source_blob", "model/gltf-binary"),
        ("synthetic-provenance", b'{"synthetic":true}', "document", "application/json"),
    ):
        artifact = artifacts.promote(
            project_id, identity, data, kind=kind, purpose="concept", media_type=mime
        )
        with services.registry.project_store(project_id).transaction() as tx:
            artifacts.put(tx, artifact)
        records.append(artifact)
    report["artifacts"] = records
    report["job"]["artifact_ids"] = [record["artifact_id"] for record in records]
    report["execution"] = {"graph": proof.graph(report["reference"]["artifact_id"])}

    def fake_import(argv, environment, project_root, report):
        # Deliberately fake bytes: source-service integration, never a native acceptance claim.
        (project_root / "assets/e04/asset.blend").write_bytes(b"SYNTHETIC NOT A BLENDER FILE")
        proof.write(root / "blender-report.json", {"blender_version": "5.0.1", "synthetic": True})
        return 0

    monkeypatch.setattr(proof, "run_import", fake_import)
    monkeypatch.setattr(proof.shutil, "which", lambda command: "/synthetic/blender")
    try:
        proof.import_publish(services, catalogue, caller, report)
        assert report["checkpoint"]["source_commit"]
        assert report["publication"]
        assert report["published_version"]["kind"] == "asset"
        assert (
            report["published_version"]["provenance"]["licence"]
            == "Tencent Hunyuan Community Licence"
        )
        materialised = services.extension("sources").materialise(project_id, "e04-asset-v1")
        assert (
            materialised / "assets/e04/asset.blend"
        ).read_bytes() == b"SYNTHETIC NOT A BLENDER FILE"
        assert all(
            row["origin"]["repository_id"] == report["project"]["repository_id"]
            for row in report["closure"]["files"]
        )
        assert json.loads((root / "blender-report.json").read_text())["synthetic"]
    finally:
        asyncio.run(adapter.close())
        services.close()
