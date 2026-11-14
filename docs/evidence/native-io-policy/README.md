# Native IO policy: bounded metadata outcome

**M11 original publication work is NOT ready for Continue.** Policy correction:
`368f2ab`. Portable offline fixtures: `c42f3ff`. Base `c897729`, 14 November 2026,
SAST. No model send, Continue, new Queue, GUI, authoring, job or provider operation ran.

See [the implementation handover](../../implementation/native-io-policy.md) for
pinned SDK read/write shapes, canonical path rules and prospective settlement fix.
Shared schemas, filesystem validation and HTTP/media/source existence checks are
unchanged. This is native permission policy, not an OS sandbox.

## Corrected policy

- Read `path`, write `fileName`, resolved targets and all before/after edit paths
  use existing project/secret/runtime guards. Lexical paths are preserved, so
  symlink or parent resolution cannot hide exclusions.
- Ordinary filenames, intentions and source contents are not shell installation
  commands. Managed/bypass/grant flags still require review. Unknown custom/MCP
  consequential operations and real shell install/push/upload/sudo/destructive
  actions retain their policy. Shell action classification uses its command field.
- Missing ordinary in-scope targets reach native file-not-found semantics, not an
  invented owner refusal. Root existence and lexical/resolved ancestors remain
  checked. Only this native seam relaxes target existence, not shared validators.

## M11: explicit Stop attempted, no fabricated barrier

Original session `0cbee1a2-b5ed-4ab8-962b-ccf1d9f8a454`, request
`1a914aa8-2bbc-461a-a87d-1bb7431de02c`, work
`work-4a7e644e78514109b4a5232316e3aacc` were retained.

Before: awaiting_user revision **8**, one running native `rg` wrapper
`call_XcQ3lvQAC0HOmPxz3LBd6jzc`, permission
`f4cb7f12-085b-42b8-9288-19b946111107`. Native history has tool start
`8fa39d4d-0399-474e-b8a4-1d23ddd7e3c4` and read permission request
`87b37652-ab81-42e5-a93c-b70166e14562`, but **no permission completion or tool return**.
The original SDK PID/start and parent were already gone.

One cold metadata snapshot with execution held matched the immutable original
stage/decision/tool/work/delivery bindings, argument hash and validated file target.
Native pending metadata was empty. The probe retained one exact read-wait candidate
but **did not settle it**.

The explicitly authorised scripted-owner Stop cancelled the unanswered decision
through normal control admission. Actual native queue pause and Abort RPCs both
acknowledged. However, the cold runtime emitted **neither a fresh native Abort
event nor session-idle**, and supplied no read result. No final verified idle/tool
barrier was committed. The guard correctly refused to clear the invocation.

| Record | Actual final state |
| --- | --- |
| Work | **stop_requested, revision 9** |
| Stop request/phase | **outcome_uncertain**, pause and Abort acknowledged |
| rg invocation | **running / unresolved**, native result and exit unknown |
| Decision | **cancelled**, response null, resolved_by null, delivery not_sent |
| Separate SDK stage | Original prepared host reject and outcome_uncertain delivery, unchanged |
| Original Continue readiness | **false** |

The existing Stop path reports provider_unavailable for its local confirmation
timeout. This is not evidence of an upstream outage. A cold-runtime Abort ACK,
dead PID and unchanged source do not supply a read completion. No second open,
Stop retry or private-state repair was used. Missing observations are explicit in
`m11-stop.json`; uncertain Stop/read records still prevent original continuation.

HEAD and checkpoint `m11-unity-source-checkpoint` remain
**`8d49caaee980d18a140111e81b15a46c71e229ce`**. The unchanged durable receipt is
in `m11-checkpoint.json`; every receipt file hash matches current bytes. All current
source/scene/meta files, original bindings, contexts, jobs/attempts, Git refs and
old reports, including previous failed allocations, passed preservation checks.
No Unity publication was created.

