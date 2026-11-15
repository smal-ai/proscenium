import type { Camera, FrameTarget, JobRecord } from "../../api/generated/contracts";

/** Only the selected frame's pinned render job can supply its camera. */
export function frameCamera(target: FrameTarget, jobs: JobRecord[]): Camera | null {
  if (!target.render_job_id || target.source_frame === null) return null;
  const job = jobs.find(value => value.job_id === target.render_job_id);
  const spec = job?.render_spec;
  if (!job || !spec?.camera || job.kind !== "render"
    || job.target.version?.version_id !== target.version_id || job.target.version.item_id !== target.item_id
    || job.target.shot_id !== target.shot_id || spec.shot_id !== target.shot_id
    || target.source_frame < spec.frames.start || target.source_frame > spec.frames.end
    || target.dimensions.width !== spec.dimensions.width || target.dimensions.height !== spec.dimensions.height) return null;
  return structuredClone(spec.camera);
}
