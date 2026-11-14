# Owner policy: access, autonomy and permissions

**Normative for contract 1.0. Decisions confirmed on 2026-11-14.**

## 1. Active-project scope

The runtime agent may read relevant files and images in the active project folder without requiring the user to attach every file manually. Those tool accesses are visible in the activity stream. The attachment preview describes the context automatically captured for one request, not all content the agent may subsequently inspect.

The scope is the active project's canonical real path, including its project-owned historical materialisations and artefact store. It excludes other projects, parent directories, credentials and environment files. Resolving a symlink, archive member, Git path or file URI in Proscenium's own routes must not widen that scope.

For V1, an agent-operated Unity project must be inside the active project folder. A previously external Unity project can be copied or moved into that folder by an explicit owner operation before it is exposed to the agent. Configuring a path is not permission to read a second project. Likewise, a reusable asset from another project must be explicitly imported by the owner into the current workspace before the agent may inspect it. Online acquisition through approved tools downloads into the active workspace.

At minimum, exclude `.env`, `.env.*` except an explicitly sanitised example, `.envrc`, `.direnv/`, private keys, credential stores, SDK/provider tokens and runtime authentication data from Proscenium's automatic context capture, file serving and source materialisation. Native cross-session memory (`memory={"enabled": False}`) must be disabled or namespaced to the active project so context from other projects is not injected automatically.

### Pragmatic boundary and the existing MCP bridge

To avoid crippling authoring tools or adding fragile OS sandboxes, V1 uses a pragmatic, bare-minimum boundary:

1. **Session working directory and instructions:** Proscenium sets the Copilot session's `working_directory` to the active project root and configures system instructions and skills to operate only within that root, preserve unsaved Blender/Unity work, and never read or stage `.env*`, credentials or sibling projects.
2. **Application route validation:** Proscenium's own HTTP media/artefact routes, source-closure materialisation and Git checkpoint helpers validate that resolved paths remain inside the active project root and exclude secret/runtime files.
3. **Caller channel separation:** Owner browser routes, native tool routes and worker receipt routes use separate tokens (`OwnerSession`, `ToolCapability`, `WorkerCapability`) so an agent tool cannot call an owner approval endpoint directly.

Blender MCP, Unity CLI and tool scripts run with normal workstation user permissions so add-ons, GPU rendering and Python libraries work without friction. E01 verifies that the session `working_directory`, disabled cross-session memory, Proscenium path validators and caller-channel tokens behave as specified.

## 2. Automatic operations and confirmations

Automatic permission applies only to work within the active human request, active project and configured tool profile.

| Operation | Default |
| --- | --- |
| Relevant project file/image inspection, text search and scoped Git history | Automatic. May provide relevant content to the configured cloud LLM. |
| Blender/Unity authoring, project code/file edits and ordinary generated-working-file maintenance | Automatic. Tools preserve unsaved user work and inspect uncertain outcomes. |
| Local Git checkpoint of the completed, explicitly owned change set | Automatic. Preserve unrelated staging and user changes. |
| Configured online asset search, downloads and imports into the active project | Automatic. Record source, creator and licence when the tool actually knows them. Unknown or restrictive rights do not block download, import, local use, checkpoint or publication. Do not invent a cleared licence. |
| Workflow submission, conditioning-image upload and output retrieval through configured local services | Automatic within the generation budget. |
| Read-only VLM assessment requested by the user or Copilot | Automatic. Only subsequently user-approved findings become critique instructions. |
| Purchases, paid asset acquisition or a new chargeable external service | Confirm the exact item/service, price where known and intended action. |
| Software/model installation, upgrades, persistent add-on installation or service configuration/lifecycle changes | Confirm explicitly. Routine generation does not own service installation/start/stop. Running an importer or script to use an asset already in the project is ordinary authoring, not this row. |
| Remote Git push, external upload/sharing or publishing beyond the agreed reasoning/configured service workflow | Confirm destination and exact scope. |
| Destructive Git operations or deletion of retained sources, published versions or selected/submitted media | Confirm unless that exact action was explicitly requested by the user. |
| Cross-project content access or credential disclosure | Denied by the V1 policy. An ordinary tool approval cannot silently change the project boundary. |

