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

Actual UI outcome and exact source/build/browser/hash receipts are appended only after execution. The original cobalt-blue correction remains **not achieved**. Neither PNG is recoloured or replaced.
