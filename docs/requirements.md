# Proscenium: product and technical requirements

**Version:** 3.1.0

**Updated:** 2026-11-14

**Status:** V1 specification with normative contract 1.0 and confirmed owner policy. Application implementation and acceptance remain outstanding.

Start with the [specification index and precedence](specification.md). Related documents include [architecture](architecture.md), [owner policy](owner-policy.md), [contracts](contracts/README.md), [execution](execution.md) and [user journeys](user-journeys.md).

## 1. Product definition

Proscenium is a personal, desktop-browser studio for directing AI-authored Blender assets, scenes, storyboards and silent cinematics, with a playable Unity scene available as an optional per-project downstream output. The Unity capability itself is included in V1.

The user works through a persistent project-wide discussion, selected references and annotations. A cloud frontier LLM runs through the GitHub Copilot Python SDK in FastAPI. Copilot manages its native subagents and operates the existing CLI/MCP toolchain. VLM analysis, image generation and planned image-to-3D generation are local-first capabilities behind separate interfaces.

**Copilot and its tools own Blender authoring. Proscenium owns the interaction, published-output catalogue and review presentation.** Proscenium can render completed, version-pinned Blender sources for previews. It does not interpret creative instructions into scene mutations, coordinate Blender authoring transactions or update scene dependencies itself.

This is a consolidation of the working practices and tooling in `smal-ai-blender`, `smal-ai-game` and `smal`, not a replacement of those tested integrations with a new scene engine.

## 2. V1 scope

### Included

- One user, multiple saved projects and one active project at a time.
- Host-native deployment on one workstation, accessed locally or remotely through the existing SSH setup.
- A desktop-only interface designed for a 1920 × 1080 viewport, using the supplied mockup's Linear Minimal styling.
- Persistent project-wide Copilot discussion with immutable context for Queue/Steer and a distinct Stop control request.
- Autonomous progression through the requested creative stages, subject to the confirmed permission policy and genuinely missing decisions.
- Agent access to relevant active-project content, including images, without requiring manual attachment of every file. No cross-project or credential access.
- Configurable generation budgets with a three-attempt default per target and originating human generation task.
- Native Copilot delegation, with an explicitly configured frontier lead and optional economical worker models.
- Blender desktop authoring through existing MCP/CLI tools, including virtual-display operation for remote sessions.
- A catalogue of individual assets, scenes, storyboards and sequences, not one project-wide Blender file.
- Canonical source filenames with Git/Git LFS history and content-bound published versions.
- One central viewer toggled between static **3D Inspection**, rendered **Keyframes** and rendered **Animation**.
- Annotation of keyframes and available animation frames/ranges, including during an unfinished render.
- Story planning, reference research, storyboard images, Blender camera/timeline authoring and silent cinematic delivery.
- Text/file-based local asset discovery and configured online asset MCPs. BlendSwap is optional. A missing key or endpoint is an unavailable capability, not a failed studio.
- Independent LM Studio and ComfyUI services reached through configurable endpoints.
- On-demand local VLM critique, with user-approved findings and relevant images passed to Copilot.
- A planned image-to-3D integration, subject to an early real-hardware feasibility proof.
- A Copilot Unity specialist delivering a playable scene with basic player controls for playtesting in Unity.
- Work surviving browser disconnection, with explicit Resume after a backend or workstation restart.

### Excluded or superseded

| Area | V1 boundary |
| --- | --- |
| Mobile and responsive layouts | No mobile UI, breakpoint-specific layouts or mobile acceptance matrix. |
| Collaboration | No multiple users, roles, shared editing or concurrent active projects. |
| Public hosting | No public service or account-registration system. Use loopback access and SSH forwarding. |
| Distributed execution | Configurable locations and endpoints prepare for later split-host operation. V1 runs on one execution workstation. |
| Fully offline AI | LLM reasoning requires cloud Copilot access. Existing local outputs remain reviewable without that connection. |
| Additional agent frameworks | No LangGraph, AutoGen or Proscenium-managed agent hierarchy. |
| Application-owned Blender mutations | No custom scene daemon, mandatory authoring transaction queue or application-level `bpy` execution wrapper. |
| Semantic asset database | No Qdrant, embeddings or vector-search requirement. |
| Animated browser scene | The 3D proxy is for static inspection. Motion review uses Blender-rendered media. |
| Gameplay scope | No cinematic-to-gameplay transition, quests, interactions, NPC systems or gameplay beyond basic player controls. |
| Browser playtesting | No Unity Web build or embedded player. |
| Audio | No voice-over, music or sound effects in V1 cinematic deliveries. |
| Diffusion video | Existing H3 tooling is a reference, not the V1 source of Blender animation previews. |

