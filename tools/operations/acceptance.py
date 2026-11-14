"""Resolve real acceptance checks and reject incomplete release evidence.

This never starts models, browsers or native tools. JUnit is offline evidence only.
Receipt validation checks provenance fields and bytes, not creative approval or
the scientific adequacy of the attached test. Human evidence review is still required.
"""

import argparse
import ast
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .recovery import CHECKOUT, write_json
from .safety import RecoveryError, digest, safe_file

LAYERS = {"offline", "native", "browser:chromium", "browser:brave", "browser:edge"}


def resolve_map(root: Path, mapping: dict) -> None:
    scenarios = json.loads((root / "docs/acceptance/scenarios.json").read_text())
    ids = [case["id"] for case in mapping["cases"]]
    if len(set(ids)) != 31 or set(ids) != {case["id"] for case in scenarios["scenarios"]}:
        raise RecoveryError("coverage_requires_C01_through_C31_exactly_once")
    for case in mapping["cases"]:
        if not case["tests"] or not set(case["required_evidence"]) <= LAYERS:
            raise RecoveryError("invalid_evidence_requirements")
        for node in case["tests"]:
            filename, function = node.split("::")
            if not filename.startswith("tests/") or not function.startswith("test_"):
                raise RecoveryError("not_a_test_reference")
            tree = ast.parse(safe_file(root, filename).read_text())
            if not any(
                isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == function
                for item in tree.body
            ):
                raise RecoveryError("test_function_missing:" + node)


def junit_outcomes(paths: list[Path]) -> dict[str, list[str]]:
    outcomes: dict[str, list[str]] = {}
    for path in paths:
        document = ET.parse(path)
        for case in document.iter("testcase"):
            filename = case.get("classname", "").replace(".", "/") + ".py"
            function = case.get("name", "").split("[", 1)[0]
            status = "synthetic_verified"
            if case.find("failure") is not None or case.find("error") is not None:
                status = "failed"
            elif case.find("skipped") is not None:
                status = "unexecuted"
            outcomes.setdefault(f"{filename}::{function}", []).append(status)
    return outcomes


def inspect_receipts(root: Path, path: Path | None, application_commit: str | None) -> dict:
    if path is None:
        return {
            "receipts": [],
            "journeys": [],
            "m10": {"status": "blocked_no_owner_scope_decision"},
        }
    evidence = json.loads(path.read_text())
    if not application_commit or not re.fullmatch("[0-9a-f]{40}", application_commit):
        raise RecoveryError("exact_application_commit_required_for_receipts")
    for receipt in evidence.get("receipts", []) + evidence.get("journeys", []):
        required = {
            "case_id",
            "layer",
            "status",
            "evidence_type",
            "application_commit",
            "contract_revision",
            "fixture_revision",
            "command",
            "runtime_identity",
            "artifacts",
            "limits",
        }
        if not required <= receipt.keys():
            raise RecoveryError("incomplete_evidence_receipt")
        if (
            receipt["application_commit"] != application_commit
            or receipt["contract_revision"] != "1.0.0"
        ):
            raise RecoveryError("evidence_revision_mismatch")
        if receipt["layer"] not in LAYERS | {"journey", "ssh", "performance"}:
            raise RecoveryError("invalid_evidence_layer")
        if receipt["status"] not in {
            "planned",
            "synthetic_verified",
            "live_pass",
            "failed",
            "blocked",
        }:
            raise RecoveryError("invalid_evidence_status")
        if receipt["layer"] == "offline" and receipt["status"] == "live_pass":
            raise RecoveryError("offline_fixture_is_not_native_pass")
        if receipt["status"] == "live_pass":
            expected_type = (
                "browser_product_capture"
                if receipt["layer"].startswith("browser:")
                else "ssh_authenticated_product_capture"
                if receipt["layer"] == "ssh"
                else "host_measurement"
                if receipt["layer"] == "performance"
                else "native_runtime_capture"
            )
            if receipt["evidence_type"] != expected_type:
                raise RecoveryError("fixture_or_transport_double_is_not_live_product_evidence")
        if receipt["status"] in {"synthetic_verified", "live_pass"}:
            if (
                not receipt["command"]
                or not receipt["runtime_identity"]
                or not receipt["fixture_revision"]
                or not receipt["artifacts"]
            ):
                raise RecoveryError("passing_evidence_requires_runtime_command_and_bytes")
            for artifact in receipt["artifacts"]:
                if digest(safe_file(root, artifact["path"])) != artifact["sha256"]:
                    raise RecoveryError("evidence_hash_mismatch")
    # Scope decisions are never inferred from a failed image-to-3D run.
    m10 = evidence.get("m10", {})
    if m10.get("status") == "owner_scope_change":
        if (
            m10.get("actor") != "human_owner"
            or not m10.get("decision_reference")
            or not m10.get("artifacts")
        ):
            raise RecoveryError("explicit_owner_scope_decision_required")
        for artifact in m10["artifacts"]:
            if digest(safe_file(root, artifact["path"])) != artifact["sha256"]:
                raise RecoveryError("scope_decision_hash_mismatch")
    return evidence


