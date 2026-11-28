# Contract 1.0.0

- `schema.json` defines the JSON Schema records and native tool envelopes.
- `openapi.yaml` defines the HTTP operations and shared types.
- `state-transitions.json` defines guarded work and job-attempt transitions.

Run `make generate` after an authorised change and `make check-generated` to verify
the backend/frontend bindings. Wire `schema_version` remains `1.0`.

## Compatible UAT extensions

- `setPreviewOrder` (`set_preview_order`) accepts an idempotent `PreviewOrderUpdate`
  for a published version in the active request scope. Listed render jobs move to
  the front; omitted jobs keep their relative order. No frames are rendered again.
  The response and `preview.order_changed` payload are the updated `VersionRecord`.
- Owner `GET /api/v1/configuration/status` returns bounded read-only provider and
  workstation bridge observations (`ConfigurationStatus`), without starting services
  or reserving generation budgets. Configuration remains independently readable.
- Owner `PUT /api/v1/configuration/profiles/{profile_id}` and
  `POST /api/v1/configuration/profiles/{profile_id}/delete` accept revision-checked,
  idempotent `AuthoringProfileUpsert` / `AuthoringProfileDelete` commands and return
  `ConfigurationView`. New requests freeze the selected profile's model identities.

Contract `1.0.0` retains all existing required stored-record fields and lifecycle
edges. Omitted extensions do not alter historical bytes, context fingerprints or
same-ID capture receipts. Consumers must accept historical records without them.

- `ContextSnapshot` optionally carries `request_target`, `reference_versions` and
  `execution_scope`. Project and new-item intent have no published item/version pair.
  Item and shot targets pin exact project-owned versions. Shot identity is checked
  against that storyboard's canonical `AuthoredShotPlan` JSON without writing snapshots.
  Explicit version references never follow a mutable catalogue head.
- Native request payloads expose these fields with read-time defaults of `null`,
  `[]` and `default`. `planning_only` is explicit intent, not inferred from item kind.
  An invocation's admitted request supplies context, including Steer context. Its work
  and originating Queue retain the generation-task identity and frozen allowance.
- Optional `ProjectSnapshot.execution` projects control eligibility, fences, queue
  holds, native child activity/freshness and per-work output state separately from
  `WorkRecord.state`. Unknown native outcomes must not be projected as completed.
- Optional `ActivityEventData` fields are `exit_code`, `display_name`, `output_status`
  and `published_versions`. Missing historical metadata stays absent.
- `getResumeOperation` reads the original operation at
  `GET /api/v1/projects/{project_id}/resumes/{operation_id}`. Admission is not completion.
- `listRetainedArtifacts` reads `RetainedArtifactPage` at
  `GET /api/v1/projects/{project_id}/artifacts`, with `cursor`, `limit`, `kind` and
  `purpose` filters. Each `RetainedArtifactSummary` wraps an actual registered
  artefact, nullable request/work/invocation/job identities, exact published versions
  and `verified` or `unrecorded` attribution. Retention does not invent a catalogue item.
- Native envelopes cover capability discovery (`ProjectCapabilities`), authored
  validation (`AuthoredDocumentValidationRequest` / `AuthoredDocumentValidationResult`)
  and local generation preparation (`ImageGenerationPrepare` / `ImageGenerationPrepared`).
  Validation accepts exactly one path or inline JSON object. The inline `document`
  is deliberately opaque, including invalid authored fields, so the validator can
  return structured issues rather than rejecting those fields at transport admission.
  Schema, semantic and preflight results are distinct. Preparation does not submit
  inference or consume an attempt.
- `ValidationIssue` optionally identifies a pin diagnostic with exact
  `consuming_version` and `reference` pairs and, for shot-local fields, `shot_id`.
  In a publication batch, `path` is relative to the authored document identified
  by `consuming_version`. These details are separate from the static message.
- Native `search_web` accepts a non-blank `query` (up to 512 characters) and an
  optional result `limit` (1–15, default 8). It returns a project-bound
  `WebSearchResult` with ranked `WebSearchHit` records: `title`, exact source
  `url`, bounded `snippets` and nullable `published_at`. This read requires active,
  unfenced work. Hits are discovery leads, not acquired local bytes.
- `ProviderCapabilitySummary` optionally reports nullable `default_dimensions`,
  `default_steps` and `max_pixels`. `ImageGenerationPrepare` permits omitted
  `project_id` and `generation_task_id`, while preserving supplied historical identities.
- `ReviewRequest` optionally carries `title`, `human_review` and `question` without
  changing required fields or historical fingerprints. `ReviewTicket` optionally projects
  `title`, `created_at`, `revision`, `human_review_state`, `awaiting_human`, `question`,
  `finding_count`, `unreviewed_count` and `error_code`. `getOwnerReview` reads
  `ReviewDetail`, `resolveOwnerReview` resolves an open human review, `listOwnerReviews`
  accepts `filter` (`pending` or `all`) with filter-bound pagination cursors,
  `escalateToolReview` (`ReviewEscalate`) attaches an idempotent human review question,
  and `Event` includes `review.changed` alongside `review.ready`.
- `ProxyMapping` optionally reports `mapping_artifact_id`, and `getVersionProxyMapping`
  accepts an optional `mapping_artifact_id` query parameter so spatial annotations can
  retain and reopen an exact proxy map even when a version links multiple proxy passes,
  while existing stored `proxy-map.json` payloads remain valid without migration.
