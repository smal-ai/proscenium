"""One original M11 metadata/control runtime. No model send, new Stop or publication."""

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from native_denial_metadata import bindings, workspace_hashes
from native_io_recovery import Metadata, old_report_hashes
from proscenium.api.composition import configure_services
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.copilot.launcher import identity
from proscenium.services import Services
from runtime_recovery_history import export, sha

REPO = Path(__file__).resolve().parents[3]
ORIGINAL = Path("/home/quintin/.cache/proscenium-development/milestones/m11-20261114-native-a")
ROOT = Path("/home/quintin/.cache/proscenium-development/cold-stop-recovery-02a3a8d")
SESSION = "0cbee1a2-b5ed-4ab8-962b-ccf1d9f8a454"
WORK = "work-4a7e644e78514109b4a5232316e3aacc"
STOP = "33048c8a-df79-4027-a6c2-10ba396e5044"
HEAD = "8d49caaee980d18a140111e81b15a46c71e229ce"


class ColdMetadata(Metadata):
    def blocked(self, kind):
        runtime = self.services.extension("copilot")
        owner = runtime.callback_owners.get(self.services.registry.active_id())
        if owner:
            owner.activity_sequence += 1
        return super().blocked(kind)

    async def cold_stop_snapshot(self, session, stable):
        assert self.counts["blocked_callbacks"] == self.counts["unexpected_turns"] == 0
        result = await super().cold_stop_snapshot(session, stable)
        assert self.counts["blocked_callbacks"] == self.counts["unexpected_turns"] == 0
        return result


def load(path):
    return json.loads(path.read_text())


