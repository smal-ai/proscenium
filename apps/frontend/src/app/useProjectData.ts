import { useCallback, useEffect, useRef, useState } from "react";
import { ApiClient, ApiError } from "../api";
import type { ActivityEventData, Event, ItemRecord, JobRecord, ProjectSnapshot, RequestRecord, RetainedArtifactSummary, SocketControl, WorkRecord } from "../api";
import { routes } from "../api/generated/operations";
import { describeError, writePrivate } from "./storage";

const TERMINAL_WORK = new Set(["completed", "failed", "stopped"]);

export async function allPages<T>(load: (cursor?: string) => Promise<{ items: T[]; next_cursor: string | null }>): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | undefined;
  const seen = new Set<string>();
  do {
    const page = await load(cursor);
    items.push(...page.items);
    cursor = page.next_cursor ?? undefined;
    if (cursor && seen.has(cursor)) throw new Error("Server repeated a pagination cursor.");
    if (cursor) seen.add(cursor);
  } while (cursor);
  return items;
}

export function sortRequests(records: RequestRecord[]): RequestRecord[] {
  return [...records].sort((a, b) => Date.parse(a.accepted_at) - Date.parse(b.accepted_at) || a.request_id.localeCompare(b.request_id));
}

export function mergeRequests(current: RequestRecord[], incoming: RequestRecord[]): RequestRecord[] {
  const byId = new Map<string, RequestRecord>();
  for (const record of current) byId.set(record.request_id, record);
  for (const record of incoming) {
    const prev = byId.get(record.request_id);
    if (!prev || record.receipt.state_revision >= prev.receipt.state_revision) byId.set(record.request_id, record);
  }
  return sortRequests([...byId.values()]);
}

export function mergeItems(current: ItemRecord[], incoming: ItemRecord[]): ItemRecord[] {
  const byId = new Map<string, ItemRecord>();
  for (const item of current) byId.set(item.item_id, item);
  for (const item of incoming) {
    const prev = byId.get(item.item_id);
    if (!prev || item.state_revision >= prev.state_revision) byId.set(item.item_id, item);
  }
  return [...byId.values()].sort((a, b) => a.item_id.localeCompare(b.item_id));
}

export function mergeJobs(current: JobRecord[], incoming: JobRecord[]): JobRecord[] {
  const byId = new Map<string, JobRecord>();
  for (const job of current) byId.set(job.job_id, job);
  for (const job of incoming) {
    const prev = byId.get(job.job_id);
    if (!prev || job.state_revision >= prev.state_revision) byId.set(job.job_id, job);
  }
  return [...byId.values()];
}

export function mergeSnapshot(
  current: ProjectSnapshot | null,
  incoming: ProjectSnapshot,
  cursor: number,
  knownWork: ReadonlyMap<string, WorkRecord>,
): ProjectSnapshot {
  if (!current) {
    const incomingWork = incoming.active_work;
    if (incomingWork) {
      const newer = knownWork.get(incomingWork.work_id);
      if (newer && newer.state_revision > incomingWork.state_revision) {
        return { ...incoming, active_work: TERMINAL_WORK.has(newer.state) ? null : newer };
      }
    }
    return incoming;
  }
  if (incoming.event_sequence >= Math.max(current.event_sequence, cursor)) return incoming;
  const project = incoming.project.state_revision >= current.project.state_revision ? incoming.project : current.project;
  const settings = incoming.settings.state_revision >= current.settings.state_revision ? incoming.settings : current.settings;
  let active_work = current.active_work;
  if (incoming.active_work) {
    const newer = knownWork.get(incoming.active_work.work_id);
    const candidate = newer && newer.state_revision > incoming.active_work.state_revision ? newer : incoming.active_work;
    if (!active_work) active_work = TERMINAL_WORK.has(candidate.state) ? null : candidate;
    else if (active_work.work_id === candidate.work_id && candidate.state_revision >= active_work.state_revision) {
      active_work = TERMINAL_WORK.has(candidate.state) ? null : candidate;
    }
  }
  const decisionMap = new Map(current.pending_decisions.map(d => [d.decision_id, d]));
  for (const decision of incoming.pending_decisions) {
    const prev = decisionMap.get(decision.decision_id);
    if (!prev) {
      // Do not resurrect a decision already resolved by a newer event.
      if (incoming.event_sequence >= current.event_sequence) decisionMap.set(decision.decision_id, decision);
    } else if (decision.state_revision >= prev.state_revision) {
      decisionMap.set(decision.decision_id, decision);
    }
  }
  return {
    ...incoming,
    event_sequence: Math.max(current.event_sequence, incoming.event_sequence, cursor),
    project,
    settings,
    active_work,
    items: mergeItems(current.items, incoming.items),
    jobs: mergeJobs(current.jobs, incoming.jobs),
    held_requests: mergeRequests(current.held_requests, incoming.held_requests).filter(r => r.receipt.state === "held"),
    pending_decisions: [...decisionMap.values()].filter(d => d.state === "pending"),
    execution: current.execution && incoming.event_sequence < current.event_sequence ? current.execution : incoming.execution,
  };
}

