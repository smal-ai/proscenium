---
name: image-generation
description: Generate or correct project concept images with the configured, budgeted local ComfyUI adapter, including explicit FLUX.2 Klein reference conditioning.
---

# Project image generation

- Work only in the active project and current human task. Use its host-assigned
  generation-task identity. Steer, delegation and Resume retain that task's allowance.
- For one standalone concept without a published source, use empty `settings` and
  `target_id == generation_task_id`. Do not invent a fake source item or a new target ID
  for every correction. Other empty-settings targets must be existing real item IDs or
  explicitly pre-bound targets.
- For multiple concepts/frames, publish the storyboard first and use
  `settings.target_slot` with the normative SelectionSlot: `storyboard_frame` names its
  exact storyboard VersionRef, shot and frame IDs. An `assembly_shot` names its exact
  sequence VersionRef and entry ID. An `item` slot names a real project item.
  With a slot, `target_id` is only a caller alias. The host resolves the actual budget key.
  Renaming aliases or publishing a later version does not reset that frame/entry's budget.
- Discover relevant project references and identify composition, style and identity roles.
  Inspect the actual bytes. Do not copy a previous project's treatment or ask for approval
  of routine creative progression. Missing essential input is reported explicitly.
- Register the exact API graph and UTF-8 prompt as project artefacts. Use `$prompt` in
  CLIPTextEncode and `$seed` in the noise input. Supply a decimal seed and exact model filenames.
- Use the **host-selected, frozen image family**. The default `comfy-image-1` remains the
  FLUX.2 Klein path: `artifact:<id>` in LoadImage feeds VAEEncode, ReferenceLatent and the
  positive CFGGuider. `tools/comfy/graph.py` constructs that graph.
- For an explicitly configured `comfy-sd-turbo-img2img-1` provider, use the exact
  `proscenium.providers.sd_turbo.graph(reference_id)` profile instead. It requires one
  actual 512×512 reference, no implicit resize, the pinned `sd_turbo.safetensors`, four
  SDTurboScheduler steps at denoise 0.5 and cfg 1. LoadImage feeds VAEEncode and the sampler's
  `latent_image` while CLIPTextEncode supplies prompt conditioning. This is **img2img**,
  not FLUX ReferenceLatent. Include the actual image in `input_artifact_ids`.
- A filename in prose is never image conditioning. The host chooses the profile through
  private `provider_options.image_profiles`, not tool JSON. Do not change the family,
  checkpoint or graph of a previously admitted job, including the retained Klein job.
- Submit through the native `submit_generation` tool, which calls
  `extensions['providers'].submit(caller, GenerationSubmit JSON)`. Do not POST inference
  directly or try `retry_job` to bypass the ledger. `settings` may contain only the target
  slot above. The exact graph is the execution specification, not opaque settings.
  Image-to-3D is a separate configured capability and is not invoked by these image profiles.
- The host preflights actual node schemas/model choices, checks queues/memory and obtains
  its shared GPU claim. Busy/unavailable means waiting or an actionable failure. Never
  start/stop services, unload models, clear a global queue or substitute another endpoint.
- Retain the admitted job identity. A timeout does not authorise another inference.
  Poll/reconcile the existing prompt and download verified immutable outputs. A genuinely
  new correction is a new GenerationSubmit under the same canonical task/target allowance.
- Inspect the generated image. Distinguish technical execution, agent assessment and
  owner selection. Do not replace source authoring or owner selection automatically.
- A VLM review is a separate explicit `request_vlm_review` call. Supply only clean target
  images, criteria and explicitly selected telemetry. Its native result is ONLY a review
  ticket/status. Findings remain in the owner readstore until the owner submits them.
- Report exact job/output identities and limitations. Missing telemetry is unknown, not zero.
