"""Export only allowlisted, redacted M01 evidence. No private runtime logs/stores."""

import hashlib
import importlib.metadata
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

root = Path(sys.argv[1])
repo = Path(__file__).resolve().parents[3]
destination = repo / "docs/evidence/m01"
destination.mkdir(parents=True, exist_ok=True)
subprocess.run([sys.executable, str(Path(__file__).with_name("verify.py")), str(root)], check=True)
public = json.loads((root / "public-final.json").read_text())
connection = sqlite3.connect(f"file:{root}/project/.proscenium/state.sqlite3?mode=ro", uri=True)
events = [
    json.loads(row[0]) for row in connection.execute("SELECT body FROM events ORDER BY sequence")
]
selected = [
    e
    for e in events
    if e["event_type"]
    in {
        "work.state_changed",
        "request.accepted",
        "request.state_changed",
        "publication.created",
        "frame.available",
        "annotation.saved",
        "selection.changed",
        "job.state_changed",
        "assistant.completed",
    }
]
(root / "public-events-redacted.json").write_text(json.dumps(selected, indent=2))
durations = []
for request in public["requests"]["items"]:
    completed = next(
        e
        for e in events
        if e["event_type"] == "work.state_changed"
        and e["payload"]["originating_request_id"] == request["request_id"]
        and e["payload"]["state"] == "completed"
    )
    duration = (
        datetime.fromisoformat(completed["occurred_at"])
        - datetime.fromisoformat(request["accepted_at"])
    ).total_seconds()
    assert duration <= 180
    durations.append(
        {
            "request_id": request["request_id"],
            "accepted_at": request["accepted_at"],
            "completed_at": completed["occurred_at"],
            "seconds": duration,
        }
    )
paths = subprocess.check_output(
    [
        "git",
        "ls-files",
        "apps/backend/src",
        "apps/frontend/src",
        "agent",
        "tools/blender",
        "uv.lock",
        "package-lock.json",
    ],
    cwd=repo,
    text=True,
).splitlines()
hashes = {
    path: hashlib.sha256((repo / path).read_bytes()).hexdigest()
    for path in paths
    if (repo / path).is_file()
}
addon = Path.home() / ".config/blender/4.5/scripts/addons/blender_mcp.py"
identity = {
    "accepted_base": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip(),
    "source_sha256": hashes,
    "sdk": {
        "package": "github-copilot-sdk",
        "version": importlib.metadata.version("github-copilot-sdk"),
    },
    "cli": {
        "version": "1.0.112",
        "sha256": "463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69",
    },
    "model": "gpt-6.1-sol",
    "mcp": {
        "package": "mcp-for-blender",
        "version": "2.0.0",
        "sdk_config_type": "copilot.session.MCPStdioServerConfig",
        "args": [
            "--python",
            "3.11",
            "mcp-for-blender==2.0.0",
            "--host",
            "127.0.0.1",
            "--port",
            "9877",
        ],
    },
    "installed_addon_sha256": hashlib.sha256(addon.read_bytes()).hexdigest(),
    "installed_addon_mtime_ns": addon.stat().st_mtime_ns,
    "browser": {
        "build": "chromium-1255",
        "version": "159.0.8154.6",
        "interaction_viewport": {"width": 1920, "height": 1080, "dpr": 1},
    },
    "request_durations": durations,
    "executed_launcher_sha256_before_post_proof_launch_claim_hardening": "b9abaffd89da7d65b3757d0bd64ed6c554909193ca378650e11b92417075d9f7",
    "private_root": str(root),
}
identity["compiled_frontend_sha256"] = {
    str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted((repo / "apps/frontend/dist").rglob("*"))
    if path.is_file()
}
identity["private_configuration_sha256"] = hashlib.sha256(
    (root / "config.json").read_bytes()
).hexdigest()
(destination / "identities.json").write_text(json.dumps(identity, indent=2) + "\n")
for name in [
    "verification.json",
    "public-final.json",
    "public-submitted-feedback.json",
    "public-restarted.json",
    "native-delivery-redacted.json",
    "public-events-redacted.json",
    "native-before-feedback.json",
    "native-after-feedback.json",
    "publication-replay.json",
    "restart-verification.json",
    "cleanup.json",
    "processes.json",
]:
    shutil.copyfile(root / name, destination / name)
processes = json.loads((root / "owned-process-identities.json").read_text())
browser_pid = json.loads((root / "processes.json").read_text())["browser"]["pid"]
# Browser subprocess flags contain irrelevant renderer salts. Keep the primary
# executable identity and the actual host, SDK, MCP, Blender and Xvfb processes.
primary = [p for p in processes if p["pid"] == browser_pid or "chrome" not in p["executable"]]
(destination / "owned-process-identities.json").write_text(json.dumps(primary, indent=2) + "\n")
for source, target in [
    ("new-version.png", "browser-new-version.png"),
    ("restore-annotation.png", "browser-retained-annotation.png"),
    ("erase-first-pin.png", "browser-pin-before-send.png"),
]:
    shutil.copyfile(root / source, destination / target)
annotation = public["annotations"]["items"][0]
for artifact in public["artifacts"].values():
    if artifact["artifact_id"] == annotation["markup_artifact_id"]:
        filename = "marked-v1.png"
    elif artifact["kind"] == "image":
        filename = (
            "clean-v2.png" if artifact.get("source_version_id") == "loop-v2" else "clean-v1.png"
        )
    else:
        continue
    shutil.copyfile(
        root / "project/.proscenium/artefacts/sha256" / artifact["sha256"] / "content",
        destination / filename,
    )
workers = []
for path in sorted((root / "project/.proscenium/jobs/previews").glob("*/*/task.json")):
    directory = path.parent
    workers.append(
        {
            "path": str(directory.relative_to(root)),
            "task_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "task": json.loads(path.read_text()),
            **{
                name.removesuffix(".json"): json.loads((directory / name).read_text())
                for name in ["process.json", "native-process.json", "terminal.json"]
                if (directory / name).exists()
            },
            "native_error": json.loads((directory / "native-error.json").read_text())
            if (directory / "native-error.json").exists()
            else None,
        }
    )
(destination / "workers.json").write_text(json.dumps(workers, indent=2) + "\n")
manifest = {
    p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
    for p in sorted(destination.iterdir())
    if p.is_file() and p.name not in {"manifest.json", "README.md"}
}
(destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"Exported {len(manifest)} allowlisted evidence files")
