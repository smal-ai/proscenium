"""Verify retained REAL M01 evidence without launching a model or native process."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import rfc8785
from PIL import Image, ImageChops

root = Path(sys.argv[1])
repo = Path(__file__).resolve().parents[3]
public = json.loads((root / "public-final.json").read_text())
before = json.loads((root / "public-before-feedback.json").read_text())
submitted = json.loads((root / "public-submitted-feedback.json").read_text())
native_before = json.loads((root / "native-before-feedback.json").read_text())
native_after = json.loads((root / "native-after-feedback.json").read_text())
versions = {v["version_id"]: v for page in public["versions"].values() for v in page["items"]}
assert set(versions) == {"loop-v1", "loop-v1-clean", "loop-v2"}
assert versions["loop-v2"]["source_commit"] != versions["loop-v1-clean"]["source_commit"]
for version in versions.values():
    source = version["source"]
    assert (
        hashlib.sha256(
            rfc8785.dumps({k: v for k, v in source.items() if k != "digest"})
        ).hexdigest()
        == source["digest"]
    )
    for file in source["files"]:
        origin = file["origin"]
        # Git LFS hydrated bytes, not its pointer object.
        content = subprocess.check_output(
            ["git", "show", f"{origin['commit']}:{origin['path']}"], cwd=root / "project"
        )
        if content.startswith(b"version https://git-lfs.github.com/spec/v1"):
            oid = next(
                line.split(b"sha256:")[1].decode()
                for line in content.splitlines()
                if line.startswith(b"oid ")
            )
            content = (root / "project/.git/lfs/objects" / oid[:2] / oid[2:4] / oid).read_bytes()
        assert hashlib.sha256(content).hexdigest() == origin["sha256"]
assert (
    native_before["source_sha256"]
    == versions["loop-v1-clean"]["source"]["files"][0]["origin"]["sha256"]
)
assert (
    native_after["source_sha256"] == versions["loop-v2"]["source"]["files"][0]["origin"]["sha256"]
)
assert (
    hashlib.sha256((root / "project/scenes/loop/scene.blend").read_bytes()).hexdigest()
    == native_after["source_sha256"]
)
blue_before = next(o for o in native_before["objects"] if o["object_id"] == "loop-blue-cube")
blue_after = next(o for o in native_after["objects"] if o["object_id"] == "loop-blue-cube")
assert blue_before["dimensions"] == [2, 2, 2] and blue_after["dimensions"] == [2, 2, 4]
assert blue_after["location"][2] - blue_after["dimensions"][2] / 2 == 0
assert blue_before["instance_id"] == blue_after["instance_id"]
assert next(o for o in native_before["objects"] if o["object_id"] == "loop-orange-cube") == next(
    o for o in native_after["objects"] if o["object_id"] == "loop-orange-cube"
)
assert not native_after["dependencies"] and not native_after["is_dirty"]
jobs = public["jobs"]["items"]
assert len(jobs) == 3
assert Counter(j["state"] for j in jobs) == {"succeeded": 2, "failed": 1}
images = {}
for job in jobs:
    assert job["attempt_number"] == 1
    spec = job["render_spec"]
    assert spec["engine"] == "CYCLES" and spec["device"] == "CPU" and spec["samples"] == 8
    assert spec["dimensions"] == {"width": 640, "height": 360} and spec["frames"] == {
        "start": 1,
        "end": 1,
    }
    assert spec["renderer_revision"] == "proscenium-blender-1"
    frames = public["frames"][job["job_id"]]["items"]
    if job["state"] == "succeeded":
        assert len(frames) == 1 and job["native_exit_code"] == 0
        artifact = public["artifacts"][frames[0]["artifact_id"]]
        path = root / "project/.proscenium/artefacts/sha256" / artifact["sha256"] / "content"
        content = path.read_bytes()
        assert (
            hashlib.sha256(content).hexdigest() == artifact["sha256"]
            and len(content) == artifact["bytes"]
        )
        image = Image.open(path).convert("RGB")
        assert image.size == (640, 360)
        images[job["target"]["version"]["version_id"]] = (image, artifact)
assert images["loop-v1-clean"][1] == before["artifacts"][images["loop-v1-clean"][1]["artifact_id"]]
diff = ImageChops.difference(images["loop-v1-clean"][0], images["loop-v2"][0])
changed = sum(pixel != (0, 0, 0) for pixel in diff.get_flattened_data())
assert changed > 10000
annotation = public["annotations"]["items"][0]
assert annotation == submitted["annotations"]["items"][0]
assert annotation["saved_revision"] == 1 and annotation["target"]["version_id"] == "loop-v1-clean"
assert len(annotation["markups"]) == 1 and annotation["markups"][0]["kind"] == "pin"
assert public["selections"]["items"][0]["choice"]["version"]["version_id"] == "loop-v1-clean"
context = next(c for c in public["contexts"].values() if c["annotations"])
assert len(context["attachments"]) == 2 and context["annotations"] == [annotation]
assert {a["artifact_id"] for a in context["attachments"]} == {
    annotation["target"]["artifact_id"],
    annotation["markup_artifact_id"],
}
events_path = next((root / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"))
events = [json.loads(line) for line in events_path.read_text().splitlines()]
start = next(e for e in events if e["type"] == "session.start")
assert start["data"]["selectedModel"] == "gpt-6.1-sol"
users = [e for e in events if e["type"] == "user.message"]
assert len(users) == 3
delivered = next(
    e
    for e in users
    if any(
        a.get("payload", {}).get("context_id") == context["context_id"]
        for a in e["data"]["attachments"]
    )
)
files = [a for a in delivered["data"]["attachments"] if a["type"] == "file"]
assert len(files) == 2
hashes = {hashlib.sha256(Path(a["path"]).read_bytes()).hexdigest() for a in files}
assert hashes == {public["artifacts"][a["artifact_id"]]["sha256"] for a in context["attachments"]}
for artifact in public["artifacts"].values():
    data = (
        root / "project/.proscenium/artefacts/sha256" / artifact["sha256"] / "content"
    ).read_bytes()
    assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
replay = json.loads((root / "publication-replay.json").read_text())
assert replay["unchanged"] and replay["http_status"] == 200 and replay["response"]["replayed"]
session_id = public["snapshot"]["project"]["native_session_id"]
assert session_id == start["data"]["sessionId"]
assert session_id == before["snapshot"]["project"]["native_session_id"]
safe_events = []
for event in events:
    typ, data = event["type"], event["data"]
    safe = {k: event[k] for k in ("id", "type", "timestamp", "parentId") if k in event}
    if typ == "user.message":
        safe["data"] = {k: data[k] for k in ("messageId", "clientTurnId") if k in data}
        safe["data"]["attachments"] = [
            {
                k: value
                for k, value in attachment.items()
                if k
                in {"type", "path", "displayName", "extensionId", "title", "capturedAt", "payload"}
            }
            for attachment in data["attachments"]
        ]
    elif typ == "tool.execution_start":
        safe["arguments_sha256"] = hashlib.sha256(
            rfc8785.dumps(data.get("arguments", {}))
        ).hexdigest()
        safe["data"] = {
            k: data[k]
            for k in (
                "toolCallId",
                "toolName",
                "model",
                "mcpServerName",
                "mcpConfigServerName",
                "mcpTransport",
            )
            if k in data
        }
    elif typ == "tool.execution_complete":
        safe["data"] = {k: data[k] for k in ("toolCallId", "success") if k in data}
    elif typ == "session.start":
        safe["data"] = {
            k: data[k]
            for k in ("sessionId", "copilotVersion", "selectedModel", "startTime")
            if k in data
        }
    else:
        continue
    safe_events.append(safe)
(root / "native-delivery-redacted.json").write_text(json.dumps(safe_events, indent=2))
project_db = sqlite3.connect(f"file:{root}/project/.proscenium/state.sqlite3?mode=ro", uri=True)
checkpoints = [
    json.loads(row[0])
    for row in project_db.execute("SELECT result FROM operations WHERE scope LIKE '%checkpoint%'")
]
result = {
    "verified_at": datetime.now(UTC).isoformat(),
    "fixture_input": True,
    "real_model_native_render_effects": True,
    "root_requests": len(users),
    "native_children": sum(e["type"] == "subagent.started" for e in events),
    "rendered_frames": sum(len(v["items"]) for v in public["frames"].values()),
    "changed_pixels": changed,
    "total_pixels": 640 * 360,
    "native_session_id": session_id,
    "versions": {
        k: {"source_commit": v["source_commit"], "source": v["source"]} for k, v in versions.items()
    },
    "annotation_id": annotation["annotation_id"],
    "sdk_attachment_sha256": sorted(hashes),
    "checkpoints": checkpoints,
    "application_base": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip(),
    "limits": [
        "Original loop-v1 failed before rendering on unused factory brush references. Repaired immutable loop-v1-clean is the baseline image.",
        "The initial and error-correction Send actions used the browser's default viewport after CDP disconnect. The real selection, annotation, visual follow-up Send and review were all 1920x1080 DPR1.",
        "E01 comprehensive native decisions/Stop/child semantics use separately accepted proofs, not rerun here.",
    ],
}
(root / "verification.json").write_text(json.dumps(result, indent=2))
print(
    json.dumps(
        {
            k: result[k]
            for k in (
                "root_requests",
                "native_children",
                "rendered_frames",
                "changed_pixels",
                "native_session_id",
            )
        },
        indent=2,
    )
)
