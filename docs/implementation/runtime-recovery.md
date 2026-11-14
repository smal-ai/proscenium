# Bounded runtime health and coordination recovery

14 November 2026, SAST. Branch `impl/runtime-recovery`, base `0b8e9ba`.
Consumes parent recovery-Stop admission fix `ff8a32d` without changing contract edges.

## Code handover

- A five-second heartbeat timeout receives **at most one additional five-second
  probe**, only when new, durably attributed semantic native events progressed
  during the first probe and remain recent, the callback owner is current, and
  the exact recorded process plus connected SDK transport/pipe are still live.
  Missing/unattributed progress, closed transport, dead process and a second
  timeout still fail closed. No durable application attribution was removed.
- Private `copilot.runtime_errors` records retain boundary, original exception
  type/module and elapsed time. Exception text, raw prompts and authentication
  are not recorded. Unexpected Python exceptions retain uncertainty rather than
  being labelled an upstream provider outage. This does not recover the lost
  original M11 exception or establish why that host failed.
- A cold SDK resume with `continue_pending_work=False` may interrupt only the
  pre-existing internal coordinator invocations of its exact session after the
  previous recorded process is proven gone and native pending metadata is empty.
  `task` requires matching started/terminal child history and settled child records.
  `ask_user` requires the original immutable native decision binding to that exact
  tool/work/request and one still-unanswered decision. Its live input notification
  is not guaranteed to appear in persisted SDK history; present source event IDs
  are retained, absent terminal evidence is not fabricated.
  It cancels the wait with no response, human actor or delivery acknowledgement.
- Interruption is appended as system recovery evidence with source native event
  IDs and exact departed PID/start. It is not a fabricated native return or child
  success. The original request/work/session identities remain unchanged. Live
  sessions, new invocations, unknown tools and Bash are never settled this way.
- Native `cancelled: true` child/tool terminal records are now retained as cancelled,
  not successful completion. Existing native history and prior public events are
  preserved. No schema, DB migration, job, lifecycle, UI or tool ABI changed.

**132 Copilot integration checks passed**, including 15 deterministic health/
coordination cases and the existing delivery/fault/cancellation regressions.
Targeted Ruff and mypy pass. The heartbeat tests model delayed RPC versus ongoing
verified events without using real model calls or an expensive per-token observer.

Actual metadata inspection exposed the SDK's non-persisted input notification.
One additional regression covers that precise case. The corrected recovery plus
delivery/fault selection passed **43 tests**, with mypy clean. The first M03
metadata pass remained conservative and its evidence was preserved before correction.

Actual follow-up observations and cumulative M03 assessment will be committed
separately. M13 candidate `209448a9d10287b777b005cd8e03e888d5e15cc8` supplies
the successful same-work compiled-UI Continue dependency, subject to parent
acceptance. Genuine unknown Bash outcomes remain M12 limits, not product completion.
