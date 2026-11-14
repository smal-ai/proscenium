# Review, ingestion and progressive media

**Normative for contract 1.0.** Browser acceptance is Chromium, Brave and Edge on the reference Linux workstation.

## 1. Ingestion and attachment lifecycle

Browser upload and project-folder opening are different actions. A remote browser uploads selected bytes to Proscenium; it does not supply a local browser-machine path for the workstation to open. Because `<input type="file" webkitdirectory>` reads the client laptop's disk over an SSH-forwarded session, opening, creating or rebinding a project in the UI uses the saved-project selector (`GET /api/v1/projects`) plus a simple workstation path text input (pre-filled with the configured default projects root, e.g. `~/play/`) validated server-side by `POST /api/v1/projects` and `POST /api/v1/projects/{project_id}/rebind`.

V1 browser uploads accept PNG, JPEG and WebP images. Default limits are 25 MiB and 40 million decoded pixels per image, eight images and 100 MiB total per request. These are configurable input limits, not model capability claims. F00 also establishes the configured model's supported attachment limits. Oversized input returns a specific error before a Queue request is accepted; it is not silently omitted.

Upload has a caller-created immutable ID. The binary endpoint stages bytes under that ID, verifies type by decoding rather than extension, computes SHA-256 and commits an artefact record. Same ID/same bytes is a retry; same ID/different bytes is `idempotency_conflict`. Interrupted upload is `incomplete` and cannot be attached. No archive, executable or SVG is accepted by this image-upload route. Asset acquisition/import remains a separate scoped tool workflow.

Keep original bytes. Where EXIF orientation/colour conversion is necessary, create a display derivative with an explicit transformation and hash. Markup targets the displayed derivative's dimensions. Canonical masks are single-channel 8-bit greyscale PNG, with no alpha or colour-profile reinterpretation: 0 is unselected and 255 is fully selected. `MaskEncoding.semantics` is `binary` (only 0/255) or `soft` (all integer values 0–255, interpreted as coverage divided by 255). Dimensions and orientation equal the target image. Coloured/transparent overlay previews are separate markup images. Adapters explicitly convert this convention for providers with different polarity/channels and record the derivative. A filename or alpha channel is not an implicit mask convention.

An upload is not automatically a creative selection or an LLM attachment. The user or an in-scope agent action assigns its role (`reference`, `concept`, `render`, `markup`, `mask`) and target. The Send snapshot contains only the chosen context/attachments. The agent can subsequently inspect other relevant permitted project content under [owner policy](owner-policy.md).

## 2. Viewer behaviour

One central viewer switches between **3D Inspection**, **Keyframes** and **Animation**. Available Blender keyframes are the default. Concept references have explicit source labels and can be inspected through the image-review surface without pretending to be material evidence.

- A mode change loads available data only. It does not dispatch rendering or generation.
- The outliner lists the published proxy mapping. Selecting a row sets the same `selected_objects` pairs (`object_id`, `instance_id`) as a proxy pick. It does not start a render. In Keyframes or Animation, an outliner selection is context for the next message, not a spatial hit, unless the proxy version and sample frame match. Distinct instances of one source object remain distinguishable even when no raycast is available.
- The current published head can change while the viewer stays pinned to an older version. Show a newer-version indicator; do not jump automatically.
- Before starting markup, pause playback/orbit and obtain the actual displayed-frame/camera capture. A requested seek is not proof that its target frame has been presented.
- A draft pins that capture at creation. Send freezes the full UI context, including each draft's own target. Later selection changes never retarget an earlier draft.
- Selecting a saved annotation restores its target version, mode, image/camera/frame and range. Missing media shows `media_unavailable` with its identity; regeneration is an explicit request and creates a new derivative.

## 3. Frame and coordinate convention