## 3. Ownership and source of truth

| Component | Owns |
| --- | --- |
| User | Creative direction, explicit selections, submitted feedback and approved VLM findings. |
| Copilot lead and native subagents | Planning, delegation, Blender/Unity decisions, tool use, source edits, source checkpoints and recovery decisions. |
| CLI/MCP tooling | Actual application operations, application-specific safeguards, generation jobs and execution evidence. |
| Git/Git LFS project workspace | Versioned authored sources, plans, references and dependency records, subject to asset rights. |
| Proscenium | Project/session association, UI context, event presentation, publication records, current published-version references, preview execution and review persistence. |
| Local VLM service | Requested analysis and findings. It has no scene-writing authority. |
| LM Studio and ComfyUI operators | Service installation, models, runtime configuration and service lifecycle. These services are independent of Proscenium. |

An existing scene keeps its specific asset dependencies. A new asset version does not update that scene. Copilot must make and publish the scene revision that adopts it.

Execution completion, agent assessment, publication and user selection are different facts. A successfully rendered or published item is not automatically user-approved.

## 4. Functional requirements

MUST identifies a V1 requirement. Proposed field names, library choices and implementation mechanisms are specified in the architecture and must be validated during the relevant milestone.

### FR-01: Projects and source history

- **FR-01.1:** The user MUST be able to create/open a chosen project folder and return to it later. V1 MUST permit only one active project at a time and preserve inactive projects. Registration MUST bind a dedicated Git working-tree root and project identity as defined in [source resolution](source-publication.md).
- **FR-01.2:** A project MUST contain independently identified assets, scenes, storyboards and sequences, each with a history and a current explicitly published version.
- **FR-01.3:** Authored sources MUST use canonical working filenames and Git/Git LFS history. Product version history MUST NOT require numbered copies of every working source file.
- **FR-01.4:** Published versions MUST identify the source commit, paths, actual file hashes and required dependency versions. Referenced LFS objects MUST be hydrated before use.
- **FR-01.5:** Copilot MUST decide and author dependency changes. Proscenium MUST NOT automatically update scenes when an asset changes.
- **FR-01.6:** Viewing a previous result MUST NOT reset the working tree or replace unsaved authoring work. A requested source restoration MUST be handled by Copilot as an explicit versioned change.
- **FR-01.7:** Rendering an older version MUST resolve the recorded dependency bytes, including textures and linked sources, into a separate materialisation. Same-destination/different-content collisions and missing retained local-only sources MUST be explicit errors.

### FR-02: Copilot conversation and delegation

- **FR-02.1:** FastAPI MUST integrate the GitHub Copilot Python SDK and maintain one persistent project-wide discussion/session association.
- **FR-02.2:** The initial LLM implementation MUST use cloud models available through the user's Copilot access. The selected lead model and worker model profiles MUST be configurable.
- **FR-02.3:** Copilot MUST manage native subagent delegation. Proscenium MUST expose the configured CLI/MCP tools and display public agent/tool activity without implementing a competing agent loop.
- **FR-02.4:** The application MUST support native subagents with scoped tools and optional explicit economical models. Enabling subagents alone MUST NOT be presented as a guarantee of cheaper execution.
- **FR-02.5:** Queue, Steer and Stop MUST be explicit request types. Ordinary Send creates Queue. Steer targets the active work. Stop bypasses queued messages and invokes native cancellation rather than submitting a text prompt. In-flight external effects are not presumed cancelled.
- **FR-02.6:** Message acceptance, tool execution, item publication and turn completion MUST be shown separately. Retrying a lost acknowledgement MUST NOT silently duplicate an accepted request.
- **FR-02.7:** Public session/subagent events and available usage information MUST be associated with the correct project and task. Missing usage data MUST NOT be represented as zero cost.
- **FR-02.8:** One authoritative application owner MUST control the active native session. Request, work, model-turn, invocation and external-job identities MUST remain distinct. Browser connections do not create separate session owners.
- **FR-02.9:** Native questions and consequential permission requests MUST produce durable user-decision cards. Answers require the actual owner channel, exact operation fingerprint and expected revision.
- **FR-02.10:** Stop MUST hold subsequent Queue requests, record requested versus confirmed cancellation, and preserve independently running jobs as separate resources. Held requests resume only through an explicit Resume choice.

