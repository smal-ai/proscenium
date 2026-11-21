---
name: storyboard
description: Pitch a complete timed storyboard from a creative brief, then refine it with the director one shot at a time. Also author or revise stable-ID shot plans and review existing frames without generating media implicitly.
---

# Storyboard direction

## Pitch the whole board first

When developing a new storyboard with the owner, give them **one complete first
pass in Conversation**, from opening image to final frame. Do not stop at a logline,
offer a menu of treatments or ask them to design each shot before they can see the
whole. Use the brief and pinned references. Make a choice where the brief leaves
room and label material assumptions. If the request already targets a shot on an
existing board, go straight to that shot instead.

- Open with the working title, narrative hook and visual direction. Say what the
  audience should discover, and how light, colour, composition or sound will reveal
  it. Honour constraints such as silence: a clue must be readable without hearing it.
- Give **every shot** a stable `shot_id`, a short title and a proposed in/out time.
  Choose the shot count and holds for this story. Check that the ranges cover the
  proposed running time without gaps or double-counting titles and transitions.
- In each card, name the discovery and its visible evidence. Describe the opening
  composition, the action and the image or movement that motivates the cut.
  Specify framing, camera position and movement (or a deliberate hold). Give exact
  intertitle or UI wording and its placement when relevant. Include a production
  note only if it explains how to achieve a particular image or respect a constraint.
- Connect the shots: name where a clue is planted, where it pays off and which
  sightlines, props or light sources must agree. For a cinematic-to-game handoff,
  match the last cinematic frame to the first playable view: camera pose, a shared
  landmark, light, the instant control changes and the player's first available
  action. If input starts before a planned camera move, say who makes that move.
  Establish aspect ratio and horizontal or vertical field of view before claiming
  a lens match. A percentage is not an angle. If the reference is missing, pitch
  the framing and mark the match unverified.
- End the pitch by inviting the owner to pick a shot, or suggest the one with the
  most consequential choice. Stop there. If delegated, return the **whole pitch**
  and that invitation to the lead for the owner, not just a completion summary.

## Refine one shot at a time

Start with the opening shot unless the owner chooses another. Resolve a title,
number or “next” to its stable shot ID on the current board. Show **only that
shot's revised card**, making clear what changes on screen or when. If there is
a real choice, explain what the audience would learn from each option and
recommend one. Stay on that shot
until the owner accepts it, skips it or chooses another. Do not treat silence or
moving on as agreement.

