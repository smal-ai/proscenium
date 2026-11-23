---
name: blender-authoring
description: Author and inspect Blender content inside the active Proscenium project using the configured Blender MCP bridge and accepted checkpoint/publication tools.
---

# Blender authoring

1. Read the host's structured context, project brief and current plan. Child
   additionalContext is host JSON rendered in the native `<system_reminder>`.
   Preserve every annotation's own frame target, camera, range and object instance.
2. Use the explicitly configured `mcp-for-blender==2.3.0` server connected to its
   installed external live add-on (2.0, protocol 16). Never reinstall or overwrite
   the add-on. Tool discovery is
   authoritative. Call `get_project_capabilities` to check the configured bridge.
   If absent, report the capability blocker rather than invoking checkout-only helpers.
   Do not invent
   a home-directory project/default. An occupied, dirty, outside-project or uncertain
   instance is preserved, never force-closed or replaced. A readiness timeout needs
   inspection of the original launch receipt before any retry. A missing configured
   MCP server/add-on remains `capability_unavailable`, not permission to install one.
3. Before mutation inspect `get_addon_status`, `get_scene_info`,
   `bpy.data.filepath` and `bpy.data.is_dirty`. Preserve unsaved user work: if a
   scene is dirty (`bpy.data.is_dirty` is true), saved outside the active project,
   or contains non-default unsaved objects beyond a pristine factory startup scene
   (`Camera`, `Cube`, `Light` with `filepath == ""` and `is_dirty == False`), ask
   an owner decision rather than overwriting. A pristine untouched factory startup
   scene may be initialised directly for the active project without asking. Do not
   close an independent Blender session.
   Never call `bpy.ops.wm.read_factory_settings` or disable add-ons in
   `blender-execute_blender_code`. This applies with or without `use_empty=True`.
   A factory reset unloads `blender_mcp` and closes the `127.0.0.1:9876` socket
   bridge. Clear authorised scene data in place via `bpy.data`. Use
   `bpy.data.objects.remove`, `bpy.data.collections.remove` and
   `bpy.data.scenes.remove` only on data within the authorised edit scope and not
   shared with scenes you must preserve. Keep one authorised scene to rename.
   If needed, remove only unused datablocks from that edit, not all orphans.
4. Assign one authoring owner per live instance and use the accepted toolchain's
   resource lock and base-state checks. A stale base is `source_conflict`.
   Never interpret parallel tool availability as permission for concurrent writers.
5. Author automatically within the human request. Keep relevant source files in
   the active project. Do not inspect credentials, environment files, siblings or
   historical tasks from a reference project. Do not inherit a particular scene,
   character, storyboard, export target or creative task from these instructions.
6. Call `checkpoint_sources(entry_path=...)` with the explicitly owned file set,
   dependencies and `required_runtime` when needed. For `.blend` source checkpoints,
   supply `required_runtime` with `"application": "blender"` and the exact
   `bpy.app.version_string` as `version` (for example `"4.5.16 LTS"`), and pass
   `"features": []` by default. When needed, use only these supported Cycles CPU
   feature tags, not ad-hoc strings: `cycles`, `cycles-cpu`, `cpu`, `denoise`,
   `openimagedenoise`, `oidn`, `agx`, `mesh`, `meshes`, `native-mesh`, `curve`,
   `curves`, `font`, `text`, `surface`, `metaball`, `materials`, `modifiers`,
   `subsurf`, `subdivision`, `bevel`, `solidify`, `boolean`, `array`, `mirror`,
   `gltf`, `glb`, `proxy`, `animation`, `keyframes`, `linked-libraries`,
   `linked-textures`, `external-textures`, `packed-textures`, `geometry-nodes`,
   `static-hair`, `python`. Tags describe requirements and do not enable GPU
   rendering, Python auto-execution or unsupported scene features.
   For a revision, pin the prior published `base_version` so unchanged scene
   files carry forward.
   List the changed `.blend` and other owned paths, and use `exclude_paths` only for
   inherited destinations to omit. For a large closure, pass the completed receipt's
   `operation_id` as `checkpoint_operation_id` and its exact `source_commit` to
   `validate_authored_document` (`publication_submit`) and `publish_outputs`, omitting
   `source`. For a small closure, pass the receipt's `source_closure` directly as
   `source`. Never script closure sorting or SHA-256 digests. Checkpoint before
   `publish_outputs`. Preserve unrelated changes and staging. Never `git add .`.
   Cycles CPU uses OpenImageDenoise by default (`denoise: true`). Use 32–64 samples
   for keyframes and inspection stills, or 8–16 for `draft_motion` scrub passes.
   Missing accepted services are a limitation, not permission to invent a receipt.
   Keep the active `project_id` and host work/invocation authority. Assign stable
   item/version IDs, pin the returned commit and exact source-closure digest, and
   use head compare-and-swap. Distinct operation/publication/intent/job IDs are not
   interchangeable. A matching retry keeps its original identity and input.
   `CheckpointRequest.expected_head` is the observed repository HEAD, not the item
   version ID. Publish the receipt's `source_commit` and use
   `expected_current_version_id` for the item head. Before proxy export, authored
   renderable surface objects (`MESH` plus extruded/beveled `CURVE`, `FONT`,
   `SURFACE` and `META`) carry stable `proscenium_object_id` and
   `proscenium_instance_id` custom properties. Instance IDs are unique, even when
   instances share an object ID. Do not substitute object names or transient glTF
   indices for those identities.