### FR-03: Per-turn context and image inputs

- **FR-03.1:** Each Queue or Steer message MUST capture the selected item/version, review mode, camera/frame/range, selected objects, relevant images, annotations and approved critique at submission time. Stop instead identifies the active work and optional reason.
- **FR-03.2:** The snapshot MUST remain unchanged while the user navigates or the message waits in the queue. Copilot MUST receive the original target rather than the UI's later selection.
- **FR-03.3:** The LLM MUST be able to receive selected original images, annotated images/masks and user-approved VLM findings together. Local VLM text MUST NOT replace the selected visual evidence.
- **FR-03.4:** Image/reference roles, source identities and transformations MUST be recorded. The system MUST distinguish concept images, actual Blender renders and spatial proxies.
- **FR-03.5:** Submitted media and context MUST be retained sufficiently to replay the discussion and interpret its annotations. Proscenium's automatic attachment list MUST contain only selected context. Copilot may independently inspect relevant permitted content in the active project, with visible tool access. Credentials, environment files and other projects are excluded by [owner policy](owner-policy.md).
- **FR-03.6:** Draft annotation targets MUST be pinned when captured, independently of the later current viewport. A Send snapshot MUST preserve each attachment/annotation's own target and actual displayed image.

### FR-04: Explicit output publication

- **FR-04.1:** Copilot MUST have an explicit publication tool, provisionally `publish_outputs`, for declaring one or more completed asset, scene, storyboard, sequence or Unity results.
- **FR-04.2:** Each publication MUST identify the project, stable item ID, immutable version identity, source revision/dependencies and available files or preview requests.
- **FR-04.3:** Completed items MUST appear incrementally while the rest of the user request continues. Preview state MUST be independent of source-publication state.
- **FR-04.4:** Repeating the same publication ID and payload MUST be a no-op. A conflicting reuse MUST be reported rather than silently replacing published content.
- **FR-04.5:** Proscenium MUST check the publication's technical identity and file availability before presenting files as available. An incomplete preview MUST NOT discard the previous available result or block Copilot's unrelated authoring.
- **FR-04.6:** A publication batch MUST contain only completed source versions and register atomically. Pending previews are independent. Each head update MUST compare the expected current version, so late publication cannot silently replace a newer head.
- **FR-04.7:** The publication, head updates, preview-job intents and public events MUST share a durable commit boundary. Recovery after worker-launch acknowledgement loss MUST reconcile the original job before a second launch.

### FR-05: Review modes and arbitrary-angle rendering

- **FR-05.1:** One central viewport MUST toggle between 3D Inspection, Keyframes and Animation. These MUST NOT be three simultaneous review panes. Keyframes MUST be the initial review mode when rendered stills are available.
- **FR-05.2:** 3D Inspection MUST support orbit, pan, zoom and object selection on a static `.glb` proxy tied to a source version and sample frame.
- **FR-05.3:** Keyframes MUST display and support annotation of material-capable Blender stills for lighting/texture review. Draft-motion output MUST NOT be represented as final material evidence.
- **FR-05.4:** Animation MUST support rendered playback, pause, frame stepping, shot navigation and inclusive frame-range selection at the declared frame rate.
- **FR-05.5:** Mode changes MUST preserve the selected item/version and associated review state. Selecting an annotation MUST restore its captured view/frame context.
- **FR-05.6:** Render this view MUST send the inspection camera and target version to Copilot. Copilot supplies the Blender render instructions. Switching modes alone MUST NOT start a render.
- **FR-05.7:** The UI MUST report actual dimensions, frame rate, renderer/profile and availability. Media resolution MUST be independent of the desktop UI's Full HD design reference. Keyframe, draft and final review images MUST be the view-transformed sRGB PNG declared by `review_output`.

