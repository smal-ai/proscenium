# Native authoring profiles

The host builds `authoring-specialist` using the pinned Python SDK's
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
