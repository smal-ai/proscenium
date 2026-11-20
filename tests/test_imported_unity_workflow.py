"""Exercise the authorised Unity adapter with a real supervisor and fake Editor."""

import asyncio
import json
import subprocess
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
from tools.unity.worker import __file__ as worker_script


def test_terminal_validation_reconciliation_preserves_the_next_build_claim(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    project = "unity/walk"
    scene = write(root, f"{project}/Assets/Main.unity", b"scene")
    editor_version = "6000.3.27f1"
    settings = write(
        root,
        f"{project}/ProjectSettings/ProjectVersion.txt",
        f"m_EditorVersion: {editor_version}\n".encode(),
    )
    adapter = write(
        root,
        f"{project}/proscenium-unity.json",
        dict(
            validate_method="Walk.Editor.Validate",
            build_method="Walk.Editor.Build",
            build_output="Builds/Player.x86_64",
        ),
    )
    gate = root / project / "release-fake-build"
    editor = tmp_path / "Unity-fake"
    editor.write_text(
        f"#!{sys.executable}\n"
        + """
import sys, time
from pathlib import Path
if sys.argv[sys.argv.index("-executeMethod") + 1] == "Walk.Editor.Build":
    deadline = time.monotonic() + 20
    while not Path("release-fake-build").is_file():
        if time.monotonic() >= deadline:
            raise SystemExit(2)
        time.sleep(0.01)
    output = Path("Builds/Player.x86_64")
    output.parent.mkdir(exist_ok=True)
    output.write_bytes(b"new player")
"""
    )
    editor.chmod(0o700)
    services = Services.create(
        Settings(
            data_directory=tmp_path / "registry",
            free_space_reserve_bytes=0,
            runtime=dict(unity_editor=str(editor)),
            execution_profiles=frozenset({"unity-local"}),
        )
    )
    catalogue_install(services)
    workflows = workflows_install(services)

    def claims():
        with services.resources.store.transaction(write=False) as tx:
            return [
                tuple(row)
                for row in tx.connection.execute(
                    "SELECT resource, project_id, job_id, attempt_id FROM resource_claims"
                )
            ]

    async def exercise():
        import_project(
            services,
            OWNER,
            root,
            dict(
                schema_version="1.0",
                import_id="claim-demo",
                project=IDENTITY,
                items=[
                    item(
                        "walk",
                        "unity_result",
                        source(scene, settings, adapter, app="unity", version=editor_version),
                        unity=dict(
                            editor_version=editor_version,
                            pipeline="urp",
                            scene_path=scene["path"],
                            validation=dict(status="not_run"),
                        ),
                    )
                ],
            ),
        )
        services.activate_project(P)
        caller = admitted_tool(services)

        async def launch(action):
            return await workflows.unity(
                caller,
                arguments(
                    "run_unity_workflow",
                    dict(
                        project=project,
                        operation_id=f"op-{action}",
                        job_id=f"job-{action}",
                        execution_profile_id="unity-local",
                        version=pin("walk"),
                        camera="perspective",
                        spawn=[0, 0, 0],
                        pipeline="urp",
                        action=action,
                    ),
                ),
            )

        validate_job = await launch("validate")
        resource, _, _, _ = claims()[0]
        await asyncio.to_thread(_SUPERVISORS[validate_job["attempt_id"]].wait, 10)
        assert [(job["job_id"], job["state"]) for job in await workflows.reconcile(P)] == [
            ("job-validate", "succeeded")
        ]
        assert claims() == []

        # A terminal attempt may still need to release its own claim after a restart.
        assert services.resources.acquire(
            resource, P, validate_job["job_id"], validate_job["attempt_id"]
        )
        await workflows.reconcile(P)
        assert claims() == []

        build = await launch("build")
        process = _SUPERVISORS[build["attempt_id"]]
        try:
            assert build["state"] == "running"
            expected_claim = [(resource, P, build["job_id"], build["attempt_id"])]
            assert claims() == expected_claim
            worker = attempt_directory(root, build["attempt_id"]) / "worker.json"

            async def wait_for_running_worker():
                while not worker.exists() or json.loads(worker.read_bytes())["state"] != "running":
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_for_running_worker(), timeout=10)
            observed = {job["job_id"]: job["state"] for job in await workflows.reconcile(P)}
            assert observed == {"job-validate": "succeeded", "job-build": "running"}
            assert claims() == expected_claim
        finally:
            gate.touch()
            await asyncio.to_thread(process.wait, 10)
        observed = {job["job_id"]: job["state"] for job in await workflows.reconcile(P)}
        assert observed == {"job-validate": "succeeded", "job-build": "succeeded"}
        assert claims() == []
        assert (root / project / "Builds/Player.x86_64").read_bytes() == b"new player"

    try:
        asyncio.run(exercise())
    finally:
        services.close()


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
with (project / "editor-invocations").open("a") as marker:
    marker.write("launched\\n")
method = a[a.index("-executeMethod") + 1] if "-executeMethod" in a else None
assert method in {None, "Walk.Editor.Check", "Walk.Editor.BuildLinux", "Walk.Editor.NoOutput",
                  "Walk.Editor.ChmodOnly", "Walk.Editor.Empty"}
output = project / "Builds/Linux/Walk.x86_64"
if method == "Walk.Editor.BuildLinux":
    assert not output.exists()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("fake player")
if method == "Walk.Editor.ChmodOnly" and output.exists():
    output.chmod(0o700)
