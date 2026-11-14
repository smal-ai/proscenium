# M06 real planning acceptance evidence

**Result: REMAINING.** Real planning/publication, targeted revision, annotation and
typed owner-selection journeys passed. Direct same-image navigation between shots
exposed an unresolved W05 target-readiness defect. This is not a milestone PASS.

The compiled host application, owner HTTP routes, Python Copilot SDK 1.0.34,
official native CLI 1.0.112 and `gpt-6.1-sol` were real. The single source image is
an explicitly labelled synthetic **owner reference**, uploaded through the real
W02 route. It is not model-generated media or a Blender result. No API was mocked.

## Evidence map

- `identities.json`: exact base, runtime, browser, native session, compiled bundle
  hashes and changed storyboard-feature hashes.
- `authored-m06-kite-board-v{1,2}.json`, `brief-*.md`: actual model-authored Git
  source bytes, checked against immutable source closures.
- `publications.json`, `checkpoints.json`: actual persisted native invocation
  associations, input commands and authoritative service receipts.
- `native-tool-invocations.json`, `invocations.json`: actual native tool calls and
  host-persisted tool attribution. No private reasoning or authentication export.
- `native-messages-redacted.json`: allowlisted user/assistant public text, without
  native system instructions, reasoning, authentication or raw session history.
- `public-final.json`, `before-revision.json`: real owner read models and exact
  contexts, selections and annotations before/after the targeted publication.
- `selection-commands.json`: actual UI select/supersede/withdraw/reselect bodies.
- `role-contexts.json`: actual central/parent reference contexts for start,
  intermediate, end and second-shot start. The second-shot observation uses the
  explicit reopen workaround described below, not a passed direct transition.
- `dependency-impact.json`, `source-verification.json`: model-produced helper
  report, rehashed historical source/asset/reference bytes and bounded root counts.
- `known-review-defect.json`: the retained null-target failure and reproduction.
- `cleanup.json`, `reopen-observation.json`: owned-process shutdown/reaping and
  unchanged request/job counts after browser reload, without Resume.
- PNGs: real 1920 × 1080 owner-browser captures. `owner-reference.png` is the
  original labelled 640 × 360 source, not a screenshot of a generated concept.
- `manifest.json`: exact SHA-256 and byte count for every non-README evidence file.

## Remaining defect

Click `kite-rise-start`, then `kite-rest-start`. Both use the same reference
artefact. W05 updates the shot target and clears `captureReady`, but the unchanged
`<img src>` does not fire another load event. The central image remains visible
while emitted `ContextSnapshot.target` becomes null and annotation is disabled.
An explicit visit to the material asset version before returning to the other
storyboard shot remounts the reviewer and allows correct context again. The proof
records that workaround, not a successful direct transition.

Fix belongs in the parent/W05 `ReviewWorkspace` external-target decoding effect.
It must reuse verified readiness for unchanged decoded image bytes or explicitly
revalidate their identity/dimensions. No W05/shared code was changed by this task.

Full criteria, checks and limitations: `docs/implementation/m06.md`.
