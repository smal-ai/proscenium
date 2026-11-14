# E01: released official Copilot SDK/runtime proof

**14 November 2026, SAST. Runtime compatibility established within the bounded
cases below. F00 integration acceptance and full product acceptance remain open.**

Baseline: `2f00ebece9c5274ebda553df4c1558d5572669e0`, branch `impl/e01`.
No parent checkout, shared manifest, application package or reference project was edited.

## Exact identities

| Component | Observed identity |
| --- | --- |
| Python | CPython `3.12.13`, SDK requires Python `>=3.11` |
| Official Python distribution | `github-copilot-sdk==1.0.34` from PyPI |
| Official CLI | `@github/copilot@1.0.112`, Linux x64 glibc platform package |
| SDK release source | tag `v1.0.34`, commit `adf1da2c6db766f96ae6be36f3224e61ede010c4` |
| Protocol | `3` in the released SDK source |
| Lead and two executed native children | `gpt-6.1-sol`, worker `model_policy="required"` |
| Authentication | Official runtime reported `authType="gh-cli"`, `isAuthenticated=true` |
| Interpreter | `/tmp/opencode/proscenium-e01-runtime/venv/bin/python` |
| Direct native binary | `/tmp/opencode/proscenium-e01-runtime/cli/node_modules/@github/copilot-linux-x64/copilot` |
| Initial cloud-run launcher | `/tmp/opencode/proscenium-e01-runtime/cli/node_modules/.bin/copilot` |

The system `/usr/bin/copilot` is not used. Explicit native paths and
`--no-auto-update` prevent PATH stubs, cached updates and the SDK's automatic
runtime download from silently selecting another runtime. The npm launcher starts
a native child. **Use the direct native binary for the product's process fence.**
Direct-native queue checks and both graceful and forced PID reaping were verified
after the cloud proof against that same installed platform binary.

The SDK used its own supported existing login resolution. No token was extracted
from OpenCode, GitHub CLI, MCP, files or environment variables. No `github_token`,
provider override or login flow was supplied. SDK environment tokens and shell
login-environment import were disabled. An isolated `base_directory` still
authenticated through the official runtime. Account identifiers are omitted.

Exact hashes, npm integrity values and settings are in
[`../../implementation/runtime-lock.json`](../../implementation/runtime-lock.json).
The proof dependency list is separate from application dependencies.

## Findings and evidence

| Capability | Actual observation | Evidence |
| --- | --- | --- |
| SDK starts its owned runtime | SDK stdio handshake and auth/model catalogue succeeded without a separately started server. | `discovery.json`, `native-controls.json` |
| Working directory | Root prompt hook and child prompt hook reported the same isolated project root. Native `pwd` returned the callbacks project path with exit code 0. | `advanced.json`, `shell-and-model-result.json` |
| Both memory controls on create/resume | Live sessions used `memory={"enabled": False}` and `enable_session_store=False`. Wire tests verify both separate fields on create and resume. Distinct A/B histories did not reveal the other sentinel on cold resumes. | `followup.json`, `test_probe.py` |
| Original user text | `prompt` and `display_prompt` were passed unchanged, with source unset. Persisted root `user.message.content` equals the submitted text and includes `clientTurnId`. | `live-events.json`, `advanced.json`, wire test |
| Root structured context and image | Lead called the recording tool with the exact extension-context sentinel and image colour `red`. The only supplied image was a generated 16 × 16 PNG. | `live-events.json` |
| Child extension-context inheritance | **Not inherited.** Child explicitly reported `unknown` and no visible attachment/context. | `live-events.json` |
| Supported context fallback | `on_user_prompt_submitted` returned only `additionalContext`. Root and child recording callbacks both returned the exact host sentinel, absent from their unchanged prompt strings. | `advanced.json` |
| Native child tool/model scope | Executed children had explicit recording-tool-only lists, no file reads, and `modelSelectionSource="configured_required"`. An unavailable required model failed with `invalid_model` before child start. | `advanced.json`, `shell-and-model-result.json` |
| Callback attribution | Tool callbacks exposed root session ID, tool-call ID, request ID and child agent ID. Permission invocation alone contained only root session identity, so its request tool-call identity is needed. A real user-input callback exposed `requestId`. | `advanced.json`, `followup.json` |
| SQLite feasibility | Actual root/child event identities and callbacks joined to the original request in a disposable SQLite replay. Replay was idempotent and conflicting event bodies rejected. **Not live application persistence.** | `sqlite-replay.json`, replay tests |
| Ordinary shell, sandbox off | Built-in `bash` executed `pwd`, exit 0. Native telemetry recorded `sandboxApplied="false"`. No shell exclusion or OS wrapper was added. | `followup.json`, `shell-and-model-result.json` |
| Native queue pause/removal | Paused enqueue remained pending and conditional withdrawal returned `removed=true`. | `native-controls.json`, direct-native repeat in `lifecycle.json` |
| Deferred-Steer Stop barrier | Immediate directive moved from steering lane to paused native queue after root completion. Native idle occurred. Abort acknowledged, pending queue became empty, and no deferred model/tool events followed the barrier. Cold non-continuing resume stayed empty. | `advanced.json`, details below |
| No execution on tested loads | Empty, settled and stopped/emptied sessions loaded with `continue_pending_work=False` without new user/model/tool execution during the observation windows. | `native-controls.json`, `advanced.json` |
| Owned process fencing | Direct native PID exited/reaped on `stop()` with code 0 and on `force_stop()` with signal 9. Original `/proc` identities disappeared. No unrelated processes were touched. | `lifecycle.json` |
| Crash before first handled message | A fresh session with one paused/unconsumed first message was missing after forced exit. Cold resume failed without a model attempt. This is a real recovery limit, not a successful load. | `lifecycle.json` |

