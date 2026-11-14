"""Prepare scripted creative input and accepted helper, never an authored plan."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
repo = Path(__file__).resolve().parents[3]
project = root / "project"
if (root / "first.txt").exists() or (project / "storyboards/kite-flight/plan.json").exists():
    raise SystemExit("Original input/source exists. Inspect it, never prepare a replacement run.")
asset = project / "assets/kite/palette.json"
asset.parent.mkdir(parents=True, exist_ok=True)
asset.write_text(
    json.dumps(
        {
            "fixture": "owner-authored material brief, not geometry",
            "subject": "paper kite",
            "units": "metres",
            "paper_colour": "warm ochre",
            "string_colour": "slate",
            "geometry_status": "missing",
        },
        indent=2,
    )
    + "\n"
)
helper = project / "tools/storyboard/plan_tools.py"
helper.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(repo / "agent/skills/storyboard/plan_tools.py", helper)
subprocess.run(
    ["git", "add", "assets/kite/palette.json", "tools/storyboard/plan_tools.py"],
    cwd=project,
    check=True,
)
subprocess.run(
    [
        "git",
        "-c",
        "user.name=M06 fixture owner",
        "-c",
        "user.email=m06@example.invalid",
        "commit",
        "-m",
        "test: retain owner material brief and accepted read-only planning helper",
    ],
    cwd=project,
    check=True,
    capture_output=True,
)
reference = json.loads((root / "reference-upload.json").read_text())
first = f"""Scripted M06 owner brief. Use the accepted storyboard skill and its maintained resources. This is a PLANNING-ONLY request, not production execution. No children, Blender, Unity, rendering, image generation, VLM, model/service lifecycle, installation, purchases, remote push or public uploads. All operations stay inside this active project. Do not query SQLite. Complete this root within 170 seconds; do not ask me for creative input because all material choices below are fixed.

Create a silent TWO-SHOT paper-kite concept named 'A small lift'. Audience: a quiet 6-second desktop storyboard, warm-paper/soft-sky treatment. No people, dialogue, cast or industrial subjects. Shot IDs are kite-rise and kite-rest. kite-rise proposes a clockwise camera arc during ascent, kite-rest proposes a locked camera while the kite settles. Proposed production duration is 3 seconds per shot at 24 fps, NOT review-card pacing and NOT an already-authored timeline. No scene exists: use null scene_version/source_range/fps and explicitly explain proposed timing in each shot's intent/motion beats and the canonical brief, and list timeline/scene geometry/camera binding as unresolved.

Author the canonical brief/treatment in brief/film.md and an EXACT AuthoredShotPlan in storyboards/kite-flight/plan.json with stable plan_id kite-flight and this project's actual project_id from project.json. Both shots require ordered start/intermediate/end frames with IDs <shot_id>-start, <shot_id>-middle, <shot_id>-end. The first shot's three roles may reference the SAME actual owner-uploaded image {reference["artifact_id"]} as an explicitly labelled reference, NOT a generated concept/render; compositions describe desired changes, not claims that new images exist. The second shot's start also references that image, while middle/end remain honestly missing with null artefact IDs. The image is a labelled synthetic owner-supplied reference, {reference["dimensions"]["width"]}x{reference["dimensions"]["height"]}. Get its metadata through get_artifact. No need to create or register an image. Explicitly list all missing concept images, geometry, scene/timeline binding and no unresolved creative decisions. Never add owner_selected or source_commit to authored plan JSON.

The existing assets/kite/palette.json is an owner fixture MATERIAL BRIEF, not geometry. Keep its exact bytes. Publish it as asset item m06-kite-asset/version m06-kite-asset-v1, with source entry that JSON, empty previews and no geometry claims. Pin it in plan.asset_dependencies and storyboard source.dependencies. Publish the storyboard item m06-kite-board/version m06-kite-board-v1 with name 'A small lift - storyboard'. Both are new heads (expected_current_version_id null). Include the actual uploaded reference in storyboard artifact_ids. This is a source-only publication with preview_requests [] throughout.

Use the installed proscenium.contracts SCHEMA/validate/canonical for exact wire shapes and tools/storyboard/plan_tools.py to validate the authored plan. Build the two SourceClosure objects from actual checkpoint file hashes. Their digest is sha256(canonical(closure_without_digest)), with files sorted by destination and dependencies sorted by item/version. Each Git origin uses this project's repository_id, the actual checkpoint source_commit, real relative path and hash. Validate PublicationSubmit before calling publish_outputs. Do not hand-commit authored outputs: use checkpoint_sources for ONLY brief/film.md, storyboards/kite-flight/plan.json and (unchanged) assets/kite/palette.json, with actual git HEAD as expected_head, then use its exact receipt and publish_outputs. Persist model-facing publication input/receipts into ordinary project reports only if useful; do not edit .proscenium stores. Finish with plan/shot/frame IDs, exact source commit, publication/version IDs, missing inputs and an explicit statement that no production jobs were requested. Proceed autonomously through planning/checkpoint/source publication without stage approvals.
"""
(root / "first.txt").write_text(first)
print("Prepared owner brief and unchanged material fixture. No plan has been seeded.")
