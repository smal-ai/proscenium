import asyncio
import json
import os
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import validate
from proscenium.sources.common import fingerprint
from proscenium.workflows import export_worker
from proscenium.workflows.service import NAMESPACE

from tools.unity import jobs
from tools.unity.worker import process_identity
from tools.unity.workflow import write_json


def fake_processes(env, monkeypatch):
    launches: dict[str, list] = {"export": [], "unity": []}

    class Export:
        pid = os.getpid()
        returncode = None

        def __init__(self, path):
            self.path = Path(path)

        async def wait(self):
            request = json.loads(self.path.read_text())
            write_json(
                Path(request["output"]),
                dict(
                    format="proscenium-triangles-1",
                    closure_digest=request["source_digest"],
                    units="metres",
                    axes="XZY",
                    blender_version="4.5.14 LTS",
                    objects=[{"offline": "metadata export double, not native geometry evidence"}],
                ),
            )
            write_json(
                self.path.parent / "export-worker.json",
                dict(
                    binding=request["binding"],
                    source_digest=request["source_digest"],
                    state="complete",
                    native_exit_code=0,
                    supervisor=process_identity(os.getpid()),
                ),
            )
            self.returncode = 0
            return 0

        def terminate(self):
            self.returncode = -15

    async def spawn(*argv, **kwargs):
        assert argv[1] == str(Path(export_worker.__file__).resolve())
        request = json.loads(Path(argv[2]).read_text())
        command = export_worker.command(request)
        assert command[0] == str(Path("/bin/false").resolve())
        assert command[1:8] == [
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--threads",
            "4",
            request["entry"],
            "--python-exit-code",
        ]
        assert command[command.index("--python") + 1].endswith("tools/unity/export_blender.py")
        with env.services.registry.project_store("project-test").transaction(write=False) as tx:
            rows = tx.all(NAMESPACE)
        assert any(
            row["phase"] == "exporting" and fingerprint(row["binding"]) == request["binding"]
            for row in rows
        )
        launches["export"].append(argv)
        return Export(argv[2])

    class Unity:
        pid = os.getpid()

        def __init__(self, argv, **kwargs):
            job = env.services.jobs.get("project-test", env.body["job_id"])
            assert job["state"] == "starting"
            path = Path(argv[-1])
            request = json.loads(path.read_text())
            recipe = json.loads((path.parent / "recipe.json").read_text())
            assert recipe["source_id"] == env.source["item_id"]
            assert recipe["source_version"] == env.body["version"]
            assert recipe["scene_path"].startswith(
                "Assets/Proscenium/Generated/" + recipe["source_key"]
            )
            assert request["job_id"] == job["job_id"]
            assert request["input_digest"] == job["input_digest"]
            assert request["command"][0] == str(Path("/bin/false").resolve())
            method = {"import": "Import", "validate": "Validate", "build": "Build"}[
                env.body["action"]
            ]
            assert "Proscenium.Unity.Editor.UnityWorkflow." + method in request["command"]
            write_json(
                path.parent / "worker.json",
                dict(
                    job_id=job["job_id"],
                    attempt_id=job["attempt_id"],
                    input_digest=job["input_digest"],
                    request_digest=request["request_digest"],
                    source_digest=request["source_digest"],
                    state="running",
                    supervisor=process_identity(os.getpid()),
                    native_exit_code=None,
                ),
            )
            launches["unity"].append(argv)

        def poll(self):
            return None

        def wait(self, *args, **kwargs):
            pytest.fail("Nonblocking facade must never wait for the Editor supervisor")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(jobs.subprocess, "Popen", Unity)
    return launches


