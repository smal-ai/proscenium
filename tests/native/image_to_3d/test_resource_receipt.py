"""Read retained negative evidence only. Never starts a process or submits inference."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.native
def test_retained_pre_admission_resource_guard_is_not_mesh_success():
    value = os.environ.get("PROSCENIUM_W09_RESOURCE_RECEIPT")
    if not value:
        pytest.skip("Supply the exact retained E04 pre-admission resource receipt")
    path = Path(value)
    report = json.loads(path.read_bytes())
    assert report["allocation"] == "e04-live-a"
    assert report["baseline"]["mem_available_bytes"] < report["fit_threshold_bytes"]
    assert report["fit_threshold_bytes"] == 12 * 1024**3
    assert report["protected_host_headroom_bytes"] == 4 * 1024**3
    assert report["gpu_inference_submitted"] is False
    assert report["instance_plan"]["executed"] is False
    assert report["existing_services_mutated"] is False
    assert report["e04_passed"] is report["m10_passed"] is False
    assert "owned_server" not in report and "job" not in report and "project" not in report
    assert "Immediate" in report["blocker"]
    assert not report["independent_queue_before"]["queue_running"]
    assert not report["independent_queue_before"]["queue_pending"]
    # The A allocation's source is historical evidence, not the newly authorised B harness.
    repository = Path(__file__).resolve().parents[3]
    original = subprocess.check_output(
        [
            "git",
            "-C",
            str(repository),
            "show",
            "97202858b7d032c6d9531b1c747b2aa87ef773d7:tests/native/image_to_3d/proof.py",
        ]
    )
    assert hashlib.sha256(original).hexdigest() == report["harness_sha256"]
