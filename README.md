# Proscenium

A personal, desktop-browser studio for directing AI-authored Blender assets, scenes, storyboards and silent cinematics, with playable Unity scenes as a downstream output.

**Status: planning baseline.** The V1 requirements, architecture and 14 journey-led milestones are documented. Application implementation has not started, and there is no runnable studio or installation procedure yet.

## Start here

| Document | Purpose |
| --- | --- |
| [Requirements](docs/requirements.md) | Agreed V1 scope, ownership boundaries, functional requirements and exclusions. |
| [Architecture](docs/architecture.md) | Components, integration contracts, source versions, progressive review, persistence and reusable tooling. |
| [User journeys and milestones](docs/user-journeys.md) | Complete implementation plan, dependencies, acceptance criteria and failure/recovery checks. |

## Planned V1 experience

- One user, multiple saved projects and one active project at a time.
- A persistent project-wide Copilot discussion, with selected items, versions, images and annotations captured for each message. Send queues work, while Steer corrects the current turn.
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

V1 runs **host-native on one workstation**, with remote browser access through SSH. The UI targets a **1920 × 1080 desktop viewport**. Mobile layouts, multi-user collaboration, public hosting and distributed execution are outside V1.

The project consolidates lessons from `smal-ai-blender`, `smal-ai-game` and `smal`. Their reusable components and required adaptations are recorded in the [reference implementation inventory](docs/architecture.md#15-reference-implementation-inventory). They are research sources, not intended runtime dependencies.

## First implementation milestone

[M01: First Blender authoring loop](docs/user-journeys.md#m01-first-blender-authoring-loop) delivers:

**Prompt → Copilot/Blender → Git checkpoint → published render → annotation → follow-up.**

Start by proving Copilot SDK compatibility and the minimal source/publication/context contracts, then demonstrate that loop with the existing Blender tooling. The remaining milestones build out the full V1 experience.

## Local configuration

`.env`, `.envrc` and `.direnv/` remain local and ignored by Git. Do not commit credentials or copy secret configuration from the reference projects. Installation and run commands will be documented when the first working application is available.
