import type { ActivityEventData, Event, NativeAgentActivity, ProjectSnapshot } from "../../api";
export interface NativeSpecialist extends NativeAgentActivity { activity_key: string }

export function nativeAgents(snapshot: ProjectSnapshot | null, events: Event[], previous: NativeSpecialist[] = []): NativeSpecialist[] {
  const parentLive = !snapshot || (!snapshot.execution?.fenced && snapshot.project.state === "active" && !!snapshot.active_work && ["running", "awaiting_user"].includes(snapshot.active_work.state));
  const agents = new Map<string, NativeSpecialist>(previous.map(agent => [agent.activity_key, { ...agent, freshness: agent.state === "outcome_uncertain" ? "uncertain" : "last_known" }]));
  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    if (!event.event_type.startsWith("subagent.")) continue;
    if (snapshot && event.project_id !== snapshot.project.project_id) continue;
    const activity = event.payload as unknown as ActivityEventData;
    const key = `${event.native_session_id ?? "app"}:${activity.invocation_id}`;
    const current = agents.get(key);
    if (current?.updated_at && Date.parse(current.updated_at) > Date.parse(event.occurred_at)) continue;
    if (current && !busyAgent(current) && ["running", "cancel_requested", "outcome_uncertain"].includes(activity.state)) continue;
    const mappedState = activity.state === "cancel_requested" ? "outcome_uncertain" : activity.state;
    agents.set(key, {
      activity_key: key,
      invocation_id: activity.invocation_id, native_agent_id: event.actor.id, name: activity.name,
      display_name: activity.display_name ?? null, model_id: activity.model_id, work_id: event.work_id,
      request_id: event.request_id, state: mappedState,
      freshness: mappedState === "outcome_uncertain" ? "uncertain" : mappedState === "running" ? (parentLive ? "live" : "last_known") : "live", updated_at: event.occurred_at,
    });
  }
  for (const agent of snapshot?.execution?.active_agents ?? []) {
    const matches = [...agents.values()].filter(current => current.invocation_id === agent.invocation_id);
    const key = matches.length === 1 ? matches[0].activity_key : `${snapshot?.active_work?.native_session_id ?? "snapshot"}:${agent.invocation_id}`;
    const current = agents.get(key);
    // A replayed start cannot overwrite newer terminal child evidence.
    if (current && current.updated_at && agent.updated_at && Date.parse(current.updated_at) > Date.parse(agent.updated_at)) continue;
    if (current && !busyAgent(current) && busyAgent(agent)) continue;
    agents.set(key, { ...agent, activity_key: key });
  }
  return [...agents.values()];
}
export const busyAgent = (agent: NativeAgentActivity) => ["running", "outcome_uncertain"].includes(agent.state);
