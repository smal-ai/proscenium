"""Retained actual renderer observations. Read-only, no worker/model/encode calls."""

import json
from pathlib import Path

from PIL import Image
from proscenium.contracts import validate
from proscenium.previews.journal import sha

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m14-native-faults"


def load(name):
    return json.loads((EVIDENCE / name).read_text())


def test_actual_failed_writer_publishes_only_receipted_frame1():
    data = load("actual-run.json")
    assert data["status"] == "bounded_C16_failed_writer_C25_same_job_retry_pass"
    first = data["attempts"][0]
    job = first["job"]
    validate("JobRecord", job)
    validate("FrameAvailability", first["availability"])
    assert (job["state"], job["native_exit_code"], job["error_code"], job["availability"]) == (
        "failed",
        1,
        "render_failed",
        "partial",
    )
    assert job["completed_frames"] == 1 and job["expected_frames"] == 3
    assert "Unreceipted output requires reconciliation, not overwrite" in first["blender_log"]
    assert first["journals"]["native-error.json"] == {
        "error_code": "render_failed",
        "exception": "ValueError",
    }
    available = first["availability"]
    assert available["available_ranges"] == [{"start": 1, "end": 1}]
    assert available["failed_ranges"] == [{"start": 2, "end": 3}]
    assert available["pending_ranges"] == available["playable_ranges"] == []
    assert "frame-00000002.json" not in first["journals"]
    assert [row["source_frame"] for row in data["before_retry"]["frames"]] == [1]
    assert data["before_retry"]["segments"] == []


def test_injection_is_explicit_operator_bytes_not_native_output_or_disk_exhaustion():
    data = load("actual-run.json")
    injection = data["injection"]
    raw = bytes.fromhex(injection["hex"])
    assert len(raw) == injection["bytes"] == 78
    assert raw.startswith(b"OPERATOR-INJECTED UNRECEIPTED PARTIAL PNG\nnot native-generated\n")
    assert sha(raw) == injection["sha256"]
    assert not injection["native_generated"] and not injection["disk_exhaustion"]
    path = Path(injection["path"])
    assert path.is_relative_to(Path(data["attempts"][0]["root"]))
    assert path.read_bytes() == raw
    assert all(row["sha256"] != injection["sha256"] for row in data["after_retry"]["artifacts"])


def test_retry_preserves_logical_job_input_and_old_failed_attempt():
    data = load("actual-run.json")
    first, second = [row["job"] for row in data["attempts"]]
    validate("JobRecord", second)
    validate("JobRetry", data["retry_body"])
    assert first["job_id"] == second["job_id"] == data["retry_body"]["job_id"]
    assert (
        first["input_digest"]
        == second["input_digest"]
        == "d8642e14cebd1bc5bd07117acab7f3e2b1a3970bf8b37943753090189d1d36c3"
    )
    assert first["attempt_id"] == "attempt-8654c287-61f3-4b5e-b9da-32cb5f537e53"
    assert second["attempt_id"] == "attempt-87a964ef-9b82-41ca-ad10-a875511c6a3f"
    assert (first["attempt_number"], second["attempt_number"]) == (1, 2)
    assert first["render_spec"] == second["render_spec"] and first["target"] == second["target"]
    assert (second["state"], second["native_exit_code"], second["availability"]) == (
        "succeeded",
        0,
        "complete",
    )
    assert len(data["after_retry"]["jobs"]) == 1 and len(data["after_retry"]["attempts"]) == 2
    old_after = next(
        row for row in data["after_retry"]["attempts"] if row["attempt_id"] == first["attempt_id"]
    )
    assert old_after == data["before_retry"]["attempts"][0]
    assert (old_after["state"], old_after["native_exit_code"]) == ("failed", 1)
    assert data["old_exact_receipt_replay"][1] is True
    assert data["old_fresh_receipt_rejection"] == data["old_state_rejection"] == "scope_denied"
    assert data["stale_probes_no_mutation"]


def test_frame1_adoption_not_rerender_and_final_three_frame_segment():
    data, retained = load("actual-run.json"), load("retention.json")
    first, second = data["attempts"]
    original = first["journals"]["frame-00000001.json"]
    adopted = second["journals"]["frame-00000001.json"]
    assert adopted == data["adopted_receipt_before_reconciliation"]
    assert (
        adopted["attempt_id"] != original["attempt_id"]
        and adopted["receipt_id"] != original["receipt_id"]
    )
    assert (
        adopted["sha256"] == original["sha256"]
        and adopted["artifact_id"] == original["artifact_id"]
    )
    assert "timing-00000001.json" in first["journals"]
    assert "timing-00000001.json" not in second["journals"]
    assert sorted(row["frame"] for row in retained["actual_rendered_frames"]) == [1, 2, 3]
    assert data["actual_rendered_frames"] == 3
    assert len(retained["normal_segment_encodes"]) == data["normal_segment_encodes"] == 1
    receipt = retained["normal_segment_encodes"][0]["receipt"]
    validate("PreviewSegment", receipt)
    assert receipt["frame_count"] == 3 and receipt["source_frames"] == {"start": 1, "end": 3}
    assert (
        second["availability"]["available_ranges"]
        == second["availability"]["playable_ranges"]
        == [{"start": 1, "end": 3}]
    )
    assert second["availability"]["failed_ranges"] == second["availability"]["pending_ranges"] == []
    timing = second["journals"]["encode-00000001.json"]
    assert (timing["ready_ns"] - timing["started_ns"]) / 1e9 < 60
    for frame in range(1, 4):
        path = Path(second["root"]) / f"frames/{frame:08d}.png"
        with Image.open(path) as image:
            image.load()
            assert image.format == "PNG" and image.mode == "RGB" and image.size == (640, 360)


