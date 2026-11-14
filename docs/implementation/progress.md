# Implementation programme

**Authorised:** 2026-11-14. **Specification:** 3.2.0. **Contract:** 1.0.0.

The owner requested the complete M01–M14 system, a fleet of OpenCode agents using `github-copilot/gpt-6.1-sol`, individual commits for completed milestones, and autonomous coordination while away. The coordinating agent owns integration, technical decisions, resource allocations, acceptance and the milestone history. The development fleet is separate from the product's native Copilot agents.

## Execution rules

- Implement against accepted commits and the normative contracts. Raise shared-interface changes to the coordinator.
- Use isolated worktrees and explicit path ownership. Preserve the reference checkouts and existing application/model sessions.
- Install necessary application and development dependencies into local, ignored environments. Independent model-service installation, upgrades and lifecycle remain separately controlled.
- Resolve technical choices within the agreed scope. Record blocked external prerequisites and continue independent implementation.
- Implement, run the relevant checks, fix actual failures and integrate. Additional test/review cycles require a concrete defect or unresolved acceptance condition.
- A package commit is implementation evidence. A milestone completion commit additionally requires its actual cumulative user-journey evidence. Synthetic tests do not establish native/model acceptance.
- Retain M10 as required if its proof fails. The coordinator cannot invent successful generation or silently remove requested scope.

## Accepted baselines

| Revision | Meaning |
| --- | --- |
| `2f00ebece9c5274ebda553df4c1558d5572669e0` | Corrected specification, contracts and documentation fixtures. |
| `a52940d` | Accepted F00 offline interfaces, bindings, storage, authentication, jobs and harness. Native SDK acceptance remains E01-owned. |
| `7b95381` | Shared feature dependencies and frontend component seams accepted after type/lint checks. |
| `fd330ae` | E01 runtime-pair evidence accepted within its documented limits. |
| `3ef8020` | W04 desktop/project/conversation implementation, initially verified with synthetic browser APIs. |
| `b6f1f62` | E03 actual progressive-media and three-browser codec/paused-frame proof. |
| `76d8451` | W06 maintained research/storyboard skills and typed shotboard UI. Integrated journeys remain pending. |
| `9945df9`, `5da13fb` | W05 review backend and UI. Forty-eight component browser cases passed, including continuous numbered segment boundaries and missing-interval behaviour. |
| `72b0771`, `3c38031`, `df933fa`, `d2fe9a6` | W02 projects/catalogue/checkpoints/media and native historical-source evidence. |
| `029e56a`, `cfa378d`, `b907972` | Production Copilot adapter, actual native-child context consumption and scoped metadata tools. |
| `3881c80`, `a55f2f2` | Production renderer/assembly and atomic frame/artefact/job/event reduction. |
| `ae02c10`, `10b439b` | Canonical generation budgets, exact-worker registration and versioned image/mesh capability plugins. |
| `65358d1` | All 53 normative operations, quiet application lifecycle, native association projection and copy/rebind coordination. |
| `2920201` | W10 backup/restore tooling and evidence validator, including retained incomplete-release results. |
| `a218a65` | Actual M01 browser/SDK/Blender visual-feedback journey, source correction and replay/restart evidence. |

## Active assignments

| Package | Worktree / branch | Base | Scope | Agent |
| --- | --- | --- | --- | --- |
| Native workflows | `/home/quintin/.cache/proscenium-development/fleet/native-workflows`, `impl/native-workflows` | `b907972` | Unity specialist, tracked nonblocking Unity jobs, sequence assembly and catalogue-discovery tools. | `ses_e262dde9cffevqHbPSmMm5CYKL` |
| Inspection/catalogue | `/home/quintin/.cache/proscenium-development/fleet/review-camera`, `impl/review-camera` | `b907972` | Actual GLB picking/capture, Render this view context, provenance/search and pinned metadata refresh. | `ses_e264897f0ffe99g7L7VPZVpjjv` |
| E04 live-b | `/home/quintin/.cache/proscenium-development/fleet/w09-live-b`, `impl/w09-live-b` | `9720285` | Renewed one-attempt mesh execution after reclaiming build-owned tmpfs caches. | `ses_e25ed774effeLrmFIM6D17Qv1x` |
| Recovery/SSH | `/home/quintin/.cache/proscenium-development/fleet/w10-integration`, `impl/w10-integration` | `9720285` | Fix unrelated protected-process backup blocker and prove actual SSH-forwarded application traffic. | `ses_e25df843bffetn697vChzMs1dn` |

