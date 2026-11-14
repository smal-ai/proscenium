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
| `4c14ab4`, `milestone/m03` | Coordinator acceptance of durable discussion, native delegation, Queue/Steer/Stop and explicit continuation. |
| `a616460`, `milestone/m04` | Coordinator acceptance of static inspection, exact camera renders and saved markup. |
| `4866886`, `304c245`, `milestone/m05` | Actual `scene-main-v2` adoption of unchanged `asset-cube-v3`, followed by cumulative acceptance. |
| `b044e89`, `milestone/m06` | Coordinator acceptance of model-authored planning, targeted revisions, owner selection and corrected direct review. |
| `455840a`, `milestone/m08` | Coordinator acceptance of progressive review, pinned inputs and final frame lineage. |
| `8da4147`, `milestone/m09` | Coordinator acceptance of actual VLM reviews and exactly-once owner-approved native handoff. |
| `03dbad1` | Selective ACK filtering reached real native acceptance; the original explicit-cut helper failed. The failed allocation and corrected offline pipe helper are retained. |
| `b85ce21`, `cc4d1a6`, `289b93a`, `904f733` | Exact native-denial/unentered-checkpoint recovery and validated operation-aware permission classification. Both original M10/M11 records have zero live invocations and await explicit Resume. |

## Active assignments

| Package | Worktree / branch | Base | Scope | Agent |
| --- | --- | --- | --- | --- |
| M07 FP16 preparation | `/home/quintin/.cache/proscenium-development/fleet/m07-fp16`, `impl/m07-fp16` | `455840a` | Bounded CPU conversion of the verified SD-Turbo checkpoint into a separately identified FP16 derivative/profile. No new inference in this preparation. | `ses_e262de015ffe8DXqhgJPJyyt0K` |
| Final accepted ACK-loss | `/home/quintin/.cache/proscenium-development/fleet/m12-ack-final`, `impl/m12-ack-final` | `03dbad1` | Preflighted selective-response loss, persisted pre-cut facts, real pipe closure and original-ID reconciliation. | `ses_e25df843bffetn697vChzMs1dn` |
| M10 final continuation | `/home/quintin/.cache/proscenium-development/fleet/m10-final`, `impl/m10-final` | `904f733` | One original-work Continue, canonical asset revision and native checkpoint/publication. No inference. | `ses_e25ed774effeLrmFIM6D17Qv1x` |
| M11 final publication | `/home/quintin/.cache/proscenium-development/fleet/m11-final`, `impl/m11-final` | `904f733` | One original-work Continue to checkpoint/publish the existing tested Unity scene. No new children, Editors or exports. | `ses_e24cae3afffeduGiWpAGVlgj62` |
| Stateless Geometry Nodes | `/home/quintin/.cache/proscenium-development/fleet/geometry-nodes`, `impl/geometry-nodes` | `03dbad1` | Bounded stateless Geometry Nodes presentation and actual model-authored CPU/native proof. Unsupported simulation/cache/import dependencies remain explicit. | `ses_e263fa96bffeElxNsPQlQ6n0j1` |
| M14 SSH/review | `/home/quintin/.cache/proscenium-development/fleet/m14-ssh`, `impl/m14-ssh` | `904f733` | Actual message continuity, selected-video playback/download and saved feedback over authenticated SSH, followed by configured-unavailable service review. | `ses_e24c92d5effec3g3ZSSa6I6Ytm` |

F00 returned `c0c31ec` and was integrated as `a52940d`. Integration corrected job admission/retry HTTP responses to the contract's 202 status and pinned shared image, WebSocket, browser-test and 3D/frontend dependencies before feature dispatch. The combined baseline passed specification/generated-binding checks, lint, types, 23 unit tests, 45 contract tests, 21 offline integration tests and a production frontend build.

The later composed F00 baseline passed 26 unit, 45 contract and 292 offline integration tests, plus lint/types/specification/build and a real pairing/project/WebSocket CLI startup smoke with external execution blocked. Package test counts describe their recorded revisions, not a new full release run.

The subsequent integrated run at `e3cddf8` passed **26 unit, 45 contract and 488 offline integration tests**. Recovery-Stop admission changes additionally passed 31 targeted delivery/fault/cancellation tests. No native execution was inferred from those checks.

