import type { ReactNode } from "react";
import { X } from "lucide-react";
import type { ApiClient, JobRecord, RevisionCommand } from "../../api";
import { durableCommand, id } from "../../app/storage";

export function jobsBadgeState(jobs: JobRecord[], stale: boolean): { text: string; ariaLabel: string; tone: "empty" | "active" | "done" | "attention" } {
  const unfinished = jobs.filter(job => !["succeeded", "failed", "cancelled"].includes(job.state));
  const uncertain = jobs.some(job => job.state === "outcome_uncertain");
  const failed = jobs.some(job => job.state === "failed");
  const text = unfinished.length ? `${unfinished.length} active${stale || uncertain ? "?" : ""}` : jobs.length ? `${jobs.length} done${stale ? "?" : ""}` : `0${stale ? "?" : ""}`;
  const tone = unfinished.length ? uncertain ? "attention" : "active" : jobs.length ? failed ? "attention" : "done" : "empty";
  return { text, ariaLabel: `Jobs: ${text}${stale || uncertain ? " · last known or uncertain" : ""}${failed ? " · failed job recorded" : ""}`, tone };
}

export function ComposerPanel({ panelId, title, onClose, children }: {
  panelId: string; title: string; onClose: () => void; children: ReactNode;
}) {
  return <section className="composer-panel" id={panelId} aria-label={title}>
    <header><strong>{title}</strong><button className="subtle" aria-label="Back to message" title="Back to message" onClick={onClose}><X size={13} /></button></header>
    <div className="composer-panel-content">{children}</div>
  </section>;
}

export function JobList({ api, projectId, jobs, onRefresh, onError }: {
  api: ApiClient; projectId: string; jobs: JobRecord[];
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
  const finished = (job: JobRecord) => ["succeeded", "failed", "cancelled"].includes(job.state);
  return <>{[false, true].map(terminal => <section key={String(terminal)}>{jobs.some(job => finished(job) === terminal) && <h4>{terminal ? "Finished job history" : "Unfinished jobs"}</h4>}{jobs.filter(job => finished(job) === terminal).map(job => <div className={`job-row${["failed", "outcome_uncertain"].includes(job.state) ? " needs-attention" : ""}`} key={job.job_id}>
    <strong>{job.kind.replaceAll("_", " ")}</strong><small>{job.state.replaceAll("_", " ")}</small>
    <small>{job.job_id}</small>
    <small>Provider: {job.provider_id ?? "not reported"}</small>
    {job.state === "failed" && job.error_code && <small className="activity-error">Failure: {job.error_code}</small>}
    {job.state === "outcome_uncertain" && <small role="status">Needs attention · reconcile the original job outcome before retrying.</small>}
    {!["succeeded", "failed", "cancelled"].includes(job.state) && <button onClick={() => void cancel(job)}>Request job cancellation</button>}
  </div>)}</section>)}{!jobs.length && <small>No jobs recorded.</small>}</>;
}
