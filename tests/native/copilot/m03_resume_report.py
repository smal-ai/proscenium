"""Read retained follow-up evidence only. Never starts native work or supplies outcomes."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from m03_resume_control import BASE, REPO, ROOT, manifest
from PIL import Image
from proscenium.copilot.launcher import identity


def load(path):
    return json.loads(path.read_text())


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    output = REPO / "docs/evidence/m03-resume"
    output.mkdir(parents=True, exist_ok=True)
    source = subprocess.check_output(["git", "rev-parse", "e3cddf8"], cwd=REPO, text=True).strip()
    frozen, snapshots, runs = {}, {}, {}
    for label, action in [("a", "continue-a"), ("b", "reconcile-b")]:
        original = BASE / f"m03-runtime-proof-{label}"
        before = ROOT / "before" / label
        before_manifest = load(ROOT / f"before-{label}.private.json")
        assert manifest(before) == before_manifest
        snapshots[label] = dict(
            files=len(before_manifest),
            manifest_sha256=digest(ROOT / f"before-{label}.private.json"),
            verified_exact_before_copy=True,
        )
        frozen[label] = {}
        for path in original.iterdir():
            if path.is_file():
                # All original top-level exports, images, logs and receipts are unchanged.
                assert digest(path) == before_manifest[path.name], path.name
                if path.suffix == ".png" or path.name in {
                    "database-export.json",
                    "fixture.json",
                    "sends.jsonl",
                    "send-acks.jsonl",
                    "native-events.jsonl",
                    "transport.jsonl",
                    "cleanup.json",
                }:
                    frozen[label][path.name] = digest(path)
        for name in ["m03_hold.py", "m03_direct.py", "m03_transport.py"]:
            relative = f"project/{name}"
            assert digest(original / relative) == before_manifest[relative]
        for path in (original / "project/.proscenium/tool-output/m03").glob("*"):
            if path.is_file():
                assert digest(path) == before_manifest[str(path.relative_to(original))]
        before_export = load(before / "database-export.json")
        after = load(ROOT / label / "after.private.json")
        ui = load(ROOT / label / f"ui-{action}.json")
        operation = next(
            o
            for o in ui["settled"]["copilot.resumes"]
            if o["body"]["operation_id"] == ui["command"]["operation_id"]
        )
        old_ids = {r["request_id"] for r in before_export["records"]["requests"]}
        assert old_ids == {r["request_id"] for r in after["records"]["requests"]}
        assert not after["records"].get("copilot.continuation_sends")
        old_last = before_export["events"][-1]["sequence"]
        events = [e for e in after["events"] if e["sequence"] > old_last]
        (output / f"public-events-{label}.json").write_text(json.dumps(events, indent=2) + "\n")
        live = [
            i
            for i in after["records"]["invocations"]
            if i["state"] not in {"completed", "failed", "stopped"}
        ]
        clean = load(ROOT / label / "cleanup.json")
        assert all(not clean[k]["same_identity_alive"] for k in ["server", "browser", "native"])
        assert not any(h["proc_exists"] for h in clean["helpers"])
        evidence = ROOT / label / "reconciliation-evidence.jsonl"
        runs[label] = dict(
            action=action,
            viewport=ui["viewport"],
            command=ui["command"],
            http_status=ui["status"],
            receipt=ui["receipt"],
            operation=operation,
            target_before=ui["target_before"],
            project_revision=ui["before"]["project"]["state_revision"],
            requests_before=ui["requests_before"],
            requests_after=ui["requests_after"],
            held_before=[
                r["request_id"] for r in ui["requests_before"] if r["receipt"]["state"] == "held"
            ],
            held_after=[
                r["request_id"] for r in ui["requests_after"] if r["receipt"]["state"] == "held"
            ],
            work_after=after["records"]["work"],
            live_invocations=live,
            decisions_after=after["records"].get("decisions", []),
            native_session=after["records"]["copilot.session"],
            reconciliation_evidence=[json.loads(line) for line in evidence.read_text().splitlines()]
            if evidence.exists()
            else None,
            reconciliation_evidence_boundary="A did not have the observational evidence wrapper; only its retained guard result and live invocation are claimed"
            if label == "a"
            else "Actual returned metadata evidence",
            cleanup=clean,
        )
    selector = load(ROOT / "a/ui-selector.json")
    stop = load(ROOT / "a/ui-stop-b-failed.json")
    checks_server = load(ROOT / "ui-checks-server.json")
    checks_browsers = [
        json.loads(line) for line in (ROOT / "ui-checks-browsers.jsonl").read_text().splitlines()
    ]
    for row in [checks_server, *checks_browsers]:
        row["same_identity_alive"] = identity(row["pid"]) == row["start"]
        assert not row["same_identity_alive"]
    # Read-only own-process/root-argument check. No global process-name matching or killing.
    ancestors, cursor = set(), os.getpid()
    while cursor > 1:
        ancestors.add(cursor)
        try:
            cursor = int(Path(f"/proc/{cursor}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError):
            break
    own_groups = {
        runs[label]["cleanup"][kind]["pid"] for label in "ab" for kind in ["server", "browser"]
    }
    leftovers = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) in ancestors:
            continue
        try:
            fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
            argv = (process / "cmdline").read_bytes()
            if fields[0] != "Z" and (int(fields[2]) in own_groups or str(ROOT).encode() in argv):
                leftovers.append(int(process.name))
        except (OSError, ValueError):
            pass
    assert not leftovers
    compiled = {
        str(p.relative_to(REPO)): digest(p)
        for p in (REPO / "apps/frontend/dist").rglob("*")
        if p.is_file() and p.suffix in {".html", ".js", ".css"}
    }
    production = {
        str(p.relative_to(REPO)): digest(p)
        for p in [
            REPO / "apps/frontend/src/features/chat/ChatPanel.tsx",
            REPO / "apps/frontend/src/features/chat/resume.ts",
            *[
                REPO / f"apps/backend/src/proscenium/copilot/{name}.py"
                for name in ["service", "state", "context", "native"]
            ],
        ]
    }
    report = dict(
        application_revision=source,
        accepted_ui_fix="5333468",
        evidence_date="2026-11-14",
        status="BLOCKED: UI commands accepted correctly; original A continuation not executed",
        source_sha256=production,
        compiled_sha256=compiled,
        sdk="1.0.34",
        cli="1.0.112",
        browser=load(ROOT / "a/ui-setup.json")["browser"],
        lead_model="gpt-6.1-sol",
        required_worker_model="gpt-6-luna",
        snapshots=snapshots,
        original_evidence_unchanged_sha256=frozen,
        allocation=dict(
            original_actual_sdk_sends=5,
            followup_sdk_sends=0,
            followup_children=0,
            followup_helpers=0,
            maximum_followup_sends=1,
            maximum_followup_children=1,
            named_followup_roots=1,
            new_queue_requests=0,
        ),
        runs=runs,
        selector={k: selector[k] for k in ["options", "selected", "known_original", "viewport"]},
        exact_pending_b_stop={k: stop[k] for k in ["at", "status", "receipt", "command", "error"]},
        ui_checks=dict(
            passed=4,
            evidence_kind="Real Chromium and compiled UI with controlled HTTP/socket APIs, not native continuation",
            cases=[
                "stopped work from durable events and own revision",
                "work-targeted Reconcile own revision",
                "multiple eligible targets require selection",
                "uncertain Resume retries exact original command after reload",
            ],
            server=checks_server,
            browsers=checks_browsers,
        ),
        owned_process_leftovers=leftovers,
        no_product_source_changes=True,
        original_m10_accessed=False,
        actual_missing_native_ack_proof=False,
        successful_continuation=False,
        pending_original_resume_retry_live_fault_tested=False,
    )
    assert not (ROOT / "allocation.jsonl").exists()
    assert (
        sum(
            len((ROOT / "before" / label / "sends.jsonl").read_text().splitlines())
            for label in "ab"
        )
        == 5
    )
    (output / "run.json").write_text(json.dumps(report, indent=2) + "\n")
    captures = [
        ("a", "selector.png", "original-A-selector.png"),
        ("a", "continue-a.png", "continue-A-admitted.png"),
        ("a", "FAILED-stop-b.png", "exact-B-stop-refused.png"),
        ("b", "before-reconcile-b.png", "known-ACK-reconcile-control.png"),
        ("b", "reconcile-b.png", "known-ACK-reconcile-admitted.png"),
    ]
    for label, original, name in captures:
        shutil.copyfile(ROOT / label / original, output / name)
        with Image.open(output / name) as image:
            assert image.size == (1920, 1080)
    hashes = {
        p.name: digest(p) for p in output.iterdir() if p.is_file() and p.name != "manifest.json"
    }
    (output / "manifest.json").write_text(json.dumps(hashes, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                status=report["status"],
                original_exports_unchanged=True,
                snapshots_verified=True,
                sdk_sends=0,
                screenshots=len(captures),
            )
        )
    )


if __name__ == "__main__":
    main()