The sentinel tests are bounded observations, not proof that arbitrary hostile shell
commands cannot cross project roots. V1 deliberately has no OS project sandbox.
Application route containment and caller capabilities remain F00 implementation work.

## Exact supported signatures W01 needs

The full installed signatures are captured in `discovery.json` under `surface`.
These are real SDK `1.0.34` names, not an older dictionary-based constructor API.

```python
client = CopilotClient(
    connection=RuntimeConnection.for_stdio(path=absolute_native_binary),
    base_directory=private_application_runtime_directory,
    use_logged_in_user=True,
)

settings = {
    "working_directory": canonical_active_project_root,
    "memory": {"enabled": False},
    "enable_session_store": False,
    "sandbox_config": SandboxConfig(enabled=False),
    "infinite_sessions": {"enabled": True},
    "autopilot": {"continuation_enabled": True, "max_continues": 5},
    "large_output": {
        "enabled": True,
        "output_directory": project_tool_output_directory,
    },
    "skill_directories": adapted_skill_directories,
    "on_event": persist_native_event,
    "on_permission_request": permission_callback,
    "on_user_input_request": input_callback,
    "hooks": {"on_user_prompt_submitted": context_hook},
}
session = await client.create_session(model="gpt-6.1-sol", **settings)
session = await client.resume_session(
    existing_session_id, **settings, continue_pending_work=False
)
message_id = await session.send(
    human_text,
    display_prompt=human_text,
    mode="enqueue",  # "immediate" for an admitted Steer
    client_turn_id=application_request_id,
    attachments=ordered_images_then_extension_context,
)
```

The example's application functions are deliberately undefined. They are W01/F00
integration points, not a second application implementation. Adapted skills,
large-output spilling and repeated Autopilot continuations were **not** exercised.
Both memory controls and the sandbox option must be supplied again on resume.
Register `on_event` in create/resume, not after the session handle returns.

A create/send acknowledgement is not proof of durable history. The no-cloud
pending-load experiment paused the first message before it was handled, killed
only the owned direct runtime, then tried a non-continuing cold resume with a
blocking prompt-hook guard. Resume returned `Session not found`; the guard saw no
prompt and no model request ran. W01 must retain `native_session_missing` and the
original uncertain admission. It must not manufacture a new conversation or
resend that first message. This does not test recovery of an interrupted model
turn with an already durable transcript.

### Context and callback identity

- Keep `extension_context` for the root and canonical context/history capture.
  Its fields are `type`, `extensionId`, `title`, `capturedAt`, optional `payload`.
- Provide trusted child context through `on_user_prompt_submitted` returning
  `{"additionalContext": canonical_context_json}`. Do not use `modifiedPrompt`.
- **The hook input has no `clientTurnId` or native message ID.** Do not select a
  snapshot by prompt text or a global current request. The lead already receives
  its per-message extension attachment. For child hooks, bind native
  `subagent.started.agentId` and its `toolCallId` to the parent's persisted
  invocation/context, then resolve hook `agentId`/`parentAgentId` through that map.
  In this capture, `subagent.started` preceded the child prompt hook. If the mapping
  is not established, fail closed rather than inject another work's context.
