"""Assemble committed release evidence without executing the application or its tests.

This builds an index and INACTIVE receipt proposals. It cannot grant applicability,
accept a milestone, infer equivalent descendants or produce a release-ready result.
Only local Git objects and explicit recipe files are read. Historical identities
and raw evidence bytes are never rewritten.
"""

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .acceptance import inspect_receipts, report
from .applicability import git, known_commit, patch_hash, receipt_digest, validate_cherry_picks
from .safety import RecoveryError, relative


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob(root: Path, snapshot: str, path: str) -> bytes:
    relative(path)
    tree = git(root, "ls-tree", snapshot, "--", path)
    if not tree.stdout.startswith((b"100644 blob ", b"100755 blob ")):
        raise RecoveryError("release_input_not_committed_regular_file:" + path)
    result = git(root, "cat-file", "blob", tree.stdout.split()[2].decode())
    if result.returncode:
        raise RecoveryError("release_input_unavailable:" + path)
    return result.stdout


def pointer(value, path: str):
    for token in path.strip("/").split("/") if path else []:
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def recorded_commands(value, path="") -> list[dict]:
    """Keep the original context: a preflight command is not a native invocation."""
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            target = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if (key in {"command", "argv"} or key.endswith("_command")) and isinstance(
                child, str | list
            ):
                found.append({"pointer": target, "value": child})
            else:
                found.extend(recorded_commands(child, target))
    elif isinstance(value, list):
        for number, child in enumerate(value):
            found.extend(recorded_commands(child, path + f"/{number}"))
    return found


def cherry_pick_proposal(root: Path, receipt: dict, candidate: str, pairing: dict) -> dict:
    """Expand ONLY supplied exact pairs, prove completeness, never approve them.

    The caller supplies the original receipt bytes, accepted commit, common base,
    full ordered tested/integrated pairs and distinct regression artefacts. This
    never searches for a similar patch or assumes arbitrary descendants equivalent.
    The parent's final approval must bind the final receipt digest, not a draft ID.
    """
    record = {
        "format": "proscenium-applicability-1",
        "receipt_sha256": receipt_digest(receipt),
        "tested_commit": receipt["application_commit"],
        "accepted_commit": pairing["accepted_commit"],
        "integration_commit": candidate,
        "reviewer": None,
        "decision": "pending",
        "reason": pairing["reason"],
        "mode": "reviewed_cherry_pick",
        "validation_artifacts": pairing["validation_artifacts"],
        "common_base": pairing["common_base"],
        "patches": [
            {**pair, "patch_sha256": patch_hash(root, pair["tested_commit"])}
            for pair in pairing["patches"]
        ],
    }
    validate_cherry_picks(root, receipt["application_commit"], pairing["accepted_commit"], record)
    known_commit(root, candidate)
    if git(root, "merge-base", "--is-ancestor", pairing["accepted_commit"], candidate).returncode:
        raise RecoveryError("unrelated_applicability_acceptance")
    original = {(a["path"], a["sha256"]) for a in receipt["artifacts"]}
    regression = record["validation_artifacts"]
    if not regression or not any((a["path"], a["sha256"]) not in original for a in regression):
        raise RecoveryError("applicability_requires_distinct_regression_evidence")
    for artifact in regression:
        if sha(blob(root, candidate, artifact["path"])) != artifact["sha256"]:
            raise RecoveryError("applicability_artifact_hash_mismatch")
    return record