F00 returned `c0c31ec` and was integrated as `a52940d`. Integration corrected job admission/retry HTTP responses to the contract's 202 status and pinned shared image, WebSocket, browser-test and 3D/frontend dependencies before feature dispatch. The combined baseline passed specification/generated-binding checks, lint, types, 23 unit tests, 45 contract tests, 21 offline integration tests and a production frontend build.

The later composed F00 baseline passed 26 unit, 45 contract and 292 offline integration tests, plus lint/types/specification/build and a real pairing/project/WebSocket CLI startup smoke with external execution blocked. Package test counts describe their recorded revisions, not a new full release run.

## Current acceptance blockers

- W07's original `w07-live-initial` remains planned with one reservation and no prompt/client ID. Its 600-second allocation ended before inference because host RAM stayed below 20,427,268,612 bytes. Preserve that identity for a separately allocated continuation.
- E04 `e04-live-a` created no project, job, reservation or native process. It recorded 10.46 GiB available against a 12 GiB fit floor. The distinct `e04-live-b` allocation retains those guards.
- LM Studio's inspected resident model is non-vision. No VLM inference or approved-finding native journey has passed yet.
- Parent integration found 18 W10 checks blocked by an unrelated protected `sshd-session` process. The 127 other checks in that affected run passed. Recovery/SSH owns the scoped-process correction.
- Remaining milestones require their cumulative application journeys. Component/browser/native fixture passes alone do not close them.

## Resource allocations

Earlier rows record consumed allocations. Only the explicitly active assignments above may start their current bounded work.

| Allocation | Owner | Boundaries |
| --- | --- | --- |
| E01 | E01 agent | Its own temporary SDK runtime. At most eight small cloud root requests, each bounded to 120 seconds, plus explicitly scoped native children. No native authoring or GPU generation. Use supported existing login only. |
| F00 | F00 agent | Local package environments and deterministic offline fixtures. No native/model processes. |
| E02 fixture preparation | Coordinator | Disposable F01 only, one background Blender at a time, four CPU threads, 180-second per-process timeout. No GPU or desktop authoring. |
| W02 native source checks | W02 agent | Its own disposable copy of F01 or newly generated fixture. Sequential Blender CPU inspections, four threads, 180-second per-process timeout. |
| W04/W05 | Respective agents | Isolated browser data, local dependency environments and offline tests. No model/native/GPU processes. |
| W01 live persistence | W01 agent | Six small cloud root requests and at most two native children, 120-second request bounds. Its own direct runtimes and disposable projects, no Blender/Unity/GPU. |
| E03 | E03 agent | One CPU renderer, four threads, at most twelve 640×360 frames and sixteen samples, 300-second limit. Six short synthetic segments, one encoder at a time, sixty seconds each. Isolated Chromium/Brave/Edge profiles. No GPU/model services. |
| W07 VLM | W07 agent | Read-only provider inventory and at most two 64×64-image, 512-output-token VLM calls on an already loaded model, 120 seconds each. No model loading or ComfyUI generation/service lifecycle. |
| W08 | W08 agent | Its own disposable Unity project. At most three Unity invocations, 600 seconds each, plus one Blender CPU export, four threads, 180 seconds. Existing official Editor/licence, isolated project dependencies, own Xvfb if needed. |
| W03 | W03 agent | At most twenty CPU-rendered 640×360 frames, sixteen samples, four threads, 300 seconds per worker, plus three static proxies. One renderer at a time and bounded short encodes. No model/GPU/service changes. |
| M01 | M01 agent, completed | Three cloud roots, no children, two rendered frames. All 18 observed owned processes exited. Independent Blender on 9876 preserved. |
| E04 live-b | W09 agent, active exclusive GPU allocation | One temporary ComfyUI on free loopback 18588, one fixed Hunyuan attempt, 1800-second server bound, four CPU threads. Unchanged 12 GiB fit floor and four-GiB protected headroom. One Blender import, 180 seconds. Fresh disk-backed run directory. |
| Recovery/SSH | W10 agent | One temporary unprivileged loopback sshd, one SSH client, one disposable application server and Chromium profile, 300-second process bounds. Fixture keys only. No native/model/GPU work. |

## Milestones

