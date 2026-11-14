"""Read retained M11 effects and export bounded non-secret evidence. No execution/replay."""

import base64
import importlib.metadata
import json
import math
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

from proscenium.contracts import validate

from tools.unity.worker import alive
from tools.unity.workflow import sha256

REPO = Path(__file__).resolve().parents[3]


def main(root):
    out = REPO / "docs/evidence/m11"
    out.mkdir(parents=True, exist_ok=True)
    project = root / "project"
    database = sqlite3.connect(f"file:{project / '.proscenium/state.sqlite3'}?mode=ro", uri=True)

    def rows(namespace):
        return [
            json.loads(row[0])
            for row in database.execute(
                "SELECT body FROM records WHERE namespace=? ORDER BY id", (namespace,)
            )
        ]

    def write(name, value):
        (out / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")

    jobs = {j["job_id"]: j for j in rows("jobs")}
    assert set(jobs) == {"m11-import-job", "m11-repeat-proof-job", "m11-playmode-job"}
    for job in jobs.values():
        validate("JobRecord", job)
    assert all(j["state"] == "succeeded" and j["native_exit_code"] == 0 for j in jobs.values())
    from tools.unity.jobs import attempt_directory

    native = {}
    for name, job in jobs.items():
        directory = attempt_directory(project, job["attempt_id"])
        target = out / name
        target.mkdir(exist_ok=True)
        worker = json.loads((directory / "worker.json").read_text())
        assert not alive(worker["supervisor"]) and not alive(worker["process"])
        for filename in (
            "request.json",
            "worker.json",
            "unity-result.json",
            "playmode.xml",
            "unity-result.json.guids.txt",
            "unity-result.json.metrics.txt",
        ):
            if (directory / filename).is_file():
                shutil.copyfile(directory / filename, target / filename)
        native[name] = dict(
            job=job,
            root=str(directory),
            logs={
                p.name: dict(path=str(p), sha256=sha256(p), bytes=p.stat().st_size)
                for p in directory.glob("*.log")
            },
            remaining_processes=False,
        )
    write("native-jobs.json", native)
    original = json.loads((out / "m11-import-job/unity-result.json").read_text())
    played = json.loads((out / "m11-playmode-job/unity-result.json").read_text())
    assert (
        original["validation"]["status"] == "not_run" and played["validation"]["status"] == "passed"
    )
    report = ElementTree.parse(out / "m11-playmode-job/playmode.xml").getroot()
    assert (
        report.attrib["result"] == "Passed"
        and report.attrib["passed"] == "1"
        and report.attrib["failed"] == "0"
    )
    workflow = next(row for row in rows("native_workflows") if row["job_id"] == "m11-import-job")
    exported = json.loads((project / workflow["export_path"]).read_text())
    geometry = []
    for obj in exported["objects"]:
        coordinates = [[v[axis] for v in obj["vertices"]] for axis in ("x", "y", "z")]
        assert all(math.isfinite(v) for column in coordinates for v in column)
        geometry.append(
            dict(
                name=obj["name"],
                object_id=obj["object_id"],
                instance_id=obj["instance_id"],
                vertices=len(obj["vertices"]),
                triangles=len(obj["triangles"]) // 3,
                bounds_min=[min(c) for c in coordinates],
                bounds_max=[max(c) for c in coordinates],
            )
        )
    write(
        "source-and-geometry.json",
        dict(
            version=rows("versions")[0],
            source_unchanged={
                row["destination"]: sha256(project / row["destination"]) == row["origin"]["sha256"]
                for row in workflow["source"]["files"]
            },
            export_sha256=sha256(project / workflow["export_path"]),
            blender_version=exported["blender_version"],
            units=exported["units"],
            axes=exported["axes"],
            objects=geometry,
        ),
    )
    write("workflow-intent.json", workflow)
    unity = project / "unity/M11"
    scene = project / played["scene_path"]
    text = scene.read_text()
    assert text.count("m_Name: Proscenium " + workflow["recipe"]["source_key"]) == 1
    assert text.count("m_Name: Unrelated acceptance sentinel") == 1
    source_files = [
        p
        for folder in ("Assets", "Packages", "ProjectSettings")
        for p in (unity / folder).rglob("*")
        if p.is_file()
    ]
    source_files += [unity / "M11-operator-playmode.json"]
    write(
        "unity-source-hashes.json",
        {
            p.relative_to(project).as_posix(): dict(sha256=sha256(p), bytes=p.stat().st_size)
            for p in source_files
        },
    )
    write(
        "unity-guids.json",
        {
            p.relative_to(project).as_posix(): next(
                line[6:] for line in p.read_text().splitlines() if line.startswith("guid: ")
            )
            for p in (unity / "Assets").rglob("*.meta")
        },
    )
    write(
        "saved-scene.json",
        dict(
            sha256=sha256(scene),
            encoding="base64",
            content=base64.b64encode(scene.read_bytes()).decode("ascii"),
        ),
    )
    for p, name in [
        (unity / "Packages/packages-lock.json", "unity-packages-lock.json"),
        (unity / "Assets/Proscenium/Runtime/WalkthroughPlayer.cs", "executed-controller.cs.txt"),
        (unity / "Assets/Proscenium/Tests/PlayableTests.cs", "executed-playmode-test.cs.txt"),
    ]:
        shutil.copyfile(p, out / name)
    write(
        "native-invocations.json",
        [json.loads(p.read_text()) for p in sorted(root.glob("unity-*.claim"))],
    )
    write("requests.json", rows("requests"))
    write("invocations.json", rows("invocations"))
    write(
        "recovery-state.json",
        {ns: rows(ns) for ns in ("work", "copilot.resumes", "copilot.control", "copilot.hooks")},
    )
    write(
        "public-catalogue.json",
        dict(
            items=rows("items"),
            versions=rows("versions"),
            artifacts=rows("artifacts"),
            checkpoint_intents=[
                dict(operation_id=r.get("operation_id"), state=r.get("state"))
                for r in rows("checkpoint_intents")
            ],
        ),
    )
    history = [
        json.loads(line) for line in (root / "native-events.private.jsonl").read_text().splitlines()
    ]
    events = []
    for value in history:
        if value.get("ephemeral"):
            continue
        data = value.get("data", {})
        if value["type"] in {
            "subagent.started",
            "subagent.completed",
            "tool.execution_start",
            "tool.execution_complete",
            "assistant.message",
            "session.error",
            "session.shutdown",
        }:
            safe = {
                k: data[k]
                for k in (
                    "toolCallId",
                    "toolName",
                    "arguments",
                    "success",
                    "content",
                    "model",
                    "modelId",
                    "agentId",
                    "agentName",
                    "agentType",
                    "messageId",
                    "originatingMessageId",
                    "parentToolCallId",
                    "phase",
                    "errorType",
                    "message",
                )
                if k in data
            }
            # Raw command output and encrypted/usage/accounting blocks stay private.
            events.append(
                {
                    k: value[k]
                    for k in ("id", "type", "timestamp", "agentId", "parentId")
                    if k in value
                }
                | {"data": safe}
            )
    write("native-events-redacted.json", events)
    for name in (
        "browser-disconnect.json",
        "import-observed.json",
        "fresh-reconcile-owner-http.json",
    ):
        if (root / name).is_file():
            shutil.copyfile(root / name, out / name)
    for source_name, target in [
        ("006-replay-identities.json", "original-replay.json"),
        ("002-seed.json", "operator-source-setup.json"),
        ("011-attest.json", "operator-playmode-attestation.json"),
    ]:
        shutil.copyfile(root / "receipts" / source_name, out / target)
    for filename in (
        "browser-first-before-disconnect.png",
        "browser-imported.png",
        "browser-after-publication-failure.png",
        "browser-reconciliation-only.png",
        "browser-final.png",
        "public-first.json",
        "public-final.json",
    ):
        if (root / filename).is_file():
            shutil.copyfile(root / filename, out / filename)
    compiled = {
        p.relative_to(root / "compiled-frontend").as_posix(): sha256(p)
        for p in (root / "compiled-frontend").rglob("*")
        if p.is_file()
    }
    from proscenium.copilot.native import Configuration, unity_agent

    config = json.loads((root / "config.json").read_text())
    configured = Configuration.load(config["runtime"])
    profile = unity_agent(configured)
    profile.pop("prompt")
    write(
        "runtime-identities.json",
        dict(
            application_base="a2a2097725bb18bb95b64f1a7ab82b63e0fb301e",
            sdk_version=importlib.metadata.version("github-copilot-sdk"),
            runtime_binary_sha256=sha256(configured.cli_path),
            unity_profile=profile,
            lead_model=configured.lead_model,
            compiled_frontend=compiled,
            compiled_origin="Read-only copy of existing application dist; exact bundle hashes, not a claim of rebuilding this worktree",
            raw_native_events=dict(
                path=str(root / "native-events.private.jsonl"),
                sha256=sha256(root / "native-events.private.jsonl"),
            ),
            harness=dict(
                current={p.name: sha256(p) for p in (REPO / "tests/native/unity").glob("m11*.py")},
                executed_host_snapshot=sha256(root / "executed-harness/m11_host-cloud-roots.py"),
            ),
        ),
    )
    errors = []
    for p in (root / "machine/copilot-runtime/home/logs").glob("*.log"):
        errors += [
            dict(path=str(p), line=line)
            for line in p.read_text().splitlines()
            if "unknown session" in line or "original session binding" in line
        ]
    write(
        "publication-failure.json",
        dict(
            status="blocked",
            requests=[
                dict(request_id=r["request_id"], receipt=r["receipt"]) for r in rows("requests")
            ],
            checkpoint_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=project, text=True
            ).strip(),
            unity_publication_present=any(v["kind"] == "unity_result" for v in rows("versions")),
            runtime_errors=errors,
            observations=[
                "Second root fenced provider_unavailable at checkpoint_sources pre-tool hook",
                "No Unity checkpoint intent or commit was created",
                "Fresh owner reconcile_only accepted but retained outcome_uncertain/attribution_unavailable",
                "Per-token fsync observation may have delayed SDK reader; cause is not established",
                "No M03/F00/shared/UI state was patched or forced complete",
            ],
        ),
    )
    write(
        "manifest.json",
        dict(
            status="partial_blocked",
            milestone="M11",
            application_base="a2a2097725bb18bb95b64f1a7ab82b63e0fb301e",
            root=str(root),
            cloud_roots=len(list(root.glob("cloud-*.claim"))),
            native_children=len([r for r in rows("invocations") if r.get("kind") == "child"]),
            unity_invocations=len(list(root.glob("unity-*.claim"))),
            blender_exports=len(
                list(project.glob(".proscenium/jobs/native-workflows/*/export-request.json"))
            ),
            generic_import_validation="not_run",
            operator_playmode_validation="passed",
            unity_publication=False,
            requests=[r["request_id"] for r in rows("requests")],
            scene_path=played["scene_path"],
            scene_sha256=sha256(scene),
            controller_sha256=sha256(unity / "Assets/Proscenium/Runtime/WalkthroughPlayer.cs"),
            test_sha256=sha256(unity / "Assets/Proscenium/Tests/PlayableTests.cs"),
            remaining_cloud_roots=1,
            remaining_native_children=0,
            remaining_unity_invocations=0,
            remaining_blender_exports=0,
        ),
    )
    print(json.dumps(json.loads((out / "manifest.json").read_text()), indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
