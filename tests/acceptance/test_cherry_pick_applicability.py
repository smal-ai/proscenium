"""Synthetic Git histories exercise evidence transfer, not native product execution."""

import copy
import json
import subprocess

import pytest

from tools.operations.acceptance import inspect_receipts
from tools.operations.applicability import patch_hash, receipt_digest
from tools.operations.safety import RecoveryError, digest


@pytest.fixture
def transferred(tmp_path):
    def git(*args):
        return (
            subprocess.check_output(
                ["git", "-c", "core.hooksPath=/dev/null", *args],
                cwd=tmp_path,
                stderr=subprocess.PIPE,
            )
            .decode()
            .strip()
        )

    def commit(message):
        git("add", ".")
        git("commit", "-qm", message)
        return git("rev-parse", "HEAD")

    git("init", "-qb", "main")
    git("config", "user.name", "Synthetic applicability fixture")
    git("config", "user.email", "fixture@example.invalid")
    source = tmp_path / "app.py"
    source.write_text("# original header\n" * 10 + "VALUE = 1\n" + "# original footer\n" * 10)
    base = commit("Common source")
    git("checkout", "-qb", "tested")
    source.write_text(source.read_text().replace("VALUE = 1", "VALUE = 2"))
    first = commit("Tested source edit")
    (tmp_path / "proof.txt").write_text("Synthetic proof driver\n")
    (tmp_path / "fixture.bin").write_bytes(bytes(range(256)))
    tested = commit("Tested proof inputs")
    git("checkout", "-q", "main")
    source.write_text("# independent earlier integration\n\n" + source.read_text())
    commit("Unrelated integration changes hunk coordinates")
    git("cherry-pick", first)
    integrated_first = git("rev-parse", "HEAD")
    git("cherry-pick", tested)
    accepted = git("rev-parse", "HEAD")
    (tmp_path / "release.md").write_text("Synthetic later documentation\n")
    final = commit("Later integration")
    original, regression = tmp_path / "original.json", tmp_path / "regression.json"
    original.write_text('{"synthetic_fixture":true}\n')
    regression.write_text('{"synthetic_regression":true}\n')
    receipt = {
        "case_id": "C02",
        "layer": "native",
        "status": "live_pass",
        "evidence_type": "native_runtime_capture",
        "application_commit": tested,
        "contract_revision": "1.0.0",
        "fixture_revision": "synthetic-validator-only",
        "command": "synthetic validator fixture, no native runtime",
        "runtime_identity": {"synthetic": True},
        "artifacts": [{"path": "original.json", "sha256": digest(original)}],
        "limits": ["This is synthetic validator input, never product acceptance"],
    }
    review = {
        "format": "proscenium-applicability-1",
        "receipt_sha256": receipt_digest(receipt),
        "tested_commit": tested,
        "accepted_commit": accepted,
        "integration_commit": final,
        "reviewer": "coordinator",
        "decision": "applicable",
        "reason": "Synthetic exact-edit transfer with distinct regression evidence",
        "mode": "reviewed_cherry_pick",
        "common_base": base,
        "patches": [
            {
                "tested_commit": first,
                "integrated_commit": integrated_first,
                "patch_sha256": patch_hash(tmp_path, first),
            },
            {
                "tested_commit": tested,
                "integrated_commit": accepted,
                "patch_sha256": patch_hash(tmp_path, tested),
            },
        ],
        "validation_artifacts": [{"path": "regression.json", "sha256": digest(regression)}],
    }
    return tmp_path, git, commit, receipt, review


def inspect(transferred, *, review=None, receipt=None):
    root, _, _, original, original_review = transferred
    selected = review or original_review
    path = root / "receipts.json"
    path.write_text(json.dumps({"receipts": [receipt or original], "applicability": [selected]}))
    return inspect_receipts(root, path, selected["integration_commit"])


def test_exact_transferred_edits_keep_original_revision_and_limits(transferred):
    root, git, _, receipt, review = transferred
    assert (
        git("merge-base", receipt["application_commit"], review["integration_commit"])
        == review["common_base"]
    )
    for pair in review["patches"]:
        assert patch_hash(root, pair["tested_commit"]) == patch_hash(
            root, pair["integrated_commit"]
        )
    result = inspect(transferred)
    assert result["receipts"] == [receipt]
    assert result["receipts"][0]["limits"]
    assert result["validated_applicability"][0]["mode"] == "reviewed_cherry_pick"


@pytest.mark.parametrize(
    "bad", ["missing", "reversed", "duplicate_target", "wrong_tested", "wrong_hash"]
)
def test_incomplete_or_mismatched_transfer_mapping_is_rejected(transferred, bad):
    review = copy.deepcopy(transferred[4])
    if bad == "missing":
        review["patches"].pop()
    elif bad == "reversed":
        review["patches"].reverse()
    elif bad == "duplicate_target":
        review["patches"][1]["integrated_commit"] = review["patches"][0]["integrated_commit"]
    elif bad == "wrong_tested":
        review["patches"][0]["tested_commit"] = review["tested_commit"]
    else:
        review["patches"][0]["patch_sha256"] = "0" * 64
    with pytest.raises(RecoveryError, match="patch_mapping|patch_lineage|patch_hash"):
        inspect(transferred, review=review)


def test_changed_whitespace_cannot_use_git_patch_id_equivalence(transferred):
    root, git, _, _, original_review = transferred
    review = copy.deepcopy(original_review)
    git("checkout", "-qb", "wrong-transfer", review["common_base"])
    source = root / "app.py"
    source.write_text(source.read_text().replace("VALUE = 1", "VALUE  = 2"))
    git("add", "app.py")
    git("commit", "-qm", "Different exact edit")
    wrong_first = git("rev-parse", "HEAD")
    assert patch_hash(root, wrong_first) != review["patches"][0]["patch_sha256"]
    git("cherry-pick", review["tested_commit"])
    wrong_final = git("rev-parse", "HEAD")
    review.update(accepted_commit=wrong_final, integration_commit=wrong_final)
    review["patches"][0]["integrated_commit"] = wrong_first
    review["patches"][1]["integrated_commit"] = wrong_final
    with pytest.raises(RecoveryError, match="patch_hash_mismatch"):
        inspect(transferred, review=review)


def test_divergent_mode_requires_common_history_and_accepted_targets(transferred):
    _, git, _, receipt, original_review = transferred
    review = copy.deepcopy(original_review)
    unrelated = git("commit-tree", "HEAD^{tree}", "-m", "Unrelated root")
    changed = {**receipt, "application_commit": unrelated}
    review.update(tested_commit=unrelated, receipt_sha256=receipt_digest(changed))
    with pytest.raises(RecoveryError, match="unrelated_applicability_commit"):
        inspect(transferred, review=review, receipt=changed)
    review = copy.deepcopy(original_review)
    review["patches"][1]["integrated_commit"] = receipt["application_commit"]
    with pytest.raises(RecoveryError, match="patch_lineage"):
        inspect(transferred, review=review)


@pytest.mark.parametrize("bad", ["limits_override", "source_hashes", "wrong_mode", "no_regression"])
def test_cherry_pick_mode_retains_existing_review_guards(transferred, bad):
    review = copy.deepcopy(transferred[4])
    if bad == "wrong_mode":
        review["mode"] = "reviewed_ancestor"
    elif bad == "no_regression":
        review["validation_artifacts"] = []
    else:
        review[bad] = [] if bad == "limits_override" else {"app.py": "0" * 64}
    with pytest.raises(
        RecoveryError, match="record_fields|applicability_mode|regression_artifacts"
    ):
        inspect(transferred, review=review)
