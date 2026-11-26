---
name: unity
description: Create a minimal playable Unity scene from an explicitly pinned Blender source inside the active Proscenium project, using admitted local jobs and Unity Editor APIs.
---

# Unity specialist

## Scope, workflow admission and publication boundaries

- Call `get_project_capabilities` first. Honour host `request_target`,
  `reference_versions` and `execution_scope`. `planning_only` forbids scene
  execution, previews and inference. Validate plans, sequences and publications
  through `validate_authored_document` before checkpoint/publication. Use
  `compare_to_path` and `changed_ids` for shot-plan impact, not checkout helpers.
- Standalone assets need no storyboard. Publish an `asset` and keep dependent
  scenes/storyboards pinned until explicit adoption. Requested image inference
  belongs to `prepare_image_generation` then `submit_generation`, not a drawing substitute.
  Omit preparation `project_id`/`generation_task_id`, which the host binds. Omit
  width/height/steps for capability recipe defaults unless the user requested valid
  multiple-of-16 dimensions within `max_pixels`.
- Work only inside the active project. The Unity project must be beneath its
  `unity/` subtree. A configured absolute path does not authorise another project.
  Use `list_items` to find current-project assets by name, description, tags and
  provenance, then `get_version` for the exact immutable source. Never inspect
  private SQLite or choose filesystem-latest.
- Submit import, validation and build through `run_unity_workflow`. Supply the
  exact published `version`, stable `operation_id` and `job_id`, configured
  execution profile ID, project-relative Unity directory and declared
  spawn/camera/pipeline/action. The host computes hashes and supplies the caller
  and configured executable. Never launch an untracked Editor, detach a player
  or invent a job ID after launch.
- The facade owns source materialisation, fixed export, script preparation and
  job admission. Use ordinary scoped shell authoring or inspection, never a
  checkout helper, separate Blender export or shell/CLI batch launch to bypass
  the durable workflow. The host resolves the pinned Blender closure and exports
  evaluated meshes in metres, converts Blender XYZ to Unity XZY and corrects
  winding. Non-finite geometry or missing/ambiguous persistent object or instance
  IDs are failures. Do not guess IDs from names.
- The project-scoped preparer supplies runtime and Editor scripts. Existing local
  script edits, live Editors and lockfiles are blockers. Preserve unsaved work,
  unrelated scene objects and every existing `.meta`. On reimport, replace only
  the selected source-ID-owned subtree and update mesh/material assets in place
  via Unity APIs. The source manifest records version, closure digest and export hash.
- For an existing `unity_result`, use `validate` or `build` against its saved
  project under `unity/`, with the Editor closed. Pass the published scene as
  `Assets/...` or its workspace-relative path, or omit it to use the published
  path. These actions use the live working tree without installing scripts,
  exporting Blender or replacing `.meta` files.
- An authored `proscenium-unity.json` in the Unity project can declare
  `editor_version`, `pipeline`, `scene_path`, `validate_method`, `build_method`
  and a project-relative `build_output` executable beneath `Builds/`. The output
  cannot be a `.meta` file and symlinks must stay inside the Unity project.
  Methods must exit non-zero on failure. Without a validate method, validation
  checks batch compilation only.
- Admission returns a `JobRecord`, not a completed scene. Wait with
  `get_job(job_id=..., wait_seconds=15)` (bounded `0..30`), not rapid polling,
  then read `get_unity_result` for the finished `UnityResult`. After interruption,
  reconcile the existing attempt's process identity and receipts. Never relaunch
  a live or uncertain attempt. A verified failed or cancelled local job needs
  the shared `JobRetry` operation before another attempt.
- Once the job settles, checkpoint only completed, explicitly owned Unity source,
  `.meta`, package and settings files. Use `checkpoint_sources(entry_path=...)`
  with dependencies and `required_runtime` as needed. It uses
  `SourceService.checkpoint`. Use the receipt SHA and pass the returned
  `source_closure` directly to `publish_outputs`. Use the workspace-relative
  scene returned by `get_unity_result` and give the lead publication-ready source
  references and ancestry. Do not use a private Git wrapper, stage runtime
  caches or unrelated changes, script closure sorting or SHA-256 digests, or
  push remotely as part of the local workflow. The facade does not infer a
  creative brief, plan cuts, auto-publish or confer owner creative approval.
- Complete the assigned slice directly without unnecessary nested delegation.
  Load `unity` first when `skill` is exposed. The owner policy requires explicit,
  recorded confirmation before software or package installation (including new
  project setup), upgrades or other consequential actions.

## Pause screen, controls and remote desktop cursor hygiene