### FR-06: Annotation and spatial identity

- **FR-06.1:** Keyframes and available animation frames MUST support sketches/arrows, masks, erasing annotation ink, region selection and text pins. Region gestures describe requested changes rather than directly editing Blender.
- **FR-06.2:** Drawing on animation MUST pause and capture the actual displayed frame. Frame and range annotations MUST retain exact source and render identities as additional media becomes available.
- **FR-06.3:** The request MUST include the clean image, markup/mask, image-space coordinates and available object/raycast metadata. Letterboxing, zoom and device-pixel ratio MUST be accounted for.
- **FR-06.4:** Stable object identifiers and declared coordinate conversions MUST map a valid proxy selection back to Blender. Names alone MUST NOT be treated as permanent identity.
- **FR-06.5:** A 3D hit MUST only be attached when the geometry, camera and sample frame match the reviewed view. Missing or uncertain hits MUST remain explicit instead of invented XYZ coordinates.
- **FR-06.6:** Drafts MUST survive navigation. Saved annotations MUST have durable IDs, safe retries and explicit save/error status, including across tabs or lost acknowledgements.
- **FR-06.7:** A temporal range annotation MUST distinguish its drawn anchor frame from the range to which its comment applies. V1 MUST NOT imply automatic tracking of a mask through motion.

### FR-07: Progressive animation review

- **FR-07.1:** Rendering MUST operate from completed, pinned source inputs. Ongoing authoring MUST NOT silently alter an in-progress render's inputs.
- **FR-07.2:** Before output is available, the selected animation view MUST show Rendering and real job state. Available frames MUST become inspectable without waiting for the full animation.
- **FR-07.3:** Completed contiguous frame ranges MUST become playable preview segments while remaining frames render. Pending/failed ranges MUST be identifiable on the timeline.
- **FR-07.4:** New frames or segments MUST NOT move the user's playhead, replace an annotation target or silently skip unavailable frames.
- **FR-07.5:** Annotations MUST use source/job/frame or segment identities before a final movie hash exists. Final assembly MUST preserve their relationship to the reviewed frames.
- **FR-07.6:** Render profiles MUST distinguish high-quality keyframes, cheaper draft animation and final delivery. Copilot supplies the profile/settings appropriate to the requested work.

### FR-08: Asset research, discovery and reuse

- **FR-08.1:** Copilot MUST discover reusable assets within the active project using filenames, text metadata and ordinary search tools, without requiring a vector database. Another project's content requires an explicit owner import into the active folder before agent access.
- **FR-08.2:** Configured online MCPs MUST support asset discovery/acquisition where available. Search results MUST NOT be reported as imported or validated assets.
- **FR-08.3:** When a tool knows a source URL, creator or licence, it records them on optional `provenance`. Unknown stays null. Missing or restrictive rights MUST NOT block download, import, local use, checkpoint or publication, and MUST NOT be rewritten as a cleared licence.
- **FR-08.4:** Copilot/tools MUST perform imports, procedural/Geometry Nodes authoring, scale/origin preparation and actual Blender validation as required by the request.
- **FR-08.5:** Reusing or revising an asset MUST preserve existing scenes and published versions. Proscenium presents the result rather than choosing replacements.

### FR-09: Story planning and cinematic authoring

