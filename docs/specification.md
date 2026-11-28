# Runtime specification

**Specification:** 3.2.0. **Contract:** 1.0.0. **Wire:** `schema_version: "1.0"`.

The local-runtime branch originated from accepted revision
[`6b714c0`](https://github.com/smal-ai/proscenium/commit/6b714c0dd574abe3995cf7020b988190336cefdf),
whose [historical specification and accepted journeys](https://github.com/smal-ai/proscenium/blob/6b714c0dd574abe3995cf7020b988190336cefdf/docs/specification.md)
remain the baseline archive. Additive contract `1.0` extensions are documented in the
files below.

## Maintained interfaces

| File | Authority |
| --- | --- |
| [Owner policy](owner-policy.md) | Active-project scope, caller separation, generation budgets and explicit controls. |
| [Studio guide](studio.md) | Studio lexicon, layouts, request targeting, Composer controls and recovery UX. |
| [Contract overview](contracts/README.md) | Additive 1.0 extensions, compatibility and binding regeneration. |
| [JSON Schema](contracts/schema.json) | Closed wire records and native tool envelopes. |
| [OpenAPI](contracts/openapi.yaml) | HTTP operations and shared records. |
| [State transitions](contracts/state-transitions.json) | Work/job transitions and guards. |
| [Operator guide](operator.md) | Local startup, configuration, recipes, SSH and recovery. |

The backend's packaged schema/transitions and frontend bindings are generated from
these contract inputs. Use `make generate` and `make check-generated` to keep them
consistent. Do not add competing hand-written wire shapes or silently change the
contract revision.

## Runtime invariants

- One user and one active project on a Linux workstation.
- One persistent Copilot session per project with immutable per-request context (`request_target`, `reference_versions`, `execution_scope`, media targets and annotations) and explicit Queue, Steer, Stop and Resume.
- Request targeting decouples creative intent (`project`, pinned `item`, storyboard `shot` or `new_item`) from the current Review workspace selection without widening active-project scope or resetting task generation budgets.
- Native tools own Blender/Unity authoring, while host tools provide read-only capability discovery (`get_project_capabilities`), non-materialising schema/semantic validation (`validate_authored_document`) and deterministic local workflow preparation (`prepare_image_generation`).
- Browser disconnection does not cancel work. Backend restart and unsolicited native aborts hold execution until explicit Resume.
- Git/Git LFS retain source history. Publications and jobs pin immutable source, dependencies and media identities. Storyboard publications require canonical `AuthoredShotPlan` JSON entry points.
- Execution completion, retained-artefact availability, catalogue publication, VLM findings and owner creative selection remain distinct records; unpublished retained outputs remain accessible through authenticated artefact routes.
- Configured ComfyUI and LM Studio endpoints remain independent. Generation uses the shared `(project_id, generation_task_id, target_id)` budget ledger.

`make check` verifies the checkout's build, contracts and offline domain/runtime suites;
`make test-browser` runs the hermetic Playwright browser suite. Historical native
acceptance archives remain on
[`impl/studio`](https://github.com/smal-ai/proscenium/tree/impl/studio/docs/evidence/release).

## Proposals under review

- [Published relationships, the Outliner and reusable scene inputs](design/version-relationships.md) is the agreed design, not part of the current runtime contract.
- [Implementation plan: published relationships, Outliner and reusable scene inputs](design/version-relationships-plan.md) sequences the agreed design. It does not change the current runtime contract.
