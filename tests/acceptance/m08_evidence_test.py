"""Integrity of retained M08/M12 measurements. Never starts another native journey."""

import json
from pathlib import Path

from proscenium.contracts import validate
from proscenium.sources.resolver import closure_digest

REPORT = Path(__file__).resolve().parents[2] / "docs/evidence/m08/final-run.json"


def load():
    return json.loads(REPORT.read_bytes())


def test_actual_allocation_and_no_unqualified_milestone_claim():
    report = load()
    assert report["allocation"] == {
        "cloud_roots": 3,
        "native_children": 0,
        "authoring_processes": 1,
        "cpu_frames": 17,
        "proxy_exports": 1,
        "held_probe_rendered": False,
    }
    assert report["status"].endswith("dependencies_pending")
    assert report["runtime"]["model"] == "gpt-6.1-sol"
    assert report["runtime"]["samples"] == 8 and report["runtime"]["threads"] == 4
    assert set(report["m08_criteria"]) == {str(i) for i in range(1, 10)}
    assert len(report["limits"]) >= 8


def test_annotation_and_playback_strictly_precede_actual_last_frame():
    report = load()
    progressive = report["progressive"]
    assert (
        progressive["annotation_host_ns"]
        < progressive["first_segment_played_host_ns"]
        < progressive["last_frame_ready_ns"]
    )
    assert progressive["annotation_margin_seconds"] > 4
    assert progressive["playback_margin_seconds"] > 4
    assert {1, 2, 3, 4} <= set(progressive["rvfc_order_with_intentional_pause_replay"])
    annotation = progressive["annotation"]
    assert annotation["target"]["source_frame"] == 3
    assert annotation["range"] == {"start": 1, "end": 4}
    frame = next(
        row
        for row in report["frame_receipts"]
        if row["job_id"] == progressive["job"]["job_id"] and row["source_frame"] == 3
    )
    assert frame["sha256"] == annotation["target"]["displayed_sha256"]
    assert frame["artifact_id"] == annotation["target"]["artifact_id"]


def test_receipts_and_final_map_keep_same_original_source_identity():
    report = load()
    for row in report["frame_receipts"]:
        validate("FrameReceipt", row)
    for row in report["segment_receipts"]:
        validate("PreviewSegment", row)
    movie = report["final_movie"]
    validate("FrameMap", movie["frame_map"])
    assert [row["sequence_frame"] for row in movie["frame_map"]["frames"]] == list(range(1, 13))
    for row in movie["frame_map"]["frames"]:
        assert row["contributors"] == [
            {
                "job_id": report["progressive"]["job"]["job_id"],
                "shot_id": "m08-motion",
                "version_id": "m08-scene-v1",
                "source_frame": row["sequence_frame"],
                "weight": {"numerator": 1, "denominator": 1},
            }
        ]
    stream = movie["verified_stream"]
    assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
    assert stream["nb_read_frames"] == "12" and stream["avg_frame_rate"] == "24/1"
    assert [stream["width"], stream["height"]] == [640, 360]
    assert not movie["actual_browser_readback"]["gate_open"]
    assert not movie["actual_browser_readback"]["sdk_object_created"]


def test_same_attempt_produces_later_frames_after_backend_sigkill():
    report = load()
    recovery = report["recovery"]
    assert recovery["backend_kill"]["signal"] == "SIGKILL"
    assert recovery["same_attempt_input"] and recovery["closed_gate"] and recovery["no_sdk_object"]
    assert recovery["old_sdk_absent"] and recovery["same_contexts_publications_versions"]
    assert recovery["postkill_frame_ready_seconds"][0] < 0
    assert all(value > 0 for value in recovery["postkill_frame_ready_seconds"][1:])
    assert recovery["native_exit_code"] == 0
    assert recovery["held_job"]["state"] == "cancelled"
    assert recovery["held_job"].get("native_exit_code") is None
    assert recovery["held_job"]["completed_frames"] == 0


def test_live_texture_does_not_change_frozen_native_inputs():
    source = load()["source"]
    closure = source["version"]["source"]
    assert closure["digest"] == closure_digest(closure)
    texture = next(
        row for row in closure["files"] if row["destination"] == "assets/cube/colour.png"
    )
    assert texture["origin"]["sha256"] != source["working_live_texture_sha256"]
    assert source["probe_pixels_equal_original_main_frames_1_4"] == [True] * 4


def test_actual_shot_cards_have_authored_bindings_not_placeholders():
    report = load()
    cards = report["actual_shot_cards"]["text"]
    assert "m08-opening" in cards and "m08-motion" in cards
    assert "m08-scene / m08-scene-v1" in cards and "24/1" in cards
    assert "Unbound" not in cards and "Not authored" not in cards
    assert report["authoring_process"]["native_exit_code"] == 0
