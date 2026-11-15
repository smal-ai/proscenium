import type { ActivityEventData, ApiClient, Artifact, Event, RetainedArtifactSummary } from "../../api";
import { OutputCards } from "./OutputCards";

export function ActivityEntry({ event, api, projectId, retainedArtifacts = [], onInspectArtifact, artifactIds }: { event: Event; api: ApiClient; projectId: string; retainedArtifacts?: RetainedArtifactSummary[]; onInspectArtifact?: (artifact: Artifact) => void; artifactIds?: string[] }) {
  const activity = event.payload as unknown as ActivityEventData;
  const label = `${event.event_type.startsWith("subagent.") ? "Native specialist" : "Tool"}: ${activity.display_name || activity.name}`;
  const correlated = retainedArtifacts.filter(summary => summary.invocation_id === activity.invocation_id);
  const outputs = <>{activity.output_status === "publication_blocked" && <p className="publication-status" role="status">Finished · publication blocked{activity.summary ? ` · ${activity.summary}` : ""}</p>}{activity.output_status === "retained_unpublished" && <p className="publication-status">Outputs retained · not published</p>}<OutputCards api={api} projectId={projectId} artifactIds={artifactIds ?? [...activity.artifact_ids, ...correlated.map(summary => summary.artifact.artifact_id)]} retainedArtifacts={retainedArtifacts} onInspectArtifact={onInspectArtifact} /></>;
  const problem = activity.state === "failed" || activity.state === "outcome_uncertain";
  const diagnostics = <>
    <small>Model: {activity.model_id ?? "not reported"} · {event.actor.kind}: {event.actor.id}</small>
    <small>Work: {event.work_id ?? "unattributed"} · Request: {event.request_id ?? "unattributed"}</small>
  </>;
  if (problem) return <article className={`activity activity-${activity.state}`}>
    <div className="activity-heading"><strong>{label}</strong><span>{activity.state === "failed" ? "Failed" : "Outcome uncertain"}</span></div>
    <p className="activity-error" role="status">{activity.summary || (activity.state === "failed"
      ? "This recorded call has no failure reason. Check the latest Copilot response before retrying."
      : "The tool result has not been confirmed. Reconcile the original work before retrying.")}</p>
    <details className="activity-diagnostics"><summary>Technical details</summary>{diagnostics}</details>
    {outputs}
  </article>;
  return <div><details className="activity">
    <summary><span className={activity.state === "completed" ? "activity-dot complete" : "activity-dot"} />{label}<span>{activity.state.replaceAll("_", " ")}</span></summary>
    {activity.summary && <p>{activity.summary}</p>}{diagnostics}
  </details>{outputs}</div>;
}
