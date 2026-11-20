"""Exercise the authorised Unity adapter with a real supervisor and fake Editor."""

import asyncio
import json
import sys

import pytest
from proscenium.api.errors import DomainError
from proscenium.catalogue import install as catalogue_install
from proscenium.configuration import Settings
from proscenium.copilot.workflows import arguments
from proscenium.projects.importer import import_project
from proscenium.services import Services
from proscenium.sources.common import git, sha
from proscenium.workflows import install as workflows_install
from test_project_importer import (
    IDENTITY,
    OWNER,
    P,
    admitted_tool,
    item,
    pin,
    publish_row,
    revised_source,
    source,
    write,
)

from tools.unity.jobs import _SUPERVISORS, attempt_directory


def test_authored_unity_validate_build_and_publish(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    project = "unity/walk"
    scene_path = "Assets/Scenes/Main.unity"
    workspace_scene = f"{project}/{scene_path}"
    settings_path = f"{project}/ProjectSettings/ProjectVersion.txt"
    editor_version = "6000.3.27f1"
    output = "Builds/Linux/Walk.x86_64"
    editor = tmp_path / "Unity-fake"
    editor.write_text(
        f"#!{sys.executable}\n"
        + """
import json
import sys
from pathlib import Path
a = sys.argv[1:]
assert "-batchmode" in a and "-nographics" in a and "-quit" in a
assert a[a.index("-job-worker-count") + 1] == "2"
project = Path(a[a.index("-projectPath") + 1])
assert Path.cwd() == project
Path(a[a.index("-logFile") + 1]).write_text(json.dumps(a))
method = a[a.index("-executeMethod") + 1] if "-executeMethod" in a else None
assert method in {None, "Walk.Editor.Check", "Walk.Editor.BuildLinux", "Walk.Editor.NoOutput"}
if method == "Walk.Editor.BuildLinux":
    output = project / "Builds/Linux/Walk.x86_64"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("fake player")
"""
    )
    editor.chmod(0o700)
    adapter = dict(
        editor_version=editor_version,
        pipeline="urp",
        scene_path=scene_path,
        validate_method="Walk.Editor.Check",
        build_method="Walk.Editor.BuildLinux",
        build_output=output,
    )
    target = write(root, workspace_scene, b"authored scene")
    meta = write(root, workspace_scene + ".meta", b"guid: preserve-this")
    configuration = write(root, settings_path, f"m_EditorVersion: {editor_version}\n".encode())
    adapter_path = f"{project}/proscenium-unity.json"
    adapter_file = write(root, adapter_path, adapter)
    player_path = f"{project}/Assets/Player.cs"
    player = write(root, player_path, b"// authored player code")
    player_meta = write(root, player_path + ".meta", b"guid: preserve-player")
    walk = item(
        "walk",
        "unity_result",
        source(
            target,
            meta,
            configuration,
            adapter_file,
            player,
            player_meta,
            app="unity",
            version=editor_version,
        ),
        unity=dict(
            editor_version=editor_version,
            pipeline="urp",
            scene_path=workspace_scene,
            player_script=player_path,
            validation=dict(status="not_run"),
        ),
    )
    manifest = dict(schema_version="1.0", import_id="walk-import", project=IDENTITY, items=[walk])
    services = Services.create(
        Settings(
            data_directory=tmp_path / "registry",
            free_space_reserve_bytes=0,
            runtime=dict(unity_editor=str(editor)),
            execution_profiles=frozenset({"unity-local"}),
        )
    )
    catalogue = catalogue_install(services)
    workflows = workflows_install(services)

    async def exercise():
        import_project(services, OWNER, root, manifest)
        services.activate_project(P)
        caller = admitted_tool(services)

        def body(action, suffix, scene=scene_path):
            return arguments(
                "run_unity_workflow",
                dict(
                    project=project,
                    operation_id=f"unity-{suffix}",
                    job_id=f"unity-job-{suffix}",
                    execution_profile_id="unity-local",
                    version=pin("walk"),
                    camera="perspective",
                    spawn=[0, 0, 0],
                    pipeline="urp",
                    action=action,
                    scene_path=scene,
                ),
            )

        async def run(action, suffix, scene=scene_path, state="succeeded"):
            request = body(action, suffix, scene)
            job = await workflows.unity(caller, request)
            assert job["state"] == "running"
            attempt = attempt_directory(root, job["attempt_id"])
            await asyncio.to_thread(_SUPERVISORS[job["attempt_id"]].wait, 10)
            result = await workflows.unity_result(
                caller, arguments("get_unity_result", {"job_id": job["job_id"]})
            )
            assert (await workflows.unity(caller, request))["state"] == state
            assert result == json.loads((attempt / "unity-result.json").read_bytes())
            assert (
                json.loads((attempt / "process.json").read_bytes())["attempt_id"]
                == job["attempt_id"]
            )
            assert json.loads((attempt / "terminal.json").read_bytes())["state"] == "complete"
            assert (
                result["scene_path"] == workspace_scene and result["player_script"] == player_path
            )
            assert "--proscenium-export" not in json.loads((attempt / "editor.log").read_bytes())
            return result

        assert (await run("validate", "validate"))["validation"]["status"] == "passed"
        update = write(root, workspace_scene, b"changed authored scene")
        result = await run("build", "build", workspace_scene)
        assert result["validation"]["status"] == "passed"
        assert (root / project / output).read_text() == "fake player"
        assert not (root / project / "Assets/Proscenium").exists()
        assert not (root / "unity/exports").exists()
        for entry in (meta, player, player_meta, configuration, adapter_file):
            assert sha((root / entry["path"]).read_bytes()) == entry["sha256"]

        base = catalogue.version(P, "walk-v1")
        closure, commit = revised_source(services, root, base, update, "Revise authored scene")
        revised = publish_row(base, "walk-v2", closure, commit)
        revised["unity"] = result
        publication, _ = catalogue.publish(
            caller,
            dict(
                schema_version="1.0", project_id=P, publication_id="walk-revision", items=[revised]
            ),
        )
        assert publication["versions"] == [pin("walk", "walk-v2")]
        assert catalogue.version(P, "walk-v2")["unity"] == result

        write(root, adapter_path, {**adapter, "build_method": "Walk.Editor.NoOutput"})
        # An old executable cannot prove this attempt succeeded.
        assert (await run("build", "stale", state="failed"))["validation"]["status"] == "failed"
        (root / adapter_path).unlink()
        assert "compilation only" in (await run("validate", "compile"))["validation"]["notes"]
        with pytest.raises(DomainError, match="needs a method and output"):
            await workflows.unity(caller, body("build", "no-adapter"))
        with pytest.raises(DomainError, match="inside this project's Assets"):
            await workflows.unity(
                caller, body("validate", "wrong-project", "unity/other/Assets/Main.unity")
            )
        lock = root / project / "Temp/UnityLockfile"
        lock.parent.mkdir(exist_ok=True)
        lock.touch()
        with pytest.raises(DomainError, match="project busy"):
            await workflows.unity(caller, body("validate", "editor-open"))
        lock.unlink()
        (root / settings_path).unlink()
        with pytest.raises(DomainError, match="ProjectVersion.txt is missing"):
            await workflows.unity(caller, body("validate", "missing-settings"))

    try:
        asyncio.run(exercise())
    finally:
        services.close()
