import { useCallback, useEffect, useRef, useState } from "react";
import { ApiClient, ApiError } from "../api";
import type { Event, ItemRecord, JobRecord, ProjectSnapshot, RequestRecord, SocketControl } from "../api";
import { routes } from "../api/generated/operations";
import { describeError, writePrivate } from "./storage";

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

export function useProjectData(api: ApiClient, projectId: string | null, onUnauthorised: () => void) {
  const [snapshot, setSnapshot] = useState<ProjectSnapshot | null>(null);
  const [requests, setRequests] = useState<RequestRecord[]>([]);
  const [items, setItems] = useState<ItemRecord[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [connection, setConnection] = useState("Disconnected");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const refresh = useCallback(() => refreshRef.current(), []);
  useEffect(() => {
    setSnapshot(null); setRequests([]); setItems([]); setJobs([]); setEvents([]); setError("");
    if (!projectId) { setConnection("No active project"); return; }
    let disposed = false;
    let socket: WebSocket | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let cursor = 0;
    let replaying: Promise<void> | null = null;
    const path = { project_id: projectId };
    const seenNative = new Set<string>();
    const journal = new Map<number, Event>();
    const report = (e: unknown) => {
      if (disposed) return;
      setError(describeError(e));
      if (e instanceof ApiError && e.status === 401) onUnauthorised();
    };
    async function hydrate() {
      const results = await Promise.allSettled([
        api.call("getProjectSnapshot", { path }),
        allPages(c => api.call("listRequests", { path, query: { cursor: c, limit: 100 } })),
        allPages(c => api.call("listItems", { path, query: { cursor: c, limit: 100 } })),
        allPages(c => api.call("listJobs", { path, query: { cursor: c, limit: 100 } })),
      ]);
      if (disposed) return;
      const [s, r, i, j] = results;
      if (s.status === "fulfilled") { setSnapshot(s.value); setItems(s.value.items); setJobs(s.value.jobs); }
      if (r.status === "fulfilled") setRequests(r.value);
      if (i.status === "fulfilled") setItems(i.value);
      if (j.status === "fulfilled") setJobs(j.value);
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
        setRequests(records => [...records.filter(r => r.request_id !== record.request_id), record]);
      }
      if (event.event_type === "work.state_changed") setSnapshot(s => s ? { ...s, active_work: event.payload as unknown as ProjectSnapshot["active_work"] } : s);
      if (event.event_type === "project.state_changed") setSnapshot(s => s ? { ...s, project: event.payload as unknown as ProjectSnapshot["project"] } : s);
      if (event.event_type === "decision.requested" || event.event_type === "decision.resolved") {
        const decision = event.payload as unknown as ProjectSnapshot["pending_decisions"][number];
        setSnapshot(s => s ? { ...s, pending_decisions: [...s.pending_decisions.filter(d => d.decision_id !== decision.decision_id), ...(decision.state === "pending" ? [decision] : [])] } : s);
      }
      if (event.event_type === "job.state_changed") {
        const job = event.payload as unknown as JobRecord;
        setJobs(old => [...old.filter(j => j.job_id !== job.job_id), job]);
      }
      if (event.event_type === "publication.created") void allPages(c => api.call("listItems", { path, query: { cursor: c, limit: 100 } })).then(value => { if (!disposed) setItems(value); }).catch(report);
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
      cursor = 0; journal.clear(); seenNative.clear(); setEvents([]);
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
  return { snapshot, requests, items, jobs, events, connection, error, revision, refresh };
}