def build(root: Path, recipe: dict, snapshot: str, candidate: str) -> dict:
    known_commit(root, snapshot)
    known_commit(root, candidate)
    cache = {}

    def data(path):
        if path not in cache:
            cache[path] = blob(root, snapshot, path)
        return cache[path]

    def artifact(path):
        raw = data(path)
        return {"path": path, "sha256": sha(raw), "bytes": len(raw)}

    mapping = json.loads(data("tests/acceptance/coverage.json"))
    scenarios = json.loads(data("docs/acceptance/scenarios.json"))["scenarios"]
    if [c["id"] for c in recipe["cases"]] != [c["id"] for c in mapping["cases"]]:
        raise RecoveryError("release_recipe_requires_ordered_C01_C31")
    sources = {}
    for source in recipe["sources"]:
        identity = source["application_commit"]
        if not re.fullmatch("[0-9a-f]{40}", identity) or source["id"] in sources:
            raise RecoveryError("release_source_identity_invalid")
        facts, commands = [], []
        for reference in source["facts"]:
            value = json.loads(data(reference["path"]))
            facts.append({**reference, "value": pointer(value, reference["pointer"])})
        for path in source["files"]:
            if path.endswith(".json"):
                commands.extend(
                    {"path": path, **c} for c in recorded_commands(json.loads(data(path)))
                )
        sources[source["id"]] = {
            **source,
            "artifacts": [artifact(path) for path in source["files"]],
            "facts": facts,
            "recorded_commands": commands,
        }
    retained = []
    for path in recipe.get("retained_artifacts", []):
        retained.append(artifact(path))
    m13_manifest = json.loads(data("docs/evidence/m13/retained-bytes.json"))
    for expected in sorted(m13_manifest["files"]):
        path = "docs/evidence/m13/retained/" + expected
        entry = artifact(path)
        if (
            entry["sha256"] != expected
            or entry["bytes"] != m13_manifest["files"][expected]["bytes"]
        ):
            raise RecoveryError("release_m13_retained_hash_mismatch")
        retained.append(entry)
    historical = []
    for failure in recipe["historical_failures"]:
        historical.append({**failure, "artifacts": [artifact(p) for p in failure["paths"]]})
    # Validate explicit main-reachable inspection mirrors without querying an
    # original fleet-only Git object or changing its historical execution identity.
    mirror_path = "docs/evidence/m14-ssh/complete-source-mirrors.json"
    mirrors = json.loads(data(mirror_path))
    for mirror in mirrors["sources"].values():
        for path, entry in mirror["files"].items():
            if mirror["mode"] == "retained_exact_source":
                raw = data(entry["retained_path"])
                retained.append(artifact(entry["retained_path"]))
            elif mirror["mode"] == "accepted_git_mirror":
                accepted = mirror["accepted_revision"]
                if git(root, "merge-base", "--is-ancestor", accepted, snapshot).returncode:
                    raise RecoveryError("release_mirror_not_snapshot_reachable")
                raw = blob(root, accepted, path)
            else:
                raise RecoveryError("release_unknown_mirror_mode")
            if sha(raw) != entry["sha256"]:
                raise RecoveryError("release_inspection_mirror_hash_mismatch")
    outcomes = {}
    junit = []
    for path in recipe["offline_junit"]:
        raw = data(path)
        document = ET.fromstring(raw)
        # Same normalisation as acceptance.junit_outcomes, without a temporary file.
        for test in document.iter("testcase"):
            node = test.get("classname", "").replace(".", "/") + ".py::"
            node += test.get("name", "").split("[", 1)[0]
            status = "synthetic_verified"
            if test.find("failure") is not None or test.find("error") is not None:
                status = "failed"
            elif test.find("skipped") is not None:
                status = "unexecuted"
            outcomes.setdefault(node, []).append(status)
        junit.append(artifact(path))
    cases, proposals = [], []
    for declared, scenario, case in zip(mapping["cases"], scenarios, recipe["cases"], strict=True):
        if declared["id"] != scenario["id"]:
            raise RecoveryError("release_scenario_order_mismatch")
        required = declared["required_evidence"]
        for identity in case["sources"]:
            if identity not in sources:
                raise RecoveryError("release_source_missing:" + identity)
        invariants = []
        for number, text in enumerate(scenario["then"], 1):
            invariants.append(
                {
                    "ordinal": number,
                    "requirement": text,
                    "offline": {
                        "mapped_nodes": declared["tests"],
                        "meaning": "Declared case-level test mapping, not a claim that each node proves every clause.",
                    },
                    "native_support": case["sources"]
                    if number in case["native_invariants"]
                    else [],
                    "browser_support": ["browser-matrix"]
                    if number in case["browser_invariants"]
                    else [],
                    "interpretation": "Support is scoped by the case observation and source qualifications. Unlisted clauses remain offline/parent evidence review, not invented live execution.",
                }
            )
        cases.append({**declared, **case, "name": scenario["name"], "invariants": invariants})
        for layer in required:
            if layer == "offline":
                continue
            ids = ["browser-matrix"] if layer.startswith("browser:") else case["sources"]
            for identity in ids:
                source = sources[identity]
                receipt = {
                    "case_id": case["id"],
                    "layer": layer,
                    "status": "planned",
                    "evidence_type": "browser_product_capture"
                    if layer.startswith("browser:")
                    else "native_runtime_capture",
                    "application_commit": source["application_commit"],
                    "contract_revision": "1.0.0",
                    "fixture_revision": source["fixture_revision"],
                    "command": source["command_reference"],
                    "runtime_identity": {
                        "source_id": identity,
                        "references": [
                            {"path": f["path"], "pointer": f["pointer"]} for f in source["facts"]
                        ],
                    },
                    "artifacts": source["artifacts"],
                    "limits": [
                        "INACTIVE: coordinator must review scope and final-candidate applicability.",
                        *case["remaining"],
                    ],
                    "qualifications": [source["identity_meaning"], *source["qualifications"]],
                    "observation": case["observation"],
                }
                proposals.append(
                    {
                        "proposal_id": f"{case['id']}:{layer}:{identity}",
                        "receipt": receipt,
                        "receipt_sha256": receipt_digest(receipt),
                    }
                )
    journey_proposals = []
    for journey in recipe["journeys"]:
        for identity in journey["sources"]:
            source = sources[identity]
            layer = {"SSH": "ssh", "PERFORMANCE": "performance", "M10": "native"}.get(
                journey["id"], "journey"
            )
            receipt = {
                "case_id": journey["id"],
                "layer": layer,
                "status": "planned",
                "evidence_type": {
                    "ssh": "ssh_authenticated_product_capture",
                    "performance": "host_measurement",
                }.get(layer, "native_runtime_capture"),
                "application_commit": source["application_commit"],
                "contract_revision": "1.0.0",
                "fixture_revision": source["fixture_revision"],
                "command": source["command_reference"],
                "runtime_identity": {
                    "source_id": identity,
                    "references": [f["path"] for f in source["facts"]],
                },
                "artifacts": source["artifacts"],
                "limits": [
                    "INACTIVE: parent must assemble the cumulative criterion set and review applicability.",
                    journey["state"],
                ],
                "qualifications": [source["identity_meaning"], *source["qualifications"]],
            }
            journey_proposals.append(
                {
                    "proposal_id": f"{journey['id']}:{layer}:{identity}",
                    "receipt": receipt,
                    "receipt_sha256": receipt_digest(receipt),
                }
            )
    # Deliberately no receipt activation or inferred applicability. Historical JUnit
    # supplies an archival offline inventory only. Parent supplies final candidate runs.
    partial = report(root, mapping, outcomes, inspect_receipts(root, None, None))
    partial["assembly_scope"] = (
        "Archival JUnit only, no activated native/browser/journey receipts or final-candidate applicability. These validator gaps are NOT a list of new execution requirements."
    )
    partial["candidate"] = candidate
    partial["snapshot"] = snapshot
    partial["junit_artifacts"] = junit
    if partial["release_ready"]:
        raise RecoveryError("draft_must_not_claim_release_ready")
    return {
        "index.json": {
            "format": "proscenium-release-input-index-1",
            "snapshot": snapshot,
            "candidate": candidate,
            "authority": "Evidence assembly only. Parent owns applicability activation, milestone tags and release.",
            "sources": list(sources.values()),
            "cases": cases,
            "journeys": recipe["journeys"],
            "gaps": recipe["gaps"],
            "historical_failures": historical,
            "portable_retained_artifacts": retained,
            "input_artifacts": [artifact(p) for p in sorted(cache)],
        },
        "receipts.draft.json": {
            "format": "proscenium-release-receipt-proposals-1",
            "candidate": candidate,
            "active": False,
            "proposals": proposals,
            "journey_proposals": journey_proposals,
            "journeys": [],
            "applicability": [],
            "m10": {"status": "blocked_remaining_work_publication_pending"},
        },
        "report.partial.json": partial,
        "gaps.json": {
            "format": "proscenium-release-gaps-1",
            "candidate": candidate,
            "required_remaining": recipe["gaps"],
            "validator_assembly_gaps": partial["gaps"],
            "meaning": "Do not buy new native runs to fill archival receipt/import gaps. Parent first reviews existing evidence at its proper layer.",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--snapshot", required=True, help="Exact committed evidence tree")
    parser.add_argument(
        "--candidate",
        required=True,
        help="Exact proposed integration commit, not a tested identity",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    outputs = build(args.root, json.loads(args.recipe.read_text()), args.snapshot, args.candidate)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in outputs.items():
        payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        path = args.output / name
        if path.exists() and path.read_text() != payload:
            raise RecoveryError("release_output_exists_with_different_bytes:" + str(path))
        if not path.exists():
            path.write_text(payload)
    print(
        json.dumps(
            {
                "candidate": args.candidate,
                "sources": len(outputs["index.json"]["sources"]),
                "cases": 31,
                "release_ready": False,
            }
        )
    )


if __name__ == "__main__":
    main()