Keep agreed decisions by shot ID. If an edit affects another shot's timing, clue,
geography or playable starting pose, flag the affected IDs and the consequence.
Do not silently rewrite those shots. When the owner finishes the review, present
the complete revised board, including untouched shots. Recheck the total and the
continuity and mark decisions still open.
See the [worked example](templates.md#worked-example-pitch-and-revision).

A discussion-only pitch or revision stays in Conversation. It is not a request to
write files, publish a version, generate panels, render or build a scene. An agreed
creative direction is not an owner media selection. When the request includes
authoring or publication, use the workflow below within that scope. Do not impose
this conversational review loop on an explicit autonomous production request.

## Author or publish a canonical plan

1. Read the active-project brief, selected treatment, current request, pinned
   references and owner selections. Resolve conflicting inputs explicitly. Never
   inherit another project's subject, cast, shot count, numbered takes, prompt
   fragments, durations or service endpoints. No styles directory is required.
2. Maintain the canonical brief/treatment in the project's chosen authored brief
   source. Store each plan at `storyboards/<plan_id>/plan.json` as **AuthoredShotPlan**,
   validated by installed `proscenium.contracts`. See [templates.md](templates.md).
   Do not add `source_commit` or `owner_selected` to authored JSON. Publication
   supplies the commit and the owner read model supplies selection independently.
3. Keep plan, shot and frame IDs stable across targeted revisions. Array order is
   presentation order, not identity. New intent gets a new ID only when it is a
   genuinely new entity. Git/source publications retain previous plans. Never
   renumber unrelated shots or carry owner selections across version slots.
4. Describe each shot's intent, camera intent and motion beats. Order frames as
   start, zero or more intermediate compositions, then end. Keep reference role
   explicit in each frame's role/composition and its actual artefact identity.
   Missing images use null artefact IDs and missing/proposed states, not invented
   images. List unresolved assets, decisions, references and bindings explicitly.
5. Pin asset dependencies by item/version. Bind actual authored Blender scenes
   using `scene_version`, inclusive `source_range` and rational `fps` only when
   known. Otherwise use null and explain the gap in `unresolved`. Preserve proposed
   editorial holds, cuts and title cues as prose in `motion_beats`, not invented
   frame ranges, frame rates or new JSON fields. Review-card pacing is UI-only.
   Camera/marker/timeline authoring belongs to Copilot/native tools, not to the
   storyboard browser.
6. Call `get_project_capabilities` first to check exposed skills, tools and providers.
   Validate with `validate_authored_document`, `document_kind: authored_shot_plan`
   and the active-project-relative `path`. Set `compare_to_path` to the previous plan
   and `changed_ids` to changed artefact/version IDs for deterministic impact checks.
   The host reports changed/removed IDs, affected
   downstream scene versions and dependent artefacts. It never rewrites sources,
   regenerates images or resolves dependencies by filesystem modification time.
7. For publication, checkpoint the exact owned brief/plan sources through
   `checkpoint_sources(entry_path=...)`, with dependencies/`required_runtime` when
   needed. Omit `retained_files` (registered frame/concept image artefacts belong in
   `plan.json` frames and `PublishedItem.artifact_ids`, never in `retained_files`).
   Pass the receipt's `source_closure` directly to `publish_outputs`.
   Never script closure sorting or SHA-256 digests.
   Publish through the configured accepted source/publication tool with exact
   dependency IDs, original idempotency key and empty preview requests for
   planning-only work. Read the receipt before claiming success. No direct DB
   writes or parallel catalogue. Reconcile uncertain acknowledgement before retry.
8. Inspect images with `get_artifact(artifact_id=..., include_image=true)`, never
   copy managed artefact content just to call `view`. For produced storyboards and
   keyframes, call `request_vlm_review` (with a short `title` and concise visual
   `criteria`) when capabilities report a `configured` or `ready` VLM provider, read
   `get_vlm_review(review_id=..., wait_seconds=15)` for bounded self-correction, and
   use `escalate_vlm_review` with an explicit question when human review is needed.
   Only owner-approved findings carry owner instruction authority.
   Use source-type labels.
   Missing frames stay missing. Keep authored availability/proposal, execution
   completion, agent assessment, exact owner selection and animation approval
   separate. A selected concept is not a render or geometry.

```json
{
  "schema_version": "1.0",
  "plan_id": "plan-id",
  "project_id": "project-id",
  "treatment": "Brief visual direction",
  "brief_artifact_ids": [],
  "asset_dependencies": [{"item_id": "asset-id", "version_id": "asset-v1"}],
  "shots": [{
    "shot_id": "shot-01",
    "intent": "Narrative beat",
    "camera_intent": "Camera framing",
    "motion_beats": ["Subtle motion beat"],
    "scene_version": null,
    "source_range": null,
    "fps": null,
    "frames": [
      {"frame_id": "shot-01-start", "role": "start", "composition": "Opening composition", "artifact_id": null, "state": "missing"}
    ]
  }],
  "unresolved": []
}
```

```json
{"schema_version":"1.0","project_id":"project-id","document_kind":"authored_shot_plan","path":"storyboards/plan-id/plan.json","compare_to_path":"storyboards/plan-id/before.json","changed_ids":["asset-version-id"]}
```

Validate the complete publication with `document_kind: publication_submit` before
`publish_outputs`. Its storyboard `source.entry_path` is the canonical plan JSON,
never an HTML/SVG/Markdown viewer. Workspace file links are not browser URLs.
Honour host `request_target`, `reference_versions` and `execution_scope`.
For requested model inference use `prepare_image_generation` then `submit_generation`.
Omit preparation `project_id`/`generation_task_id` because the host binds them.
Omit width/height/steps to use capability recipe defaults unless the user requested
valid multiple-of-16 dimensions within `max_pixels`. Wait for admitted jobs with
`get_job(job_id=..., wait_seconds=15)` (bounded `0..30`), not rapid polling.
Never use silent vector substitutes. A standalone asset needs no storyboard. Publish it
as `asset` and keep dependent scenes/storyboards pinned until explicit adoption.

Planning, image generation, Blender authoring and assembly are separately invocable.
A planning-only edit, navigation or gallery rebuild **never submits generation**.
An approved broad production task may progress across those stages autonomously
through configured budgeted tools. Do not impose approval after every stage. Pause
only at requested checkpoints, material missing decisions, consequential owner
actions or exhausted budget. Do not start/stop/install shared services.

Resource origin: [research notice](../research/NOTICE.md). All necessary planning
templates and helper code are maintained in this package, not the reference checkout.
