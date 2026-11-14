"""Synthetic receipt validation only. These fixtures claim no actual native pass."""

import copy
import json
import subprocess

import pytest

from tools.operations.acceptance import CHECKOUT, inspect_receipts, report
from tools.operations.applicability import local_git_environment, receipt_digest, source_hash
from tools.operations.safety import RecoveryError, digest


@pytest.fixture
def history(tmp_path):
    def git(*args):
        return (
            subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=tmp_path)
            .decode()
            .strip()
        )

    git("init", "-b", "main")
    git("config", "user.name", "Applicability fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / "app.py").write_text("# synthetic source v1\n")
    git("add", "app.py")
    git("commit", "-m", "Tested synthetic fixture")
    tested = git("rev-parse", "HEAD")
    (tmp_path / "notes.md").write_text("Unrelated documentation revision\n")
    git("add", "notes.md")
    git("commit", "-m", "Later integration fixture")
    final = git("rev-parse", "HEAD")
    original = tmp_path / "original.json"
    original.write_text('{"synthetic":true}\n')
    regression = tmp_path / "regression.json"
    regression.write_text('{"synthetic_regression":true}\n')
    receipt = dict(
        case_id="C15",
        layer="native",
        status="live_pass",
        evidence_type="native_runtime_capture",
        application_commit=tested,
        contract_revision="1.0.0",
        fixture_revision="synthetic-test-only",
        command="synthetic validation fixture, not a native execution",
        runtime_identity={"fixture": True},
        artifacts=[{"path": "original.json", "sha256": digest(original)}],
        limits=[],
    )
    applicability = dict(
        format="proscenium-applicability-1",
        receipt_sha256=receipt_digest(receipt),
        tested_commit=tested,
        accepted_commit=final,
        integration_commit=final,
        reviewer="coordinator",
        decision="applicable",
        reason="Synthetic coordinator decision for validator regression only",
        mode="reviewed_ancestor",
        validation_artifacts=[{"path": "regression.json", "sha256": digest(regression)}],
    )
    return tmp_path, git, receipt, applicability, tested, final


def inspect(history, *, receipt=None, applicability=None):
    root, _, original, review, _, final = history
    value = {"receipts": [receipt or original], "applicability": [applicability or review]}
    path = root / "receipts.json"
    path.write_text(json.dumps(value))
    return inspect_receipts(root, path, final)


def test_explicit_ancestor_validation_keeps_exact_tested_revision(history):
    result = inspect(history)
    assert result["receipts"][0]["application_commit"] == history[4]
    assert result["validated_applicability"][0]["integration_commit"] == history[5]
    assert (
        result["validated_applicability"][0]["meaning"]
        == "coordinator applicability, not fresh execution"
    )


def test_git_identity_queries_cannot_lazy_fetch_or_prompt_for_credentials():
    environment = local_git_environment()
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"


def test_source_hash_mode_checks_both_committed_revisions(history):
    root, _, _, review, tested, _ = history
    review["mode"] = "source_hashes"
    review["source_hashes"] = {"app.py": source_hash(root, tested, "app.py")}
    assert inspect(history)["validated_applicability"][0]["mode"] == "source_hashes"
    review["source_hashes"]["app.py"] = "0" * 64
    with pytest.raises(RecoveryError, match="source_hash_mismatch"):
        inspect(history)


def test_changed_source_is_not_implicitly_equivalent(history):
    root, git, _, review, tested, _ = history
    (root / "app.py").write_text("# changed source\n")
    git("add", "app.py")
    git("commit", "-m", "Changed synthetic implementation")
    final = git("rev-parse", "HEAD")
    review.update(
        mode="source_hashes",
        integration_commit=final,
        source_hashes={"app.py": source_hash(root, tested, "app.py")},
    )
    updated = (*history[:5], final)
    with pytest.raises(RecoveryError, match="source_hash_mismatch"):
        inspect(updated)


def test_ancestry_alone_and_merely_setting_live_pass_are_rejected(history):
    root, _, receipt, _, _, final = history
    path = root / "no-review.json"
    path.write_text(json.dumps({"receipts": [receipt]}))
    with pytest.raises(RecoveryError, match="explicit_applicability"):
        inspect_receipts(root, path, final)
    changed = {**receipt, "command": "Different purported live pass"}
    with pytest.raises(RecoveryError, match="explicit_applicability"):
        inspect(history, receipt=changed)
    blocked = {**receipt, "status": "blocked"}
    blocked_review = {**history[3], "receipt_sha256": receipt_digest(blocked)}
    with pytest.raises(RecoveryError, match="explicit_applicability"):
        inspect(history, receipt={**blocked, "status": "live_pass"}, applicability=blocked_review)


def test_unknown_and_unrelated_commits_are_rejected(history):
    root, git, receipt, review, _, _ = history
    unknown = {**receipt, "application_commit": "f" * 40}
    changed = {**review, "tested_commit": "f" * 40, "receipt_sha256": receipt_digest(unknown)}
    with pytest.raises(RecoveryError, match="unknown_evidence_commit"):
        inspect(history, receipt=unknown, applicability=changed)
    unrelated = git("commit-tree", "HEAD^{tree}", "-m", "Unrelated synthetic root")
    changed_receipt = {**receipt, "application_commit": unrelated}
    changed_review = {
        **review,
        "tested_commit": unrelated,
        "receipt_sha256": receipt_digest(changed_receipt),
    }
    with pytest.raises(RecoveryError, match="unrelated_applicability_commit"):
        inspect(history, receipt=changed_receipt, applicability=changed_review)


def test_regression_hash_mismatch_and_limits_override_are_rejected(history):
    review = copy.deepcopy(history[3])
    review["validation_artifacts"][0]["sha256"] = "0" * 64
    with pytest.raises(RecoveryError, match="artifact_hash_mismatch"):
        inspect(history, applicability=review)
    review = {**history[3], "limits_override": []}
    with pytest.raises(RecoveryError, match="record_fields"):
        inspect(history, applicability=review)
    with pytest.raises(RecoveryError, match="coordinator_validation"):
        inspect(history, applicability={**history[3], "reviewer": "model_claim"})
    with pytest.raises(RecoveryError, match="requires_regression_artifacts"):
        inspect(history, applicability={**history[3], "validation_artifacts": []})


def test_applicability_never_clears_unproven_limits_or_m10(history):
    receipt = {**history[2], "limits": ["Actual native relocation remains unproved"]}
    review = {**history[3], "receipt_sha256": receipt_digest(receipt)}
    result = inspect(history, receipt=receipt, applicability=review)
    assert result["receipts"][0]["limits"] == receipt["limits"]
    mapping = json.loads((CHECKOUT / "tests/acceptance/coverage.json").read_text())
    outcome = report(CHECKOUT, mapping, {}, result)
    assert outcome["cases"][14]["evidence"]["native"] == "incomplete"
    assert not outcome["release_ready"]
    assert any(gap.startswith("M10:blocked") for gap in outcome["gaps"])
