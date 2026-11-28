---
name: hero-assets
description: Shortlist hero assets from a storyboard, refine one design at a time with the director, and generate standalone concepts only on explicit instruction.
---

# Hero assets from a storyboard

## 1. Shortlist from the whole board

- Call `get_project_capabilities` first. Honour `request_target`, pinned
  `reference_versions` and `execution_scope`. Read the active storyboard at
  `storyboards/<plan_id>/plan.json`, or use `get_version(version_id=...)` and read
  the canonical plan at the pinned publication's source commit. Do not silently
  substitute the working copy for a pinned version.
- Inspect ready shot frames and relevant attachments inline with
  `get_artifact(artifact_id=..., include_image=true)`. Never use bash `cp` to copy
  content from `.proscenium/artefacts/` for viewing. Use `list_items`, following
  all pages, to find existing catalogue assets to reuse or flag for a decision.
- Present **one complete hero-asset shortlist in Conversation**. For each asset,
  give a stable `asset_id`, title, shot IDs, why it needs bespoke definition,
  existing catalogue matches, and candidate panel or attachment `artifact_id`s
  for style or form. Name any missing reference, emblem or sketch the director
  could supply. Promote an asset when the audience must notice it, read a clue on
  it, recognise it across shots or interact with it in playable space. Recurring
  background walls and cobbles stay modular scenery unless explicitly promoted.
- Invite the director to start with the first listed asset or pick one by ID or
  name. Stop there: write no files, publish nothing and submit no generations.
  If delegated to `authoring-specialist`, return the **entire shortlist and
  invitation** to the lead for the director, not a summary.

## 2. Refine one definition at a time

- Start with the first listed asset unless the director chooses another. Resolve
  a name, ordinal or “next” to the stable `asset_id` from the shortlist.
- Show **only that asset's proposed definition card**. Separate what the panels
  actually show from proposals for unseen angles. Specify silhouette and
  proportions/scale, materials and condition, readable clue or gameplay
  affordance, and cross-shot continuity (including distant readability versus
  close-up detail and the playable Unity handoff where relevant).
- Recommend a concept view, such as a clean three-quarter view on neutral ground
  for a 3D prop or a straight-on orthographic view for a seal. List the proposed
  ordered `reference_artifact_ids` as `image 1`, `image 2`, etc. and say what each
  contributes. Flag missing panels or sketches rather than inventing an ID.
- Stay on that asset until the director accepts, revises, merges, drops, supplies
  a reference or moves on. Flag effects on other assets or shots by ID without
  silently editing them or `plan.json`. Accepting the written definition settles
  the design brief only. It never authorises image generation.

## 3. Generate only on explicit instruction

On explicit instruction and within `execution_scope`, author parametric humanoid
heroes in Blender with `mpfb2` or source existing humanoids with `mixamo`.

1. Proceed only on explicit director instruction to generate and when
   `execution_scope` is not `planning_only`. Verify each conditioning image has
   `state == "ready"` with `get_artifact(artifact_id=..., include_image=true)`.
   If a requested panel is `missing` or `proposed` with `artifact_id: null`, report
   the gap before spending an attempt. Never invent references or silently
   substitute SVG/vector art for inference.
2. Never target a `storyboard_frame` or overwrite its artefact. For multiple hero
   assets, or when establishing durable entries first, write each agreed spec at
   `assets/<asset_id>/spec.md`. Validate the `publication_submit` with
   `validate_authored_document`. Checkpoint with
   `checkpoint_sources(entry_path="assets/<asset_id>/spec.md", ...)`, omitting
   `retained_files`, and use the exact returned `source_closure` in the final
   validated `publish_outputs` request. Publish each as `kind: "asset"` with
   `artifact_ids: []`, `preview_requests: []` and
   `derived_from: [storyboard_version_ref]` when derived from a published board.
   Prepare each concept with its own `target_slot: {"kind": "item", "item_id": "<asset_id>"}`
   so each asset has an independent generation allowance. Reuse an existing item
   for the same asset rather than publishing a duplicate.
3. For one standalone concept before publication, use the active new asset creation
   intent or a project-level request with the board pinned as a reference. Omit
   unpublished `target_id` and `target_slot` from `prepare_image_generation` so the
   host binds the creation intent/task, not a storyboard item. After verifying the
   result, publish `kind: "asset"` with the concept artefact ID in
   `PublishedItem.artifact_ids`. Existing storyboard and scene versions stay
   pinned until explicit adoption. `character`, `motion` and `wardrobe` are optional
   searchable asset tags, not item kinds. Pin a published hero asset for an
   individual shot with `Shot.planned_assets` in a later storyboard revision
   without mutating earlier boards.
4. On `comfy-image-1`, pass 1–4 distinct `reference_artifact_ids` in deliberate order.
   Name what `image 1`, `image 2`, etc. contribute in the prompt. For example,
   use `image 1` for the sketch silhouette and `image 2` for shot lighting, patina
   and palette, not the room composition. Respect the single exact 512×512
   reference requirement for either SD-Turbo profile. Follow
   the `image-generation` skill: prepare the standalone
   concept and pass the exact `submit_request` to `submit_generation`.
5. Wait with `get_job(job_id=..., wait_seconds=15)` and inspect the output inline
   with `get_artifact`. If capabilities report a `configured` or `ready` VLM
   provider, call `request_vlm_review` with a short `title`, visual `criteria`,
   and the output and reference comparison images in `targets`, then read
   `get_vlm_review(review_id=..., wait_seconds=15)`.
   Report execution completion, VLM self-check, catalogue publication and owner
   selection separately. No generation or assessment selects media for the owner.

See the [worked example and spec template](templates.md).
