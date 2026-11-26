---
name: image-generation
description: Generate or correct project concept images with the configured, budgeted local ComfyUI adapter, including explicit FLUX.2 Klein reference conditioning.
---

# Project image generation

- Work only in the active project and current human task. Use its host-assigned
  generation-task identity. Steer, delegation and Resume retain that task's allowance.
- Call `get_project_capabilities` first. Honour host `request_target`, pinned
  `reference_versions` and `execution_scope`. `planning_only` forbids inference,
  preview rendering and scene execution.
- For one standalone concept without a published source, use `prepare_image_generation`
  to resolve the creation intent or original generation-task target. Do not invent a fake source item or a new target ID
  for every correction. Other empty-settings targets must be existing real item IDs or
  explicitly pre-bound targets.
- For multiple storyboard frames, publish the storyboard first and use
  `settings.target_slot` with the normative SelectionSlot: `storyboard_frame` names its
  exact storyboard VersionRef, shot and frame IDs. An `assembly_shot` names its exact
  sequence VersionRef and entry ID. An `item` slot names a real project item.
  With a slot, `target_id` is only a caller alias. The host resolves the actual budget key.
  Renaming aliases or publishing a later version does not reset that frame/entry's budget.
  For multiple standalone hero assets, publish each asset first and use its own
  `item` slot. See the `hero-assets` skill.
- Discover relevant project references and identify composition, style and identity roles.
  Inspect the actual bytes. Do not copy a previous project's treatment or ask for approval
  of routine creative progression. Missing essential input is reported explicitly.
- Call `prepare_image_generation` with a stable `operation_id`, prompt, decimal
  seed and optional reference artefact IDs/target slot. Omit `project_id` and
  `generation_task_id`: the host binds them automatically. Omit width, height and
  steps to use capability `default_dimensions`/`default_steps`, unless the user
  requested valid multiple-of-16 dimensions within `max_pixels`.
  It registers exact prompt/workflow artefacts locally without consuming an attempt.
  Replay the same input/ID after acknowledgement loss. It returns the exact `submit_request`.
- Use the **host-selected, frozen image family**. The default `comfy-image-1` remains the
  FLUX.2 Klein path. When conditioning, pass 1–4 distinct `reference_artifact_ids`
  in deliberate order. The host chains `ReferenceLatent` in exact list order on
  both positive and negative conditioning. `reference_artifact_ids[0]` is `image 1`,
  `reference_artifact_ids[1]` is `image 2`, and so on. Name each image's role in the prompt.
- For an explicitly configured `comfy-sd-turbo-img2img-1` or
  `comfy-sd-turbo-img2img-fp16-1` provider, use the exact host preparation tool's pinned
  SD-Turbo profile instead. Both require one actual 512×512 reference, no implicit
  resize, the pinned checkpoint for that profile, four
  SDTurboScheduler steps at denoise 0.5 and cfg 1. LoadImage feeds VAEEncode and the sampler's
  `latent_image` while CLIPTextEncode supplies prompt conditioning. This is **img2img**,
  not FLUX ReferenceLatent. Include the actual image in `input_artifact_ids`.
- A filename in prose is never image conditioning. The host chooses the profile through
  private `provider_options.image_profiles`, not tool JSON. Do not change the family,
  checkpoint or graph of a previously admitted job, including the retained Klein job.
- Submit the returned `submit_request` through native `submit_generation`. Do not POST inference
  directly or try `retry_job` to bypass the ledger. `settings` may contain only the target
  slot above. The exact graph is the execution specification, not opaque settings.
  Image-to-3D is a separate configured capability and is not invoked by these image profiles.
- The host preflights actual node schemas/model choices, checks queues/memory and obtains
  its shared GPU claim. Busy/unavailable means waiting or an actionable failure. Never
  start/stop services, unload models, clear a global queue or substitute another endpoint.
- Retain the admitted job identity. A timeout does not authorise another inference.
  Wait with `get_job(job_id=..., wait_seconds=15)` (bounded `0..30`), not rapid
  polling. Reconcile the existing prompt and verified immutable outputs. A genuinely
  new correction is a new GenerationSubmit under the same canonical task/target allowance.
- Inspect with `get_artifact(artifact_id=..., include_image=true)`. Never copy
  `.proscenium/artefacts/.../content` just to call `view`.
  Distinguish technical execution, agent assessment and
  owner selection. Do not replace source authoring or owner selection automatically.
- A VLM review is a separate explicit `request_vlm_review` call (include a short `title`,
  concise visual `criteria` without policy boilerplate, and pass any registered `reference`
  or `start` comparison frame alongside the candidate in `targets`) when a `configured` or
  `ready` VLM provider is reported by capabilities. Call `get_vlm_review(review_id=..., wait_seconds=15)`
  to inspect your current work's validated findings for bounded self-correction within the
  remaining task budget, or call `escalate_vlm_review` with a clear question when you need
  owner judgement in **Critic**. Only owner-approved findings in host context carry owner
  instruction or selection authority.
- Report exact job/output identities and limitations. Missing telemetry is unknown, not zero.
- Publish a standalone `asset` without requiring a storyboard. Validate publications
  with `validate_authored_document` before `checkpoint_sources`/`publish_outputs`.
  Use `checkpoint_sources(entry_path=...)` with dependencies/`required_runtime`
  when needed (omit `retained_files`; registered image artefacts belong in
  `PublishedItem.artifact_ids`, never in `retained_files`), then pass its receipt's
  `source_closure` directly to `publish_outputs`.
  Never script source-closure sorting or SHA-256 digests.
  Dependent scenes/storyboards retain exact version pins until explicit adoption.
  Never silently replace requested inference with SVG/vector drawings or use relative
  workspace viewer links as browser URLs. Report retained artefact identities.
