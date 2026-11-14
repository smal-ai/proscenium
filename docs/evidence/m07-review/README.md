# M07 review identity follow-up

Parent owns M07 acceptance. This scope fixes review identity, not model quality.

## Authorised execution boundary

- One own production-app server and one Full-HD Chromium browser, at most 300 seconds, four-GiB available-memory headroom.
- Only retained M07 images. Zero SDK sends, provider requests, native authoring, rendering or model-service lifecycle.
- Explicit browser pairing and saved-project activation. Read-only owner navigation, no creative-selection mutations.
- Private original: `/home/quintin/.cache/proscenium-development/image-generation-light/runs/m07-fp16-execution-20261114/`.
- Disposable import and new captures: `/tmp/opencode/m07-review-actual-20261114-b/`.

## Fixture limitation, discovered before app/browser start

The accepted backup tool correctly rejects `project_busy`. The original harness left a scripted invocation with `work_id: m07-setup-work` in `running`, despite completing that setup work. Both actual native invocations, their works and both generation jobs are terminal. This is a retained fixture defect, not permission to repair an unrelated backend or falsify an invocation outcome.

The bounded importer therefore uses the accepted read-only `BackupSource` SQLite backup API, copies the original project and exact associated native history into private disposable storage, sanitises/rebinds a fresh registry, and preserves the scripted orphan unchanged. Protected record and budget hashes must match before and after readback. The app uses unavailable native/provider configuration and guards SDK sends, subprocess creation, outbound HTTP and non-read-only owner operations. Only pairing and project activation writes are permitted.

The first failed backup attempt removed two **empty** original SQLite WAL sidecars. Their recorded SHA-256 was `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`. No database, source, image, report or non-empty file changed. Original inventory from that attempt is retained privately under `/tmp/opencode/m07-review-actual-20261114-a/`. The isolated import's subsequent full original-file inventory is unchanged.

## Actual execution outcome: incomplete, not accepted

- Start: **14 November 2026, 20:47:01 SAST**. Elapsed own stack: **17.841484 seconds**.
- Source: `f6747bb221f40a4680436d34fd0602fa07dbcd4f`. Product and regression tests were committed before the actual stack started.
- Actual browser: Chromium `159.0.8154.6`, executable SHA-256 `045558b2f167db04624e36d952496705418a85d87ff9b5fb55a8c40cc59201ae`.
- JS: `index-D8_DbBaC.js`, SHA-256 `0fce5b5a5b3f76d973a8e2c5e6ca6e2989983d7b24aeb41ced81700b42e262b8`.
- CSS: `index-VG5WV1Cp.css`, SHA-256 `8d68faf9412d4fb66e6eba2ff9831a78d7076648dbf12581b45d653eca41d517`.
- Minimum MemAvailable: **36741410816 bytes**, above the four-GiB guard.

Pairing returned 201. The saved project snapshot then changed the project-trigger button's accessible name from `Choose a project` to its actual project name while the driver tried to click the old exact label. The driver timed out before explicit activation. **Zero review views, image URI/hash comparisons or displayed job/context comparisons were verified in this actual follow-up.** There is no passed composed-app claim and no invented screenshot.

The driver now targets the stable `button.project-trigger` selector and retains a failure screenshot on future failures. That correction passed syntax checking but was **not rerun against another actual stack**. The authorised single stack was consumed. A fresh bounded allocation is needed to finish actual readback.

### Cleanup and preservation

- Own server exited 0, browser driver exited 1. Both direct child processes were reaped and the browser closed through its own `finally` handler.
- Port 18969 is released. Execution gate remained closed, lifecycle errors are empty, and zero SDK sends/provider sends or subprocess-guard violations occurred.
- All original files in the import baseline are unchanged. Protected restored job/request/attempt/input/selection/source/media/work/invocation records and generation-budget rows retain identical hashes after shutdown.
- Only one owner mutation occurred: browser pairing. No Queue, Steer, Resume, creative selection, annotation save, generation or native work was submitted.
- Full raw private receipts remain in the disposable root. Only sanitised receipts, protected-record hashes and synthetic browser screenshots are exported here.

## Focused regression results

Twelve checks pass at the final implementation: three UI scenarios plus one pure identity check in each real Chromium, Brave and Edge browser engine. Engine versions recorded by Playwright are Chromium `159.0.8154.6`, Brave `157.0.8103.39` and Edge `157.0.4341.42`. These use controlled API/image fixtures, **not real M07 generation acceptance**.

They cover external initial-to-correction jumps, both explicit concept job choices, exact target context, conditioning exclusion, missing/pending output honesty, no-job references, late-job/draft/range stability, ambiguous ownership, hash mismatch and preserved sequence job-control behaviour. Existing Chromium checks also pass for durable original-operation retry, progressive gaps with pinned annotation, external navigation and actual recorded-M04 frame camera/save/restore.

With `M07_REVIEW_ACTUAL` set to this retained run, the acceptance module reports **seven passes and one failure**. The boundary/cleanup check passes. The actual UI-readback assertion deliberately fails on the genuine activation-driver timeout rather than treating missing proof as success. Without a retained run, six guard checks pass and the two retained-evidence checks skip.

The original cobalt-blue correction remains **not achieved**. Neither PNG is recoloured or replaced. Parent still owns M07 acceptance.
