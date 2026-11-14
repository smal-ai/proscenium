"""Integrity/oracles over retained passive metadata effects, not continuation proof."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "docs/evidence/native-denial-recovery"


def load(name):
    return json.loads((ROOT / name).read_text())


def test_retained_passive_evidence_integrity():
    for relative, digest in load("manifest.json").items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest


def test_actual_m10_denial_is_separate_from_unknown_delivery_or_owner_authority():
    data = load("m10.json")
    assert len(data["live_before"]) == 1 and data["live_after"] == []
    observation = data["outcomes"][0]
    assert observation["kind"] == "native_authorization_denied"
    assert observation["tool_call_id"] == "call_Yo8Pyi1HvNYCI5E6CSQxCamx"
    assert observation["native_id"] == "1fbd60bd-5e63-4553-af72-8b6d50648dc1"
    assert observation["source_event_ids"] == [
        "dbfcd230-d244-4c69-8a2a-cfd39e1d10fc",
        "01ec62fb-3aa3-49e0-ae42-f9b2720bb48d",
        "43d539c1-3a3a-4173-abc4-8e09db265c51",
    ]
    assert observation["native_exit_code"] is None and not observation["native_return_observed"]
    before = next(
        s for s in data["permission_stages_before"] if s["native_id"] == observation["native_id"]
    )
    after = next(
        s for s in data["permission_stages_after"] if s["native_id"] == observation["native_id"]
    )
    assert before == after
    assert (
        after["phase"] == "outcome_uncertain"
        and after["reply"] is None
        and after["authority"] is None
    )
    assert after["delivery_state"] == "not_sent" and data["decisions_after"] == []
    assert not data["new_authoring_outputs_exist"]


def test_actual_m11_wait_cancelled_without_owner_answer_or_checkpoint():
    data = load("m11.json")
    decision = next(
        d
        for d in data["decisions_after"]
        if d["native_decision_id"] == "cc62c65b-eb3b-4f3d-93a4-740a0925207f"
    )
    assert (
        decision["state"] == "cancelled"
        and decision["response"] is None
        and decision["resolved_by"] is None
    )
    assert decision["delivery_state"] == "not_sent"
    observation = data["outcomes"][0]
    assert observation["kind"] == "uninvoked_custom_tool_interrupted"
    assert observation["source_event_ids"] == [
        "a9c0ab74-2a20-4b96-98e1-922514ce7555",
        "ca6ff698-1d3a-448f-b284-55afea447991",
        "d35f1be6-dd38-4b29-9bf4-aea761be9004",
    ]
    assert (
        not observation["checkpoint_effect_observed"] and not observation["native_return_observed"]
    )
    assert not data["new_checkpoint_intent_exists"]
    assert data["head_before"] == "3297ae647200270ebdc102630e7cfbfce2f4bfd1"
    before = next(
        s
        for s in data["permission_stages_before"]
        if s["native_id"] == decision["native_decision_id"]
    )
    after = next(
        s
        for s in data["permission_stages_after"]
        if s["native_id"] == decision["native_decision_id"]
    )
    assert (
        before["reply"] == after["reply"] == "reject"
        and before["authority"] == after["authority"] == "host"
    )
    assert (
        after["delivery_state"] == "acknowledged"
    )  # Actual matching native host-rejection completion, not an owner answer.


def test_passive_recovery_does_not_change_source_jobs_context_or_work_state():
    for name in ["m10.json", "m11.json"]:
        data = load(name)
        for field in [
            "source_scene_meta_bytes_unchanged",
            "old_reports_unchanged",
            "original_bindings_unchanged",
            "git_refs_unchanged",
            "contexts_unchanged",
            "source_job_counter_records_unchanged",
            "submissions_unchanged",
            "work_states_unchanged",
            "send_records_unchanged",
            "native_history_prefix_preserved",
        ]:
            assert data[field], (name, field)
        assert data["work_before"] == data["work_after"]
        assert data["native_user_messages_before"] == data["native_user_messages_after"]
        assert (
            data["reconciliation"]["pending_empty"]
            and data["reconciliation"]["live_invocations"] == 0
        )


def test_no_new_send_continue_or_owned_process_left_live():
    for name in ["m10.json", "m11.json"]:
        data = load(name)
        assert data["sends"] == data["continue_commands"] == 0
        assert not data["revision_script_executed_by_this_proof"]
        assert not data["native_process"]["same_identity_alive"]
        assert not data["native_process"]["parent_same_identity_alive"]


def test_exports_have_no_authentication_fields():
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
