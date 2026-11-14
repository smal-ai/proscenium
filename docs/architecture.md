# Proscenium: system architecture

**Version:** 3.0.0

**Updated:** 2026-11-14

**Status:** V1 design and normative contract 1.0 baseline. Compatibility proofs and application implementation remain outstanding.

See the [specification index](specification.md) for precedence, [requirements](requirements.md) for scope and [user journeys](user-journeys.md) for cumulative acceptance. [Contracts](contracts/README.md), [execution](execution.md), [source publication](source-publication.md), [review/media](review-media.md) and [owner policy](owner-policy.md) define the implementation hand-offs.

## 1. Architectural decisions

| Decision | V1 choice |
| --- | --- |
| Product | Personal desktop-browser studio, one active project at a time. |
| UI | 1920 × 1080 reference viewport, Linear Minimal styling, one central viewer with mode toggles. |
| Execution | Host-native FastAPI/frontend on one workstation. Remote browser access uses SSH. |
| LLM | Cloud frontier lead through GitHub Copilot Python SDK, with native Copilot subagents. |
| Agent hierarchy | Copilot decides delegation. Explicit agent profiles can select cheaper worker models. |
| Authoring | Copilot and its CLI/MCP tools read/write Blender and Unity. |
| Application boundary | Conversation, UI context, publication/catalogue, review persistence and presentation rendering. No Blender authoring gatekeeper. |
| Blender | Existing desktop MCP integration, virtual display when necessary, isolated workers for preview rendering. |
| Local AI | Independently operated LM Studio and ComfyUI endpoints. Separate interfaces for inference and workflow execution. |
| Versions | Canonical working filenames, Git/Git LFS source history, pinned dependencies and immutable published-media identities. |
| Review | Static 3D inspection, material-capable rendered keyframes and progressively available draft animation. |
| Critique | Requested by user or Copilot. User-approved findings and relevant images are sent to the lead. |
| Asset search | Text/files and configured online MCPs. No vector database. |
| Storyboards | V1 capability, including research, concept images, shot planning and Blender camera/timeline bindings. |
| Cinematics | Silent in V1. |
| Unity | Native specialist subagent delivers a playable scene with basic controls. Playtesting stays in Unity. |
| Recovery | Browser disconnect does not cancel work. Backend/workstation restart requires explicit Resume. |
| Control requests | Queue, Steer and Stop. Stop invokes runtime cancellation and holds queued requests. External-job cancellation is separate. |
| Autonomy | Progress through requested creative stages automatically, with the confirmed permission policy and configurable three-attempt generation default. |
| Content scope | Relevant active-project content may reach cloud reasoning. No cross-project or credential access. |
| Browsers | Chromium, Brave and Edge on the Linux development/reference workstation. |

These decisions replace the original headless-only daemon, application-owned transaction wrapper, local Builder model, LangGraph/AutoGen layer, Qdrant index and seamless cinematic-to-gameplay requirement.

## 2. System boundary and topology

```text
Desktop browser, local or SSH-forwarded
  React workspace / conversation / catalogue / review
                     |
          HTTP commands, media and WebSocket events
                     |
Proscenium FastAPI on the execution workstation
  project/session association, per-turn context, event journal
  publications, review data, preview jobs and media serving
        |                                 |
        | Copilot Python SDK              | explicit preview request
        v                                 v
Copilot runtime                       Isolated render/encode workers
  frontier lead                       pinned completed sources
  native subagents                    no source-file saves
        |
        | configured tools, MCPs and CLIs
        v
  Blender desktop / virtual display
  Blender source and export tools
  Unity CLI and C# Editor tools
  local asset search / online asset MCPs
  LM Studio VLM / ComfyUI generation workflows
        |
        v
  project files and Git/Git LFS checkpoints
        |
        +---- explicit publish_outputs ----> Proscenium catalogue
```

The model inference is cloud-hosted. The Copilot runtime and tool execution are local in V1. Local services may later move to other endpoints, but cross-host filesystem transfer, remote application sessions and distributed workers are not V1 implementations.

### Responsibility limits

- FastAPI does not turn a prompt into `bpy`, manage scene transactions, resolve creative dependencies or decide whether a Blender change is artistically acceptable.
- Copilot coordinates its subagents and application tooling. Safeguards against unsaved-file loss and ambiguous tool retries belong in those tools and agent instructions.
- Proscenium checks that publication metadata and files can be represented safely. Rejecting a malformed file reference is not approval/rejection of the agent's scene-editing plan.
- Proscenium may execute a declared preview render against a completed source snapshot. It uses the supplied camera/profile and never saves those temporary rendering overrides back into the authored file.
- A VLM finding is advice until the user submits it. It is not an executable repair in the UI.

