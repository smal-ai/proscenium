"""Pure allocation/helper tests. Never sends a prompt or launches native work."""

import importlib.util
import json
from pathlib import Path

import pytest

DIRECTORY = Path(__file__).resolve().parents[1] / "native/image_to_3d"
spec = importlib.util.spec_from_file_location(
    "m10_complete_author", DIRECTORY / "m10_complete_author.py"
)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.fixture
def allocation(tmp_path):
    script = tmp_path / "revision.py"
    script.write_text("MODEL SCRIPT FIXTURE ONLY")
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    return receipts, script


def test_fresh_guard_admits_one_authoring_claim(allocation):
    receipts, script = allocation
    assert guard.admission(receipts, script) == 1


def test_uncertain_first_claim_cannot_be_retried(allocation):
    receipts, script = allocation
    (receipts / "claim-1.json").write_text("{}")
    with pytest.raises(FileNotFoundError):
        guard.admission(receipts, script)


@pytest.mark.parametrize(
    "outcome",
    [{"exit_code": 0}, {"exit_code": 17, "uncertain": True}, {"exit_code": -9, "timed_out": True}],
)
def test_success_or_uncertainty_never_allows_second_author(allocation, outcome):
    receipts, script = allocation
    (receipts / "claim-1.json").write_text("{}")
    (receipts / "terminal-1.json").write_text(
        json.dumps({**outcome, "script_sha256": guard.sha(script)})
    )
    script.write_text("A REAL REPAIR WOULD STILL NOT AUTHORISE THIS OUTCOME")
    with pytest.raises(ValueError):
        guard.admission(receipts, script)


def test_verified_nonzero_requires_actual_changed_script_and_cannot_allow_third(allocation):
    receipts, script = allocation
    (receipts / "claim-1.json").write_text("{}")
    (receipts / "terminal-1.json").write_text(
        json.dumps({"exit_code": 17, "script_sha256": guard.sha(script)})
    )
    with pytest.raises(ValueError, match="actual model-authored script repair"):
        guard.admission(receipts, script)
    script.write_text("FIXTURE-ONLY CHANGED SCRIPT, NOT PRODUCT AUTHORSHIP")
    assert guard.admission(receipts, script) == 2
    (receipts / "claim-2.json").write_text("{}")
    with pytest.raises(ValueError, match="exhausted"):
        guard.admission(receipts, script)


def test_browser_uses_stable_project_selector_and_single_followup_latch():
    code = (DIRECTORY / "m10_complete_browser.mjs").read_text()
    assert "button.project-trigger" in code
    assert "name:'Choose a project'" not in code
    assert "owner-queue-claimed.json" in code and "{flag:'wx'}" in code
    assert code.count("name:'Queue message',exact:true}).click()") == 1
    assert "resume('continue_work')" not in code
    assert "ui-preflight-ready.json" in code and "send-authorised.json" in code
    assert "source!=='coordinator-explicit-message'" in code


def test_host_enforces_same_session_zero_children_one_send_without_permission_override():
    code = (DIRECTORY / "m10_complete_host.py").read_text().replace('"', "'")
    assert "excluded_tools=['task']" in code and "custom_agents=[]" in code
    assert "session_id == SESSION" in code
    assert "sdk-send-claimed.json').open('x')" in code
    assert "on_permission_request" in code and "'on_permission_request'," not in code
    assert "original_publish(catalogue, caller, body)" in code
    assert "original_send(session, prompt, **kwargs)" in code
