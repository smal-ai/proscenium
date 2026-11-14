"""Normal production lifespan with one private scripted preview-admission fixture.

No cancellation dispatch, renderer, SDK send, owner approval or extra HTTP route
is implemented here. All actual cancellation is owned by the production watcher.
"""

import hashlib
import json
import shutil
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from proscenium.app import create_app
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.services import Services
from proscenium.sources import closure_digest
from proscenium.sources.common import git

ROOT = Path(sys.argv[1])
services = Services.create(Settings.load(ROOT / "config.json"))
audit = []
original_resume = services.gate.resume


def observed_resume(project_id, **kwargs):
    result = original_resume(project_id, **kwargs)
    audit.append({"project_id": project_id, "monotonic_ns": time.monotonic_ns()})
    (ROOT / "gate-resume-audit.json").write_text(json.dumps(audit, indent=2))
    return result


services.gate.resume = observed_resume
app = create_app(services.settings, services=services, manage_features=True)
(ROOT / "pairing.url").write_text(
    f"{services.settings.origin}/#pair={services.auth.issue_pairing()}"
)
stop = threading.Event()


def planned_preview():
    project_id = services.registry.active_id()
    project = services.registry.get(project_id)
    destination = Path(project["path"])
    work_id, invocation_id = "followup-fixture-work", "followup-fixture-invocation"
    store = services.registry.project_store(project_id)
    with store.transaction(write=False) as tx:
        control_before_fixture = tx.require("copilot.control", "gate")
    with services.extension("previews").lock:
        services.gate.resume(
            project_id,
            reconciliation_evidence="Explicit scripted preview admission only; no native allocation",
        )
        with store.transaction() as tx:
            tx.put(
                "work",
                work_id,
                {
                    "schema_version": "1.0",
                    "project_id": project_id,
                    "work_id": work_id,
                    "originating_request_id": "followup-fixture-not-a-browser-request",
                    "directive_request_ids": [],
                    "generation_task_id": "followup-fixture-task",
                    "native_session_id": "fixture-not-an-sdk-session",
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
                    "fixture": "scripted source import, not SDK",
                },
            )
        caller = Caller(
            "tool",
            "followup-scripted-import",
            project_id,
            work_id,
            services.ownership.epoch,
            operations=("checkpoint_sources", "publishOutputs"),
            invocation_id=invocation_id,
        )
        fixture = Path("/tmp/opencode/proscenium-f01-20261114-a")
        metadata = json.loads((fixture / "fixture-evidence.json").read_text())
        paths = []
        for file in metadata["historical_files"]:
            source = fixture / "historical" / file["path"]
            assert hashlib.sha256(source.read_bytes()).hexdigest() == file["sha256"]
            target = destination / file["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            paths.append(file["path"])
        checkpoint = services.extension("sources").checkpoint(
            caller,
            {
                "schema_version": "1.0",
                "operation_id": "followup-checkpoint",
                "project_id": project_id,
                "paths": paths,
                "message": "Scripted F01 import for closed-gate cancellation; not model authoring",
                "expected_head": git(destination, "rev-parse", "--verify", "HEAD", required=False)
                .decode()
                .strip()
                or None,
            },
        )
        assert checkpoint["state"] == "completed"
        closure = {
            "entry_path": "scenes/fixture/scene.blend",
            "files": [
                {
                    "destination": f["path"],
                    "origin": {
                        "kind": "git",
                        "repository_id": project["repository_id"],
                        "commit": checkpoint["source_commit"],
                        "path": f["path"],
                        "sha256": f["sha256"],
                    },
                }
                for f in metadata["historical_files"]
            ],
            "dependencies": [],
            "required_runtime": {
                "application": "blender",
                "version": "4.5.14 LTS",
                "features": ["linked-libraries", "external-textures"],
            },
        }
        closure["digest"] = closure_digest(closure)
        spec = {
            "intent_id": "followup-planned-intent",
            "shot_id": None,
            "purpose": "keyframe",
            "source_closure_digest": closure["digest"],
            "scene_name": "Scene",
            "camera": None,
            "frames": {"start": 1, "end": 1},
            "fps": {"numerator": 24, "denominator": 1},
            "dimensions": {"width": 640, "height": 360},
            "engine": "CYCLES",
            "device": "CPU",
            "samples": 8,
            "colour_management": {
                "view_transform": "Standard",
                "look": "None",
                "exposure": 0,
                "gamma": 1,
            },
            "renderer_revision": "proscenium-blender-1",
            "review_output": {
                "media_type": "image/png",
                "encoding": "view_transformed_srgb",
                "bit_depth": 8,
            },
        }
        body = {
            "schema_version": "1.0",
            "publication_id": "followup-scripted-publication",
            "project_id": project_id,
            "items": [
                {
                    "item_id": "followup-scene",
                    "kind": "scene",
                    "name": "Unrendered cancellation fixture",
                    "version_id": "followup-scene-v1",
                    "expected_current_version_id": None,
                    "source_commit": checkpoint["source_commit"],
                    "source": closure,
                    "artifact_ids": [],
                    "preview_requests": [spec],
                }
            ],
        }
        publication, repeated = services.extension("catalogue").publish(caller, body)
        assert not repeated
        services.gate.close(project_id)
        closed_ns = time.monotonic_ns()
        with store.transaction() as tx:
            work = tx.require("work", work_id)
            tx.put(
                "work",
                work_id,
                {
                    **work,
                    "state": "completed",
                    "state_revision": 1,
                    "job_ids": publication["job_ids"],
                },
                expected_revision=0,
                schema="WorkRecord",
            )
            invocation = tx.require("invocations", invocation_id)
            tx.put(
                "invocations",
                invocation_id,
                {**invocation, "state": "completed"},
                expected_revision=0,
            )
        return {
            "fixture": "actual W02 publication/preview admission, scripted not SDK",
            "project_id": project_id,
            "checkpoint": checkpoint,
            "publication": publication,
            "submission": body,
            "closed_monotonic_ns": closed_ns,
            "r1_control_before_fixture": control_before_fixture,
        }


def watch():
    while not stop.wait(0.05):
        observation = ROOT / "observe-reattach.request"
        if observation.exists() and not (ROOT / "reattach-observed.json").exists():
            observed_project = observation.read_text()
            if (
                services.registry.active_id() == observed_project
                and observed_project in services.runtime.sessions
            ):
                with services.registry.project_store(observed_project).transaction(
                    write=False
                ) as tx:
                    resumes = tx.all("copilot.resumes")
                if resumes and all(
                    r["state"] == "completed" and r["body"]["mode"] == "reconcile_only"
                    for r in resumes
                ):
                    (ROOT / "reattach-observed.json").write_text(
                        json.dumps(
                            {
                                "project_id": observed_project,
                                "resumes": resumes,
                                "observed_monotonic_ns": time.monotonic_ns(),
                            },
                            indent=2,
                        )
                    )
        request = ROOT / "planned.request"
        claim = ROOT / "planned.claim"
        if request.exists() and not claim.exists():
            request.rename(claim)
            try:
                result = planned_preview()
                (ROOT / "planned.json").write_text(
                    json.dumps({"status": "completed", "result": result}, indent=2)
                )
            except Exception as error:
                (ROOT / "planned.json").write_text(
                    json.dumps(
                        {
                            "status": "failed",
                            "type": type(error).__name__,
                            "code": getattr(error, "code", None),
                            "message": str(error),
                        }
                    )
                )
        plan = ROOT / "planned.json"
        if plan.exists() and json.loads(plan.read_text())["status"] == "completed":
            result = json.loads(plan.read_text())["result"]
            job = services.jobs.get(result["project_id"], result["publication"]["job_ids"][0])
            if job["state"] == "cancelled" and not (ROOT / "cancel-observed.json").exists():
                (ROOT / "cancel-observed.json").write_text(
                    json.dumps(
                        {
                            "job": job,
                            "observed_monotonic_ns": time.monotonic_ns(),
                            "gate_is_open": result["project_id"] in services.gate._open,
                            "resumes_after_close": [
                                r
                                for r in audit
                                if r["monotonic_ns"] > result["closed_monotonic_ns"]
                            ],
                            "lifecycle_errors": services.extension("lifecycle").errors.copy(),
                        },
                        indent=2,
                    )
                )


thread = threading.Thread(target=watch, daemon=True, name="followup-fixture-observer")
thread.start()
url = urlsplit(services.settings.origin)
try:
    uvicorn.run(app, host=url.hostname, port=url.port, workers=1)
finally:
    stop.set()
    thread.join()
    (ROOT / "gate-resume-audit.json").write_text(json.dumps(audit, indent=2))
    services.close()
