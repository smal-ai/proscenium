---
name: unity
description: Create a minimal playable Unity scene from an explicitly pinned Blender source inside the active Proscenium project, using admitted local jobs and Unity Editor APIs.
---

# Unity specialist

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

- Work only inside the active project. The Unity project must be beneath its `unity/` subtree. A configured absolute path does not authorise another project.
- Use `run_unity_workflow` with exact published `version`, stable `operation_id`/`job_id`, configured execution profile ID, project-relative Unity directory and declared spawn/camera/pipeline/action. The host computes hashes and supplies the caller and configured executable. Do not start an Editor through shell, detach a player or invent a job ID after launch.
- For an existing `unity_result`, use `validate` or `build` against its saved project under `unity/`. Close the Editor first. Supply the published scene as `Assets/...` or its workspace-relative path (or omit it to use the published path). These actions run against the live working tree without installing scripts, exporting Blender, or replacing `.meta` files.
- An authored `proscenium-unity.json` in the Unity project can declare `editor_version`, `pipeline`, `scene_path`, `validate_method`, `build_method` and a project-relative `build_output` executable. The output must be beneath `Builds/` and cannot be a `.meta` file. Methods must exit non-zero on failure. Without a validate method, validation checks batch compilation only. The supervisor stages any previous executable privately and requires a newly written, non-empty output without clearing `Library/`. Symlinks must stay inside the Unity project. Use `get_unity_result`, then checkpoint the authored source and use the returned workspace-relative scene when publishing the next version.
- The tool returns an admitted `JobRecord`, not a completed scene. Wait using `get_job(job_id=..., wait_seconds=15)` (bounded `0..30`), not rapid polling, then `get_unity_result` for the actual finished `UnityResult`. Never infer completion from an admitted job or publish a fabricated validation result.
- Inspect registered images with `get_artifact(artifact_id=..., include_image=true)`. Never copy managed artefact content just to call `view`. Request `request_vlm_review` for produced visual outputs when a `configured` or `ready` VLM provider is available and the tool is exposed, read `get_vlm_review(review_id=..., wait_seconds=15)` for active-work self-checks, and call `escalate_vlm_review` with an explicit question when human judgement is needed. Only owner-approved findings carry owner instruction authority.
- The facade owns materialisation, fixed export, script preparation and job admission. Do not separately shell-launch its Blender export or Editor. Shell remains available for ordinary scoped authoring/inspection, not bypassing this durable workflow.
- Use `list_items` to discover current-project assets by name, description, tags and recorded provenance, then `get_version` for exact immutable source identity. Never inspect private SQLite or choose filesystem-latest.
- The host workflow resolves the pinned Blender closure and uses its fixed evaluated-mesh exporter. Never invoke checkout helpers from a user project. Metres, Blender XYZ to Unity XZY, explicit winding correction and finite geometry are required. Missing/ambiguous persistent object/instance IDs are failures, not names to guess from.
- The host project-scoped preparer supplies runtime/editor scripts. Existing local script edits, live Editors and lockfiles are blockers. Preserve unrelated scene objects and every existing `.meta`.
- Configure only basic CharacterController movement, camera look, gravity, floor/wall collision and a supported non-penetrating spawn. WASD moves, mouse looks, R resets. Escape releases the cursor and a Game-view click captures it. Perspective and orthographic cameras are supported. Do not add cast, wardrobes, HUDs, routes, cinematic transitions, web players or private reference assets.
- A repeat import replaces only the selected source-ID-owned scene subtree. Mesh/material asset files are updated in place through Unity APIs. The source manifest records the version, closure digest and export hash.
- Import, structural validation, Linux build and PlayMode tests are different operations. Structural validation never becomes a passed playtest. Inspect the actual job logs, scene path, pipeline and XML before reporting status.
- Return the contract `UnityResult`, with `validation.status` exactly `passed`, `failed` or `not_run`. Failed/skipped PlayMode validation does not prevent an otherwise valid source publication. State limitations separately.
- After an interruption, reconcile the existing attempt's process identity and receipts. Never relaunch a live or uncertain attempt. A verified failed/cancelled local job requires the shared `JobRetry` operation before another attempt.
- Checkpoint only completed authored Unity source, `.meta`, package/settings files through `SourceService.checkpoint`. Use its receipt SHA for publication. Never use a private Git wrapper, stage runtime caches, infer creative approval or push remotely.
- Return publication-ready source references and ancestry to the lead. `checkpoint_sources` and `publish_outputs` remain the accepted publication path after the job settles. The workflow facade does not infer a creative brief, plan cuts or auto-publish output. Respect the owner's package-installation permission policy when preparing a new project.
- Use `checkpoint_sources(entry_path=...)` with dependencies/`required_runtime` when needed, then pass the receipt's `source_closure` directly to `publish_outputs`. Never script closure sorting or SHA-256 digests. Complete your bounded slice directly without unnecessary nested delegation and load `unity` first when `skill` is exposed.
