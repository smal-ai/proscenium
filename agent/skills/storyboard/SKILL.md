---
name: storyboard
description: Author or revise a stable-ID shot plan and review existing storyboard frames without implicitly generating images or constructing scenes.
---

# Canonical storyboard planning

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
   known. Otherwise use null and explain the gap in `unresolved`. Review-card
   pacing is UI-only, never an authored duration. Camera/marker/timeline authoring
   belongs to Copilot/native tools, not to the storyboard browser.
6. Validate and compare with `python agent/skills/storyboard/plan_tools.py` using
   the commands below. The helper is read-only, deterministic and uses the shared
   validator, not a second schema. It reports changed/removed IDs, affected
   downstream scene versions and dependent artefacts. It never rewrites sources,
   regenerates images or resolves dependencies by filesystem modification time.
7. Checkpoint the exact owned brief/plan sources through `checkpoint_sources`.
   Publish through the configured accepted source/publication tool with exact
   dependency IDs, original idempotency key and empty preview requests for
   planning-only work. Read the receipt before claiming success. No direct DB
   writes or parallel catalogue. Reconcile uncertain acknowledgement before retry.
8. Review existing images through the central reviewer with source-type labels.
   Missing frames stay missing. Keep authored availability/proposal, execution
   completion, agent assessment, exact owner selection and animation approval
   separate. A selected concept is not a render or geometry.

```bash
uv run python agent/skills/storyboard/plan_tools.py validate storyboards/<plan_id>/plan.json
uv run python agent/skills/storyboard/plan_tools.py compare <before.json> <after.json>
uv run python agent/skills/storyboard/plan_tools.py impact <plan.json> --changed-id <artifact-or-version-id>
```

Planning, image generation, Blender authoring and assembly are separately invocable.
A planning-only edit, navigation or gallery rebuild **never submits generation**.
An approved broad production task may progress across those stages autonomously
through configured budgeted tools. Do not impose approval after every stage. Pause
only at requested checkpoints, material missing decisions, consequential owner
actions or exhausted budget. Do not start/stop/install shared services.

Resource origin: [research notice](../research/NOTICE.md). All necessary planning
templates and helper code are maintained in this package, not the reference checkout.