def report(root: Path, mapping: dict, outcomes: dict, evidence: dict) -> dict:
    resolve_map(root, mapping)
    cases = []
    for case in mapping["cases"]:
        states = {}
        for layer in case["required_evidence"]:
            receipts = [
                r
                for r in evidence.get("receipts", [])
                if r["case_id"] == case["id"] and r["layer"] == layer
            ]
            needed = "synthetic_verified" if layer == "offline" else "live_pass"
            if any(r["status"] == "failed" for r in receipts):
                states[layer] = "failed"
            elif layer == "offline" and any(
                "failed" in outcomes.get(node, []) for node in case["tests"]
            ):
                states[layer] = "failed"
            elif any(r["status"] == needed and not r["limits"] for r in receipts):
                states[layer] = needed
            elif layer == "offline" and all(
                node in outcomes and set(outcomes[node]) == {"synthetic_verified"}
                for node in case["tests"]
            ):
                states[layer] = "synthetic_verified"
            else:
                states[layer] = "incomplete" if receipts else "unexecuted"
        observations = {node: outcomes[node] for node in case["tests"] if node in outcomes}
        cases.append({**case, "evidence": states, "offline_test_observations": observations})
    gaps = [
        f"{case['id']}:{layer}:{state}"
        for case in cases
        for layer, state in case["evidence"].items()
        if state not in {"synthetic_verified", "live_pass"}
    ]
    journey_receipts = evidence.get("journeys", [])
    for identity in [f"M{i:02}" for i in range(1, 15) if i != 10] + ["SSH", "PERFORMANCE"]:
        expected_layer = (
            "ssh"
            if identity == "SSH"
            else "performance"
            if identity == "PERFORMANCE"
            else "journey"
        )
        if not any(
            r["case_id"] == identity
            and r["layer"] == expected_layer
            and r["status"] == "live_pass"
            and not r["limits"]
            for r in journey_receipts
        ):
            gaps.append(f"{identity}:cumulative_acceptance_unexecuted_or_incomplete")
    m10 = evidence.get("m10", {})
    if m10.get("status") != "owner_scope_change" and not any(
        r["case_id"] == "M10"
        and r["status"] == "live_pass"
        and r["layer"] == "native"
        and not r["limits"]
        for r in journey_receipts
    ):
        gaps.append("M10:blocked_requires_native_mesh_proof_or_explicit_owner_scope_decision")
    return {
        "format": "proscenium-acceptance-report-1",
        "map_resolved": True,
        "release_ready": not gaps,
        "cases": cases,
        "gaps": gaps,
        "m10": m10,
        "limits": [
            "Function resolution is not execution.",
            "JUnit proves only offline checks.",
            "Structured receipts require human review of their actual evidence.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", type=Path, default=CHECKOUT / "tests/acceptance/coverage.json")
    parser.add_argument("--junit", type=Path, action="append", default=[])
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--application-commit")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--release", action="store_true", help="Exit 1 unless all required evidence is complete"
    )
    args = parser.parse_args()
    try:
        result = report(
            CHECKOUT,
            json.loads(args.map.read_text()),
            junit_outcomes(args.junit),
            inspect_receipts(CHECKOUT, args.receipts, args.application_commit),
        )
        if args.output:
            write_json(args.output, result)
        print(
            json.dumps(
                {
                    "map_resolved": result["map_resolved"],
                    "cases": len(result["cases"]),
                    "release_ready": result["release_ready"],
                    "gaps": result["gaps"],
                },
                indent=2,
            )
        )
        if args.release and not result["release_ready"]:
            raise SystemExit(1)
    except (RecoveryError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"Acceptance evidence refused: {exc}\n")


if __name__ == "__main__":
    main()
