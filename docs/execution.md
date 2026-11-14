# Execution, ownership and recovery protocol

**Normative for contract 1.0.** See [contracts](contracts/README.md) for wire shapes and [owner policy](owner-policy.md) for permissions.

## 1. Terms

| Term | Meaning |
| --- | --- |
| Request | One immutable user Queue, Steer or Stop submission and its captured context. |
| Work | The objective started by a Queue request, including its attached steering directives and native subagents. |
| Native message | The SDK/runtime-assigned message identity. It is distinct from an application request ID. |
| Model turn | One model API call. Many model turns can serve one request/work. |
| Invocation | One native subagent or tool call, with its own correlation. |
| Job | A render, encode, generation or Unity operation which may outlive a model turn or browser connection. |
| Publication | The atomic registration of completed source versions and declared preview intents. |

V1 uses one authoritative application process, one active project and at most one active root work unit. Native subagents and independent jobs can still run within that work/project. The runtime's ordinary agent loop supplies autonomous task progression. The application schedules delivery of human requests, not the agent's reasoning or tool plan.

## 2. Process and resource ownership

- Use one ASGI worker for V1. Browser connections subscribe to that owner's read model and do not create independent SDK clients.
- An OS-held application-instance lock protects the configured application registry. A project lock protects activation. A second process reports `owner_conflict`; it does not steal a lease because a timestamp appears old.
- Record an owner epoch, PID, process start identity and native session association. On a new backend boot, create a new epoch and close the work-admission gate until the user resumes.
- Every admitted invocation/job retains its work, owner epoch, source and provider identity. Late outcomes are reconciled against that original record, not attributed to whichever request is currently visible.
- Detached render workers write their own receipts. PID alone is not proof of ownership or liveness.

Before a new owner attaches for execution, the previous native owner must be quiescent or fenced. New authoring/tool admission cannot continue through an orphaned native connection after the new backend has entered its awaiting-Resume state. The selected service/runtime lifecycle must prove this boundary in E01. Already-admitted independent operations may finish and leave receipts to reconcile; unrelated desktop/provider processes are never killed to manufacture ownership.

### Authoring and Git coordination belong to tools

The lead assigns one owner for edits to a live Blender instance and one owner for the project's Git index/checkpoint operation. Other specialists can perform read-only research, produce independent files or use genuinely separate application contexts. Shared writers must use the toolchain's resource lock and base-state check.

If no Blender instance is listening on the configured MCP bridge port (`127.0.0.1:9876`), Copilot launches one through the Blender launcher tool (desktop `DISPLAY` or `Xvfb` virtual display). Before mutating a running instance, the tool/skill checks `get_addon_status`, `get_scene_info`, `bpy.data.filepath` and `bpy.data.is_dirty`. If an unsaved scene or a file outside the active project is open, Copilot pauses for a user decision rather than discarding or overwriting that work.

The lock does not approve a scene change. It protects against concurrent operations on the same application/index. A stale base returns `source_conflict` for Copilot to inspect. Routine source checkpoints stage only explicitly owned files and retain unrelated staging. A generic `git add .` is not a checkpoint protocol.

SDK permission to request multiple tools is not a serialization guarantee. E01 proves the chosen tool-path coordination, including native subagent calls. The Proscenium application does not introduce a Blender authoring transaction queue.

## 3. Request delivery

The application holds the durable FIFO of human Queue requests. It submits one eligible request to the runtime with native `enqueue`; later requests remain application-held until the active work settles. This makes Stop and restart behaviour independent of undocumented native queue persistence.

1. Validate active project, ready attachment/context references and request type.
2. In one SQLite transaction, record the immutable body hash, request, context snapshot and `request.accepted` event. A matching retry returns that record. A different body under the same ID returns `idempotency_conflict`.
3. A live owner reserves delivery and records `dispatching` before making the SDK call. Never hold a database transaction open across that call.
4. Serialise the user prompt and immutable `ContextSnapshot` using the deterministic `<proscenium_context>` envelope below, attach the resolved local image files (`attachments=[{"type": "file", "path": ...}]`), and forward once with the application request ID as `client_turn_id`. Record the native acknowledgement separately.
5. Events arriving before the acknowledgement are joined using their proven native correlation. Persist before broadcasting.
6. On an authoritative request terminal, finish that request/work only after accounting for its accepted steering directives and native children. External jobs retain their own states.

`client_turn_id` is a correlation facility, not a promised server deduplication key. E01 must prove its persisted echo and callback mapping in the pinned SDK/runtime. F00 supplies a compatibility adapter for root request, steering, subagent and tool attribution. Do not use a mutable global “current request” as the sole callback identity.

