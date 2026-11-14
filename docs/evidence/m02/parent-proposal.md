# Parent-owned M02 integration corrections

**Acceptance base:** `4e8b2337e09dc8416fdc833532f20db473c873a7`.
No proposed application change was applied in this worktree. Two real cloud roots
have been consumed. Do not send another prompt under this allocation.

## R1. Retired session events must not fence another project

### Observed failure

1. Browser-created project A executed one real SDK request to completion.
2. The browser created/activated fresh project B through the saved-project UI.
3. A's native history recorded `session.shutdown` at
   `2026-11-14T13:59:25.679Z`, following disconnect hook events.
4. Read-only inspection observed B's `copilot.control/gate` as
   `fenced: true`, `reason: attribution_unavailable`, before B created a native
   session. B's original request remained held. No replacement was submitted.
5. Explicit UI **Release held queue → Resume** executed that same request once.
   B acquired its own real native ID and returned only B's actual brief sentinel.

The before/recovered browser records and redacted native history are retained.
The diagnostic fixture subsequently passed an **actual retained** terminal SDK
event from inactive B to the production reducer while A was active. It returned
`scope_denied` before any write. See `fixture-receipts.json`,
`11-diagnose-old-event`. This is a reducer probe, not a second model execution.

### Exact code path and requested correction

- `apps/backend/src/proscenium/copilot/service.py`, `Copilot.tick`: switching
  disconnects `old_session` after the project has become inactive.
- The `session()` event callback calls `State.reduce(project_id, ...)` for those
  disconnect/housekeeping events. `State.reduce` opens an active-project Store.
- The inactive Store guard rejects the event. The callback catches it and sets
  the **global** `self.failure = "attribution_unavailable"`.
- `run()/tick()` then applies that global failure to the **currently active** B.

Parent/W01 should retire the exact old session callback before disconnect, handle
or ignore only proven terminal/housekeeping metadata for that settled retired
session, and keep unexpected execution events attributed to their original
project. Failure state must be project/session-scoped, not a global value later
consumed by a different project. Do not disable inactive-store guards generally
or turn an unexpected tool/model event into permission to mutate inactive work.

Required regression: completed A → activate fresh B → actual disconnect terminal
event for A → B remains unfenced and its single admitted first request is eligible
for delivery. No duplicate A/B send, no A event written to B, and no new work on
restoration. Captured terminal events permit an offline regression without another
cloud request. A new live comparison needs a separately authorised allocation.

## R2. Closed-gate owner cancellation is starved by lifecycle gating

### Observed failure

- A real four-item W02 publication created one planned preview, with the explicit
  M02 fixture closing admission under the preview-dispatch lock before release.
  No renderer was authorised or launched.
- Authenticated owner HTTP cancellation returned **202**. The production record
  remained `cancel_requested`, with its `job.cancel` outbox row pending.
- The real production cancellation adapter settled it immediately when the
  trusted fixture explicitly dispatched that exact pending intent through
  `db.outbox.dispatch_one` and `previews.runner.cancel`.
- The adapter returned `cancelled` with verified pre-launch evidence. There is no
  render launch claim, frame receipt or fabricated exit code.

See `cancel.json`, `browser-post-cancel.json` and fixture receipt
`08-explicit-cancel`. The explicit dispatch is a disclosed workaround, **not**
evidence that the production lifecycle processed the cancellation unaided.

### Requested correction

`apps/backend/src/proscenium/api/lifecycle.py` registers `preview-dispatch` with
`gated=True`. `watch()` therefore skips the whole preview pump while execution is
closed. That also skips cancellation, although the existing outbox dispatcher and
preview cancellation adapter support it without opening admission.

Provide a cancellation-only dispatch path while the gate is closed, using the
existing exact-attempt intent and registered adapter. Keep render/provider launch
checks gated. No new DTO, job state or bypass of ownership is required.

Required regression: actual app lifespan, closed gate, planned preview, owner HTTP
cancel → terminal `cancelled` without calling a harness dispatch method, opening
the gate, or starting Blender. Also test a surviving admitted worker's independent
cancellation and preserve unknown outcomes rather than inventing terminal state.

## Catalogue metadata presentation limit

At this base, catalogue UI exposes version history, source commit, current/pinned
and selection state, and registered artefact counts. It does **not** expose the
complete provenance/source closure. The proof inspected those real records via
the authenticated `listItemVersions` JSON in an actual browser tab, labelled as
API inspection. It does not claim that this is a finished catalogue metadata UI.
The parent's already assigned inspection/catalogue package owns that presentation.
