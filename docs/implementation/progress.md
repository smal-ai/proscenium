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
| `4e8b233`, `milestone/m01` | Coordinator acceptance of all M01 criteria with retained execution limitations. |
| `7b1f837` | Actual proxy framebuffer/camera capture, GLB picking and catalogue search/provenance. |
| `80ff7bf` | E04 actual Hunyuan mesh, Blender import and W02 checkpoint/publication. M10 user interaction remains separate. |
| `583e550`, `6c02885` | Native catalogue/Unity/assembly tools, required Unity specialist, awaited lifecycle and preparation/cancellation ownership. All 92 scoped workflow tests passed in the parent. |
| `f96e78b`, `a46c9ba` | Retired-session isolation, late proxy context associations and distinct native permission stages. |
| `a2a2097` | Actual browser/SDK camera renders at 1031×569 and 2063×1138, maximum measured framing error 0.145 px. |
| `7114824`, `2295602` | Decoded-image readiness, saved-camera and clean-image role fixes; actual guarded M06 shared-reference review passed. |
| `069a854` | Both real VLM review origins, owner amendment/rejection and exactly-once approved-finding native handoff passed. |
| `5333468`, `2f4258a` | Correct Resume work targets/revisions, preserved current-version context and accurate PNG/video guidance. Seven focused browser cases passed. |
| `e3cddf8` | Actual SSH product-transport follow-up passed with final guard/cleanup audit. |
| `f752676`, `milestone/m02` | Coordinator acceptance of project isolation, catalogue histories, native historical reopening and independent cancellation. |
| `ff8a32d` | Recovery Stop records its control intent while preserving the existing work state until the verified native barrier. No contract-edge change. |
| `0b8e9ba` | Actual pre-completion frame annotation/playback, pinned source isolation, detached render survival and final movie lineage. |
| `2ea26dd`, `134eb2f` | Read-only source backup handles and actual same-session native-history restoration/held-message continuation. Accepted-send ACK-loss remains separate. |
| `bdea12c` | Actual native sequence assembly, same-work UI Resume, composite annotation, revised edit and matching UI downloads. |

## Active assignments

| Package | Worktree / branch | Base | Scope | Agent |
| --- | --- | --- | --- | --- |
| M07 generation | `/home/quintin/.cache/proscenium-development/fleet/m07-generation`, `impl/m07-generation` | `069a854` | Original image-job continuation, then native generation/correction and UI acceptance if resources fit. | `ses_e262de015ffe8DXqhgJPJyyt0K` |
| Runtime recovery | `/home/quintin/.cache/proscenium-development/fleet/runtime-recovery`, `impl/runtime-recovery` | `0b8e9ba` | Normal Continue assessment and evidence-led diagnosis of stale native coordination/health failures. | `ses_e26448247ffeeKMC5WS8LrMSV5` |
| M05 adoption | `/home/quintin/.cache/proscenium-development/fleet/m05-adoption`, `impl/m05-adoption` | `bdea12c` | Finish the explicit one-scene adoption and historical inspection with a narrow native continuation. | `ses_e263d813fffefNumtCvVqwR169` |
| M10 continuation | `/home/quintin/.cache/proscenium-development/fleet/m10-continuation`, `impl/m10-continuation` | `bdea12c` | Reconcile and continue the original generated-asset revision without replacing its Queue or manually running its script. | `ses_e25ed774effeLrmFIM6D17Qv1x` |
| Accepted ACK-loss | `/home/quintin/.cache/proscenium-development/fleet/m12-accepted-ack`, `impl/m12-accepted-ack` | `0b8e9ba` | Callback-aware actual pipe interruption after native admission, then original-ID reconciliation. | `ses_e25df843bffetn697vChzMs1dn` |

F00 returned `c0c31ec` and was integrated as `a52940d`. Integration corrected job admission/retry HTTP responses to the contract's 202 status and pinned shared image, WebSocket, browser-test and 3D/frontend dependencies before feature dispatch. The combined baseline passed specification/generated-binding checks, lint, types, 23 unit tests, 45 contract tests, 21 offline integration tests and a production frontend build.

The later composed F00 baseline passed 26 unit, 45 contract and 292 offline integration tests, plus lint/types/specification/build and a real pairing/project/WebSocket CLI startup smoke with external execution blocked. Package test counts describe their recorded revisions, not a new full release run.

The subsequent integrated run at `e3cddf8` passed **26 unit, 45 contract and 488 offline integration tests**. Recovery-Stop admission changes additionally passed 31 targeted delivery/fault/cancellation tests. No native execution was inferred from those checks.

