# Owner policy: access, autonomy and permissions

**Normative for contract 1.0. Decisions confirmed on 2026-11-14.**

## 1. Active-project scope

The runtime agent may read relevant files and images in the active project folder without requiring the user to attach every file manually. Those tool accesses are visible in the activity stream. The attachment preview describes the context automatically captured for one request, not all content the agent may subsequently inspect.

The scope is the active project's canonical real path, including its project-owned historical materialisations and artefact store. It excludes other projects, parent directories, credentials and environment files. Resolving a symlink, archive member, Git path or file URI must not widen that scope.

For V1, an agent-operated Unity project must be inside the active project folder. A previously external Unity project can be copied or moved into that folder by an explicit owner operation before it is exposed to the agent. Configuring a path is not permission to read a second project. Likewise, a reusable asset from another project must be explicitly imported by the owner into the current workspace before the agent may inspect it. Online acquisition through approved tools downloads into the active workspace.

At minimum, exclude `.env`, `.env.*` except an explicitly sanitised example, `.envrc`, `.direnv/`, private keys, credential stores, SDK/provider tokens and runtime authentication data. The prohibition applies to content from historical revisions as well as the current working tree. Raw `.git` access is replaced by scoped Git operations which return permitted project history and redacted metadata.

Installed executables, approved tool code, standard libraries and private runtime authentication are used by trusted processes as implementation dependencies. This does not expose their directories for general agent browsing. Public tool definitions and sanitised capability diagnostics may be supplied as runtime context.

Native cross-session memory, search and automatic context retrieval must be disabled or demonstrably namespaced to the active project. A scoped filesystem tool does not prevent a global native memory feature from introducing another project's content. E01 includes a cross-project native-context sentinel and records the actual SDK configuration used.

### Enforcement and the existing MCP bridge

A working directory, prompt rule or `exec` namespace does not enforce this policy. The tool profile must combine validated file operations with an execution boundary for general shell/Python/MCP capabilities. An unrestricted Blender Python endpoint can read files outside the project even when the SDK's ordinary file tool is scoped.

E01 must test a sentinel outside the project and a credential sentinel inside it through **each enabled file, shell and Blender-code path**, including symlink and Git-history routes. A profile which cannot uphold the scope is `scope_unverified` and cannot advertise project-only operation or receive automatic approval for the affected capability. It may still support proven scoped operations. A broader permission is a new owner decision, not a fallback selected by an agent.

This is a filesystem/tool-access requirement. It does not introduce a Proscenium scene planner, authoring transaction manager or creative validator. F00 records the actual enforcement mechanism and its limits before enabling general code execution. Reusing a live desktop Blender instance is allowed only when its project association and enabled tool paths satisfy that proof.

## 2. Automatic operations and confirmations

Automatic permission applies only to work within the active human request, active project and configured tool profile.

| Operation | Default |
| --- | --- |
| Relevant project file/image inspection, text search and scoped Git history | Automatic. May provide relevant content to the configured cloud LLM. |
| Blender/Unity authoring, project code/file edits and ordinary generated-working-file maintenance | Automatic. Tools preserve unsaved user work and inspect uncertain outcomes. |
| Local Git checkpoint of the completed, explicitly owned change set | Automatic. Preserve unrelated staging and user changes. |
| Configured online asset search and free downloads into the project | Automatic, subject to source terms and import checks. Running acquired code is a different operation. |
| Workflow submission, conditioning-image upload and output retrieval through configured local services | Automatic within the generation budget. |
| Read-only VLM assessment requested by the user or Copilot | Automatic. Only subsequently user-approved findings become critique instructions. |
| Purchases, paid asset acquisition or a new chargeable external service | Confirm the exact item/service, price where known and intended action. |
| Software/model installation, upgrades, add-ons or service configuration/lifecycle changes | Confirm explicitly. Routine generation does not own service installation/start/stop. |
| Remote Git push, external upload/sharing or publishing beyond the agreed reasoning/configured service workflow | Confirm destination and exact scope. |
| Destructive Git operations or deletion of retained sources, published versions or selected/submitted media | Confirm unless that exact action was explicitly requested by the user. |
| Cross-project content access or credential disclosure | Denied by the V1 policy. An ordinary tool approval cannot silently change the project boundary. |

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

Configured generation submission is exposed through the budgeted adapter. A general shell/Python path must not provide an unmetered alternative submission route to the same providers. The enabled tool profile and E01/C12 prove that routing boundary; diagnostic/status reads remain available without consuming an attempt.

## 5. Queue, Steer and Stop

Queue and Steer carry text and captured UI context. Stop carries the intended active work ID and optional reason. All three are visible, durable request types. Stop uses native cancellation control and is never sent as natural-language text behind the current work.

Stop holds queued messages until explicit Resume. Independently running render/generation/Unity jobs remain visible and have separate cancellation requests where supported. Native cancellation must be acknowledged before the UI calls the agent stopped. See [execution and recovery](execution.md).

## 6. Client support

The Linux workstation is the development and reference execution host. V1 clients are desktop Chromium, Brave and Edge at 1920 × 1080 CSS pixels, DPR 1 for the primary visual acceptance run. DPR 2 and letterboxing are additional coordinate fixtures, not mobile-layout targets.

Automated tests use a pinned Chromium build. Release acceptance records actual installed Brave and Edge versions and exercises media decoding, segment boundaries, frame capture, uploads, WebSocket reconnect and keyboard controls in each. The fixture suite must prove the selected H.264/MP4 implementation in the installed browsers rather than infer codec availability from the browser family name.