- Root message events carry `clientTurnId` and `messageId`. Assistant events carry
  `originatingMessageId`. Tool events carry `toolCallId`, `turnId`, and child events
  additionally carry top-level `agentId` and `parentToolCallId`.
- Product deduplication keys include native session ID as well as native event ID.
  The disposable SQLite replay is deliberately restricted to one captured root
  session and its children. Its small tables are not the production schema.
- `ToolInvocation` exposes `session_id`, `tool_call_id`, optional experimental
  `agent_id` and `request_id`. Native request IDs, application requests and native
  tool-call IDs are different identities.
- Permission callback invocation does **not** provide a universal request/work
  identity. Its permission request carries `toolCallId`. Join it durably to the
  invocation, not to whichever conversation card happens to be active.
- Legacy user-input callback exposes a request dictionary containing `requestId`
  plus an invocation with `session_id`. It does not expose a tool-call ID itself.
  The released `UserInputRequestedData` event schema has `request_id` and optional
  `tool_call_id`. W01 must prove/persist that live join, including child questions,
  before claiming complete decision attribution. It was not captured in this
  bounded proof, and the callback cannot simply be reconstructed from transcript.
- Preserve the admitted work/generation-task identity independently of later Steer
  correlations. MCP per-turn metadata persistence was not tested or assumed.

## Deferred steering: the actual Stop facility

The released typed RPC group is **experimental**, but it is present and exercised:

```python
await session.rpc.queue.set_drain_paused(
    QueueSetDrainPausedRequest(paused=True), timeout=10
)
pending = await session.rpc.queue.pending_items(timeout=10)
await session.abort()
withdrawal = await session.rpc.queue.withdraw_message(
    QueueWithdrawMessageRequest(
        expected_prompt=original_unchanged_prompt,
        message_id=native_message_id,  # NOT the queue item's id
    ),
    timeout=10,
)
```

Source-defined semantics:

1. Pause affects **queued-lane draining**, not an already running turn, children
   or the immediate-steering lane. Fence new Queue and Steer at the application
   first. Keep accepted/in-flight sends and their immutable identities accounted for.
2. Pause acquisition is exclusive and **not idempotent**. Repeating `paused=True`
   raises `queue_already_paused`. A lost pause acknowledgement is uncertainty, not
   permission to retry or release another owner's pause.
3. Pause is not automatically released on caller exit. `paused=False` is unowned
   release, so never use it as a generic cleanup operation.
4. `pending_items()` distinguishes queue-item `id` from native `messageId` and
   exposes steering text, in-flight steering count and optional withdrawable turn ID.
5. `withdraw_message` compares the original prompt and native message ID atomically.
   Its `removed` and `interrupted` results must both be interpreted. A false result
   is not proof of removal. `remove_at` addresses queue-item IDs instead.
6. `session.disconnect()` releases session resources and preserves history. It is
   not a separately verified active-turn/child execution fence.
7. `client.stop()` requests shutdown, then terminates/reaps an owned process if
   necessary. `client.force_stop()` kills and reaps its owned process. This only
   gives the intended PID boundary when the SDK spawned the **direct binary**.
   Independent shell/render/provider processes may outlive it and need their own
   attribution and admission rules. Never kill unrelated native/provider sessions.

### Real completion-race trace

The supported agent-stop hook held a root after its model reply. The harness
acquired native queued-lane pause, sent one real `immediate` directive, then
released the completion hook. No synthetic runtime events were used.

- The accepted directive initially appeared in `steeringMessages`.
- It moved to queue item `id="2"`, native message
  `8a270af4-d6ea-4b85-8529-fcb4df6a6e32` after root completion.
- Native idle was observed **while that message remained pending**. Idle is not
  evidence of an empty queue.
- With the native queue still paused, `abort()` acknowledged and the queue became
  empty. The later conditional withdrawal returned `removed=false,
  interrupted=false` because the message was already absent. We do not label
  that as successful withdrawal.
- The barrier was recorded at `2026-11-14T10:45:11.879946+00:00`, after cancellation
  acknowledgement and an empty native pending-state observation.
- No deferred user/model/tool events occurred after that barrier in the remaining
  observation window. The pause was never released.
