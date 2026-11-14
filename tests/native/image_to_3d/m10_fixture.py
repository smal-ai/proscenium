"""Explicit owner-fixture import of accepted E04 bytes, then one real W03 v1 proxy.

No model request, inference, authoring Blender, scene rewriting or invented proxy mapping.
An import-only synthetic work is completed before a later real SDK association is created.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from proscenium.auth import Caller
from proscenium.catalogue import install as install_catalogue
from proscenium.configuration import Settings
from proscenium.previews import install as install_previews
from proscenium.services import Services
from proscenium.sources import closure_digest
from proscenium.sources.common import git

REPO = Path(__file__).resolve().parents[3]
SOURCE = Path("/home/quintin/.cache/proscenium-development/image-to-3d/runs/e04-live-b")
ROOT = Path("/home/quintin/.cache/proscenium-development/milestones/m10-asset-a")
CLAIM = "/tmp/opencode/proscenium-native-claims/blender-render.lock"
NAMES = (
    "asset.blend",
    "original.glb",
    "conditioning.png",
    "generation-provenance.json",
    "inspection.json",
    "model-licence.txt",
    "workflow.json",
)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    temp = path.with_suffix(".new")
    with temp.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def mem_available():
    return next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )


def original_snapshot():
    root = SOURCE / "project"
    with sqlite3.connect(f"file:{root}/.proscenium/state.sqlite3?mode=ro", uri=True) as db:
        reservations = list(
            db.execute("SELECT id,task_id,target_id,state FROM budget_reservations")
        )
        execution = json.loads(
            db.execute(
                "SELECT body FROM records WHERE namespace='providers.execution' AND id='e04-mesh-job'"
            ).fetchone()[0]
        )
    return {
        "files": {name: sha(root / "assets/e04" / name) for name in NAMES},
        "report_sha256": sha(SOURCE / "report.json"),
        "git_refs": git(root, "show-ref").decode().splitlines(),
        "reservations": reservations,
        "prompt_id": execution["prompt_id"],
        "client_id": execution["client_id"],
    }


def recipe(digest):
    return {
        "intent_id": "m10-v1-proxy",
        "shot_id": "m10-asset-shot",
        "purpose": "proxy",
        "source_closure_digest": digest,
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
    }


def main():
    ROOT.mkdir(parents=True, exist_ok=False)
    original = original_snapshot()
    accepted = json.loads((SOURCE / "report.json").read_text())
    expected = {Path(row["path"]).name: row["sha256"] for row in accepted["checkpoint"]["files"]}
    assert all(original["files"][name] == expected[name] for name in NAMES)
    assert original["reservations"] == [
        ("e04-mesh-job", "e04-generation-task", "e04-generation-task", "consumed")
    ]
    config = json.loads((REPO / "config.example.json").read_text())
    config.update(
        data_directory=str(ROOT / "machine"),
        origin="http://127.0.0.1:18860",
        frontend_directory=str(REPO / "apps/frontend/dist"),
        services=[],
        provider_options={},
    )
    config["runtime"].update(
        cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
        skill_directories=[str(REPO / "agent/skills")],
        mcp_servers={},
    )
    config["rendering"].update(claim_path=CLAIM, max_frames=1, max_pixels=640 * 360, max_samples=8)
    write(ROOT / "config.json", config)
    (ROOT / "config.json").chmod(0o600)
    settings = Settings.load(ROOT / "config.json")
    services = Services.create(settings)
    catalogue = install_catalogue(services)
    previews = install_previews(services)
    report = {
        "recorded_at": datetime.now(timezone(timedelta(hours=2))).isoformat(),
        "code_revision": git(REPO, "rev-parse", "HEAD").decode().strip(),
        "original_before": original,
        "scripted_owner_fixture": True,
        "new_inference_calls": 0,
        "cloud_roots": 0,
        "children": 0,
        "authoring_processes": 0,
        "readback_processes": 0,
        "proxy_attempts": 0,
        "cpu_stills": 0,
        "shared_claim_path": CLAIM,
        "memory_observations": [],
    }
    owner = Caller("owner", "m10-scripted-owner-fixture")
    project_id = None
    caller = None
    try:
        project, _ = services.extension("projects").open(
            owner,
            {
                "schema_version": "1.0",
                "operation_id": "m10-fixture-open",
                "path": str(ROOT / "project"),
                "create": True,
                "name": "M10 retained Hunyuan asset",
            },
        )
        project_id = project["project_id"]
        services.activate_project(project_id)
        services.extension("projects").update_settings(
            owner,
            {
                "schema_version": "1.0",
                "operation_id": "m10-fixture-profile",
                "project_id": project_id,
                "expected_revision": 0,
                "generation_attempt_limit": 3,
                "default_profile_id": "authoring",
            },
        )
        services.gate.resume(
            project_id,
            reconciliation_evidence="Explicit M10 owner-fixture import and bounded CPU proxy",
        )
        root = Path(project["path"])
        destination = root / "assets/e04"
        destination.mkdir()
        for name in NAMES:
            shutil.copyfile(SOURCE / "project/assets/e04" / name, destination / name)
            assert sha(destination / name) == original["files"][name]
        provenance = {
            "fixture": "Explicit owner import of accepted original bytes. Not new generated content.",
            "original_project_id": accepted["project"]["project_id"],
            "original_source_commit": accepted["checkpoint"]["source_commit"],
            "original_job": accepted["job"],
            "original_graph_sha256": accepted["execution"]["graph_sha256"],
            "original_model": accepted["verified_weights"],
            "original_input": accepted["reference"],
            "original_hashes": original["files"],
        }
        write(destination / "import-provenance.json", provenance)
        git(root, "config", "user.name", "M10 Owner Fixture")
        git(root, "config", "user.email", "m10-fixture@example.invalid")
        with services.registry.project_store(project_id).transaction() as tx:
            tx.put(
                "work",
                "m10-import-fixture",
                {
                    "schema_version": "1.0",
                    "project_id": project_id,
                    "work_id": "m10-import-fixture",
                    "originating_request_id": "m10-owner-import",
                    "directive_request_ids": [],
                    "state": "running",
                    "state_revision": 0,
                    "native_session_id": "fixture-import-not-an-sdk-session",
                    "job_ids": [],
                    "generation_task_id": "m10-import-no-inference",
                },
                schema="WorkRecord",
            )
            tx.put(
                "invocations",
                "m10-import-invocation",
                {
                    "work_id": "m10-import-fixture",
                    "owner_epoch": services.ownership.epoch,
                    "state": "running",
                },
            )
        caller = Caller(
            "tool",
            "m10-owner-import-fixture",
            project_id,
            "m10-import-fixture",
            services.ownership.epoch,
            operations=("checkpoint_sources", "publishOutputs"),
            invocation_id="m10-import-invocation",
        )
        artifacts = services.extension("artifacts")
        registered = []
        for name, kind, mime, purpose in (
            ("original.glb", "source_blob", "model/gltf-binary", "inspection"),
            ("conditioning.png", "image", "image/png", "reference"),
            ("generation-provenance.json", "document", "application/json", "metadata"),
            ("import-provenance.json", "document", "application/json", "metadata"),
        ):
            artifact = artifacts.promote(
                project_id,
                "m10-import-" + name.split(".")[0],
                (destination / name).read_bytes(),
                kind=kind,
                purpose=purpose,
                media_type=mime,
            )
            with services.registry.project_store(project_id).transaction() as tx:
                artifacts.put(tx, artifact)
            registered.append(artifact)
        paths = ["project.json", ".gitattributes", ".gitignore"] + sorted(
            str(p.relative_to(root)) for p in destination.iterdir()
        )
        checkpoint = services.extension("sources").checkpoint(
            caller,
            {
                "schema_version": "1.0",
                "operation_id": "m10-import-checkpoint",
                "project_id": project_id,
                "paths": paths,
                "expected_head": None,
                "message": "test: explicit owner import of accepted Hunyuan source and provenance",
            },
        )
        closure = {
            "entry_path": "assets/e04/asset.blend",
            "files": [
                {
                    "destination": path,
                    "origin": {
                        "kind": "git",
                        "repository_id": project["repository_id"],
                        "commit": checkpoint["source_commit"],
                        "path": path,
                        "sha256": sha(root / path),
                    },
                }
                for path in paths
            ],
            "dependencies": [],
            "required_runtime": {"application": "blender", "version": "4.5.14 LTS", "features": []},
            "digest": "0" * 64,
        }
        closure["digest"] = closure_digest(closure)
        publication, _ = catalogue.publish(
            caller,
            {
                "schema_version": "1.0",
                "publication_id": "m10-import-publication",
                "project_id": project_id,
                "items": [
                    {
                        "item_id": "m10-generated-asset",
                        "version_id": "m10-asset-v1",
                        "kind": "asset",
                        "name": "Imported Hunyuan mesh",
                        "expected_current_version_id": None,
                        "source_commit": checkpoint["source_commit"],
                        "source": closure,
                        "artifact_ids": [a["artifact_id"] for a in registered],
                        "preview_requests": [recipe(closure["digest"])],
                        "description": "Accepted E04 native mesh. Owner fixture import only. Untextured and uncalibrated scale.",
                        "provenance": {
                            "source_url": "https://huggingface.co/tencent/Hunyuan3D-2",
                            "creator": "Tencent",
                            "licence": "Tencent Hunyuan Community Licence",
                            "note": "Original E04 inference/checkpoint/hash retained in import-provenance.json. No new generation.",
                        },
                    }
                ],
            },
        )
        job_id = publication["job_ids"][0]
        report.update(
            project=project,
            checkpoint=checkpoint,
            publication=publication,
            registered=registered,
            closure=closure,
            job_id=job_id,
            proxy_attempts=1,
        )
        write(ROOT / "fixture.json", report)
        started = time.monotonic()
        cancel_requested = False
        while time.monotonic() - started < 360:
            job = services.jobs.get(project_id, job_id)
            available = mem_available()
            report["memory_observations"].append(
                {
                    "monotonic_ns": time.monotonic_ns(),
                    "mem_available_bytes": available,
                    "job_state": job["state"],
                }
            )
            if available < 5 * 1024**3 and not cancel_requested:
                services.jobs.cancel(
                    owner,
                    job_id,
                    {
                        "schema_version": "1.0",
                        "operation_id": "m10-headroom-cancel",
                        "project_id": project_id,
                        "expected_revision": job["state_revision"],
                    },
                )
                cancel_requested = True
            previews.pump(project_id)
            previews.reconcile(project_id)
            job = services.jobs.get(project_id, job_id)
            report["job"] = job
            write(ROOT / "fixture.json", report)
            if job["state"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.5)
        report["job_root"] = str(previews.root(job))
        report["version"] = catalogue.version(project_id, "m10-asset-v1")
        if job["state"] == "succeeded":
            report["proxy_mapping"] = catalogue.proxy_mapping(project_id, "m10-asset-v1")
        else:
            report["blocker"] = (
                "Actual W03 source-only v1 proxy did not succeed. Preserve its identity before correction."
            )
        with services.registry.project_store(project_id).transaction() as tx:
            work = tx.require("work", "m10-import-fixture")
            work.update(state="completed", state_revision=work["state_revision"] + 1)
            tx.put(
                "work",
                "m10-import-fixture",
                work,
                expected_revision=tx.revision("work", "m10-import-fixture"),
                schema="WorkRecord",
            )
            invocation = tx.require("invocations", "m10-import-invocation")
            invocation["state"] = "completed"
            tx.put(
                "invocations",
                "m10-import-invocation",
                invocation,
                expected_revision=tx.revision("invocations", "m10-import-invocation"),
            )
            report["new_generation_reservations"] = tx.connection.execute(
                "SELECT count(*) FROM budget_reservations"
            ).fetchone()[0]
            report["copilot_association_before_real_sdk"] = tx.get(
                "copilot.session", "native", required=False
            )
    finally:
        previews.close()
        services.close()
        report["original_after"] = original_snapshot()
        report["original_preserved"] = report["original_after"] == report["original_before"]
        report["finished_at"] = datetime.now(timezone(timedelta(hours=2))).isoformat()
        write(ROOT / "fixture.json", report)
        print(
            json.dumps(
                {
                    "root": str(ROOT),
                    "job": report.get("job"),
                    "blocker": report.get("blocker"),
                    "original_preserved": report["original_preserved"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