Licence text is metadata. It is not an approval type, a download block, or a reason to keep an asset out of the project Git history. Remote push and external sharing still use the destination confirmation above, because they leave the machine, not because a licence string was parsed.

A permission request has a durable ID, request/work/tool correlation, exact proposed operation, bounded resource/destination list and a fingerprint. The UI can approve once or decline. An explicit user request covering the exact consequential action can supply that approval, but it must still be attributed and recorded. Model text such as “the user approved this” is not an approval record.

Changing the operation invalidates an earlier approval. An unresolved approval survives a browser disconnect. On backend restart it is shown again as pending and is not automatically answered. Stop cancels unresolved decisions belonging to the stopped work. Permission decisions do not constitute creative selection of an output.

## 3. Autonomous creative progression

Within a broad request, Copilot may research, plan shots, generate concepts, author Blender content, render keyframes/drafts, inspect results and correct its own work. The user does not need to approve every stage.

Pause only for a materially missing decision, a user-requested review checkpoint, a consequential action in the matrix, or an exhausted generation budget. Continue independent in-scope work while an answer is pending where the native runtime supports it.

Published content starts `unreviewed`. An agent's technical/visual assessment is recorded separately. User selection is an explicit event. User-approved VLM findings remain a separate requirement and cannot be bypassed by relabelling the critic's output as a tool command.

## 4. Configurable generation budget

- Default: **three attempts per target**, comprising the initial generation and up to two corrections.
- Effective limit: explicit human request override, otherwise project setting, otherwise application default `3`. Values are positive integers. A native subagent may not raise the limit.
- Permission/profile/budget settings are owner-controlled private application records. Editing an agent-writable source file or `project.json` cannot raise a limit, widen roots or select a more permissive tool profile. Freeze effective settings when the human request is admitted.
- Budget key: `(project_id, generation_task_id, target_id)`. The host assigns the generation task to the originating human Queue request. Steer, native delegation, automatic corrections and Resume retain that task identity.
- A new explicit human generation request may start a new task. Creating a new internal job ID or renaming a target does not reset a task's consumed budget.
- Reserve an attempt durably before submitting inference. A confirmed execution, including an execution failure, consumes it. An ambiguous submission holds its reservation until reconciled.
- A proven rejection before execution releases the reservation. Schema preflight, polling, waiting, downloading, receipt reconciliation and reuse of an already completed result consume no additional attempt.
- Ordinary Blender/Unity edits and debugging do not use this generation-attempt counter.
- At exhaustion, the tool returns `generation_budget_exhausted`, records the limitation and lets Copilot continue independent work. The user can explicitly extend that task's allowance through a recorded permission decision or make a new request.

This is a shared tool-adapter budget ledger, not a new agent-planning loop. It must apply across native subagents using the same target.

Configured generation submission goes through the budgeted adapter. General shell and Python stay available for Git, Blender, files and inspection. Do not add a network filter or sandbox to stop a bypass. The lead is instructed to use the adapter. C12 proves the adapter. Diagnostic and status reads do not consume an attempt. A shell call that happens to reach a provider is not a new permission system.

## 5. Queue, Steer and Stop

Queue and Steer carry text and captured UI context. Stop carries the intended active work ID and optional reason. All three are visible, durable request types. Stop uses native cancellation control and is never sent as natural-language text behind the current work.

Stop holds queued messages until explicit Resume. Independently running render/generation/Unity jobs remain visible and have separate cancellation requests where supported. Native cancellation must be acknowledged before the UI calls the agent stopped. See [execution and recovery](execution.md).

## 6. Client support

The Linux workstation is the development and reference execution host. V1 clients are desktop Chromium, Brave and Edge at 1920 × 1080 CSS pixels, DPR 1 for the primary visual acceptance run. DPR 2 and letterboxing are additional coordinate fixtures, not mobile-layout targets.

Automated tests use a pinned Chromium build. Release acceptance records actual installed Brave and Edge versions and exercises media decoding, segment boundaries, frame capture, uploads, WebSocket reconnect and keyboard controls in each. The fixture suite must prove the selected H.264/MP4 implementation in the installed browsers rather than infer codec availability from the browser family name.