- Keep the walkthrough to basic `CharacterController` movement, camera look,
  gravity, floor/wall collision and a supported, non-penetrating spawn.
  Perspective and orthographic cameras are supported. `WASD` moves, mouse looks,
  `R` resets and `Escape` toggles the built-in Pause Screen (`PauseMenu`).
  Provide **Resume**, **Reset Position** (reset to spawn and resume) and **Quit**
  (`Application.Quit()` in the player, `EditorApplication.isPlaying = false` in
  the Editor).
- Pair `Cursor.lockState` with `Cursor.visible` on every transition. Use
  `CursorLockMode.None` with `Cursor.visible = true` when paused, unfocused
  (`OnApplicationFocus(false)`), disabled, destroyed or exiting. Use
  `CursorLockMode.Locked` with `Cursor.visible = false` only while focused and
  actively playing. A Game view click can recapture it in that state. Never
  leave an invisible, trapped cursor on the remote desktop.
- Save the exact pre-pause `Time.timeScale`, including `0f`. Freeze simulation
  and suppress movement/look while paused. Restore the saved scale on Resume,
  disable, destroy or Quit. Do not assume a default scale of `1f`.
- The standard Escape Pause Screen is not an invitation to add gameplay HUDs.
  Do not add unrequested HUDs, routes, cinematic transitions, web players,
  cast or wardrobes. Never add private reference assets.

## Materials, instancing, colliders and URP lighting fidelity

- Preserve the real Blender shader graph (`Principled BSDF`, `Emission`,
  `MixShader`), not viewport `diffuse_color` or slot-0 flat grey. Preserve
  multi-material submeshes and UV seams. Map linear to sRGB for `_BaseColor`,
  carry `_Metallic` and `_Smoothness` (inverse roughness), and preserve HDR
  emission chromaticity in `_EmissionColor` with `_EMISSION` enabled.
- Share meshes and materials across repeated or instanced seating and props
  instead of creating thousands of unique `.asset` and `.mat` files or
  `MeshCollider` components. Use `BoxCollider` for repeated small props and
  `none` for tiny decorative or emissive trim. Reserve `MeshCollider` for
  floors, stairs, walls, stages and hero geometry.
- On Proscenium-owned URP projects, verify `PlayerSettings.colorSpace == ColorSpace.Linear`
  (`m_ActiveColorSpace: 1`). Fresh Unity projects can default to Gamma (`0`),
  which breaks `lightsUseLinearIntensity`, double-crushes dark interiors and
  shifts HDR blues towards cyan. Populate `postProcessData` on generated
  `UniversalRendererData` assets. Use `Neutral` tonemapping and subtle Bloom to
  keep orange and violet hues and dark shadow detail. Do not mutate a
  pre-existing user-owned pipeline or colour-space setting without explicit
  authorisation.
- Do not light an enclosed interior with one unshadowed directional light and
  `Default-Skybox`. Use `AmbientMode.Trilight`, `DefaultReflectionMode.Custom`,
  and `reflectionIntensity = 0` to suppress skybox ambient light and reflections.
  Set `camera.clearFlags = CameraClearFlags.SolidColor` with a dark
  `camera.backgroundColor`. Preserve `RenderSettings.skybox` and restore
  `CameraClearFlags.Skybox` for exteriors.
- On the Unity 6000.3.27f1 Linux reference setup, OpenGL Core is the default.
  URP disables Deferred there and caps Forward+ at 32 visible additional lights
  (`k_MaxVisibleLightCountMobile = 32`). Keep no more than 32 active realtime
  punctual lights and count any additional directional lights in the same
  visible budget. Prioritise directional lights, primary stage/key lights and
  spatially distributed fills. Limit directional/spot shadow casters to five
  for the 2048 shadow atlas. If the scene needs more than 32 simultaneous
  realtime lights, explicitly select Vulkan where authorised and verify it on
  the target runtime.

## Linux runtime media (`VideoPlayer`) and audio

- Do not ship H.264 in MP4 for Unity `VideoPlayer` on the reference Linux runtime.
  It has no H.264/MP4 decoder and can fail with `VideoPlayer cannot play url`.
  Encode cutscenes/intros as VP8 or VP9 `.webm` with Vorbis or Opus audio, or Ogg
  Theora `.ogv`.
- Route the intended soundtrack with `VideoAudioOutputMode.Direct` or
  `VideoAudioOutputMode.AudioSource`. In AudioSource mode, bind the intended
  track with `SetTargetAudioSource(...)`. Keep a single enabled `AudioListener`
  on the active camera. Never leave `VideoAudioOutputMode.None` when the clip
  has an intended soundtrack.
- In live Play Mode, verify `isPlaying == true`, an advancing `frame > 0`,
  audible intended audio, and zero `VideoPlayer.errorReceived` or Console errors.

