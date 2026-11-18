# Native authoring profiles

The host builds `authoring-specialist` and `unity-specialist` using the pinned Python SDK's
`custom_agents`. This is a product-native specialist, not an OpenCode agent.

- Lead defaults to `gpt-6.1-sol`.
- The lead uses `task` to delegate multi-step authoring, storyboards, image/mesh
  generation waves and Blender builds to `authoring-specialist`, or Unity workflows
  to `unity-specialist`. Bounded specialists complete their slice directly and load
  the matching domain `skill` first when exposed.
- Private `runtime.worker_model` is required. Set an explicitly available economical
  model for ordinary use. An unavailable required model must fail, never silently
  fall back. The bounded proof explicitly chose `gpt-6.1-sol` for both roles.
- `runtime.worker_tools` is an explicit native tool-name list. Include configured
  Blender MCP tool names when giving that specialist authoring responsibility.
  The host never invents an MCP tool name from a UI label.
- The default authoring scope includes `skill`, `get_project_capabilities`,
  `validate_authored_document`, `prepare_image_generation`, `get_job`, `get_artifact`,
  `get_version`, `list_job_frames` and `list_items`. An explicit owner-supplied
  `runtime.worker_tools` list remains exact; `get_project_capabilities` reports any
  omitted recommended names under `missing_worker_tools` rather than silently widening
  an operator override.
- `get_project_capabilities` inspects exposed tools/skills and performs bounded read-only
  provider checks without starting services or reserving budgets.
- `validate_authored_document` validates `AuthoredShotPlan`, `SequenceMap` and
  `PublicationSubmit` payloads (plus shot-plan diff/impact) in memory using the host's
  installed contract schemas without shell Python imports or snapshot materialisation.
- `prepare_image_generation` builds and registers deterministic prompt/workflow artefacts
  for the active host image profile/recipe without consuming a generation attempt.
  Omit `project_id` and `generation_task_id` because the host binds them. Omit width,
  height and steps to use capability `default_dimensions`/`default_steps`, unless the
  user requested valid multiple-of-16 dimensions within `max_pixels`.
- `list_items` adds read-only catalogue discovery by name, description, tags and known
  provenance. Its cursors bind the active project and case-insensitive query.
- `assemble_sequence` resolves an exact published sequence and its frame receipts in the
  host. Do not ask the model to invent frame hashes or admission digests. Source cut,
  hold/drop and transition choices remain agent-authored and published first.
- `unity-specialist` has an explicit scope including `run_unity_workflow`,
  `get_unity_result`, `get_project_capabilities`, `validate_authored_document`,
  source/catalogue reads, checkpoint/publication, native view and shell.
  Its required model is `runtime.unity_worker_model`, defaulting to the already required
  `runtime.worker_model`. There is no model fallback. Its prompt includes the installed
  `agent/skills/unity/SKILL.md` as well as the shared project/security instructions
  and bounded specialist instructions.
- `runtime.unity_editor` is an optional absolute configured executable path, required to
  run Unity workflows. No user installation path is guessed. Blender export uses the
  owner's existing `rendering.blender` setting and a fixed bounded export entry point.
- Unity workflow results are admitted jobs. A tracked native supervisor continues outside
  the model callback. Read `get_job` and `get_unity_result`; never claim completed PlayMode
  validation merely because launch succeeded. Parent lifecycle must compose the workflow
  service's reconciliation, cancellation and async close hooks.
- `runtime.skill_directories` contains absolute Proscenium-adapted skill roots.
  Do not point it at reference-project or OpenCode skills.
- The active working directory is the only project scope. No sibling projects,
  secrets, `.env*`, credential stores or native runtime state may be inspected.
- The host supplies immutable root `extension_context` and native-child
  `additionalContext` (including `request_target`, `reference_versions` and
  `execution_scope`) resolved through persisted invocation attribution, preserving the
  latest acknowledged Steer context across `continue_work` Resume while keeping the
  originating `generation_task_id`. The native runtime renders child context JSON inside
  `<system_reminder>`.
- Source checkpoints use `checkpoint_sources(entry_path=...)` with dependencies
  and `required_runtime` when needed. Pass its receipt's `source_closure` directly
  to `publish_outputs`. Never script closure sorting or SHA-256 digests. Preserve
  unrelated staging.
- Read metadata through native tools, never private SQLite. Inspect registered
  images with `get_artifact(artifact_id=..., include_image=true)`, which verifies
  ready bytes inline without writing files. Never copy managed artefact content
  just to call `view`. Wait using `get_job(job_id=..., wait_seconds=15)` rather
  than rapid polling. The bounded range is `0..30` seconds.
- `request_vlm_review` admits a structured critique job; active work may call
  `get_vlm_review(review_id=..., wait_seconds=15)` to read its own validated findings
  for bounded self-correction within the task budget, or `escalate_vlm_review` with an
  explicit question when human judgement is needed in **Critic**. Only owner-selected
  `approved_findings` in host context carry owner instruction authority.
  Request read-only visual assessment for produced storyboards, concepts, keyframes
  and renders when capabilities report a `configured` or `ready` VLM provider.
- Use `submit_generation` for inference and preserve the original generation task.
  Status reads, delegation, steering and Resume do not reset its attempt budget.
- Assign one live Blender writer and Git index owner. Independent jobs retain
  their own identity and cancellation. Never stop a provider to stop Copilot.

`copilot.native.LEAD_INSTRUCTIONS` applies to root create/resume sessions.
`SPECIALIST_INSTRUCTIONS` applies to authoring specialists, with Unity-specific
instructions for `unity-specialist`. Both retain `SHARED_INSTRUCTIONS`.
`INSTRUCTIONS` remains an alias to `LEAD_INSTRUCTIONS` for import compatibility.
