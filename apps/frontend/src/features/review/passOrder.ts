import type { Artifact, JobRecord } from "../../api/generated/contracts";
import { generationOutputs } from "./jobIdentity";

const isProxy = (job: JobRecord) => job.render_spec?.purpose === "proxy";
const numericToken = /^\d+$/;

function naturalCompare(a: string, b: string): number {
  const left = a.toLowerCase().split(/(\d+)/);
  const right = b.toLowerCase().split(/(\d+)/);
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    const x = left[i];
    const y = right[i];
    if (numericToken.test(x) && numericToken.test(y)) {
      // Compare digit strings without losing precision for long numeric tokens.
      const xn = x.replace(/^0+(?=\d)/, "");
      const yn = y.replace(/^0+(?=\d)/, "");
      const compared = xn.length - yn.length || (xn < yn ? -1 : xn > yn ? 1 : 0);
      if (compared) return compared;
    } else if (x !== y) return x < y ? -1 : 1;
  }
  return left.length - right.length;
}

export function naturalPassCompare(a: JobRecord, b: JobRecord): number {
  const aRender = a.kind === "render";
  const bRender = b.kind === "render";
  const aSpec = aRender ? a.render_spec : null;
  const bSpec = bRender ? b.render_spec : null;
  return Number(isProxy(a)) - Number(isProxy(b))
    || Number(bRender) - Number(aRender)
    || Number(Boolean(bSpec)) - Number(Boolean(aSpec))
    || (aSpec && bSpec ? aSpec.frames.start - bSpec.frames.start || aSpec.frames.end - bSpec.frames.end
      || naturalCompare(a.target.shot_id ?? aSpec.scene_name ?? "", b.target.shot_id ?? bSpec.scene_name ?? "") : 0)
    || a.job_id.localeCompare(b.job_id);
}

export function orderVersionJobs(jobs: readonly JobRecord[], previewJobIds: readonly string[] = []): JobRecord[] {
  const remaining = new Map(jobs.map(job => [job.job_id, job]));
  const pinned: JobRecord[] = [];
  for (const id of previewJobIds) {
    const job = remaining.get(id);
    if (job) { pinned.push(job); remaining.delete(id); }
  }
  return [...pinned, ...[...remaining.values()].sort(naturalPassCompare)];
}

export function selectablePassJobs(orderedJobs: readonly JobRecord[], mode: "3d_inspection" | "keyframes" | "animation", artifacts: readonly Artifact[]): JobRecord[] {
  const renders = orderedJobs.filter(job => job.kind === "render" && !isProxy(job));
  if (mode === "animation") {
    const motion = renders.filter(job => job.render_spec?.purpose === "draft_motion" || job.render_spec?.purpose === "final");
    if (motion.length) return motion;
    const multipleFrames = renders.filter(job => (job.expected_frames ?? 0) > 1);
    return multipleFrames.length ? multipleFrames : renders;
  }
  if (mode === "keyframes") {
    const outputs = [...artifacts];
    return orderedJobs.filter(job => !isProxy(job) && (job.kind === "render" || generationOutputs(job, outputs).length > 0));
  }
  return renders;
}

export function formatPassOptionLabels(passes: readonly JobRecord[]): Map<string, string> {
  const bodies = passes.map(job => {
    const spec = job.render_spec;
    const name = job.target.shot_id ?? spec?.scene_name ?? "Scene";
    const frameSpan = spec ? (spec.frames.start === spec.frames.end ? `f${spec.frames.start}` : `f${spec.frames.start}–${spec.frames.end}`) : "image";
    return `${name} · ${frameSpan} · ${spec?.purpose ?? job.kind}`;
  });
  const counts = new Map<string, number>();
  for (const body of bodies) counts.set(body, (counts.get(body) ?? 0) + 1);
  return new Map(passes.map(({job_id}, index) => {
    const body = bodies[index];
    const suffix = counts.get(body)! > 1 ? ` · ${job_id.length > 16 ? job_id.slice(-8) : job_id}` : "";
    return [job_id, `${index + 1} · ${body}${suffix}`];
  }));
}
