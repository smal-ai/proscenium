# Contract 1.0.0

- `schema.json` defines the JSON Schema records and native tool envelopes.
- `openapi.yaml` defines the HTTP operations and shared types.
- `state-transitions.json` defines guarded work and job-attempt transitions.

Run `make generate` after an authorised change and `make check-generated` to verify
the backend/frontend bindings. Wire `schema_version` remains `1.0`.

## Compatible UAT extensions

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

JSON HTTP bodies and successful JSON responses reference named normative definitions.
Query parameters use explicit string/integer schemas, with string enums preserved in
the generated TypeScript operations. Binary media operations remain binary.

These definitions freeze integration interfaces. Their presence alone does not claim
that every consuming endpoint or native tool is implemented or that a provider has
passed native/GPU acceptance.

The [complete protocol documentation](https://github.com/smal-ai/proscenium/tree/6b714c0dd574abe3995cf7020b988190336cefdf/docs/contracts)
and [execution semantics](https://github.com/smal-ai/proscenium/blob/6b714c0dd574abe3995cf7020b988190336cefdf/docs/execution.md)
are retained at the accepted implementation revision.
