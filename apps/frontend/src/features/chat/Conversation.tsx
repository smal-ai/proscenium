import type { ActivityEventData, ApiClient, Artifact, ConversationEventData, Event, ItemRecord, RequestRecord, RetainedArtifactSummary, RevisionCommand, UsageEventData } from "../../api";
import { durableCommand, id } from "../../app/storage";
import { StoredContext } from "./ContextPreview";
import { ActivityEntry } from "./ActivityEntry";
import { OutputCards } from "./OutputCards";

const usageNumber = new Intl.NumberFormat("en-GB", { maximumSignificantDigits: 8 });

export function compareConversationEntries(a: { time: string; sequence?: number; key: string }, b: { time: string; sequence?: number; key: string }): number {
  return Date.parse(a.time) - Date.parse(b.time) || (a.sequence ?? 0) - (b.sequence ?? 0) || a.key.localeCompare(b.key);
}

function credits(data: UsageEventData): string {
  if (data.reported_cost === null) return "— credits";
  // SDK 1.0.34's legacy cost field is premium-request usage, not token-priced AI credits.
  const unit = data.cost_unit ?? (data.reported_cost === 1 ? "premium credit" : "premium credits");
  return `${usageNumber.format(data.reported_cost)} ${unit}`;
}

export function Conversation({ api, projectId, requests, events, onRefresh, onError, onStarter, items = [], retainedArtifacts = [], onInspectArtifact }: {
  api: ApiClient; projectId: string; requests: RequestRecord[]; events: Event[]; onRefresh: () => Promise<void>; onError: (e: unknown) => void;
  onStarter?: (kind: "asset" | "scene" | "storyboard") => void; items?: ItemRecord[]; retainedArtifacts?: RetainedArtifactSummary[]; onInspectArtifact?: (artifact: Artifact) => void;
}) {
  const messages = new Map<string, { event: Event; text: string }>();
  const activities = new Map<string, Event>();
  const usage: Event[] = [];
  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    if (event.event_type.startsWith("assistant.")) {
      const payload = event.payload as unknown as ConversationEventData;
      const key = `${event.native_session_id ?? "app"}:${payload.native_agent_id ?? event.actor.id}:${payload.message_id}`;
      const previous = messages.get(key);
      messages.set(key, { event: previous?.event ?? event, text: event.event_type === "assistant.completed" ? payload.text : (previous?.text ?? "") + payload.text });
    } else if (event.event_type.startsWith("tool.") || event.event_type.startsWith("subagent.")) {
      const activity = event.payload as unknown as ActivityEventData;
      activities.set(`${event.native_session_id ?? "app"}:${activity.invocation_id}`, event);
    } else if (event.event_type === "usage.recorded") usage.push(event);
  }
  const assigned = new Set<string>();
  const activityOutputs = new Map<string, string[]>();
  for (const [key, event] of activities) {
    const activity = event.payload as unknown as ActivityEventData;
    const ids = [...activity.artifact_ids, ...retainedArtifacts.filter(summary => summary.invocation_id === activity.invocation_id).map(summary => summary.artifact.artifact_id)].filter(id => !assigned.has(id));
    ids.forEach(id => assigned.add(id)); activityOutputs.set(key, [...new Set(ids)]);
  }
  const requestOutputs = new Map<string, string[]>();
  for (const record of requests) {
    const ids = retainedArtifacts.filter(summary => !assigned.has(summary.artifact.artifact_id) && !summary.invocation_id && (summary.request_id === record.request_id || (record.receipt.work_id && summary.work_id === record.receipt.work_id))).map(summary => summary.artifact.artifact_id);
    ids.forEach(id => assigned.add(id)); requestOutputs.set(record.request_id, ids);
  }
  const entries = [
    ...requests.map(record => ({ key: `request.${record.request_id}`, time: record.accepted_at, node: <article className="message user-message" key={record.request_id} data-request-id={record.request_id}><div className="message-heading"><strong>You</strong><span>{record.submission.type} · {record.receipt.state}</span></div><p>{record.submission.type === "stop" ? `Stop work ${record.submission.target_work_id}${record.submission.reason ? `: ${record.submission.reason}` : ""}` : record.submission.text}</p>
      {record.submission.type === "steer" && <small>Target work: {record.submission.target_work_id}. Already executed actions are not undone.</small>}
      {record.submission.type !== "stop" && <StoredContext api={api} projectId={projectId} contextId={record.submission.context_id} items={items} />}
      <OutputCards api={api} projectId={projectId} artifactIds={requestOutputs.get(record.request_id) ?? []} retainedArtifacts={retainedArtifacts} onInspectArtifact={onInspectArtifact} />
      {record.receipt.state === "held" && <button className="subtle" onClick={() => void durableCommand<RevisionCommand, unknown>(`cancel.${record.request_id}`, () => ({ schema_version: "1.0", operation_id: id(), project_id: projectId, expected_revision: record.receipt.state_revision }), body => api.call("cancelHeldRequest", { path: { project_id: projectId, request_id: record.request_id }, body, idempotencyKey: body.operation_id })).then(onRefresh).catch(onError)}>Cancel held request</button>}
    </article> })),
    ...[...messages.entries()].filter(([, message]) => message.text.trim()).map(([key, { event, text }]) => ({ key, time: event.occurred_at, sequence: event.sequence, node: <article className="message assistant-message" key={key}><div className="message-heading"><strong>{event.actor.kind === "subagent" ? "Native specialist" : "Copilot"}</strong><span>{event.actor.id}</span></div><p>{text}</p></article> })),
    ...[...activities.entries()].map(([key, event]) => ({ key: `activity.${key}`, time: event.occurred_at, sequence: event.sequence, node: <ActivityEntry key={key} event={event} api={api} projectId={projectId} retainedArtifacts={retainedArtifacts} onInspectArtifact={onInspectArtifact} artifactIds={activityOutputs.get(key)} /> })),
    ...usage.map(event => { const data = event.payload as unknown as UsageEventData; return { key: `usage.${event.sequence}`, time: event.occurred_at, sequence: event.sequence, node: <small className="usage-credit" key={event.sequence} title={data.cost_unit === null && data.reported_cost !== null ? "Copilot premium-request usage" : "Reported credit usage"}>{credits(data)}</small> }; }),
  ].sort(compareConversationEntries);
  return <div className="conversation" aria-label="Project conversation" aria-live="polite">{entries.map(entry => <div key={entry.key}>{entry.node}</div>)}{!requests.length && !entries.length && <div className="conversation-empty"><h3>What would you like to make?</h3><p>Choose a starting point, or type any idea directly.</p>{onStarter && <div className="starter-actions"><button onClick={() => onStarter("storyboard")}>Plan a storyboard</button><button onClick={() => onStarter("asset")}>Create an asset</button><button onClick={() => onStarter("scene")}>Build a scene</button></div>}<small>These options prepare a draft. Nothing is sent until you choose Queue or Steer.</small></div>}</div>;
}