## Current acceptance blockers

- W07's original `w07-live-initial` remains planned with one reservation and no prompt/client ID. Its 600-second allocation ended before inference because host RAM stayed below 20,427,268,612 bytes. Preserve that identity for a separately allocated continuation.
- E04 `e04-live-a` created no project, job, reservation or native process. The distinct `e04-live-b` passed hardware execution under the unchanged guards. M10 still needs static inspection and normal revision/reuse acceptance.
- M02 is accepted. Its clean A→B run and real closed-gate cancellation supplement the preserved historical-source proof.
- M03 proved active-child Stop, economical native delegation, queued context and durable owner input. The cinematic journey additionally proved actual same-work Continue through the corrected UI. Older genuinely interrupted invocations remain a recovery diagnosis; no terminal return is invented.
- M05 proved metadata discovery, real Khronos acquisition, editable import/revision and a real asset render. A test selector included floor/wall geometry and stopped its adoption script. The explicit scene-adoption continuation remains unexecuted. Re-selecting the current version is fixed.
- M06's actual planning/revision/selection proof is retained. The shared-reference readiness defect now passes an actual guarded application replay without further model calls.
- M09's positive journey passed after two narrowly scoped VLM fixes. Only the owned Gemma instance was unloaded; Qwen and the saved profile remained unchanged. Milestone acceptance still depends on M03/M04.
- M10 has actual proxy inspection but its original revision request was fenced before Blender launch. Late proxy association and duplicate permission-stage defects are fixed. Preserve request `927cf238-56c2-454e-a063-014e072f9010` and session `5971843f-f216-40f8-b8cb-995675f48161` for explicit continuation; no replacement Queue or manual script execution.
- W10's protected-process blocker is fixed. The final SSH audit passed. Same-host transport/API traffic does not itself close full model-message/video playback or cumulative M12/M14.
- M08's native progressive path and detached-worker recovery passed. M11 import and PlayMode passed, but the original publication root is held by an unresolved native parent-task outcome. M12 restored the real M01 native session and delivered a held request once in the relocated project; its first pipe-loss placement was pre-admission and remains unproved for accepted ACK loss.
- M07 host pressure is now materially external: the latest observation was 14,436,642,816 bytes available. An unrelated SQL Server used about 5 GiB RSS and a Blender instance in `smal-ai-blender` about 3 GiB. Neither is owned by this build or eligible for cleanup. The original cold-fit threshold is unchanged.
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
| E04 live-b | W09 agent, completed | One Hunyuan attempt and one Blender import passed. No owned processes/claims remain, and port 18588 is free. Lowest sampled host available RAM was 5.89 GiB, above both guards. |
| Recovery/SSH | W10 agent | One temporary unprivileged loopback sshd, one SSH client, one disposable application server and Chromium profile, 300-second process bounds. Fixture keys only. No native/model/GPU work. |
| M02 | M02 agent | Two small cloud roots, no children, 120 seconds each. One historical Blender CPU inspection, four threads, 180 seconds. Disposable Full-HD browser and server. |
| M03 | M03 agent | Six cloud roots and three native children maximum, 180 seconds per root. Harmless project-local native activity has a 90-second self-timeout. No Blender/Unity/provider/GPU work. |
| M04 | M04 agent | Two cloud roots, no children, 180 seconds each. Three CPU stills maximum at the exact captured dimensions, eight samples/four threads, 300-second worker bounds, plus at most one static proxy. No GPU/model-service work. |
| M06 | M06 agent | Two cloud roots, no children, 180 seconds each. Actual browser/SDK planning only; no rendering, provider or native editor execution. |
| M09 positive | W07 agent, completed | One saved-profile Gemma load, two real VLM calls, two cloud roots, no children. Exactly approved findings/image delivered once. Owned Gemma and all proof processes stopped; Qwen preserved. |
| M10 | W09 agent | No inference. Two cloud roots, one authoring process, one readback, two proxy exports and two 640×360 CPU keyframes maximum. Four threads/eight samples; preserve four-GiB host headroom and the original E04 project. |
| M02 follow-up | M02 agent | Two small read-only roots maximum, no children/native authoring/rendering. Actual UI project switch and planned-preview cancellation only. |
| M03 Resume | W01 agent | One SDK continuation send and at most one product child; 180-second root and 90-second harmless helper bounds. No replacement Queue, renderer or provider. |
| M07 continuation/native | W07 agent, active exclusive GPU allocation | Preserve original job/attempt/task/reservation in append-only window `w07-continuation-b`, with unchanged cold-fit threshold. At most three Comfy executions total: original 64×64 plus two native 256×256/four-step image cases. Three cloud roots maximum, no children. No service lifecycle changes. |
| M08 | W03 agent | Three roots/one child maximum, eighteen 640×360 CPU frames/eight samples/four threads, one proxy and two bounded authoring/inspection processes. One owned-backend kill tests detached-worker recovery. |
| M11 | W08 agent | Three roots/two product children maximum, three bounded Unity invocations and one Blender export. Actual import/PlayMode/result publication; no GPU inference. |
| M12 native | W10 agent | Two read-only SDK sends maximum, no children/native authoring/providers. Exact native-history recovery and one owned transport interruption only. |
| M13 | F00 agent | Three cloud roots and three short tracked FFmpeg encodes maximum, 60-second encoder bounds. Existing verified frame inputs only; no Blender, Unity or GPU/model-service execution. |
| Runtime recovery | W01 agent | At most two fresh harmless SDK sends for normal Stop/Continue, no children; reuse M13's actual positive Continue evidence if no fresh send started. Old-project investigation is metadata-only and cannot fabricate outcomes. |
| M05 adoption follow-up | W06 agent | One SDK root, one bounded authoring/inspection process, one proxy and one optional CPU still. No reacquisition or inference. |
| M10 continuation | W09 agent | One original-work SDK continuation, one authoring process/readback, one proxy and one CPU still. No new Queue, manual script execution or GPU inference. |
| M12 accepted ACK-loss | W10 agent | One SDK send, no tools/children/providers. Genuine hook reply precedes stdout gating and actual pipe closure. Old uncertain request is not retried. |

