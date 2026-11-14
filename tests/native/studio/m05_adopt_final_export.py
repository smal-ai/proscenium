"""Verify actual saved/publication effects and export only the separate final proof."""

import hashlib
import importlib.metadata
import json
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

from proscenium.contracts import canonical, validate

REPO = Path(__file__).resolve().parents[3]
OLD = Path.home() / ".cache/proscenium-development/milestones/m05-20261114T165850"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    proof = Path(sys.argv[1]).resolve()
    project = OLD / "project"
    output = REPO / "docs/evidence/m05-adoption/final"
    output.mkdir(parents=True, exist_ok=True)

    def read(name):
        return json.loads((proof / name).read_text())

    def write(name, value):
        (output / name).write_text(json.dumps(value, indent=2) + "\n")

    before = read("before.json")
    prior, final = read("public-before-release.json"), read("public-final.json")
    request = read("single-request.json")
    request_id = request["request"]["request_id"]
    assert request["request"]["receipt"]["state"] == "completed"
    assert request["elapsed_ms"] <= 180000
    assert len(prior["requests"]["items"]) == 4 and len(final["requests"]["items"]) == 5
    assert prior["jobs"] == final["jobs"] and prior["selections"] == final["selections"]
    for item in ("asset-cube", "asset-companion", "scene-alternate"):
        assert prior["versions"][item] == final["versions"][item]
    old_main = prior["versions"]["scene-main"]["items"][0]
    main_versions = {v["version_id"]: v for v in final["versions"]["scene-main"]["items"]}
    assert len(main_versions) == 2 and main_versions["scene-main-v1"] == old_main
    adopted = main_versions["scene-main-v2"]
    validate("VersionRecord", adopted)
    assert adopted["source"]["dependencies"] == [
        {"item_id": "asset-cube", "version_id": "asset-cube-v3"}
    ]
    assert not adopted["preview_requests"] and not adopted["preview_job_ids"]
    asset = next(
        v for v in final["versions"]["asset-cube"]["items"] if v["version_id"] == "asset-cube-v3"
    )
    manifest = {f["destination"]: f for f in adopted["source"]["files"]}
    for file in asset["source"]["files"]:
        assert manifest[file["destination"]]["origin"] == file["origin"]
    closure = adopted["source"]
    assert (
        hashlib.sha256(canonical({k: v for k, v in closure.items() if k != "digest"})).hexdigest()
        == closure["digest"]
    )
    hydrated = {}
    for file in closure["files"]:
        origin = file["origin"]
        content = subprocess.check_output(
            ["git", "show", f"{origin['commit']}:{origin['path']}"], cwd=project
        )
        if content.startswith(b"version https://git-lfs.github.com/spec/v1"):
            oid = next(
                line.split(b"sha256:")[1].decode()
                for line in content.splitlines()
                if line.startswith(b"oid ")
            )
            content = (project / ".git/lfs/objects" / oid[:2] / oid[2:4] / oid).read_bytes()
        assert hashlib.sha256(content).hexdigest() == origin["sha256"]
        hydrated[file["destination"]] = {
            "sha256": origin["sha256"],
            "bytes": len(content),
            "origin": origin,
        }
    report_path = project / "reports/history-adoption-final.json"
    report = json.loads(report_path.read_text())
    assert report["status"] == "passed" and report["request_id"] == request_id
    assert report["context_id"] == request["request"]["submission"]["context_id"]
    assert report["work_id"] == request["request"]["receipt"]["work_id"]
    adoption = report["adoption"]
    assert adoption["state_preserved"] and adoption["state_before"] == adoption["state_after"]
    assert adoption["dependencies"] == adoption["saved_dependency_recheck"]
    assert report["protected_before"] == report["protected_after"]
    scene_path = project / "scenes/fixture/scene.blend"
    assert scene_path.read_bytes().startswith(b"BLENDER")
    assert (
        sha(scene_path)
        == adoption["output"]["sha256"]
        == manifest["scenes/fixture/scene.blend"]["origin"]["sha256"]
    )
    protected = {
        name: {"before": value, "after": sha(project / name)}
        for name, value in before["source_files"].items()
        if name != "scenes/fixture/scene.blend"
    }
    assert all(row["before"] == row["after"] for row in protected.values())
    index = project / ".git/index"
    assert (sha(index) if index.exists() else None) == before["index_sha256"]
    assert (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
        == adopted["source_commit"]
    )
    connection = sqlite3.connect(f"file:{project}/.proscenium/state.sqlite3?mode=ro", uri=True)

    def rows(namespace):
        return [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT body FROM records WHERE namespace=?", (namespace,)
            )
        ]

    publication = next(
        p
        for p in rows("publications")
        if any(i["version_id"] == "scene-main-v2" for i in p["submission"]["items"])
    )
    validate("PublicationSubmit", publication["submission"])
    validate("PublicationReceipt", publication["receipt"])
    assert publication["request_id"] == request_id and len(publication["submission"]["items"]) == 1
    assert publication["submission"]["items"][0]["expected_current_version_id"] == "scene-main-v1"
    checkpoint = next(
        c
        for c in rows("checkpoint_intents")
        if c["receipt"]["source_commit"] == adopted["source_commit"]
    )
    assert checkpoint["state"] == "completed" and checkpoint["parent"] == before["head"]
    assert {f["path"] for f in checkpoint["receipt"]["files"]} == {
        "scenes/fixture/scene.blend",
        "tools/history_adopt_final.py",
        "reports/history-adoption-final.json",
    }
    invocations = [v for v in rows("invocations") if v.get("request_id") == request_id]
    assert invocations and all(v["state"] == "completed" for v in invocations)
    source = next((OLD / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"))
    initial = next((proof / "private-before/native-session-state").glob("*/events.jsonl"))
    old_ids = {json.loads(line)["id"] for line in initial.read_text().splitlines()}
    events = [json.loads(line) for line in source.read_text().splitlines()]
    delta = [e for e in events if e["id"] not in old_ids]
    assert sum(e["type"] == "user.message" for e in delta) == 1
    calls = [
        {
            "native_event_id": e["id"],
            "timestamp": e["timestamp"],
            **{k: e["data"][k] for k in ("toolCallId", "toolName", "arguments")},
        }
        for e in delta
        if e["type"] == "tool.execution_start"
    ]
    counts = Counter(c["toolName"] for c in calls)
    assert counts["checkpoint_sources"] == counts["publish_outputs"] == 1
    assert not set(counts) & {"task", "request_preview", "submit_generation", "request_vlm_review"}
    allocation = project / "reports/native-final-allocation"
    terminal = json.loads((allocation / "terminal.json").read_text())
    assert terminal["exit_code"] == 0 and terminal["reaped"]
    assert terminal["available_bytes_before"] >= 4 * 1024**3
    assert len(list(allocation.glob("*.claim"))) == 1
    write(
        "verification.json",
        {
            "strict_result": "pass_scoped_M05_journey",
            "new_model_sends": 1,
            "new_children": 0,
            "elapsed_ms": request["elapsed_ms"],
            "new_native_processes": 1,
            "new_jobs": 0,
            "head_before": before["head"],
            "head_after": adopted["source_commit"],
            "index_before": before["index_sha256"],
            "index_after": sha(index) if index.exists() else None,
            "protected_files": protected,
            "hydrated_source_files": hydrated,
            "adoption_saved": True,
            "adoption_published": True,
            "all_original_versions_jobs_owner_choices_preserved": True,
        },
    )
    write("public-before.json", prior)
    write("public-final.json", final)
    write("publication.json", publication)
    write("checkpoint.json", {k: checkpoint[k] for k in ("state", "parent", "receipt", "files")})
    write("native-tool-invocations.json", calls)
    write("host-invocations.json", invocations)
    write(
        "native-messages-redacted.json",
        [
            {
                "native_event_id": e["id"],
                "type": e["type"],
                "timestamp": e["timestamp"],
                "content": e["data"].get("content", ""),
            }
            for e in delta
            if e["type"] in {"user.message", "assistant.message"}
        ],
    )
    for name in (
        "static-review.json",
        "release-receipt.json",
        "single-request.json",
        "request-observations.json",
        "same-current-context.json",
        "reopen-observation.json",
        "processes.json",
        "cleanup.json",
        "old-main-scene.png",
        "adopted-main-scene.png",
        "asset-current-context.png",
    ):
        shutil.copyfile(proof / name, output / name)
    shutil.copyfile(report_path, output / "history-adoption-final.json")
    shutil.copyfile(
        project / "tools/history_adopt_final.py",
        output / "model-authored-history_adopt_final.py.txt",
    )
    shutil.copyfile(allocation / "terminal.json", output / "native-terminal.json")
    shutil.copyfile(allocation / "blender.log", output / "native-blender.log")
    start = next(e for e in events if e["type"] == "session.start")
    write(
        "identities.json",
        {
            "accepted_base": subprocess.check_output(
                ["git", "rev-parse", "2f1281e"], cwd=REPO, text=True
            ).strip(),
            "private_proof_root": str(proof),
            "retained_project_root": str(project),
            "sdk": importlib.metadata.version("github-copilot-sdk"),
            "cli_version": start["data"]["copilotVersion"],
            "model": start["data"]["selectedModel"],
            "native_session_id": start["data"]["sessionId"],
            "browser": final["browser"],
            "viewport": final["view"]["viewport"],
            "compiled_frontend": {
                str(p.relative_to(REPO)): sha(p)
                for p in (REPO / "apps/frontend/dist").rglob("*")
                if p.is_file()
            },
        },
    )
    write(
        "manifest.json",
        {
            p.name: {"sha256": sha(p), "bytes": p.stat().st_size}
            for p in sorted(output.iterdir())
            if p.is_file() and p.name not in {"README.md", "manifest.json"}
        },
    )
    connection.close()
    print(
        json.dumps(
            {
                "saved": True,
                "published": True,
                "version_id": "scene-main-v2",
                "source_commit": adopted["source_commit"],
                "publication_id": publication["submission"]["publication_id"],
                "new_model_sends": 1,
                "new_native_processes": 1,
            }
        )
    )


if __name__ == "__main__":
    main()
