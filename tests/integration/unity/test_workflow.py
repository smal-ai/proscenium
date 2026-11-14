import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from proscenium.api.errors import DomainError

# tools is deliberately not a second application distribution or dependency root.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.unity.jobs import attempt_directory, reconcile_unity_job, run_unity_job
from tools.unity.worker import editor_running, process_identity
from tools.unity.workflow import (
    bootstrap_project,
    input_digest,
    make_recipe,
    prepare,
    scoped,
    sha256,
    unity_path,
    verify_materialisation,
    write_json,
)


@pytest.fixture
def prepared(services, caller, admission):
    root = Path(services.registry.get("project-test")["path"])
    materialised = root / "sealed"
    materialised.mkdir()
    source = materialised / "fixture.blend"
    source.write_bytes(b"OFFLINE double, not a Blender native fixture")
    closure = dict(
        entry_path="fixture.blend",
        files=[
            dict(
                destination="fixture.blend",
                origin=dict(
                    kind="retained",
                    artifact_id="blob-source",
                    sha256=sha256(source),
                ),
            )
        ],
        dependencies=[],
        required_runtime=dict(application="Blender", version="4.5.14", features=[]),
        digest="b" * 64,
    )
    services.extensions["sources"] = SimpleNamespace(
        materialise=lambda project_id, version_id: materialised
    )
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put("versions", "version-source", dict(item_id="item-source", source=closure))
    export = root / "export.json"
    write_json(export, dict(closure_digest=closure["digest"]))
    prepare(root, "unity/Fixture", tests=True)
    recipe = make_recipe(
        root,
        "unity/Fixture",
        source_id="source-fixture",
        source_version=dict(item_id="item-source", version_id="version-source"),
        closure=closure,
        export_path="export.json",
        spawn=(0, 0.92, -2),
    )
    admission["input_digest"] = input_digest(
        dict(project="unity/Fixture", recipe=recipe, action="import")
    )
    services.gate.resume("project-test", reconciliation_evidence="offline-disposable-fixture")
    return root, recipe


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/absolute",
        "unity//bad",
        "unity/./bad",
        "unity/../bad",
        "unity/.env",
        "unity/a\\b",
    ],
)
def test_safe_paths(tmp_path, path):
    with pytest.raises(DomainError):
        scoped(tmp_path, path)


def test_project_must_live_under_unity(tmp_path):
    with pytest.raises(DomainError):
        unity_path(tmp_path, "external/project")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "unity").symlink_to(outside, target_is_directory=True)
    with pytest.raises(DomainError):
        unity_path(tmp_path, "unity/Fixture")


def test_prepare_retains_meta_and_unrelated_files(tmp_path):
    prepare(tmp_path, "unity/Fixture", tests=True)
    script = tmp_path / "unity/Fixture/Assets/Proscenium/Runtime/WalkthroughPlayer.cs"
    meta = script.with_suffix(".cs.meta")
    meta.write_text("guid: stable-guid\n")
    unrelated = script.parent / "Unrelated.cs"
    unrelated.write_text("// keep")
    prepare(tmp_path, "unity/Fixture", tests=True)
    assert meta.read_text() == "guid: stable-guid\n"
    assert unrelated.read_text() == "// keep"


def test_changed_materialisation_rejected(services, prepared):
    root, recipe = prepared
    (root / "sealed/fixture.blend").write_bytes(b"changed")
    with pytest.raises(DomainError):
        verify_materialisation(root / "sealed", recipe["source"])


def test_admission_denied_before_any_process(services, caller, admission, prepared, monkeypatch):
    _, recipe = prepared
    services.gate.close("project-test")
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("Process before admission")
    )
    with pytest.raises(DomainError):
        run_unity_job(
            services,
            caller,
            admission,
            project="unity/Fixture",
            recipe=recipe,
            editor=Path("/bin/true"),
        )


def test_missing_editor_is_tracked_blocker(services, caller, admission, prepared):
    _, recipe = prepared
    result = run_unity_job(
        services,
        caller,
        admission,
        project="unity/Fixture",
        recipe=recipe,
        editor=Path("/not-installed/Unity"),
    )
    assert result["job"]["state"] == "planned"
    assert result["blocked"]
    assert services.jobs.get("project-test", admission["job_id"])["kind"] == "unity"


