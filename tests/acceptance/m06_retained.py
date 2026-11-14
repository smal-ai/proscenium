"""Offline verification of retained real M06 evidence. Never sends a model request."""

import hashlib
import importlib.util
import json
from pathlib import Path

from PIL import Image
from proscenium.contracts import canonical, validate

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence/m06"


def read(name):
    return json.loads((EVIDENCE / name).read_text())


def test_real_sdk_allocation_and_no_execution():
    identity = read("identities.json")
    proof = read("source-verification.json")
    assert identity["sdk"] == "1.0.34" and identity["model"] == "gpt-6.1-sol"
    assert identity["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    assert proof["native_roots"] == 2 and proof["native_children"] == 0
    assert all(ms <= 180000 for ms in proof["root_durations_ms"])
    assert proof["jobs"] == proof["attempts"] == 0
    assert len(read("public-final.json")["requests"]["items"]) == 2
    assert read("public-final.json")["jobs"]["items"] == []


def test_authored_shape_stable_ids_and_targeted_impact():
    old, new = (read(f"authored-m06-kite-board-v{n}.json") for n in (1, 2))
    for plan in (old, new):
        validate("AuthoredShotPlan", plan)
        assert plan["plan_id"] == "kite-flight"
        assert len(plan["shots"]) == 2 and plan["unresolved"]
        for shot in plan["shots"]:
            assert [f["role"] for f in shot["frames"]] == ["start", "intermediate", "end"]
            assert shot["scene_version"] is shot["source_range"] is shot["fps"] is None
            assert "3 seconds" in shot["intent"] and "24 fps" in shot["intent"]
    assert canonical(old["shots"][1]) == canonical(new["shots"][1])
    assert old["asset_dependencies"] == new["asset_dependencies"]
    assert old["treatment"] == new["treatment"]
    for read_model in read("public-final.json")["plans"].values():
        validate("ShotPlan", read_model)
    for a, b in zip(old["shots"], new["shots"], strict=True):
        assert [(f["frame_id"], f["role"], f["artifact_id"]) for f in a["frames"]] == [
            (f["frame_id"], f["role"], f["artifact_id"]) for f in b["frames"]
        ]
    spec = importlib.util.spec_from_file_location(
        "m06_plan_tools", ROOT / "agent/skills/storyboard/plan_tools.py"
    )
    assert spec and spec.loader
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)
    assert tools.compare(old, new) == read("dependency-impact.json")["comparison"]


def test_actual_publication_receipts_and_native_invocation_join():
    publications = read("publications.json")
    invocations = {i["invocation_id"]: i for i in read("invocations.json")}
    native = {i["toolCallId"]: i for i in read("native-tool-invocations.json")}
    assert len(publications) == 2
    assert len(read("checkpoints.json")) == 2
    for publication in publications:
        validate("PublicationSubmit", publication["submission"])
        validate("PublicationReceipt", publication["receipt"])
        invocation = invocations[publication["invocation_id"]]
        assert invocation["state"] == "completed" and invocation["name"] == "publish_outputs"
        assert native[invocation["native_id"]]["toolName"] == "publish_outputs"
        assert all(not item["preview_requests"] for item in publication["submission"]["items"])
    assert not {i["toolName"] for i in native.values()} & {
        "task",
        "request_preview",
        "submit_generation",
        "request_vlm_review",
        "admit_job",
    }


def test_actual_selection_slot_supersede_withdraw_and_reload():
    commands = read("selection-commands.json")
    assert [c["expected_revision"] for c in commands] == [0, 1, 2, 3]
    assert [c["action"] for c in commands] == ["select", "select", "withdraw", "select"]
    for command in commands:
        validate("SelectionSave", command)
        assert command["slot"] == commands[0]["slot"]
        assert command["selection_id"] == commands[0]["selection_id"]
    final = read("public-final.json")
    before = read("before-revision.json")
    assert final["selections"] == before["selections"]
    selection = final["selections"]["items"][0]
    validate("SelectionRecord", selection)
    assert selection["state"] == "selected" and selection["state_revision"] == 4
    assert selection["slot"]["frame_id"] == "kite-rise-start"
    assert selection["slot"]["storyboard_version"]["version_id"] == "m06-kite-board-v1"
    assert final["plans"]["m06-kite-board-v1"]["shots"][0]["frames"][0]["owner_selected"]
    assert not final["plans"]["m06-kite-board-v2"]["shots"][0]["frames"][0]["owner_selected"]


def test_reference_bytes_and_real_pin_annotation_are_preserved():
    reference = read("reference-upload.json")
    content = (EVIDENCE / "owner-reference.png").read_bytes()
    assert hashlib.sha256(content).hexdigest() == reference["sha256"]
    assert Image.open(EVIDENCE / "owner-reference.png").size == (640, 360)
    final = read("public-final.json")
    assert final["annotations"] == read("before-revision.json")["annotations"]
    annotation = final["annotations"]["items"][0]
    validate("AnnotationSnapshot", annotation)
    assert annotation["saved_revision"] == 1 and annotation["target"]["shot_id"] == "kite-rise"
    assert annotation["target"]["displayed_sha256"] == reference["sha256"]
    assert annotation["markups"][0]["kind"] == "pin"
    assert annotation["markup_artifact_id"] != annotation["target"]["artifact_id"]


def test_role_context_and_exact_revision_queue_context():
    views = read("role-contexts.json")
    assert len(views) == 4
    for view in views:
        target = view["context"]["target"]
        validate("FrameTarget", target)
        assert target["shot_id"] == view["shotId"] and target["origin"] == "reference"
        assert f"{view['frameId']} / {view['role']}" in view["context"]["attachments"][0]["use"]
    final = read("public-final.json")
    request = next(
        r
        for r in final["requests"]["items"]
        if "Owner planning instructions:" in r["submission"]["text"]
    )
    context = final["contexts"][request["submission"]["context_id"]]
    validate("ContextSnapshot", context)
    assert "kite-rise-middle / intermediate" in context["attachments"][0]["use"]
    assert context["target"]["shot_id"] == "kite-rise"


def test_manifest_and_no_exported_authentication():
    manifest = read("manifest.json")
    for name, identity in manifest.items():
        data = (EVIDENCE / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == identity["sha256"]
        assert len(data) == identity["bytes"]
        if name.endswith(".json"):
            text = data.decode()
            for secret in ("#pair=", '"csrf_token":', "github_pat_", "ghp_", "Bearer "):
                assert secret not in text


def test_cleanup_reopen_and_truthful_remaining_result():
    cleanup = read("cleanup.json")
    assert cleanup["server"]["reaped"] and cleanup["browser"]["reaped"]
    assert not cleanup["native"]["live_original"]
    reopen = read("reopen-observation.json")
    assert reopen["requests_before"] == reopen["requests_after"] == 2
    assert reopen["jobs"] == 0 and not reopen["automatic_resume_sent"]
    defect = read("known-review-defect.json")
    assert defect["status"] == "remaining"
    assert defect["observed"]["context"]["target"] is None