- **FR-09.1:** Copilot MUST turn a project brief into a structured narrative/shot plan with stable IDs, asset dependencies, composition, camera intent, motion beats and unresolved decisions.
- **FR-09.2:** Plans MUST support ordered start/intermediate/end storyboard images, role-specific references and explicit missing/unbuilt states.
- **FR-09.3:** Copilot MUST author the corresponding Blender scene, camera/marker and timeline bindings. Proscenium MUST present those bindings rather than create them independently.
- **FR-09.4:** Storyboards and shot plans MUST be versioned and reviewable in V1. Revising one shot MUST identify affected downstream work. Copilot may continue the requested production task autonomously; Proscenium MUST NOT launch regeneration merely because catalogue/reference data changed.
- **FR-09.5:** Planning, image generation, Blender authoring and assembly MUST be separately invocable stages. Browsing or rebuilding a review display MUST NOT submit generation jobs.
- **FR-09.6:** Storyboard review pacing MUST NOT be represented as authored animation timing. Shot/sequence timing MUST be explicit.
- **FR-09.7:** For a broad production request, Copilot MUST be permitted to progress through planning, concepts, authoring and draft/keyframe review without mandatory approval after each stage. User-requested checkpoints, consequential actions and approved VLM findings retain their explicit rules.

### FR-10: Local image generation

- **FR-10.1:** Copilot MUST invoke ComfyUI through reusable tools against a configured endpoint, initially using the existing FLUX tooling as the implementation reference.
- **FR-10.2:** Image generation MUST accept selected references as actual conditioning inputs where supported, not merely filenames mentioned in a prompt.
- **FR-10.3:** Tools MUST record exact submitted graphs, prompts, seeds, models, input hashes, job IDs, transformations and output hashes.
- **FR-10.4:** Known job IDs MUST be reconciled after timeouts before any resubmission. Targeted revisions MUST preserve the previous selected result and unaffected work.
- **FR-10.5:** Independently running ComfyUI MUST NOT be installed, upgraded, started, stopped or have its queue cleared implicitly by this workflow.
- **FR-10.6:** Image and image-to-3D generation MUST share the configurable attempt policy: three attempts per target/task by default, with request/project overrides. Reserve before submission, retain uncertain reservations, and do not charge polling/download/reconciliation as fresh attempts.
- **FR-10.7:** Budget exhaustion MUST preserve results, report the limitation and allow independent work to continue. Only an explicit owner allowance change/new generation request resets or extends the relevant budget.

### FR-11: Requested VLM review

- **FR-11.1:** A local VLM review MUST run only when the user or Copilot requests it, initially through a configured LM Studio endpoint such as `http://localhost:1234`.
- **FR-11.2:** Findings MUST identify the exact reviewed source/media, evidence and suggested change. Image observations and measured scene telemetry MUST remain distinguishable.
- **FR-11.3:** The UI MUST allow findings to be selected, amended or rejected and submitted together. Only the user's approved findings become authorised critique input for the Builder.
- **FR-11.4:** Approved findings MUST reach Copilot with the relevant original images and UI context. Neither the VLM nor an Approve button may directly mutate Blender.
- **FR-11.5:** A failed/unavailable review MUST leave normal authoring and review of existing outputs usable. Publication MUST NOT require a VLM pass.
- **FR-11.6:** A Copilot-triggered review tool MUST return a review ticket/status. Unapproved findings are delivered to the owner UI. A single owner Queue/Steer submission atomically links the approved finding revisions/text to that message.

### FR-12: Image-to-3D capability

- **FR-12.1:** The plan MUST include an early hardware and import feasibility proof and a later end-to-end image-to-3D journey. No working local pipeline is currently established. A known licence is recorded. Rights do not decide the proof.
- **FR-12.2:** A successful integration MUST produce an actual mesh that Copilot imports into Blender, inspects, saves and publishes with its inputs and source history.
- **FR-12.3:** Supported topology, textures, scale/origin preparation, memory usage and limitations MUST be measured. Automatic production-ready retopology or rigging MUST NOT be assumed.
- **FR-12.4:** Failure of the proof MUST be recorded as an unresolved capability, not a completed feature. Core studio journeys can proceed, but changing this planned V1 scope requires an explicit owner decision.

### FR-13: Playable Unity scene

