# Proscenium: user journeys and implementation milestones

**Version:** 1.0.0

**Updated:** 2026-11-14

**Status:** Complete V1 implementation plan from the architecture discussion. All milestones are planned, not started.

Read with [requirements](requirements.md) and [architecture](architecture.md). Requirement families below refer to those documents. This plan records the full experience, not just the first demonstration.

## 1. Delivery principles

- Deliver working user journeys rather than separate frontend, agent and renderer projects that only meet at the end.
- Begin with the prompt → Blender → published render → annotation → follow-up loop.
- Copilot and its native subagents own application authoring and creative decisions. Proscenium supplies interaction, presentation, persistence and explicit output hand-off.
- Reuse the tested Blender, Review Studio, Unity and ComfyUI work. Adapt shared tools and skills, not their existing film/game content or historical directory structure.
- Use the Linear Minimal desktop layout from the first milestone. Expand its real controls as the corresponding journeys arrive, without fake success states.
- Keep one active project, one persistent project discussion and exact per-turn UI context.
- Keep source checkpoints, execution, publication, technical assessment and user selection distinct.
- Every milestone preserves the previous milestones' acceptance tests. Technical acceptance does not substitute for the owner's creative review.
- Generated media and model calls must be bounded test work in disposable projects. Do not disturb live reference-project sessions or shared service queues.
- Milestone numbers are the recommended delivery order. Dependencies describe the actual prerequisites. This is not a request to run parallel development agents.

## 2. Milestone map

