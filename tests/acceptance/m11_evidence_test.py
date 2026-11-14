"""Integrity of retained REAL M11 effects. Passing these does not make M11 complete."""

import base64
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree

from proscenium.contracts import validate

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m11"


def read(name):
    return json.loads((EVIDENCE / name).read_text())


def test_real_required_model_child_consumed_workflow():
    invocations = read("invocations.json")
    children = [row for row in invocations if row.get("kind") == "child"]
    assert len(children) == 2 and all(
        row["model_id"] == "gpt-6.1-sol" and row["name"] == "unity-specialist" for row in children
    )
    workflow = next(row for row in invocations if row.get("name") == "run_unity_workflow")
    assert (
        workflow["agent_id"] == "48131525-e543-4f0d-bb35-2701ce506366"
        and workflow["state"] == "completed"
    )
    assert workflow["work_id"] == read("native-jobs.json")["m11-import-job"]["job"]["work_id"]


def test_actual_native_admission_precedes_own_supervisor_spawn():
    invocations = read("native-invocations.json")
    assert len(invocations) == 3
    for row in invocations:
        job = row["job_before_supervisor_spawn"]
        validate("JobRecord", job)
        assert job["state"] == "starting" and job["attempt_id"] == row["request"]["attempt_id"]
        assert row["memory"]["MemAvailable"] >= 4 * 1024**3
    jobs = read("native-jobs.json")
    assert all(
        row["job"]["state"] == "succeeded"
        and row["job"]["native_exit_code"] == 0
        and row["remaining_processes"] is False
        for row in jobs.values()
    )


def test_source_pin_units_axes_and_finite_native_geometry():
    source = read("source-and-geometry.json")
    assert all(source["source_unchanged"].values())
    assert source["version"]["version_id"] == "m11-source-v1"
    assert source["units"] == "metres" and source["axes"] == "XZY"
    assert len(source["objects"]) == 4 and sum(o["triangles"] for o in source["objects"]) == 48
    floor = next(o for o in source["objects"] if o["instance_id"] == "instance-floor")
    assert floor["bounds_max"] == [3, 0, 3]


def test_saved_scene_has_real_all_polling_playmode_proof_not_injected_inputs():
    report = ElementTree.parse(EVIDENCE / "m11-playmode-job/playmode.xml").getroot()
    assert (
        report.attrib["result"] == "Passed"
        and report.attrib["passed"] == "1"
        and report.attrib["failed"] == "0"
    )
    case = report.find(".//test-case")
    assert case is not None
    assert (
        case.attrib["methodname"] == "F05_KeyboardMousePolling_RealController_BothCameraProjections"
    )
    manifest = read("manifest.json")
    for filename, key in [
        ("executed-controller.cs.txt", "controller_sha256"),
        ("executed-playmode-test.cs.txt", "test_sha256"),
    ]:
        assert hashlib.sha256((EVIDENCE / filename).read_bytes()).hexdigest() == manifest[key]
    assert (
        manifest["test_sha256"]
        == "0616357329be3770268340f93331cb89758d7ca36f00b29653554576d6f7ed29"
    )
    metrics = (EVIDENCE / "m11-playmode-job/unity-result.json.metrics.txt").read_text()
    for camera in ("perspective", "orthographic"):
        assert (
            f"projection={camera} input=Keyboard.current/Mouse.current->Update injected=False movement_m=3 floor_error_m=0.0199999213 wall_penetration_m=0 reset_error_m=0"
            in metrics
        )
        for key in "WASD":
            assert f"projection={camera} key={key} movement_m=0.666666" in metrics
    assert "test_settings_restored=True" in metrics and "physical_OS_input=not_tested" in metrics


def test_original_retry_and_real_repeat_import_preserve_identity():
    replay = read("original-replay.json")["result"]
    assert replay["before"] == replay["after"]
    assert replay["before"]["unity_claims"] == 1 and replay["before"]["exports"] == 1
    assert replay["before"]["ids"]["attempts"] == ["attempt-ca32d643-7017-417e-857b-195d202e4f7a"]
    guids = (EVIDENCE / "m11-repeat-proof-job/unity-result.json.guids.txt").read_text().splitlines()
    assert len(guids) >= 12 and all(line.split()[1] == line.split()[2] for line in guids)
    saved = read("saved-scene.json")
    content = base64.b64decode(saved["content"])
    assert (
        hashlib.sha256(content).hexdigest()
        == saved["sha256"]
        == read("manifest.json")["scene_sha256"]
    )
    scene = content.decode("utf-8")
    assert scene.count("m_Name: Proscenium f4ecf3b8541adc9031e22d92") == 1
    assert scene.count("m_Name: Unrelated acceptance sentinel") == 1


def test_browser_disconnect_did_not_own_import_and_status_is_honest():
    disconnected = read("browser-disconnect.json")
    assert disconnected["job"]["state"] in {"starting", "running"}
    observed = read("import-observed.json")
    assert observed["at"] * 1000 >= disconnected["at"] and observed["job"]["state"] == "succeeded"
    assert read("m11-import-job/unity-result.json")["validation"]["status"] == "not_run"
    assert read("m11-playmode-job/unity-result.json")["validation"]["status"] == "passed"


def test_publication_failure_is_not_relabelled_as_milestone_completion():
    failure = read("publication-failure.json")
    assert not failure["unity_publication_present"]
    assert failure["checkpoint_commit"] == "3297ae647200270ebdc102630e7cfbfce2f4bfd1"
    assert read("manifest.json")["status"] == "partial_blocked"
    assert all(
        version["kind"] != "unity_result" for version in read("public-catalogue.json")["versions"]
    )
