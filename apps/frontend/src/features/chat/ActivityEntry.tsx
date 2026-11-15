import type { ActivityEventData, Event } from "../../api";

export function ActivityEntry({ event }: { event: Event }) {
  const activity = event.payload as unknown as ActivityEventData;
  const label = `${event.event_type.startsWith("subagent.") ? "Native specialist" : "Tool"}: ${activity.name}`;
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
  </article>;
  return <details className="activity">
    <summary><span className={activity.state === "completed" ? "activity-dot complete" : "activity-dot"} />{label}<span>{activity.state.replaceAll("_", " ")}</span></summary>
    {activity.summary && <p>{activity.summary}</p>}{diagnostics}
  </details>;
}
