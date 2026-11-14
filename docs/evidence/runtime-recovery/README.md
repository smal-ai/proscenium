# Runtime recovery and cumulative M03 evidence

14 November 2026, SAST. Base `0b8e9ba`, code commits `b6bffda`, `f11bf26` and
`35f1a9f` (duplicate-event progress fencing).
No contract edges, shared schema/database, jobs, lifecycle, UI or tool ABI changed.

## Successful Continue dependency: inspected M13 candidate

The exact candidate is **`209448a9d10287b777b005cd8e03e888d5e15cc8`**.
Its `docs/implementation/m13.md` records the actual compiled Full-HD owner UI
**Continue original work → Resume** after the original host timeout. The source
result and unchanged W01 tree were inspected directly from that Git object.

`m13-continuation.json` retains the source-file SHA-256 and exact cross-record join:

- Original request `510aa045-3bf0-443a-9889-efe267ca0cfb`, original work
  `work-5bd8623b033b4df0b6825911b798eaf2`.
- Original session `bd724185-83d9-43fe-a290-05ff06addbdb`.
- Continue operation `665b040a-168d-4d02-9525-dc04867620c8`, native continuation
  message `626a6506-87aa-41fa-bed7-021a29e3a527`, acknowledged.
- The completed `assemble_sequence` invocation has that Continue operation as its
  delivery, the same original request/work/session, and real publication
  `m13-publish-v1` / `m13-film-v1`. The original request ultimately completed.
- No prompt replay, reset, replacement conversation or W01 product change in the
  candidate. Source checkpoint `d81a9f8e6f94dfe9d611f438c5f836b1209e2aee` and
  subsequent real publication are retained by M13. This package did not execute
  M13, adopt all its dependencies or declare cumulative M12 acceptance.

Parent acceptance of that candidate is authoritative. It supplies positive
same-work Continue for M03 alongside the inherited actual M03 Stop/held-queue,
source/context, owner-card, Steer, economical worker and usage evidence. M02 is
accepted separately as `f752676`, tag `milestone/m02`.

## Already-started clean probe: retained failure, not another positive claim

Before the steering message supplied M13, one fresh project/root and one continuous
1920×1080 page had already run the authorised simple helper. **One SDK send** was
used. The 25-second helper started and the owner pressed Stop while it was active.
Native pause and Abort acknowledged, `abort(reason=user_initiated)`, turn-end and
live idle were observed. Its heartbeat stopped immediately and its exact PID/start
was later absent. It did not reach natural completion.

However, neither the live observations nor the retained native history contain
`tool.execution_complete` for `call_uWJVx2uNIYluc3IYjEST1Nz8`. The host correctly
kept that Bash invocation running/unresolved and Stop outcome_uncertain, instead
of calling an unproven barrier confirmed. No Continue, second SDK send, child,
replacement Queue/project or alleged tool cancellation/success was fabricated.
The probe closed before the new M13 steering was acted on. Its failure, native
abort and missing-terminal evidence, two real Full-HD screenshots and cleanup are
in `normal-probe.json`. This result does not replace M13's positive capability proof.

## Historical SDK metadata-only reconciliation

Private before-snapshots include each exact project DB, registry DB, native process
record and session-state files, with verified copy hashes. The original top-level
exports/logs/screenshots remain byte-identical. Snapshots and raw native data are
outside Git under
`/home/quintin/.cache/proscenium-development/runtime-recovery-history-0b8e9ba`.
The first conservative M03-A result is retained alongside its narrowly corrected
binding-recovery pass. **No historical session received a model send.**

| Record | Actual metadata result | Boundary |
| --- | --- | --- |
| M03 lost `ask_user`, work `work-6c566aaa39084a139da0b42fcff0b555` | Exact previous process gone, native pending empty. Original immutable decision binding matched the tool/work/request. Lost coordination cancelled, pending decision cancelled with response/resolved_by still null and delivery not_sent. Live invocations 1 → 0. | No owner answer, SDK reply acknowledgement, work completion or native successful tool return invented. The input notification was ephemeral and absent from persisted SDK history. |
| M03 interrupted Bash, work `work-ea4ab25e133747dba8e1f74dad160d2a` | Original session/history and known message retained; native pending empty, but live Bash remains 1 → 1. | No terminal event for `call_ZnLmPpinhkSxNCWJS2lDE5RJ`. Process exit is not Bash side-effect/return proof. Work stays awaiting_resume. |
| M11 parent task, work `work-4a7e644e78514109b4a5232316e3aacc` | Exact previous process gone, native pending empty. Child start `e65e59a0-d0e6-407b-876e-53f614e6d9df` and terminal `39a8c906-fe3e-460a-a3ff-f8c551a9ce48` match `call_m00hecCK14WE4HWf95RE5Y30` and child `a7e23121-6fa1-4221-84d7-483c72693158`. Internal parent coordination cancelled, 1 → 0. | Child terminal carries cancelled:true; checkpoint failed. No parent successful return, new checkpoint, Unity publication, job or source mutation. Original work stays awaiting_resume for a separately authorised continuation. |

`historical-reconciliation.json` contains exact identities, before/after counts,
system recovery evidence and follow-up-only public events. All source/catalogue/
job/attempt records remain unchanged. No old work was moved to terminal by fiat,
and the recovery path never treats an arbitrary Bash/tool as internal coordination.

## Health diagnosis and limits

The original M11 host lost the exception type at `Copilot.run`; its retained generic
provider_unavailable code cannot prove a cloud/provider outage. No preceding native
session.error was established by that evidence. The old synchronous per-token-fsync
observer is a plausible delay source, **not proven causality**. Its original harness
correction was not rerun here. New observers avoid per-token journalling/fsync, but
the product's durable attribution remains enabled.

The deterministic tests demonstrate a delayed heartbeat while correctly attributed
events still progress. The production fix permits one more bounded probe only with
current-owner progress and a verified live connected exact process/pipe. Dead or
closed transport, unattributed/no progress and a second timeout remain fenced.
Private diagnostics preserve exception type/module, boundary and elapsed time,
not exception text, auth or raw private prompts. The old missing exception cannot
be reconstructed and is explicitly still a missing observation.

## Verification and allocation

- Initial code handover: **132 Copilot checks passed**. The precise ephemeral-input
  correction then passed **43 focused recovery/delivery/fault checks**. Ruff and
  production mypy passed. A final exact-duplicate progress case brought the focused
  health/coordination set to **17 passing cases**. Replayed events cannot justify
  heartbeat deferral. No repeated broad test matrix or cloud comparison.
- Seven new retained-evidence checks pass. These are integrity/oracles, not model
  execution or certification of unknown outcomes.
- Allocation used: **one fresh-root SDK send**, zero continuation sends, zero
  native/development children. Four metadata-only cold resumes across the three
  old roots, with before-snapshots and exact original sessions, used **zero sends**.
- Every recorded server/browser/native/helper identity is closed. Metadata-host
  parents also exited. The already-terminal independent encoder was not rerun.
- No original M10 access, Blender/Unity/GPU/provider process or new independent job.
  No M03 acceptance tag or whole-M12 claim was created by this package.

```bash
PYTHONPATH=. uv run --offline python tests/native/copilot/runtime_recovery_report.py
uv run --offline pytest tests/acceptance/m03_normal_resume_evidence_test.py -q
```

Do not rerun the model/native drivers without a new allocation. Parent may accept
M03 from its cumulative evidence while keeping the unproven Bash outcomes and
missing-terminal Stop variant explicitly scoped to M12.