### Prompt context envelope

Every `QueueRequest` or `SteerRequest` delivered to `session.send` formats `prompt` as the user's `text`, two newlines, and a `<proscenium_context>` block containing canonical UTF-8 JSON:

```text
<user text>

<proscenium_context>
{
  "schema_version": "1.0",
  "request_id": "req-create-scene",
  "work_id": "work-create-scene",
  "generation_task_id": "gen-task-1",
  "delivery_mode": "queue",
  "context_id": "ctx-project-only",
  "project": {
    "project_id": "fixture-project",
    "path": "/home/quintin/play/fixture-project"
  },
  "selection": {
    "item_id": "scene-fixture",
    "version_id": "scene-fixture-v1",
    "view_mode": "keyframes",
    "target": { "...FrameTarget or null..." },
    "camera": { "...Camera or null..." },
    "range": { "start": 2, "end": 4 },
    "selected_object_ids": ["object-cube-source"]
  },
  "annotations": [ "...AnnotationSnapshot items..." ],
  "approved_findings": [ "...ApprovedFinding items..." ],
  "attached_files": [
    {
      "artifact_id": "image-frame-2",
      "role": "render",
      "sha256": "dddd...",
      "path": ".proscenium/artefacts/sha256/dddd.../image.png",
      "dimensions": { "width": 1920, "height": 1080 }
    }
  ]
}
</proscenium_context>
```

- `attached_files` lists the exact files passed to the SDK's `attachments` parameter in the same order: clean target renders/concepts (`role: "render"` | `"concept"` | `"reference"`), rendered markup composites (`role: "markup"`), and lossless PNG masks (`role: "mask"`).
- For `Render this view`, the UI submits a `QueueRequest` whose `selection.view_mode` is `"3d_inspection"` and whose `selection.camera` contains the complete `Camera` record (`camera_to_world`, `projection`, `aspect`, `near_m`, `far_m`, `vertical_fov_degrees` or `orthographic_height_m`, and optional `shift`), allowing Copilot to pass that exact camera to `request_preview`.
- Agent skills and subagent prompts consume this envelope directly rather than parsing coordinates, object IDs or finding revisions out of free-form prose.

### Lost acknowledgement

| Observation | Action |
| --- | --- |
| Application already has the native acknowledgement | Return the existing request. Never call native Send again. |
| Exactly one native history/queue match for the recorded correlation | Bind it and reconcile observed status. |
| More than one match | `outcome_uncertain`; stop delivery and surface duplicate evidence. |
| No match and transport proves the request was never sent/admitted | Retry the original delivery identity. |
| No match but the call may have reached the runtime | `outcome_uncertain`; hold subsequent delivery. Absence from currently visible history is not proof of non-acceptance. |

Reconnection is not resubmission. Explicit Resume authorises reconciliation and continuation, not blind replay of the original prompt. If the native interface cannot establish an uncertain send's outcome, preserve that state and require a scoped owner recovery decision before risking another submission.

## 4. Steer

Steer has its own request ID/context and an explicit `target_work_id`. It bypasses the application FIFO and is sent with native `immediate` to that active work. Its immutable directive is appended to the work's history.

If the host already knows the target work is settled, return `no_active_work`; the user can submit a new Queue request. If the runtime accepts Steer during a completion race and defers it into a new native turn, record that actual delivery result under the same application request. Do not resend it. Drain accepted steering before delivering another application-held Queue request.

The original Queue snapshot and each Steer snapshot remain distinct. A steering correlation change must not erase the work ID or retag an earlier tool invocation.

## 5. Stop

Stop is a durable control request, not a model prompt. It names the active work ID, records `stop_requested`, closes delivery of application-held Queue requests and invokes native cancellation exactly once per control ID.

| Situation | Result |
| --- | --- |
| Native cancellation is acknowledged and the work is quiescent | Stop becomes `completed`; active work becomes `stopped`; pending Queue requests become/remain `held`. |
| Runtime reports no active work | Complete Stop as `already_idle`; pending Queue requests are still held. |
| Cancellation fails or acknowledgement is lost | Stop is `failed` or `outcome_uncertain`. Keep the delivery gate closed and report the actual state. |
| A source publication or tool result races with Stop | Record it against the original work and source. Stop is not rollback. Apply normal publication conflict rules. |

