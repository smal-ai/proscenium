"""F00 JobAdmission/JobAttempt adapter. No router, auth, schema or checkpoint replacement."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import validate
from proscenium.db.paths import project_path
from proscenium.services import Services

from .worker import alive, editor_running, observe_attempt, process_identity
from .workflow import (
    HERE,
    input_digest,
    scoped,
    sha256,
    unity_path,
    verify_materialisation,
    write_json,
)

ACTIONS = {
    "import": "Import",
    "repeat-proof": "ImportRepeatProof",
    "validate": "Validate",
    "build": "Build",
    "playtest": None,
}
_SUPERVISORS: dict[str, subprocess.Popen] = {}


def reap_unity_supervisors() -> None:
    """Reap completed own supervisors without waiting for or stopping admitted jobs."""
    for attempt_id, process in list(_SUPERVISORS.items()):
        if process.poll() is not None:
            _SUPERVISORS.pop(attempt_id, None)


def check_attempt_caller(caller: Caller, job: dict[str, Any]) -> None:
    if caller.channel == "worker":
        if (
            caller.job_id != job["job_id"]
            or caller.attempt_id != job["attempt_id"]
            or caller.input_digest != job["input_digest"]
        ):
            raise DomainError("scope_denied", 403)
    elif caller.channel != "tool" or caller.work_id != job["work_id"]:
        raise DomainError("scope_denied", 403)


def change(
    services: Services,
    caller: Caller,
    job: dict[str, Any],
    state: str,
    *,
    evidence: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body = dict(
        schema_version="1.0",
        operation_id=f"{job['attempt_id']}-{state}-{job['state_revision']}",
        project_id=job["project_id"],
        job_id=job["job_id"],
        attempt_id=job["attempt_id"],
        expected_revision=job["state_revision"],
        state=state,
        availability="complete" if state == "succeeded" else job["availability"],
        **extra,
    )
    return services.jobs.update(caller, body, evidence=evidence)[0]


def attempt_directory(root: Path, attempt_id: str) -> Path:
    # IDs are opaque and can contain punctuation. Hash rather than interpolate into paths.
    key = input_digest({"attempt_id": attempt_id})
    return project_path(
        root, f".proscenium/jobs/unity/{key}", managed=True, must_exist=False, directory=True
    )


def run_unity_job(
    services: Services,
    caller: Caller,
    admission: dict[str, Any],
    *,
    project: str,
    recipe: dict[str, Any],
    editor: Path,
    action: str = "import",
    timeout: int = 600,
    wait: bool = True,
) -> dict[str, Any]:
    """Admit before launch, retain exact evidence and never repeat an attempt.

    wait=True retains CLI behaviour. wait=False returns after tracked supervisor
    launch/job update; the caller must reconcile receipts and reap completed handles.

    `recipe` is private tool configuration, `admission` is the unchanged contract JSON.
    `services.extension('sources')` must implement accepted F00 SourceService. The caller
    comes from the trusted host, not model arguments. The configured host selects editor.
    """
    validate("JobAdmission", admission)
    if (
        admission["kind"] != "unity"
        or action not in ACTIONS
        or not 1 <= timeout <= 600
        or type(wait) is not bool
    ):
        raise DomainError("invalid_request")
    project_id = admission["project_id"]
    if services.registry.active_id() != project_id:
        raise DomainError("scope_denied", 403)
    root = Path(services.registry.get(project_id)["path"])
    destination = unity_path(root, project)
    export = scoped(root, recipe["export_path"])
    identity = {"project": project, "recipe": recipe, "action": action}
    if admission["input_digest"] != input_digest(identity):
        raise DomainError(
            "source_conflict", 409, "Admission must bind the complete tool recipe/action"
        )
    job, _ = services.jobs.admit(caller, admission)
    job = services.jobs.get(project_id, job["job_id"])
    attempt = attempt_directory(root, job["attempt_id"])
    if job["state"] != "planned" or attempt.exists():
        # A completed admission is still reusable after temporary input/cache cleanup.
        return reconcile_unity_job(services, caller, job["job_id"])
    version_id = recipe["source_version"]["version_id"]
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        version = tx.require("versions", version_id)
    if (
        version["item_id"] != recipe["source_version"]["item_id"]
        or version["source"] != recipe["source"]
    ):
        raise DomainError("source_conflict", 409, "Recipe differs from the pinned published source")
    # Consume only the accepted service seam. Its resolver, not this tool, authenticates the closure.
    materialised = services.extension("sources").materialise(project_id, version_id)
    try:
        materialised.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise DomainError(
            "scope_denied", 403, "Materialisation is outside the active project"
        ) from error
    verify_materialisation(materialised, recipe["source"])
    if sha256(export) != recipe["export_sha256"]:
        raise DomainError("source_conflict", 409, "Export bytes changed")
    exported = json.loads(export.read_text())
    if exported["closure_digest"] != recipe["source"]["digest"]:
        raise DomainError("source_conflict", 409, "Export source identity differs")
    services.gate.require(project_id)
    if not editor.is_file() or editor_running(destination):
        return dict(job=job, blocked="Editor missing or this project is already open/locked")
    resource = "unity-project:" + str(destination)
    if not services.resources.acquire(resource, project_id, job["job_id"], job["attempt_id"]):
        return dict(job=job, blocked="Unity project resource is owned by another admitted attempt")
    attempt.parent.mkdir(parents=True, exist_ok=True)
    try:
        attempt.mkdir()  # Exclusive launch intent. A crash here is uncertain, NEVER a second launch.
    except FileExistsError:
        return reconcile_unity_job(services, caller, job["job_id"])
    try:
        job = change(services, caller, job, "starting")
        write_json(attempt / "recipe.json", recipe)
        result = attempt / "unity-result.json"
        command = [
            str(editor.resolve()),
            "-batchmode",
            "-nographics",
            "-job-worker-count",
            "2",
            "-projectPath",
            str(destination),
            "-logFile",
            str(attempt / "editor.log"),
            "--proscenium-attempt",
            job["attempt_id"],
            "--proscenium-recipe",
            str(attempt / "recipe.json"),
            "--proscenium-export",
            str(export),
            "--proscenium-result",
            str(result),
        ]
        if action == "playtest":
            command += [
                "-runTests",
                "-testPlatform",
                "PlayMode",
                "-testFilter",
                "Proscenium.Unity.Tests.PlayableTests",
                "-testResults",
                str(attempt / "playmode.xml"),
            ]
        else:
            command += [
                "-quit",
                "-executeMethod",
                "Proscenium.Unity.Editor.UnityWorkflow." + str(ACTIONS[action]),
            ]
        request = dict(
            job_id=job["job_id"],
            attempt_id=job["attempt_id"],
            input_digest=job["input_digest"],
            source_digest=recipe["source"]["digest"],
            project=str(destination),
            project_lock=str(attempt.parent / (input_digest({"project": project}) + ".lock")),
            action=action,
            command=command,
            timeout=timeout,
            resource=resource,
        )
        request["request_digest"] = input_digest(request)
        write_json(attempt / "request.json", request)
        # Intent is durable before supervisor creation. Its first instruction records PID/start ticks.
        with (attempt / "supervisor.log").open("w") as output:
            supervisor = subprocess.Popen(
                [sys.executable, str(HERE / "worker.py"), str(attempt / "request.json")],
                start_new_session=True,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            _SUPERVISORS[job["attempt_id"]] = supervisor
            write_json(attempt / "supervisor.json", process_identity(supervisor.pid))
            observed = json.loads((attempt / "supervisor.json").read_text())
            external = f"unity-supervisor:{observed['pid']}:{observed['start_ticks']}"
            job = change(
                services,
                caller,
                job,
                "running",
                evidence=sha256(attempt / "supervisor.json"),
                external_job_id=external,
            )
            if not wait:
                return dict(job=job, unity=None, evidence=str(attempt))
            try:
                supervisor.wait(timeout=timeout + 30)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                if supervisor.poll() is None:
                    os.kill(supervisor.pid, signal.SIGTERM)
                    supervisor.wait(timeout=20)
    except OSError as error:
        # The launch may already have occurred. Retain the intent and inspect, never assume failure.
        write_json(attempt / "adapter-error.json", {"error": str(error)})
    return reconcile_unity_job(services, caller, job["job_id"])


def reconcile_unity_job(services: Services, caller: Caller, job_id: str) -> dict[str, Any]:
    reap_unity_supervisors()
    project_id = str(caller.project_id)
    services.jobs.check_caller(caller, "updateJobState", project_id)
    job = services.jobs.get(project_id, job_id)
    check_attempt_caller(caller, job)
    root = Path(services.registry.get(project_id)["path"])
    attempt = attempt_directory(root, job["attempt_id"])
    receipt_path = attempt / "worker.json"
    if job["state"] in {"succeeded", "failed", "cancelled"}:
        # Recover the terminal-DB-update -> resource-release crash window.
        if receipt_path.is_file() and (attempt / "request.json").is_file():
            receipt = json.loads(receipt_path.read_text())
            request = json.loads((attempt / "request.json").read_text())
            if receipt["state"] == "complete" and all(
                receipt[key] == job[key] == request[key]
                for key in ("job_id", "attempt_id", "input_digest")
            ):
                services.resources.release(
                    request["resource"],
                    project_id,
                    job_id,
                    job["attempt_id"],
                    terminal_evidence=sha256(receipt_path),
                )
        result = attempt / "unity-result.json"
        return dict(
            job=job,
            evidence=str(attempt),
            unity=json.loads(result.read_text()) if result.is_file() else None,
        )
    if not receipt_path.exists():
        if job["state"] != "outcome_uncertain":
            job = change(services, caller, job, "outcome_uncertain")
        return dict(
            job=job,
            evidence=str(attempt),
            observed_processes=observe_attempt(job["attempt_id"], attempt / "request.json"),
            note="No acknowledged worker receipt. Inspect launch intent/processes; do not relaunch.",
        )
    receipt = json.loads(receipt_path.read_text())
    request = json.loads((attempt / "request.json").read_text())
    expected_hash = request.pop("request_digest")
    if input_digest(request) != expected_hash:
        raise DomainError("source_conflict", 409)
    if (
        any(receipt[key] != job[key] for key in ("job_id", "attempt_id", "input_digest"))
        or receipt["request_digest"] != expected_hash
    ):
        raise DomainError("scope_denied", 403)
    proof = sha256(receipt_path)
    external = (
        "unity-supervisor:"
        + str(receipt["supervisor"]["pid"])
        + ":"
        + receipt["supervisor"]["start_ticks"]
    )
    if receipt["state"] != "complete":
        running = alive(receipt["supervisor"]) or (
            "process" in receipt and alive(receipt["process"])
        )
        state = "running" if running else "outcome_uncertain"
        if job["state"] != state:
            job = change(services, caller, job, state, evidence=proof, external_job_id=external)
        return dict(job=job, evidence=str(attempt), worker=receipt)
    success = receipt["native_exit_code"] == 0 and not receipt.get("error")
    unity_result: dict[str, Any] | None = None
    result_path = attempt / "unity-result.json"
    if request["action"] == "playtest":
        xml = attempt / "playmode.xml"
        passed = False
        if xml.is_file():
            report = ElementTree.parse(xml).getroot()
            passed = report.get("result") == "Passed" and int(report.get("passed", "0")) > 0
        success = success and passed
        recipe = json.loads((attempt / "recipe.json").read_text())
        # Prefer metadata observed by this actual PlayMode process. Older failed/compile
        # attempts can identify the last saved scene using its real import metadata.
        prior = Path(request["project"])
        manifest = (
            prior / "Assets/Proscenium/Generated" / recipe["source_key"] / "unity-result.json"
        )
        observed_result = result_path if result_path.is_file() else manifest
        if observed_result.is_file():
            unity_result = json.loads(observed_result.read_text())
            unity_result["validation"] = dict(
                status="passed" if success else "failed",
                notes="Actual Editor PlayMode test runner. See attempt XML/metrics; no physical OS input proof.",
            )
            write_json(result_path, unity_result)
        else:
            success = False
    elif result_path.is_file():
        unity_result = json.loads(result_path.read_text())
    else:
        success = False
    if unity_result:
        relative_project = Path(request["project"]).relative_to(root).as_posix()
        # Unity APIs report Assets/... paths. Catalogue paths resolve from the active
        # Proscenium root, which contains the Unity project rather than being it.
        for field in ("scene_path", "player_script"):
            if unity_result.get(field, "").startswith("Assets/"):
                unity_result[field] = relative_project + "/" + unity_result[field]
        validate("UnityResult", unity_result)
        if success and not scoped(root, unity_result["scene_path"]).is_file():
            success = False
        write_json(result_path, unity_result)
        if (request["action"] in {"import", "repeat-proof"} and success) or request[
            "action"
        ] == "playtest":
            recipe = json.loads((attempt / "recipe.json").read_text())
            target = (
                Path(request["project"])
                / "Assets/Proscenium/Generated"
                / recipe["source_key"]
                / "unity-result.json"
            )
            write_json(target, unity_result)
    state = "succeeded" if success else "failed"
    if job["state"] == "cancel_requested" and receipt.get("error") == "supervisor_interrupted":
        state = "cancelled"
    job = change(
        services,
        caller,
        job,
        state,
        evidence=proof,
        external_job_id=external,
        native_exit_code=receipt["native_exit_code"],
    )
    services.resources.release(
        request["resource"], project_id, job_id, job["attempt_id"], terminal_evidence=proof
    )
    return dict(job=job, unity=unity_result, evidence=str(attempt), worker=receipt)


def cancel_unity_job(services: Services, caller: Caller, job_id: str) -> dict[str, Any]:
    """Parent's cancellation consumer calls this after F00 persists cancel_requested.

    Signals only the exact live supervisor identified by PID, boot ID and start ticks.
    Returns observation, not an invented cancellation acknowledgement. Reconcile again
    once the supervisor has written its terminal receipt.
    """
    project_id = str(caller.project_id)
    services.jobs.check_caller(caller, "updateJobState", project_id)
    job = services.jobs.get(project_id, job_id)
    if job["state"] != "cancel_requested":
        return reconcile_unity_job(services, caller, job_id)
    check_attempt_caller(caller, job)
    root = Path(services.registry.get(project_id)["path"])
    path = attempt_directory(root, job["attempt_id"]) / "worker.json"
    if path.is_file():
        receipt = json.loads(path.read_text())
        if any(receipt[key] != job[key] for key in ("job_id", "attempt_id", "input_digest")):
            raise DomainError("scope_denied", 403)
        if receipt["state"] != "complete" and alive(receipt["supervisor"]):
            os.kill(receipt["supervisor"]["pid"], signal.SIGTERM)
        elif (
            receipt["state"] != "complete" and receipt.get("process") and alive(receipt["process"])
        ):
            # A dead supervisor does not own any more timeout enforcement. Signal its
            # verified surviving native group, retain uncertainty until terminal proof.
            pid = receipt["process"]["pid"]
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGTERM)
    return reconcile_unity_job(services, caller, job_id)


def checkpoint_unity(services: Services, caller: Caller, request: dict[str, Any]) -> dict[str, Any]:
    """Use the one accepted checkpoint writer. The host supplies exact paths/expected HEAD."""
    validate("CheckpointRequest", request)
    for path in request["paths"]:
        if not path.startswith("unity/") or any(
            part.lower() in {"library", "temp", "logs", "builds", "usersettings"}
            for part in path.split("/")
        ):
            raise DomainError(
                "scope_denied", 403, "Checkpoint only authored Unity source/meta/settings"
            )
    return services.extension("sources").checkpoint(caller, request)
