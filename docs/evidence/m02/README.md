# M02 real project/history proof

**Strict M02: FAIL pending parent integration fixes.** Successful recovered
observations are retained, not used to conceal the failures. See
[criteria](../../implementation/m02.md) and [exact patch requests](parent-proposal.md).

| File | Meaning |
| --- | --- |
| `verification.json` | Strict failed result, recovered facts and explicit limits. |
| `identities.json` | Accepted test base/M01 dependency, exact app/build hashes, SDK/model/browser and pinned F01 fixture identities. |
| `native-redacted.json`, `runtime-process.json` | Two real session histories reduced to allowlisted identities/tool/model events, cloud intervals, native PID/start and CLI hash. No private reasoning or raw transcript. |
| `production-records.json` | Actual persisted public records and allowlisted events from both projects. Scripted fixture work is separately labelled. |
| `fixture-receipts.json` | Real source/checkpoint/publication/materialisation/inspection outcomes, retained failed admission and explicit cancellation workaround. |
| `browser-native-b.json`, `browser-recovered-b.json` | Original held request and same-ID recovery, not a replacement model prompt. |
| `browser-pinned-after-advance.json/.png` | Live source publication advanced while the actual old version remained pinned. |
| `browser-reopened-a.json/.png`, `browser-reopened-b.json/.png` | Actual compiled UI after restart, durable catalogue/session IDs and one active project. |
| `browser-asset-cube-v2.png`, `browser-scene-main-v2.png` | Actual catalogue navigation and source/version identity. Displayed scene image is an imported reference, not a new M02 render. |
| `metadata-scene-main.json/.png` | Actual authenticated browser API inspection of full version/source/provenance data. Not a catalogue metadata UI claim. |
| `metadata-scene-main-v1.json` | Retained driver route mistake, HTTP 404. |
| `cancel.json`, `browser-post-cancel.json` | Accepted owner HTTP cancellation and real lifecycle-stalled state. |
| `m01-before.json`, `m01-after.json`, `cleanup.json` | Read-only M01 preservation and owned-process cleanup. |
| `manifest.json` | Exact hashes/lengths for exported evidence. |

Private retained root:
`/home/quintin/.cache/proscenium-development/milestones/m02-20261114T135654Z/`.
Pairing URL, cookie, CSRF, browser profile, private logs/authentication and raw SDK
history remain outside Git. Only allowlisted evidence is exported.

## Verify without another cloud/native allocation

```bash
.venv/bin/python tests/native/studio/m02_evidence.py \
  /home/quintin/.cache/proscenium-development/milestones/m02-20261114T135654Z
.venv/bin/pytest -q tests/acceptance/m02_evidence_test.py
```

These commands verify retained **actual** source/LFS/media/native-history bytes
and evidence integrity. They start no server, browser, model or Blender, and do
not change the strict FAIL verdict into a new acceptance pass.

The standalone drivers require an explicit new allocation for another live run.
Commands are immutable files, claimed before effects and never automatically
retried after uncertainty. Only source-fixture code is reloaded by the operator
mailbox. Product code is the unchanged accepted application base. No model-facing
fixture API exists. Both cloud requests used the real browser Queue control.
