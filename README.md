# Proscenium

A personal, desktop-browser studio for directing AI-authored Blender assets, scenes, storyboards and silent cinematics, with playable Unity scenes as a downstream output.

**Status: implemented, cumulative acceptance in progress.** The host-native studio runs against specification 3.2.0 and contract 1.0. The first real browser → Copilot → Blender → rendered-feedback journey has passed. Image/VLM/mesh execution and remaining cumulative journeys are tracked in [implementation progress](docs/implementation/progress.md).

## Run

```bash
make bootstrap
make run CONFIG=/absolute/path/to/private/operator.json ARGS=--pair
```

Start the private configuration from [`config.example.json`](config.example.json). Configure the installed native tools and independent model services for this workstation. Consume the printed pairing URL in the trusted browser. See [operator instructions](docs/operator.md) for startup, SSH access, Resume and backup/restore, and [development instructions](docs/development.md) for checks.

## Start here

| Document | Purpose |
| --- | --- |
| [Specification index](docs/specification.md) | Reading order, precedence, contract revision and confirmed owner decisions. |
| [Requirements](docs/requirements.md) | Agreed V1 scope, ownership boundaries, functional requirements and exclusions. |
| [Architecture](docs/architecture.md) | Components, integration contracts, source versions, progressive review, persistence and reusable tooling. |
| [User journeys and milestones](docs/user-journeys.md) | Complete implementation plan, dependencies, acceptance criteria and failure/recovery checks. |
| [Owner policy](docs/owner-policy.md) | Active-project privacy, autonomy, automatic operations, confirmations and generation budgets. |
| [Contracts](docs/contracts/README.md) | JSON Schema, OpenAPI, events, examples and semantic invariants. |
| [Execution and recovery](docs/execution.md) | Queue/Steer/Stop, session ownership, lost acknowledgements and explicit Resume. |
| [Development fleet handover](docs/development-fleet.md) | Foundation gate, work packages, file ownership and integration criteria. |
| [Acceptance pack](docs/acceptance/README.md) | Named fixtures, fault scenarios, browser matrix and documentation validation. |

## V1 scope

- One user, multiple saved projects and one active project at a time.
- A persistent project-wide Copilot discussion with immutable Queue/Steer context. Stop is a native cancellation control and holds subsequent requests until explicit Resume.
- Autonomous in-scope production, with relevant active-project content available to Copilot. Other projects and credentials stay excluded. Consequential operations require the confirmed owner authorisation.
- Configurable image/3D-generation budgets, defaulting to three attempts per target/task.
- A single central viewer toggled between **3D Inspection**, rendered **Keyframes** and rendered **Animation**. Completed frames and playable ranges become reviewable before an animation finishes rendering.
- Story planning, reference research, local/online asset discovery and ComfyUI-generated concept images.
- Requested local VLM critique, with user-approved findings and relevant images returned to Copilot.
- Silent cinematic assembly and a native Unity specialist producing playable scenes with basic movement, camera control and collision. Playtesting stays in Unity.
- Canonical source filenames with Git/Git LFS history and pinned asset dependencies.
- Browser disconnections leave work running. Backend/workstation restarts require explicit Resume.

Image-to-3D has a [real Hunyuan hardware proof](docs/evidence/w09/live-b/README.md): one image-conditioned mesh was imported into Blender and published. The generated-asset inspection/revision journey remains open for M10 acceptance. The earlier memory-blocked allocation is retained in the evidence.

## Architecture boundary

**Copilot and its tools own Blender/Unity authoring. Proscenium owns the interaction, published-output catalogue and review presentation.**

The application uses:

| Component | Role |
| --- | --- |
| React, TypeScript and Vite | Desktop workspace using the mockup's Linear Minimal styling. |
| Three.js / React Three Fiber | Static 3D proxy inspection and object selection. |
| FastAPI and SQLite | Project/session association, UI context, public events, publications, preview jobs and saved feedback. |
| GitHub Copilot Python SDK | Cloud frontier lead, native subagents and configured CLI/MCP tools. |
| Blender MCP and CLI tools | Scene authoring, export and isolated rendering of completed, version-pinned sources. |
| Independent LM Studio / ComfyUI services | Configurable local VLM inference and generation workflows. |
| Unity CLI and Editor APIs | Import, scene setup, player controls and validation through a Copilot specialist. |

V1 runs **host-native on this Linux workstation**, with remote browser access through SSH. The UI targets a **1920 × 1080 desktop viewport** in Chromium, Brave and Edge. Mobile layouts, multi-user collaboration, public hosting and distributed execution are outside V1.

The project consolidates lessons from `smal-ai-blender`, `smal-ai-game` and `smal`. Their reusable components and adaptations are recorded in the [reference inventory](docs/architecture.md#15-reference-implementation-inventory), with [committed source pins](docs/reference-pins.json). The approved [Linear Minimal mockup](docs/design/README.md) is preserved in this repository. These are research/design sources, not runtime dependencies.

## First implementation milestone

[M01: First Blender authoring loop](docs/user-journeys.md#m01-first-blender-authoring-loop) delivers:

**Prompt → Copilot/Blender → Git checkpoint → published render → annotation → follow-up.**

The [real M01 evidence](docs/evidence/m01/README.md) records the source correction, two rendered versions, clean/marked image delivery, retained annotation and idempotent publication replay. The [foundation handover](docs/implementation/foundation.md) records the pinned runtime, shared interfaces and startup checks. Remaining milestones require their own cumulative acceptance.

## Validate the specification

Using an existing Python environment with the [documentation dependencies](docs/requirements-validation.txt):

```bash
python3 -B docs/validate_spec.py
```

This checks schemas/examples, API references, document links, preserved design hashes and numerical fixture vectors. It does not run application/native/model tests. Details are in the [acceptance pack](docs/acceptance/README.md#documentation-validation).

## Local configuration

`.env`, `.envrc` and `.direnv/` remain local and ignored by Git. Do not commit credentials or copy secret configuration from the reference projects. Keep operator configuration, runtime stores and generated media outside source control.