## 3. Technology and integration baseline

| Layer | Choice and qualification |
| --- | --- |
| Frontend | React + TypeScript + Vite, initially retaining React Three Fiber/Three.js and Zustand from the proposed stack. Pin compatible versions during implementation. |
| Markup | Canvas overlay with retained vector/pin/mask records. Choose direct Canvas or Konva after testing the actual annotation workload. |
| Backend | FastAPI, AsyncIO and Pydantic contracts. Python version must be compatible with the pinned Copilot SDK. |
| Persistent application records | SQLite. No separate database server or vector store. |
| Agent runtime | GitHub Copilot Python SDK and its supported local runtime. Pin SDK/runtime together after the integration proof. |
| Conversation transport | HTTP for commands, WebSocket for public SDK/application events with durable replay cursors. |
| Media transport | Same-origin HTTP with byte ranges for video and immutable artefact URLs. |
| Source control | Native Git and Git LFS per project workspace. No remote push is implied. |
| Blender | Reference baseline is Blender 4.5.14 LTS with `mcp-for-blender==2.0.0`, add-on bridge at `127.0.0.1:9876`. Verify the installed versions before integration. |
| Unity | Reference baseline is Unity 6000.3.27f1/URP and the existing Python CLI plus C# Editor methods. Do not copy its game-specific scene assumptions. |
| VLM | LM Studio, configured endpoint initially `http://localhost:1234`, configured model and supported API. |
| Image generation | Independent ComfyUI endpoint, historically `http://127.0.0.1:8188`. Existing FLUX.2 Klein tooling is the starting reference. |
| Image-to-3D | Unproven. Select the workflow only after a real Radeon/ROCm, licence and import proof. |
| Video assembly | Existing Blender frame receipts plus FFmpeg probing, encoding and assembly patterns. |

Installed versions in the reference repositories are evidence about those workflows, not a declaration that all Proscenium dependencies or GPU modes are currently working.

## 4. Copilot session integration

### 4.1 Session and model policy

FastAPI associates the active project with its native Copilot session ID. It creates or resumes that session rather than reconstructing an agent conversation from the UI's text log.

Configuration supplies:

- The frontier lead model, selected from the authenticated runtime's available models.
- Native subagent definitions with clear descriptions, instructions, tool access and optional model/reasoning overrides.
- CLI/MCP connections and reusable skills adapted from the reference projects.
- Explicit SDK permission handling and project working directory.
- Public event subscriptions and supported usage reporting.

The lead decides when to delegate and progresses autonomously through the requested stages. Economical worker profiles provide model choices for suitable work. Generation attempts use the shared task/target budget, default three. No assumption is made that default delegation automatically chooses the cheapest model or reduces total cost.

Routine in-scope work is automatically permitted. Consequential operations and actual project-content boundaries follow [owner policy](owner-policy.md). Native user-input/permission callbacks become durable decision cards; an agent cannot manufacture owner approval. The interface exposes public tool access in addition to the initially attached UI context.

The application does not need experimental fleet orchestration to implement the initial native-subagent workflow. Any experimental SDK/RPC surface introduced later needs an explicit compatibility test against the pinned versions.

### 4.2 Messages and context snapshots

For Queue or Steer:

1. Capture the user's text and UI context, including exact media and source versions.
2. Save an immutable request ID and the attachment/context records before forwarding.
3. Persist ordinary Queue requests in the application's delivery FIFO and submit one eligible root request with native `enqueue`. Use native `immediate` for an admitted Steer. This delivery queue does not plan agent work.
4. Record the runtime acknowledgement/message ID separately from execution and completion.
5. Append public runtime/application events and stream them to connected browsers.

The saved UI snapshot is authoritative for that message. Later navigation does not change it. A queued request concerning an older source remains a request about that source. Copilot can inspect the difference when it runs, but the application must not silently retarget it.

Steering is best-effort at a model boundary. Stop is a first-class control request which bypasses the delivery queue, calls native cancellation and holds subsequent requests until explicit Resume. It is never a model text prompt. Already-admitted external jobs retain separate state and cancellation controls. The exact races, acknowledgements and transitions are in [execution](execution.md).

### 4.3 Reconnectable event stream

Persist a public event envelope containing an application event sequence, project/session/request references, runtime event identity where supplied, agent/tool correlation and payload. The browser reconnects from its last received sequence and deduplicates replayed events.

Preserve distinctions between root-agent conversation, subagent activity, tool outcomes, publication and render progress. Do not expose private model reasoning or credentials as activity output. Large logs/media remain files referenced by records, not repeated blobs in every event.

Copilot's native session store owns resumable inference history. SQLite owns the application event/read model and per-turn UI context. The event journal is not a second inference engine.

## 5. Contracts and identities

