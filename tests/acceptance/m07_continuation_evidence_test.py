"""Validate retained real resource observations. Does not execute a GPU/provider job."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
WINDOW = ROOT / "docs/evidence/w07/continuation-b"
M07 = ROOT / "docs/evidence/m07"


def load(name):
    return json.loads((WINDOW / name).read_bytes())


def test_original_identity_inputs_reservation_and_old_files_are_preserved():
    report = load("report.json")
    before, after = report["before"], report["after"]
    assert before["job"] == after["job"]
    assert before["inputs"] == after["inputs"]
    assert before["reservations"] == after["reservations"]
    assert after["job"]["job_id"] == "w07-live-initial"
    assert after["job"]["attempt_id"] == "attempt-70a06bf9-1fbb-4188-8162-bddc030e7cd4"
    assert (
        after["job"]["input_digest"]
        == "ab6c1b4bf2ab8d097f0b8550c08a3a60faebb20d78a909e47a88a19fdef04a8e"
    )
    assert after["job"]["state"] == "planned" and after["job"]["external_job_id"] is None
    assert after["execution"] == {} and after["job"]["artifact_ids"] == []
    assert after["reservations"] == [
        {
            "id": "w07-live-initial",
            "task_id": "w07-live-task",
            "target_id": "w07-live-task",
            "state": "reserved",
        }
    ]
    assert (
        report["original_hashes_before"]
        == report["original_hashes_after"]
        == {
            "w07-live-initial-report.json": "19dd5cc635dc9ea665910e5336ebe787f192573a521bcda71aefa51e316647ac",
            "w07-live-initial-timing.json": "7e04338414b5af035acab46389b26a7bd3e113f326fdd1778631d684341c67fe",
        }
    )


def test_measured_cold_memory_guard_blocked_before_any_submission():
    receipt = load("window.json")
    observations = [
        json.loads(row) for row in (WINDOW / "observations.jsonl").read_text().splitlines()
    ]
    assert receipt["window"] == "w07-continuation-b" and receipt["duration_seconds"] == 600
    assert len(observations) == 300
    assert max(o["mem_available_bytes"] for o in observations) == 16_763_625_472 < 20_427_268_612
    assert min(o["mem_available_bytes"] for o in observations) == 11_452_526_592
    for observed in observations:
        assert observed["queue_running"] == observed["queue_pending"] == 0
        assert observed["job_state"] == "planned" and observed["execution_phase"] is None
        assert observed["prompt_id"] is None and observed["client_id"] is None
        assert observed["at"] < receipt["started_epoch"] + 600
        assert observed["devices"][0]["torch_vram_total"] == 2_097_152


def test_m07_is_explicitly_not_complete_and_no_ack_loss_or_native_call_is_fabricated():
    summary = json.loads((M07 / "result.json").read_bytes())
    assert summary["status"] == "BLOCKED_BEFORE_INFERENCE_M07_NOT_COMPLETE"
    assert (
        summary["actual_comfy_inferences"]
        == summary["native_cloud_roots"]
        == summary["actual_png_outputs"]
        == 0
    )
    assert (
        summary["provider_prompt_posts"]
        == summary["injected_ack_losses"]
        == summary["new_m07_projects"]
        == 0
    )
    assert summary["native_rpc_ack_loss"] == "NOT_PROVEN"
    assert summary["known_http_ack_loss"] == "NOT_EXECUTED_NO_SUBMISSION"


def test_exported_window_bytes_match_retained_manifest():
    for name, metadata in load("manifest.json").items():
        raw = (WINDOW / name).read_bytes()
        assert (
            hashlib.sha256(raw).hexdigest() == metadata["sha256"] and len(raw) == metadata["bytes"]
        )
