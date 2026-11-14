"""Read retained own receipts and export allowlisted real M07 evidence. No inference."""

import hashlib
import json
import shutil
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = (
    Path.home()
    / ".cache/proscenium-development/image-generation-light/runs/m07-fp16-execution-20261114"
)
OUT = REPO / "docs/evidence/m07-fp16/execution"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(name):
    return json.loads((ROOT / name).read_bytes())


def lines(name):
    return [json.loads(row) for row in (ROOT / name).read_text().splitlines()]


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + "\n")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    final = read("post-cleanup.json")
    fixture = read("fixture.json")
    public = read("collect.json")["state"]
    assert all(j["state"] == "succeeded" for j in final["jobs"]) and len(final["jobs"]) == 2
    assert (
        final["original_files_unchanged"]
        and final["source_unchanged"]
        and final["remaining_claims"] == []
    )
    assert final["host_exited"] and final["sdk_exited"] and final["comfy_state"] in {"Z", "exited"}
    assert len(list(ROOT.glob("prompt-request-*.json"))) == 2
    assert len(lines("sdk-send.jsonl")) == 2
    for name in (
        "host-report.json",
        "post-cleanup.json",
        "external-cleanup.json",
        "fixture.json",
        "fault-known-response.json",
        "protected-preservation.json",
        "comfy-runtime-lines.json",
    ):
        shutil.copyfile(ROOT / name, OUT / name)
    for name in (
        "activate",
        "initial",
        "select-initial",
        "correction",
        "select-correction",
        "reuse",
        "collect",
    ):
        shutil.copyfile(ROOT / (name + ".json"), OUT / ("public-" + name + ".json"))
        shutil.copyfile(ROOT / (name + ".png"), OUT / ("browser-" + name + ".png"))
    # Retain the first failed browser setup as a disclosed fixture deviation.
    shutil.copyfile(ROOT / "setup.json", OUT / "first-setup-deviation.json")
    for name in (
        "prompt-request-m07-fp16-initial.json",
        "prompt-ack-m07-fp16-initial.json",
        "prompt-request-m07-fp16-correction.json",
        "prompt-ack-m07-fp16-correction.json",
    ):
        shutil.copyfile(ROOT / name, OUT / name)
    write("native-delivery.json", lines("sdk-send.jsonl"))
    write("generation-tool-results.json", lines("generation-tool-results.jsonl"))
    reads = lines("provider-reads.jsonl")
    uncertain = [
        row
        for row in reads
        if any(
            j["job_id"] == "m07-fp16-initial" and j["state"] == "outcome_uncertain"
            for j in row["state"]["jobs"]
        )
    ]
    assert uncertain and all(
        row["state"]["reservations"][0]["state"] == "reserved" for row in uncertain
    )
    write("provider-reads-during-uncertainty.json", uncertain)
    histories = {}
    for row in reads:
        if isinstance(row["response"], dict):
            for identity, record in row["response"].items():
                if isinstance(record, dict) and record.get("status", {}).get("completed"):
                    histories[identity] = record
    assert len(histories) == 2
    write("provider-terminal-history.json", histories)
    sdk = lines("sdk-send.jsonl")
    root = Path(fixture["project"]["path"])
    for job in final["jobs"]:
        image = public["artifacts"][job["job_id"] + "-artifact-0"]
        data = root / ".proscenium/artefacts/sha256" / image["sha256"] / "content"
        assert sha(data) == image["sha256"]
        shutil.copyfile(data, OUT / (job["job_id"] + ".png"))
        metadata = public["artifacts"][job["job_id"] + "-artifact-provenance"]
        provenance = json.loads(
            (root / ".proscenium/artefacts/sha256" / metadata["sha256"] / "content").read_bytes()
        )
        write(job["job_id"] + "-provenance.json", provenance)
    for reference in fixture["references"]:
        data = root / ".proscenium/artefacts/sha256" / reference["sha256"] / "content"
        assert sha(data) == reference["sha256"]
        shutil.copyfile(data, OUT / (reference["artifact_id"] + ".png"))
    samples = lines("resources.jsonl")
    write(
        "resource-summary.json",
        final["resource_summary"]
        | {
            "first": samples[0],
            "last": samples[-1],
            "host_before_start": read("host-report.json")["before_start"],
            "host_after_startup": read("host-report.json")["after_startup"],
            "all_device_vram_counters_include_protected_other_residents": True,
        },
    )
    shutil.copyfile(ROOT / "resources.jsonl", OUT / "resources.jsonl")
    source_files = [
        "apps/backend/src/proscenium/providers/service.py",
        "apps/backend/src/proscenium/providers/sd_turbo_fp16.py",
        "apps/backend/src/proscenium/copilot/tools.py",
        "apps/backend/src/proscenium/copilot/native.py",
    ]
    write(
        "runtime-identities.json",
        dict(
            application_base="011ab92d7c0f2a85ce61ea0235ad305d98b317fe",
            executed_host_commit="970040b8a22a3298b653924b2ce033e1b8ffba86",
            sdk_version=version("github-copilot-sdk"),
            httpx_version=version("httpx"),
            pillow_version=version("Pillow"),
            browser=read("collect.json")["browser"],
            source_sha256={name: sha(REPO / name) for name in source_files},
            native_binary_sha256=sha(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            note="No production changes. Browser fixture corrected explicit owner activation before the first native Queue; signal-cleanup fix added after run, with offline coverage only.",
        ),
    )
    commands = []
    for phase in ("activate", "initial", "select-initial", "correction", "select-correction"):
        commands += [
            {"phase": phase, **row}
            for row in read(phase + "-traffic.json")
            if row["method"] == "POST"
        ]
    write("public-owner-commands.json", commands)
    latencies = {}
    for job in final["jobs"]:
        identity = job["job_id"]
        request = next(
            r for r in public["requests"]["items"] if r["receipt"]["work_id"] == job["work_id"]
        )
        completed = next(
            e
            for e in public["events"]
            if e["event_type"] == "work.state_changed"
            and e["work_id"] == job["work_id"]
            and e["payload"]["state"] == "completed"
        )
        terminal = next(
            e
            for e in public["events"]
            if e["event_type"] == "job.state_changed"
            and e["payload"]["job_id"] == identity
            and e["payload"]["state"] == "succeeded"
        )
        sent = read(f"prompt-request-{identity}.json")["at"]
        messages = dict(histories[job["external_job_id"]]["status"]["messages"])
        latencies[identity] = {
            "owner_queue_accepted_to_native_work_completed_seconds": (
                datetime.fromisoformat(completed["occurred_at"])
                - datetime.fromisoformat(request["accepted_at"])
            ).total_seconds(),
            "prompt_post_to_product_job_succeeded_seconds": datetime.fromisoformat(
                terminal["occurred_at"]
            ).timestamp()
            - sent,
            "comfy_execution_seconds": (
                messages["execution_success"]["timestamp"]
                - messages["execution_start"]["timestamp"]
            )
            / 1000,
        }
    assert all(
        v["owner_queue_accepted_to_native_work_completed_seconds"] <= 180
        for v in latencies.values()
    )
    write("latencies.json", latencies)
    facts = dict(
        result="GENERATION_SELECTION_RECOVERY_WORKFLOW_PROVEN_COORDINATOR_ACCEPTANCE_PENDING",
        actual_inferences=2,
        actual_prompt_posts=2,
        actual_cloud_roots=2,
        native_children=0,
        actual_initial_prompt_id=final["jobs"][1]["external_job_id"]
        if final["jobs"][1]["job_id"] == "m07-fp16-initial"
        else final["jobs"][0]["external_job_id"],
        known_provider_response_loss="ONE actual completed initial response withheld; production received no out-of-band ID and reconciled original client ID through genuine history",
        native_rpc_ack_loss="NOT_TESTED",
        quiet_application_restart_recovery="NOT_TESTED",
        c12_actual_scope="Two separate explicit owner Queues -> two distinct frozen human task IDs, default policy 3 each, one consumed reservation each, one identical canonical frame target. No native children or actual default-three exhaustion were allocated. Existing accepted offline cases remain the child/uncertainty/exhaustion evidence.",
        source_unchanged=True,
        old_fixtures_unchanged=True,
        unrelated_frame_unchanged=True,
        previous_selected_png_retained=True,
        no_auto_selection_on_technical_success=True,
        reuse_and_reload_extra_prompt_posts=0,
        quality_limit="Requested cobalt-blue correction was NOT achieved: both observed images remain red, though output PNG hashes and some shape/detail differ. No reroll or extra inference.",
        ui_defect_for_coordinator="After selecting correction, displayed img URI/hash is the correction but the job selector still labels the initial image-generation job. No unowned UI fix or identity relabelling.",
        cleanup_deviation="Uvicorn re-raised SIGTERM after lifespan, bypassing outer Python finally. Exact saved PID/start/port/root verified external Comfy cleanup completed at <1800s. Corrected fixture signal wrapper has offline coverage, not another native run.",
        private_runtime=str(ROOT),
    )
    write("verification.json", facts)
    write(
        "manifest.json",
        {
            p.name: {"sha256": sha(p), "bytes": p.stat().st_size}
            for p in sorted(OUT.iterdir())
            if p.is_file() and p.name != "manifest.json"
        },
    )
    print(
        json.dumps(
            {
                "export": str(OUT),
                "inferences": 2,
                "cloud_roots": len(sdk),
                "c03_actual_response_loss_reconciled": True,
                "quality_blue_correction_achieved": False,
                "cleanup_complete": True,
            }
        )
    )


if __name__ == "__main__":
    main()
