"""Retained metadata evidence only. No SDK, GUI, model or original-project writes."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "docs/evidence/native-io-policy"


def load(name):
    return json.loads((ROOT / name).read_text())


def test_native_io_evidence_hashes():
    for relative, digest in load("manifest.json").items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest


def test_m11_barrier_failure_is_not_relabelled_ready_or_native_search_return():
    data = load("m11-stop.json")
    assert not data["ready_for_original_continue"]
    assert (
        data["work_before"]["state"] == "awaiting_user"
        and data["work_before"]["state_revision"] == 8
    )
    assert (
        data["work_after"]["state"] == "stop_requested"
        and data["work_after"]["state_revision"] == 9
    )
    assert data["stop_phase"]["phase"] == data["stop_receipt"]["state"] == "outcome_uncertain"
    assert data["stop_phase"]["pause"] == data["stop_phase"]["abort"] == "acknowledged"
    assert (
        "abort_native_event_id" not in data["stop_phase"]
        and "idle_native_event_id" not in data["stop_phase"]
    )
    assert (
        len(data["live_after"]) == 1
        and data["live_after"][0]["name"] == "rg"
        and data["live_after"][0]["state"] == "running"
    )
    assert data["read_outcomes"] == []


def test_m11_stop_cancels_only_decision_without_answer_or_delivery_ack():
    data = load("m11-stop.json")
    decision = data["decisions_after"][0]
    assert decision["native_decision_id"] == "f4cb7f12-085b-42b8-9288-19b946111107"
    assert (
        decision["state"] == "cancelled"
        and decision["response"] is None
        and decision["resolved_by"] is None
    )
    assert decision["delivery_state"] == "not_sent"
    assert data["permission_stage_before"] == data["permission_stage_after"]
    assert (
        data["permission_stage_after"]["reply"] == "reject"
        and data["permission_stage_after"]["authority"] == "host"
    )
    assert data["permission_stage_after"]["delivery_state"] == "outcome_uncertain"


def test_m11_checkpoint_and_actual_file_hash_attestation_preserved():
    data = load("m11-checkpoint.json")
    scope = load("m11-stop.json")
    assert data["receipt_unchanged"] and data["checkpoint_files_match_actual_bytes"]
    assert (
        data["receipt"]["source_commit"]
        == scope["head_before"]
        == scope["head_after"]
        == "8d49caaee980d18a140111e81b15a46c71e229ce"
    )
    assert data["receipt"]["files"]
    assert all(len(entry["sha256"]) == 64 for entry in data["receipt"]["files"])


def test_m10_completed_row_remains_historical_and_producer_is_not_full_task_proof():
    data = load("m10-settlement.json")
    finding = data["completion_diagnostic"]
    assert data["work_before"] == data["work_after"]
    assert data["work_after"]["state"] == "completed" and data["work_after"]["state_revision"] == 6
    assert finding["producer_native_event_id"] == "3c0c7062-ce70-4e7e-be44-01f7260fbd0e"
    assert finding["cached_completion_before"]["candidate"]
    assert finding["producer_binding"]["delivery_id"] == "dae26d99-3ef2-49ff-a5d0-78b2ee82f739"
    assert (
        not finding["full_task_terminal_observed"] and not finding["nonempty_public_final_observed"]
    )
    assert data["permission_stage_before"] == data["permission_stage_after"]
    assert data["permission_stage_before"]["authority"] == "automatic_policy"


def test_zero_send_bounded_metadata_preservation_and_exact_cleanup():
    for name in ["m11-stop.json", "m10-settlement.json"]:
        data = load(name)
        counts = data["counts"]
        assert counts["opens"] == counts["raw_history_rpc"] == counts["typed_history"] == 1
        assert counts["sends"] == counts["unexpected_turns"] == counts["blocked_callbacks"] == 0
        assert data["native_runtime_seconds"] < 180
        assert (
            not data["process"]["same_identity_alive"]
            and not data["process"]["parent_same_identity_alive"]
        )
        assert data["native_user_messages_before"] == data["native_user_messages_after"]
        for field in [
            "source_scene_meta_unchanged",
            "all_old_reports_unchanged",
            "git_refs_unchanged",
            "old_bindings_unchanged",
            "contexts_unchanged",
            "source_job_receipt_records_unchanged",
            "send_records_unchanged",
            "original_submissions_unchanged",
            "native_history_prefix_preserved",
        ]:
            assert data[field], (name, field)


def test_no_authentication_fields_exported():
    for path in ROOT.glob("*.json"):
        for excluded in [
            '"pair_url"',
            '"pairing_code"',
            '"csrf_token"',
            '"token":',
            "Bearer ",
            "#pair=",
        ]:
            assert excluded not in path.read_text(), (path.name, excluded)