- **FR-13.1:** Copilot MUST be able to delegate Unity-specific work to a native specialist subagent with the configured Unity CLI/tools.
- **FR-13.2:** The specialist MUST consume explicitly pinned Blender outputs, perform needed material/mesh/collider preparation and configure a saved Unity scene.
- **FR-13.3:** The result MUST include basic movement, camera control, collision/floor support and a safe spawn appropriate to the brief. No additional gameplay is required.
- **FR-13.4:** The specialist publishes source-version references, the Unity scene path and a `UnityResult` validation status of passed, failed or not_run. A failed or skipped playtest does not block publication. Playtesting stays in the Unity Editor. Keyboard and mouse are sufficient. Visual parity with Blender is not required.
- **FR-13.5:** Unity changes MUST use Unity APIs and preserve asset GUIDs. Tools MUST respect active/dirty Editor state rather than force-close an instance to run a batch command.
- **FR-13.6:** The agent-operated Unity project MUST be within the active Proscenium project folder in V1. A configured external path does not widen the owner's current-project-only content permission.

### FR-14: Connection and restart recovery

- **FR-14.1:** Browser closure or SSH/browser disconnection MUST NOT cancel accepted work. Reconnection MUST restore messages, public activity, annotations and published outputs.
- **FR-14.2:** Backend/workstation restart MUST restore durable project state without automatically submitting new Copilot work. The user explicitly chooses Resume.
- **FR-14.3:** Resume MUST give Copilot the unfinished request and recorded job/output identities so it can inspect actual tool state before deciding what remains.
- **FR-14.4:** Unknown outcomes MUST remain uncertain until reconciled. The application MUST NOT infer success from a stale running flag, a reused PID or a missing exit code.
- **FR-14.5:** Switching projects MUST NOT silently abandon active authoring. The UI MUST expose the active state and require an explicit settle/stop/switch choice where work is ongoing.
- **FR-14.6:** Request admission/reconciliation and the restart execution gate MUST be established during M01/M03. M12 completes cross-provider recovery and backup acceptance, rather than retrofitting identity and durable delivery late.
- **FR-14.7:** Native session loading, pending native messages and restored callback attribution MUST be tested before enabling automatic attachment to a restarted runtime. New execution waits for the explicit Resume path.

### FR-15: Silent cinematic delivery

- **FR-15.1:** Copilot MUST assemble explicitly pinned shot-output choices, labelled as agent-proposed or user-selected, with declared order, trims, timing, frame rate and transitions into a silent cinematic delivery. Autonomous assembly does not imply owner selection.
- **FR-15.2:** Deliveries MUST identify the exact source/render versions and transformations, with measured dimensions, frame counts, duration and output hashes.
- **FR-15.3:** A final video MUST be fully decodable and reviewable through Proscenium. Partial previews MUST remain labelled partial rather than complete deliveries.
- **FR-15.4:** Existing feedback MUST retain its original frame/sequence mapping when an edit or delivery changes. Carrying notes to a new edit requires an explicit mapping, not filename reuse.

### FR-16: Application configuration

- **FR-16.1:** The application MUST expose configuration for project locations, Copilot models/tool profiles, local service endpoints and preview/output locations.
- **FR-16.2:** The browser MUST use the Proscenium origin for API, events and published media. Workstation service URLs MUST NOT be assumed to refer to the remote browser's machine.
- **FR-16.3:** Local providers MUST have separate capability-appropriate interfaces. A model inference request and an asynchronous ComfyUI workflow MUST NOT be forced into one incompatible request shape.
- **FR-16.4:** Opening projects, reviewing existing media and saving feedback MUST remain possible when cloud reasoning or a generation service is unavailable. No unrequested local LLM fallback is required.

### FR-17: Owner policy and bounded autonomy

