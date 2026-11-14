import json
import subprocess

import pytest

from tools.operations.acceptance import (
    CHECKOUT,
    inspect_receipts,
    junit_outcomes,
    report,
    resolve_map,
)
from tools.operations.safety import RecoveryError


def mapping():
    return json.loads((CHECKOUT / "tests/acceptance/coverage.json").read_text())


def test_all_31_scenarios_resolve_real_functions_without_claiming_execution():
    coverage = mapping()
    resolve_map(CHECKOUT, coverage)
    result = report(CHECKOUT, coverage, {}, {"m10": {"status": "blocked_no_owner_scope_decision"}})
    assert len(result["cases"]) == 31
    assert not result["release_ready"]
    assert all(
        state == "unexecuted" for case in result["cases"] for state in case["evidence"].values()
    )
    assert any(gap.startswith("M10:blocked") for gap in result["gaps"])


def test_invalid_coverage_or_missing_function_is_not_accepted():
    coverage = mapping()
    coverage["cases"][0]["tests"] = ["tests/acceptance/test_evidence.py::test_does_not_exist"]
    with pytest.raises(RecoveryError, match="test_function_missing"):
        resolve_map(CHECKOUT, coverage)
    coverage = mapping()
    coverage["cases"][-1]["id"] = "C01"
    with pytest.raises(RecoveryError, match="exactly_once"):
        resolve_map(CHECKOUT, coverage)


def test_junit_pass_skips_and_failures_are_offline_only(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text("""<testsuite>
      <testcase classname="tests.integration.copilot.test_delivery" name="test_native_events_before_ack_and_duplicate_user_echo"/>
      <testcase classname="tests.integration.copilot.test_delivery" name="test_lost_ack_closes_gate_and_never_blind_resubmits"><skipped/></testcase>
      <testcase classname="tests.integration.copilot.test_delivery" name="test_stop_pause_fence_late_ack_and_no_retry[true]"><failure/></testcase>
    </testsuite>""")
    result = report(CHECKOUT, mapping(), junit_outcomes([path]), {})
    assert result["cases"][0]["evidence"] == {
        "offline": "synthetic_verified",
        "native": "unexecuted",
    }
    assert result["cases"][1]["evidence"]["offline"] == "unexecuted"
    assert result["cases"][3]["evidence"]["offline"] == "failed"
    assert not result["release_ready"]


def test_synthetic_native_pass_and_implicit_m10_removal_refused(tmp_path):
    receipt = dict(
        case_id="C15",
        layer="native",
        status="live_pass",
        evidence_type="offline_fixture",
        application_commit="a" * 40,
        contract_revision="1.0.0",
        fixture_revision="synthetic",
        command="pytest tests/acceptance",
        runtime_identity="fixture",
        artifacts=[],
        limits=[],
    )
    path = tmp_path / "receipts.json"
    path.write_text(json.dumps({"receipts": [receipt]}))
    with pytest.raises(RecoveryError, match="fixture_or_transport_double"):
        inspect_receipts(CHECKOUT, path, "a" * 40)
    path.write_text(json.dumps({"receipts": [], "m10": {"status": "owner_scope_change"}}))
    with pytest.raises(RecoveryError, match="explicit_owner_scope_decision"):
        inspect_receipts(CHECKOUT, path, "a" * 40)
    data = {"m10": {"status": "failed"}}
    result = report(CHECKOUT, mapping(), {}, data)
    assert any("M10:blocked" in gap for gap in result["gaps"])


def test_receipt_hashes_and_exact_application_revision_required(tmp_path):
    artifact = "docs/implementation/runtime-lock.json"
    receipt = dict(
        case_id="C15",
        layer="native",
        status="live_pass",
        evidence_type="native_runtime_capture",
        application_commit="a" * 40,
        contract_revision="1.0.0",
        fixture_revision="fixture-revision",
        command="bounded native proof",
        runtime_identity={"sdk": "1.0.34"},
        artifacts=[{"path": artifact, "sha256": "0" * 64}],
        limits=[],
    )
    path = tmp_path / "receipts.json"
    path.write_text(json.dumps({"receipts": [receipt]}))
    with pytest.raises(RecoveryError, match="evidence_revision_mismatch"):
        inspect_receipts(CHECKOUT, path, "b" * 40)
    with pytest.raises(RecoveryError, match="hash_mismatch"):
        inspect_receipts(CHECKOUT, path, "a" * 40)
    # Even all offline functions passing cannot satisfy native/browser/M10 gates.
    outcomes = {
        node: ["synthetic_verified"] for case in mapping()["cases"] for node in case["tests"]
    }
    result = report(CHECKOUT, mapping(), outcomes, {})
    assert not result["release_ready"]
    assert any("browser:brave:unexecuted" in gap for gap in result["gaps"])


def test_release_cli_refuses_incomplete_evidence():
    result = subprocess.run(
        ["uv", "run", "python", "-m", "tools.operations.acceptance", "--release"],
        cwd=CHECKOUT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stderr
    assert '"map_resolved": true' in result.stdout
    assert '"release_ready": false' in result.stdout
