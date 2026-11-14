# Proscenium

A personal, desktop-browser studio for directing AI-authored Blender assets, scenes, storyboards and silent cinematics, with playable Unity scenes as a downstream output.

**Status: specification baseline.** V1 requirements, architecture, normative contract 1.0, acceptance fixtures and a development-fleet handover are documented. Application implementation has not started, and there is no runnable studio or installation procedure yet.

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

## Planned V1 experience

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

Image-to-3D is planned but **not yet proven** on the target workstation. It has an early feasibility proof and a dedicated implementation milestone.

## Architecture boundary

**Copilot and its tools own Blender/Unity authoring. Proscenium owns the interaction, published-output catalogue and review presentation.**

The planned application uses:

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

Start with the [F00 foundation gate](docs/development-fleet.md#f00-foundation-and-compatibility-gate): prove SDK/tool scope compatibility, generate the shared bindings and establish the offline harness. Then demonstrate M01 with the existing Blender tooling. The remaining milestones build out the full V1 experience against accepted contracts.

## Validate the specification

Using an existing Python environment with the [documentation dependencies](docs/requirements-validation.txt):

```bash
python3 -B docs/validate_spec.py
```

This checks schemas/examples, API references, document links, preserved design hashes and numerical fixture vectors. It does not run application/native/model tests. Details are in the [acceptance pack](docs/acceptance/README.md#documentation-validation).

## Local configuration

`.env`, `.envrc` and `.direnv/` remain local and ignored by Git. Do not commit credentials or copy secret configuration from the reference projects. Installation and run commands will be documented when the first working application is available.
