"""Retained actual evidence checks, not another native/model acceptance run."""

import hashlib
import importlib
import json
from pathlib import Path

from copilot.tools import ToolResult
from proscenium.contracts import validate

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence/m11-final"


def load(name):
    return json.loads((EVIDENCE / name).read_text())


def test_export_hashes():
    for name, digest in load("files.json").items():
        assert hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest() == digest


def test_strict_partial_outcome_and_single_allocation():
    result = load("manifest.json")
    assert result["checkpoint_effect"] and not result["unity_publication"]
    assert result["publication_id"] is None and result["version_id"] is None
    assert result["sdk_sends"] == 1
    for key in (
        "new_queue_requests",
        "new_children",
        "new_unity",
        "new_blender",
        "new_exports",
        "new_provider_work",
    ):
        assert result[key] == 0
    assert result["publication_replay"] == "not_run_no_publication"
    assert result["checkpoint_replay"] == "not_run_attempt_stopped_at_new_permission"


def test_real_checkpoint_and_exact_source_hashes():
    proof, preservation = load("checkpoint.json"), load("preservation.json")
    receipt = validate("CheckpointReceipt", proof["receipt"])
    assert receipt["source_commit"] == "8d49caaee980d18a140111e81b15a46c71e229ce"
    assert receipt["parent_commit"] == "3297ae647200270ebdc102630e7cfbfce2f4bfd1"
    assert proof["verified_git_blobs"] == len(receipt["files"])
    assert {f["path"]: f["sha256"] for f in receipt["files"]} == preservation["source_files"]
    assert proof["invocation"]["model_id"] == "gpt-6.1-sol"
    assert proof["invocation"]["native_id"] == "call_0KgFTlXlYrinFSJ3axs84711"
    assert proof["invocation"]["state"] == "completed"
    assert preservation["source_files_identical"] and preservation["old_reports_identical"]
    assert preservation["original_bindings_unchanged"] and preservation["old_decisions_unchanged"]


def test_original_work_and_actual_ui_resume():
    manifest, continuation = load("manifest.json"), load("continuation.json")
    assert continuation["send"]["state"] == "acknowledged"
    assert continuation["continuation"]["request_id"] == manifest["request_id"]
    assert continuation["continuation"]["work_id"] == manifest["work_id"]
    assert continuation["send"]["session_id"] == manifest["session_id"]
    assert continuation["work"]["state"] == "awaiting_user"
    assert len(continuation["live_invocations"]) == 1
    assert continuation["live_invocations"][0]["native_id"] == "call_XcQ3lvQAC0HOmPxz3LBd6jzc"
    assert continuation["live_invocations"][0]["state"] == "running"
    for name in ("owner-reconcile.json", "owner-continue.json"):
        assert load(name)["status"] == 202
    assert load("public-preflight.json")["viewport"] == dict(width=1920, height=1080, dpr=1)
    assert (
        load("owner-continue.json")["body"]["operation_id"] == continuation["send"]["operation_id"]
    )


def test_new_blocker_is_a_read_not_an_installation():
    proof = load("permission-blocker.json")
    request, decision = proof["request"], proof["decision"]
    assert request["kind"] == "read" and request["path"].endswith("/tool-install.json")
    assert request["toolCallId"] == "call_XcQ3lvQAC0HOmPxz3LBd6jzc"
    assert decision["state"] == "pending" and decision["delivery_state"] == "not_sent"
    assert decision["response"] is None and decision["resolved_by"] is None
    assert proof["owner_response_supplied"] is False


def test_honest_validation_and_no_catalogue_publication():
    proof = load("validation-separation.json")
    assert proof["generic"]["validation"]["status"] == "not_run"
    assert proof["operator_attestation"]["unity"]["validation"]["status"] == "passed"
    assert all(
        item["kind"] != "unity_result" for item in load("public-progress.json")["items"]["items"]
    )


def test_headroom_cleanup_and_private_snapshots():
    cleanup = load("cleanup.json")
    assert cleanup["sdk_sends"] == 1 and cleanup["recorded_identities_gone"]
    assert cleanup["browser_reaped"] and cleanup["services_closed"]
    assert cleanup["stack_seconds"] < 300
    assert load("send-headroom.json")["memory"]["MemAvailable"] >= 4 * 1024**3
    assert set(load("private-snapshots.json")) == {"before", "blocked", "after"}


def test_sdk_tool_dataclass_serialisation(monkeypatch):
    # Post-attempt observer correction only. The blocked run never entered replay.
    monkeypatch.syspath_prepend(str(ROOT / "tests/native/unity"))
    driver = importlib.import_module("m11_final")
    assert driver.tool_wire(ToolResult(text_result_for_llm="{}"))["text_result_for_llm"] == "{}"
