# Runtime specification

**Specification:** 3.2.0. **Contract:** 1.0.0. **Wire:** `schema_version: "1.0"`.

The local-runtime branch imports the application from accepted revision
[`6b714c0`](https://github.com/smal-ai/proscenium/commit/6b714c0dd574abe3995cf7020b988190336cefdf).
The [complete specification and accepted journeys](https://github.com/smal-ai/proscenium/blob/6b714c0dd574abe3995cf7020b988190336cefdf/docs/specification.md)
remain the product baseline. This branch extraction changes packaging and documentation.

## Maintained interfaces

| File | Authority |
| --- | --- |
| [Owner policy](owner-policy.md) | Active-project scope, caller separation, generation budgets and explicit controls. |
| [JSON Schema](contracts/schema.json) | Closed wire records and native tool envelopes. |
| [OpenAPI](contracts/openapi.yaml) | HTTP operations and shared records. |
| [State transitions](contracts/state-transitions.json) | Work/job transitions and guards. |
| [Operator guide](operator.md) | Local startup, configuration, SSH and recovery. |

The backend's packaged schema/transitions and frontend bindings are generated from
these contract inputs. Use `make generate` and `make check-generated` to keep them
consistent. Do not add competing hand-written wire shapes or silently change the
contract revision.

## Runtime invariants

- One user and one active project on a Linux workstation.
- A persistent Copilot session with immutable per-request context and explicit Queue, Steer, Stop and Resume.
- Native tools own Blender/Unity authoring. The application owns catalogue, persistence, preview jobs and review presentation.
- Browser disconnection does not cancel work. Backend restart holds execution until explicit Resume.
- Git/Git LFS retain source history. Publications and jobs pin immutable source, dependencies and media identities.
- The viewer provides static inspection, keyframes and progressive animation with exact annotation targets.
- Configured ComfyUI and LM Studio endpoints remain independent. Generation uses the shared task/target budget.
- Technical completion, model-quality findings and owner creative selection remain separate records.

`make check` verifies the extracted checkout's build and cold-start integration.
The original native/browser acceptance records are available in the
[release archive](https://github.com/smal-ai/proscenium/tree/impl/studio/docs/evidence/release).
