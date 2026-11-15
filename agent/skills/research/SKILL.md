---
name: research
description: Research an active-project creative brief, find reusable local or configured online assets, acquire references and hand off traceable source publication.
---

# Research for the active brief

Call `get_project_capabilities` first. Honour host `request_target`, `reference_versions`
and `execution_scope`. Planning-only work performs no inference, previews or scene
execution. Use `validate_authored_document` for plans, sequences and publications
before checkpoint/publication. Plan impact uses `compare_to_path` and `changed_ids`.
Requested image inference uses `prepare_image_generation` then `submit_generation`.
Omit preparation `project_id`/`generation_task_id`, which the host binds. Omit
width/height/steps for capability recipe defaults unless the user requested valid
multiple-of-16 dimensions within `max_pixels`. Wait with
`get_job(job_id=..., wait_seconds=15)` (bounded `0..30`), not rapid polling.
Inspect images using `get_artifact(artifact_id=..., include_image=true)`, never
copy managed artefact content just to call `view`. Request `request_vlm_review`
for produced visual outputs when a `configured` or `ready` VLM provider is reported.
Only owner-approved findings become revision instructions.
Acquire/import and publish a standalone `asset` without requiring a storyboard.
Dependent scenes/storyboards remain pinned until an explicit adoption edit.

1. Read the active project's instructions, canonical brief and selected treatment,
   current request, pinned catalogue versions and owner selections. Check Git status
   before edits. Do not inherit unfinished creative work from a reference project.
2. Make a coverage matrix using [templates.md](templates.md). Search filenames and
   file text in the active-project library, then owner catalogue names, tags and
   descriptions through the configured catalogue tools. Use ordinary case-insensitive
   search, not an embedding service or a filesystem “latest” directory. Follow all
   catalogue pages. Keep exact item/version IDs and provenance with useful results.
3. Discover configured online MCP search/acquisition tools, including BlendSwap when
   available. Missing credentials, endpoint or tool means optional capability
   unavailable, not failed local research. Do not install services or seek credentials.
   Prefer original publishers, useful views, scale anchors and construction evidence.
4. Distinguish **discovered** (a lead), **acquired** (actual verified local bytes),
   **imported** (observed application import) and **validated** (named checks with
   evidence). A thumbnail is not an acquired model. Import success is not visual,
   deformation or motion validation. Owner selection is a separate owner record.
5. Follow [acquisition.md](acquisition.md). Record known `Provenance` fields exactly:
   `source_url`, `creator`, `licence`. Unknown values are JSON null. Missing or
   restrictive rights do not block download, import, local use, checkpoint or
   publication. Do not turn unknown rights into a cleared licence.
6. Import or procedurally author only within the requested scope, preserve live
   unsaved work and inspect actual units/materials/dependencies. Use configured
   native tools only when authorised and available. Never call an unrun import
   validated. Keep exact failures and a viable authoring fallback visible.
7. Checkpoint only owned sources through `checkpoint_sources(entry_path=...)`,
   with dependencies/`required_runtime` when needed. Pass the receipt's
   `source_closure` directly to `publish_outputs`, never script closure sorting
   or SHA-256 digests. Retain exact source/version dependencies in the configured
   publication tool. Use its generated command contract and original operation ID
   on acknowledgement loss. Do not write a database, make a parallel catalogue,
   resolve by mtime, or claim publication without its receipt. Empty preview requests
   keep a research/planning-only publication from launching rendering.
8. Reuse explicitly pinned versions. Revising an asset must not update existing
   scene dependencies. Adoption is a separate authored scene change and publication.

Free configured acquisition is automatic. Purchases, new chargeable services,
software/model installation, persistent service changes and public uploads need the
exact owner decision required by owner policy. Licence text is metadata, not a gate.
Cross-project files and credentials remain excluded. No default endpoint, subject,
cast, fixed shot count or mandatory styles directory belongs in a new brief.

Within an approved broad production request, continue independent research and
subsequent stages autonomously. Pause only for a material missing decision, requested
review checkpoint, consequential action or exhausted generation budget. Research,
browsing and dossier rebuilding alone never submit generation or alter service state.

Resources are maintained here, not loaded from a reference checkout. See
[NOTICE.md](NOTICE.md) for source identity and adaptation boundaries.
