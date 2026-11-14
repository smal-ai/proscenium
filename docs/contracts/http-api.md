# HTTP and tool API 1.0

The machine-readable operation catalogue is [openapi.yaml](openapi.yaml). Its request/response records reference [schema.json](schema.json). This document adds normative behaviour.

## 1. Origin and caller identity

Serve the compiled frontend, API, WebSocket and media from the same configured loopback origin. SSH forwards that origin to the user's desktop browser. Validate Host/Origin on browser requests and WebSocket upgrades. Reject cross-origin mutations and unexpected hosts; CORS or a working directory is not an authorisation mechanism.

There are three authenticated caller channels:

| Channel | Authority |
| --- | --- |
| Owner browser | Queue/Steer/Stop, Resume, uploads, feedback, selections and permission answers. |
| Native tool adapter | Only its admitted active project/work operations, including publication, generation registration and review requests. |
| Worker | Receipts/status for the exact admitted job and input identity. |

An agent cannot approve its own permission request by calling an owner endpoint. Loopback alone cannot establish that a request came from the human rather than a local tool process.

Use a local owner-session capability established through the trusted SSH/operator channel. Initial pairing uses an unpredictable one-use code, delivered outside agent context (for example in the operator's launch URL fragment), with a 15-minute expiry. `/api/v1/owner/session` exchanges it for an HttpOnly, SameSite=Strict owner cookie and an anti-CSRF token. Match allowed origins for all writes. Default owner-session lifetime is 12 hours. No external account-registration service is required.

Pairing retry is bound to its original operation ID and client nonce. A consumed pairing code cannot create a different client session. Pairing codes/cookies/CSRF tokens and tool/worker capabilities never enter project context, agent-readable files or public event logs. Tool and worker routes use separate scoped capabilities or equivalent in-process authenticated bindings. A supplied `actor` string cannot elevate a caller.

F00 must prove separation of these channels together with the project-only filesystem policy. The existing unrestricted shell/MCP process is not assumed to enforce it merely because the web server is private.

## 2. Commands and responses

- JSON content type is required for JSON mutations. Binary image upload uses its actual media type.
- Every mutation carries an `Idempotency-Key`. It equals its body `operation_id`, `request_id`, `publication_id`, `review_id` or `job_id`, as identified by the operation. Context creation uses `context_id`; worker receipts use `receipt_id` or `segment_id`. Binary image upload uses the path's `upload_id`.
- Store the request fingerprint and resulting resource identity before acknowledging. A matching retry returns HTTP 200 and the existing identity. Conflicting reuse returns HTTP 409 `idempotency_conflict`.
- First creation returns 201; accepted asynchronous work returns 202. Neither code means that authoring, inference or rendering succeeded.
- A mutation with `expected_revision` uses compare-and-swap. A stale value returns 409 `revision_conflict`, including the current revision but no secret data.
- A response identifies a durable resource. If the connection is lost, the caller reads/retries that identity rather than creating a new command.
- Standard read endpoints do not create SDK sessions, launch providers or regenerate missing media.

The publication tool is named `publish_outputs`; its input is `PublicationSubmit`. The `request_vlm_review` tool returns only `ReviewTicket`. Unapproved findings are available through the owner review route, not in the model-facing tool result. Generation/tool adapters record jobs under their original work and budget identity.

## 3. Operation catalogue

| Operation | Request | Response / behaviour |
| --- | --- | --- |
| `GET /api/v1/configuration` | — | `ConfigurationView`; configured profiles/endpoints and known availability, with no secrets or service startup. |
| `POST /api/v1/projects` | `ProjectOpen` | Create/open a chosen project, `ProjectRecord`. Creates no model work. |
| `GET /api/v1/projects` | Pagination | `ProjectPage`; owner-only saved-project metadata. |
| `POST /api/v1/projects/{project_id}/activate` | `RevisionCommand` | `CommandReceipt`; previous project must be settled. |
| `POST /api/v1/projects/{project_id}/rebind` | `ProjectRebind` | `CommandReceipt`; settled project only, verifies the already moved/copied matching workspace and changes its registry location. |
| `GET /api/v1/projects/{project_id}/snapshot` | — | `ProjectSnapshot` and its consistent event watermark. |
| `POST /api/v1/projects/{project_id}/contexts` | `ContextSnapshot` | Immutable snapshot, returned with 201. All referenced media must be ready. |
| `GET /api/v1/projects/{project_id}/contexts/{context_id}` | — | Exact stored `ContextSnapshot`. |
| `POST /api/v1/projects/{project_id}/requests` | `MessageSubmit` | `RequestReceipt`. Queue/Steer/Stop follow the lifecycle protocol. |
| `GET /api/v1/projects/{project_id}/requests` | Pagination | `RequestPage`. |
| `GET /api/v1/projects/{project_id}/requests/{request_id}` | — | Current `RequestReceipt`; no resubmission. |
| `POST /api/v1/projects/{project_id}/requests/{request_id}/cancel` | `RevisionCommand` | Cancel an application-held request. Active work requires Stop. |
| `POST /api/v1/projects/{project_id}/resume` | `ResumeCommand` | `CommandReceipt`; explicit recovery/release action. |
| `PUT /api/v1/projects/{project_id}/settings` | `ProjectSettings` | `CommandReceipt`; applies to future work. In-flight jobs retain admitted settings. |
| `GET /api/v1/projects/{project_id}/items` | Pagination, optional kind | `ItemPage`. |
| `GET /api/v1/projects/{project_id}/items/{item_id}/versions` | Pagination | `VersionPage`. |
| `GET /api/v1/projects/{project_id}/jobs` | Pagination | `JobPage`. |
| `GET /api/v1/projects/{project_id}/jobs/{job_id}` | — | `JobRecord`, including the admitted render specification where applicable. |
| `GET /api/v1/projects/{project_id}/jobs/{job_id}/availability` | — | `FrameAvailability`; available/pending/failed partition plus playable subset. |
| `GET /api/v1/projects/{project_id}/jobs/{job_id}/frames` | Pagination, optional `from_frame`/`to_frame` | `FramePage`; source-frame ordered ready receipts. |
| `GET /api/v1/projects/{project_id}/jobs/{job_id}/segments` | Pagination | `SegmentPage`; exact ready segment mappings. |
| `POST /api/v1/projects/{project_id}/jobs/{job_id}/cancel` | `RevisionCommand` | `CommandReceipt`; cancellation requested, not presumed completed. |
| `PUT /api/v1/projects/{project_id}/uploads/{upload_id}` | PNG/JPEG/WebP bytes | `Artifact`; decode/hash/limits checked before ready. |
| `GET /api/v1/projects/{project_id}/artifacts/{artifact_id}` | — | `Artifact` metadata, never a caller-selected filesystem path. |
| `GET/HEAD /api/v1/projects/{project_id}/artifacts/{artifact_id}/content` | Optional Range | Verified bytes, ETag, dimensions/media headers as applicable. |
| `POST /api/v1/projects/{project_id}/annotations` | `AnnotationSave` | `CommandReceipt`; immutable target, revisioned content. |
| `GET /api/v1/projects/{project_id}/annotations` | Pagination, optional target | `AnnotationPage`. |
| `POST /api/v1/projects/{project_id}/selections` | `SelectionSave` | `CommandReceipt`; owner selection/supersession/withdrawal. |
| `GET /api/v1/projects/{project_id}/selections` | Pagination | `SelectionPage`; current owner choices by slot, with revision history retained in events. |
| `POST /api/v1/projects/{project_id}/reviews` | `ReviewRequest` | `ReviewTicket`; no automatic source change. |
| `GET /api/v1/projects/{project_id}/reviews/{review_id}/findings` | Pagination | `FindingPage`; owner channel only. |
| `POST /api/v1/projects/{project_id}/reviews/{review_id}/findings/{finding_id}/reject` | `RevisionCommand` | `CommandReceipt`; owner rejects that exact finding revision without source mutation. |
| `POST /api/v1/projects/{project_id}/decisions/{decision_id}/answer` | `DecisionAnswer` | `CommandReceipt`; exact fingerprint/revision and real owner authority. |
| `GET /api/v1/projects/{project_id}/events` | `after`, `limit` | `EventPage`, ordered by durable application sequence. |
| `POST /internal/v1/artifacts` | `ArtifactRegister` | Trusted tool-owned staging → immutable `Artifact`. |
| `POST /internal/v1/publications` | `PublicationSubmit` | `PublicationReceipt`; same contract as `publish_outputs`. |
| `POST /internal/v1/previews` | `PreviewRequest` | `JobRecord`; same contract as `request_preview`, with no source/head change. |
| `POST /internal/v1/generation-jobs` | `GenerationSubmit` | `JobRecord`; reserve attempt before dispatch. |
| `POST /internal/v1/reviews` | `ReviewRequest` | `ReviewTicket`; model-facing result contains no findings. |
| `POST /internal/v1/frame-receipts` | `FrameReceipt` | `CommandReceipt`; exact admitted job and output hash required. |
| `POST /internal/v1/segments` | `PreviewSegment` | `CommandReceipt`; exact contiguous frame/encode mapping required. |

Project `paths` are directories and may use `.` for the root, including a Unity project whose root is the active project itself. Source/artefact file paths must name a file and cannot use that directory shorthand. `ShotPlan` is a wire projection: its source commit is attached from the published version, avoiding a self-referential commit field inside the source plan file.

Internal routes are a protocol boundary, not a requirement to put an extra HTTP hop inside the same process. SDK handlers may invoke the same authenticated service operation directly. Owner routes are not automatically exposed as agent tools.

An owner answer is persisted in the decision's `response` with `resolved_by`. Native-backed decisions also record `native_decision_id` and `delivery_state`. Saving the owner's choice is not evidence that its native callback was acknowledged. Reconcile uncertain callback delivery against that native decision before answering it again. A rejected VLM finding can be superseded by a later explicit owner submission; original observation text and status history remain retained.

## 4. Pagination and event recovery

Lists use a keyset cursor bound to project, filters and sort order; default limit 50, maximum 100. `next_cursor: null` ends traversal. Cursors cannot be reused against another project. Events use their monotonic integer sequence and a separate `after` cursor; zero means the beginning.

A snapshot is read consistently with `event_sequence = N`. Subscribe after N to avoid losing publications between initial fetch and socket attachment. If a cursor is unavailable or from an incompatible journal epoch, return `cursor_expired` and fetch a fresh snapshot. Do not treat an empty page as proof that an uncertain native request never executed.

The progressive viewer can recover interval/frame/segment state through the job read endpoints without replaying the entire project journal. Available, pending and failed ranges form a non-overlapping partition of the expected source interval. Playable ranges are a subset of available frames covered by ready encoded segments. Ranges are sorted and coalesced. Frame rate fractions are reduced to lowest terms before fingerprinting.

## 5. Media serving

Serve only registered ready artefacts through ID lookup and validated managed roots. Support GET/HEAD and a single byte range with 206/416 responses, `Accept-Ranges: bytes`, exact Content-Length and an ETag derived from the immutable hash. Reject symlink/traversal escape and changed bytes. Do not expose staging files, unreceipted frame writes, `.git`, databases or environment files.

Use private cache semantics appropriate to owner-authenticated immutable media. JSON command/read-model responses are not stale media-cache aliases. A new encode/version gets a new artefact identity. Existing review URLs do not begin serving new bytes.

## 6. Error catalogue

| HTTP | Codes and meaning |
| --- | --- |
| 400 | `invalid_request`, `invalid_state_transition`, `invalid_cursor`: malformed data or impossible state transition. |
| 401 | `owner_session_required`, `invalid_pairing`, `capability_required`: missing/invalid caller authentication. |
| 403 | `scope_denied`, `origin_denied`, `permission_denied`: project, origin or approved-action boundary. |
| 404 | `not_found`: no permitted record under that ID/project. |
| 409 | `idempotency_conflict`, `revision_conflict`, `publication_head_conflict`, `source_conflict`, `dependency_collision`, `project_busy`, `project_location_conflict`, `owner_conflict`, `no_active_work`, `stale_decision`, `attribution_unavailable`: retain current state and resolve explicitly. |
| 410 | `cursor_expired`: obtain a fresh snapshot. Retained unavailable media remains a known record rather than becoming a different file. |
| 413 | `upload_too_large`, `image_dimensions_exceeded`, `attachment_limit_exceeded`: configured input limit exceeded. |
| 415 | `unsupported_media_type`, `unsupported_contract`, `unsupported_repository`: unsupported format/version. |
| 422 | `source_unavailable`, `retained_source_missing`, `dependency_missing`, `hash_mismatch`, `frame_conflict`, `invalid_frame_mapping`, `media_unavailable`: valid shape with unusable references/content. |
| 429 | `generation_budget_exhausted`: wait for an explicit allowance change/new human task. |
| 500 | `internal_error`: unexpected failure with a redacted message and correlation. Preserve any uncertain operation outcome; do not imply that retrying a fresh effect is safe. |
| 501 | `cancel_unsupported`, `capability_unavailable`: requested feature not supported by the selected integration. |
| 503 | `provider_unavailable`, `native_session_missing`, `scope_unverified`, `storage_unavailable`: capability unavailable, no fabricated result. |
| 507 | `storage_full`: preserve completed data and require storage recovery. |

`outcome_uncertain` is a persisted resource state, not a reason for automatic retries. An asynchronous operation failure is represented on its request/job and corresponding event even if the original HTTP acceptance was 202. Error `retryable: true` permits retrying a read or the same known operation identity; it never authorises a fresh inference submission.