| Milestone | User journey | Depends on | Status |
| --- | --- | --- | --- |
| [M01](#m01-first-blender-authoring-loop) | Direct a scene and give feedback on its first published render | Existing toolchain and user Copilot access | Planned |
| [M02](#m02-projects-output-catalogue-and-history) | Open projects, browse assets/scenes and inspect their history | M01 | Planned |
| [M03](#m03-project-discussion-steering-and-native-delegation) | Queue requests, steer work and observe Copilot's native specialists | M01, M02 | Planned |
| [M04](#m04-spatial-inspection-keyframes-and-markup) | Toggle inspection/keyframes, annotate and render a chosen viewing angle | M02, M03 | Planned |
| [M05](#m05-research-find-import-and-reuse-assets) | Research and reuse local/online assets | M02, M03, M04 | Planned |
| [M06](#m06-brief-to-reviewable-storyboard) | Develop a complete narrative and shotboard | M02, M03, M05 | Planned |
| [M07](#m07-generate-and-revise-concept-images) | Generate/revise selected storyboard and reference images through ComfyUI | M04, M06 | Planned |
| [M08](#m08-author-and-review-animation-progressively) | Review animation and annotate available frames before rendering finishes | M04, M06. M07 when generated concepts are used | Planned |
| [M09](#m09-request-and-approve-vlm-critique) | Request local critique and send approved findings with images to Copilot | M03, M04. M08 for motion-review inputs | Planned |
| [M10](#m10-image-to-3d-asset-generation) | Generate and publish a usable Blender mesh from an image | Early feasibility proof, M04, M05. M07 for generated inputs | Planned, unproven capability |
| [M11](#m11-playable-unity-scene) | Ask the Unity specialist for a playable scene and test it in Unity | M03, M04, M05. M10 is not required for authored/imported assets | Planned |
| [M12](#m12-reconnect-and-explicitly-resume) | Reconnect or resume interrupted work without duplicates or lost context | M03, M07, M08, M11 for representative job types | Planned |
| [M13](#m13-assemble-review-and-deliver-a-silent-cinematic) | Review/export an assembled silent sequence | M06, M08, M12 | Planned |
| [M14](#m14-consolidated-desktop-studio-acceptance) | Use the complete personal studio from a clean configured workspace | All included V1 milestones | Planned |

### Early proofs, not additional product milestones

Run the smallest necessary proofs early so integration risks are discovered before the corresponding full journey is built.

| Proof | When | Evidence required |
| --- | --- | --- |
| E01: Copilot SDK compatibility | During M01 | Actual authentication, available models, image input, existing MCP connection, native subagent support, public events, queue/steer API and native session resume. Pin compatible SDK/runtime versions. |
| E02: Source and identity contract | During M01, extended in M02/M04 | A saved source checkpoint, actual LFS hydration, dependency fingerprints, stable item/object identity and image/camera coordinate fixtures. |
| E03: Progressive-media path | After the first render loop, before committing to the M08 player implementation | A bounded Blender render exposes completed frames and a playable contiguous segment before the last frame exists. Record the source-to-video frame mapping. |
| E04: Image-to-3D feasibility | Begin after M01 rather than waiting until M10 | A candidate with usable licence and real host support produces a mesh that opens in Blender. Record failure if no candidate works. Runtime/model installation needs explicit approval for changes to independent services. |

E04 does not block development of the authoring/review studio. It does determine whether M10 is deliverable as designed. A failed proof is not completion of M10 or permission to silently remove it from V1.

## 3. Shared milestone completion criteria

Every milestone must provide:

1. A reproducible demonstration in a disposable project, with actual artefacts and honest job state.
2. Tests appropriate to its contracts, tool integration and desktop UI, including its listed failure cases.
3. Updated schemas/migrations, agent instructions and operator guidance where the interface changes.
4. Exact source/tool/model identities and limitations for any claimed capability.
5. Evidence that unrelated source files, live application sessions, credentials and existing feedback were preserved.
6. A concise remaining-risk record. Unsupported features stay unavailable or explicitly experimental, not disguised by mocked controls.

Acceptance is cumulative. A milestone is not complete because an API returned successfully, a model claimed success, or a video decoded without checking its intended user interaction.

## M01: First Blender authoring loop

**User journey:** “I describe a small scene, see the result, mark what needs changing and continue the same discussion.”

**Requirements:** FR-01, FR-02, FR-03, FR-04, FR-06. Establish the initial NFR-01–NFR-06 baseline.

### Scope and implementation

- Establish repository hygiene before staging implementation: ignore secrets, runtime stores, environments and generated caches, and configure appropriate Git LFS source patterns.
- Build the minimal host-native React/FastAPI shell in the Linear Minimal layout at Full HD.
- Create/open a disposable project with a persistent native Copilot session association and minimal item/source/publication/context records.
- Prove cloud model access and connect the existing Blender MCP/CLI tools through the SDK. Configure tool permissions deliberately.
- Adapt only the Blender instructions and isolated preview-render path needed for this loop.
- Let Copilot author a small scene, save/checkpoint it and explicitly publish the completed source plus a still-preview request.
- Render and display the still. Provide a basic text pin or sketch attachment and submit the clean/marked images with the exact source identity on the next turn.
- Persist the public conversation, publication and feedback records needed to demonstrate continuity.

### Acceptance

1. A real user prompt reaches the selected cloud model through the Python SDK and results in a saved, reopenable Blender scene.
2. The source commit and actual source/dependency hashes identify that scene. No secret file or runtime database is staged.
3. `publish_outputs` updates the UI and produces an actual preview from that source without changing the live authoring file.
4. A visual correction reaches Copilot with both images, the target version and the annotation. Copilot publishes a visibly changed result as a new version.
5. The previous rendered result and its annotation remain identifiable and available.
6. A repeated publication acknowledgement does not create another version or render the same job twice.
7. E01 confirms the required SDK features with a minimal native-subagent probe and native session-resume test. Full delegation UI and crash recovery come later.
8. A 1920 × 1080 browser check exercises the loop rather than only testing static mockup screenshots.

### Failure and recovery checks

- Missing Copilot access or Blender bridge produces an actionable unavailable state, not a local-model fallback or a fake result.
- Invalid publication/missing output leaves the last available preview intact.
- A dirty live Blender scene is preserved. Any timed-out edit is inspected through tools before retrying.

**Reuse:** Blender bridge configuration/skill, isolated renderer/worker and the Review Studio feedback identity pattern listed in the [reference inventory](architecture.md#15-reference-implementation-inventory).

**Boundary:** This is the first usable loop, not a claim that the full catalogue, three review modes or cinematic workflow are finished.

## M02: Projects, output catalogue and history

**User journey:** “I reopen a project, choose an asset or scene, inspect its previous results and return to the current work.”

**Requirements:** FR-01, FR-04, FR-16.

### Scope and implementation

- Add create/open/recent-project flows with explicit user-chosen workspace paths and no inherited creative content.
- Generalise the catalogue to assets, scenes, storyboards and sequences, each with a stable item ID and current published version.
- Define the complete Git/source/dependency reference and preview-artefact contracts. Add schema migrations and fixture validation.
- Display source revision, version history, preview availability and separate execution/review/selection states.
- Materialise historical source/dependency snapshots without checking out over the live working tree.
- Keep canonical source filenames. Requests to restore/revise a previous version go to Copilot.
- Allow publications containing several completed items and incrementally update the catalogue during an ongoing request.

### Acceptance

1. Two independent project folders retain different native session IDs and content, with only one active project.
2. One project contains multiple assets and scenes, each with independent history and published pointers.
3. Revising an asset does not change the source/dependency hashes or preview of a scene that still uses its older version.
4. Viewing a historical result does not change Git HEAD, dirty working files or an open Blender session.
5. A multi-item publication can expose a finished asset while another item/preview remains pending, without marking the whole request complete.
6. Reopening the application restores the catalogue from durable records. Filesystem modification times or “newest directory” are not used to select versions.

### Failure and recovery checks

- Missing LFS content is reported as missing/hydration-required, not passed to Blender as a model.
- Conflicting publication-ID reuse cannot overwrite an existing version.
- An invalid/missing dependency is reported against the affected item rather than silently replaced by the current asset.

**Reuse:** Canonical-file/Git LFS practices in both extracted projects and explicit project discovery in `smal/tools/pipeline/project.py`.

## M03: Project discussion, steering and native delegation

**User journey:** “I keep directing the project while Copilot works, queue later requests and correct the current one when needed.”

**Requirements:** FR-02, FR-03. Establish the event replay/message identity used by FR-14.

### Scope and implementation

- Complete the persistent project conversation and activity display, distinguishing lead messages, native subagents, tool calls and completed outputs.
- Implement Send as native queueing and a separate Steer control as native immediate delivery.
- Capture immutable UI context and attachments for every message, including queued/steered ones.
- Register native agent profiles with explicit expertise/tool access and configurable model overrides. Copilot decides delegation.
- Add reusable skills to the SDK configuration rather than depending on OpenCode-specific skill discovery at runtime.
- Display available model/usage information honestly, with parent/worker attribution where the pinned SDK supplies it.
- Persist acknowledged message IDs and stream/replay public events with deduplication.

### Acceptance

1. While Copilot works on scene A, the user queues a request about asset B and then browses elsewhere. The queued request still contains B's original version/context.
2. A Steer correction is delivered to the active turn without promising to undo an already executed tool call.
3. A native specialist handles a bounded task under a configured economical model while the lead retains its selected frontier model. Recorded runtime events identify what actually ran.
4. Copilot can also do a small task directly. The application does not force every operation through a fixed agent graph.
5. Reloading the browser restores conversation/activity without sending another model request.
6. A lost send acknowledgement is reconciled using durable request/runtime IDs rather than automatically duplicating the request.
7. Usage is measured over completed work, including workers/retries. Missing metrics are labelled unavailable rather than converted to free execution.

### Failure and recovery checks

- Unavailable worker model or failed specialist is surfaced to the lead and user, with no silent model-policy change.
- If a steering message reaches an idle session, its actual queued/new-turn behaviour is displayed accurately.
- A context snapshot referencing an older version stays intact even if Copilot must inspect newer project state to act safely.

**Reuse:** Existing creative/application skills, adapted into native Copilot profiles and skill directories. SDK semantics are linked in the [architecture references](architecture.md#16-external-api-references-and-implementation-proofs).

## M04: Spatial inspection, keyframes and markup

**User journey:** “I inspect an asset in 3D, switch to its rendered keyframe, mark a change and ask for a render from my chosen viewing angle.”

**Requirements:** FR-05, FR-06, FR-03.

### Scope and implementation

- Add the 3D Inspection / Keyframes / Animation central mode control. Animation remains honestly pending until M08.
- Display a static `.glb` proxy with orbit/pan/zoom, object selection and a source-version/sample-frame label.
- Have Copilot's tools publish a stable object-ID/export mapping, including instance and camera metadata.
- Complete keyframe drawing, arrows, masks, annotation eraser, region selection and text pins, with clean image/markup exports.
- Implement camera/frame restoration when navigating saved annotations.
- Implement Render this view as a Copilot request containing the inspection camera. The resulting isolated render uses the declared source and camera without saving a source edit.
- Test image-content rectangle, camera optics, coordinate conversions and optional raycast metadata explicitly.

### Acceptance

1. The central viewer initially opens available rendered keyframes, shows one mode at a time and retains the selected item/version between toggles.
2. A reference object can be selected in the proxy and resolved to its stable Blender ID, including after a rename that preserves identity.
3. An annotation retains correct placement under scaling/letterboxing and when reopening its saved image. Perspective, orthographic and non-default camera-aspect fixtures are covered.
4. Missing/mismatched proxy geometry produces image-space feedback without invented world coordinates.
5. Render this view yields a Blender still with tested framing correspondence to the selected camera, while a simple mode switch does not start rendering.
6. Unsaved annotation drafts survive item/mode navigation. Retried saves and conflicting edits from a second tab are handled explicitly.
7. Keyframes show the actual engine/profile/dimensions. A Workbench preview is not labelled a lighting/material pass.

### Failure and recovery checks

- Failed/absent proxy leaves keyframe review available.
- A changed proxy version does not silently reproject existing markup onto different geometry.
- No sketch, mask, erase, drag or pin action directly edits Blender.

**Reuse:** Mockup's Linear Minimal layout, existing rendered-keyframe pipeline and Review Studio targeting/outbox logic. Object mapping and coordinate fixtures are new work.

## M05: Research, find, import and reuse assets

**User journey:** “I ask for a prop or environment, inspect the sources Copilot finds and reuse or revise the resulting asset.”

**Requirements:** FR-08, FR-01, FR-03.

### Scope and implementation

- Adapt the production-research skill to the chosen project's brief, required fidelity and actual asset needs.
- Give Copilot ordinary filename/text search over the growing local library and descriptive metadata.
- Connect the installed online asset MCPs, including BlendSwap, through the Copilot tool configuration.
- Record source URLs, creators/licences/restrictions, acquired files, transformations and inspection results.
- Let Copilot import or procedurally author an asset through Blender, inspect it and publish its editable source/proxy/keyframes.
- Support explicit reuse/adoption of an asset version in a scene, with no automatic update propagation.

### Acceptance

1. Copilot finds a local asset from its name/tags/description without an embedding service.
2. An online search result is visibly distinct from an acquired file and a successfully imported asset.
3. The imported asset opens in Blender with declared units, available materials and dependencies, and has a searchable source/licence record.
4. The user can inspect that asset independently of a scene and request a revision.
5. Publishing the revised asset leaves an existing scene's recorded asset version untouched until Copilot explicitly adopts the revision.
6. Unknown licence, unavailable download or failed import is reported with the exact limitation rather than an invented successful acquisition.

### Failure and recovery checks

- Preserve existing local asset/scene files on a failed or partial import.
- Account-gated, paid or installation-requiring routes require appropriate explicit permission rather than implicit purchases/add-on execution.
- Private/licensed assets are not automatically staged or uploaded.

**Reuse:** `production-research` skill and its acquisition/coverage templates, plus existing Blender inspection and export validation.

## M06: Brief to reviewable storyboard

**User journey:** “I describe the film or world, discuss its direction and review the complete shot plan before detailed production.”

**Requirements:** FR-09, FR-03, FR-04.

### Scope and implementation

- Adapt storyboard planning around a project brief and selected style/treatment, not a historical project's story.
- Formalise assets, shots, sequences, reference roles, start/intermediate/end compositions, motion beats, timing and unresolved inputs in one typed plan contract.
- Display ordered shot cards and reference/concept-frame selections in the left panel, with missing/unbuilt states.
- Present selected storyboard images through the central image review area with source-type labels. Do not imply that a concept image is a Blender render or geometry.
- Let Copilot revise individual shots and identify affected dependants. Preserve prior source versions and user selections.
- Keep planning and view regeneration independent from image generation or scene construction.

### Acceptance

1. A new brief yields a complete ordered shot plan with stable IDs and an explicit list of missing assets/images/decisions.
2. Each shot can be selected, discussed and annotated with its reference roles and proposed camera/motion/timing context.
3. A complex camera move can have intermediate storyboard frames rather than only a first/last image.
4. Revising one shot preserves unrelated shot IDs and selected inputs, and reports affected downstream work without submitting it.
5. The user can record a storyboard/shot selection separately from render completion and final animation approval.
6. Viewing/rebuilding the storyboard UI submits no GPU jobs. Review-card pacing is not stored as production duration.

### Failure and recovery checks

- Missing images stay visible as missing. Existing images are not silently rerendered to fill the display.
- Conflicting creative inputs remain explicit rather than combined into an invented agreed direction.
- No old SMAL shot count, cast, industrial prompt fragment or production duration enters a new project by default.

**Reuse:** `storyboard-generation`, `production-research` and the real seven-scene storyboard-review lessons. Adapt their contracts to canonical Git-backed source files rather than numbered plan folders.

## M07: Generate and revise concept images

**User journey:** “I generate the selected storyboard or asset concept, compare it with its references and request a targeted correction.”

**Requirements:** FR-10, FR-09, FR-04.

### Scope and implementation

- Adapt the existing ComfyUI CLI wrapper, graph preflight and reference-conditioned FLUX tools to an explicit project and configured endpoint.
- Remove automatic ComfyUI server start/stop, hardcoded URLs and machine-specific prompt/path assumptions from production invocation paths.
- Separate submit/status/wait/download, retaining job/prompt IDs immediately and reconciling known work after interruption.
- Save graph/prompt/model/seed/settings, reference hashes/roles, conditioning derivatives and output hashes.
- Publish each completed image independently, associating it with the requested shot/asset without automatically changing selected downstream inputs.
- Allow one selected image/target to be regenerated without rerunning the whole storyboard.

### Acceptance

1. A configured independent ComfyUI service generates an actual reference-conditioned image through Copilot's tools.
2. All HTTP/CLI operations use the same configured endpoint. The workflow does not start/stop or reconfigure the service.
3. The result is displayed with its actual input references, source type, dimensions and generation identity.
4. A targeted correction preserves the previous selected image and unrelated outputs.
5. A timeout/retry with a recorded prompt ID resumes the original job. An ambiguous unacknowledged submission stays uncertain pending reconciliation.
6. Unchanged completed output is hash-verified and reused. Browsing the result or rebuilding a review display generates nothing.

### Failure and recovery checks

- Missing node/model or invalid graph is reported before claiming successful generation.
- An occupied shared queue is respected, not cleared.
- Native generation dimensions and later crops/upscales are recorded separately. No promise of bit-identical regeneration is made merely from a saved seed.

**Reuse:** `smal/tools/comfy/comfy`, `check.py`, `still_batch.py`, `image-generation` and their graph/reference/job-recovery tests. Extract general behaviour, not archived numbered-take assumptions.

## M08: Author and review animation progressively

**User journey:** “I ask Copilot to realise the shots, inspect lighting keyframes, then review and annotate motion while the rest is still rendering.”

**Requirements:** FR-07, FR-09, FR-05, FR-06.

### Scope and implementation

- Let Copilot bind the selected plan to actual Blender scenes, cameras/markers, frame ranges and animation.
- Publish material-capable keyframes separately from cheaper motion previews, with clear profiles and source bindings.
- Render from pinned source/dependency snapshots using per-frame receipts.
- Complete the Animation viewer, native-frame timeline, frame/range feedback and shot navigation.
- Publish completed frames immediately, then independently decodable segments from contiguous completed ranges.
- Expose pending/available/failed timeline ranges and actual rendering state before any media is available.
- Preserve annotation/frame identities when additional segments and the eventual complete movie arrive.

### Acceptance

1. The shot cards resolve to the authored scene/camera/frame bindings, not mockup placeholders.
2. The user toggles between static 3D inspection, keyframes and animation in one central viewer.
3. Before completion of a bounded render, the user can inspect/annotate an available frame and play a completed contiguous range.
4. Seeking into a pending range shows it as unavailable. Playback does not silently skip that range or show an old version under the new version's label.
5. Drawing pauses/captures the actual displayed frame. Saved markup and inclusive ranges retain the source/job/frame and displayed-media identity.
6. New render output updates availability without moving the playhead, clearing a draft or changing a saved annotation's target.
7. A final movie maps back to the same rendered frames, keeping pre-completion notes interpretable.
8. Actual time to first reviewable frame/segment, throughput, native frame cadence and resource use are recorded for a representative fixture.

### Failure and recovery checks

- Failure after partial output leaves those completed frames reviewable with an incomplete status.
- Changing the live source while a pinned job runs cannot mix old/new frames into that job.
- Failed/partial image writes are not published as usable frames. A gap in receipts is not inferred as completed work.
- A low-detail motion preview does not resolve a lighting/texture review finding unless suitable keyframes were examined.

**Reuse:** Existing serial renderer/worker/input fingerprints/frame receipts, FFmpeg assembly and Review Studio frame/range controls. Progressive segment publication and its viewer are new integration work.

## M09: Request and approve VLM critique

**User journey:** “I or Copilot request another visual assessment, then I choose which findings to send back for revision.”

**Requirements:** FR-11, FR-03, FR-06.

### Scope and implementation

- Add a local VLM adapter using a configured LM Studio endpoint/model and a structured finding contract.
- Expose review requests to the user and Copilot. Do not review every publication automatically.
- Associate selected image sets and available telemetry with an exact source/render target.
- Display actionable finding cards, with selection, amendment and rejection. Submit approved findings together through the project conversation.
- Include relevant original images and UI context with the approved critique, allowing the cloud lead to reason from both.

### Acceptance

1. A user-requested and a Copilot-requested review each reach the configured local service and produce attributed findings.
2. Findings distinguish visual inference from measurements supported by scene telemetry.
3. The user selects/amends a subset and submits it once. Copilot receives only those approved instructions together with their images and exact target context.
4. Neither the VLM nor the approval UI directly executes a Blender fix.
5. No review runs merely because an asset, scene or new frame was published.
6. Declining a finding leaves source files unchanged. An unavailable VLM leaves normal authoring and existing-output review available.

### Failure and recovery checks

- Malformed or partially streamed findings stay pending/failed rather than becoming executable advice.
- A finding about an old version is not silently relabelled as a finding about the latest scene.
- Repeating an acknowledgement does not submit approved critique twice.

**Reuse:** Review-note identity/persistence and reference production-review criteria. The local adapter and approved-critique hand-off are new integration work.

## M10: Image-to-3D asset generation

**User journey:** “I select a reference/concept image and ask Copilot to create a reusable 3D asset from it.”

**Requirements:** FR-12, FR-08, FR-04.

### Scope and implementation

- Use E04's real-hardware result to choose a model/workflow with an appropriate licence and supported runtime.
- Adapt the tool interface for job submission, history/status and output acquisition, preferably through the configured ComfyUI service where a working graph exists.
- Record the conditioning image, graph/model/settings, actual mesh outputs and limitations.
- Let Copilot inspect/import the mesh, prepare scale/origin/materials as needed and save a canonical editable Blender asset with a source checkpoint.
- Publish the asset for static inspection and rendered-keyframe feedback. Existing scene dependencies remain unchanged until Copilot adopts it.

### Acceptance

1. A real image-to-3D run on the intended workstation produces a mesh file, not just a generated image, thumbnail or download link.
2. The mesh opens in Blender with recorded bounds/scale, finite geometry, material/texture availability and usable object identity.
3. The user can inspect the resulting asset and request a normal Copilot revision/reuse through the existing workflows.
4. Peak resource observations and actual generation/import limitations are recorded. No untested clean topology, retopology or rigging claim is made.
5. Job recovery and source publication obey the same retry/version rules as other generation and authoring tools.

### Failure and scope decision

- If no candidate works, preserve the proof evidence and mark the capability blocked/unavailable.
- Procedural authoring and online asset import remain usable, but they are not reported as successful image-to-3D.
- The owner must explicitly accept a V1 scope change or select further feasibility work. Do not mark this milestone complete by silently deferring the feature.

**Reuse:** ComfyUI execution/provenance and Blender import/publication patterns. No working mesh-generation graph currently exists in the inspected references.

## M11: Playable Unity scene

**User journey:** “I ask for this environment to be playable, then open the result in Unity and walk through it.”

**Requirements:** FR-13, FR-01, FR-02.

### Scope and implementation

- Register a Unity native subagent/profile with the existing CLI and generalised C# Editor integration tools.
- Consume pinned Blender scene/asset versions and perform necessary export, material preparation and collider setup.
- Adapt a minimal player/controller and camera from the tested code without private character, wardrobe or SMAL-specific gameplay dependencies.
- Save and validate the Unity scene with a safe spawn, movement, camera control and floor/wall collision.
- Publish the Unity project/scene revision, source-input mapping and real validation results into the project catalogue/conversation.
- Keep playtesting in Unity Editor Play mode. Resolve active Editor/batch-mode constraints through the specialist/tools.

### Acceptance

1. The frontier lead can delegate the task to a native Unity specialist with a configured model/tool profile.
2. The saved Unity scene uses the requested Blender source versions, tested units/axes/material mapping and preserved `.meta` GUIDs.
3. In Play mode, basic movement and camera input work, the player stands on the floor, walls block movement and reset/spawn is safe.
4. Importing the same declared result again does not duplicate scene objects or silently replace unrelated project content.
5. An active/dirty Editor is respected rather than force-closed. Batch operations use a supported context or clearly report the required action.
6. Proscenium reports the actual saved scene and validation result. It does not expose a fake embedded game or cinematic hand-off.

### Failure and recovery checks

- Missing Unity installation/licence/module/import support is an explicit tool failure.
- A failed import or compilation leaves the previous working project state recoverable through checkpoints.
- No new gameplay, NPC systems, wardrobe, cinematics-to-gameplay transition or browser build is added as an implicit import step.

**Reuse:** `smal-ai-game/tools/game.py`, export helpers, C# Editor APIs, player/camera logic and relevant validation tests. Generalise hardcoded paths, scene checks and broad process guards.

## M12: Reconnect and explicitly resume

**User journey:** “I disconnect or restart the workstation, return to the same project and deliberately continue its unfinished work.”

**Requirements:** FR-14, FR-02, FR-03, NFR-03.

### Scope and implementation

- Finish durable public-event replay and saved UI/review restoration across browser/SSH-tunnel loss.
- Run accepted application work independently of browser request/socket lifetimes.
- Restore project/session/publication records after backend restart without sending model turns automatically.
- Add explicit Resume with the unfinished request, context, known tool/job IDs and current output receipts for Copilot to inspect.
- Reconcile surviving Blender workers, ComfyUI prompt IDs and Unity results without assuming stale flags mean live work.
- Define safe project-switch behaviour while busy, keeping one active project and making pending external jobs visible.
- Document consistent backups of SQLite, native Copilot session state and retained media, separately from Git/LFS source history.

### Acceptance

1. Closing the browser/SSH tunnel during a task does not cancel accepted work. On reconnect, its events and published outputs appear once.
2. A backend restart restores the project and shows Resume. No model request, generation submission or authoring operation is replayed automatically.
3. On Resume, an already completed external job is reconciled instead of duplicated. A surviving worker is identified by more than PID alone.
4. An uncertain tool outcome remains uncertain until inspected. A lost exit code is never rewritten as zero.
5. Pending/saved annotations, source/media targets and queued-turn context survive reconnect/restart as applicable.
6. Switching projects while busy requires an explicit settle/stop/wait choice. It does not launch a second active project or kill unrelated applications.
7. A backup/restore test in disposable storage restores conversation association, catalogue and feedback with the required media/source dependencies.

### Failure and recovery checks

- Simulate acknowledgement loss separately for messages, publications and note saves.
- Simulate restart during rendering and after output creation but before the application records completion.
- Resume failures, unavailable native session data and missing retained media are reported with recovery limits, not hidden by creating an unrelated new conversation.

**Reuse:** Blender worker identity/journal recovery, ComfyUI prompt-ID reconciliation and Review Studio outbox/conflict handling. Do not import historical timers, schedules or legacy recovery controllers.

## M13: Assemble, review and deliver a silent cinematic

**User journey:** “I select the shots, review the complete edit and export the silent film with its source references.”

**Requirements:** FR-15, FR-07, FR-09.

### Scope and implementation

- Let Copilot maintain a versioned sequence of explicitly chosen shot/render versions, trims, timing and transitions.
- Assemble the selected frames/clips through the existing FFmpeg approach with recorded transformations.
- Provide whole-sequence playback alongside shot/frame/range navigation in Animation mode.
- Keep draft, final and owner-selected states distinct, with actual render/encode metadata.
- Publish a complete silent video and delivery manifest, with any requested per-shot clips/keyframes as associated outputs.
- Preserve earlier sequence versions and original review mappings when shots are retimed, replaced or reordered.

### Acceptance

1. The assembly uses the selected shot versions and declared timing, with no missing range presented as a completed film.
2. The final artefact fully decodes and matches measured frame count, frame rate, duration and dimensions.
3. Playback crosses every shot boundary correctly, with stable frame/range targeting and no feedback silently attached to a different edit.
4. The cinematic has no audio stream. H3 ambience or source-file audio is not accidentally inherited.
5. A changed edit produces a new version/artefact identity. Existing annotations remain bound to the old mapping unless explicitly carried with a tested mapping.
6. The user can review/download the complete delivery while the source and output manifest remain traceable.
7. Final visual selection is recorded only from the user's decision, not inferred from technical verification or an empty note list.

### Failure and recovery checks

- Missing/corrupt shot output stops complete delivery publication and identifies the exact gap.
- Assembly never writes through source image symlinks or overwrites accepted media under the same identity.
- Interrupted assembly can reuse verified inputs without rerendering unchanged shots or pretending an incomplete MP4 is a final delivery.

**Reuse:** Existing assembler/full-decode checks, per-cue source records, Review Studio scene mapping and lessons about keeping selected delivery separate from newer authored sources.

## M14: Consolidated desktop studio acceptance

**User journey:** “I start the studio on my workstation, connect over SSH and use every included V1 workflow without depending on the old project checkouts.”

**Requirements:** FR-16, NFR-01, NFR-02, NFR-03, NFR-04, NFR-05, NFR-06 and cumulative functional coverage.

### Scope and implementation

- Package host-native startup, user-level service operation and configuration, with FastAPI serving compiled UI assets from one loopback origin.
- Complete the 1920 × 1080 Linear Minimal workspace, keyboard controls, internal scrolling, readable activity and meaningful loading/error states.
- Make Copilot models/tool profiles, local endpoints, project locations and output/cache locations configurable.
- Verify local and SSH-forwarded browser use without direct browser calls to workstation localhost services.
- Adapt/carry required tools, skills, tests and licences into maintained Proscenium components. Remove runtime dependencies on reference-project checkouts, secrets and active media.
- Define media retention and backup procedures that preserve actual submitted annotation targets and selected deliverables. Keep automatic destructive cleanup out of V1.
- Measure representative memory/render/proxy/inference performance on the actual host, then set defensible workload-specific targets.
- Produce operator and development/test instructions for the implemented interfaces.

### Acceptance

1. A clean project can complete the authoring, review, storyboard/image, critique, cinematic and Unity journeys using the configured tooling.
2. Image-to-3D either passes M10 or has an explicit owner-approved scope decision. An unresolved failure cannot disappear from the release record.
3. A full desktop browser pass at 1920 × 1080 confirms one central review mode at a time, no obstructed primary controls and no fake progress/telemetry.
4. SSH-forwarded use exercises messages, events, byte-range playback and uploads through the single Proscenium origin.
5. Existing output review and feedback saving remain usable when Copilot, LM Studio or ComfyUI is unavailable. Each unavailable capability is shown accurately.
6. File-serving tests reject environment files, Git metadata, databases, arbitrary paths and unsafe traversal/symlink routes. No provider credentials are exposed in frontend assets or event logs.
7. Frontend assets/fonts do not require the mockup's CDNs. No public ingress, mobile layout, multi-user roles or parallel active projects have been introduced.
8. Unit/contract, tool integration and browser suites pass using isolated data. Actual device/runtime/model identities accompany performance and capability claims.
9. The source repository and user-project workspaces keep secrets, environments, runtime stores and disposable generation caches out of source checkpoints.

### Failure and recovery checks

- Run the cross-journey scenarios below with unavailable services, interrupted connections and missing retained media. Show the real limitation instead of silently generating replacements.
- A clean-workspace failure must identify the missing configuration/tool/input, not depend on a reference checkout or borrow its secret configuration.
- A capability that fails acceptance stays incomplete until fixed or explicitly removed from V1 by the owner.

**Boundary:** Readiness is a verified implementation plus the owner's selected scope. It does not authorise publishing repositories, redistributing licensed sources, merging changes or exposing the service publicly.

## 4. Cross-journey acceptance scenarios

These scenarios catch integration failures that isolated milestone tests can miss.

| Scenario | Milestones | Required invariant |
| --- | --- | --- |
| Change selection after queuing a request | M02, M03, M04 | Original per-turn target remains unchanged. |
| Revise an asset used by two existing scenes | M02, M05 | Neither scene adopts the new version without Copilot making that scene change. |
| Start a render, then keep authoring | M02, M08 | Render reads pinned sources and cannot mix later edits. |
| Annotate a partially rendered animation | M04, M08, M13 | Target survives later frame publication and complete assembly. |
| Review old output while a new version publishes | M02, M04, M08 | Current catalogue updates do not rewrite the reviewed content identity. |
| Submit approved critique while other work runs | M03, M09 | Images/findings/target are captured once and follow the selected Send/Steer semantics. |
| Timeout after ComfyUI accepted a workflow | M07, M10, M12 | Recorded/uncertain job identity is reconciled before resubmission. |
| Reconnect after source publish but before preview completes | M01, M08, M12 | Publication appears once, with truthful independent preview progress. |
| Reorder or trim a reviewed sequence | M06, M08, M13 | Old frame notes stay on the original sequence mapping. |
| Export to Unity while Blender/Unity are open | M05, M11, M12 | Correct instance/project is targeted and unsaved work is preserved. |
| Copy/restore a project to a new location | M02, M12, M14 | Relative source references, LFS content, session association and retained review media have explicit recovery behaviour. |
| Open the application with generation services offline | M07, M09, M14 | Existing catalogue/media/feedback work without silently starting services. |

## 5. Remaining implementation selections

These are bounded implementation decisions, not reasons to repeat the architecture discussion before M01.

| Selection | Resolve in | Constraint |
| --- | --- | --- |
| Actual SDK/runtime versions and lead/worker model IDs | E01, M01, M03 | Discover through the user's current Copilot access, pin and test. |
| Explicit native permission policy | M01, M03 | Support the intended tool use without accidental unrestricted defaults or a new Blender gatekeeper. |
| Final schema field names and migrations | M01, M02 | Preserve stable identity, pinned dependencies and separate execution/review/publication states. |
| Canvas implementation | M04 | Must preserve editable markup, masks, image coordinates and captured targets. |
| Proxy export/instance IDs and exact coordinate mappings | E02, M04 | Test real Blender → proxy → camera/image mapping, then Unity conversion. |
| Progressive segment length and buffering | E03, M08 | Measure first-review latency, frame accuracy and encoding overhead. |
| Local VLM model/API capabilities | M09 | Use the configured service and structured evidence, without assuming the old Qwen model. |
| Image-to-3D model/workflow/runtime | E04, M10 | Real hardware support, appropriate licence and actual Blender-importable mesh. |
| Render profiles and device choices | M01, M08, M14 | Distinguish purpose and measured capability. No fixed 128 GB allocation or untested HIP guarantee. |
| Minimum Unity controller/import setup | M11 | Basic movement/camera/collision only, using tested formats and no private content dependency. |
| Retained media and backup locations | M02, M12, M14 | Git source history alone does not preserve runtime feedback or local-only reviewed images. |

## 6. Implementation starting point

Start M01 with E01 and the minimal source/publication/context contracts, then demonstrate the real Blender feedback loop. Preserve this complete milestone map while implementing that first slice.

Do not begin by recreating a Blender daemon, deploying a vector database, installing a second agent framework, rebuilding existing shared services or expanding the V1 gameplay scope.
