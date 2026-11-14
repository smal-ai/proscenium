"""One cached cold metadata snapshot/project, zero sends; M11 explicit Stop only."""

import asyncio
import copy
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from native_denial_metadata import bindings, workspace_hashes
from proscenium.api.composition import configure_services
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.copilot.launcher import identity
from proscenium.copilot.native import Native
from proscenium.copilot.service import wire
from proscenium.services import Services
from runtime_recovery_history import export, sha

REPO = Path(__file__).resolve().parents[3]
BASE = Path("/home/quintin/.cache/proscenium-development")
ROOT = BASE / "native-io-policy-c897729"
ORIGINALS = {
    "m11": BASE / "milestones/m11-20261114-native-a",
    "m10": BASE / "milestones/m10-asset-a",
}
WORKS = {
    "m11": "work-4a7e644e78514109b4a5232316e3aacc",
    "m10": "work-bc19a5da5fcd4aaf9ae552a13d06b20d",
}
SESSIONS = {
    "m11": "0cbee1a2-b5ed-4ab8-962b-ccf1d9f8a454",
    "m10": "5971843f-f216-40f8-b8cb-995675f48161",
}


def append(path, body):
    with path.open("a") as out:
        out.write(json.dumps(body) + "\n")


def old_report_hashes(original):
    result = {}
    for directory, dirs, names in os.walk(original):
        dirs[:] = [
            d for d in dirs if d not in {"project", "machine", "browser-profile", "catalogue"}
        ]
        for name in names:
            path = Path(directory) / name
            if path.is_file() and not path.is_symlink():
                result[str(path.relative_to(original))] = sha(path)
    return result


class Metadata(Native):
    def __init__(self, services, destination):
        super().__init__(services)
        self.destination = destination
        self.counts = dict(
            opens=0,
            raw_history_rpc=0,
            typed_history=0,
            sends=0,
            blocked_callbacks=0,
            aborts=0,
            pauses=0,
            unexpected_turns=0,
        )
        self.open_started = None

    def blocked(self, kind):
        self.counts["blocked_callbacks"] += 1
        append(self.destination / "blocked-callbacks.jsonl", dict(kind=kind))
        raise RuntimeError("Metadata allocation forbids callback effects")

    async def open(self, project_id, session_id, callbacks):
        assert self.counts["opens"] == 0 and session_id, (
            "Exactly one original-session cold open is allocated"
        )
        self.counts["opens"] += 1
        self.open_started = time.monotonic()
        callbacks = dict(callbacks)
        original_event = callbacks["on_event"]

        def event(value):
            original_event(value)
            raw = wire(value)
            kind = raw["type"]
            if kind in {"assistant.turn_start", "user.message", "subagent.started"}:
                self.counts["unexpected_turns"] += 1
            if kind in {
                "abort",
                "session.idle",
                "session.resume",
                "session.error",
                "tool.execution_start",
                "tool.execution_complete",
                "permission.completed",
                "assistant.turn_start",
                "user.message",
                "subagent.started",
            }:
                data = raw.get("data", {})
                append(
                    self.destination / "native-events.jsonl",
                    {
                        **{
                            k: raw[k]
                            for k in ["id", "type", "timestamp", "parentId", "agentId"]
                            if k in raw
                        },
                        "data": {
                            k: v
                            for k, v in data.items()
                            if k in {"reason", "toolCallId", "toolName", "requestId", "success"}
                        },
                    },
                )

        async def block_tool(*args, **kwargs):
            return self.blocked("tool")

        async def block_permission(*args, **kwargs):
            return self.blocked("permission")

        async def block_question(*args, **kwargs):
            return self.blocked("user_input")

        def hook(*args, **kwargs):
            return self.blocked("pre_tool_or_prompt")

        callbacks.update(
            on_event=event,
            on_permission_request=block_permission,
            on_user_input_request=block_question,
        )
        callbacks["hooks"] = {
            **callbacks["hooks"],
            "on_pre_tool_use": hook,
            "on_user_prompt_submitted": hook,
        }
        callbacks["tools"] = [
            dataclasses.replace(tool, handler=block_tool) for tool in callbacks["tools"]
        ]
        session = await super().open(project_id, session_id, callbacks)

        async def no_send(*args, **kwargs):
            self.counts["sends"] += 1
            raise RuntimeError("No SDK send is allocated")

        session.send = no_send
        raw_events = session.get_raw_events
        typed_events = session.get_events
        abort = session.abort
        snapshot = None

        async def raw():
            nonlocal snapshot
            if snapshot is None:
                self.counts["raw_history_rpc"] += 1
                snapshot = await raw_events()
            return copy.deepcopy(snapshot)

        async def typed():
            self.counts["typed_history"] += 1
            return await typed_events()

        async def control_abort():
            self.counts["aborts"] += 1
            return await abort()

        session.get_raw_events = raw
        session.get_events = typed
        session.abort = control_abort
        return session

    async def pause(self, session, paused):
        self.counts["pauses"] += 1
        return await super().pause(session, paused)


