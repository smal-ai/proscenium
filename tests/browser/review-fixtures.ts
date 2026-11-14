/** Fully shaped generated-contract fixtures. Synthetic metadata, not native evidence. */
import type { JobRecord, RenderSpec, VersionRecord } from "../../apps/frontend/src/api/generated/contracts";

const renderSpec: RenderSpec = {
  intent_id: "synthetic-preview", shot_id: "shot", purpose: "keyframe",
  source_closure_digest: "a".repeat(64), scene_name: "SyntheticScene", camera: null,
  frames: {start: 1, end: 12}, fps: {numerator: 24000, denominator: 1001},
  dimensions: {width: 640, height: 360}, engine: "BLENDER_EEVEE_NEXT", device: "CPU", samples: 16,
  colour_management: {view_transform: "Standard", look: "None", exposure: 0, gamma: 1},
  renderer_revision: "synthetic-not-executed",
  review_output: {media_type: "image/png", encoding: "view_transformed_srgb", bit_depth: 8},
};

export const syntheticVersion: VersionRecord = {
  schema_version: "1.0", project_id: "synthetic", item_id: "scene", version_id: "v1",
  kind: "scene", name: "Version one", publication_id: "publication", source_commit: "a".repeat(40),
  source: {
    entry_path: "fixture.blend",
    files: [{destination: "fixture.blend", origin: {
      kind: "git", repository_id: "synthetic-repository", commit: "a".repeat(40),
      path: "fixture.blend", sha256: "a".repeat(64),
    }}],
    dependencies: [], required_runtime: {application: "Blender", version: "synthetic-not-executed", features: []},
    digest: "a".repeat(64),
  },
  artifact_ids: [], preview_requests: [renderSpec], preview_job_ids: ["job"],
  created_at: "2026-11-14T00:00:00Z", actor: {kind: "user", id: "owner"},
};

export const syntheticJobs: JobRecord[] = [{
  schema_version: "1.0", job_id: "job", project_id: "synthetic", work_id: "synthetic-work", owner_operation_id: null,
  target: {target_id: "scene", version: {item_id: "scene", version_id: "v1"}, shot_id: "shot"},
  attempt_id: "attempt", attempt_number: 1, kind: "render", state: "running", state_revision: 0,
  availability: "partial", input_digest: "a".repeat(64), external_job_id: null, provider_id: null,
  owner_epoch: "synthetic-epoch", completed_frames: 1, expected_frames: 12, artifact_ids: [], render_spec: renderSpec,
}];
