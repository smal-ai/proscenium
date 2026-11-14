# Original M11 cold Stop confirmed from positive RPC inactivity

**Ready for separately authorised original-work Continue. No publication send ran.**
14 November 2026, SAST. Base `02a3a8d`, production code `1f0ca24`, preflight driver
`79acf89`. SDK 1.0.34 / locked CLI 1.0.112. M10 was not accessed.

## Actual final state

| Record | Result |
| --- | --- |
| Original work `work-4a7e644e78514109b4a5232316e3aacc` | **stopped, revision 10** |
| Original Stop `33048c8a-df79-4027-a6c2-10ba396e5044` | **completed, revision 2**, phase confirmed |
| Native rg wrapper | **cancelled/interrupted**, native search result and exit remain unknown |
| Live/unknown app invocations | **0** |
| Original cancelled read decision | Byte-identical: response/resolver null, delivery not_sent |
| Prepared host-reject permission stage | Byte-identical: reply reject, authority host, delivery outcome_uncertain |
| Reconcile-only operation | Completed, no model prompt |
| Unity publication/goal completion | **Not performed or claimed** |

Request `1a914aa8-2bbc-461a-a87d-1bb7431de02c`, native session
`0cbee1a2-b5ed-4ab8-962b-ccf1d9f8a454` and generation task are unchanged. The
original Stop ID/payload was reused. Its matching receipt lookup returned replayed
outcome_uncertain without redispatch; an explicit owner reconcile-only operation
then observed the new barrier. No new Stop, Queue or Continue was created.

## Real positive native facts

The recorded main-loop response was literally **`{"interrupted": false}`** from
`session.interruptMainTurn`, RPC request
**`786187b4-ec19-42f2-8617-695b7ee868a1`**, with `flushQueued: false`.
Typed response SHA-256:
`475fb20d3357e3e6ee3ba14fc517d3431f59db0f7a4f024d3a7690c719968e55`.
The pinned contract explicitly means the main loop **was not processing**.

This did not stand alone:

- All **five complete pending snapshots** were empty, with inFlightSteeringCount
  zero and no withdrawable running message. The queue was checked before pause,
  after pause, immediately before Abort, before interrupt and after interrupt.
- Exclusive `setDrainPaused(true)` successfully acknowledged. It was never
  released or retried during this proof. No queued message could be flushed.
- `session.tasks.list` returned **`{"tasks": []}`**, exactly once. No background
  agent, sidekick, promoted shell or client task was started/cancelled.
- `session.tools.listPendingRequests` returned **`{"items": []}`**, exactly once.
  No external tool call was answered, fabricated or executed.
- Typed `session.abort` returned **`{"success": true}`**, request
  `e6bf375d-8b44-4b91-af3a-1d3902eaba0f`. This is a control acknowledgement,
  **not** a native rg result or exit zero.
- Current ownership epoch, exact session object/callback generation, callback
  sequence, transport process, argument/stage/decision hashes, original Stop and
  work revision stayed exact through observation and atomic application.
- The previous runtime was proven gone. No other live/unknown app invocation,
  foreign active work, unsettled send/Steer or ambiguous attribution existed.

`rpc-observation.json` contains every real transport request ID, method, safe
parameters, start/finish time, typed-result hash and summary hash. It preserves
the previous failed Stop receipt/phase/outbox evidence before advancing the
original projection. No uncertain intent was reset to pending or dispatched again.
Its old public failure events and previous proof exports remain intact.

The confirmed phase uses **`completion_basis: cold_inactive_main_loop_rpc`** and
observation `82de02c32a95e8c29417ee78ebd507a44ae1ef8a1c801775b954389e886ce859`.
It has **no fabricated idle_native_event_id**. The system read-interruption record
references genuine historic tool/read-request IDs and this RPC observation, not
synthetic native events or ToolResults. No task-complete projection, empty send,
draining/waiting API, task start/cancel or user answer was used.

## Preservation, allocation and cleanup

- **One** additional owned cold metadata/control runtime, approximately **4.40 s**
  including closure, below 180 seconds. `continue_pending_work=False`.
- One raw history RPC was shared unchanged with its typed projection. **Zero SDK
  sends, new user messages, model turns, children, native authoring, jobs, GUI,
  Editor/Blender/export/render/inference/provider or SSH effects.** Callback and
  Python provider-HTTP entry points were blocked and never reached. This is not
  an OS network sandbox.
- `result.json.control_rpc_counts` records the actual typed control calls. The old
  session-wrapper counters remain zero because typed RPCs bypass those wrappers.
- Exact metadata SDK and parent PID/start identities are closed. No unrelated
  process was stopped, and no active background task was discovered or cancelled.
- Private before DB/registry/process/history copies were hashed and verified under
  `/home/quintin/.cache/proscenium-development/cold-stop-recovery-02a3a8d`.
- HEAD/checkpoint remains **`8d49caaee980d18a140111e81b15a46c71e229ce`**. Every
  receipt file hash matches current bytes; `checkpoint.json` retains the receipt.
  Source/scene/meta bytes, old reports, Git refs, original bindings, contexts,
  source/job/receipt/counter records, submissions and history prefix are unchanged.
- Task prompts/results and pending-call arguments are hashed, not dumped. No
  authentication or raw private prompt is committed. SDK observer instrumentation
  only records matching request IDs and passes original messages through unchanged.

## Verification and boundary

**93 scoped regression checks passed**, including hot Stop/deferred steering,
retired callback and accepted-service cases. The final **30-case cold-stop set**
also covers callback changes during commit. Production Ruff and mypy are clean.
Seven retained-evidence checks verify the actual positive observation, original
intent/history, unchanged decision/stage, preservation and cleanup without opening
an SDK runtime.

```bash
PYTHONPATH=. uv run --offline python tests/native/copilot/cold_stop_export.py
uv run --offline pytest tests/integration/copilot/test_cold_stop_evidence.py -q
```

The [implementation contract clarification](../../implementation/cold-stop-recovery.md)
adds no public states or edges. It does not broaden unknown Bash recovery or
weaken the existing hot Stop/native-stage matching rules. Parent acceptance and
one separately allocated original publication continuation are still required.
