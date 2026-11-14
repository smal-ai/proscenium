"""Explicitly authorised, bounded F05 proof. Never collected as an automatic native test.

SourceService is an explicitly labelled fixture adapter over a copied historical F01
materialisation. Job admission/attempts/resources use real accepted F00 services/SQLite.
No W02 uncommitted code or private assets are imported.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.services import Services

from tools.unity.jobs import run_unity_job
from tools.unity.worker import stop
from tools.unity.workflow import (
    HERE,
    input_digest,
    make_recipe,
    prepare,
    sha256,
    verify_materialisation,
    write_json,
)


def initialise(root: Path, historical: Path) -> None:
    import shutil

    root.mkdir(exist_ok=False)
    materialised = root / "sealed"
    shutil.copytree(historical, materialised)
    paths = sorted(
        path
        for path in materialised.rglob("*")
        if path.is_file() and path.suffix in {".blend", ".png"}
    )
    closure: dict[str, Any] = dict(
        entry_path="scenes/fixture/scene.blend",
        files=[
            dict(
                destination=path.relative_to(materialised).as_posix(),
                origin=dict(
                    kind="retained",
                    artifact_id="fixture-" + sha256(path)[:24],
                    sha256=sha256(path),
                ),
            )
            for path in paths
        ],
        dependencies=[],
        required_runtime=dict(application="Blender", version="4.5.14 LTS", features=[]),
    )
    # All keys/values here are ASCII and numbers are absent, matching RFC8785 JSON.
    closure["digest"] = input_digest(closure)
    write_json(root / "closure.json", closure)
    entry = verify_materialisation(materialised, closure)
    with (root / "blender-export.log").open("w") as log:
        process = subprocess.Popen(
            [
                "blender",
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--threads",
                "4",
                str(entry),
                "--python-exit-code",
                "1",
                "--python",
                str(HERE / "export_blender.py"),
                "--",
                str(materialised),
                str(root / "environment.json"),
                closure["digest"],
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            stop(process)
            raise
    if code:
        raise RuntimeError("Blender export failed. Preserve blender-export.log")
    project = prepare(root, "unity/Fixture", tests=True)
    write_json(
        project / "Packages/manifest.json",
        dict(
            dependencies={
                "com.unity.inputsystem": "1.20.1",
                "com.unity.render-pipelines.universal": "17.3.0",
                "com.unity.test-framework": "1.6.0",
                "com.unity.modules.physics": "1.0.0",
                "com.unity.modules.jsonserialize": "1.0.0",
            }
        ),
    )
    (project / "ProjectSettings").mkdir()
    (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.27f1\n")
    recipe = make_recipe(
        root,
        "unity/Fixture",
        source_id="F01-historical-environment",
        source_version=dict(item_id="fixture-source", version_id="fixture-source-v1"),
        closure=closure,
        export_path="environment.json",
        spawn=(0, 0.92, -2),
        pipeline="urp",
    )
    write_json(root / "recipe.json", recipe)
    write_json(
        root / "allocation.json", dict(blender_invocations=1, unity_invocations=0, maximum_unity=3)
    )


def execute(root: Path, editor: Path, action: str) -> None:
    recipe = json.loads((root / "recipe.json").read_text())
    allocation = json.loads((root / "allocation.json").read_text())
    if allocation["unity_invocations"] >= 3:
        raise RuntimeError("Native allocation exhausted")
    services = Services.create(
        Settings(
            data_directory=root / "machine",
            execution_profiles=frozenset({"unity-f05"}),
            free_space_reserve_bytes=0,
        )
    )
    try:
        services.registry.register(
            dict(
                schema_version="1.0",
                project_id="fixture-unity",
                repository_id="fixture-repository",
                name="Disposable F05 native proof",
                path=str(root),
                native_session_id=None,
                state="inactive",
                state_revision=0,
            )
        )
        services.registry.activate("fixture-unity")
        services.acquire_project("fixture-unity")
        services.gate.resume(
            "fixture-unity", reconciliation_evidence="explicit-bounded-F05-test-allocation"
        )
        services.extensions["sources"] = SimpleNamespace(
            materialise=lambda project_id, version_id: root / "sealed"
        )
        with services.registry.project_store("fixture-unity").transaction() as tx:
            if not tx.get("work", "fixture-work", required=False):
                tx.put(
                    "work",
                    "fixture-work",
                    dict(
                        schema_version="1.0",
                        work_id="fixture-work",
                        project_id="fixture-unity",
                        originating_request_id="fixture-request",
                        directive_request_ids=[],
                        state="running",
                        state_revision=0,
                        native_session_id="fixture-no-model-session",
                        job_ids=[],
                        generation_task_id="fixture-task",
                    ),
                    schema="WorkRecord",
                )
                tx.put(
                    "versions",
                    "fixture-source-v1",
                    dict(item_id="fixture-source", source=recipe["source"]),
                )
        caller = Caller(
            "tool",
            "fixture-worker",
            "fixture-unity",
            "fixture-work",
            services.ownership.epoch,
            operations=("admitToolJob", "updateJobState"),
        )
        number = allocation["unity_invocations"] + 1
        admission = dict(
            schema_version="1.0",
            operation_id=f"fixture-operation-{number}",
            project_id="fixture-unity",
            job_id=f"fixture-unity-{number}",
            kind="unity",
            target=dict(
                target_id="fixture-playable", version=recipe["source_version"], shot_id=None
            ),
            input_digest=input_digest(dict(project="unity/Fixture", recipe=recipe, action=action)),
            input_artifact_ids=[],
            execution_profile_id="unity-f05",
        )
        allocation["unity_invocations"] = number
        write_json(root / "allocation.json", allocation)
        prepare(root, "unity/Fixture", tests=True)
        result = run_unity_job(
            services,
            caller,
            admission,
            project="unity/Fixture",
            recipe=recipe,
            editor=editor,
            action=action,
            timeout=600,
        )
        if result.get("blocked"):
            allocation["unity_invocations"] = number - 1
            write_json(root / "allocation.json", allocation)
        write_json(root / f"result-{number}.json", result)
        print(json.dumps(result, indent=2))
    finally:
        services.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["initialise", "repeat-proof", "import", "playtest"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--historical", type=Path)
    parser.add_argument("--editor", type=Path)
    args = parser.parse_args()
    if not str(args.root).startswith("/tmp/opencode/proscenium-unity-"):
        parser.error("Proof root must be a fresh /tmp/opencode/proscenium-unity-* directory")
    if args.action == "initialise":
        if args.historical is None:
            parser.error("--historical requires explicitly authorised F01 materialisation")
        initialise(args.root, args.historical)
    else:
        if args.editor is None:
            parser.error("--editor requires an explicitly allocated official Editor")
        execute(args.root, args.editor, args.action)


if __name__ == "__main__":
    main()
