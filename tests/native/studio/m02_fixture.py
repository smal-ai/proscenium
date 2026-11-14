"""Explicit scripted import/admission fixture. Never presented as SDK/model effects."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from proscenium.auth import Caller
from proscenium.sources import closure_digest
from proscenium.sources.common import git
from proscenium.sources.inspection import BlenderInspectionAllocation, inspect_blender

F01 = Path("/tmp/opencode/proscenium-f01-20261114-a")
M01 = Path.home() / ".cache/proscenium-development/milestones/m01-20261114T132348Z"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state(root):
    paths = [
        root / ".git/index",
        root / "assets/cube/colour.png",
        root / "assets/cube/asset.blend",
        root / "brief/staged-note.txt",
        root / "brief/unpublished.txt",
    ]
    return {
        "head": git(root, "rev-parse", "HEAD").decode().strip(),
        "files": {str(p.relative_to(root)): sha(p) for p in paths if p.exists()},
        "status": git(root, "status", "--porcelain=v1").decode(),
    }


def apply(services, run_root, command):
    project_id = services.registry.active_id()
    root = Path(services.registry.get(project_id)["path"])
    action = command["action"]
    if action == "diagnose-old-event":
        from proscenium.api.errors import DomainError

        with services.registry.store.transaction(write=False) as tx:
            inactive = next(p for p in tx.all("projects") if p["project_id"] != project_id)
        sid = inactive["native_session_id"]
        history = run_root / "machine/copilot-runtime/home/session-state" / sid / "events.jsonl"
        event = next(
            e
            for e in reversed([json.loads(line) for line in history.read_text().splitlines()])
            if e["type"] == "session.shutdown"
        )
        try:
            services.runtime.state.reduce(inactive["project_id"], sid, event)
        except DomainError as error:
            return {
                "fixture": "targeted diagnosis using actual retained terminal SDK event; no inference or replay of a prompt",
                "event_id": event["id"],
                "event_type": event["type"],
                "native_session_id": sid,
                "inactive_project_id": inactive["project_id"],
                "active_project_id": project_id,
                "reducer_error": error.code,
                "unchanged_active_project_id": services.registry.active_id(),
            }
        raise AssertionError("Expected scoped inactive event refusal at this accepted base")
    if action == "mtime-trap":
        ghost = root / "scenes/newest-unpublished/scene.blend"
        ghost.parent.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(F01 / "project/scenes/fixture/scene.blend", ghost)
        os.utime(ghost, (1893456000, 1893456000))
        return {
            "fixture": "newest filesystem source is deliberately unpublished",
            "path": str(ghost),
            "sha256": sha(ghost),
            "mtime_ns": ghost.stat().st_mtime_ns,
            "state": state(root),
        }
    if action == "dispatch-cancel":
        from proscenium.db.outbox import dispatch_one
        from proscenium.previews.runner import cancel

        store = services.registry.project_store(project_id)
        with store.transaction(write=False) as tx:
            rows = list(
                tx.connection.execute(
                    "SELECT id,body FROM outbox WHERE kind='job.cancel' AND state='pending'"
                )
            )
        assert len(rows) == 1
        dispatch_one(
            store,
            rows[0]["id"],
            services.gate,
            {"job.cancel": lambda body: cancel(services.extension("previews"), project_id, body)},
        )
        job_id = json.loads(rows[0]["body"])["job_id"]
        return {
            "fixture": "trusted explicit dispatch of actual owner-HTTP cancellation via production outbox/adapter; lifecycle closed-gate workaround",
            "job": services.jobs.get(project_id, job_id),
        }
    if action == "inspect":
        assert not any(
            json.loads(path.read_text()).get("command", {}).get("action") == "inspect"
            for path in (run_root / "receipts").glob("*.json")
        ), "Single native inspection allocation already consumed"
        with (run_root / "native-inspection.claim").open("x") as claim:
            claim.write(
                "One background old-source inspection reserved. Unknown outcomes are never retried.\n"
            )
            claim.flush()
            os.fsync(claim.fileno())
        before = state(root)
        directory = services.extension("sources").materialise(project_id, "scene-main-v1")
        processes = []
        original_popen = subprocess.Popen

        class ObservedPopen(original_popen):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                argv = args[0]
                if "blender" in str(argv[0]):
                    processes.append(
                        {
                            "pid": self.pid,
                            "start": Path(f"/proc/{self.pid}/stat")
                            .read_text()
                            .split(") ")[1]
                            .split()[19],
                            "argv": argv,
                        }
                    )

        # Observes the actual process only. Does not replace Blender or its results.
        with patch.object(subprocess, "Popen", ObservedPopen):
            report = inspect_blender(
                services,
                project_id,
                directory,
                BlenderInspectionAllocation(
                    "m02-one-real-old-source-open",
                    Path(shutil.which("blender")),
                    threads=4,
                    timeout=180,
                ),
                expected_digest=services.extension("catalogue").version(
                    project_id, "scene-main-v1"
                )["source"]["digest"],
            )
        after = state(root)
        assert before == after
        return {
            "fixture": "actual single background Blender inspection, no render",
            "materialisation": str(directory),
            "before": before,
            "after": after,
            "report": report,
            "processes": processes,
        }
    if action == "state":
        return state(root)
    if action == "dirty":
        (root / "assets/cube/colour.png").write_bytes(
            (F01 / "historical/assets/cube/colour.png").read_bytes()
        )
        (root / "brief/staged-note.txt").write_text(
            "Scripted fixture staging that historical browsing must preserve.\n"
        )
        (root / "brief/unpublished.txt").write_text(
            "Unpublished working change. Not a publication.\n"
        )
        git(root, "add", "--", "brief/staged-note.txt")
        os.utime(root / "assets/cube/asset.blend", (1, 1))
        return state(root)
    assert action in {"seed", "advance", "finish"}
    operation = f"m02-fixture-{action}"
    work_id, invocation_id = operation + "-work", operation + "-invocation"
    store = services.registry.project_store(project_id)
    owner = Caller("owner", "m02-scripted-fixture-owner-not-human")
    caller = Caller(
        "tool",
        "m02-scripted-import-tool-not-sdk",
        project_id,
        work_id,
        services.ownership.epoch,
        operations=("checkpoint_sources", "publishOutputs"),
        invocation_id=invocation_id,
    )
    # The real preview adapter's lock excludes dispatch while the explicit fixture
    # opens admission, publishes and recloses the gate. No renderer is authorised.
    with services.extension("previews").lock:
        services.gate.resume(
            project_id,
            reconciliation_evidence="Explicit M02 scripted import allocation, not SDK execution",
        )
        with store.transaction() as tx:
            tx.put(
                "work",
                work_id,
                {
                    "schema_version": "1.0",
                    "project_id": project_id,
                    "work_id": work_id,
                    "originating_request_id": operation + "-fixture-request",
                    "directive_request_ids": [],
                    "generation_task_id": operation + "-task",
                    "native_session_id": "m02-fixture-not-a-native-session",
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
                    "fixture": "scripted import, not native SDK invocation",
                },
            )
        metadata = json.loads((F01 / "fixture-evidence.json").read_text())
        label = "historical" if action == "seed" else "current"
        imported = []
        if action != "finish":
            origin_root = F01 / ("historical" if action == "seed" else "project")
            for record in metadata[label + "_files"]:
                source = origin_root / record["path"]
                assert sha(source) == record["sha256"]
                path = root / record["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, path)
                imported.append({"from": str(source), "to": record["path"], "sha256": sha(path)})
            if action == "seed":
                for source, target in [
                    ("assets/cube/asset.blend", "assets/companion/asset.blend"),
                    ("assets/cube/colour.png", "assets/companion/colour.png"),
                    ("scenes/fixture/scene.blend", "scenes/alternate/scene.blend"),
                ]:
                    path = root / target
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(root / source, path)
        else:
            (root / "assets/companion/colour.png").write_bytes(
                (F01 / "project/assets/cube/colour.png").read_bytes()
            )
            for name in ("asset.blend", "colour.png"):
                shutil.copyfile(
                    F01 / "project/assets/cube" / name, root / "assets/unfinished" / name
                )
        paths = ["assets/cube/asset.blend", "assets/cube/colour.png", "scenes/fixture/scene.blend"]
        if action == "seed":
            paths += [
                "assets/companion/asset.blend",
                "assets/companion/colour.png",
                "scenes/alternate/scene.blend",
            ]
            (root / "assets/unfinished").mkdir()
            (root / "assets/unfinished/draft.txt").write_text(
                "UNFINISHED SCRIPTED FIXTURE. Not eligible for publication.\n"
            )
        if action == "finish":
            paths = [
                "assets/companion/asset.blend",
                "assets/companion/colour.png",
                "scenes/alternate/scene.blend",
                "assets/unfinished/asset.blend",
                "assets/unfinished/colour.png",
            ]
        expected = (
            git(root, "rev-parse", "--verify", "HEAD", required=False).decode().strip() or None
        )
        checkpoint = services.extension("sources").checkpoint(
            caller,
            {
                "schema_version": "1.0",
                "operation_id": operation + "-checkpoint",
                "project_id": project_id,
                "paths": paths,
                "message": f"M02 scripted developmental fixture {action}; not model authored",
                "expected_head": expected,
            },
        )
        assert checkpoint["state"] == "completed"
        commit = checkpoint["source_commit"]
        repository_id = services.registry.get(project_id)["repository_id"]

        def closure(entry, files, dependencies):
            manifest = {
                "entry_path": entry,
                "files": [
                    {
                        "destination": p,
                        "origin": {
                            "kind": "git",
                            "repository_id": repository_id,
                            "commit": commit,
                            "path": p,
                            "sha256": sha(root / p),
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
            if action == "finish":
                for dependency in dependencies:
                    frozen = services.extension("catalogue").version(
                        project_id, dependency["version_id"]
                    )["source"]["files"]
                    for row in manifest["files"]:
                        original = next(
                            (f for f in frozen if f["destination"] == row["destination"]), None
                        )
                        if original:
                            row["origin"] = original["origin"]
            manifest["digest"] = closure_digest(manifest)
            return manifest

        generation = 1 if action == "seed" else 2
        definitions = [
            (
                "asset-cube",
                "asset",
                "Linked cube",
                "assets/cube/asset.blend",
                ["assets/cube/asset.blend", "assets/cube/colour.png"],
                [],
            ),
            (
                "scene-main",
                "scene",
                "Linked scene",
                "scenes/fixture/scene.blend",
                ["scenes/fixture/scene.blend", "assets/cube/asset.blend", "assets/cube/colour.png"],
                [{"item_id": "asset-cube", "version_id": f"asset-cube-v{generation}"}],
            ),
        ]
        if action == "seed":
            definitions += [
                (
                    "asset-companion",
                    "asset",
                    "Companion asset",
                    "assets/companion/asset.blend",
                    ["assets/companion/asset.blend", "assets/companion/colour.png"],
                    [],
                ),
                (
                    "scene-alternate",
                    "scene",
                    "Alternate scene",
                    "scenes/alternate/scene.blend",
                    [
                        "scenes/alternate/scene.blend",
                        "assets/cube/asset.blend",
                        "assets/cube/colour.png",
                    ],
                    [{"item_id": "asset-cube", "version_id": "asset-cube-v1"}],
                ),
            ]
        if action == "finish":
            definitions = [
                (
                    "asset-companion",
                    "asset",
                    "Companion asset",
                    "assets/companion/asset.blend",
                    ["assets/companion/asset.blend", "assets/companion/colour.png"],
                    [],
                ),
                (
                    "scene-alternate",
                    "scene",
                    "Alternate scene",
                    "scenes/alternate/scene.blend",
                    [
                        "scenes/alternate/scene.blend",
                        "assets/cube/asset.blend",
                        "assets/cube/colour.png",
                    ],
                    [{"item_id": "asset-cube", "version_id": "asset-cube-v2"}],
                ),
                (
                    "asset-delayed",
                    "asset",
                    "Later completed asset",
                    "assets/unfinished/asset.blend",
                    ["assets/unfinished/asset.blend", "assets/unfinished/colour.png"],
                    [],
                ),
            ]
        media = None
        if action != "finish":
            reference = json.loads(
                (
                    Path(__file__).resolve().parents[3] / "docs/evidence/m01/public-final.json"
                ).read_text()
            )
            artifact = next(
                a
                for a in reference["artifacts"].values()
                if a.get("source_version_id") == ("loop-v1-clean" if generation == 1 else "loop-v2")
                and a["kind"] == "image"
            )
            source = M01 / "project/.proscenium/artefacts/sha256" / artifact["sha256"] / "content"
            assert sha(source) == artifact["sha256"]
            media, _ = services.extension("artifacts").upload(
                owner,
                project_id,
                operation + "-explicit-imported-reference",
                source.read_bytes(),
                "image/png",
            )
        items = []
        for item, kind, name, entry, files, dependencies in definitions:
            manifest = closure(entry, files, dependencies)
            previews = []
            if action == "seed" and item == "scene-main":
                spec = json.loads(
                    (
                        Path(__file__).resolve().parents[3]
                        / "docs/contracts/examples/preview-authored-camera.json"
                    ).read_text()
                )["spec"]
                spec.update(
                    intent_id="m02-explicit-pending-preview",
                    source_closure_digest=manifest["digest"],
                    scene_name="Scene",
                    camera=None,
                    frames={"start": 1, "end": 1},
                    dimensions={"width": 640, "height": 360},
                    engine="CYCLES",
                    device="CPU",
                    samples=8,
                    renderer_revision="proscenium-blender-1",
                )
                previews = [spec]
            item_generation = 1 if item == "asset-delayed" else generation
            items.append(
                {
                    "item_id": item,
                    "kind": kind,
                    "name": name,
                    "version_id": f"{item}-v{item_generation}",
                    "expected_current_version_id": None if item_generation == 1 else f"{item}-v1",
                    "source_commit": commit,
                    "source": manifest,
                    "artifact_ids": [media["artifact_id"]] if media and kind == "scene" else [],
                    "preview_requests": previews,
                    "description": "Explicit developmental F01 import. Attached image is an imported M01 reference, NOT a render of this version.",
                    "provenance": {
                        "source_url": None,
                        "creator": "Proscenium developmental fixture",
                        "licence": "Synthetic test fixture",
                    },
                    "tags": ["scripted-fixture-import", "not-model-authored"],
                }
            )
        body = {
            "schema_version": "1.0",
            "publication_id": operation + "-publication",
            "project_id": project_id,
            "items": items,
        }
        receipt, replayed = services.extension("catalogue").publish(caller, body)
        services.gate.close(project_id)
        with store.transaction() as tx:
            work = tx.require("work", work_id)
            work.update(state="completed", state_revision=1, job_ids=receipt["job_ids"])
            tx.put("work", work_id, work, expected_revision=0, schema="WorkRecord")
            invocation = tx.require("invocations", invocation_id)
            tx.put(
                "invocations",
                invocation_id,
                {**invocation, "state": "completed"},
                expected_revision=0,
            )
        assert not replayed
        return {
            "fixture": "scripted import through production services, not SDK effect",
            "imports": imported,
            "checkpoint": checkpoint,
            "publication": body,
            "receipt": receipt,
            "media": media,
            "state": state(root),
            "gate_closed_before_dispatch_lock_released": True,
        }
