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
   authoritative. Report `capability_unavailable` if the server/tool is absent.
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
7. Call `request_preview` against the pinned published source. Pass the exact host
   camera for “Render this view”. A preview receipt admits a job, not a finished
   render. Observe its real state and registered outputs.
8. Use the budgeted generation adapter for inference. Do not install/start/stop
   ComfyUI, LM Studio, Unity or other independent services as a workaround.
9. A VLM review ticket is not a critique. Do not retrieve unapproved findings into
   model context. Only owner-selected findings in host context authorise repairs.
10. After compaction re-read the brief and current plan. Stop is not rollback.
    Late publication/job results belong to their original source, work and attempt.