def test_native_tool_nonblocking_admission_retry_and_actual_result(environment, monkeypatch):
    env = environment
    launches = fake_processes(env, monkeypatch)

    async def run():
        first = await env.tools.invoke(
            "run_unity_workflow", env.invocation("run_unity_workflow", env.body)
        )
        job = json.loads(first.text_result_for_llm)
        validate("JobRecord", job)
        assert job["state"] == "running"
        assert job["target"]["version"] == env.body["version"]
        assert len(launches["export"]) == len(launches["unity"]) == 1
        waiting = await env.tools.invoke(
            "get_unity_result", env.invocation("get_unity_result", {"job_id": job["job_id"]})
        )
        assert waiting.result_type == "failure" and "get_job" in waiting.text_result_for_llm
        assert str(env.root) not in waiting.text_result_for_llm
        env.services.gate.close("project-test")
        repeated = await env.tools.invoke(
            "run_unity_workflow", env.invocation("run_unity_workflow", env.body)
        )
        assert json.loads(repeated.text_result_for_llm)["attempt_id"] == job["attempt_id"]
        assert len(launches["export"]) == len(launches["unity"]) == 1
        path = jobs.attempt_directory(env.root, job["attempt_id"])
        receipt = json.loads((path / "worker.json").read_text())
        receipt.update(state="complete", native_exit_code=0)
        write_json(path / "worker.json", receipt)
        scene = env.root / "unity/Fixture/Assets/Offline.unity"
        scene.parent.mkdir(parents=True, exist_ok=True)
        scene.write_text("OFFLINE metadata file, not native scene proof")
        result = dict(
            editor_version="offline-fake-editor",
            pipeline="offline-fake-pipeline",
            scene_path="Assets/Offline.unity",
            validation={"status": "not_run"},
        )
        write_json(path / "unity-result.json", result)
        await env.workflows.reconcile("project-test")  # Closed gate, no second launch.
        read = await env.tools.invoke(
            "get_unity_result", env.invocation("get_unity_result", {"job_id": job["job_id"]})
        )
        observed = json.loads(read.text_result_for_llm)
        validate("UnityResult", observed)
        assert observed["scene_path"] == "unity/Fixture/Assets/Offline.unity"
        assert observed["validation"]["status"] == "not_run"
        assert len(launches["export"]) == len(launches["unity"]) == 1
        await env.workflows.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        dict(project="../outside"),
        dict(project="unity/../outside"),
        dict(editor="/arbitrary/command"),
        dict(spawn=[0, float("nan"), 0]),
    ],
)
def test_native_scope_and_model_executable_denied(environment, monkeypatch, change):
    env = environment
    launches = fake_processes(env, monkeypatch)

    async def run():
        result = await env.tools.invoke(
            "run_unity_workflow", env.invocation("run_unity_workflow", {**env.body, **change})
        )
        assert result.result_type == "failure"
        assert not launches["export"] and not launches["unity"]
        await env.workflows.close()

    asyncio.run(run())


def test_owner_and_stale_invocation_denied(environment):
    env = environment

    async def run():
        for caller in (Caller("owner", "owner"), replace(env.caller, owner_epoch="stale")):
            with pytest.raises(DomainError):
                await env.workflows.unity(caller, env.body)
        await env.workflows.close()

    asyncio.run(run())


def test_known_preflight_failure_does_not_leave_phantom_native_blocker(environment, monkeypatch):
    env = environment
    launches = fake_processes(env, monkeypatch)
    env.body["version"]["item_id"] = "wrong-published-item"

    async def run():
        response = await env.tools.invoke(
            "run_unity_workflow", env.invocation("run_unity_workflow", env.body)
        )
        assert json.loads(response.text_result_for_llm)["error"] == "source_conflict"
        assert await env.workflows.pending("project-test") == []
        assert not launches["export"] and not launches["unity"]
        await env.workflows.close()

    asyncio.run(run())


def test_validate_cannot_claim_a_source_not_imported_into_this_scene(environment, monkeypatch):
    env = environment
    launches = fake_processes(env, monkeypatch)
    env.body["action"] = "validate"

    async def run():
        response = await env.tools.invoke(
            "run_unity_workflow", env.invocation("run_unity_workflow", env.body)
        )
        assert json.loads(response.text_result_for_llm)["error"] == "source_conflict"
        assert not launches["export"] and not launches["unity"]
        await env.workflows.close()

    asyncio.run(run())


