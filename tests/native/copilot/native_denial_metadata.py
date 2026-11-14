"""One before-snapshotted metadata-only pass per retained M10/M11 project. Never Continue."""

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from proscenium.api.composition import configure_services
from proscenium.configuration import Settings
from proscenium.copilot.launcher import identity
from proscenium.services import Services
from runtime_recovery_history import MetadataOnly, export, sha

REPO = Path(__file__).resolve().parents[3]
BASE = Path("/home/quintin/.cache/proscenium-development")
OUT = BASE / "native-denial-recovery-2f1281e"
ORIGINALS = {
    "m10": BASE / "milestones/m10-asset-a",
    "m11": BASE / "milestones/m11-20261114-native-a",
}


def workspace_hashes(root):
    result = {}
    for directory, dirs, names in os.walk(root):
        dirs[:] = [
            d
            for d in dirs
            if d not in {".git", ".proscenium", "Library", "Temp", "Logs", "obj", "__pycache__"}
        ]
        for name in names:
            path = Path(directory) / name
            if not path.is_symlink() and path.is_file():
                result[str(path.relative_to(root))] = sha(path)
    return result


def bindings(root):
    db = sqlite3.connect(f"file:{root}/project/.proscenium/state.sqlite3?mode=ro", uri=True)
    try:
        return dict(db.execute("SELECT id,body FROM records WHERE namespace='copilot.bindings'"))
    finally:
        db.close()


async def run(label):
    os.umask(0o077)
    original = ORIGINALS[label]
    destination = OUT / label
    destination.mkdir(parents=True, mode=0o700)
    old_process = json.loads((original / "machine/copilot-runtime/process.json").read_text())
    assert identity(old_process["pid"]) != old_process["start"]
    assert identity(old_process["parent_pid"]) != old_process["parent_start"]
    before = export(original)
    snapshots = {}
    paths = [
        *original.glob("project/.proscenium/state.sqlite3*"),
        *original.glob("machine/registry.sqlite3*"),
        original / "machine/copilot-runtime/process.json",
        *[
            p
            for p in (original / "machine/copilot-runtime/home/session-state").rglob("*")
            if p.is_file()
        ],
    ]
    for path in paths:
        relative = path.relative_to(original)
        target = destination / "before" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        snapshots[str(relative)] = sha(path)
        shutil.copy2(path, target)
        assert sha(target) == snapshots[str(relative)] == sha(path)
    (destination / "before-hashes.private.json").write_text(json.dumps(snapshots, indent=2))
    (destination / "before.private.json").write_text(json.dumps(before, indent=2))
    source_before = workspace_hashes(original / "project")
    top = {p.name: sha(p) for p in original.iterdir() if p.is_file()}
    bound_before = bindings(original)
    refs_before = subprocess.check_output(["git", "-C", str(original / "project"), "show-ref"])
    head_before = subprocess.check_output(
        ["git", "-C", str(original / "project"), "rev-parse", "HEAD"], text=True
    ).strip()
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
    result = dict(
        label=label,
        project_id=services.registry.active_id(),
        sends=0,
        continue_commands=0,
        snapshot_files=len(snapshots),
        before_manifest_sha256=sha(destination / "before-hashes.private.json"),
        head_before=head_before,
    )
    try:
        result["reconciliation"] = await asyncio.wait_for(
            runtime.reconcile(result["project_id"]), 90
        )
    except Exception as exc:
        result["error"] = dict(type=type(exc).__qualname__, code=getattr(exc, "code", None))
    finally:
        await runtime.close()
        services.close()
    after = export(original)
    (destination / "after.private.json").write_text(json.dumps(after, indent=2))
    new_process = json.loads((original / "machine/copilot-runtime/process.json").read_text())
    result["native_process"] = {
        k: new_process[k] for k in ["pid", "start", "parent_pid", "parent_start"]
    }
    result["native_process"]["same_identity_alive"] = (
        identity(new_process["pid"]) == new_process["start"]
    )
    assert not result["native_process"]["same_identity_alive"]
    result["source_scene_meta_bytes_unchanged"] = source_before == workspace_hashes(
        original / "project"
    )
    result["old_reports_unchanged"] = all(
        sha(original / name) == expected for name, expected in top.items()
    )
    afterbindings = bindings(original)
    result["original_bindings_unchanged"] = all(
        afterbindings.get(k) == v for k, v in bound_before.items()
    )
    result["git_refs_unchanged"] = refs_before == subprocess.check_output(
        ["git", "-C", str(original / "project"), "show-ref"]
    )
    result["contexts_unchanged"] = before["records"].get("contexts") == after["records"].get(
        "contexts"
    )
    result["source_job_counter_records_unchanged"] = all(
        before["records"].get(n) == after["records"].get(n)
        for n in [
            "versions",
            "items",
            "jobs",
            "attempts",
            "checkpoint_intents",
            "generation_reservations",
            "generation_tasks",
        ]
    )
    result["submissions_unchanged"] = {
        r["request_id"]: r["submission"] for r in before["records"]["requests"]
    } == {r["request_id"]: r["submission"] for r in after["records"]["requests"]}
    result["work_before"] = before["records"]["work"]
    result["work_after"] = after["records"]["work"]
    result["live_before"] = [
        i
        for i in before["records"]["invocations"]
        if i["state"] not in {"completed", "failed", "cancelled", "stopped"}
    ]
    result["live_after"] = [
        i
        for i in after["records"]["invocations"]
        if i["state"] not in {"completed", "failed", "cancelled", "stopped"}
    ]
    result["outcomes"] = after["records"].get("copilot.permission_outcomes", [])
    result["permission_stages_before"] = before["records"].get("copilot.permissions", [])
    result["permission_stages_after"] = after["records"].get("copilot.permissions", [])
    result["decisions_before"] = [
        {k: v for k, v in d.items() if k not in ["prompt", "options"]}
        for d in before["records"].get("decisions", [])
    ]
    result["decisions_after"] = [
        {k: v for k, v in d.items() if k not in ["prompt", "options"]}
        for d in after["records"].get("decisions", [])
    ]
    last = before["events"][-1]["sequence"]
    result["new_public_events"] = [e for e in after["events"] if e["sequence"] > last]
    assert all(
        result[k]
        for k in [
            "source_scene_meta_bytes_unchanged",
            "old_reports_unchanged",
            "original_bindings_unchanged",
            "git_refs_unchanged",
            "contexts_unchanged",
            "source_job_counter_records_unchanged",
            "submissions_unchanged",
        ]
    )
    (destination / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in ["label", "reconciliation", "sends"] if k in result}))


