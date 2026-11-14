"""Immediate failed-guard receipt checks. No process/model/inference calls."""

import json
import runpy
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
EVIDENCE = REPO / "docs/evidence/m07-lightweight"


def test_actual_first_guard_failed_and_no_positive_stage_is_claimed():
    report = json.loads((EVIDENCE / "execution-admission.json").read_bytes())
    assert report["status"] == "BLOCKED_PRE_START_HOST_FIT"
    assert report["observed_mem_available_bytes"] == 13_516_251_136
    assert report["required_fit_bytes"] == 13_804_495_920
    assert report["shortfall_bytes"] == 288_244_784
    for field in (
        "own_comfy_started",
        "server_deadline_started",
        "checkpoint_or_profile_changed",
        "gpu_claim_acquired",
        "extra_wait_or_resample",
        "milestone_complete",
    ):
        assert report[field] is False
    for field in (
        "inference_posts",
        "actual_image_inferences",
        "native_cloud_roots",
        "native_children",
        "generation_jobs_admitted",
        "owner_selections",
        "owner_permission_decisions",
    ):
        assert report[field] == 0


def test_recorder_is_write_once_and_has_no_positive_authority(tmp_path):
    code = runpy.run_path(
        str(REPO / "tests/native/providers/m07_light_admission.py"),
        run_name="offline_guard_recorder",
    )
    function = code["record_blocker"]
    function.__globals__["RUN"] = tmp_path / "isolated-run"
    with pytest.raises(ValueError):
        function(code["FIT_BYTES"])
    assert not (tmp_path / "isolated-run").exists()
    function(1)  # Explicitly synthetic guard-recorder unit input, not a hardware observation.
    before = (tmp_path / "isolated-run/admission.json").read_bytes()
    with pytest.raises(FileExistsError):
        function(2)
    assert (tmp_path / "isolated-run/admission.json").read_bytes() == before


def test_original_klein_remains_unchanged_and_no_runtime_subdirectories_exist():
    preservation = json.loads((EVIDENCE / "execution-original-preservation.json").read_bytes())
    assert preservation["job_matches_original_report"]
    assert preservation["inputs_match_original_report"]
    assert preservation["database_matches_preparation_anchor"]
    assert preservation["database_unchanged_by_read"]
    assert preservation["original_job"]["state"] == "planned"
    assert preservation["reservations"] == [
        ["w07-live-initial", "w07-live-task", "w07-live-task", "reserved"]
    ]
    assert preservation["actual_run_entries"] == ["admission.json"]
