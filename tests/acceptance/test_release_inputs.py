"""Pure evidence assembly checks. No application/browser/SDK/native execution."""

import copy
import json
from pathlib import Path

import pytest

from tools.operations import release_inputs as release
from tools.operations.acceptance import inspect_receipts, report
from tools.operations.applicability import receipt_digest
from tools.operations.safety import RecoveryError

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = "722dce5e7631454ac116819d41ed18837ea160f1"
RECIPE = ROOT / "docs/evidence/release/recipe.json"


@pytest.fixture(scope="module")
def assembled():
    return release.build(ROOT, json.loads(RECIPE.read_text()), SNAPSHOT, SNAPSHOT)


def test_complete_case_invariant_and_required_layer_inventory(assembled):
    index = assembled["index.json"]
    mapping = json.loads(release.blob(ROOT, SNAPSHOT, "tests/acceptance/coverage.json"))
    scenarios = json.loads(release.blob(ROOT, SNAPSHOT, "docs/acceptance/scenarios.json"))
    assert len(index["cases"]) == 31
    assert {j["id"] for j in index["journeys"]} == {
        *(f"M{i:02}" for i in range(1, 15)),
        "SSH",
        "PERFORMANCE",
    }
    for case, declared, scenario in zip(
        index["cases"], mapping["cases"], scenarios["scenarios"], strict=True
    ):
        assert case["id"] == declared["id"] == scenario["id"]
        assert case["required_evidence"] == declared["required_evidence"]
        assert case["tests"] == declared["tests"]
        assert [i["requirement"] for i in case["invariants"]] == scenario["then"]
        assert case["observation"] and case["sources"]
        for field in ("native_invariants", "browser_invariants"):
            assert set(case[field]) <= set(range(1, len(scenario["then"]) + 1))


def test_historical_identities_inactive_receipts_and_explicit_real_gaps(assembled):
    draft = assembled["receipts.draft.json"]
    sources = {s["id"]: s for s in assembled["index.json"]["sources"]}
    assert draft["active"] is False and not draft["applicability"]
    for proposal in draft["proposals"] + draft["journey_proposals"]:
        receipt = proposal["receipt"]
        assert receipt["status"] == "planned" and receipt["limits"]
        assert (
            receipt["application_commit"]
            == sources[receipt["runtime_identity"]["source_id"]]["application_commit"]
        )
        assert receipt["application_commit"] != draft["candidate"]
        assert proposal["receipt_sha256"] == receipt_digest(receipt)
    gaps = assembled["gaps.json"]
    assert {g["id"] for g in gaps["required_remaining"]} == {f"G{i:02}" for i in range(1, 7)}
    assert not assembled["report.partial.json"]["release_ready"]
    assert gaps["validator_assembly_gaps"]
    assert len(gaps["validator_assembly_gaps"]) > len(gaps["required_remaining"])


def test_all_indexed_artifacts_are_original_committed_bytes_and_failures_retained(assembled):
    index = assembled["index.json"]
    for artifact in index["input_artifacts"]:
        data = release.blob(ROOT, SNAPSHOT, artifact["path"])
        assert release.sha(data) == artifact["sha256"] and len(data) == artifact["bytes"]
    assert (
        len(index["portable_retained_artifacts"]) == 24
    )  # 16 actual M13 outputs + 8 inert SSH sources.
    assert any(
        "m12-selective-ack/result.json" in p
        for f in index["historical_failures"]
        for p in f["paths"]
    )
    assert any(
        "m07-review/acceptance.xml" in p for f in index["historical_failures"] for p in f["paths"]
    )


def test_qualification_boundaries_and_blocked_milestones(assembled):
    index = assembled["index.json"]
    sources = {s["id"]: s for s in index["sources"]}
    assert "Executor OFF" in " ".join(sources["m07-budget"]["qualifications"])
    assert "NOT disk exhaustion" in " ".join(sources["partial"]["qualifications"])
    assert "not a cold provider restart" in " ".join(sources["m07"]["qualifications"])
    assert "BEFORE cut" in " ".join(sources["m12-ack"]["qualifications"])
    assert "controlled API states" in " ".join(sources["browser-matrix"]["qualifications"])
    assert "uncommitted" in sources["m04"]["identity_meaning"]
    for journey in index["journeys"]:
        if journey["id"] in {"M07", "M10", "M11", "M14"}:
            assert journey["state"].startswith("blocked_")
        if journey["id"] == "M13":
            assert "dependency_acceptance_pending" in journey["state"]


