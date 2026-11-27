"""Offline discovery and guidance checks for the optional MPFB2 Blender extension."""

import re
from pathlib import Path
from types import SimpleNamespace

import yaml
from proscenium.configuration import Settings
from proscenium.copilot.capabilities import get_project_capabilities
from proscenium.copilot.native import SHARED_INSTRUCTIONS, SPECIALIST_INSTRUCTIONS

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "agent/skills"


def assert_guidance(text, *phrases):
    text = " ".join(text.split())
    for phrase in phrases:
        assert phrase in text, f"Missing guidance: {phrase}"


def test_project_capabilities_discovers_mpfb2_without_blender_extension(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    services = SimpleNamespace(
        settings=Settings(data_directory=tmp_path, runtime={"worker_model": "fixture"}),
        extension=lambda name: None,
    )

    capabilities = get_project_capabilities(services, "project-one")

    assert capabilities["blender_mcp_configured"] is False
    assert "mpfb2" in capabilities["available_skills"]


def test_mpfb2_skill_frontmatter_and_workflow():
    skill = (SKILLS / "mpfb2/SKILL.md").read_text(encoding="utf-8")
    opening, frontmatter, body = skill.split("---\n", 2)
    assert opening == ""
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "mpfb2"
    assert isinstance(metadata["description"], str) and metadata["description"].strip()

    assert_guidance(
        body,
        "Call `get_project_capabilities` first",
        "`planning_only` permits read-only discovery",
        "Never install Blender extensions, download MakeHuman asset packs, alter Blender preferences",
        "explicit recorded confirmation",
        "bpy.ops.wm.read_factory_settings",
        "mpfb_mod.VERSION",
        'os.path.join(verified_root, "packs")',
        "makehuman_system_assets.json",
        "find_asset_files_matching_pattern([os.path.join(verified_root, section)]",
        'pattern="*.mhmat"',
        'pattern="*.mhclo"',
        "str(path)",
        "Before writing, resolve each `.blend`, `human.<slug>.json` and `.fbx` path",
        "HumanService.create_human(",
        "scale=0.1",
        "Before equipping any body parts or clothes",
        "HumanService.add_builtin_rig(basemesh, rig_name, import_weights=True)",
        'ANIMOPS_PROPERTIES.set_value("call_fbx", False, entity_reference=bpy.context.scene)',
        "bpy.ops.mpfb.reduced_doll()",
        "use_selection=True",
        "remove those exact temporary objects",
        "unused copied mesh and armature datablocks",
        "no `*_reduced` objects",
        "recorded owner approval for the exact file and Adobe Mixamo destination before running `mixamo upload`",
        "bpy.ops.mpfb.map_mixamo()",
        "stable `proscenium_object_id`",
        "unique `proscenium_instance_id` per instance",
        "blender -b --factory-startup",
        "bpy.ops.file.pack_all()",
        "zero unpacked external `FILE` images",
        "Git LFS before `checkpoint_sources`",
        'never `"mpfb"` or `"mpfb2"` in `required_runtime.features`',
        "request_preview",
        'purpose: "proxy"',
        "ready PNG stills with `get_artifact(artifact_id=..., include_image=true)`",
        "For GLB, inspect the proxy receipt and `get_artifact(artifact_id=...)` metadata without `include_image=true`",
        "request_vlm_review",
    )
    prohibition = next(block for block in body.split("\n-") if "Do not call blanket" in block)
    for helper in (
        "update_all_asset_lists",
        "list_mhclo_assets",
        "list_mhmat_assets",
        "get_asset_list",
    ):
        assert f"{helper}()" in prohibition
        assert not re.search(rf"\b{helper}\(", body.replace(prohibition, ""))

    root_gate = next(
        block for block in body.split("\n-") if "Call indirect root-searching" in block
    )
    assert_guidance(
        body,
        "Inspect all four `LocationService` roots before asset or manifest reads",
        "os.path.realpath(LocationService.get_mpfb_data())",
        'os.path.realpath(os.path.join(os.path.dirname(mpfb_mod.__file__), "data"))',
        "os.path.realpath(LocationService.get_user_data())",
        'os.path.realpath(bpy.utils.extension_path_user(mpfb_mod.__package__, path="data"))',
        "`LocationService.get_mh_user_data()` and `LocationService.get_second_root()` must each "
        "be unset (`None` or empty) or resolve to an owner-imported directory inside the active project root",
        "Reject symlinked directories",
        "`HumanService.refit(basemesh)` only when all configured MPFB roots are verified in scope",
        "verify `faceunits01.json` and any required `visemes01.json` or `visemes02.json` directly "
        'under `os.path.join(verified_root, "packs")`',
        "Do not rely on `FaceService.is_faceunits01_installed()`: it caches a session boolean, not a verified path",
        "any configured MPFB root is not verified in scope, skip facial targets and report the blocker",
    )
    assert_guidance(
        root_gate,
        "only when all configured MPFB roots are verified in scope",
        "HumanService.refit()",
        "HumanService.deserialize_from_json_file()",
        "HumanService.deserialize_from_dict()",
        "FaceService.is_faceunits01_installed()",
        "FaceService.load_targets()",
        "FaceService.set_expression()",
        "TargetService.target_full_path()",
        "If any root is out of scope or cannot be verified, stop and report the configured root as a blocker",
    )

    assert_guidance(
        body,
        "assets/<slug>/source/human.<slug>.json",
        "HumanService.serialize_to_json_file(basemesh, filepath, save_clothes=True)",
        "`HumanService.deserialize_from_json_file(filepath, settings)` requires a filename matching `human.<name>.json`",
        'set `human_info["name"]` and call `HumanService.deserialize_from_dict(human_info, settings)`',
        "Both restore methods resolve skins and clothes across configured roots. Call them only when "
        "all configured MPFB roots are verified in scope",
        "Read `source`, `author`, `license`, `original_author` and `original_source`",
        "`license` (US spelling) to the component licence in `Provenance.note`",
        "The Proscenium contract uses `licence` (British spelling)",
        "Mixamo motion UUIDs and separately sourced materials in `Provenance.note`",
        'use `licence: "CC0"` only if every component',
        "no separately sourced or Mixamo assets",
        "all components share another verified licence, record it",
        "`licence: null` without discarding component attribution",
        "Set `source_url` and `creator` to the exact observed source and author respectively only "
        "when each value is uniform across all components",
        "Set the corresponding field to `null` for mixed or unverified sources or creators",
        "Do not substitute the extension homepage or `MakeHuman Community`",
    )
    assert ".mpfb.json" not in body


def test_native_prompts_route_mpfb2_to_the_skill():
    assert_guidance(SHARED_INSTRUCTIONS, "load the mpfb2 skill", "without installing software")
    specialist_guidance = SPECIALIST_INSTRUCTIONS.split(SHARED_INSTRUCTIONS, 1)[0]
    assert re.search(r"\bmpfb2\b", specialist_guidance)


def test_mpfb2_cross_references_and_operator_docs():
    for skill in ("blender-authoring", "mixamo", "hero-assets", "unity"):
        assert_guidance((SKILLS / skill / "SKILL.md").read_text(encoding="utf-8"), "`mpfb2`")
    assert_guidance(
        (ROOT / "docs/operator.md").read_text(encoding="utf-8"),
        "MPFB2",
        "`mpfb` Blender Extension",
        "`v2.0.18`",
        "`makehuman_system_assets`",
        "`--factory-startup` Cycles CPU worker without MPFB2 loaded",
    )
