import type { JobRecord } from "../../api/generated/contracts";

export function reviewJobsFingerprint(jobs: JobRecord[]) {
  return JSON.stringify(jobs.map(job => [job.job_id, job.state, job.availability, job.error_code,
    // Raster render outputs are attached on every receipt. The scoped refresh
    // clock already handles them and asset changes for these jobs.
    job.kind === "render" && job.render_spec?.purpose !== "proxy" ? null : job.artifact_ids, job.render_spec]));
}

export function proxyJobsFingerprint(jobs: JobRecord[], versionId?: string) {
  return JSON.stringify(jobs.filter(job => versionId && job.target.version?.version_id === versionId && job.render_spec?.purpose === "proxy")
    .map(job => [job.job_id, job.artifact_ids]));
}
