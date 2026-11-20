# Import a local project and verified media

Stop the backend using the target registry and close any native editor writing the project.
Run the import against a full local Git checkout with the LFS objects already available.
The command does not start Blender, Unity, a backend or a model, and does not change the active project.

```bash
uv run python -m tools.operations.import_project \
  --config /absolute/private/config.json \
  --project-root /absolute/workspace \
  --manifest /absolute/import-manifest.json
```

For a separate registry with default host settings, use `--data-dir /absolute/registry`
instead of `--config`. Keep the registry outside the workspace. The manifest is a private
CLI input, not an HTTP or native-tool contract.

## Manifest format 1.0

```json
{
  "schema_version": "1.0",
  "import_id": "baseline-1",
  "project": {"name": "Example", "project_id": "project-example", "repository_id": "repo-example"},
  "items": [],
  "retained_artifacts": [],
  "selections": []
}
```

- `import_id` is an immutable operation ID. The same canonical JSON returns the same IDs and event watermark with `replayed: true`, without re-reading media. A changed manifest with that ID is a conflict after a successful import. For a revision, use a new import and version/job/intent IDs, and pin the item's `expected_current_version_id`.
- Omit `project` to take identity from `project.json`, or generate a new identity for a fresh project. Supply both identity fields for a new project whose authored documents already reference `project_id`. `name` is only used when initialising `project.json`. Existing ignore rules and attribute precedence are preserved. The importer installs local LFS filters without changing repository hooks.
- Each item has `item_id`, `version_id`, `name`, `kind`, and `source`. It may include `description`, `tags`, `provenance`, `derived_from` (exact version references) and `expected_current_version_id` (defaults to `null` for a new item).
- Each `source` has `entry_path`, exact `files`, `required_runtime` (`application`, `version`, `features`) and optional `dependencies` (exact `{item_id, version_id}` pins). A file is `{path, sha256}`, where the hash is of the hydrated bytes, **not** the LFS pointer. All paths are workspace-relative. List the entry path itself. An optional full `source_commit` pins the bytes already committed, independent of dirty files. Otherwise the importer checkpoints declared working files through the native isolated-index writer. In an existing repository it retains a private ref without moving HEAD or touching the index.
- `asset` items may have `artifacts` with reference/concept images. `storyboard` items use committed `AuthoredShotPlan` JSON and can include panel `artifacts` with purpose `keyframe`. Each artefact declares `{artifact_id, path, sha256, media_type, kind, purpose}`. Use those IDs directly in the authored plan. Pin its asset and scene references in the storyboard source dependencies.
- `scene` items may declare `renders`, an ordered array of passes. Each pass has `job_id`, the real `external_job_id`, a contract `RenderSpec` under `spec`, and `frames` entries `{source_frame, path, sha256}` for every frame in the inclusive range. The importer fills `spec.source_closure_digest` if omitted. The spec needs a non-null `shot_id`. For a continuous source feeding multiple shots, declare a pass per shot and use those separate job IDs in the sequence. Set `segment: {path, sha256}` to verify a supplied silent H.264 MP4, or omit it to encode one from the exact RGB PNGs.
- `sequence` items use committed `SequenceMap` JSON as their entry path, with scene references pinned in `source.dependencies`. A `delivery` object has `{job_id, external_job_id, video: {path, sha256}, dimensions: {width, height}}`. Its supplied silent H.264 MP4 is fully decoded and checked against the declared dimensions, FPS and frame count. The importer retains it with a `FrameMap` and an assembly manifest. It verifies the declared contributor PNGs but does not independently prove their pixels were used by the external encoder. Identity and trim cuts need no explicit mapping artefact. For retimes and transitions, register the complete `FrameMap` JSON under `retained_artifacts` and reference its ID in the map.
- `unity_result` requires a contract `unity` object (editor version, pipeline, workspace-relative scene path, validation status and optional player script). Include those paths in `source.files` and pin adopted assets in `derived_from` and `source.dependencies`. See the [Unity workflow skill](../agent/skills/unity/SKILL.md) for subsequent validate/build actions.
- `retained_artifacts` uses the same artefact fields and accepts `external_id` and `provenance` for historical evidence, without inventing a native request or job. `selections` is optional and contains `{selection_id, slot, choice}` for new slots. Without an explicit selection the version remains unselected, regardless of artefact purpose or prior external delivery history.

Catalogue records, terminal receipts and events commit together. Initial workspace setup,
private checkpoint refs and content-addressed blobs may remain if verification fails.
No verified job is enqueued. Use the same immutable manifest after correcting an unavailable
file or media dependency. Use a new import ID if the manifest changed after a private
checkpoint was created. To back up or move the project, see [Backup and restore](operator.md#backup-and-restore).
