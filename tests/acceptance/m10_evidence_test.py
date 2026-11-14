"""Read retained M10 partial evidence. Never reruns a model, browser or native app."""

import hashlib
import json
from pathlib import Path

from proscenium.contracts import validate

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m10"


def load(name):
    return json.loads((EVIDENCE / name).read_text())


def test_m10_does_not_claim_missing_revision_or_milestone_completion():
    summary = load("summary.json")
    assert summary["overall"] == "BLOCKED"
    assert {key: value["status"] for key, value in summary["criteria"].items()} == {
        "1": "PASS",
        "2": "PASS",
        "3": "REMAINING",
        "4": "PASS",
        "5": "REMAINING",
    }
    assert summary["cloud_roots"] == 1 and summary["native_children"] == 0
    assert summary["native_authoring_processes"] == summary["native_readbacks"] == 0
    assert summary["proxy_exports"] == 1 and summary["cpu_stills"] == 0
    assert summary["root_elapsed_seconds"] < 180
    assert summary["model_authored_script"]["executed"] is False
    assert summary["native_request"]["receipt"]["state"] == "outcome_uncertain"
    assert summary["native_request"]["receipt"]["detail_code"] == "attribution_unavailable"


def test_actual_fullhd_software_proxy_orbit_and_triangle_pick():
    summary, inspection = load("summary.json"), load("inspection.json")
    assert inspection["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    assert "SwiftShader" in inspection["renderer"]
    assert inspection["orbit_before_sha256"] != inspection["orbit_after_sha256"]
    assert inspection["selected_title"] == "e04-object-001 / e04-instance-001"
    assert inspection["selected_aria_pressed"] == "true"
    mapping = inspection["mapping"]
    validate("ProxyMapping", mapping)
    assert mapping["nodes"][0]["object_id"] == "e04-object-001"
    assert mapping["nodes"][0]["instance_id"] == "e04-instance-001"
    assert mapping["version_id"] == "m10-asset-v1"
    assert summary["proxy_job"]["state"] == "succeeded"
    assert summary["proxy_job"]["native_exit_code"] == 0
    assert summary["proxy_job"]["attempt_number"] == 1
    assert summary["proxy_profile"]["spec"]["purpose"] == "proxy"


def test_original_e04_bytes_refs_budget_and_prompt_are_unchanged():
    fixture, cleanup, summary = load("fixture.json"), load("cleanup.json"), load("summary.json")
    assert fixture["original_before"] == fixture["original_after"] == cleanup["original_e04_after"]
    original = cleanup["original_e04_after"]
    assert original["reservations"] == [
        ["e04-mesh-job", "e04-generation-task", "e04-generation-task", "consumed"]
    ]
    assert original["prompt_id"] == "ed610c36-0fbc-45f6-8367-d3a1b56fa329"
    assert summary["new_generation_reservations"] == []
    assert summary["original_e04_preserved"] and not summary["independent_services_mutated"]
    assert fixture["copilot_association_before_real_sdk"] is None
    assert summary["native_association"][0]["session_id"] != "fixture-import-not-an-sdk-session"
    assert cleanup["server_closed"] and cleanup["browser_closed"]
    assert not cleanup["remaining_observed_owned_processes"]
    assert not summary["remaining_owned_processes"]


def test_duplicate_permission_is_not_an_owner_denial_or_new_authoring_attempt():
    defect = load("permission-defect.json")
    a, b = defect["requests"]
    assert a["request_id"] != b["request_id"]
    assert a["permission_request"] == b["permission_request"]
    assert [a["prompt_kind"], b["prompt_kind"]] == ["path", "commands"]
    assert a["completion"]["kind"] == "approved"
    assert b["completion"]["kind"] == "denied-interactively-by-user"
    assert defect["human_denial"] is False


def test_public_export_contains_no_private_assistant_reasoning():
    events = load("native-events-redacted.json")["events"]
    assert not [event for event in events if event["type"].startswith("assistant.")]
    start = next(event for event in events if event["type"] == "session.start")
    assert start["data"]["selectedModel"] == "gpt-6.1-sol"
    assert len([event for event in events if event["type"] == "user.message"]) == 1
    assert not [event for event in events if event["type"].startswith("subagent.")]
    user = next(event for event in events if event["type"] == "user.message")
    context = user["extension_context"][0]
    assert context["selection"]["version_id"] == "m10-asset-v1"
    assert context["selection"]["selected_objects"] == []


def test_retained_actual_sources_and_native_proxy_hashes():
    for row in load("retained-files.json")["files"]:
        path = Path(row["path"])
        if not path.exists():
            # Public evidence can be transported without private runtime histories.
            continue
        assert path.stat().st_size == row["bytes"]
        with path.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == row["sha256"]