## M10: exact completion producer, historical row unchanged

Inspected source `e18310c6872c166a6a7b3989730d5f3f23e85ca1` and the authorised cold
read of session `5971843f-f216-40f8-b8cb-995675f48161` confirm:

1. Continue `dae26d99-3ef2-49ff-a5d0-78b2ee82f739` was acknowledged as native
   message `0616732c-cddd-44b1-9875-4e59ff93a202`.
2. The assistant message had **empty public content and two tool requests**. View
   attempted absent `assets/e04/revision-measurements.json`.
3. Automatic policy rejected read `67539b84-6fdc-4a6e-89e2-4c802647f66b`. Native
   labels this user rejection, but authority was automatic_policy, with no owner decision.
4. View failed with native rejected; the companion Bash wrapper returned a terminal
   observation. Its wrapper success flag is not an asserted shell exit zero or authoring success.
5. Root turn-end **`3c0c7062-ce70-4e7e-be44-01f7260fbd0e`** set the old
   `copilot.completion.candidate=true`. Its stored binding joins to the original
   request/work and Continue delivery. Durable idle plus settled-tool/no-decision
   checks then let **`Copilot.settle`** write completed.
6. There is **no nonempty public final response or archived session.task_complete**
   in the retained continuation trace. The harness saw application completion,
   failed its no-v2 guard and closed the host. Routine shutdown is not independent
   natural-task or business-goal completion proof.

The actual empty-batch sequence reproduced premature settlement before the fix.
Future candidates require a public final message without tool requests plus
turn-end, or explicit session.task_complete, along with existing idle/pending/
invocation/decision/latest-delivery guards. Both valid terminal forms are tested.
This does not certify publication or business goals from response text.

Historical M10 work **remains completed revision 6**, byte-identical across this
pass. Its receipt, automatic rejection and source/history were not rewritten as
successful authoring or manually made resumable. Existing Continue admission does
not accept completed work. Parent owns the next explicit bounded action/protocol.

## Preservation, allocation and cleanup

- Per project: **one cold SDK runtime and one raw history RPC**, shared unchanged
  with typed projection. `continue_pending_work=False`. M11 used one pause/Abort;
  M10 used neither. **Zero SDK sends and zero unexpected model turns/children.**
- Tool, permission, prompt and Python provider-HTTP entry points were blocked.
  No callback or provider-HTTP attempt occurred. This is not an OS network sandbox.
- SDK lifetimes: approximately **7.73 s M11 / 2.77 s M10**, each below 180 seconds
  including closure. Exact SDK and parent PID/start identities are recorded and
  closed. No server, browser, helper or authoring process started.
- Private before DB/registry/process/history copies were hashed and verified under
  `/home/quintin/.cache/proscenium-development/native-io-policy-c897729`.
  Native history prefixes/user-message counts, original submissions, checkpoint/
  job/counter records, source bytes and nested old reports were preserved.
- An initial M11 harness import failure occurred **before Services/native open**.
  Its before-copy and failure record were kept. The corrected metadata run used
  a marked nested directory and the same project, not another runtime allocation.
- No auth or raw private model prompts are exported. Event extracts retain source
  IDs/types and selected protocol fields, not private reasoning.

**131 affected integration checks passed** once after implementation, with Ruff
and production mypy clean. The portability correction passed **23 focused offline
checks**. Ordinary integration tests now read checked-in redacted captures/local
fixtures, not unreachable fleet Git refs. Historical capture bytes are unchanged.
Seven retained-evidence checks verify the truthful blocked M11 outcome, M10
producer/unchanged historical state, checkpoint hashes, preservation and cleanup.

```bash
PYTHONPATH=. uv run --offline python tests/native/copilot/native_io_export.py
uv run --offline pytest tests/integration/copilot/test_native_io_evidence.py -q
```

No further cold open, Stop retry, Continue or model send is authorised here.
