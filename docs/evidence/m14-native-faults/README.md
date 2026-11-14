# Actual C16 failed-writer and C25 same-job retry observations

**PASS for these bounded renderer branches. No cumulative M12/M14 claim.**

| Observation | Actual result |
| --- | --- |
| Source | Accepted model-authored GeometryNodes-b Git/LFS history, imported read-only from the accepted project |
| Source commit | `fd6282b0a22725298d5f155181a8b64cabc762e0` |
| Entry | `assets/procedural/asset.blend` |
| SHA-256 | `fd0d1293080a1cd9f0bfaf3feace1488ec6c85e86705159a046daf07d35e0cd8` |
| Runtime | Blender `4.5.14 LTS`, build `62c1db4208e8`, Cycles CPU |
| Worker profile | Four threads, 640 × 360, eight samples, 300-second timeout |
| Consumption | Two workers, three actual rendered frames, one normal segment encode |
| Encode bound | 60 seconds, actual retained duration approximately 0.121 seconds |
| Independent services | Zero SDK/model/provider/Unity/authoring calls, no development children |
| Cleanup | Both supervisors and actual Blender identities dead, no resource claim or generation reservation left |

## Exact logical job and attempts

- Project: `m14-native-fault-operator-project`.
- Logical job: `preview-2d32402249ac9c855ac3dd8a72c1c9de886987d378223cf35b260a062ca3fae0`.
- Input digest: `d8642e14cebd1bc5bd07117acab7f3e2b1a3970bf8b37943753090189d1d36c3`.
- Version: `procedural-block / procedural-block-v1`.
- Exact closure digest: `3760c8a81175dc9d75deed66928c2054331903fc7099c9ec7f338707be5c5ac1`.

| Attempt | Actual native PID/start ticks | Actual exit | Outcome |
| --- | --- | --- | --- |
| `attempt-8654c287-61f3-4b5e-b9da-32cb5f537e53` | `943 / 47424240` | `1` | Frame 1 registered, frame-2 output guard failed, partial availability |
| `attempt-87a964ef-9b82-41ca-ad10-a875511c6a3f` | `6360 / 47437043` | `0` | Frame 1 adopted, frames 2–3 rendered, complete and playable 1–3 |

The first supervisor was `934 / 47424214`, the second `6355 / 47437017`.
First supervisor terminal time precedes second supervisor start. No overlapping writers.
Both used the existing OS claim
`/tmp/opencode/proscenium-native-claims/blender-render.lock` and their own writer locks.

The initial `native-process.json` can capture the `/bin/sh` launcher before `exec`.
The production `native-profile.json` records the actual pinned Blender executable, the
same PID and start ticks, version/build and evaluated Geometry Nodes result. Both the
initial identity and actual Blender identity are retained and checked dead. The evaluated
block has zero base vertices, eight evaluated vertices and six polygons in both workers.

## C16: actual failed writer / unreceipted partial output

Before launch, the operator wrote **78 bytes** only to first-attempt
`frames/00000002.png`. The exact bytes, hex, path and hash are in `actual-run.json`.
They begin `OPERATOR-INJECTED UNRECEIPTED PARTIAL PNG`, explicitly say
`not native-generated`, and contain a partial PNG signature. They are **not native-produced
image bytes** and have no receipt.

Injected SHA-256:
`ace66c27d689d84cf4854b4bc56e42863dfc9609e379ef4f33bd146e4017d171`.

Production rendered frame 1, wrote its real receipt and timing journal, then raised
`ValueError: Unreceipted output requires reconciliation, not overwrite` before any frame-2
render. Native error is `render_failed`, actual exit `1`, verified terminal job state `failed`,
availability `partial`, completed count one of three. No state was manually flipped.

- Available range: **1–1**.
- Failed range: **2–3**.
- Playable ranges: none, because the three-frame segment group was incomplete.
- No frame-2 receipt, no frame-2 artefact and no first-attempt segment.
- Injected bytes remain exactly unchanged after retry. They never became available media.
- Real frame-1 PNG is registered and served after failure. Production owner-authenticated
  ASGI route returns **200**, byte-range request **206**, preserving the same bytes and ETag.
  This is actual route execution in-process, not an end-to-end browser claim.