The following overview is implemented as a normative specification in [contract 1.0](contracts/README.md), including closed JSON schemas, HTTP operations, events and examples. These are not application implementations. F00 owns generated bindings and compatibility proofs; later schema changes follow the specification change policy rather than independent agent inventions.

| Record | Essential fields and invariants |
| --- | --- |
| Project | Stable ID, chosen dedicated working-tree root, repository identity, project-relative Unity location, native session ID and active state. No inherited creative brief. |
| Item | Stable project-scoped ID, kind, descriptive metadata and current explicitly published version ID. Kinds include asset, scene, storyboard and sequence. |
| Source reference | Repository ID, full commit SHA, canonical path, actual file SHA-256 and explicitly pinned dependency references. A Git LFS pointer is not the file content. |
| Published version | Immutable version ID, item ID, source/dependency closure, creation/request attribution, available artefacts and preview requests. |
| Publication request | Schema version, immutable publication ID, project/request context and one or more completed item versions. Same ID plus same payload is retry-safe. |
| Artefact | Stable artefact ID, kind, local managed location, hash, dimensions and media metadata as applicable. URLs resolve by ID, not arbitrary supplied paths. |
| UI context | Target item/version, selected mode, camera/frame/range, selection IDs, reference/media IDs and immutable annotation/finding snapshots captured for Queue/Steer. Stop identifies its active work instead. |
| Annotation | Immutable target reference, actual displayed frame/media hash, vector/pin/mask data, image dimensions/coordinates, optional valid spatial hits, text and save revision. |
| Render job | Job ID, pinned source closure, profile/engine/device, camera, sample or inclusive frame range, rational frame rate, expected outputs, process identity and frame receipts. |
| Preview segment | Job/shot ID, inclusive source-frame interval, frame count, frame-to-video-time mapping and encoded artefact hash. |
| Shot plan | Stable shot/sequence IDs, intent, composition, assets/references, ordered storyboard frames, scene/camera bindings, motion, timing and unresolved inputs. |
| Generation job | Exact graph/prompt/model/seed/settings, input hashes/roles, endpoint/job IDs, execution status and output/derivative records. |
| Review finding | Exact source/media target, evidence, observation versus telemetry, recommendation and separate agent/user review states. |
| Unity result | Pinned Blender inputs, Unity project revision, Editor/pipeline, saved scene/controller locations and validation results. |

### Identity rules

- A file path locates content. It does not identify a version on its own.
- An item can have several published versions without several canonical working filenames.
- A source commit without its dependency closure is insufficient for a reproducible linked Blender scene.
- An annotation can target a render job/frame before a complete MP4 exists.
- An encoding, crop, upscale or editorial change creates a derived artefact with an explicit input/transformation record.
- Execution status, review status, publication state and owner selection must remain separate fields.
- Unknown hashes, costs, exit codes and selections remain unknown. Do not fill them with invented success values.
- Record timestamps in ISO 8601 with an explicit UTC offset. Display user-facing times in SAST (UTC+2), using the 24-hour clock.

## 6. Source versions and explicit publication

### 6.1 Git/Git LFS working model

Each project keeps canonical files such as `assets/food-cart/asset.blend` and `scenes/alley/scene.blend`. Copilot creates local checkpoints for completed authored changes, retaining the plans, dependencies and applicable source/licence metadata.

On publication, record the source commit and hashes of the files actually used. Assets excluded by licence require a permitted private/local storage reference and backup policy rather than an accidental Git upload. Source commits do not authorise remote pushes.

A scene published against food-cart version A continues to use A after food-cart version B is created. Copilot chooses how the Blender source preserves that binding: appended/packed content or explicitly pinned linked dependencies. Proscenium never follows a mutable asset path and declares it equivalent to the recorded dependency.

For previews of historical or still-rendering versions, materialise the declared source/dependency closure into an isolated ignored cache or equivalent read-only job workspace. Hydrate and verify the referenced LFS content. This is runtime materialisation of a revision, not a new numbered authored source or a checkout over the artist's live working tree.

[Source resolution](source-publication.md) specifies destination-path binding, cross-commit dependencies, local-only retained blobs, collision rejection, exact hydration and clean-workspace reopening. A hash list alone does not cause Blender to use the correct linked bytes.

### 6.2 Publication protocol

1. Copilot saves and checks the requested source outputs through its tools.
2. Copilot creates the required source checkpoint and calls `publish_outputs` for completed items.
3. Proscenium validates the project association, schema, version/hash references and representable file locations.
4. It atomically records completed source items, compare-and-swap head updates, preview-job intents and durable events.
5. It exposes available artefacts and schedules only the explicitly declared presentation renders/encodes.
6. It emits catalogue/preview events without waiting for the entire Copilot turn to end.