def test_validate_accepts_matching_saved_import_manifest(environment, monkeypatch):
    env = environment
    launches = fake_processes(env, monkeypatch)

    async def run():
        job = await env.workflows.unity(env.caller, env.body)
        attempt = jobs.attempt_directory(env.root, job["attempt_id"])
        recipe = json.loads((attempt / "recipe.json").read_text())
        scene = env.root / "unity/Fixture" / recipe["scene_path"]
        scene.parent.mkdir(parents=True, exist_ok=True)
        scene.write_text("OFFLINE metadata fixture, not a native Unity scene")
        write_json(scene.parent / "source-manifest.json", recipe)
        receipt = json.loads((attempt / "worker.json").read_text())
        receipt.update(state="complete", native_exit_code=0)
        write_json(attempt / "worker.json", receipt)
        write_json(
            attempt / "unity-result.json",
            dict(
                editor_version="offline-fake-editor",
                pipeline="offline-fake-pipeline",
                scene_path=recipe["scene_path"],
                validation={"status": "not_run"},
            ),
        )
        assert (await env.workflows.reconcile("project-test"))[0]["state"] == "succeeded"
        env.body.update(action="validate", operation_id="validate-operation", job_id="validate-job")
        validated = await env.workflows.unity(env.caller, env.body)
        assert validated["state"] == "running"
        assert len(launches["export"]) == len(launches["unity"]) == 2
        await env.workflows.close()

    asyncio.run(run())


def test_ambiguous_authoring_is_fenced_without_reexport(environment, monkeypatch):
    env = environment
    launches = fake_processes(env, monkeypatch)

    async def run():
        identity, row, _ = await env.workflows.io(
            env.workflows.begin, env.caller, env.body, "unity"
        )
        row["authoring_started"] = True
        await env.workflows.io(env.workflows.save, "project-test", identity, row)
        await env.workflows.reconcile("project-test")
        with pytest.raises(DomainError):
            await env.workflows.unity(env.caller, env.body)
        assert not launches["export"] and not launches["unity"]
        await env.workflows.close()

    asyncio.run(run())


def test_close_joins_cancelled_io_writer_and_keeps_loop_responsive(environment):
    env = environment
    entered, release = threading.Event(), threading.Event()
    writes = []

    def io_work():
        entered.set()
        release.wait(5)
        writes.append("finished before close returned")

    async def run():
        async def operation():
            async with env.workflows.operation():
                await env.workflows.io(io_work)

        operation_task = asyncio.create_task(operation())
        while not entered.is_set():
            await asyncio.sleep(0)
        closing = asyncio.create_task(env.workflows.close())
        await asyncio.sleep(0.01)
        assert not closing.done()
        release.set()
        await closing
        assert writes == ["finished before close returned"]
        assert operation_task.cancelled()
        with pytest.raises(DomainError):
            await env.workflows.unity(env.caller, env.body)

    asyncio.run(run())


def test_native_fence_denies_new_workflow_even_when_shared_gate_open(environment, monkeypatch):
    env = environment
    launches = fake_processes(env, monkeypatch)
    env.tools.state.control = lambda tx: {"fenced": True}

    async def run():
        result = await env.tools.invoke(
            "run_unity_workflow", env.invocation("run_unity_workflow", env.body)
        )
        assert json.loads(result.text_result_for_llm)["error"] == "permission_denied"
        assert not launches["export"] and not launches["unity"]
        await env.workflows.close()

    asyncio.run(run())


def test_lost_workflow_ack_reconciles_original_job_before_reexport(environment, monkeypatch):
    env = environment
    launches = fake_processes(env, monkeypatch)
    original = env.workflows.save
    lost = False

    def save(project_id, identity, row):
        nonlocal lost
        if row["phase"] == "launched" and not lost:
            lost = True
            raise RuntimeError("Offline lost workflow acknowledgement")
        original(project_id, identity, row)

    monkeypatch.setattr(env.workflows, "save", save)

    async def run():
        first = await env.tools.invoke(
            "run_unity_workflow", env.invocation("run_unity_workflow", env.body)
        )
        assert first.result_type == "failure"
        env.services.gate.close("project-test")
        repeat = await env.tools.invoke(
            "run_unity_workflow", env.invocation("run_unity_workflow", env.body)
        )
        result = json.loads(repeat.text_result_for_llm)
        validate("JobRecord", result)
        assert result["job_id"] == env.body["job_id"]
        assert len(launches["export"]) == len(launches["unity"]) == 1
        await env.workflows.close()

    asyncio.run(run())


