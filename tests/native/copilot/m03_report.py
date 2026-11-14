"""Export only allowlisted retained real M03 observations. Never starts an app/model."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[3]
BASE = Path("/home/quintin/.cache/proscenium-development")
REVISION = "4e8b2337e09dc8416fdc833532f20db473c873a7"


def load(root, name):
    return json.loads((root / name).read_text())


def lines(root, name):
    path = root / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    a, b = [BASE / f"m03-runtime-proof-{label}" for label in ("a", "b")]
    da, db = [load(root, "database-export.json") for root in (a, b)]
    ra, rb = da["records"], db["records"]
    stop_ui = load(a, "browser-steer-stop-default.json")
    cycle = load(b, "browser-decision-cycle-default.json")
    fault_ui = load(b, "browser-transport-default.json")
    requests = ra["requests"] + rb["requests"]
    request_map = {r["request_id"]: r for r in requests}
    sdk_sends = [
        dict(proof=label, **row)
        for label, root in (("a", a), ("b", b))
        for row in lines(root, "sends.jsonl")
    ]
    acks = {v["request_id"]: v for root in (a, b) for v in lines(root, "send-acks.jsonl")}
    native = [
        dict(proof=label, **v)
        for label, root in (("a", a), ("b", b))
        for v in lines(root, "native-events.jsonl")
    ]
    children = [e for e in native if e["type"] == "subagent.started"]
    assert len(sdk_sends) == 5 and len(children) == 1
    assert len({r["request_id"] for r in sdk_sends}) == 5
    queue_b = next(
        r
        for r in ra["requests"]
        if r["submission"].get("text", "").startswith("M03 queued request B.")
    )
    b_payload = next(p for p in ra["copilot.payloads"] if p["request_id"] == queue_b["request_id"])
    steer_id = stop_ui["steer"]["request_id"]
    native_steer = native_b = None
    for path in (a / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"):
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event["type"] != "user.message":
                continue
            data = event.get("data", {})
            if data.get("clientTurnId") == steer_id:
                native_steer = dict(
                    native_event_id=event["id"],
                    at=event["timestamp"],
                    native_message_id=data["messageId"],
                    client_turn_id=data["clientTurnId"],
                    delivery=data.get("delivery"),
                )
            if data.get("clientTurnId") == queue_b["request_id"]:
                extension = next(v for v in data["attachments"] if v["type"] == "extension_context")
                payload = extension["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                native_b = dict(
                    native_event_id=event["id"],
                    native_message_id=data["messageId"],
                    at=event["timestamp"],
                    client_turn_id=data["clientTurnId"],
                    delivery=data.get("delivery"),
                    context_id=payload["context_id"],
                    payload_matches_original=payload == b_payload,
                    payload_sha256=hashlib.sha256(
                        __import__("proscenium.contracts", fromlist=["canonical"]).canonical(
                            payload
                        )
                    ).hexdigest(),
                )
    assert native_steer["delivery"] == "steering"
    assert native_b["payload_matches_original"]
    get_version = next(
        e
        for e in native
        if e["type"] == "tool.execution_start" and e["data"].get("toolName") == "get_version"
    )
    assert get_version["data"]["arguments"] == {"version_id": "m03-version-B"}
    decision = rb["decisions"][0]
    native_answer = next(
        e
        for e in native
        if e["type"] == "user_input.completed"
        and e["data"]["requestId"] == decision["native_decision_id"]
    )
    prices = {
        m["id"]: m["billing"].get("tokenPrices", {})
        for m in load(a / "catalogue", "catalogue.json")["models"]
    }
    barriers = ra["copilot.stops"][0]
    heartbeats = lines(a / "project/.proscenium/tool-output/m03", "child.jsonl")
    source_hashes = {}
    for name in ("service.py", "state.py", "context.py", "router.py", "native.py", "tools.py"):
        data = subprocess.check_output(
            ["git", "show", f"{REVISION}:apps/backend/src/proscenium/copilot/{name}"], cwd=REPO
        )
        source_hashes[name] = hashlib.sha256(data).hexdigest()
    usage = [
        dict(
            proof=label,
            sequence=e["sequence"],
            actor=e["actor"],
            request_id=e["request_id"],
            work_id=e["work_id"],
            **e["payload"],
        )
        for label, data in (("a", da), ("b", db))
        for e in data["events"]
        if e["event_type"] == "usage.recorded"
    ]
    root_fixtures = {}
    for label, root in (("a", a), ("b", b)):
        fixture = load(root, "fixture.json")
        current = {
            relative: hashlib.sha256((Path(fixture["path"]) / relative).read_bytes()).hexdigest()
            for relative in fixture["fixture_sha256"]
        }
        assert current == fixture["fixture_sha256"]
        root_fixtures[label] = dict(
            project_id=fixture["project_id"],
            source_hashes=current,
            versions=[
                {k: item[k] for k in ("item_id", "version_id", "source_commit")}
                for item in fixture["items"]
            ],
        )
    owned_pids = []
    for label, root in (("a", a), ("b", b)):
        host, browser = load(root, "host.private.json"), load(root, "browser.private.json")
        owned_pids.extend(
            [
                dict(
                    proof=label, kind="server", pid=host["server_pid"], start=host["server_start"]
                ),
                dict(proof=label, kind="browser", pid=browser["pid"], start=browser["start"]),
            ]
        )
        for path in (root / "machine/copilot-runtime/home/logs").glob("process-*.log"):
            owned_pids.append(
                dict(proof=label, kind="sdk-direct-runtime", pid=int(path.stem.rsplit("-", 1)[1]))
            )
        for marker in (root / "project/.proscenium/tool-output/m03").glob("*.jsonl"):
            owned_pids.append(
                dict(
                    proof=label,
                    kind="bounded-native-helper",
                    pid=json.loads(marker.read_text().splitlines()[0])["pid"],
                )
            )
    for path in (a / "catalogue/logs").glob("process-*.log"):
        owned_pids.append(
            dict(
                proof="catalogue",
                kind="catalogue-direct-runtime",
                pid=int(path.stem.rsplit("-", 1)[1]),
            )
        )
    for row in lines(a, "process-observations.jsonl"):
        if row.get("state") == "running":
            owned_pids.append(
                dict(proof="a", kind="independent-ffmpeg", pid=row["pid"], start=row["start"])
            )
    for row in owned_pids:
        row["proc_exists_after_cleanup"] = Path(f"/proc/{row['pid']}").exists()
    assert not any(row["proc_exists_after_cleanup"] for row in owned_pids)
    # Read-only orphan check. Inspect membership/own-root matches only, never dump
    # unrelated argv or kill by a global executable name.
    ancestors, cursor = set(), os.getpid()
    while cursor > 1:
        ancestors.add(cursor)
        try:
            cursor = int(Path(f"/proc/{cursor}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError):
            break
    groups = {r["pid"] for r in owned_pids if r["kind"] in {"browser", "independent-ffmpeg"}}
    leftovers = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) in ancestors:
            continue
        try:
            fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
            argv = (process / "cmdline").read_bytes()
            if fields[0] != "Z" and (
                int(fields[2]) in groups or any(str(root).encode() in argv for root in (a, b))
            ):
                leftovers.append(int(process.name))
        except (OSError, ValueError, ProcessLookupError):
            pass
    assert not leftovers, "Inspect exact owned leftover identities before any cleanup"
    report = dict(
        evidence_kind="Real compiled Chromium, production backend and official SDK; scripted owner input",
        executed_application_revision=REVISION,
        post_run_fix_commits={"M02_R1": "c9c52ba", "M04_role_only": "16011d9"},
        post_run_fixes_native_rerun=False,
        executed_production_source_sha256=source_hashes,
        browser=dict(
            engine=load(a, "browser-setup-default.json")["browser"],
            playwright="1.65.1",
            width=1920,
            height=1080,
            dpr=1,
            compiled_index_sha256=hashlib.sha256(
                (REPO / "apps/frontend/dist/index.html").read_bytes()
            ).hexdigest(),
        ),
        sdk="1.0.34",
        cli="1.0.112",
        binary_sha256="463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69",
        allocation=dict(
            actual_sdk_sends=5,
            maximum_sends=6,
            queue_sends=4,
            steer_sends=1,
            native_children=1,
            maximum_children=3,
            repeated_original_sends=0,
            extra_model_calls_for_R1_or_M04=0,
        ),
        sdk_sends=[
            {**s, "native_message_id": acks[s["request_id"]]["native_message_id"]}
            for s in sdk_sends
        ],
        fixtures=root_fixtures,
        child=children[0],
        economical_model=dict(
            lead="gpt-6.1-sol",
            worker="gpt-6-luna",
            model_policy="required",
            actual_selection="configured_required",
            lead_catalogue_prices=prices["gpt-6.1-sol"],
            worker_catalogue_prices=prices["gpt-6-luna"],
            standard_context_rate_ratio=20,
            currency_unit_unreported=True,
        ),
        queue_b=dict(
            request_id=queue_b["request_id"],
            context_id=queue_b["submission"]["context_id"],
            accepted_at=queue_b["accepted_at"],
            selection=b_payload["selection"],
            attached_files=b_payload["attached_files"],
            native_delivery=native_b,
            get_version=get_version,
            browser_after_queue=load(a, "browser-queue-b-default.json")["view_after_queue"],
            final_state=queue_b["receipt"]["state"],
            retained_failure="Browser last-window session-cookie loss; watchdog fenced unanswered request",
        ),
        steer=dict(
            request_id=steer_id,
            native_delivery=native_steer,
            final_state=request_map[steer_id]["receipt"]["state"],
            semantic_application_claimed=False,
        ),
        stop=dict(
            request_id=stop_ui["stop_admission"]["request_id"],
            admission=stop_ui["stop_admission"],
            confirmed=barriers,
            actual_child_tool_before_stop=stop_ui["active_child_tool"],
            child_pid=stop_ui["actual_child_pid"],
            helper_started=heartbeats[0],
            helper_last_write=heartbeats[-1],
            natural_timeout_seconds=70,
            no_helper_natural_completion=not any(
                h["event"] == "natural-completion" for h in heartbeats
            ),
            no_further_effects_observed=stop_ui["no_further_helper_effects"],
            helper_final_sha256=hashlib.sha256(
                (a / "project/.proscenium/tool-output/m03/child.jsonl").read_bytes()
            ).hexdigest(),
            independent_before=stop_ui["independent_before"],
            independent_after=stop_ui["independent_after"],
            independent_process_observations=lines(a, "process-observations.jsonl"),
            independent_final=next(
                j for j in ra["jobs"] if j["job_id"] == "m03-independent-encode"
            ),
            held_after=[r["request_id"] for r in stop_ui["held_after"]],
        ),
        reload=load(a, "browser-reload-default.json"),
        resume=dict(
            reconcile_only=load(a, "browser-resume-reconcile_only.json")["command"],
            release=load(a, "browser-resume-release_held_queue.json")["command"],
            continue_failure={
                k: load(a, "browser-resume-continue_work.json")[k]
                for k in ("status", "body", "command")
            },
            interrupted_reconcile_failure={
                k: load(b, "browser-resume-reconcile_only.json")[k]
                for k in ("status", "body", "command")
            },
        ),
        decision=dict(
            before=cycle["before"],
            restored=cycle["restored"],
            owner_command=cycle["answer_command"],
            owner_receipt=cycle["answer_receipt"],
            current=decision,
            native_completed_event=native_answer,
            completed_root=cycle["completed"]["receipt"],
            owner_retry=cycle["owner_retry"],
            tool_to_owner=cycle["tool_to_owner"],
            scope_rejections=cycle["scope_rejections"],
        ),
        usage=usage,
        genuine_transport_fault=dict(
            action=lines(b, "transport.jsonl")[0],
            accepted_before_cut=fault_ui["before_cut"]["receipt"],
            after=fault_ui["after"]["request"]["receipt"],
            work_state="awaiting_resume",
            native_ack_loss_claimed=False,
            original_send_count=sum(
                s["request_id"] == fault_ui["receipt"]["request_id"] for s in sdk_sends
            ),
        ),
        owned_processes=owned_pids,
        cleanup={"a": load(a, "cleanup.json"), "b": load(b, "cleanup.json")},
        owned_group_or_root_argument_leftovers=leftovers,
        milestone_status="BLOCKED: parent UI Resume payload defects and independent M02 dependency",
        native_child_stop_status="PASS: active builtin/helper, native pause/abort/quiescence, held B and independent live encoder",
        true_missing_native_ack_fault_tested=False,
        no_blender_unity_gpu_or_independent_services_touched=True,
    )
    (output / "run.json").write_text(json.dumps(report, indent=2) + "\n")
    for label, root in (("a", a), ("b", b)):
        journal = [dict(proof=label, **e) for e in (da if label == "a" else db)["events"]]
        (output / f"public-events-{label}.json").write_text(json.dumps(journal, indent=2) + "\n")
    captures = [
        (a, "03-steer-before-stop.png", "steer-before-stop.png"),
        (a, "04-stop-requested.png", "stop-requested.png"),
        (a, "05-stop-observed.png", "child-stop-confirmed.png"),
        (a, "action-queue-b-default.png", "queued-B-browse-A.png"),
        (a, "action-resume-continue_work.png", "continue-work-409.png"),
        (a, "FAILED-decision-disconnect.png", "initial-browser-session-ended.png"),
        (b, "08-decision-before-disconnect.png", "owner-card-before.png"),
        (b, "09-decision-restored.png", "owner-card-restored.png"),
        (b, "action-collect-default.png", "measured-usage-unit-unavailable.png"),
        (b, "action-resume-reconcile_only.png", "work-reconcile-409.png"),
    ]
    for root, name, target in captures:
        shutil.copyfile(root / name, output / target)
        with Image.open(output / target) as image:
            assert image.size == (1920, 1080)
    manifest = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in output.iterdir()
        if p.name != "manifest.json" and p.is_file()
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "sdk_sends": len(sdk_sends),
                "children": len(children),
                "screenshots": len(captures),
                "status": report["milestone_status"],
            }
        )
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