- **FR-17.1:** Routine in-scope authoring, project edits, local checkpoints, configured free asset acquisition and configured local generation MUST run under the automatic policy in [owner policy](owner-policy.md).
- **FR-17.2:** Purchases, installation/service changes, external publishing and destructive retained-content/Git operations MUST require the exact owner authorisation specified there. An agent statement is not a human approval.
- **FR-17.3:** Active-project content scope MUST set the session working directory to the active project root, disable cross-session memory, instruct agents/skills to stay within the project root, and enforce path/exclusion checks on Proscenium's own HTTP and publication routes. No OS-level sandbox wrapper is required around Blender MCP or CLI tools.
- **FR-17.4:** Owner, native-tool and worker API channels MUST have distinct capabilities. A tool cannot answer its own permission request or submit a user selection by spoofing an actor field.

### FR-18: Uploads and explicit selections

- **FR-18.1:** Browser image upload MUST have immutable IDs, decoding/type/size checks, content hashes and explicit readiness before attachment. Normalisation retains original bytes and transformation provenance.
- **FR-18.2:** Uploading or publishing an image MUST NOT automatically select it or attach it to a model request. Roles and target context are explicit.
- **FR-18.3:** The owner MUST be able to select, supersede and withdraw an asset/storyboard/shot version choice using revision-safe operations. Current publication, agent choice and owner selection remain separate.
- **FR-18.4:** Input formats, default limits, range-annotation semantics and unavailable-media behaviour MUST follow [review and media](review-media.md).

## 5. Non-functional requirements

### NFR-01: Desktop interaction

- **NFR-01.1:** The acceptance viewport MUST be 1920 × 1080 in a desktop browser. Panels MAY scroll internally. No mobile layout is required.
- **NFR-01.2:** Linear Minimal MUST be the V1 visual baseline: neutral dark surfaces, thin borders, restrained indigo, Inter UI text and monospace technical labels. A multi-theme system is not required.
- **NFR-01.3:** Busy, pending, partial, saved, failed and unavailable states MUST be visible. Keyboard playback and mode controls follow the [design reference](design/README.md). Selectable conversation text MUST remain usable. Keys are ignored while a text field has focus.
- **NFR-01.4:** Desktop Chromium, Brave and Edge MUST pass the defined acceptance cases. Automated Chromium checks and actual Brave/Edge checks MUST record browser versions on the Linux reference workstation.

### NFR-02: Deployment and service boundaries

- **NFR-02.1:** V1 MUST run host-native on one workstation, with FastAPI serving the compiled frontend. Browser access MUST work through SSH forwarding.
- **NFR-02.2:** Blender desktop authoring MUST work with the existing bridge and virtual-display tooling. If no compatible Blender instance is listening on the bridge port, Copilot MUST start one using the launch tooling and verify active-file/dirty state before editing. Preview rendering MUST not replace the live authoring session.
- **NFR-02.3:** LM Studio and ComfyUI MUST remain independent services. Their availability and capabilities MUST be reported honestly rather than assumed.

### NFR-03: Integrity and repeatability

- **NFR-03.1:** Publications, message submissions and annotation saves MUST have stable retry identities. Known completed work MUST be reused rather than executed twice accidentally.
- **NFR-03.2:** Published media MUST be content-bound and source inputs pinned. Preview workers MUST NOT save over authored sources or accepted media.
- **NFR-03.3:** Tooling MUST preserve unsaved Blender/Unity work and use checkpoints and isolated workers where appropriate. There is no claim of an application sandbox or universal rollback for arbitrary agent Python.
- **NFR-03.4:** Runtime databases, logs and caches MUST be separate from authored sources. Tests MUST use isolated databases/output directories rather than the user's projects or review notes.

### NFR-04: Resource and performance evidence

- **NFR-04.1:** Runtime inventory MUST discover actual available RAM/GPU memory, Blender/Unity versions, rendering devices and service capabilities. Historical 128 GB targets and ROCm version examples MUST NOT be treated as live measurements.
- **NFR-04.2:** Representative tests MUST record time to first reviewable frame, draft-render throughput, proxy size/load time, viewport frame times and local inference/generation memory use.
- **NFR-04.3:** Resource-heavy tools MUST inspect shared-service/GPU state and avoid unbounded concurrent generation. No static allocation of the entire workstation's memory or application-owned global GPU scheduler is required.
- **NFR-04.4:** Latency targets MUST be set from the measured workload. The superseded fixed 800 ms export, 2.5-second LLM and five-second VLM claims are not V1 guarantees.

