import type { ReactNode } from "react";
import { X } from "lucide-react";
import type { ApiClient, ItemRecord, JobRecord, RevisionCommand } from "../../api";
import { durableCommand, id } from "../../app/storage";

type JobGroup = "running" | "queued" | "uncertain" | "failed" | "settled";
const jobGroup: Record<JobRecord["state"], JobGroup> = {
  starting: "running", running: "running", cancel_requested: "running",
  planned: "queued", waiting_resource: "queued", outcome_uncertain: "uncertain",
  failed: "failed", succeeded: "settled", cancelled: "settled",
};

function partitionJobs(jobs: JobRecord[]) {
  const groups: Record<JobGroup, JobRecord[]> = { running: [], queued: [], uncertain: [], failed: [], settled: [] };
  for (const job of jobs) groups[jobGroup[job.state]].push(job);
  return groups;
}

export function jobsBadgeState(jobs: JobRecord[], stale: boolean): { text: string; ariaLabel: string; tone: "empty" | "active" | "done" | "attention" } {
  const { running, queued, uncertain, failed, settled } = partitionJobs(jobs);
  const unfinished = running.length + queued.length + uncertain.length > 0;
  const parts: string[] = [];
  if (unfinished) {
    if (running.length) parts.push(`${running.length} running`);
    if (queued.length) parts.push(`${queued.length} queued`);
    if (!parts.length) parts.push(`${uncertain.length} uncertain`);
  } else {
    if (failed.length) parts.push(`${failed.length} failed`);
    if (settled.length) parts.push(`${settled.length} done`);
  }
  const text = (parts.join(" · ") || "0") + (stale || uncertain.length ? "?" : "");
  const tone = uncertain.length || failed.length ? "attention" : unfinished ? "active" : jobs.length ? "done" : "empty";
  return { text, ariaLabel: `Jobs: ${text}${stale ? " · last known" : ""}${uncertain.length ? " · uncertain job outcome" : ""}${failed.length ? " · failed job recorded" : ""}`, tone };
}

export function ComposerPanel({ panelId, title, onClose, children }: {
  panelId: string; title: string; onClose: () => void; children: ReactNode;
}) {
  return <section className="composer-panel" id={panelId} aria-label={title}>
    <header><strong>{title}</strong><button className="subtle" aria-label="Back to message" title="Back to message" onClick={onClose}><X size={13} /></button></header>
    <div className="composer-panel-content">{children}</div>
  </section>;
}

function JobRow({ job, itemName, cancel }: { job: JobRecord; itemName?: string; cancel: (job: JobRecord) => Promise<void> }) {
  const { target, completed_frames: completed, expected_frames: expected } = job;
  const purpose = job.render_spec?.purpose;
  const kind = job.kind.replaceAll("_", " ");
  const group = jobGroup[job.state];
  const attention = group === "failed" || group === "uncertain";
  const title = purpose === "proxy" ? `Proxy · ${itemName ?? target.version?.version_id ?? target.target_id}`
    : target.shot_id ?? `${target.version ? itemName ?? target.version.item_id : target.target_id} · ${kind}`;
  return <div className={`job-row${attention ? " needs-attention" : ""}`}>
    <div className="job-row-summary"><strong>{title}</strong>
      {target.shot_id && purpose && purpose !== "proxy" && <span className="job-pass-badge">{purpose.replaceAll("_", " ")}</span>}
      <span className="job-state-pill">{job.state.replaceAll("_", " ")}</span>
    </div>
    {expected !== null && <span className="job-progress">
      {completed !== null ? `${completed}/${expected} frames` : `${expected} ${expected === 1 ? "frame" : "frames"}`}
      {completed !== null && expected > 1 && <progress className="job-progress-bar" aria-hidden="true" value={completed} max={expected} />}
    </span>}
    {group === "failed" && job.error_code && <small className="activity-error">Failure: {job.error_code}</small>}
    {group === "uncertain" && <small role="status">Needs attention · reconcile the original job outcome before retrying.</small>}
    <details className="job-row-details"><summary>Details</summary>
      <small>Job ID: <code>{job.job_id}</code></small>
      <small>Kind: {kind}</small>
      {target.version && <small>Version: <code>{target.version.item_id} · {target.version.version_id}</code></small>}
      <small>Provider: {job.provider_id ?? "not reported"}</small>
      {group !== "settled" && group !== "failed" && <>
        <button onClick={() => void cancel(job)}>Request job cancellation</button>
        <small>Cancels this independent job only. It does not stop Copilot.</small>
      </>}
    </details>
  </div>;
}

export function JobList({ api, projectId, jobs, items = [], onRefresh, onError }: {
  api: ApiClient; projectId: string; jobs: JobRecord[]; items?: ItemRecord[];
  onRefresh: () => Promise<void>; onError: (error: unknown) => void;
}) {
  async function cancel(job: JobRecord) {
    try {
      await durableCommand<RevisionCommand, unknown>(`job-cancel.${job.job_id}`, () => ({
        schema_version: "1.0", operation_id: id(), project_id: projectId, expected_revision: job.state_revision,
      }), body => api.call("requestJobCancellation", { path: { project_id: projectId, job_id: job.job_id }, body, idempotencyKey: body.operation_id }));
      await onRefresh();
    } catch (error) { onError(error); }
  }
  const { failed, uncertain, running, queued, settled } = partitionJobs(jobs);
  const names = new Map(items.map(item => [item.item_id, item.name]));
  const rows = (records: readonly JobRecord[]) => records.map(job => <JobRow key={job.job_id} job={job} cancel={cancel} itemName={names.get(job.target.version?.item_id ?? job.target.target_id)} />);
  if (!jobs.length) return <small>No jobs recorded.</small>;
  return <div className="job-groups">
    {([["Needs attention", [...failed, ...uncertain]], ["Running", running], ["Planned / waiting", queued]] as const).map(([label, records]) => records.length > 0 && <section className="job-group" key={label}>
      <h4>{label} ({records.length})</h4>{rows(records)}
    </section>)}
    {settled.length > 0 && <details className="jobs-finished-group"><summary>Finished ({settled.length})</summary>{rows(settled)}</details>}
  </div>;
}