export function useProjectData(api: ApiClient, projectId: string | null, onUnauthorised: () => void) {
  const [snapshot, setSnapshot] = useState<ProjectSnapshot | null>(null);
  const [requests, setRequests] = useState<RequestRecord[]>([]);
  const [items, setItems] = useState<ItemRecord[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [retainedArtifacts, setRetainedArtifacts] = useState<RetainedArtifactSummary[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const [connection, setConnection] = useState("Disconnected");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const refresh = useCallback(() => refreshRef.current(), []);
  useEffect(() => {
    setSnapshot(null); setRequests([]); setItems([]); setJobs([]); setRetainedArtifacts([]); setEvents([]); setHydrated(false); setError("");
    if (!projectId) { setConnection("No active project"); return; }
    let disposed = false;
    let socket: WebSocket | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let cursor = 0;
    let hydrateEpoch = 0;
    let appliedHydrateEpoch = 0;
    let replaying: Promise<void> | null = null;
    const path = { project_id: projectId };
    const seenNative = new Set<string>();
    const journal = new Map<number, Event>();
    const knownWork = new Map<string, WorkRecord>();
    const report = (e: unknown) => {
      if (disposed) return;
      setError(describeError(e));
      if (e instanceof ApiError && e.status === 401) onUnauthorised();
    };
    async function loadRetainedArtifacts(): Promise<void> {
      try {
        const summaries = await allPages(c => api.call("listRetainedArtifacts", { path, query: { cursor: c, limit: 100 } }));
        if (!disposed) setRetainedArtifacts(summaries);
      } catch (e) {
        if (e instanceof ApiError && (e.status === 401 || e.status >= 500)) report(e);
      }
    }
    async function hydrate() {
      const myEpoch = ++hydrateEpoch;
      const results = await Promise.allSettled([
        api.call("getProjectSnapshot", { path }),
        allPages(c => api.call("listRequests", { path, query: { cursor: c, limit: 100 } })),
        allPages(c => api.call("listItems", { path, query: { cursor: c, limit: 100 } })),
        allPages(c => api.call("listJobs", { path, query: { cursor: c, limit: 100 } })),
      ]);
      void loadRetainedArtifacts();
      if (disposed || myEpoch < appliedHydrateEpoch) return;
      appliedHydrateEpoch = myEpoch;
      const [s, r, i, j] = results;
      if (s.status === "fulfilled") {
        if (s.value.active_work) {
          const prev = knownWork.get(s.value.active_work.work_id);
          if (!prev || s.value.active_work.state_revision >= prev.state_revision) knownWork.set(s.value.active_work.work_id, s.value.active_work);
        }
        setSnapshot(prev => mergeSnapshot(prev, s.value, cursor, knownWork));
        setItems(prev => mergeItems(prev, s.value.items));
        setJobs(prev => mergeJobs(prev, s.value.jobs));
      }
      if (r.status === "fulfilled") setRequests(prev => mergeRequests(prev, r.value));
      if (i.status === "fulfilled") setItems(prev => mergeItems(prev, i.value));
      if (j.status === "fulfilled") setJobs(prev => mergeJobs(prev, j.value));
      if (s.status === "fulfilled" && r.status === "fulfilled" && i.status === "fulfilled" && j.status === "fulfilled") {
        setHydrated(true);
      }
      for (const result of results) if (result.status === "rejected") report(result.reason);
      setRevision(n => n + 1);
    }
    refreshRef.current = hydrate;
    function apply(event: Event) {
      if (disposed || event.project_id !== projectId || event.sequence <= cursor) return;
      cursor = event.sequence;
      writePrivate(`cursor.${projectId}`, cursor);
      const nativeKey = event.native_session_id && event.native_event_id ? `${event.native_session_id}:${event.native_event_id}` : null;
      if (nativeKey && seenNative.has(nativeKey)) return;
      if (nativeKey) seenNative.add(nativeKey);
      journal.set(event.sequence, event);
      setEvents([...journal.values()]);
      if (event.event_type.startsWith("request.")) {
        const record = event.payload as unknown as RequestRecord;
        setRequests(records => mergeRequests(records, [record]));
      }
      if (event.event_type === "work.state_changed") {
        const work = event.payload as unknown as WorkRecord;
        const prevWork = knownWork.get(work.work_id);
        if (!prevWork || work.state_revision >= prevWork.state_revision) knownWork.set(work.work_id, work);
        setSnapshot(s => {
          if (!s) return s;
          if (s.active_work && s.active_work.work_id === work.work_id && work.state_revision < s.active_work.state_revision) return s;
          return {
            ...s,
            event_sequence: Math.max(s.event_sequence, event.sequence),
            active_work: TERMINAL_WORK.has(work.state) ? (s.active_work?.work_id === work.work_id ? null : s.active_work) : work,
          };
        });
        void api.call("getProjectSnapshot", { path }).then(fresh => {
          if (!disposed) setSnapshot(prev => mergeSnapshot(prev, fresh, cursor, knownWork));
        }).catch(() => { /* Event state remains authoritative offline. */ });
      }
      if (event.event_type === "project.state_changed") {
        const project = event.payload as unknown as ProjectSnapshot["project"];
        setSnapshot(s => s && project.state_revision >= s.project.state_revision ? { ...s, event_sequence: Math.max(s.event_sequence, event.sequence), project } : s);
      }
      if (event.event_type === "decision.requested" || event.event_type === "decision.resolved") {
        const decision = event.payload as unknown as ProjectSnapshot["pending_decisions"][number];
        setSnapshot(s => {
          if (!s) return s;
          const existing = s.pending_decisions.find(d => d.decision_id === decision.decision_id);
          if (existing && decision.state_revision < existing.state_revision) return s;
          return {
            ...s,
            event_sequence: Math.max(s.event_sequence, event.sequence),
            pending_decisions: [...s.pending_decisions.filter(d => d.decision_id !== decision.decision_id), ...(decision.state === "pending" ? [decision] : [])],
          };
        });
      }
      if (event.event_type === "job.state_changed") {
        const job = event.payload as unknown as JobRecord;
        setJobs(old => mergeJobs(old, [job]));
        if (job.artifact_ids.length > 0) void loadRetainedArtifacts();
      }
      if (event.event_type === "tool.completed" || event.event_type === "subagent.completed") {
        const activity = event.payload as unknown as ActivityEventData;
        if (activity.artifact_ids?.length || activity.output_status) {
          void loadRetainedArtifacts();
          void api.call("getProjectSnapshot", { path }).then(fresh => {
            if (!disposed) setSnapshot(prev => mergeSnapshot(prev, fresh, cursor, knownWork));
          }).catch(() => {});
        }
      }
      if (event.event_type === "publication.created") {
        void allPages(c => api.call("listItems", { path, query: { cursor: c, limit: 100 } })).then(value => { if (!disposed) setItems(prev => mergeItems(prev, value)); }).catch(report);
        void loadRetainedArtifacts();
      }
      if (["publication.created", "frame.available", "segment.available", "annotation.saved", "selection.changed", "review.ready", "job.state_changed"].includes(event.event_type)) setRevision(n => n + 1);
    }
    async function replay() {
      if (replaying) return replaying;
      replaying = (async () => {
        while (!disposed) {
          const page = await api.call("replayProjectEvents", { path, query: { after: cursor, limit: 100 } });
          const before = cursor;
          for (const event of page.events) {
            if (event.sequence > cursor + 1) throw new Error("Event journal has a gap. Metadata remains available. Reconnect to recover.");
            apply(event);
          }
          if (cursor >= page.last_available) break;
          if (before === cursor) throw new Error("Event replay made no progress.");
        }
      })().finally(() => { replaying = null; });
      return replaying;
    }
    async function resync() {
      cursor = 0; journal.clear(); seenNative.clear(); knownWork.clear(); setEvents([]);
      await hydrate(); await replay();
    }
    function connect() {
      if (disposed) return;
      setConnection("Reconnecting");
      const url = new URL(routes.connectProjectEvents.path, location.href);
      url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(url);
      socket.onopen = () => socket?.send(JSON.stringify({ schema_version: "1.0", type: "subscribe", project_id: projectId, after_sequence: cursor }));
      let messages = Promise.resolve();
      socket.onmessage = message => {
        messages = messages.then(async () => {
          if (disposed) return;
          const frame = JSON.parse(String(message.data)) as Event | SocketControl;
          if (frame.project_id !== projectId) return;
          if ("type" in frame) {
            const control = frame as SocketControl;
            if (control.type === "welcome") {
              setConnection("Replaying");
              if (cursor > control.last_sequence || (cursor > 0 && cursor < control.earliest_sequence - 1)) await resync();
            } else if (control.type === "caught_up") {
              if (control.through_sequence > cursor) await replay();
              setConnection("Connected");
              await hydrate();
            } else if (control.type === "resync_required") await resync();
          } else {
            if (frame.sequence > cursor + 1) await replay();
            apply(frame);
          }
        }).catch(report);
      };
      socket.onerror = () => { if (!disposed) setConnection("Connection unavailable"); };
      socket.onclose = () => { if (!disposed) { setConnection("Disconnected · retrying"); timer = setTimeout(() => { void hydrate().then(replay).catch(report).finally(connect); }, 2000); } };
    }
    void hydrate().then(replay).catch(async e => {
      if (e instanceof ApiError && e.status === 410) await resync().catch(report);
      else report(e);
    }).finally(connect);
    return () => { disposed = true; clearTimeout(timer); socket?.close(); refreshRef.current = async () => {}; };
  }, [api, projectId, onUnauthorised]);
  return { snapshot, requests, items, jobs, retainedArtifacts, events, hydrated, connection, error, revision, refresh };
}
