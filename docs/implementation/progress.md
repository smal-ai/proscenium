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

## Active assignments

| Package | Worktree / branch | Base | Scope | Agent |
| --- | --- | --- | --- | --- |
| W02 | `/tmp/opencode/proscenium-fleet/w02`, `impl/w02` | `7b95381` | Project/source/catalogue APIs, checkpoint, LFS materialisation and ingestion. | `ses_e264898f1ffeZI7lWdopwgAFJn` |
| W04 | `/tmp/opencode/proscenium-fleet/w04`, `impl/w04` | `7b95381` | Desktop shell, projects, conversation and runtime-control UI. | `ses_e264898baffe4JFIm65KLpjHLp` |
| W05 | `/tmp/opencode/proscenium-fleet/w05`, `impl/w05` | `7b95381` | Review backend, three viewer modes, annotation, selections and critic presentation. | `ses_e264897f0ffe99g7L7VPZVpjjv` |
| W01 | `/tmp/opencode/proscenium-fleet/w01`, `impl/w01` | `2bc3d5c` | Production Copilot adapter, context, durable request/decision/event delivery and native Stop/Resume. | `ses_e26448247ffeeKMC5WS8LrMSV5` |
| E03 | `/tmp/opencode/proscenium-fleet/e03`, `impl/e03` | `148ad0f` | Bounded native progressive-render proof and numbered media/browser fixtures. | `ses_e263fa96bffeElxNsPQlQ6n0j1` |
| W06 | `/tmp/opencode/proscenium-fleet/w06`, `impl/w06` | `148ad0f` | Adapted research/storyboard resources and typed shotboard UI against accepted API seams. | `ses_e263d813fffefNumtCvVqwR169` |

F00 returned `c0c31ec` and was integrated as `a52940d`. Integration corrected job admission/retry HTTP responses to the contract's 202 status and pinned shared image, WebSocket, browser-test and 3D/frontend dependencies before feature dispatch. The combined baseline passed specification/generated-binding checks, lint, types, 23 unit tests, 45 contract tests, 21 offline integration tests and a production frontend build.

## Resource allocations

| Allocation | Owner | Boundaries |
| --- | --- | --- |
| E01 | E01 agent | Its own temporary SDK runtime. At most eight small cloud root requests, each bounded to 120 seconds, plus explicitly scoped native children. No native authoring or GPU generation. Use supported existing login only. |
| F00 | F00 agent | Local package environments and deterministic offline fixtures. No native/model processes. |
| E02 fixture preparation | Coordinator | Disposable F01 only, one background Blender at a time, four CPU threads, 180-second per-process timeout. No GPU or desktop authoring. |
| W02 native source checks | W02 agent | Its own disposable copy of F01 or newly generated fixture. Sequential Blender CPU inspections, four threads, 180-second per-process timeout. |
| W04/W05 | Respective agents | Isolated browser data, local dependency environments and offline tests. No model/native/GPU processes. |
| W01 live persistence | W01 agent | Six small cloud root requests and at most two native children, 120-second request bounds. Its own direct runtimes and disposable projects, no Blender/Unity/GPU. |
| E03 | E03 agent | One CPU renderer, four threads, at most twelve 640×360 frames and sixteen samples, 300-second limit. Six short synthetic segments, one encoder at a time, sixty seconds each. Isolated Chromium/Brave/Edge profiles. No GPU/model services. |

## Milestones

| Milestone | State | Completion commit | Evidence |
| --- | --- | --- | --- |
| M01 First Blender authoring loop | Foundation in progress | — | — |
| M02 Projects and history | Pending | — | — |
| M03 Discussion and delegation | Pending | — | — |
| M04 Inspection and markup | Pending | — | — |
| M05 Asset research and reuse | Pending | — | — |
| M06 Storyboards | Pending | — | — |
| M07 Concept generation | Pending | — | — |
| M08 Progressive animation | Pending | — | — |
| M09 Requested VLM critique | Pending | — | — |
| M10 Image-to-3D | Feasibility pending | — | — |
| M11 Playable Unity scene | Pending | — | — |
| M12 Recovery and backup | Pending | — | — |
| M13 Silent cinematic delivery | Pending | — | — |
| M14 Consolidated acceptance | Pending | — | — |

## Host observations

Initial read-only inventory found Node `v24.18.0`, uv `0.11.32`, Git LFS `3.8.0`, Blender, FFmpeg, Xvfb, Brave and Edge executables. The default Python environment has no application dependencies. `/usr/bin/copilot` is a distribution stub rather than an installed runtime, so E01 owns an isolated official runtime installation and authentication check. No `DISPLAY` is inherited. These observations are prerequisites, not capability passes.

Native fixture preparation passed on Blender `4.5.14 LTS`, build `62c1db4208e8`. Two source commits contain real Git LFS Blender/texture objects. Reopening hydrated historical bytes retained the original 1 m cubes after the current asset became 1.5 m with a different texture. A repeated generator invocation verified/reused the result. [Evidence](../evidence/e02-fixture.json) records exact source and artefact hashes. Production resolver acceptance remains assigned to W02.

Read-only provider inventory found LM Studio responding at `http://127.0.0.1:1234`, ComfyUI unavailable at `http://127.0.0.1:8188`, and no running Blender or Unity process. Installed browsers report Brave `157.1.99.55` and Edge `157.0.4341.42`. ROCm reports Radeon 8060S `gfx1151`. No model service was started, stopped or reconfigured.

E01 verified SDK `1.0.34` with official CLI `1.0.112`, existing `gh-cli` authentication and GPT-6.1-Sol. The direct runtime binary's SHA-256 matches the application-local npm installation. The native queue pause/abort/reconciliation barrier was exercised, including deferred Steer and cold non-continuing resume. Child context requires the supported prompt hook. Eight bounded root sends and two executed children exhausted the E01 allocation, and all owned runtimes were stopped. [Exact evidence and remaining gaps](../evidence/e01/README.md) distinguish native observations from application-persistence work.