def test_uncertain_export_spawn_is_never_relaunched(environment, monkeypatch):
    env = environment
    calls = []

    async def ambiguous(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("Offline spawn acknowledgement lost, effect unknown")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", ambiguous)

    async def run():
        with pytest.raises(RuntimeError):
            await env.workflows.unity(env.caller, env.body)
        env.services.gate.close("project-test")
        await env.workflows.reconcile("project-test")
        with env.services.registry.project_store("project-test").transaction(write=False) as tx:
            assert tx.all(NAMESPACE)[0]["phase"] == "outcome_uncertain"
        with pytest.raises(DomainError):
            await env.workflows.unity(env.caller, env.body)
        assert len(calls) == 1
        await env.workflows.close()

    asyncio.run(run())


def test_close_reaps_owned_export_and_waits_for_spawn_ack(environment, monkeypatch):
    env = environment
    spawned, acknowledge = asyncio.Event(), asyncio.Event()
    observations = []

    class Process:
        pid = os.getpid()
        returncode = None

        def terminate(self):
            observations.append("terminate owned export supervisor")
            self.returncode = -15

        async def wait(self):
            while self.returncode is None:
                await asyncio.sleep(0)
            observations.append("reaped owned export supervisor")
            return self.returncode

    async def spawn(*args, **kwargs):
        spawned.set()
        await acknowledge.wait()
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def run():
        task = asyncio.create_task(env.workflows.unity(env.caller, env.body))
        await spawned.wait()
        closing = asyncio.create_task(env.workflows.close())
        await asyncio.sleep(0.01)
        assert not closing.done()
        acknowledge.set()
        await closing
        assert task.cancelled()
        assert observations == [
            "terminate owned export supervisor",
            "reaped owned export supervisor",
        ]

    asyncio.run(run())


def test_closed_gate_cancellation_routes_exact_known_worker_without_fake_terminal(
    environment, monkeypatch
):
    from proscenium.workflows import service as module

    env = environment
    fake_processes(env, monkeypatch)
    observations = []

    def cancel(services, caller, job_id):
        observations.append(caller)
        assert caller.channel == "worker" and caller.job_id == job_id
        return {"job": services.jobs.get("project-test", job_id)}

    monkeypatch.setattr(module, "cancel_unity_job", cancel)

    async def run():
        job = await env.workflows.unity(env.caller, env.body)
        env.services.gate.close("project-test")
        env.services.jobs.cancel(
            Caller("owner", "offline-owner"),
            job["job_id"],
            dict(
                schema_version="1.0",
                operation_id="cancel-original",
                project_id="project-test",
                expected_revision=job["state_revision"],
            ),
        )
        result = await env.workflows.cancel("project-test", job["job_id"])
        assert result["state"] == "cancel_requested"
        assert observations[0].attempt_id == job["attempt_id"]
        assert observations[0].input_digest == job["input_digest"]
        with pytest.raises(DomainError):
            env.workflows.cancel_job({**job, "attempt_id": "stale-attempt"})
        await env.workflows.close()

    asyncio.run(run())


def test_verified_f00_retry_reuses_frozen_export_for_new_attempt(environment, monkeypatch):
    env = environment
    env.body["spawn"] = [-0.0, 0.92, -2.0]
    launches = fake_processes(env, monkeypatch)

    async def run():
        first = await env.workflows.unity(env.caller, env.body)
        path = jobs.attempt_directory(env.root, first["attempt_id"])
        receipt = json.loads((path / "worker.json").read_text())
        receipt.update(state="complete", native_exit_code=7)
        write_json(path / "worker.json", receipt)
        failed = (await env.workflows.reconcile("project-test"))[0]
        assert failed["state"] == "failed"
        retried, _ = env.services.jobs.retry(
            env.caller,
            dict(
                schema_version="1.0",
                operation_id="explicit-shared-retry",
                project_id="project-test",
                job_id=failed["job_id"],
                expected_revision=failed["state_revision"],
            ),
        )
        assert retried["attempt_id"] != first["attempt_id"]
        second = await env.workflows.unity(env.caller, env.body)
        assert second["attempt_id"] == retried["attempt_id"] and second["state"] == "running"
        assert len(launches["export"]) == 1 and len(launches["unity"]) == 2
        await env.workflows.close()

    asyncio.run(run())
