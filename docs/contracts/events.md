# Public event protocol 1.0

## 1. Durable envelope

Every public event validates against `schema.json#/$defs/Event`. Its application sequence is monotonically increasing within the project. The sequence, record mutation and any required job intent are committed together. Broadcast happens after commit.

`request_id` identifies the causing human request when known; `work_id` identifies the root objective. `actor` records user, lead, subagent, tool, worker or system provenance. Nullable native event IDs and correlations stay null when unavailable. Events which cannot be reliably attributed must not be assigned to the currently selected browser item or an unrelated request.

Native deduplication uses `(native_session_id, native_event_id)` when available. Application event sequences remain project-scoped across explicit native-session replacement. A native event ID is not assumed globally unique across sessions.

| Event family | Payload / reduction |
| --- | --- |
| `project.state_changed` | `ProjectRecord`; update activation/recovery state. |
| `work.state_changed` | `WorkRecord`; update root objective/held or interrupted execution state. |
| `request.accepted`, `request.state_changed` | `RequestReceipt`; update one immutable request identity. |
| `assistant.delta` | `ConversationEventData`; append public text to the named message. |
| `assistant.completed` | Same shape; authoritative complete public message text replaces accumulated deltas. |
| `tool.started`, `tool.completed` | `ActivityEventData`; a terminal payload can state failed/cancelled/uncertain. |
| `subagent.started`, `subagent.completed` | Same activity shape, including actual model ID where supplied. |
| `publication.created` | `PublicationReceipt`; invalidate/read affected catalogue records. |
| `job.state_changed` | `JobRecord`; independently update execution and media availability. |
| `frame.available`, `segment.available` | Exact `FrameReceipt` / `PreviewSegment`; extend the interval map. |
| `annotation.saved`, `selection.changed` | `CommandReceipt`; read the acknowledged revision when needed. |
| `decision.requested`, `decision.resolved` | `Decision`; the exact pending question/permission lifecycle. |
| `review.ready` | `ReviewTicket`; the owner UI fetches findings. It is not an agent instruction. |
| `usage.recorded` | `UsageEventData`; report only observed units/tokens/model identity. |

Raw private reasoning, system prompts, authentication data, full HTTP headers and provider secrets are outside the event allowlist. Large tool logs/images are registered artefacts with scoped visibility, not repeated event blobs. Review findings are owner-only records; a native review tool receives the ticket/status rather than an unapproved repair.

## 2. WebSocket handshake

Endpoint: `/api/v1/events`. Authenticate the owner cookie and Origin at upgrade. The first client message is a `Subscription` with project ID and `after_sequence`. A connection subscribes to one project; changing project sends a new subscription only after the UI performs its activation/read transition.

Server control frames are separate from durable events:

```json
{"type":"welcome","schema_version":"1.0","project_id":"project-demo","earliest_sequence":1,"last_sequence":42}
```

```json
{"type":"caught_up","schema_version":"1.0","project_id":"project-demo","through_sequence":42}
```

```json
{"type":"resync_required","schema_version":"1.0","project_id":"project-demo","reason":"cursor_expired"}
```

1. Read a replay watermark, send all persisted events after the requested sequence through that watermark, then send `caught_up`. In V1 the project event journal is append-only (`earliest_sequence = 1` for any non-empty project), so full conversation and activity replay from `after_sequence: 0` is always available.
2. Buffer/tail later committed events without a gap while replay is running.
3. Client ignores already-applied sequences and persists its last applied cursor. Receiving a gap triggers HTTP replay or a fresh snapshot, not invented intermediate state.
4. Heartbeats are control messages with no business sequence. They do not count as progress or keep a completed work unit running.
5. If a slow client exceeds the configured outbound buffer, close it with a reconnectable reason. The durable journal is the backlog; an unbounded in-memory queue is not required.

`SocketControl` defines these control-frame shapes for generated bindings alongside the durable envelope. One WebSocket supplies conversation, catalogue and render updates. Video/image bytes use ordinary authenticated same-origin HTTP.

## 3. Native event adapter

Pin the SDK/runtime and record the tested mapping in the E01 compatibility lock. Do not expose the vendor's complete event union as Proscenium's public API.

- Map native root messages and subagent-originated messages using actual native message/agent IDs.
- Capture work/tool attribution when an invocation is admitted. Later tool results use that stored mapping even after Steer, Stop or another request changes the active context.
- Native per-model turns are not user requests. `assistant.turn_end` cannot complete an application work unit by itself.
- Native idle/terminal/error observations must be reconciled with pending steering, child activity and independent jobs. `session.task_complete` is an agent assessment, not evidence that a render or Unity validation succeeded.
- Missing ephemeral native events after restart are not fabricated. Persisted complete message text can repair the public conversation projection without replaying inference or appending duplicate deltas.
- Native usage field units and availability vary by version. Preserve unknown values and the runtime's reported cost unit. Do not label a premium multiplier or nano-unit value as currency.

## 4. Required fault traces

The [acceptance scenario catalogue](../acceptance/scenarios.json) includes acknowledgement loss, native-event-before-ack, duplicate callbacks, late worker completion, sequence replay gaps, Stop/Steer races and a final message after lost streaming deltas. Offline traces are explicitly synthetic. A live E01 trace is sanitised and pinned before it becomes a compatibility fixture.
