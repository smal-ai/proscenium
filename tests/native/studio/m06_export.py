"""Read retained M06 state, verify real effects, export explicit non-secret evidence."""

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


def sha(content):
    return hashlib.sha256(content).hexdigest()


def main():
    root = Path(sys.argv[1]).resolve()
    output = REPO / "docs/evidence/m06"
    output.mkdir(parents=True, exist_ok=True)

    def read(name):
        return json.loads((root / name).read_text())

    def write(name, value):
        (output / name).write_text(json.dumps(value, indent=2) + "\n")

    final = read("public-final.json")
    before = read("public-before-revision.json")
    assert len(final["requests"]["items"]) == 2
    assert all(r["receipt"]["state"] == "completed" for r in final["requests"]["items"])
    assert not final["jobs"]["items"] and not before["jobs"]["items"]
    assert final["selections"] == before["selections"]
    assert final["annotations"] == before["annotations"]
    versions = {v["version_id"]: v for page in final["versions"].values() for v in page["items"]}
    v1, v2 = (versions[f"m06-kite-board-v{n}"] for n in (1, 2))
    assert v1["source_commit"] != v2["source_commit"]
    source_files = {}
    authored = {}
    for version_id, version in versions.items():
        validate("VersionRecord", version)
        closure = version["source"]
        assert (
            sha(canonical({k: v for k, v in closure.items() if k != "digest"})) == closure["digest"]
        )
        for entry in closure["files"]:
            origin = entry["origin"]
            data = subprocess.check_output(
                ["git", "show", f"{origin['commit']}:{origin['path']}"], cwd=root / "project"
            )
            assert sha(data) == origin["sha256"]
            source_files[f"{version_id}:{entry['destination']}"] = {
                "sha256": sha(data),
                "bytes": len(data),
                "commit": origin["commit"],
            }
            if (
                entry["destination"] == version["source"]["entry_path"]
                and version["kind"] == "storyboard"
            ):
                plan = json.loads(data)
                validate("AuthoredShotPlan", plan)
                authored[version_id] = plan
                (output / f"authored-{version_id}.json").write_bytes(data)
            if entry["destination"] == "brief/film.md":
                (output / f"brief-{version_id}.md").write_bytes(data)
    old, new = (authored[f"m06-kite-board-v{n}"] for n in (1, 2))
    assert old["plan_id"] == new["plan_id"] == "kite-flight"
    assert old["shots"][1] == new["shots"][1]
    assert old["shots"][0] != new["shots"][0]
    assert "counterclockwise" in new["shots"][0]["camera_intent"].lower()
    assert [s["shot_id"] for s in old["shots"]] == [s["shot_id"] for s in new["shots"]]
    for a, b in zip(old["shots"], new["shots"], strict=True):
        assert [(f["frame_id"], f["role"], f["artifact_id"], f["state"]) for f in a["frames"]] == [
            (f["frame_id"], f["role"], f["artifact_id"], f["state"]) for f in b["frames"]
        ]
        assert a["scene_version"] is b["scene_version"] is None
        assert a["source_range"] is b["source_range"] is None
        assert a["fps"] is b["fps"] is None
    asset = versions["m06-kite-asset-v1"]["source"]["files"][0]["origin"]
    current_asset = root / "project/assets/kite/palette.json"
    assert sha(current_asset.read_bytes()) == asset["sha256"]
    for artifact in final["artifacts"].values():
        data = (
            root / "project/.proscenium/artefacts/sha256" / artifact["sha256"] / "content"
        ).read_bytes()
        assert sha(data) == artifact["sha256"] and len(data) == artifact["bytes"]
    reference = read("reference-upload.json")
    assert sha((root / "owner-reference.png").read_bytes()) == reference["sha256"]
    selection = final["selections"]["items"][0]
    assert selection["state"] == "selected" and selection["state_revision"] == 4
    assert selection["slot"] == {
        "kind": "storyboard_frame",
        "storyboard_version": {"item_id": "m06-kite-board", "version_id": "m06-kite-board-v1"},
        "shot_id": "kite-rise",
        "frame_id": "kite-rise-start",
    }
    assert selection["choice"] == {"kind": "artifact", "artifact_id": reference["artifact_id"]}
    annotation = final["annotations"]["items"][0]
    assert annotation["saved_revision"] == 1 and len(annotation["markups"]) == 1
    assert annotation["markups"][0]["kind"] == "pin"
    assert annotation["target"]["artifact_id"] == reference["artifact_id"]
    assert annotation["target"]["shot_id"] == "kite-rise"
    assert final["plans"]["m06-kite-board-v1"]["shots"][0]["frames"][0]["owner_selected"]
    assert not final["plans"]["m06-kite-board-v2"]["shots"][0]["frames"][0]["owner_selected"]
    connection = sqlite3.connect(f"file:{root}/project/.proscenium/state.sqlite3?mode=ro", uri=True)

    def rows(namespace):
        return [
            json.loads(r[0])
            for r in connection.execute("SELECT body FROM records WHERE namespace=?", (namespace,))
        ]

    assert not rows("jobs") and not rows("attempts")
    publications = rows("publications")
    assert len(publications) == 2
    for publication in publications:
        validate("PublicationSubmit", publication["submission"])
        validate("PublicationReceipt", publication["receipt"])
        assert all(not item["preview_requests"] for item in publication["submission"]["items"])
    checkpoints = [
        {k: row[k] for k in ("state", "parent", "receipt", "files")}
        for row in rows("checkpoint_intents")
    ]
    assert len(checkpoints) == 2 and all(row["state"] == "completed" for row in checkpoints)
    initial_checkpoint = next(
        row for row in checkpoints if row["receipt"]["source_commit"] == v1["source_commit"]
    )
    initial_asset = subprocess.check_output(
        ["git", "show", f"{initial_checkpoint['parent']}:assets/kite/palette.json"],
        cwd=root / "project",
    )
    assert sha(initial_asset) == asset["sha256"]
    invocations = rows("invocations")
    assert all(i["state"] == "completed" for i in invocations)
    native_path = next((root / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"))
    native = [json.loads(line) for line in native_path.read_text().splitlines()]
    started = next(e for e in native if e["type"] == "session.start")
    assert started["data"]["selectedModel"] == "gpt-6.1-sol"
    users = [e for e in native if e["type"] == "user.message"]
    assert len(users) == 2
    tools = []
    for event in native:
        if event["type"] == "tool.execution_start":
            data = event["data"]
            tools.append(
                {
                    "native_event_id": event["id"],
                    "timestamp": event["timestamp"],
                    **{k: data[k] for k in ("toolCallId", "toolName", "arguments")},
                }
            )
    names = Counter(t["toolName"] for t in tools)
    assert names["checkpoint_sources"] == names["publish_outputs"] == 2
    assert not set(names) & {
        "task",
        "submit_generation",
        "request_preview",
        "request_vlm_review",
        "admit_job",
    }
    messages = []
    for event in native:
        if event["type"] in {"user.message", "assistant.message"}:
            data = event["data"]
            messages.append(
                {
                    "native_event_id": event["id"],
                    "type": event["type"],
                    "timestamp": event["timestamp"],
                    "content": data.get("content", ""),
                }
            )
    durations = [read(f"request-{i}.json")["elapsed_ms"] for i in (1, 2)]
    assert all(ms <= 180000 for ms in durations)
    impact = json.loads((root / "project/reports/kite-flight-impact.json").read_text())
    comparison = impact["comparison"]
    assert comparison["changed_shot_ids"] == ["kite-rise"]
    assert comparison["removed_frame_ids"] == []
    assert comparison["downstream_scene_version_ids"] == []
    write("public-final.json", final)
    write(
        "before-revision.json",
        {k: before[k] for k in ("selections", "annotations", "plans", "jobs")},
    )
    write("publications.json", publications)
    write("checkpoints.json", checkpoints)
    write("invocations.json", invocations)
    write("native-tool-invocations.json", tools)
    write("native-messages-redacted.json", messages)
    write("dependency-impact.json", impact)
    write(
        "source-verification.json",
        {
            "files": source_files,
            "unchanged_asset_sha256": asset["sha256"],
            "reference_sha256": reference["sha256"],
            "unrelated_shot_equal": True,
            "all_frame_ids_and_references_equal": True,
            "old_sources_rehashed": True,
            "jobs": 0,
            "attempts": 0,
            "root_durations_ms": durations,
            "native_roots": len(users),
            "native_children": 0,
            "tool_counts": dict(names),
        },
    )
    write(
        "known-review-defect.json",
        {
            "status": "remaining",
            "scope_owner": "W05/integration",
            "reproduction": "Click kite-rise-start then kite-rest-start, both using the same real reference artefact. The image remains displayed but captureReady is cleared without a new img load, and parent context.target becomes null.",
            "observed": read("public-after-fix-inspect.json")["view"],
            "workaround": "Explicitly open the material asset version before returning to the other storyboard shot. This is not a passed direct transition.",
        },
    )
    for filename in (
        "role-contexts.json",
        "selection-commands.json",
        "reference-upload.json",
        "alternative-upload.json",
        "reopen-observation.json",
        "cleanup.json",
    ):
        shutil.copyfile(root / filename, output / filename)
    for filename in (
        "review-kite-rise-start.png",
        "review-kite-rise-middle.png",
        "review-kite-rise-end.png",
        "review-kite-rest-start.png",
        "annotation-saved.png",
        "owner-selection-reloaded.png",
        "revised-plan.png",
        "missing-frames.png",
        "retained-annotation-v1.png",
        "final-full-hd.png",
        "owner-reference.png",
    ):
        shutil.copyfile(root / filename, output / filename)
    binary = REPO / "node_modules/@github/copilot-linux-x64/copilot"
    write(
        "identities.json",
        {
            "accepted_base": "b1fa32c8a73d5aca8243fa82e65837a8acaba75c",
            "private_root": str(root),
            "sdk": importlib.metadata.version("github-copilot-sdk"),
            "cli_version": started["data"]["copilotVersion"],
            "cli_sha256": sha(binary.read_bytes()),
            "model": started["data"]["selectedModel"],
            "native_session_id": started["data"]["sessionId"],
            "browser": final["browser"],
            "viewport": final["view"]["viewport"],
            "compiled_frontend": {
                str(p.relative_to(REPO)): sha(p.read_bytes())
                for p in (REPO / "apps/frontend/dist").rglob("*")
                if p.is_file()
            },
            "changed_frontend": {
                str(p.relative_to(REPO)): sha(p.read_bytes())
                for p in (REPO / "apps/frontend/src/features/storyboards").glob("*")
                if p.is_file()
            },
        },
    )
    write(
        "manifest.json",
        {
            p.name: {"sha256": sha(p.read_bytes()), "bytes": p.stat().st_size}
            for p in sorted(output.iterdir())
            if p.is_file() and p.name not in {"README.md", "manifest.json"}
        },
    )
    connection.close()
    print(
        json.dumps(
            {
                "exported": str(output),
                "roots": len(users),
                "jobs": 0,
                "result": "remaining: W05 same-reference cross-shot readiness",
            }
        )
    )


if __name__ == "__main__":
    main()