The publication tool must be available to the appropriate lead/subagent profiles, with request/project attribution derived from the session integration rather than trusted solely from model-supplied text.

A completed source may have a queued, partial or failed preview. The UI shows those separate states and keeps the previous available media accessible. A publication retry reuses its original ID. A changed payload requires a new publication identity.

Every item in one publication batch is source-complete. Unfinished source items use later publications. A pending preview is permitted. Expected-head comparisons prevent late publications from replacing newer heads, and persisted preview intents survive the commit-to-worker-launch failure window. See the [publication protocol](source-publication.md#4-publication-transaction).

The current-published pointer is application metadata. It is not a declaration of creative approval and does not change any Blender dependency.

## 7. Blender tooling and preview jobs

### 7.1 Live authoring

Reuse the pinned MCP bridge and desktop/virtual-display launcher patterns. Agent skills instruct Copilot to inspect the active file and dirty state, preserve unsaved work and reconcile uncertain edits before retrying.

The existing virtual-display reference starts a real Blender UI under Xvfb because the MCP add-on needs its event loop. This does not imply that every GPU render mode is supported by that display setup. Verify viewport capture and the selected renderer/device separately.

There is no new Proscenium authoring daemon or transaction-manager class. Arbitrary agent Python and shell commands have their tool/process permissions. An `exec` namespace or working directory is not a sandbox. The project-only access policy therefore requires a proven execution boundary for each enabled code path; an unproven path remains unavailable for scoped automatic operation. Authoring writers and Git checkpoints use the tool-owned coordination policy in [execution](execution.md#2-process-and-resource-ownership).

### 7.2 Presentation rendering

Copilot supplies a render specification for a completed source version. A trusted reusable renderer reads that specification and uses an isolated Blender process. Proscenium may start and monitor this presentation job without making scene-authoring decisions.

The specification includes:

- Pinned source/dependency closure and required Blender build/features.
- Shot/camera and frame(s), including an explicit camera override for Render this view if requested.
- Purpose/profile, engine/device, dimensions, samples, colour management and frame rate.
- Output/artefact locations and immutable job identity.

Use a fixed renderer entry point and validated job data rather than treating an arbitrary publication shell string as a preview command. Temporary camera/render overrides stay in worker memory. Workers never save over source `.blend` files.

Reuse input fingerprints, process identity, per-frame hashes, writer locks and atomic receipts from the reference renderer. Locks protect the worker's own output files, not a new global authoring queue. GPU coordination remains in the tooling and independent service operation.

### 7.3 Render profiles

| Purpose | Contract |
| --- | --- |
| Spatial proxy | Static `.glb`, object-ID mapping, source revision, sample frame and camera metadata. Not final lighting evidence. |
| Keyframe | Actual Blender render suitable for material/lighting review, with recorded engine and settings. |
| Draft animation | Reduced resolution/samples or a suitable Blender preview engine, retaining the requested motion and native timing. |
| Final delivery | Explicitly selected production settings and source inputs. Not inferred from an approved draft. |

Workbench is suitable for geometric/motion checks but must be labelled accordingly. The existing production notes record material artefacts that were misleading in Workbench. Do not approve lighting or change materials based only on that evidence.

Cycles HIP discovery is not a benchmark. CPU rendering is a valid fallback when chosen by the tools/brief and reported accurately.

## 8. Progressive animation review

The application must not wait for a whole-movie hash to offer review.

1. Register the source version and expected shot/frame map when the render starts.
2. The worker writes each completed frame and then its verified receipt. Do not expose partially written images.
3. Proscenium makes completed frames available for still inspection and markup immediately.
4. The preview encoder publishes short, independently decodable segments from completed contiguous ranges. Each records its exact source-frame interval and artefact hash.
5. The timeline displays available, pending and failed intervals separately. Playback waits at an unavailable interval rather than pretending it is rendered or silently skipping it.
6. Later receipts extend availability. They do not seek the viewer, replace its selected target or clear annotations.
7. Final assembly produces a new complete artefact with a mapping to the same source frames and preview lineage.

Segment length is an implementation parameter to choose by measuring time to first playable output, encoder overhead and playback behaviour. Do not impose an untested latency claim.

The initial segment profile is silent H.264/MP4 with exact rational-FPS mappings and independently decodable segment starts. [Review/media](review-media.md#4-progressive-animation) defines boundary, buffering, missing-range and annotation behaviour. E03 verifies it in Chromium, Brave and Edge.

Before frames exist, Animation shows Rendering and actual progress. It may keep the previous version accessible, but must label that version explicitly rather than display it as the new render.

Review records bind to source version + render job + shot/frame/range + displayed frame/segment hash. A later source revision or changed frame does not overwrite that identity. Render failure leaves already published frames reviewable with an incomplete/failed status.

## 9. Desktop workspace and annotation

### 9.1 Visual baseline

The owner's PNG and original HTML are preserved byte-for-byte under [docs/design](design/README.md), with hashes and a fixed-desktop layout specification. Use only the Linear Minimal treatment. The archived HTML is a visual research artefact, not production code or a request for four themes.

Preserve these selected design tokens so the styling decision is not dependent on the Downloads files remaining in place:

| Token group | Linear Minimal values |
| --- | --- |
| Surfaces | App/header `#09090b`, panel `#101013`, elevated `#18181c`, input `#141418`, viewport `#0c0c0f`. |
| Borders | Hairline `#222228`, stronger boundary `#32323a`. |
| Text | Main `#fafafa`, secondary `#a1a1aa`, muted `#52525b`. Check contrast for actual control sizes. |
| Accents | Monochrome primary `#e4e4e7`, primary background `rgba(255,255,255,0.09)`, restrained indigo `#6366f1`, success `#22c55e`. |
| Shape and type | 8 px control/card radius, Inter interface/display text, JetBrains Mono technical text. |
| Inspection grid | Major `#27272a`, minor `#18181b`. |

| Region | V1 use |
| --- | --- |
| Header | Project/item/version, connection/activity state and relevant project actions. |
| Left panel | Shots/storyboard, scene outliner and asset catalogue/search. |
| Central area | One selected review mode: 3D Inspection, Keyframes or Animation. |
| Central controls | Mode-appropriate camera, annotation, frame/range and render controls. |
| Timeline | Shot/frame navigation and progressive availability when relevant. |
| Right panel | Project discussion, VLM review and native decision cards, with visible context attachments and Queue/Steer/Stop. |

Remove obsolete mockup behaviour: direct UI `bpy` fixes, fake transaction undo/redo, Qdrant labels, automatic Unity HUD/transition verification, hardcoded local Builder models and fabricated telemetry. Historical version actions submit explicit requests to Copilot rather than resetting files.

The page is desktop-only at a 1920 × 1080 reference viewport. Fit and scroll panels without introducing mobile layouts. Bundle the frontend and fonts instead of shipping the mockup's CDN scripts. Review media keeps its own declared aspect ratio and resolution. Keyframes is the initial review mode when rendered stills are available.

Chromium supplies the automated browser baseline; installed Brave and Edge also receive actual acceptance runs on the Linux reference host. The [design](design/README.md) and [acceptance](acceptance/README.md) documents define geometry, target capture and browser evidence.

### 9.2 Static inspection and camera transfer

The `.glb` proxy must preserve stable object identifiers through a sidecar/exported mapping and identify its sample frame. Multiple instances, renamed objects and replacements need explicit identity rules and fixtures.

Render this view captures the inspection camera, including projection type, pose, aspect, clipping and relevant optics. It sends this as a request to Copilot. Copilot determines the corresponding Blender render instructions. Changing the central tab does not invoke that tool.

### 9.3 Coordinate contract

- Use metres for world-space measurements and declare the coordinate convention of every transform.
- Define tested conversion matrices between Blender source space, exported proxy space and Unity space. Preserve normal/vector semantics as well as positions.
- Record camera view/projection matrices and relevant optical settings, including perspective/orthographic mode and shifts where used.
- Store 2D annotation points in image-relative coordinates, with original image dimensions and a top-left origin. Derive them from the media content rectangle, not the entire panel.
- Test letterboxing, viewport scaling, browser device-pixel ratio and camera aspect changes.
- A spatial hit is valid only for the matching proxy version, sample frame and camera. Most animation annotations can remain image-space instructions. Do not project moving objects onto a stale static proxy and report the result as measured geometry.

Starting markup on video freezes/captures the actual displayed frame. Frame numbering is one-based and ranges are inclusive. Preserve native/source-to-sequence mapping and rational frame rates rather than relying on rounded timestamp strings.

[Review/media](review-media.md#3-frame-and-coordinate-convention) fixes matrix storage/multiplication, basis conversions, projection semantics and normalised image coordinates. A range annotation has one drawn anchor frame plus a temporal range; automatic mask tracking is outside V1.

### 9.4 Feedback persistence

Carry forward the reference Review Studio's stable note IDs, explicit targets, browser draft/outbox and optimistic conflict checks. Save acknowledgements must be truthful. A lost acknowledgement must be retry-safe, and a stale edit from another tab must not overwrite newer feedback.

UI actions operate on review metadata. An eraser removes markup or describes a requested removal mask. Moving a region or approving a finding does not move/delete Blender objects directly.

## 10. Storyboards, references and generation

### 10.1 Agent-owned production stages

Retain independently invocable stages:

1. Brief and production research.
2. Narrative/shot plan and reference selection.
3. Concept/storyboard image generation.
4. Blender asset/scene/camera/timeline authoring.
5. Rendered keyframes and draft motion review.
6. Selected silent assembly/delivery.

Copilot progresses autonomously within the user's request and can delegate through native subagents. It pauses for material unknowns, requested checkpoints, consequential permissions or exhausted generation budgets. Proscenium displays plans and artefacts. Merely opening a gallery, changing a selected reference or discovering stale dependencies does not submit a new generation job.

Plans distinguish reusable assets, shots, storyboard frames, scene bindings, reference roles, motion beats, timing and unresolved inputs. Shared style text contains treatment, not a previous project's industrial subjects or cast. Concept images remain design references until actual geometry is authored.

### 10.2 ComfyUI integration

Adapt the existing configured CLI wrapper, graph preflight and reference-conditioned FLUX graph. Persist the exact submitted graph, prompt, references/transformations, seed, model filenames, endpoint and prompt/client IDs.

Separate submission from waiting/downloading. Reconcile a known prompt ID through queue/history after a timeout. An ambiguous submission must not be blindly repeated. Completed unchanged output can be reused after hash verification.

Required changes to the references include:

- Replace hardcoded HTTP URLs in batch runners with the same configured endpoint the CLI uses.
- Remove implicit server start/stop from generation paths. Report an unavailable independent service.
- Remove repository-root, archived path, numbered-plan and project-specific prompt assumptions.
- Use one shared typed plan/job/publication contract instead of treating the empty template as the executable runner schema.
- Replace filesystem latest-take selection with explicit published/input version references.

The tool layer can inspect queue and memory availability and serialise its own writes. It must not clear unrelated jobs, terminate applications or upgrade the shared ROCm environment.

Generation reserves an attempt against the host-bound human task/target before submission. The configurable default is three. Polling, downloading and reconciliation do not consume attempts; ambiguous submissions retain their reservations. The common resource claim and external-queue policy are in [storage/operations](storage-operations.md#5-resource-admission).

### 10.3 Requested VLM review

User or Copilot requests a review of specified images/source versions. A local adapter calls the configured VLM endpoint with its analysis prompt and available telemetry. The model-facing tool returns only a review ticket/status. Findings are stored for the owner UI and are not returned as unapproved repair instructions.

The user can select/amend findings and submit them as a batch. The next Copilot input contains approved findings, the associated images and captured UI context. Findings not approved by the user do not become instructions to repair the scene. The VLM service is not an automatic publication gate.

### 10.4 Image-to-3D

No functioning reference workflow exists. Run an early proof of model/licence compatibility, real Radeon/ROCm execution and Blender import. The integration milestone then turns a successful proof into repeatable CLI/MCP tooling and publication.

A result must be an actual mesh with inspected scale, topology/material availability and source evidence. Do not promise automatic clean retopology, rigging or production-ready materials. If the proof fails, leave the capability visibly unresolved and obtain an explicit scope decision before excluding it from V1.

## 11. Unity specialist

Register a native Copilot specialist with Unity instructions and tool access. It consumes pinned Blender asset/scene versions, performs export/preparation and uses Unity CLI/C# Editor APIs to import, configure and validate a playable scene.

Reuse the tested FBX/export and Editor-method approach before selecting an unverified alternative importer. Adapt the existing player/camera logic to a minimal controller without inheriting SMAL-specific characters, wardrobe, route, HUD or private assets.

The output includes basic movement, camera control, collision/floor support and a safe spawn. Publish the Unity project/scene revision and source-input mapping. The user playtests in Unity Editor Play mode. No cinematic transition, additional game mechanics, Web build or browser player is required.

The Unity workspace must be inside the active project folder. An external location from an older draft is not automatically authorised under the confirmed project-only access policy.

The reference CLI expects the project's Editor closed for batch operations and has some broad Blender-process checks. Do not copy those checks as a studio-wide prohibition on live authoring. Target the actual project/instance, respect unsaved work and report when a batch action needs the user/agent to settle the Editor. Never force-close unrelated instances.

## 12. Storage and runtime lifecycle

### 12.1 Persistence split

| Store | Contents |
| --- | --- |
| Project Git/Git LFS workspace | Briefs, canonical editable assets/scenes, shot plans, source/reference metadata and permitted binaries. |
| Published artefact store/cache | Preview images, frame sequences, segments, proxies, final outputs and retained submitted-image snapshots, addressed by immutable identities. |
| SQLite | Project locations, session association, public event read model, immutable turn context, publication catalogue, review notes/findings and preview-job records. |
| Copilot native storage | Native conversation/session state required by the runtime for resume. |
| External tool/service records | Blender worker receipts, ComfyUI job history and Unity logs/results referenced by their stable IDs. |

The project-local runtime directory is `.proscenium/`, ignored by Git. Project SQLite lives at `.proscenium/state.sqlite3`; the machine's XDG application registry maps project IDs/locations and owns activation. Immutable artefacts, retained local-only sources, job outputs and snapshots have distinct roots. Exact locations and relocation/backup behaviour are in [storage/operations](storage-operations.md).

Generated frame sequences are not automatically committed to LFS. Preserve submitted annotation targets and explicitly selected/published deliverables under a documented retention policy. Runtime SQLite and Copilot session state need backup independently of source Git history. Do not introduce automatic destructive cleanup in V1.

### 12.2 Disconnect and restart

- Browser/SSH-tunnel loss detaches the viewer. FastAPI's accepted work and independently running tool jobs continue.
- A reconnect replays persisted public events, restores saved UI/review state and reads the current output catalogue.
- After backend/workstation restart, the application restores records without sending a new model turn or resubmitting jobs.
- Existing independent workers may have survived. Passive receipt/status inspection is safe, but new work waits for explicit Resume.
- Resume restores the native session and provides recorded request/job identities. Copilot inspects tools, sources and outputs before continuing.
- Preserve uncertainty if a native exit code was lost. Reconciliation may establish output completion without inventing a historical exit code.
- Source checkpoints, publications, feedback saves and message submissions each need their own retry identity. A browser reconnect is not a reason to run them again.

### 12.3 One active project

Persist inactive projects and their native session IDs. Only the active project accepts new work. If it is busy, expose wait, Stop Copilot and supported individual job cancellation. Project switching requires the settled state defined in [execution](execution.md#9-project-switching). Stopping an agent turn does not prove that external jobs stopped. Hold queued requests until an explicit Resume choice releases them.

## 13. Deployment, access and resource policy

- Run FastAPI as a user-level host-native service and serve the compiled frontend from the same origin. Node is build tooling, not a required production frontend server.
- Bind to loopback by default. Use a configured local port through the existing SSH tunnel. Do not replace an unknown listener or the reference projects' live review servers.
- The browser talks only to Proscenium. Copilot tools/backend adapters resolve LM Studio/ComfyUI URLs from the execution workstation, not the user's remote browser.
- Use artefact-ID routes with permitted roots and content checks, HTTP byte ranges and explicit origins/hosts. Do not serve arbitrary workspace paths, `.git`, `.env`, databases or secret configuration.
- Automatically permit routine in-scope operations and require the exact consequential confirmations in [owner policy](owner-policy.md). Keep owner-browser, native-tool and worker capabilities distinct. This is not an application-level scene-authoring gate.
- Relevant active-project images/files can reach cloud reasoning through visible tool access. Automatic attachment capture stays explicit. Credentials, environment files and other projects are excluded through every enabled tool path.
- Discover actual memory, service queues, models, device capabilities and runtime versions. Do not statically allocate the nominal 128 GB or assume historical ROCm/GPU figures still apply.
- Benchmark first-frame availability, proxy load, local generation and render throughput using representative scenes. Report actual resource constraints rather than unsupported universal latency promises.

## 14. Proposed code and workspace layout

This is a target organisation, not a list of existing implementation directories. Create only what a milestone needs.

```text
proscenium/
  docs/
    requirements.md
    architecture.md
    user-journeys.md
    contracts/                Normative schema, API, events and examples
    acceptance/               Named fixtures and fault scenarios
    design/                   Preserved mockup and desktop layout
  apps/
    frontend/                 React desktop workspace and review modes
    backend/                  FastAPI API, sessions, events, catalogue and previews
  agent/
    profiles/                 Native Copilot agent/model/tool profiles
    skills/                   Adapted research, storyboard and application skills
  tools/
    blender/                  Reusable MCP/CLI support and isolated preview workers
    comfy/                    Configured workflow CLI/adapters and graphs
    unity/                    CLI and C# Editor integration support
  tests/
    contracts/
    integration/
    browser/
```

An independent project workspace can contain:

```text
chosen-project/
  project.json                Identity and meaningful project-relative locations
  brief/                      Creative direction and selected treatment
  assets/                     Canonical assets, references and searchable metadata
  scenes/                     Canonical Blender scene files
  storyboards/                Canonical plan and reference-selection records
  sequences/                  Shot order, timing and explicit source bindings
  unity/                      Optional Unity project inside the active workspace
  .proscenium/                Ignored runtime state, source snapshots and media cache
```

These are shared tools operating on an explicit project, not copied per-project scripts or a mandatory nested `projects/` registry. Source names and directories can evolve through the contracts without inheriting the reference projects' production content.

## 15. Reference implementation inventory

These links point to local research sources outside this repository. They are not runtime imports. [reference-pins.json](reference-pins.json) fixes the committed research revisions and selected file hashes. Extraction must use those revisions, not whichever working tree happens to be newest. Adapt useful code/full skill resources with their licences/notices at the relevant milestone.

| Reference | Useful evidence and adaptation |
| --- | --- |
| [Blender bridge configuration](../../smal-ai-blender/opencode.json) and [integration skill](../../smal-ai-blender/.opencode/skills/blender-integration/SKILL.md) | Pinned live connection, dirty-state inspection and uncertain-edit reconciliation. |
| [Blender renderer](../../smal-ai-blender/src/smal_blender/render.py), [worker](../../smal-ai-blender/src/smal_blender/blender_worker.py) and [I/O helpers](../../smal-ai-blender/src/smal_blender/io.py) | Isolated rendering, source closure, per-frame receipts, process identity and verified reuse. |
| [Review Studio](../../smal-ai-blender/docs/review-studio.md), [feedback store](../../smal-ai-blender/src/smal_blender/feedback.py) and [viewer](../../smal-ai-blender/studio/review.js) | Frame/range targets, content-bound notes, outbox/retry/conflict handling and byte-range playback. |
| [Unity CLI](../../smal-ai-game/tools/game.py), [Editor integration](../../smal-ai-game/Assets/Game/Editor/GameBuild.cs), [player](../../smal-ai-game/Assets/Game/Scripts/WalkthroughPlayer.cs) and [camera rig](../../smal-ai-game/Assets/Game/Scripts/PlayerViewRig.cs) | Actual CLI/export/import/validation patterns and tested movement/camera logic. Generalise project-specific checks and dependencies. |
| [ComfyUI tooling](../../smal/tools/comfy/README.md), [CLI wrapper](../../smal/tools/comfy/comfy), [still runner](../../smal/tools/comfy/still_batch.py) and [preflight](../../smal/tools/comfy/check.py) | Configured local execution, actual reference conditioning, recorded prompt IDs and known-job recovery. Remove implicit service lifecycle control. |
| [Storyboard skill](../../smal/.opencode/skills/storyboard-generation/SKILL.md), [image skill](../../smal/.opencode/skills/image-generation/SKILL.md) and [production research](../../smal/.opencode/skills/production-research/SKILL.md) | Stable IDs, reference roles, independent stages, dependency-aware revisions and actual acquisition/rights evidence. |
| [Virtual-display Blender tools](../../smal/tools/blender/README.md) | Existing Xvfb/MCP verification and isolated inspection/render recipes. |
| [Media retention lessons](../../smal/docs/media-retention.md) | Source history is not a complete archive of local-only generated media or feedback. Retain the actual reviewed inputs deliberately. |

Historical verification is documented in the references. This documentation pass did not rerun their applications, generation jobs or test suites.

## 16. External API references and implementation proofs

- [Copilot SDK runtime architecture](https://github.com/github/copilot-sdk/blob/cf045793958ce674259c557f7769788f94ee20c9/README.md#architecture): default local runtime/JSON-RPC relationship at the research pin.
- [Python SDK](https://github.com/github/copilot-sdk/tree/cf045793958ce674259c557f7769788f94ee20c9/python): tools, images, permissions, streaming and session configuration.
- [Native custom agents](https://github.com/github/copilot-sdk/blob/cf045793958ce674259c557f7769788f94ee20c9/docs/features/custom-agents.md): scoped tools, model overrides and delegation.
- [Steering and queueing](https://github.com/github/copilot-sdk/blob/cf045793958ce674259c557f7769788f94ee20c9/docs/features/steering-and-queueing.md): delivery semantics and best-effort steering.
- [Usage reporting](https://github.com/github/copilot-sdk/blob/cf045793958ce674259c557f7769788f94ee20c9/docs/features/usage-and-billing.md): parent/subagent usage and version-sensitive metrics.
- [Session persistence](https://github.com/github/copilot-sdk/blob/cf045793958ce674259c557f7769788f94ee20c9/docs/features/session-persistence.md): native resume contract.
- [Unity Editor CLI](https://docs.unity3d.com/6000.0/Documentation/Manual/EditorCommandLineArguments.html): batch execution, `-executeMethod` and project-instance constraints.

F00 establishes contract bindings, owner/scope proof, delivery/recovery primitives and the offline fixture harness as M01 begins. M01 pins/proves the actual SDK/runtime/API combination. M12 completes cross-provider recovery instead of introducing the foundational protocol late. The [fleet handover](development-fleet.md) defines ownership and acceptance for independent implementation. Image-to-3D retains its early feasibility proof and separate completion milestone.
