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
- Private browser results: `/tmp/opencode/m07-review-browser-20261114-c/`. Screenshots use per-test directories. Previous attempt `-b` retains three test-selector ambiguity failures, corrected without a product change.
- Build retains existing Lucide `use client` and large-chunk warnings.

Actual retained-image composed-app readback is recorded separately under `docs/evidence/m07-review/`. No fresh generation is authorised or required. The earlier cobalt-blue correction was not achieved and is not changed by this UI fix.
