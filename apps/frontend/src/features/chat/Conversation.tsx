import type { ActivityEventData, ApiClient, ConversationEventData, Event, RequestRecord, RevisionCommand, UsageEventData } from "../../api";
import { durableCommand, id } from "../../app/storage";
import { StoredContext } from "./ContextPreview";
import { ActivityEntry } from "./ActivityEntry";

const usageNumber = new Intl.NumberFormat("en-GB", { maximumSignificantDigits: 8 });

function credits(data: UsageEventData): string {
  if (data.reported_cost === null) return "— credits";
  // SDK 1.0.34's legacy cost field is premium-request usage, not token-priced AI credits.
  const unit = data.cost_unit ?? (data.reported_cost === 1 ? "premium credit" : "premium credits");
  return `${usageNumber.format(data.reported_cost)} ${unit}`;
}

export function Conversation({ api, projectId, requests, events, onRefresh, onError }: {
  api: ApiClient; projectId: string; requests: RequestRecord[]; events: Event[]; onRefresh: () => Promise<void>; onError: (e: unknown) => void;
}) {
  const messages = new Map<string, { event: Event; text: string }>();
  const activities = new Map<string, Event>();
  const usage: Event[] = [];
  for (const event of events) {
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
  const entries = [
    ...requests.map(record => ({ key: `request.${record.request_id}`, time: record.accepted_at, node: <article className="message user-message" key={record.request_id} data-request-id={record.request_id}><div className="message-heading"><strong>You</strong><span>{record.submission.type} · {record.receipt.state}</span></div><p>{record.submission.type === "stop" ? `Stop work ${record.submission.target_work_id}${record.submission.reason ? `: ${record.submission.reason}` : ""}` : record.submission.text}</p>
      {record.submission.type === "steer" && <small>Target work: {record.submission.target_work_id}. Already executed actions are not undone.</small>}
      {record.submission.type !== "stop" && <StoredContext api={api} projectId={projectId} contextId={record.submission.context_id} />}
      {record.receipt.state === "held" && <button className="subtle" onClick={() => void durableCommand<RevisionCommand, unknown>(`cancel.${record.request_id}`, () => ({ schema_version: "1.0", operation_id: id(), project_id: projectId, expected_revision: record.receipt.state_revision }), body => api.call("cancelHeldRequest", { path: { project_id: projectId, request_id: record.request_id }, body, idempotencyKey: body.operation_id })).then(onRefresh).catch(onError)}>Cancel held request</button>}
    </article> })),
    ...[...messages.entries()].map(([key, { event, text }]) => ({ key, time: event.occurred_at, node: <article className="message assistant-message" key={key}><div className="message-heading"><strong>{event.actor.kind === "subagent" ? "Native specialist" : "Copilot"}</strong><span>{event.actor.id}</span></div><p>{text}</p></article> })),
    ...[...activities.entries()].map(([key, event]) => ({ key: `activity.${key}`, time: event.occurred_at, node: <ActivityEntry key={key} event={event} /> })),
    ...usage.map(event => { const data = event.payload as unknown as UsageEventData; return { key: `usage.${event.sequence}`, time: event.occurred_at, node: <small className="usage-credit" key={event.sequence} title={data.cost_unit === null && data.reported_cost !== null ? "Copilot premium-request usage" : "Reported credit usage"}>{credits(data)}</small> }; }),
  ].sort((a, b) => a.time.localeCompare(b.time) || a.key.localeCompare(b.key));
  return <div className="conversation" aria-label="Project conversation" aria-live="polite">{entries.map(entry => <div key={entry.key}>{entry.node}</div>)}{!entries.length && <div className="conversation-empty"><h3>Direct the next iteration.</h3><p>Describe what to make, inspect the result, then refine it in the same project discussion.</p><small>Queue captures your current selection. Browsing afterwards does not change it.</small></div>}</div>;
}
