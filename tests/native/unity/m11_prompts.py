"""Scripted test-user requests. These do not author or preseed Unity output."""

import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
source = {"item_id": "m11-source", "version_id": "m11-source-v1"}
if len(sys.argv) > 2 and sys.argv[2] == "second":
    attestation = json.loads((root / "receipts/011-attest.json").read_text())["result"]
    text = f"""SCRIPTED M11 TEST-USER FOLLOW-UP. Complete this root within 290 seconds. The actual independent import m11-import-job has finished successfully, after the browser disconnected. Its generic UnityResult honestly says not_run for PlayMode. Now delegate EXACTLY ONE native task to unity-specialist for inspection, source checkpoint and publication ONLY. This is the SECOND and LAST allocated native child. No more delegation, Blender export, Unity Editor invocation, build, rendering, model/VLM/generation, service changes or installations. Do not call run_unity_workflow again and do not shell-launch a native app. No questions or extra planning cycles.

The saved Unity project is unity/M11 inside this active project. The real saved scene is unity/M11/Assets/Proscenium/Generated/f4ecf3b8541adc9031e22d92/Playable.unity. Source is exactly {json.dumps(source)}. Use get_job(m11-import-job), get_unity_result(m11-import-job) and get_version for the actual source metadata, not private SQLite.

Separate, explicitly labelled OPERATOR ACCEPTANCE FIXTURES have now run against that saved project: m11-repeat-proof-job repeated the same pinned import through the actual Editor APIs and preserved GUIDs, one generated root and an unrelated acceptance sentinel. m11-playmode-job then ran the unchanged accepted F05 all-polling test in the actual Editor. Both operator jobs exited 0/succeeded. This is NOT a claim that the generic import performed PlayMode. The operator added only the unrelated test sentinel and accepted test assembly/package metadata, not private game assets or gameplay.

Inspect get_job(m11-playmode-job), get_artifact(m11-playmode-attestation), get_artifact(m11-playmode-xml) and get_artifact(m11-playmode-metrics). Read the attestation's verified project-owned immutable bytes at .proscenium/artefacts/sha256/{attestation["artifacts"][0]["sha256"]}/content, or the identical explicit metadata unity/M11/M11-operator-playmode.json. Its actual UnityResult is {json.dumps(attestation["attestation"]["unity"])}. All movement/look/reset used Keyboard.current/Mouse.current -> production Update -> real CharacterController/PhysX, with injectedInput false. Both camera cases measured movement3m, eachWASD0.666667m, floorerror0.020m, wallpenetration0m, reseterror0m and camera8.94275degrees. Physical OS input was not tested.

Checkpoint the completed Unity SOURCE ONLY through checkpoint_sources: explicit extant files recursively under unity/M11/Assets, unity/M11/Packages, unity/M11/ProjectSettings, and unity/M11/M11-operator-playmode.json. Include every .meta. Exclude Library, Logs, Temp, UserSettings, Builds, .git, private .proscenium and unrelated staging. Do not hand-commit authored outputs. Read actual git HEAD for expected_head. The original source fixture is already checkpointed and must remain byte-identical. Use the actual checkpoint receipt commit and actual hydrated hashes, not a later mutable HEAD.

Publish one NEW unity_result item/version with fixed IDs item_id m11-playable, version_id m11-playable-v1, publication_id m11-unity-publication, name 'M11 playable F01 Unity scene', expected_current_version_id null, artifact_ids ['m11-playmode-attestation','m11-playmode-xml','m11-playmode-metrics'], preview_requests [], derived_from [{json.dumps(source)}]. Its SourceClosure.entry_path is the full project-relative saved scene path above; files map ALL the checkpointed Unity source/metadata to git origins using this project's actual repository_id from project.json, exact checkpoint commit, paths and actual hydrated SHA-256. SourceClosure.dependencies includes {json.dumps(source)}, required_runtime is application Unity/version6000.3.27f1/features['urp','character_controller']. Digest is SHA256 of RFC8785 canonical JSON excluding digest, files sorted by destination and dependencies by item/version. Use installed proscenium.contracts SCHEMA/validate/canonical to validate exact normative shape, then publish_outputs. Do not invent a second DTO.

Set PublishedItem.unity to the actual attested UnityResult with status passed, BUT validation.notes must explicitly state: 'Operator F05 PlayMode job m11-playmode-job passed on this saved scene; generic import m11-import-job validation was not_run. Virtual Input System polling tested; physical OS input not tested.' Do NOT alter the import job or its historical result. Tags M11/operator-tested are technical metadata, not owner creative approval. Optional provenance can explain source was operator F01 setup, generic import was native Unity specialist, and repeat/PlayMode were operator verification. Unknown licence stays unknown. No previews or game/player builds. Finish with actual publication/version/commit/scene IDs and this honest separation of model authoring versus operator verification.
"""
    assert not (root / "second.txt").exists()
    (root / "second.txt").write_text(text)
    print("Prepared publication follow-up using actual registered operator evidence.")
    raise SystemExit(0)
body = dict(
    operation_id="m11-import-operation",
    job_id="m11-import-job",
    version=source,
    execution_profile_id="unity-local",
    project="unity/M11",
    spawn=[0, 0.92, -2],
    camera="perspective",
    pipeline="urp",
    action="import",
)
first = f"""SCRIPTED M11 TEST-USER REQUEST, not a human creative-selection claim. Make the already-published Blender environment playable as a minimal saved Unity scene. This real application journey is bounded: complete this root within 290 seconds. No questions or extra planning cycles.

Use the frontier lead and delegate EXACTLY ONE native task to agent_type unity-specialist (its required configured model is gpt-6.1-sol). Do not use authoring-specialist or any other child. The native child's authoritative host additionalContext is wrapped in system_reminder; consume it rather than rejecting that wrapper. The pinned source is {json.dumps(source)}. It is an explicitly labelled operator-imported native F01 fixture, not content you authored. It has a 6m floor at Unity Y=0, a wall near Unity Z=2.4 and two 1m cubes. The source closure/materialisation is supplied by the accepted W02 tools. Do not edit Blender geometry.

Tell the Unity child to inspect get_version for the exact source, then call run_unity_workflow ONCE with this exact envelope, with no extra fields:
{json.dumps(body)}

This uses the real fixed Blender export and tracked Unity Editor import. Installed Editor is configured privately by the host. Do not shell-launch Blender, Unity, any player, renderer, GPU work, model/provider/service, installation or download. No Linux build, web player, characters, HUD, camera cinematics or look-dev parity. The generic import's honest PlayMode validation is not_run. A separate operator acceptance job will playtest later. Do not pretend you performed it.

The tool returns an independent admitted JobRecord, NOT a completed scene. As soon as job m11-import-job is admitted, the child should report its exact job/attempt/source identity and finish without polling to wait for the Editor. The lead should then finish this root with the job ID and honest pending/not_run status. Do not call get_unity_result repeatedly while running. Do NOT checkpoint or publish Unity yet because the Editor may still be writing. A later explicit request will inspect the actual completion, perform source checkpoint and publish. Do not create a second operation/job, re-export, or spend another native child. Existing shell/read tools remain available for scoped metadata, but all native launch must go through the facade.
"""
assert not (root / "first.txt").exists()
(root / "first.txt").write_text(first)
print("Prepared original scripted owner request. No Unity output or native result seeded.")