The integrated browser matrix has **173 passing unique cases** across Chromium, Brave,
Edge and the DPR-2 review subset. It combines 109 initial passes and 64 remainder
passes after correcting an Edge test-oracle timing race, with no production change
between those runs. [The exact matrix](../evidence/browser-matrix-20261114.json)
preserves that execution history. At `904f733`, **77 affected recovery/permission/evidence
checks passed**. These are additional scoped checks, not a relabelled full baseline run.

## Current acceptance blockers

- W07's original `w07-live-initial` remains planned with one reservation and no prompt/client ID. Both original and append-only continuation windows ended without inference. The separate SD-Turbo F32 profile also failed before startup, short of its unchanged floor by 288,244,784 bytes. Its original checkpoint is preserved while a bounded, separately identified FP16 derivative is prepared.
- E04 `e04-live-a` created no project, job, reservation or native process. The distinct `e04-live-b` passed hardware execution under the unchanged guards. M10 static inspection passed; its normal revision/publication is now allocated to the original work.
- M02 is accepted. Its clean A→B run and real closed-gate cancellation supplement the preserved historical-source proof.
- M03 proved active-child Stop, economical native delegation, queued context and durable owner input. The cinematic journey additionally proved actual same-work Continue through the corrected UI. Older genuinely interrupted invocations remain a recovery diagnosis; no terminal return is invented.
- M05 is accepted. The final native scene adoption was saved and published as `scene-main-v2`, pinning unchanged `asset-cube-v3`; alternate scene v1 retains asset v1. Earlier failed scripts/reports are preserved.
- M06 is accepted. The shared-reference readiness correction passed an actual guarded application replay without further model calls.
- M09 is accepted after M03/M04. Both real review origins and exactly-once approved handoff passed; only the owned Gemma instance was unloaded. Qwen and the saved profile remained unchanged.
- M10's exact native commands denial is reconciled, with orphaned reply/authority/ACK facts preserved. Request `927cf238-56c2-454e-a063-014e072f9010`, work `work-bc19a5da5fcd4aaf9ae552a13d06b20d` and session `5971843f-f216-40f8-b8cb-995675f48161` now have a bounded original-work continuation. No revised v2 is claimed before its result.
- W10's protected-process blocker is fixed. The final SSH audit passed. Same-host transport/API traffic does not itself close full model-message/video playback or cumulative M12/M14.
- M08 is accepted after M04/M06. M11 import/PlayMode passed. The `tool-install.json` permission false positive is fixed and the unentered wait is reconciled without an invented owner answer. Original request `1a914aa8-2bbc-461a-a87d-1bb7431de02c` and work `work-4a7e644e78514109b4a5232316e3aacc` now have a publication-only continuation. No Unity publication is claimed before its result.
- M12 restored the real M01 native session and delivered a held request once in the relocated project. Its latest selective ACK-loss test reached native acceptance, but a pipe-helper type assumption prevented the explicit cut. The next proof preflights that helper and persists acceptance/withheld-response facts before cutting; previous attempts remain failed/partial.
- M07 host pressure is now materially external: the latest observation was 14,436,642,816 bytes available. An unrelated SQL Server used about 5 GiB RSS and a Blender instance in `smal-ai-blender` about 3 GiB. Neither is owned by this build or eligible for cleanup. The original cold-fit threshold is unchanged.
- Remaining milestones require their cumulative application journeys. Component/browser/native fixture passes alone do not close them.
- FR-08.4's stateless Geometry Nodes presentation gap is assigned. M14 additionally needs the remaining provider/recovery evidence, the complete SSH message/video journey and revision-aware consolidated release validation.

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
| M07 continuation/native, consumed without inference | W07 agent | Original job/attempt/task/reservation remain in append-only window `w07-continuation-b`, with unchanged cold-fit threshold. No generation or native request was admitted in that continuation window. |
| M08 | W03 agent | Three roots/one child maximum, eighteen 640×360 CPU frames/eight samples/four threads, one proxy and two bounded authoring/inspection processes. One owned-backend kill tests detached-worker recovery. |
| M11 | W08 agent | Three roots/two product children maximum, three bounded Unity invocations and one Blender export. Actual import/PlayMode/result publication; no GPU inference. |
| M12 native | W10 agent | Two read-only SDK sends maximum, no children/native authoring/providers. Exact native-history recovery and one owned transport interruption only. |
| M13 | F00 agent | Three cloud roots and three short tracked FFmpeg encodes maximum, 60-second encoder bounds. Existing verified frame inputs only; no Blender, Unity or GPU/model-service execution. |
| Runtime recovery | W01 agent | At most two fresh harmless SDK sends for normal Stop/Continue, no children; reuse M13's actual positive Continue evidence if no fresh send started. Old-project investigation is metadata-only and cannot fabricate outcomes. |
| M05 adoption follow-up | W06 agent | One SDK root, one bounded authoring/inspection process, one proxy and one optional CPU still. No reacquisition or inference. |
| M10 continuation | W09 agent | One original-work SDK continuation, one authoring process/readback, one proxy and one CPU still. No new Queue, manual script execution or GPU inference. |
| M12 accepted ACK-loss | W10 agent | One SDK send, no tools/children/providers. Genuine hook reply precedes stdout gating and actual pipe closure. Old uncertain request is not retried. |
| M07 FP16 conversion | W07 agent | CPU-only streaming conversion of the existing F32 checkpoint, at most 1 GiB conversion RSS and 300 seconds. Preserve original bytes, non-F32 tensors and metadata, reject overflow/non-finite conversion. Four-GiB host headroom. No service/model execution or second download. |
| M12 final selective proof | W10 agent | One SDK send after actual buffered/unbuffered pipe and import/path preflight. Persist RPC/response/native-acceptance facts before the real cut. No second send, local provider or native authoring. |
| M10 final continuation | W09 agent | One original-work SDK continuation, 180 seconds, after exact denial recovery. At most one four-thread Blender authoring/readback, one proxy and one 640×360/eight-sample CPU still under the existing resource claim. Four-GiB host headroom. No new Queue or inference. |
| M11 final publication | W08 agent | One original-work SDK continuation, 180 seconds. Existing saved/tested scene only. Zero children, Editors, Blender, exports or renders. New private before/after snapshots and one bounded Full-HD browser/server stack. |
| Stateless Geometry Nodes | W03 agent | One native root, 180 seconds, no children. One four-thread authoring process, one proxy and at most two 640×360/eight-sample CPU stills, 300-second worker bounds and shared render claim. Four-GiB host headroom. No simulation, GPU or provider execution. |
| M14 SSH/review | W10 agent | One read-only SDK Queue, 180 seconds. One private fixture-key loopback sshd and at most two sequential forwarding clients, one browser/server at a time, 600-second proof bound. Read-only import of accepted M13 media into new isolated storage. No new renders/inference or independent-service changes. Four-GiB host headroom. |