def test_known_job_before_start_and_repeat_no_launch(
    services, caller, admission, prepared, monkeypatch
):
    root, recipe = prepared
    import tools.unity.jobs as adapter

    original = subprocess.Popen
    launches = []

    def checked(*args, **kwargs):
        job = services.jobs.get("project-test", admission["job_id"])
        assert job["state"] == "starting"
        directory = attempt_directory(root, job["attempt_id"])
        assert (
            json.loads((directory / "request.json").read_text())["attempt_id"] == job["attempt_id"]
        )
        launches.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(adapter.subprocess, "Popen", checked)
    # /bin/true is an explicit OFFLINE process double. No UnityResult means failure, not success.
    first = run_unity_job(
        services,
        caller,
        admission,
        project="unity/Fixture",
        recipe=recipe,
        editor=Path("/bin/true"),
    )
    assert first["job"]["state"] == "failed"
    assert first["worker"]["native_exit_code"] == 0
    second = run_unity_job(
        services,
        caller,
        admission,
        project="unity/Fixture",
        recipe=recipe,
        editor=Path("/bin/true"),
    )
    assert second["job"]["attempt_id"] == first["job"]["attempt_id"]
    assert len(launches) == 1


def test_uncertain_launch_never_relaunched(services, caller, admission, prepared, monkeypatch):
    root, recipe = prepared
    job, _ = services.jobs.admit(caller, admission)
    attempt_directory(root, job["attempt_id"]).mkdir(parents=True)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Uncertain relaunch"))
    result = run_unity_job(
        services,
        caller,
        admission,
        project="unity/Fixture",
        recipe=recipe,
        editor=Path("/bin/true"),
    )
    assert result["job"]["state"] == "outcome_uncertain"


def test_changed_recipe_rejected(services, caller, admission, prepared):
    _, recipe = prepared
    recipe["camera"] = "orthographic"
    with pytest.raises(DomainError):
        run_unity_job(
            services,
            caller,
            admission,
            project="unity/Fixture",
            recipe=recipe,
            editor=Path("/bin/true"),
        )


def test_lock_guard_scoped_to_project(tmp_path):
    (tmp_path / "Temp").mkdir()
    assert not editor_running(tmp_path)
    (tmp_path / "Temp/UnityLockfile").touch()
    assert editor_running(tmp_path)
    assert process_identity(os.getpid())["pid"] == os.getpid()


def test_reconcile_requires_own_work(services, caller, admission, prepared):
    services.jobs.admit(caller, admission)
    from dataclasses import replace

    other = replace(caller, work_id="work-other")
    with pytest.raises(DomainError):
        reconcile_unity_job(services, other, admission["job_id"])


def test_cli_rejects_native_launch_action(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.unity.workflow",
            "build",
            "--root",
            str(tmp_path),
            "--project",
            "unity/Fixture",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_prepare_refuses_locally_edited_tool_and_active_editor(tmp_path):
    project = prepare(tmp_path, "unity/Fixture")
    script = project / "Assets/Proscenium/Runtime/WalkthroughPlayer.cs"
    script.write_text("// owner's local edit\n")
    with pytest.raises(DomainError, match="Preserve locally edited"):
        prepare(tmp_path, "unity/Fixture")
    (project / "Temp").mkdir()
    (project / "Temp/UnityLockfile").touch()
    with pytest.raises(DomainError, match="Save/close"):
        prepare(tmp_path, "unity/Fixture")
    assert script.read_text() == "// owner's local edit\n"


def test_source_version_closure_cannot_be_substituted(services, caller, admission, prepared):
    _, recipe = prepared
    recipe["source"]["digest"] = "c" * 64
    admission["input_digest"] = input_digest(
        dict(project="unity/Fixture", recipe=recipe, action="import")
    )
    with pytest.raises(DomainError, match="pinned published source"):
        run_unity_job(
            services,
            caller,
            admission,
            project="unity/Fixture",
            recipe=recipe,
            editor=Path("/bin/true"),
        )


def test_bounded_timeout_reaps_only_owned_process(services, caller, admission, prepared):
    root, recipe = prepared
    fake = root / "offline-editor-double"
    fake.write_text("#!/bin/sh\nexec /bin/sleep 30\n")
    fake.chmod(0o700)
    result = run_unity_job(
        services, caller, admission, project="unity/Fixture", recipe=recipe, editor=fake, timeout=1
    )
    from tools.unity.worker import alive

    assert result["job"]["state"] == "failed"
    assert result["worker"]["error"] == "timeout"
    assert result["worker"]["native_exit_code"] == -15
    assert not alive(result["worker"]["process"])


def test_terminal_resource_release_crash_reconciles(
    services, caller, admission, prepared, monkeypatch
):
    root, recipe = prepared
    release = services.resources.release
    monkeypatch.setattr(
        services.resources,
        "release",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("lost acknowledgement")),
    )
    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        run_unity_job(
            services,
            caller,
            admission,
            project="unity/Fixture",
            recipe=recipe,
            editor=Path("/bin/true"),
        )
    assert services.jobs.get("project-test", admission["job_id"])["state"] == "failed"
    monkeypatch.setattr(services.resources, "release", release)
    reconcile_unity_job(services, caller, admission["job_id"])
    resource = "unity-project:" + str(root / "unity/Fixture")
    assert services.resources.acquire(resource, "project-test", "next-job", "next-attempt")