Stop invalidates unanswered user-input/permission decisions for that work. A racing late answer receives `stale_decision`. It does not cancel independently running jobs. A separate job-cancel operation records `cancel_requested`, calls only that job's supported cancellation method and waits for confirmation. Unsupported cancellation returns `cancel_unsupported` while the job remains visible.

Resume after Stop/restart defaults to retaining held Queue requests. Its command explicitly chooses `continue_work`, `release_held_queue` or `reconcile_only`. `continue_work` names the interrupted/stopped work and does not release unrelated held requests. Discarding a held request is an explicit cancellation of that request, not a source deletion. There is no automatic resumption after Stop.

## 6. State machines

Statuses are observations, not artistic approval. Terminal records retain their evidence. A later reconciliation adds a result/revision rather than inventing an earlier exit code.

| Record | Allowed transitions |
| --- | --- |
| Queue request | `accepted → held/dispatching → acknowledged → running → completed/failed/stopped`; uncertain dispatch may enter `outcome_uncertain`, then a proved existing state. `accepted/held → cancelled` only by explicit cancellation. |
| Steer request | `accepted → dispatching → acknowledged → applied/deferred/completed`; failure/uncertainty is explicit. `deferred` still awaits its native continuation result. |
| Stop request | `accepted → stop_requested → completed/failed/outcome_uncertain`. |
| Work | `pending → running ↔ awaiting_user`; then `completed/failed/stop_requested`; `stop_requested → stopped/failed/outcome_uncertain`. Interrupted work enters `awaiting_resume` on host restart. |
| Job | `planned → waiting_resource/starting → running → succeeded/failed/cancel_requested/outcome_uncertain`; `cancel_requested → cancelled/failed/outcome_uncertain`. Resume adds a new attempt/owner association to the same job identity when inputs are unchanged. |
| Media availability | `pending → partial → complete`. A failed job can still have `partial` availability. Failure is recorded on the job and unavailable intervals rather than erasing completed artefacts. |
| Permission/input decision | `pending → answered/declined/cancelled/expired`. A changed operation requires a new decision ID. |

Persist a `state_revision` on mutable records. An invalid transition is `invalid_state_transition`; it is not repaired by overwriting a status string. The adapter can receive duplicate/out-of-order native events and must reduce them idempotently using native identity and the established work mapping.

## 7. Native questions and permissions

Register native user-input and permission callbacks when creating or resuming a session. Present them as project-history cards with durable decision IDs, exact operation/question fingerprints and work/invocation references. Ordinary tool requests follow the automatic policy without a prompt.

When an answer is genuinely needed, that work is `awaiting_user`. Browser closure leaves the decision pending. Responding from another tab uses the decision's expected revision. Returning to the UI restores the same card. No native decision is answered twice after acknowledgement loss.

Creative selection, VLM-finding approval and tool permission are different resources with different targets. Approving one does not approve the others.

## 8. Backend/workstation restart

At startup, restore SQLite/project locations and acquire ownership without sending model messages, recreating native queues or starting new renderer/generation jobs. Read-only native history, worker receipts and provider status can refresh the display. Existing independent workers may still be progressing.

On explicit Resume:

1. Reconcile ownership and every ambiguous dispatch before permitting new work.
2. Restore the exact project/session association and original UI/request snapshots.
3. Register tools, permissions, skills and attribution mappings before native execution can resume.
4. Attach to a surviving native session if supported, or resume its persisted history. Do not manufacture a fresh conversation when history is missing.
5. Inspect known job IDs and published results. Reuse completed results, observe live jobs, and let Copilot decide the remaining authorised work.
6. Keep held Queue requests held unless the Resume command explicitly releases them.

F00/E01 must prove whether native session loading itself continues work. The selected adapter must keep the execution gate closed until this explicit Resume path. In-memory MCP turn metadata cannot be assumed restored merely because the transcript is present.

Missing native history is `native_session_missing`. Offer recovery limits and an explicit new-conversation action only after the user chooses it. Do not silently relabel a new session as the original.

## 9. Project switching

A project is settled when no root work, native child, unanswered decision, ambiguous dispatch or non-terminal tracked job remains. Runtime `idle` alone is insufficient.

While unsettled, offer wait, Stop Copilot, and supported individual job cancellation. Preserve held requests in the old project. Activate another project only after settlement is verified, or after a separately recorded owner recovery decision resolves an otherwise unknowable outcome. Never infer settlement from a disconnected browser or a recycled PID.

Inactive projects can be listed and their saved metadata restored through the UI. Their content is not supplied to the active agent. Loading them for authoring requires the active-project transition and a newly scoped tool context.