if method == "Walk.Editor.Empty":
    output.touch()
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

        async def run(action, suffix, scene=scene_path, state="succeeded", prior=None):
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
            terminal = json.loads((attempt / "terminal.json").read_bytes())
            assert terminal["state"] == "complete"
            if action == "build":
                assert terminal["native_exit_code"] == 0
                assert terminal["build_output_verified"] is (state == "succeeded")
            if prior is not None:
                assert (attempt / "previous-build-output").read_bytes() == prior
            assert (
                result["scene_path"] == workspace_scene and result["player_script"] == player_path
            )
            assert "--proscenium-export" not in json.loads((attempt / "editor.log").read_bytes())
            return result

        assert (await run("validate", "validate"))["validation"]["status"] == "passed"
        update = write(root, workspace_scene, b"changed authored scene")
        write(root, f"{project}/Library/incremental-cache", b"keep")
        write(root, f"{project}/{output}", b"older player")
        result = await run("build", "build", workspace_scene, prior=b"older player")
        assert result["validation"]["status"] == "passed"
        assert (root / project / output).read_text() == "fake player"
        assert (root / project / "Library/incremental-cache").read_bytes() == b"keep"
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

        for suffix, method in (("stale", "NoOutput"), ("chmod", "ChmodOnly"), ("empty", "Empty")):
            write(root, f"{project}/{output}", b"stale player")
            write(root, adapter_path, {**adapter, "build_method": f"Walk.Editor.{method}"})
            attempt = await run("build", suffix, state="failed", prior=b"stale player")
            assert attempt["validation"]["status"] == "failed"
            if method == "Empty":
                assert (root / project / output).stat().st_size == 0
            else:
                assert not (root / project / output).exists()
        calls = root / project / "editor-invocations"
        before = calls.read_bytes()
        for suffix, invalid_path in (("scene", scene_path), ("meta", "Builds/Linux/Player.MeTa")):
            target_file = root / project / invalid_path
            if suffix == "meta":
                target_file.write_bytes(b"guid: preserve")
            original = target_file.read_bytes()
            write(root, adapter_path, {**adapter, "build_output": invalid_path})
            with pytest.raises(
                DomainError, match="Unity build_output must be beneath Builds/"
            ) as refused:
                await workflows.unity(caller, body("build", f"protected-{suffix}"))
            assert (refused.value.code, refused.value.status) == ("scope_denied", 403)
            assert target_file.read_bytes() == original
            assert calls.read_bytes() == before
        (root / adapter_path).unlink()
        assert "compilation only" in (await run("validate", "compile"))["validation"]["notes"]
        with pytest.raises(DomainError, match="needs a method and output"):
            await workflows.unity(caller, body("build", "no-adapter"))
        with pytest.raises(DomainError, match="inside this project's Assets"):
            await workflows.unity(
                caller, body("validate", "wrong-project", "unity/other/Assets/Main.unity")
            )
        external = tmp_path / "other-project"
        external.mkdir()
        unrelated = root / project / "Assets/Unrelated"
        unrelated.symlink_to(external, target_is_directory=True)
        calls = root / project / "editor-invocations"
        before = calls.read_bytes()
        with pytest.raises(DomainError, match="external symlink") as refused:
            await workflows.unity(caller, body("validate", "external-link"))
        assert refused.value.code == "scope_denied" and refused.value.status == 403
        assert calls.read_bytes() == before
        unrelated.unlink()
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


def test_supervisor_rechecks_unity_project_tree_before_launch(tmp_path):
    project = tmp_path / "project"
    assets = project / "Packages"
    assets.mkdir(parents=True)
    external = tmp_path / "external.txt"
    external.write_text("not in project")
    (assets / "Redirected.txt").symlink_to(external)
    marker = tmp_path / "editor-launched"
    attempt = tmp_path / "attempt"
    request = dict(
        job_id="job",
        attempt_id="attempt",
        input_digest="inputs",
        request_digest="request",
        source_digest="source",
        project=str(project),
        project_lock=str(tmp_path / "project.lock"),
        action="validate",
        authored={},
        timeout=10,
        command=[sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
    )
    request["authored"] = dict(build_output=None)
    write(attempt, "request.json", request)
    subprocess.run(
        [sys.executable, worker_script, str(attempt / "request.json")], check=True, timeout=10
    )
    receipt = json.loads((attempt / "terminal.json").read_bytes())
    assert "external symlink: Packages/Redirected.txt" in receipt["error"]
    assert receipt["native_exit_code"] is None and not marker.exists()


@pytest.mark.parametrize(
    "output",
    ["Assets/Game/Scenes/Main.unity", "Builds/Linux/Player.META", "Builds/../Assets/Player.cs"],
)
def test_supervisor_cannot_stage_a_protected_build_path(tmp_path, output):
    project = tmp_path / "project"
    (project / "Builds").mkdir(parents=True)
    original = b"authored bytes"
    write(project, output, original)
    marker = tmp_path / "editor-launched"
    attempt = tmp_path / "attempt"
    request = dict(
        job_id="job",
        attempt_id="attempt",
        input_digest="inputs",
        request_digest="request",
        source_digest="source",
        project=str(project),
        project_lock=str(tmp_path / "project.lock"),
        action="build",
        authored=dict(build_output=output),
        timeout=10,
        command=[sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
    )
    write(attempt, "request.json", request)
    subprocess.run(
        [sys.executable, worker_script, str(attempt / "request.json")], check=True, timeout=10
    )
    receipt = json.loads((attempt / "terminal.json").read_bytes())
    assert receipt["error"] == "Unity build_output must be beneath Builds/"
    assert receipt["build_output_verified"] is False and receipt["native_exit_code"] is None
    assert (project / output).read_bytes() == original
    assert not (attempt / "previous-build-output").exists() and not marker.exists()