def test_selected_source_media_and_both_source_trees_remain_unchanged():
    data = load("actual-run.json")
    before, after = data["before_retry"], data["after_retry"]
    assert before["selections"] == after["selections"]
    assert before["versions"] == after["versions"]
    selected = after["selections"][0]
    assert selected["state"] == "selected" and selected["state_revision"] == 1
    assert selected["choice"] == {
        "kind": "version",
        "version": {"item_id": "procedural-block", "version_id": "procedural-block-v1"},
    }
    assert data["served_frame1_after_failure"] == data["served_frame1_after_retry"]
    served = data["served_frame1_after_failure"]
    assert served["status"] == 200 and served["range_status"] == 206 and served["range_bytes"] == 16
    assert served["sha256"] == data["reviewable_frame1"]["sha256"]
    assert data["source_preserved"] and data["source_after"] == data["prepared"]["source_before"]
    assert (
        data["clone_blend_sha256_after"]
        == "fd0d1293080a1cd9f0bfaf3feace1488ec6c85e86705159a046daf07d35e0cd8"
    )
    for name, metadata in data["attempts"][0]["files"].items():
        assert sha((Path(data["attempts"][0]["root"]) / name).read_bytes()) == metadata["sha256"]


def test_real_process_identities_seals_bounds_and_cleanup():
    data = load("actual-run.json")
    first, second = data["attempts"]
    assert (
        first["journals"]["terminal.json"]["finished_ns"]
        < second["journals"]["process.json"]["started_ns"]
    )
    for attempt in (first, second):
        journals = attempt["journals"]
        task = journals["task.json"]
        terminal, verified = journals["terminal.json"], journals["renderer-verified.json"]
        assert (
            terminal["task_sha256"]
            == verified["task_sha256"]
            == attempt["files"]["task.json"]["sha256"]
        )
        assert verified["renderer_files_sha256"] == task["renderer_files_sha256"]
        assert set(task["renderer_files_sha256"]) == {
            "blender_worker.py",
            "geometry_nodes.py",
            "journal.py",
        }
        assert (
            task["config"]["threads"] == 4
            and task["config"]["timeout"] == 300
            and task["config"]["encode_timeout"] == 60
        )
        assert (
            task["config"]["claim_path"]
            == "/tmp/opencode/proscenium-native-claims/blender-render.lock"
        )
        assert (terminal["finished_ns"] - journals["process.json"]["started_ns"]) / 1e9 < 300
        profile = journals["native-profile.json"]
        assert profile["version"] == "4.5.14 LTS" and profile["build_hash"] == "62c1db4208e8"
        assert profile["device"] == "CPU" and profile["samples"] == 8 and profile["threads"] == 4
        actual, launch = profile["process"], journals["native-process.json"]
        assert actual["pid"] > 0 and actual["start_ticks"] > 0
        assert actual["pid"] == launch["pid"] and actual["start_ticks"] == launch["start_ticks"]
        mesh = profile["procedural_meshes"][0]
        assert (
            mesh["source_vertices"] == 0
            and mesh["evaluated_vertices"] == 8
            and mesh["evaluated_polygons"] == 6
        )
        assert attempt["observations"]["headroom_min_bytes"] > 4 * 1024**3
    assert len(data["cleanup"]) == 4 and all(not row["alive"] for row in data["cleanup"].values())
    final = load("post-export-verification.json")
    assert all(not row["alive_at_export"] for row in final["actual_native_identities"])
    assert final["resource_claims_after"] == final["generation_reservations_after"] == []


def test_retention_and_reporting_repair_did_not_spend_an_extra_worker():
    data = load("actual-run.json")
    repair = data["reporting_repair"]
    assert repair["first_worker_relaunched"] is False and repair["extra_workers_allocated"] == 0
    root = Path(load("retention.json")["root"])
    historical = (root / "result.json").read_bytes()
    assert sha(historical) == repair["original_report_sha256"]
    assert json.loads(historical)["attempts"] == []
    assert len(data["attempts"]) == 2
    for row in load("retention.json")["files"]:
        path = Path(row["path"])
        assert path.stat().st_size == row["bytes"] and sha(path.read_bytes()) == row["sha256"]