def test_other_worker_cannot_inspect_or_cancel_attempt(services, caller, admission, prepared):
    services.jobs.admit(caller, admission)
    from dataclasses import replace

    worker = replace(
        caller,
        channel="worker",
        job_id="not-this-job",
        attempt_id="not-this-attempt",
        input_digest="0" * 64,
    )
    with pytest.raises(DomainError):
        reconcile_unity_job(services, worker, admission["job_id"])


def test_bootstrap_does_not_overwrite_existing_project(tmp_path):
    project = bootstrap_project(tmp_path, "unity/Fresh", tests=True)
    packages = json.loads((project / "Packages/manifest.json").read_text())["dependencies"]
    assert packages["com.unity.inputsystem"] == "1.20.1"
    with pytest.raises(DomainError):
        bootstrap_project(tmp_path, "unity/Fresh", tests=True)
    assert json.loads((project / "Packages/manifest.json").read_text())["dependencies"] == packages


def test_terminal_admission_reuses_without_materialising_again(
    services, caller, admission, prepared
):
    root, recipe = prepared
    result = run_unity_job(
        services,
        caller,
        admission,
        project="unity/Fixture",
        recipe=recipe,
        editor=Path("/bin/true"),
    )
    assert result["job"]["state"] == "failed"
    (root / "sealed/fixture.blend").unlink()
    (root / "export.json").unlink()
    repeated = run_unity_job(
        services,
        caller,
        admission,
        project="unity/Fixture",
        recipe=recipe,
        editor=Path("/bin/true"),
    )
    assert repeated["job"] == result["job"]


def test_checkpoint_calls_only_existing_source_seam(services, caller, prepared):
    from tools.unity.jobs import checkpoint_unity

    calls = []

    def fixture_checkpoint(supplied_caller, request):
        calls.append((supplied_caller, request))
        return {"fixture": "receipt"}

    services.extensions["sources"].checkpoint = fixture_checkpoint
    request = dict(
        schema_version="1.0",
        operation_id="checkpoint-fixture",
        project_id="project-test",
        paths=["unity/Fixture/Assets/Proscenium/Runtime/WalkthroughPlayer.cs"],
        message="Fixture checkpoint",
        expected_head=None,
    )
    assert checkpoint_unity(services, caller, request) == {"fixture": "receipt"}
    assert calls == [(caller, request)]
    for path in ("unrelated/file.py", "unity/Fixture/Library/cache"):
        request["paths"] = [path]
        with pytest.raises(DomainError):
            checkpoint_unity(services, caller, request)
    assert len(calls) == 1


def test_materialisation_service_cannot_widen_project(
    services, caller, admission, prepared, tmp_path
):
    root, recipe = prepared
    services.extensions["sources"].materialise = lambda *a: root.parent
    with pytest.raises(DomainError, match="outside the active project"):
        run_unity_job(
            services,
            caller,
            admission,
            project="unity/Fixture",
            recipe=recipe,
            editor=Path("/bin/true"),
        )
