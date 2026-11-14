# M07 review job identity correction

Specification 3.2.0 and contract 1.0.0 are unchanged. Parent owns M07 acceptance.

## Product correction

- The displayed target determines its associated job. Concept identity uses exact job output membership, artefact ID, displayed hash and dimensions. No render job or frame is invented for a concept.
- Source/reference images have an explicit `No associated job` option instead of inheriting the first job.
- Explicit job navigation supports ordinary generated concepts, using declared output order rather than asynchronous fetch order. Conditioning encode derivatives are excluded.
- Missing/pending results retain the current identified image, context and label, with an unavailable-output message.
- Draft targets, ranges, camera restoration, old-version pinning and sequence/frame identities remain authoritative. Late job arrival only refreshes data.

## Focused checks before actual readback

- Frontend typecheck, ESLint and production build passed.
- Twelve focused checks passed across actual Chromium, Brave and Edge, comprising nine browser scenarios and three repetitions of the pure identity check.
- Existing recorded-M04 frame-camera/save/restore regression passed in Chromium.
- Private browser results: `/tmp/opencode/m07-review-browser-20261114-d/`. Screenshots use per-test directories. Previous attempt `-b` retains three test-selector ambiguity failures, corrected without a product change. The final focused run additionally locks the existing sequence playback job-control semantics.
- Build retains existing Lucide `use client` and large-chunk warnings.

Actual retained-image composed-app readback is recorded separately under `docs/evidence/m07-review/`. No fresh generation is authorised or required. The earlier cobalt-blue correction was not achieved and is not changed by this UI fix.

## Actual follow-up limitation

The single actual app/browser stack at `f6747bb` consumed 17.841484 seconds and was cleaned up. Pairing succeeded, but the driver used an obsolete project-trigger label after the saved-project snapshot loaded. It failed before explicit activation and review navigation. **Actual image/job/context readback is not complete.** The driver selector is corrected but no second stack was started without another allocation.

The original scripted setup invocation also remains `running`, blocking the ordinary settled backup. A read-only SQLite backup/disposable import preserved this fixture defect unchanged. Protected record/budget hashes and original files were unchanged by readback. The first attempted backup removed two empty WAL sidecars, fully disclosed in the evidence. No backend repair, native send, provider call or model-service lifecycle occurred.

The product fix is covered by the focused actual-browser fixtures and existing review regressions. Parent must allocate and accept the remaining real retained-image readback before treating this follow-up as complete.

## Renewed final follow-up

Parent accepted the original fix and allocated up to two sequential read-only app/browser stacks on base `6c7cec5`, including `cef82ee`'s reproduced empty-WAL preservation correction. New receipts live under `docs/evidence/m07-review/final/`. Earlier failed evidence remains unchanged.

A bounded correction retains the uniquely associated generation job's known shot when constructing an image target. Project/item/version and exact output membership are checked, with null shot for unassociated/ambiguous images. Render-job and source-frame fields remain null for concepts. The one affected new regression passes in all three browser engines.

Both renewed stacks were consumed without review acceptance. The first imported registry activated the shell implicitly and reset its project menu. The second used an explicitly inactive import and reached actual owner activation, but the proof guard rejected the product's read-only `git rev-parse` root validation before spawning Git. Both failures and screenshots are retained under the final evidence prefix. Total stack time was 262.568660 seconds. Original inventories/protected records stayed unchanged, with zero SDK/provider sends and no third stack.

The fixture now requires explicit activation, handles the shell's deliberate remount and permits only the two exact disposable-root Git validation commands (`--show-toplevel` and `--show-object-format`). Its positive and negative guard cases pass offline. That final proof-guard correction has not been run against another actual app. Actual image/job/context/known-shot readback still needs a fresh allocation. The shot product correction itself is committed and narrowly browser-tested.
