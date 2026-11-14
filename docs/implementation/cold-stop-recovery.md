# Positive cold Stop RPC barrier

14 November 2026, SAST. Branch `impl/cold-stop-recovery`, base `02a3a8d`.
Only the existing uncertain M11 Stop/read wait is in the actual allocation. M10
is separately assigned and is not accessed by this proof.

## Pinned contract clarification, not a new state edge

SDK **1.0.34**, CLI **1.0.112**, existing accepted binary lock. The SDK's generated
`rpc.py` declares `InterruptMainTurnResult.interrupted: bool`, explicitly **false
when the main loop was not processing**. `session.rpc.interrupt_main_turn` is a
no-op in that case and leaves background agents/promoted shells alive. This is a
positive main-loop inactivity observation, unlike absence of an idle notification.

`TaskList.tasks` enumerates tracked agent/client/shell tasks; only completed,
failed and cancelled statuses are terminal. Idle means waiting for input and is
not accepted as quiescent. `PendingExternalToolRequestList.items` includes pending
external calls of the session and its agents. Neither API starts/cancels work.

`flushQueued=False` can clear a queue, so the protocol first requires a complete
empty pending snapshot, acquires the exclusive pause, verifies empty again, reads
tasks and pending external calls once each, verifies empty immediately before
Abort, obtains typed Abort success, verifies empty before interrupt, requires
literal interrupted false, and verifies complete empty pending again. Unknown,
null, malformed, true, queued/inflight/withdrawable messages or active/idle tasks
cannot pass. No `flushQueued=True`, draining, task starts/cancels, synthetic
task-complete projection, empty send, log event or fake idle ID is used.

## Scope and evidence

Only an original immutable Stop with outcome_uncertain receipt/phase qualifies.
Its persisted submit fingerprint and adjacent original decision-cancellation,
work-stop-request and admission events prove the exact cancelled read decision.
Cold non-continuing original session, departed process, validated native rg
arguments/path and exact stage/work/request/delivery binding are rechecked.
Generic Bash or another live/unknown invocation/foreign work remains fenced.

Current ownership epoch, session object, callback generation, callback sequence,
transport process and all candidate record hashes must stay exact throughout the
RPC observation and atomic application. Proof span is bounded to 30 seconds and
commit freshness to five seconds. Callback changes during commit roll back the
application. Non-idempotent pause is attempted once per epoch/Stop observation;
uncertain effects are never retried automatically or released without authority.

Each real RPC observation retains the actual pinned transport request ID, method,
safe parameters, start/finish times, typed-result hash and redacted-summary hash.
Task prompts/results and external-call arguments are hashed, not dumped. The
private transport send observer is pass-through and version-bound; it never edits
requests or results. The old failed Stop receipt/phase/outbox evidence is retained
in a separate observation and the existing public event history remains intact.

On a complete proof, the lost read wrapper is cancelled/interrupted with unknown
native return/exit, the original Stop receipt becomes completed and the original
work becomes stopped. The same old outbox intent is settled acknowledged from
dispatching/uncertainty, never reset to pending or dispatched again. Question
response/resolver remain null and delivery not_sent; the prepared host rejection
is not converted into an owner answer or invented delivery ACK. No Unity goal
completion/publication is implied. Existing wire types/edges and hot Stop remain.

## Preflight verification

**93 scoped regression checks passed**, including hot Stop/deferred steering,
retired callbacks and accepted service boundaries. The final **30-case focused
cold-stop set** additionally verifies callback activity at commit. Typed false/
true/null/malformed responses, active child/external call, queue/Steer, wrong
session/owner/generation, read versus Bash, stale proof and duplicate observation
are meaningful negative/positive cases. Production Ruff and mypy pass on four
owned modules. All tests use fake SDK transport, not models or native authoring.

The authorised one additional M11 cold metadata/control runtime follows this code
commit. Its actual RPC facts, preservation checks and readiness outcome must be
reported separately. No publication model send is allocated to this package.
