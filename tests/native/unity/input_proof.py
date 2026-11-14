"""Two-invocation input follow-up, on disk-backed disposable storage and real W02 sources."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from proscenium.auth import Caller
from proscenium.catalogue import install
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.projects.service import Projects
from proscenium.services import Services
from proscenium.sources import closure_digest
from proscenium.sources.common import git

from tools.unity.jobs import run_unity_job
from tools.unity.worker import observe_attempt
from tools.unity.workflow import input_digest, prepare, sha256, write_json

RESERVE = 5 * 1024**3
PROJECT_ID = "project-w08-input"
REPOSITORY_ID = "repository-w08-input"


def reserve(root: Path) -> int:
    free = shutil.disk_usage(root if root.exists() else root.parent).free
    if free < RESERVE:
        raise RuntimeError("Keep at least 5 GiB free on the fixture volume")
    return free


def services_for(root: Path) -> tuple[Services, Caller]:
    services = Services.create(
        Settings(
            data_directory=root / "machine",
            execution_profiles=frozenset({"unity-input-proof"}),
            free_space_reserve_bytes=RESERVE,
        )
    )
    services.registry.register(
        dict(
            schema_version="1.0",
            project_id=PROJECT_ID,
            repository_id=REPOSITORY_ID,
            name="Disposable W08 input follow-up",
            path=str(root),
            native_session_id=None,
            state="inactive",
            state_revision=0,
        )
    )
    services.registry.activate(PROJECT_ID)
    services.acquire_project(PROJECT_ID)
    services.gate.resume(
        PROJECT_ID, reconciliation_evidence="explicit-two-invocation-input-follow-up"
    )
    invocation = "invocation-" + services.ownership.epoch
    with services.registry.project_store(PROJECT_ID).transaction() as tx:
        if not tx.get("work", "work-input", required=False):
            tx.put(
                "work",
                "work-input",
                dict(
                    schema_version="1.0",
                    work_id="work-input",
                    project_id=PROJECT_ID,
                    originating_request_id="request-input-follow-up",
                    directive_request_ids=[],
                    state="running",
                    state_revision=0,
                    native_session_id="no-model-native-proof",
                    job_ids=[],
                    generation_task_id="task-input",
                ),
                schema="WorkRecord",
            )
        tx.put(
            "invocations",
            invocation,
            dict(work_id="work-input", owner_epoch=services.ownership.epoch, state="running"),
        )
    install(services)
    caller = Caller(
        "tool",
        "input-proof-host",
        PROJECT_ID,
        "work-input",
        services.ownership.epoch,
        operations=("checkpoint_sources", "publishOutputs", "admitToolJob", "updateJobState"),
        invocation_id=invocation,
    )
    return services, caller


def setup(root: Path, previous: Path) -> None:
    root.parent.mkdir(parents=True, exist_ok=True)
    free_before = reserve(root)
    root.mkdir(exist_ok=False)
    (root / "DISPOSABLE-NATIVE-FIXTURE").write_text(
        "W08 input follow-up. No user assets or runtime owner locks copied.\n"
    )
    project = root / "unity/Fixture"
    for name in ("Assets", "Packages", "ProjectSettings"):
        shutil.copytree(previous / "unity/Fixture" / name, project / name)
    prior_recipe = json.loads((previous / "recipe.json").read_text())
    for row in prior_recipe["source"]["files"]:
        path = row["destination"]
        source = previous / "sealed" / path
        if sha256(source) != row["origin"]["sha256"]:
            raise RuntimeError("Prior source bytes changed")
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shutil.copyfile(previous / "environment.json", root / "original-environment.json")
    shutil.copyfile(previous / "recipe.json", root / "original-recipe.json")
    Projects.bootstrap(root, "Disposable W08 input proof")
    git(root, "config", "user.name", "Proscenium W08 native fixture")
    git(root, "config", "user.email", "w08-fixture@example.invalid")
    (root / "project.json").write_bytes(
        canonical(
            dict(
                schema_version="1.0",
                project_id=PROJECT_ID,
                repository_id=REPOSITORY_ID,
                name="Disposable W08 input proof",
                paths={p: p for p in ("brief", "assets", "scenes", "storyboards", "sequences")},
            )
        )
    )
    # Acknowledge only copied, previously executed tool files, not user-edited production files.
    tool_root = project / "Assets/Proscenium"
    write_json(
        tool_root / "tool-install.json",
        {
            path.relative_to(tool_root).as_posix(): sha256(path)
            for path in tool_root.rglob("*")
            if path.suffix in {".cs", ".asmdef"}
        },
    )
    services, caller = services_for(root)
    try:
        checkpoint = services.extension("sources").checkpoint(
            caller,
            dict(
                schema_version="1.0",
                operation_id="checkpoint-input-source",
                project_id=PROJECT_ID,
                paths=[row["destination"] for row in prior_recipe["source"]["files"]],
                message="Pin copied F01 native source for W08 input proof",
                expected_head=None,
            ),
        )
        closure: dict[str, Any] = {
            **prior_recipe["source"],
            "files": [
                dict(
                    destination=row["destination"],
                    origin=dict(
                        kind="git",
                        repository_id=REPOSITORY_ID,
                        commit=checkpoint["source_commit"],
                        path=row["destination"],
                        sha256=row["origin"]["sha256"],
                    ),
                )
                for row in prior_recipe["source"]["files"]
            ],
        }
        closure["digest"] = closure_digest(closure)
        publication, _ = services.extension("catalogue").publish(
            caller,
            dict(
                schema_version="1.0",
                publication_id="publication-input-source",
                project_id=PROJECT_ID,
                items=[
                    dict(
                        item_id="fixture-source",
                        version_id="fixture-source-v1",
                        kind="scene",
                        name="Byte-identical historical F01 for input follow-up",
                        expected_current_version_id=None,
                        source_commit=checkpoint["source_commit"],
                        source=closure,
                        artifact_ids=[],
                        preview_requests=[],
                    )
                ],
            ),
        )
        materialised = services.extension("sources").materialise(PROJECT_ID, "fixture-source-v1")
        write_json(
            root / "source-evidence.json",
            dict(
                checkpoint=checkpoint,
                publication=publication,
                source=closure,
                materialisation_path=str(materialised),
                receipt_sha256=sha256(materialised / ".receipt.json"),
                previous_closure_digest=prior_recipe["source"]["digest"],
                note="Real W02 checkpoint/publication/materialisation. Source bytes unchanged. No new Blender export. Only copied export closure metadata is rebound to the Git-origin closure; mesh objects remain byte-equivalent JSON values.",
            ),
        )
        exported = json.loads((root / "original-environment.json").read_text())
        exported["closure_digest"] = closure["digest"]
        write_json(root / "environment.json", exported)
        recipe = {
            **prior_recipe,
            "source": closure,
            "export_sha256": sha256(root / "environment.json"),
        }
        write_json(root / "recipe.json", recipe)
        write_json(
            root / "allocation.json",
            dict(
                maximum_unity=2,
                unity_invocations=0,
                blender_invocations=0,
                reserve_bytes=RESERVE,
                free_bytes_before=free_before,
            ),
        )
        write_json(
            root / "initial-guids.json",
            {
                p.relative_to(project).as_posix(): sha256(p)
                for p in (project / "Assets").rglob("*.meta")
            },
        )
    finally:
        services.close()


def run(root: Path, editor: Path) -> None:
    free = reserve(root)
    if not (root / "DISPOSABLE-NATIVE-FIXTURE").is_file():
        raise RuntimeError("Missing disposable fixture marker")
    allocation = json.loads((root / "allocation.json").read_text())
    if allocation["unity_invocations"] >= 2:
        raise RuntimeError("Two-invocation allocation exhausted")
    prepare(root, "unity/Fixture", tests=True)
    recipe = json.loads((root / "recipe.json").read_text())
    services, caller = services_for(root)
    try:
        number = allocation["unity_invocations"] + 1
        admission = dict(
            schema_version="1.0",
            operation_id=f"input-operation-{number}",
            project_id=PROJECT_ID,
            job_id=f"unity-input-{number}",
            kind="unity",
            target=dict(target_id="input-playable", version=recipe["source_version"], shot_id=None),
            input_digest=input_digest(
                dict(project="unity/Fixture", recipe=recipe, action="playtest")
            ),
            input_artifact_ids=[],
            execution_profile_id="unity-input-proof",
        )
        allocation["unity_invocations"] = number
        write_json(root / "allocation.json", allocation)
        write_json(
            root / f"executed-source-{number}.json",
            {
                p.relative_to(root / "unity/Fixture").as_posix(): sha256(p)
                for p in (root / "unity/Fixture/Assets/Proscenium").rglob("*")
                if p.suffix in {".cs", ".asmdef", ".unity"}
            },
        )
        result = run_unity_job(
            services,
            caller,
            admission,
            project="unity/Fixture",
            recipe=recipe,
            editor=editor,
            action="playtest",
            timeout=600,
        )
        if result.get("blocked"):
            allocation["unity_invocations"] = number - 1
            write_json(root / "allocation.json", allocation)
        result["remaining_processes"] = (
            observe_attempt(result["job"]["attempt_id"], Path(result["evidence"]) / "request.json")
            if result.get("evidence")
            else []
        )
        result["free_bytes_before"] = free
        result["free_bytes_after"] = reserve(root)
        write_json(root / f"result-{number}.json", result)
        print(json.dumps(result, indent=2))
    finally:
        services.close()


def collect(root: Path, output: Path, number: int) -> None:
    result = json.loads((root / f"result-{number}.json").read_text())
    attempt = Path(result["evidence"])
    output.mkdir(parents=True, exist_ok=True)
    for source, name in [
        (root / f"result-{number}.json", "result.json"),
        (root / f"executed-source-{number}.json", "source-hashes.json"),
    ]:
        shutil.copyfile(source, output / name)
    for name in (
        "request.json",
        "worker.json",
        "playmode.xml",
        "unity-result.json",
        "unity-result.json.metrics.txt",
    ):
        if (attempt / name).is_file():
            shutil.copyfile(attempt / name, output / name)
    project = root / "unity/Fixture"
    for relative, expected in json.loads(
        (root / f"executed-source-{number}.json").read_text()
    ).items():
        if Path(relative).name not in {"WalkthroughPlayer.cs", "PlayableTests.cs"}:
            continue
        source = project / relative
        if sha256(source) != expected:
            raise RuntimeError("Collect source snapshot before preparing the next test revision")
        target = output / "executed-sources" / Path(relative).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    write_json(
        output / "log-identities.json",
        {
            name: dict(path=str(attempt / name), sha256=sha256(attempt / name))
            for name in ("editor.log", "console.log", "supervisor.log")
            if (attempt / name).is_file()
        },
    )
    initial = json.loads((root / "initial-guids.json").read_text())
    write_json(
        output / "guid-retention.json",
        {
            relative: dict(
                before=expected,
                after=sha256(project / relative),
                guid=next(
                    line.removeprefix("guid: ")
                    for line in (project / relative).read_text().splitlines()
                    if line.startswith("guid: ")
                ),
            )
            for relative, expected in initial.items()
        },
    )
    for name in ("source-evidence.json", "original-recipe.json", "recipe.json", "allocation.json"):
        shutil.copyfile(root / name, output / name)
    shutil.copyfile(project / "Packages/packages-lock.json", output / "packages-lock.json")
    write_json(
        output / "materialisation-receipts.json",
        [
            dict(path=str(path), sha256=sha256(path))
            for path in sorted((root / ".proscenium/snapshots").rglob(".receipt.json"))
        ],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "run", "collect"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--editor", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--number", type=int, choices=[1, 2])
    args = parser.parse_args()
    expected = Path.home() / ".cache/proscenium-development/unity-w08-input-20261114"
    if args.root != expected:
        parser.error("This allocation is restricted to the explicitly assigned disk-backed root")
    if args.action == "setup" and args.previous is not None:
        setup(args.root, args.previous)
    elif args.action == "run" and args.editor is not None:
        run(args.root, args.editor)
    elif args.action == "collect" and args.output is not None and args.number is not None:
        collect(args.root, args.output, args.number)
    else:
        parser.error("setup requires --previous; run requires --editor")
