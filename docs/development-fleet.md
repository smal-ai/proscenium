# Development fleet handover

**Baseline:** specification 3.2.0, contract 1.0.0 amended before implementation. **Status:** work packages defined, none dispatched or implemented.

This document governs agents developing Proscenium. It is separate from the product's native Copilot subagents, which author user projects at runtime. A work-package description is not authority to start agents, install models, alter shared services or modify the reference projects.

## F00: Foundation and compatibility gate

Assign one foundation/integration owner before independent implementation work. That owner is responsible for shared schemas, generated bindings, database migration ordering, application router composition, dependency locks and test-harness commands.

F00 must deliver:

1. Validation of contract 1.0 and deterministic TypeScript/Pydantic-compatible bindings, with no hand-written competing DTOs.
2. An exact SDK/runtime/dependency lock based on E01 and the [runtime lock](execution.md#pinned-runtime-lock). Include authentication, `session.abort()`, context delivery, sandbox off, shell left available, and callback attribution stored in SQLite.
3. Active-project working directory, disabled cross-session memory, Proscenium HTTP/publication path validation and distinct owner/tool/worker HTTP capabilities. No OS sandbox wrapper is required around Blender MCP or CLI tools.
4. One backend owner, request/outbox/event primitives, Stop/Resume transitions and the publication-to-job-intent commit boundary.
5. An offline fake-runtime/worker/provider harness for named scenarios. Test doubles must emit failures, races and uncertainty, not just successful replies.
6. An isolated test workspace factory, database migrations and source/coordinate/media fixture generators.
7. The command contract below and a recorded baseline revision consumed by every work package.

F00 overlaps the foundation work of M01; it is not an additional product feature. Do not dispatch a broad fleet against unpinned interfaces. A small owner-led E01/E02 proof can precede the complete application shell.

### Command contract to implement in F00

These are required future developer commands, **not commands currently available**:

| Command | Required scope |
| --- | --- |
| `make spec-check` | Schemas, examples, OpenAPI references, local links and design/reference hashes. |
| `make lint` | Maintained Python/TypeScript source and formatting rules. |
| `make typecheck` | Backend/frontend contract and application type checking. |
| `make test-unit` | Deterministic logic with no native apps, cloud calls or GPU work. |
| `make test-contracts` | Valid/invalid wire fixtures, generated-binding parity and semantic validators. |
| `make test-integration-offline` | SDK/provider/worker doubles, SQLite/outbox and failure traces. |
| `make test-browser` | Pinned Chromium against disposable data and numbered media. |
| `make test-native PROFILE=<profile>` | Explicit bounded Blender/Unity/provider tests under an acquired resource reservation. |
| `make test-browser-installed BROWSER=brave` / `BROWSER=edge` | Actual installed-browser compatibility cases. |

The current documentation-only validation command is documented in [acceptance](acceptance/README.md#documentation-validation). F00 supplies dependency manifests and the remaining commands before consumers are asked to run them.

## Work packages and write ownership

Paths below are target implementation locations. Missing directories are created only by their assigned owner. One agent may own several packages; the coordinator decides useful parallelism. Each assignment fixes a base commit, contract revision and permitted path list.

| Package | Deliverable and milestone slices | Owned paths | Consumes | Required evidence |
| --- | --- | --- | --- | --- |
| F00 Foundation/integration | Contracts, bootstrap, shared persistence/auth interfaces, generated bindings and harness | `docs/contracts/`, root dependency/build/CI files, `apps/backend/src/proscenium/contracts/`, `apps/backend/src/proscenium/db/`, `apps/backend/src/proscenium/api/router.py`, `apps/frontend/src/api/generated/`, `tests/contracts/`, shared fixture factory | Specification and E01/E02 | Binding parity, ownership/scope proof, migration rollback and offline fault baseline. |
| W01 Copilot integration | Native session adapter, Queue/Steer/Stop, callback decisions and recovery, M01/M03/M12 | `apps/backend/src/proscenium/copilot/`, `tests/integration/copilot/`, native agent profiles under `agent/profiles/` | F00 caller/context/event/decision contracts | C01–C05, C12–C14; E01 live evidence; no duplicated native sends. |
| W02 Source/catalogue | Workspace registration, closure resolution, publication/head CAS, `checkpoint_sources` and artefact indexing, M01/M02/M05 | `apps/backend/src/proscenium/projects/`, `sources/`, `catalogue/` beneath the same package, `tools/source/`, `tests/integration/sources/` | F00 storage transactions and source/publication schemas | C06–C09, C15; clean historical materialisation and lost publication acknowledgement. Other packages call this checkpoint tool. They do not invent another. |
| W03 Rendering/media | Isolated render/encode jobs, frame receipts, progressive segments and assembly, M01/M08/M13 | `apps/backend/src/proscenium/previews/`, `tools/blender/`, `tests/integration/previews/` | W02 materialisations, F00 jobs/events/resource claims | E03 on the draft engine with `camera: null` and view-transformed PNG review frames; C10–C11, C16; no source writes or unreceipted publication. |
| W04 Desktop shell/conversation | Linear Minimal layout, project navigation, chat and Queue/Steer/Stop/decision UI, M01/M02/M03 | `apps/frontend/src/app/`, `features/chat/`, `features/projects/`, corresponding browser tests | F00 generated client and W01/W02 interfaces | 1920×1080 layout, reconnect, pending decisions, Stop and held-queue controls. |
| W05 Review UI | Static proxy, image/video annotation, frame/range restoration and progressive player, M04/M08 | `apps/frontend/src/features/review/`, `features/catalogue/`, corresponding browser tests | W03 frame/segment APIs, F00 camera/annotation contract | F02–F04 fixtures, E03 in three browsers, C10/C11/C16. |
| W06 Research/storyboards | Generic reference/shot planning skills and shotboard data/UI, M05/M06 | `agent/skills/research/`, `agent/skills/storyboard/`, `apps/frontend/src/features/storyboards/`, corresponding fixtures/tests | F00 ShotPlan/selection APIs, W02 catalogue | Stable IDs, explicit missing inputs, no inherited SMAL defaults, autonomous stage progression. |
| W07 Local AI adapters | ComfyUI jobs/budgets and local VLM review, M07/M09 | `tools/comfy/`, `apps/backend/src/proscenium/providers/`, `agent/skills/image-generation/`, corresponding integration tests | F00 budget/review/job contracts and W02 artefacts | C03/C12/C17/C18; one endpoint throughout; no implicit service lifecycle changes. |
| W08 Unity | Scoped CLI/Editor import/controller/validation workflow, M11 | `tools/unity/`, `agent/skills/unity/`, `tests/integration/unity/` | W02 pinned source versions, W01 native specialist integration | F05, C19; repeat import without duplicate objects or GUID damage; actual Play mode. |
| W09 Image-to-3D proof | E04 and M10 if feasible | `tools/image_to_3d/`, proof-specific fixtures/evidence and `agent/skills/image-to-3d/` | W07 job/budget interface, W02 publication | Actual mesh, import and resource evidence. Record a known licence. Rights do not fail the proof. A mesh failure stays blocked and is escalated to the owner. |
| W10 Release acceptance | Cross-journey acceptance, operator documentation and backup/restore, M12/M14 | `tests/acceptance/`, operator documentation, acceptance evidence manifests | Accepted feature packages | C01–C31, all required browsers, clean workspace and explicit capability status. |

Only F00 changes schemas, generated bindings, dependency lockfiles, shared migrations, the composition router and global build commands. Feature agents submit an interface/migration proposal to that owner rather than editing shared files independently. The owner can delegate a bounded change explicitly and updates the ownership record.

### Backend endpoint ownership

[operation-owners.json](contracts/operation-owners.json) assigns every OpenAPI operation to exactly one backend package, implementation directory and acceptance cases. Its paths extend the package table above and must be included in the dispatched task's explicit write grant. The documentation validator rejects missing or multiply owned operations.

| Package | Explicit backend responsibility and additional owned paths |
| --- | --- |
| F00 | `apps/backend/src/proscenium/auth/`, `configuration/` and `jobs/`: owner pairing/caller authentication, configuration reads, common job/attempt admission, status, cancellation and retry. Shared repository interfaces and migrations remain F00-owned. |
| W01 | `apps/backend/src/proscenium/copilot/`: request/context history, native decisions, Stop/Resume and public event reads/streaming. |
| W02 | `apps/backend/src/proscenium/projects/`, `catalogue/` and `sources/`: project lifecycle/settings, image ingestion, artefact storage/media serving, source/publication reads and checkpoint tooling. |
| W03 | `apps/backend/src/proscenium/previews/`: render/encode execution, frame/segment receipts and progressive availability, using F00's job service. |
| W05 | `apps/backend/src/proscenium/reviews/`: annotation and typed-selection persistence, owner review/findings reads and rejection. This package owns the corresponding backend handlers as well as its existing review frontend. |
| W07 | `apps/backend/src/proscenium/providers/`: generation budgets/provider execution and VLM request/completion, consuming W05's accepted review-store interface and F00's job service. |

W06 consumes W02/W05 shot-plan and selection APIs; W08 calls the common job admission and checkpoint/publication tools. No feature package implements a second pairing, upload, job or selection service. F00 supplies interface stubs and the shared schema/migrations before these consumers start, and integrates each package's routers through the composition router.

## Assignment template

Each dispatched task includes:

```text
Package and goal:
Base application commit:
Specification/contract revision:
Exact allowed write paths:
Required reference pins and resource directories:
Consumed interfaces and fixtures:
Concrete outputs/endpoints/UI states:
Acceptance scenario IDs and commands:
Native/cloud/GPU test authorisation and resource reservation:
Known unsupported capabilities:
Escalation contact for shared-contract changes:
Return: changed paths, commit/revision, commands/results, evidence and unresolved limits.
```

Research/reference access is explicitly granted to the development task. It is not the runtime product's permission to read other user projects. Never inherit a reference project's outstanding creative work or run its historical scripts against its live scenes.

## Integration procedure

1. Work on an assigned branch/worktree and leave unrelated changes intact. Do not use another agent's uncommitted filesystem as an interface.
2. A package depends on an **accepted commit/contract revision**, not a message saying another agent is nearly done.
3. Run deterministic offline checks first. Native/LLM/GPU jobs require the coordinator's explicit bounded test allocation and an exclusive relevant resource reservation. Browser tests use their own database/owner capability.
4. Return actual evidence. Fixture output is labelled synthetic; it cannot be cited as proof of a live tool or model.
5. The integration owner reviews the patch, applies required shared migrations/wiring and reruns affected cross-package tests.
6. Mark the product milestone complete only after its real user journey and cumulative checks pass. A completed work package is not necessarily a completed milestone.

### Definition of ready for a package

- Its consumed contract revision and fixtures exist and validate.
- Required predecessor commits are accepted.
- Its write ownership and escalation route are explicit.
- Required runtime capabilities are either proven or the task is expressly a feasibility proof.
- Its test commands exist and the native-test allocation is clear.

### Definition of done

- Intended functionality and failure cases are implemented in the owned paths.
- No placeholder success, invented metrics or undeclared fallback closes an acceptance case.
- Schema/implementation drift, migrations, configuration and operator impacts are reported.
- Evidence records exact source/runtime/model/device/browser identities where relevant.
- Source, feedback and private assets from reference/user projects remain outside test mutations.
- Remaining defects or unproven capabilities are explicit. Only the integration owner accepts the package into the next baseline.

## Sequencing

Start F00 and the M01 real Blender loop under one integration owner. Once shared contracts and the offline harness are accepted, W01/W02/W03/W04 can be decomposed into bounded slices. W05–W08 can proceed against accepted interfaces and fixtures where their milestone dependencies permit. E04 begins early with explicit host/model-test permission. M12 proves recovery across the completed providers, while its delivery/ownership foundations are present from M01/M03.
