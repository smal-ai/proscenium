"""Retained actual M11 positive RPC barrier, no SDK/model or original-project writes."""

import hashlib
import json
from pathlib import Path

from proscenium.contracts import canonical
from proscenium.copilot.cold_stop import METHODS, queue_empty

ROOT = Path(__file__).resolve().parents[3] / "docs/evidence/cold-stop-recovery"


def load(name):
    return json.loads((ROOT / name).read_text())


def test_actual_cold_stop_evidence_integrity():
    for relative, digest in load("manifest.json").items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest


def test_original_work_and_stop_are_ready_without_goal_completion():
    data = load("result.json")
    assert data["ready_for_original_continue"]
    assert (
        data["work_before"]["state"] == "stop_requested"
        and data["work_before"]["state_revision"] == 9
    )
    assert data["work_after"]["state"] == "stopped" and data["work_after"]["state_revision"] == 10
    assert (
        data["stop_receipt"]["state"] == "completed" and data["stop_receipt"]["state_revision"] == 2
    )
    assert (
        data["stop_id"]
        == data["stop_receipt"]["request_id"]
        == "33048c8a-df79-4027-a6c2-10ba396e5044"
    )
    assert data["live_after"] == [] and data["reconcile_operation"]["state"] == "completed"
    assert not data["new_unity_publication"] and not data["native_goal_completion_claimed"]
    assert data["new_request_ids"] == [] and data["original_stop_retry"]["replayed"]


def test_actual_literal_false_and_real_response_ids_hashes():
    observation = load("rpc-observation.json")
    rpc = observation["snapshot"]["rpc"]
    assert observation["status"] == "applied"
    assert tuple(r["method"] for r in rpc) == METHODS
    assert len({r["rpc_request_id"] for r in rpc}) == len(METHODS)
    for record in rpc:
        assert record["session_id"] == "0cbee1a2-b5ed-4ab8-962b-ccf1d9f8a454"
        assert record["status"] == "succeeded" and record["started_at"] and record["finished_at"]
        assert (
            record["response_sha256"]
            == record["summary_sha256"]
            == hashlib.sha256(canonical(record["response"])).hexdigest()
        )
    value = next(r for r in rpc if r["method"] == "session.interruptMainTurn")
    assert (
        value["parameters"] == {"flushQueued": False} and value["response"]["interrupted"] is False
    )
    assert value["rpc_request_id"] == "786187b4-ec19-42f2-8617-695b7ee868a1"
    assert (
        value["response_sha256"]
        == "475fb20d3357e3e6ee3ba14fc517d3431f59db0f7a4f024d3a7690c719968e55"
    )
    assert all(
        queue_empty(r["response"]) for r in rpc if r["method"] == "session.queue.pendingItems"
    )
    assert next(r for r in rpc if r["method"] == "session.tasks.list")["response"] == {"tasks": []}
    assert next(r for r in rpc if r["method"] == "session.tools.listPendingRequests")[
        "response"
    ] == {"items": []}


def test_original_failed_receipt_and_outbox_preserved_as_history_not_reset():
    data = load("result.json")
    o = load("rpc-observation.json")
    assert o["previous_stop_receipt"] == data["original_stop_receipt"]
    assert o["previous_stop_receipt"]["state"] == "outcome_uncertain"
    assert o["previous_stop_phase"]["phase"] == "outcome_uncertain"
    assert o["previous_outbox"]["state"] in {"dispatching", "outcome_uncertain"}
    assert o["stop_payload"] == data["original_stop_payload"]
    assert data["stop_phase"]["completion_basis"] == "cold_inactive_main_loop_rpc"
    assert "idle_native_event_id" not in data["stop_phase"]
    outcome = data["read_outcomes"][0]
    assert outcome["rpc_observation_id"] == o["id"] and not outcome["native_return_observed"]
    assert outcome["native_exit_code"] is None
    assert outcome["source_event_ids"] == [
        "8fa39d4d-0399-474e-b8a4-1d23ddd7e3c4",
        "87b37652-ab81-42e5-a93c-b70166e14562",
    ]


def test_old_question_and_prepared_host_reject_delivery_are_byte_identical():
    data = load("result.json")
    assert data["questions_before"] == data["questions_after"] and data["question_byte_identical"]
    question = data["questions_after"][0]
    assert (
        question["state"] == "cancelled"
        and question["response"] is None
        and question["resolved_by"] is None
        and question["delivery_state"] == "not_sent"
    )
    assert data["stages_before"] == data["stages_after"] and data["permission_stage_byte_identical"]
    assert (
        data["stages_after"][0]["reply"] == "reject"
        and data["stages_after"][0]["authority"] == "host"
        and data["stages_after"][0]["delivery_state"] == "outcome_uncertain"
    )


def test_checkpoint_preservation_zero_send_and_exact_cleanup():
    data = load("result.json")
    receipt = load("checkpoint.json")
    assert (
        receipt["source_commit"]
        == data["head_before"]
        == data["head_after"]
        == "8d49caaee980d18a140111e81b15a46c71e229ce"
    )
    assert data["checkpoint_receipt_files_verified"] and receipt["files"]
    for field in [
        "source_scene_meta_unchanged",
        "all_old_reports_unchanged",
        "git_refs_unchanged",
        "old_bindings_unchanged",
        "contexts_unchanged",
        "source_job_receipt_records_unchanged",
        "send_records_unchanged",
        "old_submissions_unchanged",
        "native_history_prefix_preserved",
    ]:
        assert data[field], field
    assert data["counts"]["opens"] == data["counts"]["raw_history_rpc"] == 1
    assert (
        data["counts"]["sends"]
        == data["counts"]["blocked_callbacks"]
        == data["counts"]["unexpected_turns"]
        == 0
    )
    assert data["native_user_messages_before"] == data["native_user_messages_after"]
    assert data["native_runtime_seconds"] < 180
    assert (
        not data["process"]["same_identity_alive"]
        and not data["process"]["parent_same_identity_alive"]
    )


def test_no_authentication_or_private_task_prompts_exported():
    for path in ROOT.glob("*.json"):
        for excluded in [
            '"pair_url"',
            '"pairing_code"',
            '"csrf_token"',
            '"token":',
            "Bearer ",
            "#pair=",
            "PRIVATE_ONLY_TEST",
        ]:
            assert excluded not in path.read_text(), (path.name, excluded)
