# Bounded actual C16/C25 renderer fault proof

**Actual result: PASS for C16 failed-writer/partial-output and C25 verified failed-attempt
same-job retry. No cumulative M12/M14 completion claim.**

## Actual outcome

- Driver `80630a4` preceded worker 1. Reporting-only correction `c624e6e` preceded worker 2.
  The reporting defect and its original report are retained below, not concealed as a clean run.
- One logical job
  `preview-2d32402249ac9c855ac3dd8a72c1c9de886987d378223cf35b260a062ca3fae0`, same input digest
  `d8642e14cebd1bc5bd07117acab7f3e2b1a3970bf8b37943753090189d1d36c3`.
- First attempt `attempt-8654c287-61f3-4b5e-b9da-32cb5f537e53`: actual native exit `1`,
  `render_failed`, job `failed`/`partial`, real frame 1 registered and reviewable, failed range
  2–3. Exactly 78 labelled operator bytes at frame-2 output, no receipt, unchanged and unavailable.
- Normal F00 retry after verified terminal failure allocated
  `attempt-87a964ef-9b82-41ca-ad10-a875511c6a3f`. Exact old receipt replay acknowledged without
  mutation. Fresh stale receipt and stale state update rejected with `scope_denied`.
- New attempt adopted only registered/hash-verified frame 1. It rendered only frames 2–3,
  exited `0`, reached `succeeded`/`complete` and published one normal segment, available and
  playable range 1–3. Native timings prove three actual renders across the two workers.
- Selected source version and reviewable frame-1 bytes were unchanged across retry. Actual
  production content/range route returned 200/206 before and after with identical PNG hash.
- Both actual Blender identities and supervisors are dead. Resource claims and generation
  reservations are empty. Original source/Git refs/hash, clone source and both sealed runtime
  source materialisations are preserved. No production code or independent service changed.
- Explicitly not proved: actual disk exhaustion/`storage_full`, cancellation-completion race,
  provider reconnect, backup restore, cumulative M12/M14. No SDK/model/authoring calls occurred.

Full identities, exact bytes, failure/exit, tasks/helper seals, profiles, counts, preservation
and caveats are in [the retained evidence](../evidence/m14-native-faults/README.md).

## Allocation and preflight

- Base `04ad1810b5abe1334364b5074fe4123a703c4c2f`, branch `impl/m14-native-faults`.
- Own only `m14_fault*` preview/acceptance helpers and this evidence/documentation slice.
  No production/shared/schema/jobs/UI/Copilot changes and no development children.
- Zero SDK/model/provider/Unity/authoring calls. M10 and the accepted M08 backend-death
  proof are not reopened or repeated.
- Actual accepted GeometryNodes-b Git history at
  `fd6282b0a22725298d5f155181a8b64cabc762e0` and local LFS bytes are imported into
  `/home/quintin/.cache/proscenium-development/milestones/m14-native-faults-a/project`.
  No source authoring helper or old runtime/session store is executed/copied. Original
  repository ID is preserved as the imported Git source identity in a new runtime project.
- The first hydration stopped because local Git LFS filters are not cloned. Before any
  admission/native launch, only the owned clone's local filters were configured and its
  already-copied local LFS objects hydrated. No download or source repository write occurred.
- Exact entry `assets/procedural/asset.blend`, SHA-256
  `fd0d1293080a1cd9f0bfaf3feace1488ec6c85e86705159a046daf07d35e0cd8`.
- One logical job requests frames 1–3, Cycles CPU, 640 × 360, eight samples, four threads.
  At most two workers, 300 seconds each, one at a time under the existing OS claim.
  Three actual renders are planned under the four-frame cap. One normal three-frame
  segment encode is planned, bounded at 60 seconds. No cinematics or separate stills.
- Caller/work/invocation fields are explicitly synthetic operator admission context.
  Source authorship is inherited from accepted evidence, not a fresh Copilot invocation.
  Retry uses F00's normal tool-only `retryToolJob` service with explicit operator authority.

## Planned fault and retained boundaries

Before worker 1 launch, write exactly labelled operator junk to its owned
`frames/00000002.png`, without a receipt. Production must render/receipt frame 1 then raise
`Unreceipted output requires reconciliation, not overwrite` before rendering frame 2.
This is the failed-writer/partial-output branch of C16, **not actual disk exhaustion**.
`storage_full` remains offline evidence only.

After the actual terminal journal and native exit are verified, normal retry must preserve
the logical job/input, allocate a new attempt, adopt only registered/hash-verified frame 1
and render frames 2–3. Existing exact old receipt replay is allowed without mutation.
Fresh stale receipt and stale state probes must be rejected before the second launch.
Owner selection pins the imported source version. Verified frame-1 bytes must remain served
by the production owner-authenticated content/range route before and after retry. This is
an in-process ASGI route proof, not a browser journey claim.

First-attempt files, junk, failure, receipts, task/helper seals and process identities remain
retained. Timing journals distinguish rendered frames from adopted frames. A failed stage
stops the allocation rather than buying a third worker or a replacement logical job.

## Prelaunch checks

- 100 affected/offline tests passed: new pure installed-loop preflight, preview integration
  and foundation job recovery. No Blender or model process was launched by these checks.
- Actual W02 publication/F00 admission and source materialisation completed with zero
  spawns. Runtime bounds, claim path, exact source hash and absence of launch claim verified.
- Driver and pure preflight are committed before workers. Actual outcomes are recorded
  separately after the bounded run. No cumulative M12/M14 completion claim is made.

## First actual failure and owned reporting correction

Driver `80630a4` launched exactly one actual worker. It rendered and registered frame 1,
then failed at the intended frame-2 output guard, with native exit `1`, `render_failed`,
partial availability and verified terminal job evidence. No segment was encoded.

After observing this terminal failure, the driver itself failed to update its operator
report because it incorrectly used the immutable worker-journal writer. The original
`result.json` remains unchanged. Actual worker journals, injected bytes, registered frame,
terminal job/attempt and source were unaffected. Live driver observations were not durably
saved, so they are not reconstructed or claimed. Actual positive PID/start identities
remain retained in production supervisor/native process journals.

The reporting-only correction saves numbered immutable report revisions plus a separate
latest pointer. Its explicit continuation requires the original sole attempt to be verified
failed, both original processes dead, no second attempt and unchanged injected bytes.
It does not rerun worker 1, create another job, flip runtime states or change any worker
journal. The remaining allocation is still one worker for the normal same-job retry.