async def run(label, repair_preflight=False):
    os.umask(0o077)
    original = ORIGINALS[label]
    destination = ROOT / label
    if repair_preflight:
        assert (
            destination.exists()
            and not (destination / "after.private.json").exists()
            and not (destination / "native-events.jsonl").exists()
            and not (destination / "stop-prepared.json").exists()
        )
        assert sha(original / "machine/copilot-runtime/process.json") == sha(
            destination / "before/machine/copilot-runtime/process.json"
        )
        (destination / "preflight-failure.json").write_text(
            json.dumps(
                dict(type="NameError", missing_import="Services", native_open_before_failure=False)
            )
        )
        destination = destination / "after-preflight-import-fix"
    destination.mkdir(parents=True, mode=0o700)  # Never allocate a replacement over an attempt.
    old_process = json.loads((original / "machine/copilot-runtime/process.json").read_text())
    assert (
        identity(old_process["pid"]) != old_process["start"]
        and identity(old_process["parent_pid"]) != old_process["parent_start"]
    )
    before = export(original)
    (destination / "before.private.json").write_text(json.dumps(before, indent=2))
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
    sources = workspace_hashes(original / "project")
    reports = old_report_hashes(original)
    old_bindings = bindings(original)
    refs = subprocess.check_output(["git", "-C", str(original / "project"), "show-ref"])
    head = subprocess.check_output(
        ["git", "-C", str(original / "project"), "rev-parse", "HEAD"], text=True
    ).strip()
    if label == "m11":
        assert head == "8d49caaee980d18a140111e81b15a46c71e229ce"
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
    native = Metadata(services, destination)
    runtime.native = native
    project = services.registry.active_id()
    assert project
    association = before["records"]["copilot.session"][0]
    assert association["session_id"] == SESSIONS[label]
    result = dict(
        label=label,
        project_id=project,
        work_id=WORKS[label],
        session_id=SESSIONS[label],
        head_before=head,
        source_files=len(sources),
        old_report_files=len(reports),
        snapshot_files=len(snapshots),
        snapshot_manifest_sha256=sha(destination / "before-hashes.private.json"),
    )
    original_http = httpx.Client.send
    original_async_http = httpx.AsyncClient.send

    def blocked_http(*args, **kwargs):
        return native.blocked("python_provider_http")

    async def blocked_async_http(*args, **kwargs):
        return native.blocked("python_provider_http")

    httpx.Client.send = blocked_http
    httpx.AsyncClient.send = blocked_async_http

    async def metadata_control():
        result["reconciliation_before_control"] = await runtime.reconcile(project)
        result["read_wait_candidates"] = len(
            runtime.read_wait_recovery.get(project, {}).get("proofs", [])
        )
        assert native.counts["blocked_callbacks"] == native.counts["unexpected_turns"] == 0
        if label == "m11":
            stop_id = str(uuid4())
            command = dict(
                schema_version="1.0",
                request_id=stop_id,
                project_id=project,
                type="stop",
                target_work_id=WORKS[label],
            )
            (destination / "stop-prepared.json").write_text(json.dumps(command, indent=2))
            result["stop_command"] = command
            result["stop_admission"] = runtime.state.submit(
                Caller("owner", "native-io-policy-scripted-owner-stop"), command
            )[0]
            await runtime.stop(project, WORKS[label], stop_id)
            with services.registry.project_store(project).transaction(write=False) as tx:
                result["stop_phase"] = tx.require("copilot.stops", stop_id)
                result["work_after_control"] = tx.require("work", WORKS[label])

    try:
        await asyncio.wait_for(metadata_control(), 150)
    except Exception as exc:
        result["error"] = dict(type=type(exc).__qualname__, code=getattr(exc, "code", None))
    finally:
        await runtime.close()
        services.close()
        httpx.Client.send = original_http
        httpx.AsyncClient.send = original_async_http
    result["native_runtime_seconds"] = (
        time.monotonic() - native.open_started if native.open_started else 0
    )
    assert result["native_runtime_seconds"] <= 180
    after = export(original)
    (destination / "after.private.json").write_text(json.dumps(after, indent=2))
    process = json.loads((original / "machine/copilot-runtime/process.json").read_text())
    result["process"] = {k: process[k] for k in ["pid", "start", "parent_pid", "parent_start"]}
    result["process"]["same_identity_alive"] = identity(process["pid"]) == process["start"]
    assert not result["process"]["same_identity_alive"]
    result["counts"] = native.counts
    result["source_scene_meta_unchanged"] = sources == workspace_hashes(original / "project")
    result["all_old_reports_unchanged"] = reports == old_report_hashes(original)
    result["git_refs_unchanged"] = refs == subprocess.check_output(
        ["git", "-C", str(original / "project"), "show-ref"]
    )
    result["head_after"] = subprocess.check_output(
        ["git", "-C", str(original / "project"), "rev-parse", "HEAD"], text=True
    ).strip()
    new_bindings = bindings(original)
    result["old_bindings_unchanged"] = all(
        new_bindings.get(k) == v for k, v in old_bindings.items()
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
    original_requests = {r["request_id"]: r["submission"] for r in before["records"]["requests"]}
    new_requests = {r["request_id"]: r["submission"] for r in after["records"]["requests"]}
    result["original_submissions_unchanged"] = all(
        new_requests.get(k) == v for k, v in original_requests.items()
    )
    result["work_before"] = next(
        w for w in before["records"]["work"] if w["work_id"] == WORKS[label]
    )
    result["work_after"] = next(w for w in after["records"]["work"] if w["work_id"] == WORKS[label])
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
    result["read_outcomes"] = after["records"].get("copilot.permission_outcomes", [])
    native_id = (
        "f4cb7f12-085b-42b8-9288-19b946111107"
        if label == "m11"
        else "67539b84-6fdc-4a6e-89e2-4c802647f66b"
    )
    result["permission_stage_before"] = next(
        s for s in before["records"]["copilot.permissions"] if s.get("native_id") == native_id
    )
    result["permission_stage_after"] = next(
        s for s in after["records"]["copilot.permissions"] if s.get("native_id") == native_id
    )
    result["decisions_before"] = [
        {k: v for k, v in d.items() if k not in ["prompt", "options"]}
        for d in before["records"].get("decisions", [])
        if d.get("native_decision_id") == native_id
    ]
    result["decisions_after"] = [
        {k: v for k, v in d.items() if k not in ["prompt", "options"]}
        for d in after["records"].get("decisions", [])
        if d.get("native_decision_id") == native_id
    ]
    last = before["events"][-1]["sequence"]
    result["new_public_events"] = [e for e in after["events"] if e["sequence"] > last]
    result["ready_for_original_continue"] = bool(
        label == "m11"
        and result.get("stop_phase", {}).get("phase") == "confirmed"
        and result["work_after"]["state"] == "stopped"
        and not result["live_after"]
        and not result.get("error")
    )
    result["native_user_messages_before"] = 0
    result["native_user_messages_after"] = 0
    result["native_history_prefix_preserved"] = True
    for path in (destination / "before/machine/copilot-runtime/home/session-state").glob(
        "*/events.jsonl"
    ):
        old = path.read_bytes()
        new = (original / path.relative_to(destination / "before")).read_bytes()
        result["native_history_prefix_preserved"] &= new.startswith(old)
        result["native_user_messages_before"] += sum(
            json.loads(line)["type"] == "user.message" for line in old.splitlines()
        )
        result["native_user_messages_after"] += sum(
            json.loads(line)["type"] == "user.message" for line in new.splitlines()
        )
    assert result["native_user_messages_before"] == result["native_user_messages_after"]
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
            "original_submissions_unchanged",
            "native_history_prefix_preserved",
        ]
    )
    if label == "m10":
        assert result["work_before"] == result["work_after"]
    (destination / "result.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                k: result[k]
                for k in [
                    "label",
                    "counts",
                    "ready_for_original_continue",
                    "work_after",
                    "native_runtime_seconds",
                ]
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1], "--repair-preflight" in sys.argv[2:]))
