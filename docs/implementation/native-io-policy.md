# Native file-IO policy and bounded recovery

14 November 2026, SAST. Branch `impl/native-io-policy`, base `c897729`.
No shared schema, database migration, filesystem validator, job, UI, native profile
or authoring/provider change is included.

## Typed IO classification

The pinned SDK 1.0.34 definitions in `copilot/generated/session_events.py` declare:

- `PermissionRequestRead`: file/directory `path`, intention and optional resolved
  target, managed-approval/bypass/path-grant flags.
- `PermissionRequestWrite`: `fileName`, diff, intention, optional new file contents,
  before/after `fileEdits` targets and the same special approval flags.

Native file IO now validates those SDK shapes, original live invocation/session/
work/request/delivery/epoch attribution and every declared lexical/resolved target.
Read/write names, intentions and ordinary source contents are data, not shell
installation or upload commands. Explicit managed/bypass/grant requests still need
owner review. Unknown custom/MCP consequential operations retain their existing
policy; shell classification uses the actual command field, with existing secret
checks and install/push/upload/destructive confirmations retained.

The existing `project_path` validator is unchanged. The native seam preserves both
lexical and resolved paths instead of resolving away forbidden prefixes. An
experimental resolvedPath cannot override an unsafe original path, and all write
edit paths are checked. Secret/runtime roots, cross-project and symlink escapes
still reject. Managed reads retain only the existing managed-path policy. This is
not an OS sandbox, recursive-content filter or race-proof filesystem boundary.

Existence is separate from authority: absent in-scope ordinary native read targets
are permitted through to the native file-not-found/parent-not-found result. Root
existence and lexical/resolved-ancestor validation remain mandatory. Only this
native IO seam uses non-strict target existence; HTTP/media/source publication
helpers and the shared validator are not loosened.

## M10 completion producer

Inspected source `e18310c6872c166a6a7b3989730d5f3f23e85ca1` contains the exact missing
`revision-measurements.json` read and terminal trace. The previous reducer made
`copilot.completion.candidate=true` on any attributed root `assistant.turn_end`.
`Copilot.settle` then completed the request/work after idle, empty native pending,
settled tools/directives and no pending decisions. That can settle an empty tool
batch rather than a full-request terminal. The captured M10 pattern reproduced the
false completion deterministically before the fix.

New candidates require either an attributed nonempty public final message without
tool requests followed by turn-end, or explicit `session.task_complete`. Existing
idle, pending, tool/child/decision and latest-delivery checks remain. Private
completion records now retain the producer/source event identity, without private
reasoning or tool arguments. This is request terminal evidence, not proof of a
successful business goal or publication. Historical completed work is not silently
reopened or rewritten by this change.

## Exact departed read-wait Stop

The actual M11 history has its read permission request but no recorded permission
completion or tool return. A dead process or unchanged source cannot supply either.
Cold metadata can therefore only retain a **candidate** for the exact unanswered
bounded native rg wait: original tool/permission/decision/work bindings, argument
hash, validated matching file targets, departed process and empty complete pending
metadata. No invocation is settled at this probe stage.

Only an explicit original-work Stop may interrupt that candidate, after actual
pause/Abort acknowledgement, fresh native Abort and session-idle events, complete
empty pending metadata, no current send and the original decision cancellation.
Session-scoped Stop housekeeping is recorded without inventing a current-request
binding. Unknown/ambiguous tools, missing evidence, live owners or approved/changed
decisions still block the barrier. A cancelled read wait records its native result
and exit as unknown, not a successful/failed search result or fabricated ToolResult.
Original stage reply/delivery fields and strict completion matching remain intact.

## Offline handover

The actual M11 filename and actual M10 missing-file/empty-batch regressions failed
before their fixes. **131 affected integration tests passed** in the scoped run,
including new IO/path/binding/Stop/settlement cases and existing permission,
registered-handler, delivery/fault and retired-callback coverage. Ruff and mypy on
four owned production files pass. Metadata execution and final preservation/
readiness outcomes follow separately; no model send is authorised by this report.

## Actual bounded outcome

The [metadata handover](../evidence/native-io-policy/README.md) records both
before-snapshotted, one-cold-open, zero-send executions. M11 matched one exact
departed read wait, then the explicit Stop received pause/Abort acknowledgements
but no fresh Abort/idle event or native read completion. It correctly stayed
unconfirmed: work stop_requested revision 9, Stop outcome_uncertain, rg unresolved.
The decision is cancelled with null answer/resolver and not_sent delivery. No
native result or successful cancellation barrier was invented. Original publication
work is **not ready for Continue** on this evidence.

M11 HEAD/receipt `8d49caaee980d18a140111e81b15a46c71e229ce` and all checkpoint
bytes are preserved. M10's historical completed revision-6 record is unchanged;
the actual turn-end candidate/binding and absent full-task/final-response evidence
are retained without manually making it resumable. Both SDK runtimes and their
parents are closed. No extra allocation or native retry occurred.

The small offline-test portability correction is `c42f3ff`: checked-in captures
and a selected immutable M10 fixture replace unreachable fleet refs. Twenty-three
focused checks pass, without changing historical capture files.
