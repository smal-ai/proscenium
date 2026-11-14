"""Final preflight evidence remains distinct from actual SSH journey evidence."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_whole_driver_and_actual_blocked_api_preflight():
    value = json.loads((ROOT / "docs/evidence/m14-ssh/final-preflight.json").read_text())
    flow = value["synthetic_full_flow"]
    assert len(flow["processes"]) == 6 and flow["all_original_identities_gone"]
    assert flow["output_parent_created"] and flow["original_m13_preserved"]
    assert "not product acceptance" in flow["scope"]
    partial = value["synthetic_partial_startup"]
    assert len(partial["processes"]) == 1 and partial["all_original_identities_gone"]
    actual = value["actual_blocked_api"]
    assert actual["seconds"] < 30 and actual["sends"] == 0
    assert actual["forbidden_attempts"] == [] and actual["gate_closed"]
    assert all(row["status"] == 200 for row in actual["paths"])
    assert actual["cleanup"]["original_identity_gone"]
    assert len(actual["lifespan_audit_sha256"]) == 64
