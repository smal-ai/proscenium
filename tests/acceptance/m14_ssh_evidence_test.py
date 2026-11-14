"""Honest retained failure classification. Does not launch a new allocation."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = json.loads((ROOT / "docs/evidence/m14-ssh/result.json").read_text())


def test_unexercised_journeys_are_not_promoted_to_acceptance():
    assert REPORT["status"] == "blocked_harness_before_transport_or_sdk"
    assert not REPORT["journey_pass"] and not REPORT["cumulative_m12_m14_acceptance"]
    observed = REPORT["observations"]
    assert observed["temporary_sshd_processes_started"] == 1
    assert observed["forwarding_clients_started"] == observed["browser_profiles_started"] == 0
    assert (
        observed["sdk_sends"]
        == observed["native_session_associations"]
        == observed["requests"]
        == 0
    )
    assert not observed["host_lifespan_audits_present"]
    assert len(REPORT["remaining_gaps"]) == 5


def test_original_preservation_and_cleanup_limits_are_explicit():
    assert REPORT["observations"]["original_m13_byte_preserved"]
    assert REPORT["cleanup"]["owned_descendants_remaining"] == 0
    assert REPORT["cleanup"]["host_1"]["exit_code"] == -15
    assert REPORT["cleanup"]["host_1_numeric_pid_start_receipt"].startswith("missing")
    assert not REPORT["cleanup"]["second_live_allocation_started"]
    assert REPORT["preflight"]["video_bytes"] == 64876


def test_executed_hashes_bind_original_driver_not_corrected_head():
    revision = REPORT["executed_driver_revision"]
    for path, digest in REPORT["executed_source_hashes"].items():
        data = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT)
        assert hashlib.sha256(data).hexdigest() == digest
