import { useMemo, type ReactNode } from "react";
import type { ActivityEventData, ApiClient, Artifact, ConversationEventData, Event, ItemRecord, RequestRecord, RetainedArtifactSummary, RevisionCommand } from "../../api";
import { durableCommand, id } from "../../app/storage";
import { StoredContext } from "./ContextPreview";
import { ActivityEntry } from "./ActivityEntry";
import { ChatMarkdown } from "./ChatMarkdown";
import { isRenderPassStreamArtifact, OutputCards } from "./OutputCards";

export function compareConversationEntries(a: { time: string; sequence?: number; key: string }, b: { time: string; sequence?: number; key: string }): number {
  return Date.parse(a.time) - Date.parse(b.time) || (a.sequence ?? 0) - (b.sequence ?? 0) || a.key.localeCompare(b.key);
}

interface TimelineEntry {
  key: string;
  time: string;
  sequence?: number;
  routineTool?: boolean;
  toolRunning?: boolean;
  toolLabel?: string;
  memberKeys?: string[];
  node: ReactNode;
}

export function Conversation({ visible = true, api, projectId, requests, events, onRefresh, onError, onStarter, items = [], retainedArtifacts = [], onInspectArtifact }: {
  visible?: boolean; api: ApiClient; projectId: string; requests: RequestRecord[]; events: Event[]; onRefresh: () => Promise<void>; onError: (e: unknown) => void;
  onStarter?: (kind: "asset" | "scene" | "storyboard") => void; items?: ItemRecord[]; retainedArtifacts?: RetainedArtifactSummary[]; onInspectArtifact?: (artifact: Artifact) => void;
}) {
  const { messages, activities, agentLabels, hasSubagents } = useMemo(() => {
    const messages = new Map<string, { event: Event; text: string }>();
    const activities = new Map<string, { start: Event; latest: Event }>();
    const agentLabels = new Map<string, string>();
    let hasSubagents = false;
    for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
      if (event.event_type.startsWith("assistant.")) {
        const payload = event.payload as unknown as ConversationEventData;
        const key = `${event.native_session_id ?? "app"}:${payload.native_agent_id ?? event.actor.id}:${payload.message_id}`;
        const previous = messages.get(key);
        messages.set(key, { event: previous?.event ?? event, text: event.event_type === "assistant.completed" ? payload.text : (previous?.text ?? "") + payload.text });
      } else if (event.event_type.startsWith("tool.") || event.event_type.startsWith("subagent.")) {
        const activity = event.payload as unknown as ActivityEventData;
        if (event.event_type.startsWith("subagent.")) {
          hasSubagents = true;
          agentLabels.set(event.actor.id, activity.display_name || activity.name);
        }
        const actKey = `${event.native_session_id ?? "app"}:${activity.invocation_id}`;
        const previous = activities.get(actKey);
        activities.set(actKey, { start: previous?.start ?? event, latest: event });
      }
    }
    return { messages, activities, agentLabels, hasSubagents };
  }, [events]);
  const assigned = new Set<string>();
  const summaryById = new Map(retainedArtifacts.map(summary => [summary.artifact.artifact_id, summary]));
  const activityOutputs = new Map<string, string[]>();
  for (const [key, { latest: event }] of activities) {
    const activity = event.payload as unknown as ActivityEventData;
    const explicitIds = activity.artifact_ids.filter(artifactId => {
      const summary = summaryById.get(artifactId);
      return !summary || !isRenderPassStreamArtifact(summary);
    });
    const correlatedIds = retainedArtifacts
      .filter(summary => summary.invocation_id === activity.invocation_id && !isRenderPassStreamArtifact(summary))
      .map(summary => summary.artifact.artifact_id);
    const ids = [...explicitIds, ...correlatedIds].filter(id => !assigned.has(id));
    ids.forEach(id => assigned.add(id)); activityOutputs.set(key, [...new Set(ids)]);
  }
  const requestOutputs = new Map<string, string[]>();
  for (const record of requests) {
    const ids = retainedArtifacts
      .filter(summary => !assigned.has(summary.artifact.artifact_id) && !summary.invocation_id && summary.published_versions.length === 0 && !isRenderPassStreamArtifact(summary) && (summary.request_id === record.request_id || (record.receipt.work_id && summary.work_id === record.receipt.work_id)))
      .map(summary => summary.artifact.artifact_id);
    ids.forEach(id => assigned.add(id)); requestOutputs.set(record.request_id, ids);
  }
  const rawEntries: TimelineEntry[] = [
    ...requests.flatMap(record => {
      const standaloneOutputs = requestOutputs.get(record.request_id) ?? [];
      return [
        { key: `request.${record.request_id}`, time: record.accepted_at, sequence: -1, node: <article className="message user-message" key={record.request_id} data-request-id={record.request_id}><div className="message-heading"><strong>You</strong><span>{record.submission.type} · {record.receipt.state}</span></div><p>{record.submission.type === "stop" ? `Stop work ${record.submission.target_work_id}${record.submission.reason ? `: ${record.submission.reason}` : ""}` : record.submission.text}</p>
          {record.submission.type === "steer" && <small>Target work: {record.submission.target_work_id}. Already executed actions are not undone.</small>}
          {record.submission.type !== "stop" && <StoredContext api={api} projectId={projectId} contextId={record.submission.context_id} items={items} />}
          {record.receipt.state === "held" && <button className="subtle" onClick={() => void durableCommand<RevisionCommand, unknown>(`cancel.${record.request_id}`, () => ({ schema_version: "1.0", operation_id: id(), project_id: projectId, expected_revision: record.receipt.state_revision }), body => api.call("cancelHeldRequest", { path: { project_id: projectId, request_id: record.request_id }, body, idempotencyKey: body.operation_id })).then(onRefresh).catch(onError)}>Cancel held request</button>}
        </article> },
        ...(standaloneOutputs.length ? [{
          key: `request.outputs.${record.request_id}`,
          time: record.accepted_at,
          sequence: 0,
          node: <div className="retained-turn-outputs"><OutputCards visible={visible} api={api} projectId={projectId} artifactIds={standaloneOutputs} retainedArtifacts={retainedArtifacts} onInspectArtifact={onInspectArtifact} /></div>,
        }] : []),
      ];
    }),
    ...[...messages.entries()].filter(([, message]) => message.text.trim()).map(([key, { event, text }]) => {
      const specialist = event.actor.kind === "subagent" ? (agentLabels.get(event.actor.id) ?? "authoring-specialist") : null;
      return { key, time: event.occurred_at, sequence: event.sequence, node: <article className="message assistant-message" key={key}><div className="message-heading"><strong>{specialist ? "Specialist" : "Copilot"}</strong>{specialist && <span>{specialist}</span>}</div><ChatMarkdown text={text} /></article> };
    }),
    ...[...activities.entries()].flatMap(([key, { start, latest: event }]) => {
      const activity = event.payload as unknown as ActivityEventData;
      const outputs = activityOutputs.get(key) ?? [];
      if (hasSubagents && event.event_type.startsWith("tool.") && activity.name === "task" && activity.state !== "failed" && activity.state !== "outcome_uncertain" && !outputs.length && !activity.output_status) {
        return [];
      }
      const isTool = event.event_type.startsWith("tool.");
      const toolRunning = isTool && activity.state !== "completed" && activity.state !== "failed" && activity.state !== "outcome_uncertain";
      const routineTool = isTool && (activity.state === "completed" || toolRunning) && !outputs.length && !activity.output_status;
      return [{
        key: `activity.${key}`,
        time: start.occurred_at,
        sequence: start.sequence,
        routineTool,
        toolRunning,
        toolLabel: activity.display_name || activity.name,
        node: <ActivityEntry visible={visible} key={key} event={event} api={api} projectId={projectId} retainedArtifacts={retainedArtifacts} onInspectArtifact={onInspectArtifact} artifactIds={outputs} />,
      }];
    }),
  ].sort(compareConversationEntries);

  const entries: TimelineEntry[] = [];
  for (let i = 0; i < rawEntries.length;) {
    if (rawEntries[i].routineTool) {
      let j = i + 1;
      while (j < rawEntries.length && rawEntries[j].routineTool) j++;
      if (j - i >= 2) {
        const group = rawEntries.slice(i, j);
        const runningItems = group.filter(item => item.toolRunning);
        const doneCount = group.length - runningItems.length;
        const latestRunning = runningItems.at(-1);
        const summaryText = latestRunning
          ? `${doneCount} of ${group.length} tool steps · ${latestRunning.toolLabel}…`
          : `${group.length} tool steps completed`;
        entries.push({
          key: `group.${group[0].key}`,
          memberKeys: group.map(item => item.key),
          time: group[0].time,
          sequence: group[0].sequence,
          node: <details className="activity activity-group" key={`group.${group[0].key}`}>
            <summary><span className={latestRunning ? "activity-dot" : "activity-dot complete"} /><span className="activity-group-label">{summaryText}</span><span>Show</span></summary>
            <div className="activity-group-items">{group.map(item => <div key={item.key}>{item.node}</div>)}</div>
          </details>,
        });
        i = j;
        continue;
      }
    }
    entries.push(rawEntries[i]);
    i++;
  }

  return <div className="conversation" aria-label="Project conversation" aria-live="polite">{entries.map(entry => <div key={entry.key} data-entry-key={entry.key} data-member-keys={entry.memberKeys?.join("\n")}>{entry.node}</div>)}{!requests.length && !entries.length && <div className="conversation-empty"><h3>What would you like to make?</h3><p>Choose a starting point, or type any idea directly.</p>{onStarter && <div className="starter-actions"><button onClick={() => onStarter("storyboard")}>Plan a storyboard</button><button onClick={() => onStarter("asset")}>Create an asset</button><button onClick={() => onStarter("scene")}>Build a scene</button></div>}<small>These options prepare a draft. Nothing is sent until you choose Queue or Steer.</small></div>}</div>;
}
