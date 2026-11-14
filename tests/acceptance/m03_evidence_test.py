"""Integrity/oracles over retained real evidence. Does not rerun or certify models."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence/m03"


def report():
    return json.loads((EVIDENCE / "run.json").read_text())


def test_m03_retained_files_and_actual_desktop_dimensions():
    manifest = json.loads((EVIDENCE / "manifest.json").read_text())
    for relative, digest in manifest.items():
        path = EVIDENCE / relative
        assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
        if path.suffix == ".png":
            with Image.open(path) as image:
                assert image.size == (1920, 1080)
    assert len([p for p in manifest if p.endswith(".png")]) == 10
    assert report()["browser"]["engine"] == "159.0.8154.6"


def test_all_actual_sends_counted_once_and_worker_is_actually_required_economical_model():
    data = report()
    allocation = data["allocation"]
    assert allocation["actual_sdk_sends"] == 5 <= allocation["maximum_sends"] == 6
    assert allocation["native_children"] == 1 <= allocation["maximum_children"] == 3
    assert len({s["request_id"] for s in data["sdk_sends"]}) == 5
    assert all(s["native_message_id"] for s in data["sdk_sends"])
    assert allocation["queue_sends"] == 4 and allocation["steer_sends"] == 1
    model = data["economical_model"]
    assert model["lead"] == "gpt-6.1-sol"
    assert data["child"]["data"]["model"] == model["worker"] == "gpt-6-luna"
    assert data["child"]["data"]["modelSelectionSource"] == "configured_required"
    lead, worker = model["lead_catalogue_prices"], model["worker_catalogue_prices"]
    assert lead["batchSize"] == worker["batchSize"]
    assert (
        lead["inputPrice"] / worker["inputPrice"]
        == lead["outputPrice"] / worker["outputPrice"]
        == 20
    )
    assert model["currency_unit_unreported"]


def test_held_b_has_original_context_in_actual_native_delivery_after_browsing_a():
    data = report()
    queued = data["queue_b"]
    assert queued["browser_after_queue"] == "M03 labelled scene A"
    assert queued["selection"]["item_id"] == "m03-item-B"
    assert queued["selection"]["version_id"] == "m03-version-B"
    assert [f["artifact_id"] for f in queued["attached_files"]] == ["m03-image-B"]
    assert queued["native_delivery"]["context_id"] == queued["context_id"]
    assert queued["native_delivery"]["payload_matches_original"]
    assert queued["get_version"]["data"]["arguments"] == {"version_id": "m03-version-B"}
    barrier = datetime.fromisoformat(data["stop"]["confirmed"]["barrier_at"])
    delivered = datetime.fromisoformat(queued["native_delivery"]["at"].replace("Z", "+00:00"))
    assert delivered > barrier
    assert queued["request_id"] in data["stop"]["held_after"]
    assert data["resume"]["release"]["mode"] == "release_held_queue"


def test_stop_was_active_child_not_idle_and_independent_real_encoder_survived():
    data = report()
    stop = data["stop"]
    tool = stop["actual_child_tool_before_stop"]
    assert tool["actor"]["kind"] == "subagent"
    assert tool["actor"]["id"] == data["child"]["agentId"]
    assert tool["payload"]["name"] == "bash" and tool["payload"]["state"] == "running"
    assert stop["admission"]["state"] == "stop_requested"
    assert stop["confirmed"]["phase"] == "confirmed"
    assert stop["confirmed"]["abort"] == "acknowledged"
    assert stop["confirmed"]["pending_empty"]
    barrier = datetime.fromisoformat(stop["confirmed"]["barrier_at"]).timestamp()
    assert stop["helper_started"]["at"] < stop["helper_last_write"]["at"] <= barrier
    assert 0 < barrier - stop["helper_started"]["at"] < stop["natural_timeout_seconds"] <= 90
    assert stop["no_helper_natural_completion"] and stop["no_further_effects_observed"]
    assert stop["independent_before"] == stop["independent_after"]
    assert stop["independent_after"]["state"] == "running"
    assert stop["independent_after"]["work_id"] != stop["confirmed"]["work_id"]
    assert stop["independent_process_observations"][-1]["exit_code"] == 0
    assert stop["independent_final"]["state"] == "succeeded"
    assert stop["independent_final"]["native_exit_code"] == 0
    assert data["steer"]["native_delivery"]["delivery"] == "steering"
    assert not data["steer"]["semantic_application_claimed"]


def test_reload_and_owner_card_real_ui_preserve_identity_and_separate_delivery():
    data = report()
    assert data["reload"]["unique_user_entries"]
    assert {r["id"] for r in data["reload"]["before"]} == {r["id"] for r in data["reload"]["after"]}
    decision = data["decision"]
    before, restored = decision["before"], decision["restored"]
    assert before["decision_id"] == restored["decision_id"] == decision["current"]["decision_id"]
    assert (
        before["fingerprint"] == restored["fingerprint"] == decision["owner_command"]["fingerprint"]
    )
    assert (
        before["state_revision"]
        == restored["state_revision"]
        == decision["owner_command"]["expected_revision"]
        == 0
    )
    assert decision["owner_command"]["answer"] == "DENY_TEST_SHARE_SIMULATION"
    assert decision["owner_receipt"]["state_revision"] == 1
    assert (
        decision["current"]["delivery_state"] == "acknowledged"
        and decision["current"]["state_revision"] == 3
    )
    assert (
        decision["native_completed_event"]["data"]["requestId"]
        == decision["current"]["native_decision_id"]
    )
    assert decision["owner_retry"]["status"] == 200 and decision["owner_retry"]["body"]["replayed"]
    assert decision["tool_to_owner"]["status"] == 401
    assert all(
        r["status"] == 403 and r["body"]["error"]["code"] == "scope_denied"
        for r in decision["scope_rejections"]
    )


def test_usage_and_actual_transport_fault_are_not_invented_ack_loss_or_success():
    data = report()
    assert any(
        v["actor"]["kind"] == "subagent" and v["model_id"] == "gpt-6-luna" for v in data["usage"]
    )
    assert all(v["input_tokens"] > 0 and v["output_tokens"] > 0 for v in data["usage"])
    assert all(v["cost_unit"] is None for v in data["usage"])
    fault = data["genuine_transport_fault"]
    assert fault["action"]["operation"] == "close actual SDK-owned stdin pipe"
    assert fault["accepted_before_cut"]["state"] == "acknowledged"
    assert (
        fault["after"]["state"] == "outcome_uncertain" and fault["work_state"] == "awaiting_resume"
    )
    assert fault["accepted_before_cut"]["native_message_id"] == fault["after"]["native_message_id"]
    assert fault["original_send_count"] == 1
    assert not fault["native_ack_loss_claimed"] and not data["true_missing_native_ack_fault_tested"]
    assert data["milestone_status"].startswith("BLOCKED")
    assert data["resume"]["continue_failure"]["status"] == 409
    assert data["resume"]["continue_failure"]["command"]["work_id"] is None
    assert (
        data["resume"]["interrupted_reconcile_failure"]["body"]["error"]["code"]
        == "revision_conflict"
    )
    assert not data["owned_group_or_root_argument_leftovers"]
    assert not any(p["proc_exists_after_cleanup"] for p in data["owned_processes"])


def test_export_contains_no_pairing_cookie_csrf_or_tool_bearer_values():
    for path in EVIDENCE.glob("*.json"):
        text = path.read_text()
        for excluded in (
            '"pairing_code"',
            '"csrf_token"',
            '"pair_url"',
            '"token":',
            "Bearer ",
            "#pair=",
        ):
            assert excluded not in text, (path.name, excluded)
