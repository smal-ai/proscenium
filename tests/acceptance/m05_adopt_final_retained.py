"""Final saved/publication proof. All tests are offline and execute no Blender code."""

import ast
import hashlib
import json
from pathlib import Path

import pytest
from proscenium.contracts import canonical, validate

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence/m05-adoption/final"


def read(name):
    return json.loads((EVIDENCE / name).read_text())


def test_preflight_and_actual_model_safe_function(tmp_path):
    review = read("static-review.json")
    assert review["source_lines"] == 205 and review["no_native_or_model_call"]
    assert len(review["canonical_helper_cases_passed"]) == 4
    source = (EVIDENCE / "model-authored-history_adopt_final.py.txt").read_text()
    safe = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "safe"
    )
    # Execute only the pure path function, never model-authored top-level/BPy code.
    project = tmp_path / "project"
    (project / "assets").mkdir(parents=True)
    (project / "scenes/fixture").mkdir(parents=True)
    target = project / "assets/asset.blend"
    target.write_bytes(b"pure helper fixture, not Blender media")
    (tmp_path / "outside").write_bytes(b"outside project")
    namespace = {"Path": Path, "root": project}
    exec(compile(ast.Module(body=[safe], type_ignores=[]), "<pure-model-safe>", "exec"), namespace)
    assert namespace["safe"]("scenes/fixture/../../assets/asset.blend") == target
    with pytest.raises(FileNotFoundError):
        namespace["safe"]("missing.blend")
    with pytest.raises(AssertionError):
        namespace["safe"]("../outside")
    (project / "alias").symlink_to(project / "assets", target_is_directory=True)
    with pytest.raises(AssertionError):
        namespace["safe"]("alias/asset.blend")
    tree = ast.parse(source)
    save = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "save_as_mainfile"
    )
    assert any(
        keyword.arg == "compress"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is False
        for keyword in save.keywords
    )
    assert '"native-final-allocation" not in p.parts' in source


def test_one_native_model_request_and_one_successful_guarded_process():
    verification, terminal = read("verification.json"), read("native-terminal.json")
    assert verification["strict_result"] == "pass_scoped_M05_journey"
    assert verification["new_model_sends"] == 1 and verification["elapsed_ms"] <= 180000
    assert verification["new_native_processes"] == 1 and verification["new_children"] == 0
    assert terminal["exit_code"] == 0 and terminal["reaped"]
    assert terminal["available_bytes_before"] >= 4 * 1024**3
    assert terminal["command"][terminal["command"].index("--threads") + 1] == "4"
    calls = read("native-tool-invocations.json")
    for operation in ("checkpoint_sources", "publish_outputs"):
        assert sum(call["toolName"] == operation for call in calls) == 1
    assert not {call["toolName"] for call in calls} & {
        "task",
        "request_preview",
        "submit_generation",
        "request_vlm_review",
    }


def test_actual_saved_scene_and_exact_current_request_report_ids():
    report, request = read("history-adoption-final.json"), read("single-request.json")["request"]
    assert report["status"] == "passed"
    assert report["request_id"] == request["request_id"]
    assert report["work_id"] == request["receipt"]["work_id"]
    assert report["context_id"] == request["submission"]["context_id"]
    adopted = report["adoption"]
    assert adopted["state_preserved"] and adopted["state_before"] == adopted["state_after"]
    assert adopted["dependencies"] == adopted["saved_dependency_recheck"]
    assert report["protected_before"] == report["protected_after"]
    assert {obj["ids"]["proscenium_instance_id"] for obj in adopted["instances"]} == {
        "instance-blue",
        "instance-renamed",
    }
    for obj in adopted["instances"]:
        assert obj["dimensions_m"] == pytest.approx([1, 1, 1.5], abs=1e-5)
        assert obj["materials"] == ["Red"]
        assert obj["ids"]["proscenium_object_id"] == "object-cube-source"
    for library in adopted["dependencies"]["libraries"]:
        assert library["filepath"] == "//../../assets/cube/asset.blend"
        assert ".proscenium/snapshots" not in library["filepath"]


def test_real_checkpoint_and_publication_pin_asset_v3_without_mutating_old_versions():
    publication, checkpoint = read("publication.json"), read("checkpoint.json")
    validate("PublicationSubmit", publication["submission"])
    validate("PublicationReceipt", publication["receipt"])
    item = publication["submission"]["items"][0]
    assert len(publication["submission"]["items"]) == 1
    assert item["item_id"] == "scene-main" and item["version_id"] == "scene-main-v2"
    assert item["expected_current_version_id"] == "scene-main-v1" and item["preview_requests"] == []
    assert item["source"]["dependencies"] == [
        {"item_id": "asset-cube", "version_id": "asset-cube-v3"}
    ]
    assert (
        hashlib.sha256(
            canonical({k: v for k, v in item["source"].items() if k != "digest"})
        ).hexdigest()
        == item["source"]["digest"]
    )
    assert checkpoint["receipt"]["source_commit"] == item["source_commit"]
    assert {entry["path"] for entry in checkpoint["receipt"]["files"]} == {
        "scenes/fixture/scene.blend",
        "tools/history_adopt_final.py",
        "reports/history-adoption-final.json",
    }
    prior, final = read("public-before.json"), read("public-final.json")
    for key in ("jobs", "selections"):
        assert prior[key] == final[key]
    for item_id in ("asset-cube", "asset-companion", "scene-alternate"):
        assert prior["versions"][item_id] == final["versions"][item_id]
    old_main = prior["versions"]["scene-main"]["items"][0]
    assert old_main in final["versions"]["scene-main"]["items"]
    verification = read("verification.json")
    assert verification["adoption_saved"] and verification["adoption_published"]
    assert verification["index_before"] == verification["index_after"]
    assert all(
        value["before"] == value["after"] for value in verification["protected_files"].values()
    )


def test_actual_ui_context_and_no_automatic_send_on_reload():
    context = read("same-current-context.json")
    assert context["before"] == context["after"] and context["exactly_preserved"]
    assert context["after"]["context"]["target"]["version_id"] == "asset-cube-v3"
    assert context["after"]["context"]["target"]["origin"] == "blender_render"
    assert context["after"]["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    observation = read("reopen-observation.json")
    assert observation["requests_before"] == observation["requests_after"] == 5
    assert observation["jobs_unchanged"] and not observation["automatic_resume_sent"]


def test_manifest_old_reports_untouched_and_owned_cleanup():
    for folder in (EVIDENCE, ROOT / "docs/evidence/m05-adoption", ROOT / "docs/evidence/m05"):
        manifest = json.loads((folder / "manifest.json").read_text())
        for name, identity in manifest.items():
            data = (folder / name).read_bytes()
            assert hashlib.sha256(data).hexdigest() == identity["sha256"]
            assert len(data) == identity["bytes"]
    cleanup = read("cleanup.json")
    assert cleanup["browser_reaped"] and cleanup["native_proc_absent"]