- Source frame numbers are one-based. Ranges are inclusive and identify a single shot/job unless an explicit sequence mapping is supplied.
- Frame rate is an exact positive rational `{numerator, denominator}`. A timestamp string is display text, not identity.
- For a segment starting at source frame `s` and rate `n/d`, frame `f` starts at `(f-s)*d/n` seconds and ends at `(f-s+1)*d/n`. Seek to its centre for a paused-frame request, then confirm the displayed frame.
- Sequence frame, shot frame and source frame are separate domains. A `SequenceMap` has stable `entry_id` values and an ordered, non-overlapping partition of destination frames 1 through `frame_count`. A dissolve occupies one transition entry, rather than overlapping two top-level destination entries. Its `contributors` name at least two shots; the entry's own shot fields name the outgoing shot only for catalogue linking. `choice` omitted means the agent proposed the cut. An owner selection is optional and does not block assembly.
- Store 2D points normalised to the original displayed image bounds, top-left origin, `[0,1]` on each axis. Convert through the actual media-content rectangle, including zoom/pan, letterboxing and DPR. Clip geometry to the image rather than clamping outside-panel gestures into false edge points.
- Stroke widths are image pixels. Retain editable strokes/pins/regions and lossless masks, plus a rendered markup image for model input.
- A range annotation has an **anchor frame containing the drawing** and a separate inclusive temporal range describing applicability. V1 does not track a mask across moving frames automatically.

### Sequence frame maps and composite targets

`FrameMap` is the typed JSON payload of a `frame_map` artefact. It expands the complete sequence into one ordered row per destination frame, including ordinary cuts. Each row lists the exact source job/version/shot/frame contributors and reduced rational weights whose sum is exactly one. Weights may be zero at transition endpoints. The source job/frame resolves to its immutable frame receipt; a complete delivery/review cannot depend on unavailable contributing frames.

Identity/trim entries preserve frame count and use one contributor of weight 1. V1 retiming uses explicit hold/drop mappings, with one source frame per output frame. A transition has at least two declared shot contributors per row, blends their decoded images in linear RGB and then encodes the review output as sRGB. No optical-flow interpolation, inferred frame rounding or unspecified blend curve is implied. `retime` and `transition` entries require `mapping_artifact_id`; the referenced full `FrameMap` supplies their rows. Its project, sequence version, frame count and FPS must agree with `SequenceMap`. Source frames must be within the corresponding entry/contributor intervals.

Reviewing an assembled sequence uses `FrameTarget.origin: "sequence_render"`, with `sequence_version_id`, `sequence_frame` and `frame_map_artifact_id`. Its `version_id` is the sequence version. `source_frame`, `shot_id` and `render_job_id` are null: source contributors are resolved through the map, including for a dissolve. The target's image/media hashes still identify what was actually displayed. Range annotations on this target use sequence-frame ordinals; other targets use source-frame ordinals. A range cannot silently change domain when carried to a different target.

### Spatial representation

Use metres. Wire matrices are row-major, multiply column vectors, and include their named space. The canonical world conversions are:

```text
Blender B(x,y,z) -> proxy P(x,z,-y)
Proxy   P(x,y,z) -> Unity U(x,y,-z)
Blender B(x,y,z) -> Unity U(x,z,y)
```

These are product representation conventions, not assumptions that arbitrary exporter/importer flags implement them. Adapters normalise their effective transforms to this contract and record the applied mapping. A reflection requires correct winding/normal handling. Normal conversion uses the appropriate inverse-transpose for an object's transform and is renormalised.

Camera records use Blender-world metres, camera-local +X right/+Y up/−Z forward, a camera-to-world matrix and an OpenGL-style projection matrix with clip-depth range `[-1,1]`. Record projection kind, aspect, clipping and the optical parameters needed to recreate it. Convert a proxy camera's **world basis** back to Blender before constructing the record; do not blindly apply an object-basis conjugation to camera-local axes. The projection/framing fixtures are authoritative checks.

Optional `shift` is the principal-point displacement from image centre in normalised image units, positive right/down. It is derived from the projection and is not a raw Blender `shift_x`/`shift_y` value. The worker must recreate the authoritative projection through the selected camera API and verify correspondence. F02 includes asymmetric projection vectors.

Camera-to-world poses are rigid metre-space transforms: orthonormal orientation, no scale/shear, and translation in metres. V1 output pixels are square. Camera aspect must match the rendered content rectangle; any subsequent crop/letterbox is an explicit derivative transform rather than a silent framing change.

A hit references exact proxy/mapping artefacts, source closure, sample frame, camera and stable object/instance IDs. A mismatch yields an explicit absent/invalid spatial hit and retains image-space annotation. Static proxy positions cannot represent a different animation frame merely because the object name matches.

## 4. Progressive animation

