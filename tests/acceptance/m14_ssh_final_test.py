"""Final preflight evidence remains distinct from actual SSH journey evidence."""

import hashlib
import json
import subprocess
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


def test_real_ui_opening_helper_activates_before_waiting_for_composer():
    # No browser/runtime. Execute the exact exported UI-opening helper with spies.
    module = (ROOT / "tests/native/studio/m14_ssh_open.mjs").as_uri()
    script = f"""
    import {{activateImportedProject}} from {json.dumps(module)};
    const calls=[];
    const page={{
      getByRole:(role,options)=>({{click:async()=>calls.push(['click',role,options.name]),waitFor:async()=>calls.push(['wait',role,options.name])}}),
      getByLabel:(label)=>({{selectOption:async value=>calls.push(['select',label,value])}})
    }};
    await activateImportedProject(page,'m13-cinematic');
    console.log(JSON.stringify(calls));
    """
    calls = json.loads(
        subprocess.check_output(["node", "--input-type=module", "-e", script], text=True, cwd=ROOT)
    )
    assert calls == [
        ["click", "button", "Choose a project"],
        ["select", "Saved project", "m13-cinematic"],
        ["click", "button", "Activate selected project"],
        ["wait", "textbox", "Message Copilot"],
    ]


def test_final_allocation_keeps_actual_failure_and_zero_send_audit():
    value = json.loads((ROOT / "docs/evidence/m14-ssh/final-result.json").read_text())
    assert value["status"] == "incomplete"
    assert value["driver_revision"].startswith("c89e322")
    assert value["failure"]["detail"] == "browser_exited_before_admitted.json"
    assert len(value["processes"]) == 4
    assert all(row["pid"] and row["start"] for row in value["processes"])
    assert value["host_1"]["sends"] == 0 and value["host_1"]["forbidden"] == []
    assert value["host_1"]["native_processes"] == []
    assert all(not rows for rows in value["host_1"]["records"].values())
    assert value["host_1"]["gate_closed"]
    assert value["owned_descendants_remaining"] == {} and value["original_m13_byte_preserved"]
    assert value["browser"]["off_origin"] == [] and value["browser"]["browser_closed"]
    assert any(
        row["path"] == "/api/v1/owner/session" and row["status"] == 201
        for row in value["browser"]["responses"]
    )
    for path, digest in value["source_hashes"].items():
        source = subprocess.check_output(
            ["git", "show", f"{value['driver_revision']}:{path}"], cwd=ROOT
        )
        assert hashlib.sha256(source).hexdigest() == digest