## Live Unity CLI (`com.unity.pipeline`) Play Mode and visual verification

Use live inspection of the admitted workflow's scene, not shell/CLI Editor
launches to bypass durable import, structural validation or build. Under the
owner policy, obtain explicit, recorded owner confirmation before installing
`com.unity.pipeline` (even temporarily) or upgrading the CLI with
`unity self-update`. If the package was added for inspection only, restore
`Packages/manifest.json` and `Packages/packages-lock.json` afterwards without
overwriting unrelated edits unless permanent installation was approved.

Scope every Editor command with `--project-path <active-project-under-unity/>`.
For `unity close <project-path>`, the CLI takes the path positionally instead
of accepting `--project-path`. In the examples, `$project_path` is that exact
canonical path. Do not inspect, modify or stop a sibling Editor or close an
Editor with unsaved owner work.

1. At the start of the scoped Editor inspection, clear its Console once before
   opening the target scene. Open the scene, enable non-persistent auto-tick,
   then enter Play Mode:

   ```bash
   unity command console clear --project-path "$project_path"
   unity command editor set_auto_tick --project-path "$project_path" -- --enabled true --interval-ms 16 --persist false
   unity command editor.playmode start --project-path "$project_path" -- --run-in-background true
   ```

2. Check that `Time.frameCount` advances and `playerLoopTicking` is true. Query
   errors for the whole inspection session, not just a tail of the Console:

   `unity command console get --project-path "$project_path" --format json -- --level error --since-session`

   Require `counts.error == 0`, `groundTruth.consoleErrors == 0`,
   `groundTruth.compilationFailed == false` and `dropped == false`. Do not
   clear the Console again. Recheck after exercising the scene and after
   leaving Play Mode, before closing the Editor. If the installed package
   cannot verify whole-session error and exception counts, or events were
   dropped, report the Console check as unverified, not a pass.
3. Verify a grounded spawn, walkable geometry and stairs, `WASD`, mouse look
   and `R`. Press `Escape` and verify `IsPaused == true`,
   `Cursor.visible == true`, `Cursor.lockState == None` and frozen movement.
   Check Resume, Reset Position and clean Quit. Check cursor release on focus
   loss as well as on pause and exit. Take the pause capture before exercising Quit.
4. Capture Game View PNGs from spawn, a 180° reverse view, a hero/focal close-up
   and a wide/aisle view using `unity command capture game_view --project-path "$project_path" -- --source camera`.
   For the Pause Screen use `unity command capture game_view --project-path "$project_path" -- --source screen`
   so the capture includes IMGUI/UI overlays. Stage captures outside the Unity
   project but inside the active project's `.proscenium/tool-output/staging/`.
   Clean up any temporary screenshots inside the Unity project before closing it.
5. Use `register_artifact` for key captures and inspect them with
   `get_artifact(artifact_id=..., include_image=true)`. Never copy managed
   artefact bytes just to call `view`. When the tool is exposed and a VLM provider
   is `configured` or `ready`, call `request_vlm_review` with Blender reference
   renders. Check lighting/shadows, skybox leakage, multi-material hero faces,
   emissive tints, spawn framing and Pause Screen legibility. Read the findings
   with `get_vlm_review(review_id=..., wait_seconds=15)` for bounded active-work
   self-checks. Use `escalate_vlm_review` with a concrete question if human
   judgement is needed. Only owner-approved findings carry owner instruction
   authority. Report missing review capability or reference renders as a gap.
6. Stop Play Mode if still running, restore any temporary inspection changes and
   close only the active inspection project:

   ```bash
   unity command editor.playmode stop --project-path "$project_path"
   unity close "$project_path"
   ```

   Verify that no Editor or player processes from this inspection are still
   running before `run_unity_workflow` (`validate` or `build`) or checkpointing.
   Leave sibling processes untouched. Treat uncertain closure as a blocker.

## Build and validation honesty

- Verify that the active `build_method` freshly produced the exact `build_output`
  declared in `proscenium-unity.json`. Do not accept a stale or fallback binary
  under another path. The supervisor stages any prior executable privately and
  requires a newly written, non-empty output without clearing `Library/`.
- Report import, structural validation, Linux build, automated PlayMode tests
  and live visual verification separately. Inspect the actual job logs, scene
  path, pipeline and test XML when present. Structural validation is not a
  playtest. Claim walkthrough verification only after observing zero-error live
  Play Mode and completing visual review against Blender references.
- Return the contract `UnityResult` with observed `validation.status` exactly
  `passed`, `failed` or `not_run`. Never infer a pass from admission or fabricate
  a result. Failed or skipped PlayMode validation does not prevent an otherwise
  valid source publication. State verification gaps separately.
