"""Integrity of retained M04 observations. Not a new cloud/browser/native acceptance run."""

import hashlib
import json
from pathlib import Path

from proscenium.contracts import canonical

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs/evidence/m04/measurement.json"


def load():
    return json.loads(REPORT.read_bytes())


def test_accepted_w05_reference_bytes_and_camera_stay_exact():
    report = load()
    for name, digest in report["accepted_reference_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    camera = json.loads((ROOT / "docs/evidence/w05/camera-selection-dpr1.json").read_bytes())[
        "selection"
    ]["camera"]
    assert (
        hashlib.sha256(canonical(camera)).hexdigest() == report["request"]["camera_rfc8785_sha256"]
    )
    assert report["request"]["live_capture_bytes_equal_accepted_w05"]
    assert report["request"]["model_preview_camera_equal_captured_context"]


def test_odd_png_stills_are_not_mislabelled_as_ready_video():
    report = load()
    assert [row["dimensions"] for row in report["renders"]] == [
        {"width": 1031, "height": 569},
        {"width": 2063, "height": 1138},
    ]
    for row in report["renders"]:
        assert row["state"] == "succeeded" and row["availability"] == "complete"
        assert row["native_exit_code"] == 0 and row["completed_frames"] == 1
        assert row["segments"] == 0
        assert len(row["png_sha256"]) == len(row["input_digest"]) == 64


def test_measured_edges_meet_half_pixel_image_tolerance():
    report = load()
    differences = []
    for render in report["renders"]:
        assert {edge["instance_id"] for edge in render["edge_fits"]} == {
            "instance-blue",
            "instance-renamed",
        }
        for edge in render["edge_fits"]:
            difference = abs(edge["native_offset_pixels"] - edge["inspection_offset_pixels"])
            assert abs(difference - edge["difference_pixels"]) < 1e-12
            assert difference <= report["framing_oracle"]["tolerance_image_pixels"] == 0.5
            assert edge["native_lane_max_abs_pixels"] < 0.5
            differences.append(difference)
    assert max(differences) == report["framing_oracle"]["maximum_correspondence_error_pixels"]


def test_source_and_pin_preserved_without_claiming_unproven_camera_restore():
    report = load()
    assert report["allocation_used"]["cloud_roots"] == 1
    assert report["allocation_used"]["native_children"] == 0
    assert report["allocation_used"]["cpu_keyframe_frames"] == 2
    assert report["source"]["working_hashes_and_index_unchanged"]
    assert report["source"]["original_w03_fixture_unchanged"]
    assert report["source"]["model_checkpoint_or_publication_calls"] == 0
    assert report["annotation"]["image_sha256"] == report["renders"][0]["png_sha256"]
    assert report["annotation"]["unchanged_after_dpr2"]
    assert report["annotation"]["camera"] is None and report["annotation"]["spatial_hits"] == []
    assert report["cleanup"]["sdk_exited"]
    assert report["status"] == "targeted_native_path_pass_milestone_pending"
    assert report["remaining"]
