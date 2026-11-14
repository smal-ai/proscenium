"""Retained M13 Continue join and honest recovery limits. Never starts native work."""

import hashlib
import json
from pathlib import Path

from PIL import Image

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/runtime-recovery"


def load(name):
    return json.loads((EVIDENCE / name).read_text())


def test_evidence_integrity_and_actual_full_hd():
    manifest = load("manifest.json")
    for relative, digest in manifest.items():
        path = EVIDENCE / relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        if path.suffix == ".png":
            with Image.open(path) as image:
                assert image.size == (1920, 1080)
    assert len([p for p in manifest if p.endswith(".png")]) == 2


def test_m13_positive_continue_joins_original_work_request_and_session():
    data = load("m13-continuation.json")
    assert data["commit"] == "209448a9d10287b777b005cd8e03e888d5e15cc8"
    original = data["original_request"]
    call = data["continued_invocation"]
    continued = data["continuation"]
    assert original["request_id"] == call["request_id"] == "510aa045-3bf0-443a-9889-efe267ca0cfb"
    assert (
        original["receipt"]["work_id"] == call["work_id"] == "work-5bd8623b033b4df0b6825911b798eaf2"
    )
    assert continued["session_id"] == call["session_id"] == data["native_association"]["session_id"]
    assert (
        call["delivery_id"] == continued["operation_id"] == "665b040a-168d-4d02-9525-dc04867620c8"
    )
    assert original["receipt"]["state"] == call["state"] == "completed"
    assert continued["state"] == "acknowledged"
    assert (
        data["publication"]["work_id"] == call["work_id"]
        and data["publication"]["request_id"] == original["request_id"]
    )
    assert data["publication"]["receipt"]["publication_id"] == "m13-publish-v1"
    assert not data["product_copilot_changed_in_candidate"]
    assert data["viewport"] == dict(width=1920, height=1080, dpr=1)


def test_already_started_normal_probe_failure_is_not_relabelled_positive():
    data = load("normal-probe.json")
    assert data["status"].startswith("BLOCKED")
    assert len(data["sends"]) == 1 and data["continuation_sends"] == data["children"] == 0
    assert data["stop_record"][0]["phase"] == "outcome_uncertain"
    assert data["stop_record"][0]["abort"] == data["stop_record"][0]["pause"] == "acknowledged"
    assert any(e["type"] == "abort" for e in data["native_evidence"])
    assert not any(e["type"] == "tool.execution_complete" for e in data["native_evidence"])
    assert data["invocations"][0]["state"] == "running"
    assert not any(row["event"] == "natural-completion" for row in data["helper_observations"])


def test_lost_input_wait_cancels_without_owner_consent_or_work_completion():
    data = load("historical-reconciliation.json")["m03-a"]
    assert data["reconciliation"]["live_invocations"] == 0
    assert data["work_before"] == data["work_after"]
    decision = data["decisions_after"][0]
    assert (
        decision["state"] == "cancelled"
        and decision["response"] is None
        and decision["resolved_by"] is None
    )
    assert decision["delivery_state"] == "not_sent"
    evidence = data["recovery_evidence"][0]
    assert evidence["kind"] == "coordinator_interrupted" and not evidence["native_return_observed"]
    assert evidence["decision_binding_identity"] and evidence["native_event_ids"] == []
    assert (
        load("historical-reconciliation.json")["m03-a-first"]["reconciliation"]["live_invocations"]
        == 1
    )


def test_m11_parent_interruption_uses_real_child_terminal_without_publication():
    data = load("historical-reconciliation.json")["m11"]
    assert data["live_before"][0]["name"] == "task" and data["live_after"] == []
    assert data["recovery_evidence"][0]["native_event_ids"] == [
        "e65e59a0-d0e6-407b-876e-53f614e6d9df",
        "39a8c906-fe3e-460a-a3ff-f8c551a9ce48",
    ]
    assert not data["recovery_evidence"][0]["native_return_observed"]
    assert data["source_and_job_records_unchanged"]
    assert data["work_before"] == data["work_after"]


def test_unknown_bash_outcome_stays_unresolved_and_all_metadata_calls_send_nothing():
    all_data = load("historical-reconciliation.json")
    bash = all_data["m03-b"]
    assert bash["reconciliation"]["live_invocations"] == 1
    assert bash["live_before"] == bash["live_after"] and bash["live_after"][0]["name"] == "bash"
    assert bash["recovery_evidence"] == []
    for data in all_data.values():
        assert data["sends"] == 0 and data["original_top_files_unchanged"]
        assert data["request_ids_before"] == data["request_ids_after"]
        assert data["source_and_job_records_unchanged"]
        assert not data["metadata_process"]["same_identity_alive"]
        assert not data["metadata_process"]["parent_same_identity_alive"]
    clean = load("normal-probe.json")["cleanup"]
    assert all(not clean[k]["same_identity_alive"] for k in ["server", "browser", "native"])
    assert all(not row["same_identity_alive"] for row in clean["helpers"])


def test_exports_have_no_auth_or_private_reasoning_fields():
    for path in EVIDENCE.glob("*.json"):
        for excluded in [
            '"pair_url"',
            '"pairing_code"',
            '"csrf_token"',
            '"token":',
            "Bearer ",
            "#pair=",
            "reasoningText",
        ]:
            assert excluded not in path.read_text(), (path.name, excluded)