def report():
    destination = REPO / "docs/evidence/native-denial-recovery"
    destination.mkdir(parents=True, exist_ok=True)
    for label in ORIGINALS:
        root = OUT / label
        data = json.loads((root / "result.json").read_text())
        for name, digest in json.loads((root / "before-hashes.private.json").read_text()).items():
            assert sha(root / "before" / name) == digest
        process = data["native_process"]
        process["parent_same_identity_alive"] = (
            identity(process["parent_pid"]) == process["parent_start"]
        )
        assert not process["same_identity_alive"] and not process["parent_same_identity_alive"]
        original = ORIGINALS[label]
        before = json.loads((root / "before.private.json").read_text())
        after = json.loads((root / "after.private.json").read_text())
        data["work_states_unchanged"] = data["work_before"] == data["work_after"]
        data["send_records_unchanged"] = before["records"].get("copilot.sends") == after[
            "records"
        ].get("copilot.sends") and before["records"].get("copilot.continuation_sends") == after[
            "records"
        ].get("copilot.continuation_sends")
        data["native_user_messages_before"] = 0
        data["native_user_messages_after"] = 0
        data["native_history_prefix_preserved"] = True
        for path in (root / "before/machine/copilot-runtime/home/session-state").glob(
            "*/events.jsonl"
        ):
            old = path.read_bytes()
            new = (original / path.relative_to(root / "before")).read_bytes()
            data["native_history_prefix_preserved"] &= new.startswith(old)
            data["native_user_messages_before"] += sum(
                json.loads(line)["type"] == "user.message" for line in old.splitlines()
            )
            data["native_user_messages_after"] += sum(
                json.loads(line)["type"] == "user.message" for line in new.splitlines()
            )
        assert data["native_user_messages_before"] == data["native_user_messages_after"]
        assert (
            data["work_states_unchanged"]
            and data["send_records_unchanged"]
            and data["native_history_prefix_preserved"]
        )
        data["revision_script_executed_by_this_proof"] = False
        data["new_authoring_outputs_exist"] = (
            (original / "project/assets/e04/asset-v2.blend").exists() if label == "m10" else False
        )
        db = sqlite3.connect(f"file:{original}/project/.proscenium/state.sqlite3?mode=ro", uri=True)
        try:
            data["new_checkpoint_intent_exists"] = (
                db.execute(
                    "SELECT 1 FROM records WHERE namespace='checkpoint_intents' AND id='m11-unity-source-checkpoint'"
                ).fetchone()
                is not None
            )
        finally:
            db.close()
        (destination / f"{label}.json").write_text(json.dumps(data, indent=2) + "\n")
    hashes = {
        p.name: sha(p) for p in destination.iterdir() if p.is_file() and p.name != "manifest.json"
    }
    (destination / "manifest.json").write_text(json.dumps(hashes, indent=2) + "\n")
    print(json.dumps(dict(metadata_sends=0, snapshots_verified=True, owned_processes_closed=True)))


if __name__ == "__main__":
    if sys.argv[1] == "report":
        report()
    else:
        asyncio.run(run(sys.argv[1]))
