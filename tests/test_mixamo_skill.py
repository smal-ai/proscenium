"""Offline discovery and guidance checks for the optional Mixamo CLI skill."""

from pathlib import Path
from types import SimpleNamespace

import yaml
from proscenium.configuration import Settings
from proscenium.copilot.capabilities import get_project_capabilities
from proscenium.copilot.native import SHARED_INSTRUCTIONS, SPECIALIST_INSTRUCTIONS

SKILL = Path(__file__).resolve().parents[1] / "agent/skills/mixamo/SKILL.md"


def test_project_capabilities_discovers_mixamo_without_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    services = SimpleNamespace(
        settings=Settings(data_directory=tmp_path, runtime={"worker_model": "fixture"}),
        extension=lambda name: None,
    )

    capabilities = get_project_capabilities(services, "project-one")

    assert "mixamo" in capabilities["available_skills"]


def test_mixamo_skill_frontmatter_and_workflow():
    opening, frontmatter, body = SKILL.read_text(encoding="utf-8").split("---\n", 2)
    assert opening == ""
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "mixamo"
    assert isinstance(metadata["description"], str) and metadata["description"].strip()

    body = " ".join(body.split())
    for guidance in (
        "Call `get_project_capabilities` first",
        "`planning_only` permits read-only discovery, not downloads",
        "mixamo status",
        'mixamo search "<query>" --type Character --json',
        'mixamo search "<query>" --type Motion,MotionPack --json',
        "mixamo inspect <product_uuid>",
        "mixamo character <character_uuid>",
        "mixamo download <motion_uuid> --character-id <character_uuid>",
        "obtain recorded owner approval for the exact file and Adobe Mixamo destination "
        "before running `mixamo upload assets/<slug>/source/<rigged>.fbx --timeout 180`",
        "mixamo set-character <character_uuid>",
        "--format fbx7_2019",
        "--format fbx7_unity",
        "`--fps` accepts only `24`, `30` or `60`",
        "Git LFS",
        "checkpoint_sources",
        "Download only inside the active project root under `assets/<slug>/source/`",
        "record `Provenance` for Mixamo catalogue characters and motions "
        'with `source_url: "https://www.mixamo.com"`, `creator: "Adobe Mixamo"` '
        "and `licence: null`",
        "For a custom uploaded rig, preserve the rig's original `source_url`, `creator` "
        "and `licence`, using `null` for unknowns",
        "Record the exact Mixamo motion UUIDs and names and the retargeting step in `Provenance.note`",
        "Never read `~/.config/mixamo/token`, `~/.config/mixamo/session.json`",
        "request_preview",
        "request_vlm_review",
    ):
        assert guidance in body


def test_native_prompts_route_mixamo_to_the_skill():
    assert "load the mixamo skill and use the installed mixamo CLI" in SHARED_INSTRUCTIONS
    assert "without reading credentials" in SHARED_INSTRUCTIONS
    specialist_guidance = SPECIALIST_INSTRUCTIONS.split(SHARED_INSTRUCTIONS, 1)[0]
    assert "blender-authoring, mixamo, sequence-assembly" in " ".join(specialist_guidance.split())