This closes the actual failed-writer/partial-output observation, **not actual disk
exhaustion**. No disk was filled, no mount was changed and no `storage_full` was injected
or claimed. That error mapping remains covered by offline guards only.

## C25: normal same-job retry after verified terminal failure

After real failure verification and original process exit, the explicit operator-authorised
normal tool/service route called `jobs.retry` on this same job and input. F00's public
service is tool-only, so the synthetic admission caller is labelled honestly rather than
misrepresented as an owner HTTP retry or fresh Copilot invocation.

- Retry operation: `m14-explicit-owner-authorised-tool-retry`.
- Expected failed-job revision: `6`.
- New attempt number: `2`, same job, target, render specification and input digest.
- Old exact frame-1 receipt replay was acknowledged without mutation.
- A fresh stale-attempt receipt and a stale `running` state update were both rejected with
  `scope_denied`. No native exit code was supplied by the state probe. The snapshot before
  and after these probes was identical.
- Production launcher copied only registered/hash-verified frame 1 and created a new-attempt
  receipt retaining the same artefact ID/hash. No junk file was adopted.
- Second worker has timings only for frames **2 and 3**, with no frame-1 timing. First worker
  has timing only for **1**. Thus four retained receipt records are **three actual renders**,
  not four renders: old frame 1, adopted frame 1, new frames 2 and 3.
- Second actual native exit is `0`, terminal job `succeeded`, availability `complete`,
  available/playable range **1–3**, three verified frames and one normal three-frame segment.
- Original attempt, error, journals, frame-1 PNG and injected partial bytes remain intact.

The owner selection pins the imported **source version**, not a fabricated creative
selection of a preview image. Its selection record is identical before and after retry.
Frame-1 media separately remained reviewable and byte-identical across the retry:
SHA-256 `2129178e4dc5f6763fc718e228e2555a4262569710e2dead01f0fd324be122f0`,
218,900 bytes. Full and range-route responses remained unchanged.

## Retained harness failures and continuation

The initial source import stopped before admission because Git does not clone local LFS
filter configuration. Only the owned clone's local filters were configured, and the copied
local LFS object was hydrated. No source download or source project write occurred.

Driver `80630a4` then completed the first actual native failure, but its reporting failed:
it incorrectly used the immutable worker-journal helper for a changing operator report.
The original `result.json` is retained unchanged with its hash. No worker output, runtime
state or terminal evidence was rewritten. The first driver's live-observation samples were
not durably saved. They are not reconstructed or claimed. Actual live Blender identity is
present in the worker's native profile. The resumed driver's recorded first RAM value is a
post-failure observation, not a reconstruction of first-worker peak usage.

Correction `c624e6e` committed revisioned operator reports and an explicit continuation
guard **before worker 2**. It required the sole original attempt to be verified failed,
both processes dead, no second attempt and unchanged injected bytes. It did not relaunch
worker 1 or reset runtime state. One remaining allocated worker completed the normal retry.
Both original and correction allocations are retained, and total native workers remain two.

## Retention and scope

Private root:
`/home/quintin/.cache/proscenium-development/milestones/m14-native-faults-a`.

- `actual-run.json`: complete retained observations, both tasks/helper seals, actual process
  profiles, exits, logs, receipts, attempts, range responses, selection and source checks.
- `retention.json`: exact hashes/sizes/paths of source materialisations, real PNG/MP4 media,
  injected bytes, journals, timings, reports and process identities. Both attempt roots stay
  retained. There is no automatic media cleanup.
- `post-export-verification.json`: actual post-`exec` Blender identities are dead, resource
  claim table empty, no generation reservation or inference budget consumption.
- Original source Git HEAD/refs and `.blend` hash are unchanged. Clone `.blend` and both
  independently materialised production source hashes match the accepted source.
- Application base `04ad1810b5abe1334364b5074fe4123a703c4c2f`. No production/shared/jobs/schema/
  UI/Copilot code changed. No M10 reopening, M08 backend-death rerun, cancellation race,
  provider recovery, backup restore, actual disk exhaustion or cumulative milestone claim.
