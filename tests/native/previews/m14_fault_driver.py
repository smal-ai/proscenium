"""Bounded C16/C25 operator fault fixture using unchanged production admission/workers.

No SDK session is created. Seeded work/invocation records are synthetic admission
context, NOT new Copilot invocations or evidence of model authorship in this run.
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue import install as catalogue_install
from proscenium.catalogue.router import router as catalogue_router
from proscenium.configuration import Settings
from proscenium.previews import install
from proscenium.previews.journal import alive, atomic, read, renderer_hashes, sha
from proscenium.previews.service import worker_caller
from proscenium.reviews import install as reviews_install
from proscenium.services import Services

REPO = Path(__file__).resolve().parents[3]
ROOT = Path("/home/quintin/.cache/proscenium-development/milestones/m14-native-faults-a")
ORIGIN = Path(
    "/home/quintin/.cache/proscenium-development/milestones/geometry-nodes-20261114-b/project"
)
COMMIT = "fd6282b0a22725298d5f155181a8b64cabc762e0"
BLEND_SHA = "fd0d1293080a1cd9f0bfaf3feace1488ec6c85e86705159a046daf07d35e0cd8"
PROJECT = "m14-native-fault-operator-project"
WORK = "m14-synthetic-admission-work"
INVOCATION = "m14-synthetic-admission-invocation"
INJECTED = (
    b"OPERATOR-INJECTED UNRECEIPTED PARTIAL PNG\nnot native-generated\n\x89PNG\r\n\x1a\npartial"
)
GUARD = "Unreceipted output requires reconciliation, not overwrite"
MEDIA_COOKIE = None


def memory():
    value = next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    if value < 5 * 1024**3:
        raise RuntimeError("Protected 4 GiB host headroom plus launch margin unavailable")
    return value


def git(path, *args):
    return subprocess.check_output(
        ["git", "-C", str(path), *args], text=True, env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
    ).strip()


def plan():
    accepted = read(REPO / "docs/evidence/geometry-nodes/final-run.json")
    version = accepted["source_version"]
    assert version["source_commit"] == COMMIT
    assert version["source"]["files"][0]["origin"]["sha256"] == BLEND_SHA
    spec = copy.deepcopy(version["preview_requests"][1])
    spec.update(
        intent_id="m14-failed-writer-same-job-retry",
        purpose="draft_motion",
        frames={"start": 1, "end": 3},
    )
    return {
        "version": version,
        "spec": spec,
        "workers": 2,
        "actual_frames_planned": 3,
        "encodes_planned": 1,
        "injection_frame": 2,
        "injected_hex": INJECTED.hex(),
        "injected_sha256": sha(INJECTED),
        "guard": GUARD,
        "renderer_hashes": renderer_hashes(),
    }


def prepare():
    specification = plan()
    memory()
    ROOT.mkdir(mode=0o700)  # Existing root is a hard stop, never an implicit rerun.
    assert git(ORIGIN, "rev-parse", COMMIT) == COMMIT
    source_before = {
        "head": git(ORIGIN, "rev-parse", "HEAD"),
        "refs": git(ORIGIN, "show-ref"),
        "blend_sha256": sha((ORIGIN / "assets/procedural/asset.blend").read_bytes()),
    }
    assert source_before["blend_sha256"] == BLEND_SHA
    atomic(ROOT / "source-before.json", source_before)
    subprocess.run(
        ["git", "clone", "--no-hardlinks", "--no-checkout", str(ORIGIN), str(ROOT / "project")],
        check=True,
        env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"},
    )
    project = ROOT / "project"
    shutil.copytree(ORIGIN / ".git/lfs/objects", project / ".git/lfs/objects", dirs_exist_ok=True)
    git(project, "checkout", "--detach", COMMIT)
    finish_import(specification, source_before)


def finish_import(specification, source_before):
    project = ROOT / "project"
    for key, value in {
        "clean": "git-lfs clean -- %f",
        "smudge": "git-lfs smudge -- %f",
        "process": "git-lfs filter-process",
        "required": "true",
    }.items():
        git(project, "config", "--local", "filter.lfs." + key, value)
    subprocess.run(["git", "-C", str(project), "lfs", "checkout"], check=True)
    assert git(project, "rev-parse", "HEAD") == COMMIT
    assert not (project / ".proscenium").exists()
    assert (
        sha((project / specification["version"]["source"]["entry_path"]).read_bytes()) == BLEND_SHA
    )
    # Git does not preserve empty configured roots. Create these only inside the clone.
    for name in (
        "assets",
        "scenes",
        "storyboards",
        "sequences",
        "renders",
        "references",
        "scripts",
    ):
        (project / name).mkdir(exist_ok=True)
    atomic(
        ROOT / "prepared.json",
        {
            "plan": specification,
            "source_before": source_before,
            "import": "Exact accepted Git history and local LFS bytes. No runtime database, SDK history or authoring helper executed.",
            "project_head": git(project, "rev-parse", "HEAD"),
            "project_status": git(project, "status", "--short"),
        },
    )
    print(
        json.dumps({"prepared": str(ROOT), "workers_started": 0, "plan": specification}, indent=2)
    )


def resume_import():
    assert not (ROOT / "allocation.json").exists() and not (ROOT / "prepared.json").exists()
    assert not (ROOT / "project/.proscenium").exists()
    source_before = {
        "head": git(ORIGIN, "rev-parse", "HEAD"),
        "refs": git(ORIGIN, "show-ref"),
        "blend_sha256": sha((ORIGIN / "assets/procedural/asset.blend").read_bytes()),
    }
    assert source_before["blend_sha256"] == BLEND_SHA
    atomic(
        ROOT / "import-preparation-repair.json",
        {
            "previous_stage": "Local clone completed, git lfs checkout declined unconfigured local filters; no workers or admission",
            "repair_scope": str(ROOT / "project/.git/config"),
            "source_before_repair": source_before,
        },
    )
    finish_import(plan(), source_before)


def container():
    return Services.create(
        Settings(
            data_directory=ROOT / "machine",
            free_space_reserve_bytes=4 * 1024**3,
            execution_profiles=frozenset({"blender-cpu"}),
            rendering={
                "blender": "/home/quintin/.local/bin/blender",
                "threads": 4,
                "timeout": 300,
                "encode_timeout": 60,
                "max_frames": 3,
                "max_pixels": 640 * 360,
                "max_samples": 8,
            },
        )
    )


def setup():
    services = container()
    original = plan()["version"]
    repository = original["source"]["files"][0]["origin"]["repository_id"]
    services.registry.register(
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            repository_id=repository,
            name="Imported actual source history / operator fault fixture",
            path=str(ROOT / "project"),
            native_session_id=None,
            state="inactive",
            state_revision=0,
        )
    )
    services.activate_project(PROJECT)
    catalogue, previews, reviews = (
        catalogue_install(services),
        install(services),
        reviews_install(services),
    )
    services.gate.resume(
        PROJECT,
        reconciliation_evidence="Explicit bounded C16/C25 operator allocation, zero SDK calls",
    )
    caller = Caller(
        "tool",
        "m14-operator-fixture",
        PROJECT,
        WORK,
        services.ownership.epoch,
        operations=("publishOutputs", "requestPreview", "retryToolJob"),
        invocation_id=INVOCATION,
    )
    with services.registry.project_store(PROJECT).transaction() as tx:
        tx.put("project_identity", "identity", {"project_id": PROJECT, "repository_id": repository})
        tx.put(
            "work",
            WORK,
            dict(
                schema_version="1.0",
                project_id=PROJECT,
                work_id=WORK,
                originating_request_id="m14-synthetic-admission-request",
                directive_request_ids=[],
                state="running",
                state_revision=0,
                native_session_id="operator-fixture-not-a-model-session",
                job_ids=[],
                generation_task_id="m14-no-generation",
            ),
            schema="WorkRecord",
        )
        tx.put(
            "invocations",
            INVOCATION,
            {"work_id": WORK, "owner_epoch": services.ownership.epoch, "state": "running"},
        )
    receipt, _ = catalogue.publish(
        caller,
        dict(
            schema_version="1.0",
            publication_id="m14-imported-publication",
            project_id=PROJECT,
            items=[
                dict(
                    item_id=original["item_id"],
                    version_id=original["version_id"],
                    kind="asset",
                    name="Imported actual GeometryNodes-b source / operator fault fixture",
                    expected_current_version_id=None,
                    source_commit=COMMIT,
                    source=original["source"],
                    artifact_ids=[],
                    preview_requests=[plan()["spec"]],
                )
            ],
        ),
    )
    return services, previews, reviews, caller, services.jobs.get(PROJECT, receipt["job_ids"][0])


def admit():
    assert (ROOT / "prepared.json").exists() and not (ROOT / "machine").exists()
    services, previews, _, _, job = setup()
    try:
        source = services.extension("sources").materialise(PROJECT, plan()["version"]["version_id"])
        assert sha((source / plan()["version"]["source"]["entry_path"]).read_bytes()) == BLEND_SHA
        config = previews.runtime(PROJECT, job)
        assert (
            config["threads"] == 4 and config["timeout"] == 300 and config["encode_timeout"] == 60
        )
        assert config["claim_path"] == "/tmp/opencode/proscenium-native-claims/blender-render.lock"
        assert not (previews.root(job) / "launch.claim").exists()
        atomic(
            ROOT / "admission-preflight.json",
            {
                "job": job,
                "runtime": config,
                "source_root": str(source),
                "blend_sha256": BLEND_SHA,
                "native_spawns": 0,
            },
        )
    finally:
        services.close()


def reopen():
    services = container()
    catalogue_install(services)
    previews, reviews = install(services), reviews_install(services)
    services.gate.resume(
        PROJECT,
        reconciliation_evidence="Explicit bounded native fault execution after zero-spawn admission preflight",
    )
    caller = Caller(
        "tool",
        "m14-operator-fixture",
        PROJECT,
        WORK,
        services.ownership.epoch,
        operations=("retryToolJob",),
        invocation_id=INVOCATION,
    )
    job = services.jobs.get(PROJECT, read(ROOT / "admission-preflight.json")["job"]["job_id"])
    assert job["state"] == "planned" and job["attempt_number"] == 1
    return services, previews, reviews, caller, job


def snapshot(services):
    with services.registry.project_store(PROJECT).transaction(write=False) as tx:
        return {
            name: tx.all(name)
            for name in (
                "jobs",
                "attempts",
                "job_evidence",
                "frames",
                "segments",
                "artifacts",
                "selections",
                "versions",
            )
        }


def wait(previews, job):
    root = previews.root(job)
    deadline = time.monotonic() + 380
    observed = {"headroom_min_bytes": memory(), "live_identities": {}}
    while time.monotonic() < deadline:
        observed["headroom_min_bytes"] = min(observed["headroom_min_bytes"], memory())
        for name in ("process.json", "native-process.json"):
            if (root / name).exists() and alive(read(root / name)):
                observed["live_identities"][name] = read(root / name)
        previews.reconcile(PROJECT)
        current = previews.services.jobs.get(PROJECT, job["job_id"])
        if current["state"] in {"failed", "succeeded", "cancelled"}:
            assert (root / "terminal.json").exists()
            # Terminal journal precedes supervisor exit. Observe exit before next launch.
            while alive(read(root / "process.json")) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not alive(read(root / "process.json"))
            assert not alive(read(root / "native-process.json"))
            return current, observed
        time.sleep(0.02)
    raise RuntimeError("Allocated attempt did not settle. Preserve stage, no replacement launch.")


def attempt_evidence(previews, job, observations):
    root = previews.root(job)
    files = {
        path.relative_to(root).as_posix(): {
            "sha256": sha(path.read_bytes()),
            "bytes": path.stat().st_size,
        }
        for path in root.rglob("*")
        if path.is_file()
    }
    journals = {path.name: read(path) for path in root.glob("*.json")}
    return {
        "job": job,
        "root": str(root),
        "observations": observations,
        "files": files,
        "journals": journals,
        "availability": previews.availability(PROJECT, job["job_id"]),
        "blender_log": (root / "blender.log").read_text(),
    }


def served_media(services, artifact_id):
    global MEDIA_COOKIE
    # Production owner-authenticated content route, in-process ASGI only. No UI/SDK/lifespan.
    app = FastAPI()
    app.state.services = services
    app.include_router(catalogue_router)
    if MEDIA_COOKIE is None:
        _, MEDIA_COOKIE, _ = services.auth.pair(
            dict(
                schema_version="1.0",
                operation_id="m14-media-owner-pair",
                pairing_code=services.auth.issue_pairing(),
                client_nonce="m14-operator-media-client",
            )
        )
    with TestClient(app, cookies={"proscenium_owner": MEDIA_COOKIE}) as client:
        url = f"/api/v1/projects/{PROJECT}/artifacts/{artifact_id}/content"
        full = client.get(url)
        partial = client.get(url, headers={"Range": "bytes=0-15"})
        assert full.status_code == 200 and partial.status_code == 206
        assert partial.content == full.content[:16]
        return {
            "mode": "Actual production route and owner-cookie dependency through in-process ASGI, not browser UI",
            "url": url,
            "status": full.status_code,
            "sha256": sha(full.content),
            "bytes": len(full.content),
            "etag": full.headers["etag"],
            "content_type": full.headers["content-type"],
            "range_status": partial.status_code,
            "range_bytes": len(partial.content),
        }


def run():
    prepared = read(ROOT / "prepared.json")
    assert prepared["plan"] == plan(), "Sealed preflight changed"
    marker = ROOT / "allocation.json"
    with marker.open("x") as output:
        json.dump(
            {
                "driver_commit": git(REPO, "rev-parse", "HEAD"),
                "max_workers": 2,
                "max_actual_frames": 4,
                "no_implicit_relaunch": True,
            },
            output,
        )
    evidence = {
        "status": "in_progress",
        "authority": "Explicit operator fixture, synthetic admission records, zero SDK/model/provider calls",
        "prepared": prepared,
        "attempts": [],
    }
    services = None
    try:
        services, previews, reviews, caller, job = reopen()
        first_root = previews.root(job)
        injection = first_root / "frames/00000002.png"
        injection.parent.mkdir(parents=True)
        with injection.open("xb") as output:
            output.write(INJECTED)
        evidence["injection"] = {
            "path": str(injection),
            "hex": INJECTED.hex(),
            "sha256": sha(INJECTED),
            "bytes": len(INJECTED),
            "stage": "Before worker 1 launch, frame 2 output, no receipt",
            "native_generated": False,
            "disk_exhaustion": False,
        }
        atomic(ROOT / "result.json", evidence)
        memory()
        evidence["first_pump"] = previews.pump(PROJECT)
        failed, observations = wait(previews, job)
        first = attempt_evidence(previews, failed, observations)
        evidence["attempts"].append(first)
        atomic(ROOT / "result.json", evidence)
        assert failed["state"] == "failed" and failed["native_exit_code"] == 1
        assert failed["error_code"] == "render_failed"
        assert GUARD in first["blender_log"]
        assert first["availability"]["available_ranges"] == [{"start": 1, "end": 1}]
        assert first["availability"]["failed_ranges"] == [{"start": 2, "end": 3}]
        assert first["availability"]["playable_ranges"] == []
        assert not (first_root / "frame-00000002.json").exists()
        assert injection.read_bytes() == INJECTED
        first_receipt = read(first_root / "frame-00000001.json")
        artifact = previews.artifacts.ready_path(PROJECT, first_receipt["artifact_id"])
        assert sha(artifact.read_bytes()) == first_receipt["sha256"]
        evidence["reviewable_frame1"] = {
            "artifact_path": str(artifact),
            "sha256": sha(artifact.read_bytes()),
            "receipt": first_receipt,
            "verified_after_terminal_failure": True,
        }
        evidence["served_frame1_after_failure"] = served_media(
            services, first_receipt["artifact_id"]
        )
        owner = Caller("owner", "m14-scripted-owner", PROJECT)
        evidence["selection_receipt"] = reviews.save_selection(
            owner,
            dict(
                schema_version="1.0",
                operation_id="m14-select-source-with-reviewable-frame1",
                project_id=PROJECT,
                selection_id="m14-selected-source",
                expected_revision=0,
                slot={"kind": "item", "item_id": plan()["version"]["item_id"]},
                choice={
                    "kind": "version",
                    "version": {
                        "item_id": plan()["version"]["item_id"],
                        "version_id": plan()["version"]["version_id"],
                    },
                },
                action="select",
            ),
        )
        evidence["before_retry"] = snapshot(services)
        retry_body = dict(
            schema_version="1.0",
            operation_id="m14-explicit-owner-authorised-tool-retry",
            project_id=PROJECT,
            job_id=failed["job_id"],
            expected_revision=failed["state_revision"],
        )
        evidence["retry_body"] = retry_body
        retried, replayed = services.jobs.retry(caller, retry_body)
        assert not replayed
        assert (
            retried["job_id"] == failed["job_id"]
            and retried["input_digest"] == failed["input_digest"]
        )
        assert retried["attempt_id"] != failed["attempt_id"] and retried["attempt_number"] == 2
        before_probes = snapshot(services)
        old_caller = worker_caller(failed)
        evidence["old_exact_receipt_replay"] = previews.register(old_caller, first_receipt)
        assert evidence["old_exact_receipt_replay"][1] is True
        fresh_stale = {**first_receipt, "receipt_id": failed["attempt_id"] + "-forged-late"}
        try:
            previews.register(old_caller, fresh_stale)
        except DomainError as exc:
            evidence["old_fresh_receipt_rejection"] = exc.code
        else:
            raise AssertionError("Fresh stale receipt accepted")
        try:
            previews.state(
                failed,
                "succeeded",
                exit_code=0,
                evidence="Explicit stale state rejection probe, not a native exit assertion",
            )
        except DomainError as exc:
            evidence["old_state_rejection"] = exc.code
        else:
            raise AssertionError("Stale attempt state accepted")
        assert snapshot(services) == before_probes
        evidence["stale_probes_no_mutation"] = True
        atomic(ROOT / "result.json", evidence)
        memory()
        evidence["second_pump"] = previews.pump(PROJECT)
        second_root = previews.root(retried)
        adopted = read(second_root / "frame-00000001.json")
        assert adopted["attempt_id"] == retried["attempt_id"]
        assert adopted["artifact_id"] == first_receipt["artifact_id"]
        assert adopted["sha256"] == first_receipt["sha256"]
        evidence["adopted_receipt_before_reconciliation"] = adopted
        complete, observations = wait(previews, retried)
        second = attempt_evidence(previews, complete, observations)
        evidence["attempts"].append(second)
        atomic(ROOT / "result.json", evidence)
        assert complete["state"] == "succeeded" and complete["native_exit_code"] == 0
        assert second["availability"]["available_ranges"] == [{"start": 1, "end": 3}]
        assert second["availability"]["playable_ranges"] == [{"start": 1, "end": 3}]
        assert not (second_root / "timing-00000001.json").exists()
        assert sorted(path.name for path in first_root.glob("timing-*.json")) == [
            "timing-00000001.json"
        ]
        assert sorted(path.name for path in second_root.glob("timing-*.json")) == [
            "timing-00000002.json",
            "timing-00000003.json",
        ]
        assert len(list(first_root.glob("segment-*.json"))) == 0
        assert len(list(second_root.glob("segment-*.json"))) == 1
        for name, metadata in first["files"].items():
            assert sha((first_root / name).read_bytes()) == metadata["sha256"]
        assert sha(artifact.read_bytes()) == first_receipt["sha256"]
        evidence["served_frame1_after_retry"] = served_media(services, first_receipt["artifact_id"])
        assert evidence["served_frame1_after_failure"] == evidence["served_frame1_after_retry"]
        evidence["after_retry"] = snapshot(services)
        assert evidence["after_retry"]["selections"] == evidence["before_retry"]["selections"]
        evidence["actual_rendered_frames"] = 3
        evidence["normal_segment_encodes"] = 1
        evidence["status"] = "bounded_C16_failed_writer_C25_same_job_retry_pass"
    except Exception as exc:
        evidence["status"] = "retained_failed_stage_no_extra_allocation"
        evidence["blocker"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        evidence["source_after"] = {
            "head": git(ORIGIN, "rev-parse", "HEAD"),
            "refs": git(ORIGIN, "show-ref"),
            "blend_sha256": sha((ORIGIN / "assets/procedural/asset.blend").read_bytes()),
        }
        evidence["source_preserved"] = evidence["source_after"] == prepared["source_before"]
        evidence["clone_blend_sha256_after"] = sha(
            (ROOT / "project/assets/procedural/asset.blend").read_bytes()
        )
        evidence["cleanup"] = {
            str(path): {"identity": read(path), "alive": alive(read(path))}
            for path in ROOT.glob("project/.proscenium/jobs/previews/**/*process.json")
        }
        atomic(ROOT / "result.json", evidence)
        if services:
            services.close()
        print(
            json.dumps(
                {
                    "status": evidence["status"],
                    "root": str(ROOT),
                    "blocker": evidence.get("blocker"),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    {"prepare": prepare, "resume-import": resume_import, "admit": admit, "run": run}[sys.argv[1]]()