def test_rebuild_is_deterministic_and_does_not_inspect_original_fleet_objects(
    monkeypatch, assembled
):
    original_git = release.git
    allowed = {
        SNAPSHOT,
        "a46b510af2f1a257a59e3b56a6359dac882a532f",
        "3d3fc64b0c47d64edc01cf67e2e08f77b53eda98",
    }

    def guarded(root, *args):
        if args[0] == "ls-tree":
            assert args[1] in allowed
        return original_git(root, *args)

    monkeypatch.setattr(release, "git", guarded)
    rebuilt = release.build(ROOT, json.loads(RECIPE.read_text()), SNAPSHOT, SNAPSHOT)
    assert json.dumps(rebuilt, sort_keys=True) == json.dumps(assembled, sort_keys=True)


def test_rejects_uncommitted_paths_missing_cases_and_tampered_retained_bytes(monkeypatch):
    with pytest.raises(RecoveryError, match="not_committed_regular_file"):
        release.blob(ROOT, SNAPSHOT, "docs/evidence/release/recipe.json")
    recipe = json.loads(RECIPE.read_text())
    incomplete = copy.deepcopy(recipe)
    incomplete["cases"].pop()
    with pytest.raises(RecoveryError, match="ordered_C01_C31"):
        release.build(ROOT, incomplete, SNAPSHOT, SNAPSHOT)
    original = release.blob

    def tampered(root, snapshot, path):
        raw = original(root, snapshot, path)
        return raw + b"tampered" if path.startswith("docs/evidence/m13/retained/") else raw

    monkeypatch.setattr(release, "blob", tampered)
    with pytest.raises(RecoveryError, match="m13_retained_hash_mismatch"):
        release.build(ROOT, recipe, SNAPSHOT, SNAPSHOT)


def test_partial_report_never_converts_junit_to_native_pass(assembled):
    partial = assembled["report.partial.json"]
    assert partial["m10"] == inspect_receipts(ROOT, None, None)["m10"]
    for case in partial["cases"]:
        for layer, state in case["evidence"].items():
            if layer != "offline":
                assert state == "unexecuted"
    mapping = json.loads(release.blob(ROOT, SNAPSHOT, "tests/acceptance/coverage.json"))
    assert report(ROOT, mapping, {}, {})["release_ready"] is False


def test_explicit_pairing_calculates_only_supplied_edits_without_granting_review(monkeypatch):
    calls = []
    monkeypatch.setattr(
        release, "patch_hash", lambda root, commit: calls.append(commit) or "a" * 64
    )
    monkeypatch.setattr(release, "known_commit", lambda *args: None)
    monkeypatch.setattr(
        release,
        "validate_cherry_picks",
        lambda root, tested, accepted, record: calls.append(record["patches"]),
    )
    monkeypatch.setattr(release, "git", lambda *args: type("Result", (), {"returncode": 0})())
    monkeypatch.setattr(release, "blob", lambda *args: b"distinct regression")
    receipt = {
        "application_commit": "1" * 40,
        "artifacts": [{"path": "original.json", "sha256": "0" * 64}],
    }
    pairing = {
        "accepted_commit": "2" * 40,
        "common_base": "3" * 40,
        "reason": "Explicit supplied source-identity recipe, not automatic equivalence",
        "patches": [{"tested_commit": "1" * 40, "integrated_commit": "2" * 40}],
        "validation_artifacts": [
            {"path": "regression.xml", "sha256": release.sha(b"distinct regression")}
        ],
    }
    proposal = release.cherry_pick_proposal(ROOT, receipt, "4" * 40, pairing)
    assert calls[0] == "1" * 40 and len(calls) == 2
    assert proposal["reviewer"] is None and proposal["decision"] == "pending"
    assert proposal["receipt_sha256"] == receipt_digest(receipt)
    assert receipt["application_commit"] == "1" * 40
    pairing["validation_artifacts"] = receipt["artifacts"]
    with pytest.raises(RecoveryError, match="distinct_regression"):
        release.cherry_pick_proposal(ROOT, receipt, "4" * 40, pairing)