7. Call `request_preview` against the pinned published source (including a
   `purpose: "proxy"` single-frame preview on the primary stage so **3D Inspection**
   is populated alongside `keyframe` / `inspection` still renders). Pass the exact host
   camera for “Render this view”. A preview receipt admits a job, not a finished
   render. Observe its real state and registered outputs.
   Use `retry_job` on an earlier turn's failed scene or asset preview only when
   `request_target` pins that exact published version. If the target is
   `project`, or `retry_job` returns `scope_denied` because the failed preview
   belongs to an earlier request, call `request_preview` on the existing published
   `version` with a fresh `operation_id` and fresh `spec.intent_id`, provided
   rendering is in scope. Do not halt or republish unchanged sources.
   Use `get_version(version_id)` for the pinned publication/source record and
   `list_job_frames(job_id, cursor?, limit?)` for ready frame receipts, following
   `next_cursor` until null. Call `get_artifact(artifact_id)` for each desired
   output's metadata. For multi-scene or multi-shot animation previews, call
   `set_preview_order` with the desired preview job IDs from the published version.
   This changes the pass order in place without re-rendering. Omitted passes keep
   their relative order at the end.
   Inspect registered images with
   `get_artifact(artifact_id=..., include_image=true)`. Never copy managed
   `.proscenium/artefacts/.../content` just to call `view`. Wait with
   `get_job(job_id=..., wait_seconds=15)` (bounded `0..30`), not rapid polling.
   The host verifies ready bytes/hash inline. Missing/changed bytes
   are a known failure, not permission to guess another file. These reads neither
   launch a job nor change its state. Never inspect private SQLite/runtime stores.
   The accepted preview recipe is `renderer_revision: proscenium-blender-1`,
   `engine: CYCLES`, `device: CPU` and explicit samples. Keyframe PNGs preserve
   exact positive dimensions, including odd sizes. Keep a captured inspection
   camera's exact aspect and target dimensions. H.264 motion requires even width
   and height. Do not silently pad, crop or round a requested still to fit video.
   Respect configured host bounds. `camera: null` uses the authored camera; do not
   invent a camera override. Preview references must identify the exact published
   item/version and source digest, not live working files. Do not silently substitute
   a GPU/engine/profile or call an accepted/planned job a completed render. Inspect
   actionable tool errors and the original receipt instead of looping on failure.
8. Use the budgeted generation adapter for inference. Do not install/start/stop
   ComfyUI, LM Studio, Unity or other independent services as a workaround.
9. Request `request_vlm_review` (with a short `title`, concise visual `criteria`, and any
   comparison `reference` / `start` frame in `targets`) for produced renders/keyframes when
   capabilities report a `configured` or `ready` VLM provider. Read your current work's
   critique with `get_vlm_review(review_id=..., wait_seconds=15)` for bounded self-correction,
   or call `escalate_vlm_review` with an explicit question when human judgement is needed.
   Only owner-selected findings in host context carry owner instruction or selection authority.
10. After compaction re-read the brief and current plan. Stop is not rollback.
    Late publication/job results belong to their original source, work and attempt.

Honour host `request_target`, `reference_versions` and `execution_scope`.
`planning_only` forbids scene execution, preview renders and generation. Validate
storyboards/sequences/publications with `validate_authored_document` before checkpoint
and publication. Plan comparison uses `compare_to_path` and `changed_ids`.
Requested image inference uses `prepare_image_generation` then `submit_generation`.
Omit preparation `project_id`/`generation_task_id` because the host binds them.
Use capability recipe defaults by omitting width/height/steps unless the user
requested valid multiple-of-16 dimensions within `max_pixels`.
Create/publish standalone `asset` source without a storyboard. Existing dependent
scenes/storyboards keep their version pins until the owner requests explicit adoption.