### NFR-05: Access, privacy and rights

- **NFR-05.1:** The personal service MUST bind to loopback by default and use the existing SSH access model. Public exposure is outside V1.
- **NFR-05.2:** Only intended UI assets and published media MUST be served. Arbitrary project roots, Git metadata, environment files and credentials MUST NOT be exposed by file-serving routes.
- **NFR-05.3:** Copilot tools and permissions MUST implement the pragmatic [owner policy](owner-policy.md): session working directory pinned to the active project root, cross-session memory disabled, validated Proscenium HTTP/publication paths and distinct caller tokens.
- **NFR-05.4:** Relevant permitted active-project images/files may reach cloud reasoning through attachments or agent tools. The UI MUST show captured attachments and public tool access. Credentials, environment files and cross-project content remain excluded.
- **NFR-05.5:** Record known provenance when reuse happens. Do not block reuse because rights are unknown or restrictive. A source checkpoint does not itself push the repository. External push and sharing still require the destination confirmation in owner policy.

### NFR-06: Verification and portability

- **NFR-06.1:** Contract, integration and desktop-browser tests MUST cover the completed journeys, including retry and failure paths. A technical pass MUST NOT be presented as creative acceptance.
- **NFR-06.2:** Reused tools MUST be copied into maintained Proscenium tooling with their copyright notices, rather than depend on the reference repositories' active scenes, secrets, environments or machine-specific defaults. Asset rights are not a runtime gate.
- **NFR-06.3:** The mockup is a visual reference, not production code. Runtime frontend assets MUST be bundled rather than depend on its Tailwind/Three.js/font CDNs.
- **NFR-06.4:** Development work packages MUST consume a frozen contract revision and named fixtures, have explicit file ownership and follow the [fleet handover](development-fleet.md). Shared contracts, migrations and root build files have one integration owner.

## 6. Journey and milestone coverage

The full workflow, dependencies, tests and failure cases are in [user journeys and implementation milestones](user-journeys.md). All milestones are planned, not implemented.

| Milestone | User outcome | Primary coverage |
| --- | --- | --- |
| M01 | Direct Blender, see a published render, annotate it and continue the discussion | FR-01–FR-04, FR-06 |
| M02 | Open projects, browse individual outputs and inspect source history | FR-01, FR-04 |
| M03 | Direct ongoing work with queued turns, steering and native subagents | FR-02, FR-03 |
| M04 | Inspect 3D, review keyframes and request a render from an arbitrary angle | FR-05, FR-06 |
| M05 | Research, find, import and reuse assets without changing existing scenes | FR-08 |
| M06 | Develop and review a complete shot/storyboard plan | FR-09 |
| M07 | Generate and revise storyboard/reference images through ComfyUI | FR-10 |
| M08 | Review and annotate an animation before rendering finishes | FR-07, FR-09 |
| M09 | Request VLM critique and send approved findings with images to Copilot | FR-11 |
| M10 | Generate a mesh from an image and publish a usable Blender asset | FR-12 |
| M11 | Open a playable Unity scene with basic player controls | FR-13 |
| M12 | Reconnect or explicitly resume interrupted work without duplicates | FR-14 |
| M13 | Review and export an assembled silent cinematic | FR-15 |
| M14 | Use the consolidated studio from a clean project and configured workstation | FR-16 and all NFR families |

## 7. Known readiness limits

- The reference Blender/Unity/ComfyUI workflows have historical code and test evidence. They have not yet been integrated into or validated as Proscenium.
- Copilot SDK compatibility with the actual authentication, MCP definitions, image inputs and native subagent profiles must be proved first.
- Progressive playback and version-pinned annotation are new integration work despite the existing per-frame render receipts.
- Image-to-3D has no established working implementation. Model choice, runtime support, mesh quality and resource limits remain an explicit feasibility task. Rights do not decide it.
- Contract 1.0 is specified in [contracts](contracts/README.md). F00 must validate it, generate bindings, establish the offline harness and pin/prove the SDK/runtime combination before dependent agents implement against it.
