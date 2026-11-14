"""Explicit coordinator applicability, never inferred equivalence or new execution."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from .safety import RecoveryError, digest, relative, safe_file, secret_path


def receipt_digest(receipt: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def local_git_environment() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null", *args],
        cwd=root,
        capture_output=True,
        timeout=10,
        env=local_git_environment(),
    )


def known_commit(root: Path, commit: str) -> None:
    if (
        not isinstance(commit, str)
        or not re.fullmatch("[0-9a-f]{40}", commit)
        or git(root, "cat-file", "-t", commit).stdout != b"commit\n"
    ):
        raise RecoveryError("unknown_evidence_commit")


def source_hash(root: Path, commit: str, name: str) -> str:
    relative(name)
    if secret_path(name) or ".proscenium" in Path(name).parts:
        raise RecoveryError("excluded_applicability_source")
    tree = git(root, "ls-tree", commit, "--", name)
    if tree.returncode or not tree.stdout.startswith((b"100644 blob ", b"100755 blob ")):
        raise RecoveryError("applicability_source_not_regular_blob")
    # Hash committed bytes, not another worktree or uncommitted integration changes.
    blob = tree.stdout.split()[2].decode("ascii")
    with subprocess.Popen(
        ["git", "--no-replace-objects", "cat-file", "blob", blob],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=local_git_environment(),
    ) as process:
        assert process.stdout is not None
        hasher = hashlib.sha256()
        while block := process.stdout.read(1024 * 1024):
            hasher.update(block)
        result = hasher.hexdigest()
        if process.wait(timeout=10):
            raise RecoveryError("applicability_source_unavailable")
    return result


def validate_applicability(
    root: Path, receipt: dict, integration_commit: str, records: list[dict]
) -> dict:
    fingerprint = receipt_digest(receipt)
    matches = [
        record
        for record in records
        if record.get("receipt_sha256") == fingerprint
        and record.get("integration_commit") == integration_commit
    ]
    if len(matches) != 1:
        raise RecoveryError("evidence_revision_mismatch_requires_explicit_applicability")
    record = matches[0]
    required = {
        "format",
        "receipt_sha256",
        "tested_commit",
        "accepted_commit",
        "integration_commit",
        "reviewer",
        "decision",
        "reason",
        "mode",
        "validation_artifacts",
    }
    if set(record) - (required | {"source_hashes"}) or not required <= record.keys():
        raise RecoveryError("invalid_applicability_record_fields")
    if (
        record["format"] != "proscenium-applicability-1"
        or record["reviewer"] != "coordinator"
        or record["decision"] != "applicable"
        or not isinstance(record["reason"], str)
        or not record["reason"].strip()
    ):
        raise RecoveryError("explicit_coordinator_validation_required")
    tested = receipt["application_commit"]
    if record["tested_commit"] != tested:
        raise RecoveryError("applicability_tested_identity_mismatch")
    known_commit(root, tested)
    known_commit(root, integration_commit)
    known_commit(root, record["accepted_commit"])
    if git(root, "merge-base", "--is-ancestor", tested, integration_commit).returncode:
        raise RecoveryError("unrelated_applicability_commit")
    if (
        git(root, "merge-base", "--is-ancestor", tested, record["accepted_commit"]).returncode
        or git(
            root, "merge-base", "--is-ancestor", record["accepted_commit"], integration_commit
        ).returncode
    ):
        raise RecoveryError("unrelated_applicability_acceptance")
    artifacts = record["validation_artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise RecoveryError("applicability_requires_regression_artifacts")
    original = {(artifact["path"], artifact["sha256"]) for artifact in receipt["artifacts"]}
    if not any((artifact["path"], artifact["sha256"]) not in original for artifact in artifacts):
        raise RecoveryError("applicability_requires_distinct_regression_evidence")
    for artifact in artifacts:
        if digest(safe_file(root, artifact["path"])) != artifact["sha256"]:
            raise RecoveryError("applicability_artifact_hash_mismatch")
    if record["mode"] == "source_hashes":
        hashes = record.get("source_hashes")
        if not isinstance(hashes, dict) or not hashes:
            raise RecoveryError("applicability_requires_tested_source_hashes")
        for name, expected in hashes.items():
            if (
                source_hash(root, tested, name) != expected
                or source_hash(root, integration_commit, name) != expected
            ):
                raise RecoveryError("applicability_source_hash_mismatch")
    elif record["mode"] != "reviewed_ancestor" or "source_hashes" in record:
        raise RecoveryError("invalid_applicability_mode")
    return {
        "receipt_sha256": fingerprint,
        "tested_commit": tested,
        "accepted_commit": record["accepted_commit"],
        "integration_commit": integration_commit,
        "mode": record["mode"],
        "meaning": "coordinator applicability, not fresh execution",
        "limits_preserved": True,
    }