## Milestones

| Milestone | State | Completion commit | Evidence |
| --- | --- | --- | --- |
| M01 First Blender authoring loop | Completed on 2026-11-14 | `milestone/m01` | [Exact criteria and limitations](m01.md), evidence integrated as `a218a65` |
| M02 Projects and history | Completed on 2026-11-14 | `milestone/m02` | [Accepted criteria and retained failures](m02.md) |
| M03 Discussion and delegation | Completed on 2026-11-14 | `milestone/m03` | [Cumulative runtime criteria and remaining M12 fault limits](m03.md) |
| M04 Inspection and markup | Completed on 2026-11-14 | `milestone/m04` | [Native framing and composed review acceptance](m04.md) |
| M05 Asset research and reuse | Completed on 2026-11-14 | `milestone/m05` | [Actual discovery, import, revision and explicit adoption](m05.md) |
| M06 Storyboards | Planning/selection and corrected review passed; dependencies pending | — | [Original proof](m06.md), [review follow-up](../evidence/w05/readiness-readme.md) |
| M07 Concept generation | Original-job continuation running under resource guard | — | — |
| M08 Progressive animation | Targeted journey passed; dependencies pending | — | [Progressive and worker-recovery proof](m08.md) |
| M09 Requested VLM critique | Completed on 2026-11-14 | `milestone/m09` | [Actual review, owner selection and native handoff](m09.md) |
| M10 Image-to-3D | Hardware/inspection passed; original revision awaits continuation | — | [E04](../evidence/w09/live-b/README.md), [M10](m10.md) |
| M11 Playable Unity scene | Native import/PlayMode passed; original publication blocked | — | [Exact criteria and blocker](m11.md) |
| M12 Recovery and backup | Same-session restore/continuation and worker recovery passed; accepted ACK-loss pending | — | [Native recovery](m12.md), [worker recovery](m08.md) |
| M13 Silent cinematic delivery | All seven targeted criteria passed; dependencies pending | — | [Native edit/review/download proof](m13.md) |
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

E04 live-b produced a 19,860,948-byte GLB with 350,259 vertices and 1,304,618 triangles. Blender imported it and W02 published `e04-generated-asset` / `e04-asset-v1` from checkpoint `94a17bb13ab6669eb4096473038be8cc8349975a`. Shape is untextured, without UVs or calibrated physical scale. One generation reservation was consumed. No independent service changed.

Nine completed tmpfs worktree virtual environments were subsequently removed as rebuildable dependency caches. All source, evidence, native history and active disk-backed environments remain. Available RAM immediately afterwards measured 22,411,755,520 bytes. Focused pytest commands now explicitly include the repository root so maintained `tools` imports do not depend on another suite's import side effects.
