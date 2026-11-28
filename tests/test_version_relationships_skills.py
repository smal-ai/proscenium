"""Keep the offline authoring guidance aligned with the published pin contract."""

from pathlib import Path

import pytest
from proscenium.copilot.native import SHARED_INSTRUCTIONS

SKILLS = Path(__file__).resolve().parents[1] / "agent" / "skills"


def skill(name, filename="SKILL.md"):
    return " ".join((SKILLS / name / filename).read_text().split())


@pytest.mark.parametrize("filename", ["SKILL.md", "templates.md"])
def test_storyboard_separates_local_pins_and_board_dependencies(filename):
    text = skill("storyboard", filename)
    for guidance in (
        "Shot.planned_assets",
        'published `kind: "asset"` `VersionRef`s',
        "unrecorded",
        "`[]` when no pins are declared",
        "`motion_beats` prose",
        "`SourceClosure.dependencies`",
        "deduplicated union",
        "bound `scene_version`s",
        "shot-local pins into `asset_dependencies`",
        "`derived_from`",
        "same-version cycle",
    ):
        assert guidance in text


@pytest.mark.parametrize("name", ["blender-authoring", "mpfb2"])
def test_blender_pins_and_pre_edit_guard(name):
    text = skill(name)
    if name == "blender-authoring":
        guard = text.split("3. Before mutation", 1)[1].split("4. Assign", 1)[0]
        assert "6. When authoring a reusable character or performance, publish the clothed" in text
    else:
        guard = text.split("## Pre-flight", 1)[1].split("## Character authoring", 1)[0]
    for guidance in (
        "Before any interactive mutation",
        "actual linked library files Blender resolves",
        "hashes of any selected published origin, current or historical",
        "Even current pins can differ from unpublished working edits",
        "hashes differ or cannot be verified, stop before editing",
        "Never overwrite the working file, reset Git or edit a sealed snapshot",
    ):
        assert guidance in guard
    for guidance in (
        "weighted character once with packed textures",
        "optional `character` tag",
        "named Blender Actions",
        "fake-user or NLA-strip retention, verified across save/reopen",
        "Blender Library Overrides",
        "`proscenium_object_id`",
        "`proscenium_instance_id`",
        "portable `//...`",
        "never absolute or `.proscenium/snapshots/...`",
        "include_sources=[pinned producer VersionRefs]",
        "complete replacement `include_sources` set",
        "expected_head",
        "`source_commit`",
        "`expected_current_version_id`",
    ):
        assert guidance in text


@pytest.mark.parametrize("name", ["mixamo", "research"])
def test_motion_sources_are_not_rendered_video(name):
    text = skill(name)
    for guidance in (
        "motion-only FBX",
        "retargeted Blender Action",
        "runtime-linked library (`include_sources`)",
        "rendered video",
        "raw FBX at render time",
        "motion UUIDs",
        "rig/rest pose, fps, frame range, loop and root-motion choice",
        "upload approval",
        "attribution",
        "not validation",
    ):
        assert guidance in text


def test_tags_and_unity_boundaries():
    hero = skill("hero-assets")
    assert (
        "`character`, `motion` and `wardrobe` are optional searchable asset tags, not item kinds"
        in hero
    )
    assert (
        "`Shot.planned_assets` in a later storyboard revision without mutating earlier boards"
        in hero
    )
    unity = skill("unity")
    assert "evaluated meshes only" in unity
    assert "no Humanoid Avatar, skinning, animation transfer or Action switching" in unity


def test_native_routes_via_existing_host_tools():
    assert (
        "Optional Shot.planned_assets pins exact shot-local asset versions" in SHARED_INSTRUCTIONS
    )
    assert (
        "closure dependencies alongside board-wide assets and bound scenes" in SHARED_INSTRUCTIONS
    )
    assert (
        "Use checkpoint_sources include_sources for explicit pinned source composition"
        in SHARED_INSTRUCTIONS
    )
    assert "not model-side closure flattening" in SHARED_INSTRUCTIONS
    assert "owner GET via borrowed credentials or a new always-on graph tool" in SHARED_INSTRUCTIONS


def test_native_replies_cite_exact_project_ids_in_backticks():
    assert (
        "When citing project items, published versions, media artefacts or VLM reviews "
        "in owner-facing replies, wrap their exact ID in backticks "
        "(item_id, version_id, artifact_id or review_id) so Studio can link them."
    ) in SHARED_INSTRUCTIONS.splitlines()
