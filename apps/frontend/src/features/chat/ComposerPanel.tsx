import type { ReactNode } from "react";
import { X } from "lucide-react";
import type { ApiClient, JobRecord, RevisionCommand } from "../../api";
import { durableCommand, id } from "../../app/storage";

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
  return <>{jobs.map(job => <div className="job-row" key={job.job_id}>
    <strong>{job.kind.replaceAll("_", " ")}</strong><small>{job.state.replaceAll("_", " ")}</small>
    <small>{job.job_id}</small>
    {!["succeeded", "failed", "cancelled"].includes(job.state) && <button onClick={() => void cancel(job)}>Request job cancellation</button>}
  </div>)}{!jobs.length && <small>No jobs recorded.</small>}</>;
}