- A cold runtime loaded the stopped history with `continue_pending_work=False`.
  It emitted resume but no turn and reported an empty queue.
- The application binding of the known SDK acknowledgement was deliberately
  delayed until after Stop. This proves late **application receipt handling**, not
  a genuinely lost transport response or server-side deduplication.

The verified combination is **application admission fence + acknowledged native
queue pause + abort + owned-message reconciliation + quiescence check**, not
`abort()` alone. The empty queue after abort is an observation of this exact pair,
not a promise to depend on across upgrades. Conditional withdrawal separately
removed a pending paused message successfully in the local control proof.

W01 must persist the Stop phase and pause ownership, settle every admitted send and
native child, and keep held requests held. If cancellation, owned-message outcome,
child quiescence or pause ownership is uncertain, retain `outcome_uncertain` and
keep the admission gate closed. Direct-owned runtime termination can prevent
further native execution, but does not invent a cancelled outcome for an unknown
send. Explicit Resume must reconcile, not replay the original Queue text.

## Evidence provenance and limits

- **Eight deliberately reserved root sends**, two executed native children and
  one unavailable child rejected before start. The held Steer consumed a send
  allocation even though it never executed. No retries of uncertain sends.
- All root waits were bounded below 120 seconds. No Blender/Unity/GPU, external
  publishing, persistent service or independent model-service operation occurred.
- Raw native histories/logs are under
  `/tmp/opencode/proscenium-e01-runtime/proof`, not in Git. Committed captures omit
  account data, provider API IDs and encrypted reasoning. Timestamps retain UTC
  wire format. SAST is UTC+2.
- Initial live execution completed two root requests, then output JSON encoding
  failed on a permission callback's nested typed object. The native requests were
  **not repeated**. `live-events.json` was recovered by allowlisting its private
  native event archive. Subsequent capture code records only callback correlation
  fields. `advanced.json` and `followup.json` retain the initial harness's stale
  top-level `live_capabilities="not_run"` metadata. Their nested results and real
  events are authoritative. The harness metadata bug is fixed for future runs.
- The first offline wire test constructed a default SDK client, which eagerly
  downloaded its own runtime bundle into the default SDK cache before the fake RPC
  was attached. It did **not** start that runtime. The newly created bundle was
  moved into the allocated temporary runtime, and the test now passes an explicit
  non-launched executable path. Product code must always pass the reviewed path.
- SDK release-source `client.py` exactly matched the installed wheel's bytes.
  The reviewed runtime/client/session files and queueing/persistence docs had no
  diff between the release commit and the pinned research commit. Research pins
  were not changed.
- **Still unproven:** live-child Stop/recovery, genuinely lost transport ACK,
  interrupted-tool resume behaviour, child user-input attribution, Autopilot
  continuation stress, large-output spill behaviour, adapted skill discovery,
  production SQLite-before-reply persistence, application path validators and
  caller-channel separation. These must not be marked product acceptance complete.

## Official sources

- [Released PyPI metadata](https://pypi.org/pypi/github-copilot-sdk/1.0.34/json)
- [Released npm metadata](https://registry.npmjs.org/@github/copilot/1.0.112)
- [SDK release commit](https://github.com/github/copilot-sdk/tree/adf1da2c6db766f96ae6be36f3224e61ede010c4)
- [Client lifecycle, create and resume](https://github.com/github/copilot-sdk/blob/adf1da2c6db766f96ae6be36f3224e61ede010c4/python/copilot/client.py)
- [Session send, attachment, hook and profile definitions](https://github.com/github/copilot-sdk/blob/adf1da2c6db766f96ae6be36f3224e61ede010c4/python/copilot/session.py)
- [Experimental typed queue API and parameter semantics](https://github.com/github/copilot-sdk/blob/adf1da2c6db766f96ae6be36f3224e61ede010c4/python/copilot/generated/rpc.py#L82218)
- [Steering completion fallback](https://github.com/github/copilot-sdk/blob/adf1da2c6db766f96ae6be36f3224e61ede010c4/docs/features/steering-and-queueing.md#how-steering-works-internally)
- [Native child scope and skill inheritance](https://github.com/github/copilot-sdk/blob/adf1da2c6db766f96ae6be36f3224e61ede010c4/docs/features/custom-agents.md)

CLI authentication and sandbox statements were also checked using the installed
official `copilot login --help`, `copilot help environment` and
`copilot help sandbox`, without reading its credential files.
