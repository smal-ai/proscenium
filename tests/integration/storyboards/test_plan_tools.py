"""Synthetic offline authored-plan checks. No application/model execution."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "agent/skills/storyboard/plan_tools.py"
spec = importlib.util.spec_from_file_location("plan_tools", HELPER)
assert spec and spec.loader
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


@pytest.fixture
def plan():
    return {
        "schema_version": "1.0",
        "plan_id": "garden-plan",
        "project_id": "garden-project",
        "treatment": "Watercolour garden study",
        "brief_artifact_ids": ["brief-artifact"],
        "asset_dependencies": [{"item_id": "flower-asset", "version_id": "flower-version"}],
        "shots": [
            {
                "shot_id": identity,
                "intent": "Follow a leaf",
                "camera_intent": "Slow arc",
                "motion_beats": ["Leaf turns"],
                "scene_version": {"item_id": "garden-scene", "version_id": f"{identity}-scene"},
                "source_range": {"start": 1, "end": 48},
                "fps": {"numerator": 24, "denominator": 1},
                "frames": [
                    {
                        "frame_id": f"{identity}-{role}",
                        "role": role,
                        "composition": f"Leaf {role}",
                        "artifact_id": f"{identity}-{role}-image" if role == "start" else None,
                        "state": "available" if role == "start" else "missing",
                    }
                    for role in ("start", "intermediate", "end")
                ],
            }
            for identity in ("leaf-arc", "leaf-rest")
        ],
        "unresolved": ["End image missing"],
    }


def write(tmp_path, plan):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return str(path)


def test_exact_authored_contract_and_read_only_cli(tmp_path, plan):
    path = write(tmp_path, plan)
    original = Path(path).read_bytes()
    result = subprocess.run(
        [sys.executable, str(HELPER), "validate", path], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == {"valid": True, "plan_id": "garden-plan", "shot_count": 2}
    assert Path(path).read_bytes() == original
    assert list(tmp_path.iterdir()) == [Path(path)]


@pytest.mark.parametrize("field", ["shot_id", "frame_id"])
def test_missing_identity_rejected(tmp_path, plan, field):
    target = plan["shots"][0] if field == "shot_id" else plan["shots"][0]["frames"][0]
    del target[field]
    with pytest.raises(Exception):
        tools.load(write(tmp_path, plan))


@pytest.mark.parametrize("field", ["shot_id", "frame_id"])
def test_duplicate_identity_rejected(tmp_path, plan, field):
    if field == "shot_id":
        plan["shots"][1][field] = plan["shots"][0][field]
    else:
        plan["shots"][1]["frames"][0][field] = plan["shots"][0]["frames"][0][field]
    with pytest.raises(ValueError, match="Duplicate"):
        tools.load(write(tmp_path, plan))


@pytest.mark.parametrize("field,value", [("owner_selected", True), ("review_seconds", 3)])
def test_no_owner_or_review_pacing_in_authored_frames(tmp_path, plan, field, value):
    plan["shots"][0]["frames"][0][field] = value
    with pytest.raises(Exception):
        tools.load(write(tmp_path, plan))


def test_published_commit_not_authored(tmp_path, plan):
    plan["source_commit"] = "a" * 40
    with pytest.raises(Exception):
        tools.load(write(tmp_path, plan))


def test_targeted_change_preserves_ids_and_reports_only_affected_shot(plan):
    after = copy.deepcopy(plan)
    after["shots"][0]["camera_intent"] = "Overhead arc"
    result = tools.compare(plan, after)
    assert result["changed_shot_ids"] == ["leaf-arc"]
    assert result["stable_plan_id"]
    assert result["removed_frame_ids"] == []
    assert result["downstream_scene_version_ids"] == ["leaf-arc-scene"]
    assert result["downstream_artifact_ids"] == ["leaf-arc-start-image"]
    assert plan["shots"][0]["camera_intent"] == "Slow arc"


def test_reorder_and_shared_dependencies_conservatively_affect_all(plan):
    after = copy.deepcopy(plan)
    after["shots"].reverse()
    assert tools.compare(plan, after)["shot_order_changed"]
    assert tools.impact(plan, {"flower-version"})["affected_shot_ids"] == ["leaf-arc", "leaf-rest"]
    assert tools.impact(plan, {"unrelated"})["affected_shot_ids"] == []


def test_removed_frames_keep_old_downstream_identity(plan):
    after = copy.deepcopy(plan)
    after["shots"][0]["frames"].pop(0)
    result = tools.compare(plan, after)
    assert result["removed_frame_ids"] == ["leaf-arc-start"]
    assert result["downstream_artifact_ids"] == ["leaf-arc-start-image"]


@pytest.mark.parametrize("mutation", ["roles", "range", "available"])
def test_semantic_errors_rejected(tmp_path, plan, mutation):
    shot = plan["shots"][0]
    if mutation == "roles":
        shot["frames"].reverse()
    elif mutation == "range":
        shot["source_range"] = {"start": 4, "end": 1}
    else:
        shot["frames"][0]["artifact_id"] = None
    with pytest.raises(ValueError):
        tools.load(write(tmp_path, plan))


def test_skills_ship_resources_and_no_execution_dependencies():
    storyboard = (ROOT / "agent/skills/storyboard/SKILL.md").read_text()
    research = (ROOT / "agent/skills/research/SKILL.md").read_text()
    assert "AuthoredShotPlan" in storyboard
    assert "never submits generation" in storyboard
    assert "BlendSwap" in research and "Unknown values are JSON null" in research
    helper = HELPER.read_text()
    for forbidden in ("subprocess", "requests", "urllib", "comfy", "blender", "localhost"):
        assert forbidden not in helper.lower()
    assert (ROOT / "agent/skills/research/acquisition.md").is_file()
    assert (ROOT / "agent/skills/research/templates.md").is_file()
    assert (ROOT / "agent/skills/storyboard/templates.md").is_file()


def test_maintained_templates_match_shared_contract(tmp_path):
    template = (ROOT / "agent/skills/storyboard/templates.md").read_text()
    authored = json.loads(template.split("```json\n", 1)[1].split("```", 1)[0])
    assert tools.load(write(tmp_path, authored)) == authored
    research = (ROOT / "agent/skills/research/templates.md").read_text()
    provenance = json.loads(research.split("```json\n", 1)[1].split("```", 1)[0])
    tools.validate("Provenance", provenance)
    assert provenance == {"source_url": None, "creator": None, "licence": None}
