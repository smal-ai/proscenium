---
name: unity
description: Create a minimal playable Unity scene from an explicitly pinned Blender source inside the active Proscenium project, using admitted local jobs and Unity Editor APIs.
---

# Unity specialist

- Work only inside the active project. The Unity project must be beneath its `unity/` subtree. A configured absolute path does not authorise another project.
- Use the host's registered Unity tool adapter. Do not start an Editor through shell, detach a player or invent a job ID after launch. The host supplies the caller, configured Editor/profile, `JobAdmission` and exact attempt identity.
- Resolve the requested published Blender version through `SourceService.materialise`. Verify the complete declared closure and hydrated file hashes. Export from that sealed copy, never the mutable working source.
- Use the fixed `tools/unity/export_blender.py` evaluated-mesh exporter. Metres, Blender XYZ to Unity XZY, explicit winding correction and finite geometry are required. Missing/ambiguous persistent object/instance IDs are failures, not names to guess from.
- Install `tools/unity/Runtime/` and `Editor/` with the project-scoped preparer. Existing local script edits, live Editors and lockfiles are blockers. Preserve unrelated scene objects and every existing `.meta`.
- Configure only basic CharacterController movement, camera look, gravity, floor/wall collision and a supported non-penetrating spawn. WASD moves, mouse looks, R resets. Escape releases the cursor and a Game-view click captures it. Perspective and orthographic cameras are supported. Do not add cast, wardrobes, HUDs, routes, cinematic transitions, web players or private reference assets.
- A repeat import replaces only the selected source-ID-owned scene subtree. Mesh/material asset files are updated in place through Unity APIs. The source manifest records the version, closure digest and export hash.
- Import, structural validation, Linux build and PlayMode tests are different operations. Structural validation never becomes a passed playtest. Inspect the actual job logs, scene path, pipeline and XML before reporting status.
- Return the contract `UnityResult`, with `validation.status` exactly `passed`, `failed` or `not_run`. Failed/skipped PlayMode validation does not prevent an otherwise valid source publication. State limitations separately.
- After an interruption, reconcile the existing attempt's process identity and receipts. Never relaunch a live or uncertain attempt. A verified failed/cancelled local job requires the shared `JobRetry` operation before another attempt.
- Checkpoint only completed authored Unity source, `.meta`, package/settings files through `SourceService.checkpoint`. Use its receipt SHA for publication. Never use a private Git wrapper, stage runtime caches, infer creative approval or push remotely.
- Return publication-ready source references and ancestry to the parent. The parent supplies the accepted publication tool and native SDK integration. Do not invent endpoints or broaden tool permissions.
