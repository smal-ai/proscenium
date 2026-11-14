"""Read-only export of the original W07 continuation window. No inference or service calls."""

import hashlib
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = Path("/home/quintin/.cache/proscenium-development/providers-w07-20261114")
WINDOW = ROOT / "continuations/w07-continuation-b"
OUT = REPO / "docs/evidence/w07/continuation-b"
M07 = REPO / "docs/evidence/m07"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    report = json.loads((WINDOW / "report.json").read_bytes())
    receipt = json.loads((WINDOW / "window.json").read_bytes())
    observations = [
        json.loads(row) for row in (WINDOW / "observations.jsonl").read_text().splitlines()
    ]
    for name, digest in report["original_hashes_after"].items():
        assert sha((ROOT / name).read_bytes()) == digest == report["original_hashes_before"][name]
    assert report["after"]["job"]["state"] == "planned" and report["after"]["execution"] == {}
    assert report["after"]["job"] == report["before"]["job"]
    assert not (WINDOW / "prompt-dispatch.json").exists()
    assert not (WINDOW / "known-ack-loss.json").exists()
    assert (
        len(report["after"]["reservations"]) == 1
        and report["after"]["reservations"][0]["state"] == "reserved"
    )
    assert all(o["mem_available_bytes"] < 20_427_268_612 for o in observations)
    OUT.mkdir(parents=True, exist_ok=True)
    M07.mkdir(parents=True, exist_ok=True)
    raw_hashes = {}
    for name in ("window.json", "report.json", "observations.jsonl", "preservation.json"):
        raw = (WINDOW / name).read_bytes()
        shutil.copyfile(WINDOW / name, OUT / name)
        raw_hashes[name] = {"sha256": sha(raw), "bytes": len(raw)}
    summary = dict(
        status="BLOCKED_BEFORE_INFERENCE_M07_NOT_COMPLETE",
        window="w07-continuation-b",
        code_commit=receipt["application_revision"],
        original_background_shell="sh_1da9711db0017BhqnTL3H2kd6w",
        receipt_directory=str(WINDOW),
        started_epoch=receipt["started_epoch"],
        finished_epoch=report["finished_epoch"],
        admission_deadline_seconds=receipt["duration_seconds"],
        total_wall_seconds_including_local_report_sealing=report["finished_epoch"]
        - receipt["started_epoch"],
        observed_samples=len(observations),
        required_host_mem_available_bytes=20_427_268_612,
        host_min_bytes=min(o["mem_available_bytes"] for o in observations),
        host_max_bytes=max(o["mem_available_bytes"] for o in observations),
        peak_shortfall_bytes=20_427_268_612 - max(o["mem_available_bytes"] for o in observations),
        external_queue_counts=sorted(
            {(o["queue_running"], o["queue_pending"]) for o in observations}
        ),
        original_job=report["after"]["job"],
        original_reservations=report["after"]["reservations"],
        original_hashes_before=report["original_hashes_before"],
        original_hashes_after=report["original_hashes_after"],
        actual_comfy_inferences=0,
        provider_prompt_posts=0,
        injected_ack_losses=0,
        native_cloud_roots=0,
        native_children=0,
        new_m07_projects=0,
        new_m07_selections=0,
        actual_png_outputs=0,
        native_rpc_ack_loss="NOT_PROVEN",
        known_http_ack_loss="NOT_EXECUTED_NO_SUBMISSION",
        remaining_journey="NOT_RUN: original did not succeed. No initial/correction/selection/retention journey is claimed.",
        models_or_services_changed=False,
        input_graph_changed=False,
        native_work_adoption=False,
        report_hashes=raw_hashes,
    )
    (M07 / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    manifest = {
        p.name: {"sha256": sha(p.read_bytes()), "bytes": p.stat().st_size}
        for p in sorted(OUT.iterdir())
        if p.is_file() and p.name != "manifest.json"
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": summary["status"],
                "host_max_bytes": summary["host_max_bytes"],
                "original_files_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
