---
name: blender-authoring
description: Author and inspect Blender content inside the active Proscenium project using the configured Blender MCP bridge and accepted checkpoint/publication tools.
---

# Blender authoring

1. Read the host's structured context, project brief and current plan. Child
   additionalContext is host JSON rendered in the native `<system_reminder>`.
   Preserve every annotation's own frame target, camera, range and object instance.
2. Use the explicitly configured `mcp-for-blender==2.0.0` server connected to its
   external live add-on. Do not install or replace the add-on. Tool discovery is
   authoritative. If the socket bridge is absent, use the accepted launcher from
   the installed Proscenium repository: `python3 -m tools.blender doctor --project
   <actual-active-project>` then `python3 -m tools.blender start --project
   <actual-active-project>` (add `--virtual-display` only when needed). Do not invent
   a home-directory project/default. An occupied, dirty, outside-project or uncertain
   instance is preserved, never force-closed or replaced. A readiness timeout needs
   inspection of the original launch receipt before any retry. A missing configured
   MCP server/add-on remains `capability_unavailable`, not permission to install one.
3. Before mutation inspect `get_addon_status`, `get_scene_info`,
   `bpy.data.filepath` and `bpy.data.is_dirty`. Preserve unsaved work. An unsaved
   scene or a scene outside the active project requires an owner decision, not a
   new-file command or an overwrite. Do not close an independent Blender session.
4. Assign one authoring owner per live instance and use the accepted toolchain's
   resource lock and base-state checks. A stale base is `source_conflict`.
   Never interpret parallel tool availability as permission for concurrent writers.
5. Author automatically within the human request. Keep relevant source files in
   the active project. Do not inspect credentials, environment files, siblings or
   historical tasks from a reference project. Do not inherit a particular scene,
   character, storyboard, export target or creative task from these instructions.
6. Call `checkpoint_sources` with the explicitly owned file set before
   `publish_outputs`. Preserve unrelated changes and staging. Never `git add .`.
   Missing accepted services are a limitation, not permission to invent a receipt.
   Keep the active `project_id` and host work/invocation authority. Assign stable
   item/version IDs, pin the returned commit and exact source-closure digest, and
   use head compare-and-swap. Distinct operation/publication/intent/job IDs are not
   interchangeable. A matching retry keeps its original identity and input.
   `CheckpointRequest.expected_head` is the observed repository HEAD, not the item
   version ID. Publish the receipt's `source_commit` and use
   `expected_current_version_id` for the item head. Before proxy export, authored
   meshes carry stable `proscenium_object_id` and `proscenium_instance_id` custom
   properties. Instance IDs are unique, even when instances share an object ID.
   Do not substitute object names or transient glTF indices for those identities.
7. Call `request_preview` against the pinned published source. Pass the exact host
   camera for “Render this view”. A preview receipt admits a job, not a finished
   render. Observe its real state and registered outputs.
   Use `get_version(version_id)` for the pinned publication/source record and
   `list_job_frames(job_id, cursor?, limit?)` for ready frame receipts, following
   `next_cursor` until null. Call `get_artifact(artifact_id)` for each desired
   output's metadata. For `state: ready`, the canonical project-relative byte path
   is `.proscenium/artefacts/sha256/<Artifact.sha256>/content`. The full hash and
   `bytes` identify the expected content, not the artefact ID or latest filename.
   Verify that identity before native image/file inspection. Missing/changed bytes
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
9. A VLM review ticket is not a critique. Do not retrieve unapproved findings into
   model context. Only owner-selected findings in host context authorise repairs.
10. After compaction re-read the brief and current plan. Stop is not rollback.
    Late publication/job results belong to their original source, work and attempt.
