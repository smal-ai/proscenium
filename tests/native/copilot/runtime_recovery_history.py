"""Before-snapshot and SDK metadata-only reconciliation of exact retained sessions."""

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from proscenium.api.composition import configure_services
from proscenium.configuration import Settings
from proscenium.copilot.launcher import identity
from proscenium.copilot.native import Native
from proscenium.services import Services

REPO = Path(__file__).resolve().parents[3]
BASE = Path("/home/quintin/.cache/proscenium-development")
OUT = BASE / "runtime-recovery-history-0b8e9ba"
ORIGINALS = {
    "m03-a": BASE / "m03-runtime-proof-a",
    "m03-b": BASE / "m03-runtime-proof-b",
    "m11": BASE / "milestones/m11-20261114-native-a",
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export(original):
    db = sqlite3.connect(f"file:{original}/project/.proscenium/state.sqlite3?mode=ro", uri=True)
    result = {"records": {}, "events": []}
    try:
        for ns, body in db.execute("select namespace,body from records order by namespace,id"):
            result["records"].setdefault(ns, []).append(json.loads(body))
        result["events"] = [
            json.loads(r[0]) for r in db.execute("select body from events order by sequence")
        ]
    finally:
        db.close()
    return result


class MetadataOnly(Native):
    async def open(self, project_id, session_id, callbacks):
        assert session_id, "Never create a replacement native session"
        callbacks = dict(callbacks)
        callbacks["hooks"] = {
            **callbacks["hooks"],
            "on_pre_tool_use": lambda data, context: {
                "permissionDecision": "deny",
                "permissionDecisionReason": "Metadata-only recovery. No model or tool execution allocated.",
            },
        }
        session = await super().open(project_id, session_id, callbacks)

        async def forbidden(*args, **kwargs):
            raise RuntimeError("No model send allocated for historical recovery")

        session.send = forbidden
        return session


async def run(label, attempt=None):
    os.umask(0o077)
    original = ORIGINALS[label]
    destination = OUT / label if attempt is None else OUT / label / attempt
    destination.mkdir(
        parents=True, mode=0o700
    )  # Refuse repeat reconciliation over an existing run.
    process_file = original / "machine/copilot-runtime/process.json"
    old_process = json.loads(process_file.read_text())
    assert identity(old_process["pid"]) != old_process["start"], "Original owner is still live"
    snapshots = {}
    candidates = [
        *original.glob("project/.proscenium/state.sqlite3*"),
        *original.glob("machine/registry.sqlite3*"),
        process_file,
        *[
            p
            for p in (original / "machine/copilot-runtime/home/session-state").rglob("*")
            if p.is_file()
        ],
    ]
    for path in candidates:
        relative = path.relative_to(original)
        target = destination / "before" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        snapshots[str(relative)] = sha(path)
        shutil.copy2(path, target)
        assert sha(target) == snapshots[str(relative)] == sha(path)
    (destination / "before-hashes.private.json").write_text(json.dumps(snapshots, indent=2))
    top = {p.name: sha(p) for p in original.iterdir() if p.is_file()}
    before = export(original)
    (destination / "before.private.json").write_text(json.dumps(before, indent=2))
    settings = Settings(
        data_directory=original / "machine",
        free_space_reserve_bytes=0,
        runtime=dict(
            cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            worker_model="gpt-6-luna",
            unity_worker_model="gpt-6.1-sol",
            worker_tools=["bash"],
            skill_directories=[str(REPO / "agent/skills")],
            mcp_servers={},
        ),
    )
    services = Services.create(settings)
    configure_services(services)
    runtime = services.extension("copilot")
    runtime.native = MetadataOnly(services)
    project_id = services.registry.active_id()
    result = dict(
        label=label,
        project_id=project_id,
        before_manifest_sha256=sha(destination / "before-hashes.private.json"),
        snapshot_files=len(snapshots),
        original_process={
            k: old_process[k] for k in ["pid", "start", "parent_pid", "parent_start"]
        },
        sends=0,
    )
    try:
        result["reconciliation"] = await asyncio.wait_for(runtime.reconcile(project_id), 75)
    except Exception as exc:
        result["error"] = dict(type=type(exc).__qualname__, code=getattr(exc, "code", None))
    finally:
        await runtime.close()
        services.close()
    after = export(original)
    (destination / "after.private.json").write_text(json.dumps(after, indent=2))
    new_process = json.loads(process_file.read_text())
    result["metadata_process"] = {
        k: new_process[k] for k in ["pid", "start", "parent_pid", "parent_start"]
    }
    result["metadata_process"]["same_identity_alive"] = (
        identity(new_process["pid"]) == new_process["start"]
    )
    assert not result["metadata_process"]["same_identity_alive"]
    result["original_top_files_unchanged"] = all(
        sha(original / name) == expected for name, expected in top.items()
    )
    assert result["original_top_files_unchanged"]
    result["live_before"] = [
        i
        for i in before["records"]["invocations"]
        if i["state"] not in {"completed", "failed", "stopped", "cancelled"}
    ]
    result["live_after"] = [
        i
        for i in after["records"]["invocations"]
        if i["state"] not in {"completed", "failed", "stopped", "cancelled"}
    ]
    result["recovery_evidence"] = after["records"].get("copilot.recovery_evidence", [])
    result["decisions_after"] = after["records"].get("decisions", [])
    result["work_before"] = before["records"]["work"]
    result["work_after"] = after["records"]["work"]
    result["request_ids_before"] = [r["request_id"] for r in before["records"]["requests"]]
    result["request_ids_after"] = [r["request_id"] for r in after["records"]["requests"]]
    assert result["request_ids_before"] == result["request_ids_after"]
    old_sequence = before["events"][-1]["sequence"]
    result["new_public_events"] = [e for e in after["events"] if e["sequence"] > old_sequence]
    (destination / "result.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {k: result[k] for k in ["label", "reconciliation", "sends"] if k in result}, default=str
        )
    )


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
