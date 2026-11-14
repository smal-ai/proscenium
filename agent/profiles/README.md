# Native authoring profiles

The host builds `authoring-specialist` and `unity-specialist` using the pinned Python SDK's
`custom_agents`. This is a product-native specialist, not an OpenCode agent.

- Lead defaults to `gpt-6.1-sol`.
- Private `runtime.worker_model` is required. Set an explicitly available economical
  model for ordinary use. An unavailable required model must fail, never silently
  fall back. The bounded proof explicitly chose `gpt-6.1-sol` for both roles.
- `runtime.worker_tools` is an explicit native tool-name list. Include configured
  Blender MCP tool names when giving that specialist authoring responsibility.
  The host never invents an MCP tool name from a UI label.
- The default authoring scope includes `get_job`, `get_artifact`, `get_version`
  and `list_job_frames`. An explicit owner-supplied list remains exact; include
  these names when that worker needs to inspect registered outputs and sources.
- `list_items` adds read-only catalogue discovery by name, description, tags and known
  provenance. Its cursors bind the active project and case-insensitive query.
- `assemble_sequence` resolves an exact published sequence and its frame receipts in the
  host. Do not ask the model to invent frame hashes or admission digests. Source cut,
  hold/drop and transition choices remain agent-authored and published first.
- `unity-specialist` has an explicit scope including `run_unity_workflow`,
  `get_unity_result`, source/catalogue reads, checkpoint/publication, native view and shell.
  Its required model is `runtime.unity_worker_model`, defaulting to the already required
  `runtime.worker_model`. There is no model fallback. Its prompt includes the installed
  `agent/skills/unity/SKILL.md` as well as the lead's project/security instructions.
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
  `additionalContext` resolved through persisted invocation attribution. The
  native runtime renders the latter JSON inside `<system_reminder>`.
- Source checkpoints use `checkpoint_sources`. Publication follows a successful
  checkpoint and uses `publish_outputs`. Preserve unrelated staging.
- Read metadata through those native tools, never private SQLite. For an exact
  ready Artifact, bytes live inside the active project at
  `.proscenium/artefacts/sha256/<sha256>/content`. Use the full recorded hash,
  verify its bytes/hash and media type, then inspect with native view tools.
  Do not substitute an artefact ID, a worker output_path or filesystem-latest.
- `request_vlm_review` returns only a ticket. Only owner-selected findings in a
  subsequent host context become critique instructions.
- Use `submit_generation` for inference and preserve the original generation task.
  Status reads, delegation, steering and Resume do not reset its attempt budget.
- Assign one live Blender writer and Git index owner. Independent jobs retain
  their own identity and cancellation. Never stop a provider to stop Copilot.

The instructions are implemented in `copilot.native.INSTRUCTIONS`, applied on
create and resume, and inherited by the explicitly scoped native specialist.