- `RenderSpec` optionally specifies `denoise`. The Cycles CPU worker uses
  OpenImageDenoise by default and records the effective flag in its native profile.
  Set `denoise: false` to disable it. Historical records need no migration.
- `CheckpointRequest` optionally declares `entry_path`, `dependencies`,
  `required_runtime` and `retained_files`. Closure inputs require an entry path and
  are validated before checkpoint intent or Git ref changes. Completed receipts
  include a canonical `source_closure`, with sorted files and pinned dependencies,
  reusable `RuntimeRequirement` and `RetainedClosureFile` definitions, and a verified
  digest. Omitted runtime requirements default to Proscenium `1.0` with no features.
  Legacy receipts omit the closure. Uncertain receipts never claim a completed closure.
- `CheckpointRequest.base_version` optionally pins a published version in the active
  project and inherits its closure files. The request's `paths` and `retained_files`
  replace inherited files at the same destination. `exclude_paths` omits exact inherited
  destinations and cannot overlap the supplied files. Both fields require `entry_path`.
  Dependencies and runtime requirements still come from the new request, not the base.
- `CheckpointRequest.include_sources` optionally pins exact published sources in
  the active project and requires `entry_path`. With a composed base, omission
  retains its direct inclusions, `[]` detaches them and a non-empty list replaces
  them. Composition imports each direct producer's whole sealed file manifest
  at its original destinations and origins, not ordinary dependencies or live
  files. The consumer owns its entry. It can retire a hand-copied base file with
  `exclude_paths`, but cannot exclude one file from an active inclusion.
- `SourceClosure.included_sources` records the sorted, non-empty direct inclusion
  set and is omitted when empty. These pins also appear in `dependencies`.
  Composed closures admit at most 128 includes, 4,096 distinct destinations and
  512 MiB hydrated bytes. A shared destination must have an identical sealed
  row, including its origin. `includes_source` edges record verified file
  inclusion instead of a duplicate `declared_dependency` edge. Old digests and
  receipts remain unchanged. The single-commit offline import format rejects
  origins it cannot represent. Normal backup and restore preserve them.
- Replays of completed pre-`modern_binding` checkpoints require strict proof:
  the exact original request body (including order and omission), work and invocation
  must reproduce both persisted fingerprints and match the original candidate commit
  message and fingerprint trailers, parent, owned paths and receipt closure inputs.
  A receipt alone is not proof. This compatibility path never ignores `base_version`,
  `exclude_paths` or `include_sources`, and does not apply to sorted-ref or modern intents.
- `PublishedItem` accepts exactly one of an inline `source` or `checkpoint_operation_id`.
  The latter resolves a completed checkpoint with a closure and matching `source_commit`
  in the same project. Use it in `publication_submit` validation and `publish_outputs`
  to avoid resending large closures. Stored `VersionRecord.source` remains mandatory.
- `getVersionRelationships` returns a `RelationshipPage` at
  `GET /api/v1/projects/{project_id}/versions/{version_id}/relationships` for the
  owner of the active project. Read exact pinned versions or expand one shot in
  its storyboard version with `shot_id`. `direction=uses` (default) or `used_by`
  returns one hop of indexed evidence, with the same edge orientation in both
  directions. Partial coverage is not evidence that there are no consumers.
  Cursors bind to the project, root, direction, sort and relationship revision.
  `latest_per_item=true` limits `used_by` to the newest matching version per
  consumer, preferring current where it matches and preserving all edges from the
  winning version for that consumer. It defaults to `false` and is ignored for
  `uses`. The two `used_by` modes do not share cursors.
  On `409 relationship_revision_changed`, restart at page one. Current and owner
  selection badges do not alter immutable relationships or source digests. A
  missing artefact record has `state: missing` and null kind and purpose.
  Shot and panel endpoints include their owning board version summary in `nodes`,
  even on reverse pages. A sequence entry or contributor's nullable `shot_id`
  is local to the recorded sequence input, not a storyboard pin. This route is
  strictly read-only: it does not extend sliding owner sessions (`refresh=False`).
  An expired owner session returns `401`.
- Authored `Shot` and projected `ShotPlan.shots[]` optionally carry `planned_assets`,
  a unique list of exact `VersionRef` pairs for assets in the active project.
  Omitted means not recorded and remains omitted on reads. `[]` means no pins
  declared for that shot. Neither is evidence of what appears on screen. The
  storyboard closure must pin the union of board-wide `asset_dependencies`, all
  planned assets and all bound scenes. Shot pins do not become board references
  or cause asset bytes to be loaded. A `planned_asset` relationship links the
  exact shot and its owning board version to the pinned asset version. It records
  intent, not scene binding, source inclusion or owner selection.

JSON HTTP bodies and successful JSON responses reference named normative definitions.
Query parameters use explicit string/integer schemas, with string enums preserved in
the generated TypeScript operations. Binary media operations remain binary.

These definitions freeze integration interfaces. Their presence alone does not claim
that every consuming endpoint or native tool is implemented or that a provider has
passed native/GPU acceptance.

The [complete protocol documentation](https://github.com/smal-ai/proscenium/tree/6b714c0dd574abe3995cf7020b988190336cefdf/docs/contracts)
and [execution semantics](https://github.com/smal-ai/proscenium/blob/6b714c0dd574abe3995cf7020b988190336cefdf/docs/execution.md)
are retained at the accepted implementation revision.