Use immutable frames and independently decodable short segments. The initial reference profile is silent H.264/MP4, 8-bit `yuv420p`, fixed dimensions/FPS within a job, with each segment independently seekable from its first frame. Segment duration is selected by E03 measurement. The browser implementation may use a confirmed MediaSource adapter or a prebuffered segment player; the normative behaviour is the same.

1. Register the immutable job/source/shot map before frames exist.
2. Expose only complete, hash-verified frame receipts. The first such frame can be inspected before a segment is encoded.
3. Publish segments only from complete contiguous source intervals. Save exact frame count, rational rate, interval and encoded hash.
4. Maintain an interval map, including holes and failed intervals. Do not infer completion of earlier frames from a high final frame number.
5. During playback, stop/buffer at the first unavailable frame. When the next interval arrives, continue only if playback is still requested. Paused/annotating users remain paused.
6. During seeking, show pending state for unavailable frames without substituting a different version. Preserve the last explicitly identified image where useful.
7. At a segment boundary, show each source frame in order once. E03 must detect blank frames, duplicated/skipped frame IDs and timestamp drift on all three browsers.
8. Keep source/job/frame identities when complete assembly becomes available. A new encode hash does not move annotations. Actual editorial reordering, retiming or compositing creates a new mapping/version.

Draft motion, material-capable keyframes and final delivery use distinct profile identities. Their review frames are the view-transformed sRGB PNG from `review_output`. Full delivery can differ in resolution or samples from a draft, so it is not claimed to have identical pixels. Old annotations continue pointing to their actual reviewed images; any carry-forward records both the original and the new target.

## 5. User selections and VLM findings

`current_published_version` is the latest successful compare-and-swap publication head. `selected_version` is an explicit user choice and can remain older. Agent assessment is neither of these.

A user can select a version for a defined slot (an item, a storyboard frame or an assembly shot), supersede it, or withdraw the selection. Selection updates use an operation ID and expected revision and retain prior events. Selecting a reference does not itself launch generation or update scene dependencies. The agent can choose provisional inputs autonomously within a task, labelled as agent choices rather than owner selection.

`SelectionSlot` is a typed target: an item ID, a storyboard version plus shot/frame ID, or a sequence version plus entry ID. The host keys a slot by project and canonical JSON of that target, with one revisioned selection record per slot. A new `selection_id` cannot bypass that uniqueness or an existing revision. The slot is immutable after creation.

`SelectionChoice` identifies either a published version or a ready image artefact. Item slots require a version of that same item. Assembly slots require a renderable version for that entry. Storyboard-frame slots may select an uploaded/generated image artefact directly, or a version resolving to one explicit image; ambiguous multi-image choices must use the artefact form. Withdrawal sets `choice: null`. All targets and choices must exist in the project.

The host derives a shot frame's `owner_selected` only from the exact storyboard-version/shot/frame slot and a selected choice resolving to that frame's current `artifact_id`. A choice of a different image is visible through selection reads but does not rewrite the authored plan. A new plan version has a different slot; carrying a choice forward is an explicit owner operation, not a boolean copied from an earlier plan or from an agent's `state: selected` value.

For VLM review, the native tool returns `ReviewTicket` with `review_id`, `job_id` and status, and no unapproved recommendation text. The local adapter stores completed findings and emits UI events. A pending/failed review cannot become an executable repair. The user selects/amends findings, chooses Queue or Steer, and submits one immutable message containing the approved text, exact finding revisions, images and captured targets. This atomically marks that submission linkage; a lost acknowledgement cannot submit it twice.

The approved text is the user's instruction. The original VLM observation remains preserved. No finding is approved from a model's claim about user intent. Rejected/withdrawn findings remain review history and are not forwarded as repair instructions.

## 6. Editable feedback and offline drafts

Annotation creation assigns an immutable ID and target. Text, vector/mask contents and lifecycle status are revisioned. A save contains `expected_revision`; stale edits return `revision_conflict` with the current revision. Repeating the same operation and body returns the prior acknowledgement.

Keep an IndexedDB outbox for unacknowledged draft/save operations, including original operation IDs and target references. After reconnect, resend those operations, not reconstructed variants with fresh IDs. Display browser-local, saving, saved, conflict and failed states accurately. Export/recovery is scoped to the original project/media identities.

Retargeting an annotation creates a new annotation with `derived_from`, a mapping and the original retained record. It is never an in-place edit of a saved target.
