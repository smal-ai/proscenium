"""Before-snapshot and labelled new owner intent. Never runs revision/native/model code."""

import json
import shutil
from pathlib import Path

import m10_continue_snapshot as previous

REPO = Path(__file__).resolve().parents[3]
ROOT = Path("/home/quintin/.cache/proscenium-development/milestones/m10-complete-a")
ORIGINAL = previous.ORIGINAL
PROJECT = previous.PROJECT
SESSION = previous.SESSION

PROMPT = """SCRIPTED OWNER FOLLOW-UP FOR M10 ACCEPTANCE. Not artistic approval.
This is ONE NEW explicit Queue in the SAME retained project/native session. The historical
request 927cf238-56c2-454e-a063-014e072f9010 and its completed work remain unchanged.
Do not resume/reopen/resend that work. Finish the remaining canonical asset revision now.

Known active-project files: assets/e04/asset.blend (actual retained E04 mesh),
assets/e04/revise_asset.py (your earlier model-written script), assets/e04/inspection.json,
assets/e04/import-provenance.json, tools/m10_complete_author.py (new fixture allocation guard).
assets/e04/revision-measurements.json DOES NOT EXIST YET and this is expected. Start with
the actual script and get_version for item m10-generated-asset/version m10-asset-v1.
Do not discover private/runtime files, use globs/root listings, or look for unrelated plans.
No new conversation, Task agents, children, delegation, other models, inference/Comfy/VLM,
uploads, downloads, Unity, installation or independent service/process changes.

Inspect your actual prior script and retained asset metadata, then author any necessary
correction yourself. Preserve original asset.blend, original.glb, conditioning.png and all
generation/model/import provenance. Retain all 350259 vertices and 1304618 triangles, mesh
coordinates/connectivity and stable IDs e04-object-001 / e04-instance-001. Uniformly set
WORLD height to 0.12 metres and lowest world Z to 0 using object transforms. Assign a local
blue ceramic-like material without textures. Frame the whole object with authored camera
and local lights/world. Save NEW canonical editable source assets/e04/asset-v2.blend and
actual bounds/counts/height/base/material/Blender measurements to assets/e04/revision-measurements.json.
Topology quality, UV quality, retopology and rigging remain unproven. Do not directly render.

Execute authoring ONLY with: python3 tools/m10_complete_author.py assets/e04/revise_asset.py
This is a NEW allocation replacing the old one-author brief. The helper owns the exact
four-thread CPU process, 180-second author timeout, append-only receipts and shared OS claim.
At most TWO authoring invocations. Second ONLY if the first has verified nonzero exit AND
you actually repair the owned script. No second invocation after success. No uncertain retry.
Do not edit the fixture guard or its receipts, bypass it, run a separate Blender inspection,
render/export directly or run blender --version. One independent readback is reserved for
the harness after publication. This root has a 240-second total model/tool budget. Act directly.

After real authoring success, use normal checkpoint_sources for the explicitly owned revised
script, asset-v2.blend and revision-measurements.json. Use real expected_head and returned commit.
Use normal publish_outputs for NEW version m10-asset-v2 of SAME item m10-generated-asset,
expected_current_version_id m10-asset-v1 and derived_from that version. Preserve original
retained source/image/GLB/model provenance in source closure/artefacts. No manual publication,
private SQL, fabricated IDs, owner-selection calls or manual Git checkpoint wrapper.
Source closure digest is RFC8785 SHA256 excluding digest, files sorted by destination,
dependencies sorted by item_id/version_id. Use the existing application Python environment
at APPLICATION_PYTHON for canonical helpers if needed. No installation.

Publish EXACTLY TWO initial preview requests: one proxy and one keyframe. Intent IDs
m10-v2-proxy and m10-v2-keyframe, shot_id m10-asset-shot, scene_name Scene,
camera null (your authored camera), frames {start:1,end:1}, fps {numerator:24,denominator:1},
dimensions {width:640,height:360}, engine CYCLES, device CPU, samples 8,
renderer_revision proscenium-blender-1, source_closure_digest from your actual new closure,
colour_management {view_transform:'Standard',look:'None',exposure:0,gamma:1}.
Proxy has purpose proxy without review_output. Keyframe has purpose keyframe and
review_output {media_type:'image/png',encoding:'view_transformed_srgb',bit_depth:8}.
Production owns these two launches/reconciliation. Never submit replacements or retry previews.
Observe actual original job IDs with get_job. Finish with actual checkpoint/publication/version/
job identities and honest limits. No routine authoring approval or creative selection is needed.
"""


def main():
    previous.ROOT = ROOT
    previous.main()
    before = json.loads((ROOT / "before.json").read_text())
    assert before["work"]["state"] == "completed" and before["work"]["state_revision"] == 6
    for namespace in (
        "requests",
        "work",
        "contexts",
        "invocations",
        "copilot.permissions",
        "selections",
        "versions",
        "jobs",
        "attempts",
        "frames",
        "segments",
        "artifacts",
        "copilot.sends",
        "copilot.continuation_sends",
    ):
        before.setdefault("historical_records", {})[namespace] = previous.records(namespace)
    roots = [
        ORIGINAL,
        Path("/home/quintin/.cache/proscenium-development/milestones/m10-continuation-a"),
        Path("/home/quintin/.cache/proscenium-development/milestones/m10-final-a"),
    ]
    before["old_report_hashes"] = {
        str(path): previous.sha(path) for root in roots for path in root.glob("*.json")
    }
    before["project_status"] = previous.subprocess.check_output(
        ["git", "-C", str(ORIGINAL / "project"), "status", "--short"], text=True
    )
    shutil.copyfile(
        ORIGINAL / "project/assets/e04/revise_asset.py", ROOT / "original-model-script.py"
    )
    previous.write(ROOT / "before.json", before)
    tools = ORIGINAL / "project/tools"
    tools.mkdir(exist_ok=True)
    target = tools / "m10_complete_author.py"
    assert not target.exists()
    shutil.copyfile(Path(__file__).with_name("m10_complete_author.py"), target)
    prompt = PROMPT.replace("APPLICATION_PYTHON", str(REPO / ".venv/bin/python"))
    (ROOT / "prompt.txt").write_text(prompt)
    (ORIGINAL / "project/brief/m10-complete-allocation.md").write_text(prompt)
    previous.write(
        ROOT / "fixture-helpers.json",
        {
            "author_guard": str(target),
            "sha256": previous.sha(target),
            "cloud_sent": False,
            "old_brief_and_script_preserved_before_model": True,
        },
    )


if __name__ == "__main__":
    main()
