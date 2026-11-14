"""Write labelled test-user intent, never a Blender authoring script or model output."""

import json

from m10_fixture import ROOT


def main():
    fixture = json.loads((ROOT / "fixture.json").read_text())
    project_id = fixture["project"]["project_id"]
    prompt = f"""SCRIPTED TEST-USER INPUT FOR THE M10 ACCEPTANCE FIXTURE, not a real human creative approval.

Revise the existing imported Hunyuan mesh selected in the current 3D Inspection context.
Project {project_id}, item m10-generated-asset, exact base version m10-asset-v1.
The actual source is assets/e04/asset.blend; mesh E04_Mesh_001 has stable properties
proscenium_object_id=e04-object-001 and proscenium_instance_id=e04-instance-001.
This is the real accepted Hunyuan output, 350259 vertices and 1304618 triangles.
Original image, GLB and generation/model provenance are already imported into this project.

Use this lead native session directly. NO children, delegation, Task agents or additional models.
ZERO inference/Comfy/VLM/GPU operations. Do not start a live Blender/MCP/UI or use port 9876.
You have ONE owned background Blender authoring process, at most 180 seconds and four CPU threads.
Do not run blender --version or a separate inspection process. An independent final inspection
is reserved for the test harness. Stay in this active project; do not read other projects or private databases.

AUTHOR THE REVISION YOURSELF with native bash/file tools and your own project-local Python script.
Open the existing .blend, preserve its actual mesh topology, vertex/polygon counts and stable IDs.
Uniformly scale the mesh so its WORLD vertical height is exactly 0.12 metres, then translate
it so its lowest world-space Z is 0. Keep one mesh, do not add geometry, decimate or rebuild it.
Assign a simple local blue ceramic-like material (no image textures), and add an authored camera
and local light/world suitable for a 640x360 keyframe. Frame the whole object clearly.
Save NEW editable source assets/e04/asset-v2.blend. Never overwrite asset.blend, original.glb,
conditioning.png or any original generation/model/import provenance files.
Record actual measured bounds, counts, stable IDs and Blender version in
assets/e04/revision-measurements.json from your authoring script. State that topology quality,
UVs, retopology and rigging remain unproven. Do not directly render from the authoring script.

Run only that ONE script in a foreground bounded CLI command. The installed executable is
/home/quintin/.local/bin/blender. Use --background --factory-startup --disable-autoexec --threads 4
and a nonzero --python-exit-code on error. Use OMP_NUM_THREADS=4 and MKL_NUM_THREADS=4 and timeout 180s.
Serialise the command using the existing OS render claim /tmp/opencode/proscenium-native-claims/blender-render.lock
(flock is permitted for this resource coordination only). No unrelated service/process changes.

Then use get_version for the exact base metadata, checkpoint_sources for your explicitly owned
new script/source/measurement files, and publish_outputs for a NEW version m10-asset-v2 of the
SAME item m10-generated-asset, expected_current_version_id m10-asset-v1, derived_from that version.
Use the real checkpoint commit and exact hashes. Keep the original retained source/image/GLB/model
provenance referenced in the closure/artefacts. No fake source IDs, invented files, manual Git
checkpoint wrapper, private SQL or owner-selection calls. No generation reservation is needed.
SourceClosure digest is SHA-256 of RFC 8785 JSON excluding digest, files sorted by destination,
dependencies sorted by item_id/version_id. You may invoke the existing application Python
environment for its canonical helper: /home/quintin/.cache/proscenium-development/fleet/m10-asset/.venv/bin/python.
Do not install anything. All source origins must use this project's repository_id from get_version.

Publish EXACTLY TWO initial preview requests: one static proxy and one keyframe, no animation.
Use intent IDs m10-v2-proxy and m10-v2-keyframe, shot_id m10-asset-shot, scene_name Scene,
source_closure_digest of your actual new closure, camera null (your authored camera),
frames {{start:1,end:1}}, fps {{numerator:24,denominator:1}}, dimensions {{width:640,height:360}},
engine CYCLES, device CPU, samples 8, renderer_revision proscenium-blender-1,
colour_management {{view_transform:'Standard',look:'None',exposure:0,gamma:1}}.
The proxy purpose is proxy and has no review_output. The keyframe purpose is keyframe and
review_output is {{media_type:'image/png',encoding:'view_transformed_srgb',bit_depth:8}}.
No extra outputs. The existing provider/renderer owns launch and reconciliation. Do not
call a separate render CLI or duplicate preview admission if it is waiting for the resource.

Observe those original job IDs with get_job. Once your keyframe job is SUCCEEDED, perform
ONE explicit negative retry test: call retry_job on that already-succeeded keyframe with its
actual current state_revision and operation_id m10-success-retry-must-refuse. The expected
result is invalid_state_transition and NO new attempt. Do not retry failed/cancelled jobs,
corrected inputs or the refusal. If jobs fail, retain their identities and report the blocker.
This diagnostic refusal is explicitly part of the test-user request, not permission to rerender.

Finish with actual checkpoint/version/job identities and limits. Do not ask for routine
authoring permission or creative selection. Do not claim that a test-user selected the asset.
"""
    (ROOT / "prompt.txt").write_text(prompt)
    (ROOT / "project/brief/m10-allocation.md").write_text(prompt)
    print(
        json.dumps(
            {"project_id": project_id, "prompt_bytes": len(prompt.encode()), "cloud_sent": False}
        )
    )


if __name__ == "__main__":
    main()