## Milestones

| Milestone | State | Completion commit | Evidence |
| --- | --- | --- | --- |
| M01 First Blender authoring loop | Completed on 2026-11-14 | `milestone/m01` | [Exact criteria and limitations](m01.md), evidence integrated as `a218a65` |
| M02 Projects and history | Completed on 2026-11-14 | `milestone/m02` | [Accepted criteria and retained failures](m02.md) |
| M03 Discussion and delegation | Completed on 2026-11-14 | `milestone/m03` | [Cumulative runtime criteria and remaining M12 fault limits](m03.md) |
| M04 Inspection and markup | Completed on 2026-11-14 | `milestone/m04` | [Native framing and composed review acceptance](m04.md) |
| M05 Asset research and reuse | Completed on 2026-11-14 | `milestone/m05` | [Actual discovery, import, revision and explicit adoption](m05.md) |
| M06 Storyboards | Completed on 2026-11-14 | `milestone/m06` | [Model-authored planning and corrected direct review](m06.md) |
| M07 Concept generation | Model-fit preparation; both previous execution windows remain blocked before effects | — | [Original and lightweight limits](m07.md) |
| M08 Progressive animation | Completed on 2026-11-14 | `milestone/m08` | [Pre-completion review, pinned inputs and final lineage](m08.md) |
| M09 Requested VLM critique | Completed on 2026-11-14 | `milestone/m09` | [Actual review, owner selection and native handoff](m09.md) |
| M10 Image-to-3D | Hardware/inspection passed; original revision awaits continuation | — | [E04](../evidence/w09/live-b/README.md), [M10](m10.md) |
| M11 Playable Unity scene | Native import/PlayMode passed; original publication continuation active | — | [Exact criteria and retained blocker](m11.md) |
| M12 Recovery and backup | Same-session restore/continuation and worker recovery passed; accepted ACK-loss pending | — | [Native recovery](m12.md), [worker recovery](m08.md) |
| M13 Silent cinematic delivery | All seven targeted criteria passed; dependencies pending | — | [Native edit/review/download proof](m13.md) |
| M14 Consolidated acceptance | SSH message/video and Geometry Nodes slices active; consolidation pending | — | — |

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