async def run():
    os.umask(0o077)
    ROOT.mkdir(mode=0o700)  # No second runtime/attempt is silently allocated.
    old_process = load(ORIGINAL / "machine/copilot-runtime/process.json")
    assert (
        identity(old_process["pid"]) != old_process["start"]
        and identity(old_process["parent_pid"]) != old_process["parent_start"]
    )
    before = export(ORIGINAL)
    original_stop = next(r for r in before["records"]["requests"] if r["request_id"] == STOP)
    original_work = next(w for w in before["records"]["work"] if w["work_id"] == WORK)
    assert original_stop["receipt"]["state"] == "outcome_uncertain"
    assert original_work["state"] == "stop_requested" and original_work["state_revision"] == 9
    assert before["records"]["copilot.session"][0]["session_id"] == SESSION
    head = subprocess.check_output(
        ["git", "-C", str(ORIGINAL / "project"), "rev-parse", "HEAD"], text=True
    ).strip()
    assert head == HEAD
    refs = subprocess.check_output(["git", "-C", str(ORIGINAL / "project"), "show-ref"])
    sources = workspace_hashes(ORIGINAL / "project")
    reports = old_report_hashes(ORIGINAL)
    old_bindings = bindings(ORIGINAL)
    (ROOT / "before.private.json").write_text(json.dumps(before, indent=2))
    paths = [
        *ORIGINAL.glob("project/.proscenium/state.sqlite3*"),
        *ORIGINAL.glob("machine/registry.sqlite3*"),
        ORIGINAL / "machine/copilot-runtime/process.json",
        *[
            p
            for p in (ORIGINAL / "machine/copilot-runtime/home/session-state" / SESSION).rglob("*")
            if p.is_file()
        ],
    ]
    hashes = {}
    for path in paths:
        relative = path.relative_to(ORIGINAL)
        target = ROOT / "before" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        hashes[str(relative)] = sha(path)
        shutil.copy2(path, target)
        assert hashes[str(relative)] == sha(path) == sha(target)
    (ROOT / "before-hashes.private.json").write_text(json.dumps(hashes, indent=2))
    settings = Settings(
        data_directory=ORIGINAL / "machine",
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
    native = ColdMetadata(services, ROOT)
    runtime.native = native
    project = services.registry.active_id()
    result = dict(
        application_revision=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=REPO
        ).strip(),
        project_id=project,
        session_id=SESSION,
        work_id=WORK,
        stop_id=STOP,
        head_before=head,
        original_stop_payload=original_stop["submission"],
        original_stop_receipt=original_stop["receipt"],
        work_before=original_work,
        snapshot_files=len(hashes),
        snapshot_manifest_sha256=sha(ROOT / "before-hashes.private.json"),
    )
    owner = Caller("owner", "cold-stop-scripted-owner-reconcile")
    http_send = httpx.Client.send
    async_http_send = httpx.AsyncClient.send

    def blocked(*args, **kwargs):
        return native.blocked("python_provider_http")

    async def blocked_async(*args, **kwargs):
        return native.blocked("python_provider_http")

    httpx.Client.send = blocked
    httpx.AsyncClient.send = blocked_async

    async def operation():
        result["original_stop_retry"] = runtime.state.submit(owner, original_stop["submission"])[0]
        command = dict(
            schema_version="1.0",
            operation_id=str(uuid4()),
            project_id=project,
            mode="reconcile_only",
            work_id=WORK,
            expected_revision=9,
        )
        (ROOT / "reconcile-prepared.json").write_text(json.dumps(command, indent=2))
        result["reconcile_command"] = command
        result["reconcile_admission"] = runtime.admit_resume(owner, command)[0]
        await runtime.resume(project, command["operation_id"])
        assert native.counts["blocked_callbacks"] == native.counts["unexpected_turns"] == 0

    try:
        await asyncio.wait_for(operation(), 150)
    except Exception as exc:
        result["error"] = dict(type=type(exc).__qualname__, code=getattr(exc, "code", None))
    finally:
        await runtime.close()
        services.close()
        httpx.Client.send = http_send
        httpx.AsyncClient.send = async_http_send
    result["native_runtime_seconds"] = (
        time.monotonic() - native.open_started if native.open_started else 0
    )
    assert result["native_runtime_seconds"] <= 180
    after = export(ORIGINAL)
    (ROOT / "after.private.json").write_text(json.dumps(after, indent=2))
    result["counts"] = native.counts
    result["work_after"] = next(w for w in after["records"]["work"] if w["work_id"] == WORK)
    result["stop_phase"] = next(
        s for s in after["records"]["copilot.stops"] if s["work_id"] == WORK
    )
    result["stop_receipt"] = next(
        r["receipt"] for r in after["records"]["requests"] if r["request_id"] == STOP
    )
    result["reconcile_operation"] = next(
        o
        for o in after["records"]["copilot.resumes"]
        if o["body"]["operation_id"] == result["reconcile_command"]["operation_id"]
    )
    result["observations"] = after["records"].get("copilot.cold_stop_observations", [])
    result["live_after"] = [
        i
        for i in after["records"]["invocations"]
        if i["state"] not in {"completed", "failed", "cancelled", "stopped"}
    ]
    result["read_outcomes"] = [
        o
        for o in after["records"].get("copilot.permission_outcomes", [])
        if o.get("kind") == "stopped_native_read_wait"
    ]
    result["questions_before"] = [
        {k: v for k, v in d.items() if k not in ["prompt", "options"]}
        for d in before["records"]["decisions"]
        if d.get("native_decision_id") == "f4cb7f12-085b-42b8-9288-19b946111107"
    ]
    result["questions_after"] = [
        {k: v for k, v in d.items() if k not in ["prompt", "options"]}
        for d in after["records"]["decisions"]
        if d.get("native_decision_id") == "f4cb7f12-085b-42b8-9288-19b946111107"
    ]
    result["stages_before"] = [
        s
        for s in before["records"]["copilot.permissions"]
        if s.get("native_id") == "f4cb7f12-085b-42b8-9288-19b946111107"
    ]
    result["stages_after"] = [
        s
        for s in after["records"]["copilot.permissions"]
        if s.get("native_id") == "f4cb7f12-085b-42b8-9288-19b946111107"
    ]
    result["source_scene_meta_unchanged"] = sources == workspace_hashes(ORIGINAL / "project")
    result["all_old_reports_unchanged"] = reports == old_report_hashes(ORIGINAL)
    result["git_refs_unchanged"] = refs == subprocess.check_output(
        ["git", "-C", str(ORIGINAL / "project"), "show-ref"]
    )
    result["head_after"] = subprocess.check_output(
        ["git", "-C", str(ORIGINAL / "project"), "rev-parse", "HEAD"], text=True
    ).strip()
    current_bindings = bindings(ORIGINAL)
    result["old_bindings_unchanged"] = all(
        current_bindings.get(k) == v for k, v in old_bindings.items()
    )
    result["contexts_unchanged"] = before["records"].get("contexts") == after["records"].get(
        "contexts"
    )
    result["source_job_receipt_records_unchanged"] = all(
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
    result["send_records_unchanged"] = all(
        before["records"].get(n) == after["records"].get(n)
        for n in ["copilot.sends", "copilot.continuation_sends"]
    )
    result["old_submissions_unchanged"] = {
        r["request_id"]: r["submission"] for r in before["records"]["requests"]
    } == {r["request_id"]: r["submission"] for r in after["records"]["requests"]}
    relative = Path("machine/copilot-runtime/home/session-state") / SESSION / "events.jsonl"
    old = (ROOT / "before" / relative).read_bytes()
    current = (ORIGINAL / relative).read_bytes()
    result["native_history_prefix_preserved"] = current.startswith(old)
    result["native_user_messages_before"] = sum(
        json.loads(line)["type"] == "user.message" for line in old.splitlines()
    )
    result["native_user_messages_after"] = sum(
        json.loads(line)["type"] == "user.message" for line in current.splitlines()
    )
    last = before["events"][-1]["sequence"]
    result["new_public_events"] = [e for e in after["events"] if e["sequence"] > last]
    process = load(ORIGINAL / "machine/copilot-runtime/process.json")
    result["process"] = {k: process[k] for k in ["pid", "start", "parent_pid", "parent_start"]}
    result["process"]["same_identity_alive"] = identity(process["pid"]) == process["start"]
    assert not result["process"]["same_identity_alive"]
    result["ready_for_original_continue"] = bool(
        result["stop_phase"]["phase"] == "confirmed"
        and result["work_after"]["state"] == "stopped"
        and not result["live_after"]
        and result["reconcile_operation"]["state"] == "completed"
    )
    assert all(
        result[k]
        for k in [
            "source_scene_meta_unchanged",
            "all_old_reports_unchanged",
            "git_refs_unchanged",
            "old_bindings_unchanged",
            "contexts_unchanged",
            "source_job_receipt_records_unchanged",
            "send_records_unchanged",
            "old_submissions_unchanged",
            "native_history_prefix_preserved",
        ]
    )
    assert (
        result["native_user_messages_before"] == result["native_user_messages_after"]
        and native.counts["sends"] == 0
    )
    (ROOT / "result.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                k: result[k]
                for k in [
                    "ready_for_original_continue",
                    "work_after",
                    "stop_receipt",
                    "counts",
                    "native_runtime_seconds",
                ]
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(run())
