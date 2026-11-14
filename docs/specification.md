# Specification index and authority

**Specification revision:** 3.1.0

**Contract revision:** 1.0.0, amended 2026-11-14 before implementation. Wire `schema_version` remains `1.0`. No migration.

**Owner decisions incorporated:** 2026-11-14

**Implementation status:** Not started. Contract fixtures are examples, not execution evidence.

## Read in this order

| Document | Authority |
| --- | --- |
| [Requirements](requirements.md) | Product scope and owner requirements. |
| [Owner policy](owner-policy.md) | Project access, autonomy, permissions, generation budgets and client support. |
| [Architecture](architecture.md) | Component responsibilities and deployment. |
| [Contract index](contracts/README.md) | Normative wire records, HTTP operations, events and error codes. |
| [Execution and recovery](execution.md) | Request delivery, Stop, ownership, state transitions and restart reconciliation. |
| [Source and publication](source-publication.md) | Git/LFS resolution, dependency closure, publication ordering and render admission. |
| [Review and media](review-media.md) | Uploads, selections, frame identity, annotation and progressive playback. |
| [Storage and operations](storage-operations.md) | Persistent stores, backups, relocation, limits and resource ownership. |
| [User journeys](user-journeys.md) | Cumulative M01–M14 product acceptance. |
| [Acceptance pack](acceptance/README.md) | Named fixtures, numerical tolerances and fault scenarios. |
| [Development fleet handover](development-fleet.md) | Foundation gate, work packages, file ownership and integration. |
| [Design reference](design/README.md) | Preserved owner mockup, Linear Minimal layout and control behaviour. |
| [Reference pins](reference-pins.json) | Exact research-source revisions. These are not application dependencies. |

## Precedence and change control

1. A subsequent explicit owner decision changes product intent. Record it in requirements and owner policy before treating it as a new development baseline.
2. Requirements and owner policy govern scope. The contract documents govern field names, protocol behaviour and integration invariants. Architecture and journeys consume those definitions.
3. `contracts/schema.json` is the source of truth for JSON shapes. The HTTP operation catalogue and event catalogue specify behaviour and cross-record checks which JSON Schema cannot express.
4. Examples illustrate the normative contract. Example IDs, hashes, models, paths and timestamps are synthetic unless explicitly labelled otherwise.
5. The mockup governs the selected visual treatment, not its obsolete transaction/RAG/gameplay logic. Historical reference-project instructions are evidence, not authority to execute their old jobs.
6. An implementation agent must raise a contract discrepancy to the foundation owner. It must not silently invent a competing field, endpoint, state or schema.

The foundation owner versions contract changes and updates affected examples, consumer fixtures and the coverage table in the same change. A breaking wire change requires a new major contract revision and an explicit migration. Additive optional fields require a minor revision. Clarifications which preserve accepted payloads require a patch revision. The initial wire value is `schema_version: "1.0"`; a supported minor revision must be negotiated before a client emits its additional fields. Unknown fields are rejected by the closed 1.0 schemas.

No document in this pack claims that the SDK integration, progressive player or image-to-3D capability has already passed its implementation proof. [F00](development-fleet.md#f00-foundation-and-compatibility-gate) must establish those facts before dependent development work is accepted.

## Decisions fixed by the owner

- One user and one active project, host-native on this Linux development workstation.
- Desktop Chromium, Brave and Edge, at a 1920 × 1080 CSS-pixel acceptance viewport.
- Relevant active-project content may be inspected through agent tools and sent to cloud reasoning. Other projects and credentials are excluded.
- Routine in-scope authoring, local checkpoints, configured generation and asset acquisition run automatically. Licence text is not a permission gate.
- Consequential external actions use the explicit permission matrix. Creative stages progress autonomously within the requested task. Shell stays available. The generation budget applies to the adapter, not to a network filter.
- Queue, Steer and Stop are explicit request types. Stop is a runtime control, holds subsequent requests, and leaves external-job cancellation separate.
- Generation attempts are configurable, with a default of three per target and human-originated generation task.
- Git/Git LFS source history, immutable published identities, three exclusive review modes and progressive silent animation review remain the V1 baseline.

## Ready to delegate versus ready to release

The documentation pack supplies a development contract. F00 supplies the tested compatibility lock, generated bindings and offline harness needed by implementation agents. M14 supplies cumulative product acceptance. Image-to-3D remains subject to E04 and an explicit owner scope decision if it cannot be delivered.