| Milestone | State | Completion commit | Evidence |
| --- | --- | --- | --- |
| M01 First Blender authoring loop | Completed on 2026-11-14 | `milestone/m01` | [Exact criteria and limitations](m01.md), evidence integrated as `a218a65` |
| M02 Projects and history | Pending | — | — |
| M03 Discussion and delegation | Pending | — | — |
| M04 Inspection and markup | Pending | — | — |
| M05 Asset research and reuse | Pending | — | — |
| M06 Storyboards | Pending | — | — |
| M07 Concept generation | Pending | — | — |
| M08 Progressive animation | Pending | — | — |
| M09 Requested VLM critique | Pending | — | — |
| M10 Image-to-3D | Plugin implemented, hardware proof pending | — | [E04 resource blocker](../evidence/w09/execution/README.md) |
| M11 Playable Unity scene | Pending | — | — |
| M12 Recovery and backup | Pending | — | — |
| M13 Silent cinematic delivery | Pending | — | — |
| M14 Consolidated acceptance | Pending | — | — |

## Host observations

Initial read-only inventory found Node `v24.18.0`, uv `0.11.32`, Git LFS `3.8.0`, Blender, FFmpeg, Xvfb, Brave and Edge executables. The default Python environment has no application dependencies. `/usr/bin/copilot` is a distribution stub rather than an installed runtime, so E01 owns an isolated official runtime installation and authentication check. No `DISPLAY` is inherited. These observations are prerequisites, not capability passes.

Native fixture preparation passed on Blender `4.5.14 LTS`, build `62c1db4208e8`. Two source commits contain real Git LFS Blender/texture objects. Reopening hydrated historical bytes retained the original 1 m cubes after the current asset became 1.5 m with a different texture. A repeated generator invocation verified/reused the result. [Evidence](../evidence/e02-fixture.json) records exact source and artefact hashes. Production resolver acceptance remains assigned to W02.

Read-only provider inventory found LM Studio responding at `http://127.0.0.1:1234`, ComfyUI unavailable at `http://127.0.0.1:8188`, and no running Blender or Unity process. Installed browsers report Brave `157.1.99.55` and Edge `157.0.4341.42`. ROCm reports Radeon 8060S `gfx1151`. No model service was started, stopped or reconfigured.

E01 verified SDK `1.0.34` with official CLI `1.0.112`, existing `gh-cli` authentication and GPT-6.1-Sol. The direct runtime binary's SHA-256 matches the application-local npm installation. The native queue pause/abort/reconciliation barrier was exercised, including deferred Steer and cold non-continuing resume. Child context requires the supported prompt hook. Eight bounded root sends and two executed children exhausted the E01 allocation, and all owned runtimes were stopped. [Exact evidence and remaining gaps](../evidence/e01/README.md) distinguish native observations from application-persistence work.

E03 delivered a first frame at 0.933 s, a first four-frame segment at 2.845 s and the final frame at 7.671 s from the bounded CPU fixture. Sources remained unchanged. Chromium, Brave and Edge passed actual H.264 decode and paused-frame correspondence for the real numbered media. Product continuous-boundary/hole/annotation behaviour remains W05 acceptance.

W04 integration added explicit sibling-view navigation and corrected rejection recovery. Eight synthetic shell scenarios passed in each installed browser family, for 24 cases total. This verifies real browser UI behaviour against controlled API fixtures, not the unfinished combined backend journey.

W05 is composed into the production frontend and the backend's accepted-feature router list. A targeted integration follow-up connects main-chat submission to active annotation preparation and hardens catalogue-selection acknowledgement recovery. The combined foundation/review/storyboard backend suite passes 61 tests. Source/catalogue, native request execution and provider integration remain pending package returns.

W02's 17 operations are now composed. Its actual production resolver reopens original and revised linked Blender bytes with measured 1.0 m / 1.5 m heights and rejects the missing texture. Integration fixed current-schema read handles attempting nested migration writes, and shares strict canonical-mask validation between upload and review services. The affected real-source/review integration suite passes 77 tests.

The integration-owned browser command now covers the composed shell, review/context and storyboard suites. **126 cases passed** across Chromium, Brave, Edge and the Chromium DPR-2 review subset. Main-chat preparation includes unsaved annotations and masks, rejects failed saves and preserves the captured target through navigation. The browser APIs remain controlled fixtures. Live application journeys are the next acceptance layer.

Resource inspection found `/tmp` is a 31 GiB tmpfs with about 8 GiB free, while the disk-backed home filesystem has about 348 GiB free. Larger subsequent native scratch work uses `/home/quintin/.cache/proscenium-development/` to avoid exhausting RAM-backed temporary storage. Existing evidence is retained.

Seven completed-proof native package caches, totalling 2,058,885,576 bytes, were copied to disk, hash-verified and replaced by cache-path links. Native histories, databases and media stayed in place. The private relocation receipt is `/home/quintin/.cache/proscenium-development/retained-build-caches/2026-11-14-cache-moves.json`. The immediate post-move observation was 16,004,345,856 bytes of available RAM, enabling the renewed E04 allocation without lowering its threshold.
