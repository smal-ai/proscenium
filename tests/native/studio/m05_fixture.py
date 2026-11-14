"""Labelled operator fixture only. No model authorship is claimed for setup copies."""

import hashlib
import json
import shutil
from pathlib import Path

from proscenium.auth import Caller
from proscenium.sources import closure_digest
from proscenium.sources.common import git

F01 = Path("/tmp/opencode/proscenium-f01-20261114-a")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply(services, run_root, command):
    project_id = services.registry.active_id()
    project = Path(services.registry.get(project_id)["path"])
    catalogue = services.extension("catalogue")
    if command["action"] == "materialise":
        result = {}
        for version in ("scene-main-v1", "scene-alternate-v1"):
            path = services.extension("sources").materialise(project_id, version)
            result[version] = {"path": str(path), "version": catalogue.version(project_id, version)}
        return result
    assert command["action"] == "seed"
    prior = run_root / "receipts/seed.json"
    if prior.exists():
        recorded = json.loads(prior.read_text())
        if recorded["status"] == "completed":
            return recorded["result"]
    original = json.loads((F01 / "fixture-evidence.json").read_text())
    copied = []
    for row in original["historical_files"]:
        source = F01 / "historical" / row["path"]
        assert sha(source) == row["sha256"]
        target = project / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append({"path": row["path"], "sha256": sha(target), "source_sha256": row["sha256"]})
    for source, target in (
        ("assets/cube/asset.blend", "assets/companion/asset.blend"),
        ("assets/cube/colour.png", "assets/companion/colour.png"),
        ("scenes/fixture/scene.blend", "scenes/alternate/scene.blend"),
    ):
        path = project / target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / source, path)
    helper = project / "tools/m05_blender.py"
    helper.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("m05_blender.py"), helper)
    paths = [r["path"] for r in copied] + [
        "assets/companion/asset.blend",
        "assets/companion/colour.png",
        "scenes/alternate/scene.blend",
        "tools/m05_blender.py",
    ]
    operation = "m05-labelled-setup"
    work_id, invocation_id = operation + "-work", operation + "-invocation"
    caller = Caller(
        "tool",
        "m05-fixture-not-sdk",
        project_id,
        work_id,
        services.ownership.epoch,
        operations=("checkpoint_sources", "publishOutputs"),
        invocation_id=invocation_id,
    )
    store = services.registry.project_store(project_id)
    with services.extension("previews").lock:
        services.gate.resume(
            project_id,
            reconciliation_evidence="Explicit M05 setup fixture, not a native model request",
        )
        with store.transaction() as tx:
            if tx.get("work", work_id, required=False) is None:
                tx.put(
                    "work",
                    work_id,
                    {
                        "schema_version": "1.0",
                        "project_id": project_id,
                        "work_id": work_id,
                        "originating_request_id": operation + "-request",
                        "directive_request_ids": [],
                        "generation_task_id": operation + "-task",
                        "native_session_id": "m05-fixture-not-a-native-session",
                        "job_ids": [],
                        "state": "running",
                        "state_revision": 0,
                    },
                    schema="WorkRecord",
                )
                tx.put(
                    "invocations",
                    invocation_id,
                    {
                        "work_id": work_id,
                        "owner_epoch": services.ownership.epoch,
                        "state": "running",
                        "fixture": "scripted setup, not SDK",
                    },
                )
        checkpoint = services.extension("sources").checkpoint(
            caller,
            {
                "schema_version": "1.0",
                "operation_id": operation + "-checkpoint",
                "project_id": project_id,
                "paths": paths,
                "message": "M05 explicitly imported F01 setup fixture; not model-authored",
                "expected_head": git(project, "rev-parse", "--verify", "HEAD", required=False)
                .decode()
                .strip()
                or None,
            },
        )
        commit = checkpoint["source_commit"]

        def closure(entry, files, dependencies):
            value = {
                "entry_path": entry,
                "files": [
                    {
                        "destination": p,
                        "origin": {
                            "kind": "git",
                            "repository_id": services.registry.get(project_id)["repository_id"],
                            "commit": commit,
                            "path": p,
                            "sha256": sha(project / p),
                        },
                    }
                    for p in sorted(files)
                ],
                "dependencies": dependencies,
                "required_runtime": {
                    "application": "blender",
                    "version": "4.5.14 LTS",
                    "features": ["linked-libraries", "external-textures"],
                },
            }
            value["digest"] = closure_digest(value)
            return value

        definitions = [
            (
                "asset-cube",
                "asset",
                "Reusable fixture cube",
                "assets/cube/asset.blend",
                ["assets/cube/asset.blend", "assets/cube/colour.png"],
                [],
            ),
            (
                "asset-companion",
                "asset",
                "Comparison companion",
                "assets/companion/asset.blend",
                ["assets/companion/asset.blend", "assets/companion/colour.png"],
                [],
            ),
            (
                "scene-main",
                "scene",
                "Pinned tabletop scene",
                "scenes/fixture/scene.blend",
                ["scenes/fixture/scene.blend", "assets/cube/asset.blend", "assets/cube/colour.png"],
                [{"item_id": "asset-cube", "version_id": "asset-cube-v1"}],
            ),
            (
                "scene-alternate",
                "scene",
                "Pinned alternate scene",
                "scenes/alternate/scene.blend",
                [
                    "scenes/alternate/scene.blend",
                    "assets/cube/asset.blend",
                    "assets/cube/colour.png",
                ],
                [{"item_id": "asset-cube", "version_id": "asset-cube-v1"}],
            ),
        ]
        items = []
        for item, kind, name, entry, files, deps in definitions:
            items.append(
                {
                    "item_id": item,
                    "kind": kind,
                    "name": name,
                    "version_id": item + "-v1",
                    "expected_current_version_id": None,
                    "source_commit": commit,
                    "source": closure(entry, files, deps),
                    "artifact_ids": [],
                    "preview_requests": [],
                    "description": "Reusable ochre textured one-metre cube for tabletop blocking"
                    if item == "asset-cube"
                    else "Explicitly imported F01 setup fixture",
                    "tags": ["amber-cube", "metre-fixture"]
                    if item == "asset-cube"
                    else ["setup-fixture"],
                    "provenance": {
                        "source_url": None,
                        "creator": None,
                        "licence": None,
                        "note": "Owner-authorised synthetic F01 import. Rights metadata unknown, not a publication gate.",
                    },
                }
            )
        body = {
            "schema_version": "1.0",
            "publication_id": operation + "-publication",
            "project_id": project_id,
            "items": items,
        }
        receipt, replayed = catalogue.publish(caller, body)
        with store.transaction() as tx:
            work = tx.require("work", work_id)
            work.update(state="completed", state_revision=work["state_revision"] + 1)
            tx.put(
                "work",
                work_id,
                work,
                expected_revision=tx.revision("work", work_id),
                schema="WorkRecord",
            )
            invocation = tx.require("invocations", invocation_id)
            invocation["state"] = "completed"
            tx.put(
                "invocations",
                invocation_id,
                invocation,
                expected_revision=tx.revision("invocations", invocation_id),
            )
        services.gate.close(project_id)
    return {
        "fixture": "explicit copied F01 data; not SDK effects",
        "copied": copied,
        "checkpoint": checkpoint,
        "submission": body,
        "receipt": receipt,
        "replayed": replayed,
    }
