import type { Artifact, FrameTarget, JobRecord, VersionRecord } from "../../api/generated/contracts";

/** Conditioning derivatives are images too, but are not generation results. */
export function generationOutputs(job: JobRecord, artifacts: Artifact[]): Artifact[] {
  if (job.kind !== "image_generation") return [];
  return job.artifact_ids.flatMap(id => {
    const image = artifacts.find(a => a.artifact_id === id && a.project_id === job.project_id);
    if (!image || image.kind !== "image" || image.purpose !== "concept" || image.state !== "ready" || !image.dimensions) return [];
    if (image.derived_from.some(t => t.kind === "encode" && "exif_transposed" in t.parameters)) return [];
    return [image];
  });
}

export function targetJobId(target: FrameTarget | null, jobs: JobRecord[], artifacts: Artifact[], sequenceJobId: string | null = null): string | null {
  if (!target) return null;
  if (target.render_job_id) return target.render_job_id;
  // Preserve the sequence playback control's existing job selection. Composite
  // targets deliberately have no single render job, unlike ordinary frame targets.
  if (target.origin === "sequence_render") return sequenceJobId ?? jobs[0]?.job_id ?? null;
  if (target.origin !== "concept") return null;
  const matches = jobs.filter(job => generationOutputs(job, artifacts).some(image =>
    image.artifact_id === target.artifact_id && image.sha256 === target.displayed_sha256 &&
    image.dimensions?.width === target.dimensions.width && image.dimensions.height === target.dimensions.height));
  // Ambiguous ownership is not permission to pick whichever response arrived first.
  return matches.length === 1 ? matches[0].job_id : null;
}

export function imageTarget(image: Artifact, version: VersionRecord, jobs: JobRecord[] = []): FrameTarget {
  if (!image.dimensions) throw new Error("Review image dimensions unavailable");
  const target: FrameTarget = {origin: image.purpose === "concept" ? "concept" : "reference", artifact_id: image.artifact_id,
    displayed_sha256: image.sha256, dimensions: image.dimensions, item_id: version.item_id, version_id: version.version_id,
    shot_id: null, render_job_id: null, source_frame: null, media_artifact_id: null};
  const candidates = jobs.filter(job => job.project_id === image.project_id &&
    job.target.version?.item_id === version.item_id && job.target.version.version_id === version.version_id);
  const identity = targetJobId(target, candidates, [image]);
  return {...target, shot_id: candidates.find(job => job.job_id === identity)?.target.shot_id ?? null};
}
