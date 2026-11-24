import { useCallback, useEffect, useRef, useState } from "react";
import { ApiClient, ApiError } from "../api";
import type { ActivityEventData, Event, ItemRecord, JobRecord, ProjectSnapshot, RequestRecord, RetainedArtifactSummary, ReviewTicket, SocketControl, WorkRecord } from "../api";
import { routes } from "../api/generated/operations";
import { describeError, writePrivate } from "./storage";

const TERMINAL_WORK = new Set(["completed", "failed", "stopped"]);
const TERMINAL_JOB = new Set<JobRecord["state"]>(["succeeded", "failed", "cancelled"]);
const REFRESH_EVENTS = new Set<Event["event_type"]>(["publication.created", "preview.order_changed", "frame.available", "segment.available", "annotation.saved", "selection.changed", "review.ready", "review.changed"]);
const BASE_REFRESH_EVENTS = new Set<Event["event_type"]>(["publication.created", "annotation.saved", "selection.changed", "review.ready", "review.changed"]);
const JOURNAL_GAP = "Event journal has a gap. Metadata remains available. Reconnect to recover.";

export function backfillWindows(endSequence: number, floorSequence = 0, pageSize = 100, batchSize = 4): Array<{ after: number; end: number; limit: number }> {
  const windows = [];
  for (let end = endSequence; end > floorSequence && windows.length < batchSize && pageSize > 0;) {
    const after = Math.max(floorSequence, end - pageSize);
    windows.push({ after, end, limit: end - after });
    end = after;
  }
  return windows;
}

export function validateWindowEvents(pageEvents: Event[], after: number, end: number): Event[] {
  const events = pageEvents.filter(event => event.sequence > after && event.sequence <= end);
  if (events.length !== end - after || events.some((event, i) => event.sequence !== after + i + 1)) throw new Error(JOURNAL_GAP);
  return events;
}

export function revisionScope(event: Event, prevJob?: JobRecord, receiptArtifacts?: ReadonlySet<string>) {
  const type = event.event_type;
  const payload = event.payload as {version_id?: string | null};
  if (type === "job.state_changed") {
    const job = event.payload as unknown as JobRecord;
    if (prevJob && job.state_revision < prevJob.state_revision) return {};
    const terminal = TERMINAL_JOB.has(job.state);
    // The backend emits the receipt before attaching it to the job. These
    // immutable media ticks are not changes to the catalogue's item structure.
    const nextArtifacts = job.artifact_ids.filter(id => !receiptArtifacts?.has(id)).join("\0");
    const prevArtifacts = prevJob?.artifact_ids.filter(id => !receiptArtifacts?.has(id)).join("\0");
    const catalogue = !prevJob || nextArtifacts !== prevArtifacts || (terminal && !TERMINAL_JOB.has(prevJob.state)) || job.error_code !== prevJob.error_code;
    const structural = catalogue || job.state !== prevJob.state || job.availability !== prevJob.availability;
    const material = structural || job.completed_frames !== prevJob.completed_frames;
    const versionId = job.target.version?.version_id;
    return {catalogue, base: material && !versionId, versionId: material ? versionId : undefined, immediate: terminal || structural, legacy: material};
  }
  return {
    catalogue: type === "publication.created" || type === "preview.order_changed" || type === "selection.changed",
    base: BASE_REFRESH_EVENTS.has(type),
    versionId: type === "frame.available" || type === "segment.available" || type === "preview.order_changed" ? payload.version_id ?? undefined : undefined,
    immediate: type !== "frame.available",
    legacy: REFRESH_EVENTS.has(type),
  };
}

export function versionRefreshBuffer(flush: (versions: string[]) => void, delay = 200) {
  const pending = new Set<string>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const cancel = () => { clearTimeout(timer); timer = undefined; pending.clear(); };
  return {
    cancel,
    bump(versionId: string, immediate: boolean) {
      if (immediate) {
        pending.delete(versionId);
        if (!pending.size) cancel();
        flush([versionId]);
        return;
      }
      pending.add(versionId);
      timer ??= setTimeout(() => {
        const versions = [...pending];
        cancel();
        flush(versions);
      }, delay);
    },
  };
}

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

export function mergeReviews(current: ReviewTicket[], incoming: ReviewTicket[]): ReviewTicket[] {
  const order: string[] = [];
  const seen = new Set<string>();
  for (const ticket of [...incoming, ...current]) {
    if (!seen.has(ticket.review_id)) {
      seen.add(ticket.review_id);
      order.push(ticket.review_id);
    }
  }
  const byId = new Map<string, ReviewTicket>();
  for (const ticket of current) byId.set(ticket.review_id, ticket);
  for (const ticket of incoming) {
    const prev = byId.get(ticket.review_id);
    const prevRev = prev?.revision ?? 0;
    const nextRev = ticket.revision ?? 0;
    if (!prev || nextRev >= prevRev) byId.set(ticket.review_id, ticket);
  }
  return order.map(id => byId.get(id)!).sort((a, b) => {
    const ta = a.created_at ? Date.parse(a.created_at) : NaN;
    const tb = b.created_at ? Date.parse(b.created_at) : NaN;
    if (!Number.isNaN(ta) && !Number.isNaN(tb) && tb !== ta) return tb - ta;
    return order.indexOf(a.review_id) - order.indexOf(b.review_id);
  });
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
    reviews: incoming.reviews || current.reviews ? mergeReviews(current.reviews ?? [], incoming.reviews ?? []) : undefined,
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
  const [reviews, setReviews] = useState<ReviewTicket[]>([]);
  const [retainedArtifacts, setRetainedArtifacts] = useState<RetainedArtifactSummary[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const [connection, setConnection] = useState("Disconnected");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [catalogueRevision, setCatalogueRevision] = useState(0);
  const [baseRevision, setBaseRevision] = useState(0);
  const [versionRevisions, setVersionRevisions] = useState<Record<string, number>>({});
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const refresh = useCallback(() => refreshRef.current(), []);
  useEffect(() => {
    setSnapshot(null); setRequests([]); setItems([]); setJobs([]); setReviews([]); setRetainedArtifacts([]); setEvents([]); setHydrated(false); setError("");
    setVersionRevisions({});
    if (!projectId) { setConnection("No active project"); return; }
    let disposed = false;
    let socket: WebSocket | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let cursor = 0;
    let loadEpoch = 0;
    let hydratedWatermark = 0;
    let hydrateEpoch = 0;
    let appliedHydrateEpoch = 0;
    let replaying: { epoch: number; promise: Promise<void> } | null = null;
    let retainedLoading = false;
    let retainedQueued = false;
    const path = { project_id: projectId };
    const seenNative = new Set<string>();
    const journal = new Map<number, Event>();
    const knownWork = new Map<string, WorkRecord>();
    const knownJobs = new Map<string, JobRecord>();
    const receiptArtifacts = new Set<string>();
    const currentLoad = (epoch: number) => !disposed && loadEpoch === epoch;
    const rememberCursor = (sequence: number) => {
      cursor = sequence;
      writePrivate(`cursor.${projectId}`, cursor);
    };
    const versionRefresh = versionRefreshBuffer(ids => {
      if (!disposed) setVersionRevisions(previous => ({...previous, ...Object.fromEntries(ids.map(id => [id, (Object.hasOwn(previous, id) ? previous[id] : 0) + 1]))}));
    });
    const recordJobs = (records: JobRecord[], historical = false) => {
      for (const record of records) {
        const prev = knownJobs.get(record.job_id);
        if (!prev || record.state_revision > prev.state_revision || (!historical && record.state_revision === prev.state_revision)) knownJobs.set(record.job_id, record);
      }
    };
    const report = (e: unknown) => {
      if (disposed) return;
      setError(describeError(e));
      if (e instanceof ApiError && e.status === 401) onUnauthorised();
    };
    async function loadRetainedArtifacts(): Promise<void> {
      if (disposed) return;
      if (retainedLoading) {
        retainedQueued = true;
        return;
      }
      retainedLoading = true;
      try {
        do {
          retainedQueued = false;
          try {
            const summaries = await allPages(c => api.call("listRetainedArtifacts", { path, query: { cursor: c, limit: 100 } }));
            if (!disposed) setRetainedArtifacts(summaries);
          } catch (e) {
            if (e instanceof ApiError && (e.status === 401 || e.status >= 500)) report(e);
          }
        } while (retainedQueued && !disposed);
      } finally {
        retainedLoading = false;
      }
    }
    async function hydrate() {
      const myEpoch = ++hydrateEpoch;
      const myLoad = loadEpoch;
      const results = await Promise.allSettled([
        api.call("getProjectSnapshot", { path }),
        allPages(c => api.call("listRequests", { path, query: { cursor: c, limit: 100 } })),
        allPages(c => api.call("listItems", { path, query: { cursor: c, limit: 100 } })),
        allPages(c => api.call("listJobs", { path, query: { cursor: c, limit: 100 } })),
      ]);
      if (!currentLoad(myLoad) || myEpoch < appliedHydrateEpoch) return;
      void loadRetainedArtifacts();
      appliedHydrateEpoch = myEpoch;
      const [s, r, i, j] = results;
      if (s.status === "fulfilled") {
        hydratedWatermark = s.value.event_sequence;
        if (s.value.active_work) {
          const prev = knownWork.get(s.value.active_work.work_id);
          if (!prev || s.value.active_work.state_revision >= prev.state_revision) knownWork.set(s.value.active_work.work_id, s.value.active_work);
        }
        recordJobs(s.value.jobs);
        setSnapshot(prev => mergeSnapshot(prev, s.value, cursor, knownWork));
        setItems(prev => mergeItems(prev, s.value.items));
        setJobs(prev => mergeJobs(prev, s.value.jobs));
        if (s.value.reviews) setReviews(prev => mergeReviews(prev, s.value.reviews!));
      }
      if (r.status === "fulfilled") setRequests(prev => mergeRequests(prev, r.value));
      if (i.status === "fulfilled") setItems(prev => mergeItems(prev, i.value));
      if (j.status === "fulfilled") {
        recordJobs(j.value);
        setJobs(prev => mergeJobs(prev, j.value));
      }
      if (s.status === "fulfilled" && r.status === "fulfilled" && i.status === "fulfilled" && j.status === "fulfilled") {
        setHydrated(true);
      }
      for (const result of results) if (result.status === "rejected") report(result.reason);
      setRevision(n => n + 1);
      setCatalogueRevision(n => n + 1);
      setBaseRevision(n => n + 1);
    }
    refreshRef.current = hydrate;
    function recordEvent(event: Event): boolean {
      if (event.project_id !== projectId || journal.has(event.sequence)) return false;
      const nativeKey = event.native_session_id && event.native_event_id ? `${event.native_session_id}:${event.native_event_id}` : null;
      if (nativeKey && seenNative.has(nativeKey)) return false;
      if (nativeKey) seenNative.add(nativeKey);
      journal.set(event.sequence, event);
      return true;
    }
    function recordHistoricalEvents(incoming: Event[]) {
      for (const event of incoming) {
        if (!recordEvent(event)) continue;
        if (event.event_type === "frame.available" || event.event_type === "segment.available") {
          receiptArtifacts.add((event.payload as { artifact_id: string }).artifact_id);
        } else if (event.event_type === "work.state_changed") {
          const work = event.payload as unknown as WorkRecord;
          const prior = knownWork.get(work.work_id);
          if (!prior || prior.state_revision < work.state_revision) knownWork.set(work.work_id, work);
        } else if (event.event_type === "job.state_changed") {
          recordJobs([event.payload as unknown as JobRecord], true);
        }
      }
    }
    function publishEvents() {
      if (!disposed) setEvents([...journal.values()].sort((a, b) => a.sequence - b.sequence));
    }
    function refreshSnapshot() {
      const myEpoch = loadEpoch;
      void api.call("getProjectSnapshot", { path }).then(fresh => {
        if (currentLoad(myEpoch)) setSnapshot(prev => mergeSnapshot(prev, fresh, cursor, knownWork));
      }).catch(() => { /* Event state remains authoritative offline. */ });
    }
    function apply(event: Event) {
      if (disposed || event.project_id !== projectId || event.sequence <= cursor) return;
      rememberCursor(event.sequence);
      if (!recordEvent(event)) return;
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
        refreshSnapshot();
      }
      if (event.event_type === "project.state_changed") {
        const project = event.payload as unknown as ProjectSnapshot["project"];
        setSnapshot(s => s && project.state_revision >= s.project.state_revision ? { ...s, event_sequence: Math.max(s.event_sequence, event.sequence), project } : s);
        refreshSnapshot(); // The project projection does not carry the execution gate.
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
      if (event.event_type === "frame.available" || event.event_type === "segment.available") {
        const {artifact_id} = event.payload as {artifact_id: string};
        receiptArtifacts.add(artifact_id);
      }
      const scope = revisionScope(event, event.event_type === "job.state_changed" ? knownJobs.get((event.payload as unknown as JobRecord).job_id) : undefined, receiptArtifacts);
      if (event.event_type === "job.state_changed") {
        const job = event.payload as unknown as JobRecord;
        const prevJob = knownJobs.get(job.job_id);
        const prevArtifacts = prevJob ? prevJob.artifact_ids.join("\0") : "";
        const nextArtifacts = job.artifact_ids.join("\0");
        recordJobs([job]);
        setJobs(old => mergeJobs(old, [job]));
        const draftMotion = job.kind === "render" && job.render_spec?.purpose === "draft_motion";
        const terminal = TERMINAL_JOB.has(job.state);
        // Review reads frame receipts directly. Refresh at settlement even if the artefact IDs have not changed.
        const renderSettled = job.kind === "render" && terminal && (!prevJob || !TERMINAL_JOB.has(prevJob.state));
        if (renderSettled || (scope.catalogue && (!draftMotion || terminal) && job.artifact_ids.length > 0 && nextArtifacts !== prevArtifacts)) void loadRetainedArtifacts();
      }
      if (event.event_type === "subagent.started" || event.event_type === "tool.completed" || event.event_type === "subagent.completed") {
        const activity = event.payload as unknown as ActivityEventData;
        if (activity.artifact_ids?.length) void loadRetainedArtifacts();
        if (event.event_type.startsWith("subagent.") || activity.artifact_ids?.length || activity.output_status) {
          refreshSnapshot();
        }
      }
      if (event.event_type === "publication.created") {
        void allPages(c => api.call("listItems", { path, query: { cursor: c, limit: 100 } })).then(value => { if (!disposed) setItems(prev => mergeItems(prev, value)); }).catch(report);
        void loadRetainedArtifacts();
      }
      if (event.event_type === "review.ready" || event.event_type === "review.changed") {
        const ticket = event.payload as unknown as ReviewTicket;
        setReviews(prev => mergeReviews(prev, [ticket]));
      }
      if (scope.legacy) setRevision(n => n + 1);
      if (scope.catalogue) setCatalogueRevision(n => n + 1);
      if (scope.base) setBaseRevision(n => n + 1);
      if (scope.versionId) versionRefresh.bump(scope.versionId, scope.immediate);
    }
    const expired = (e: unknown) => e instanceof ApiError && e.status === 410;
    async function replayForward(myEpoch: number) {
      if (replaying?.epoch === myEpoch) return replaying.promise;
      const promise = (async () => {
        while (currentLoad(myEpoch)) {
          const before = cursor;
          const page = await api.call("replayProjectEvents", { path, query: { after: before, limit: 100 } });
          if (!currentLoad(myEpoch)) return;
          const end = page.events.at(-1)?.sequence ?? before;
          const incoming = validateWindowEvents(page.events, before, end);
          if (incoming.length !== page.events.length || (end === before && before < page.last_available)) throw new Error(JOURNAL_GAP);
          for (const event of incoming) apply(event);
          if (incoming.length) publishEvents();
          if (cursor >= page.last_available) return;
          if (cursor === before) throw new Error(JOURNAL_GAP);
        }
      })();
      replaying = { epoch: myEpoch, promise };
      try { await promise; }
      finally { if (replaying?.promise === promise) replaying = null; }
    }
    async function fetchWave(end: number, floor: number, myEpoch: number) {
      const windows = backfillWindows(end, floor);
      const pages = await Promise.all(windows.map(({ after, limit }) => api.call("replayProjectEvents", { path, query: { after, limit } })));
      if (!currentLoad(myEpoch)) return null;
      const incoming = windows.flatMap(({ after, end }, i) => validateWindowEvents(pages[i].events, after, end));
      return { incoming, next: windows.at(-1)!.after, last: pages[0].last_available };
    }
    async function backfillOlder(end: number, floor: number, myEpoch: number) {
      try {
        while (end > floor && currentLoad(myEpoch)) {
          const wave = await fetchWave(end, floor, myEpoch);
          if (!wave || !currentLoad(myEpoch)) return;
          recordHistoricalEvents(wave.incoming);
          publishEvents();
          end = wave.next;
        }
      } catch (e) { if (currentLoad(myEpoch)) report(e); }
    }
    async function initialLoad(myEpoch: number) {
      if (!currentLoad(myEpoch)) return;
      let head = hydratedWatermark;
      let floor = 0;
      let prefix: Event[] = [];
      let wave: Awaited<ReturnType<typeof fetchWave>> = null;
      if (head > 0) {
        try { wave = await fetchWave(head, 0, myEpoch); }
        catch (e) { if (!expired(e)) throw e; head = 0; }
        if (!currentLoad(myEpoch)) return;
      }
      if (head === 0) {
        const page = await api.call("replayProjectEvents", { path, query: { after: 0, limit: 100 } });
        if (!currentLoad(myEpoch)) return;
        floor = page.events.at(-1)?.sequence ?? 0;
        head = Math.max(page.last_available, page.next_after ?? 0, floor);
        if (head === 0) return;
        prefix = validateWindowEvents(page.events, 0, floor);
        if (prefix.length !== page.events.length) throw new Error(JOURNAL_GAP);
        if (floor < head) wave = await fetchWave(head, floor, myEpoch);
      }
      if (!currentLoad(myEpoch)) return;
      if (wave) recordHistoricalEvents(wave.incoming);
      recordHistoricalEvents(prefix);
      rememberCursor(head);
      publishEvents();
      if (wave && wave.last > cursor) await replayForward(myEpoch);
      if (wave && wave.next > floor && currentLoad(myEpoch)) void backfillOlder(wave.next, floor, myEpoch);
    }
    async function resync() {
      const myEpoch = ++loadEpoch;
      versionRefresh.cancel();
      hydratedWatermark = 0;
      rememberCursor(0); journal.clear(); seenNative.clear(); knownWork.clear(); knownJobs.clear(); receiptArtifacts.clear(); setEvents([]);
      await hydrate(); await initialLoad(myEpoch);
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
              if (control.through_sequence > cursor) await replayForward(loadEpoch);
              setConnection("Connected");
            } else if (control.type === "resync_required") await resync();
          } else {
            if (frame.sequence > cursor + 1) await replayForward(loadEpoch);
            if (frame.sequence > cursor + 1) throw new Error(JOURNAL_GAP);
            if (frame.sequence > cursor) { apply(frame); publishEvents(); }
          }
        }).catch(handleReplayError);
      };
      socket.onerror = () => { if (!disposed) setConnection("Connection unavailable"); };
      socket.onclose = () => { if (!disposed) { setConnection("Disconnected · retrying"); timer = setTimeout(() => {
        const myEpoch = loadEpoch;
        void hydrate().then(() => cursor === 0 ? initialLoad(myEpoch) : replayForward(myEpoch)).catch(handleReplayError).finally(connect);
      }, 2000); } };
    }
    async function handleReplayError(e: unknown) {
      if (disposed) return;
      if (expired(e)) await resync().catch(report);
      else report(e);
    }
    const firstEpoch = loadEpoch;
    void hydrate().then(() => initialLoad(firstEpoch)).catch(handleReplayError).finally(connect);
    return () => { disposed = true; loadEpoch++; versionRefresh.cancel(); clearTimeout(timer); socket?.close(); refreshRef.current = async () => {}; };
  }, [api, projectId, onUnauthorised]);
  return { snapshot, requests, items, jobs, reviews, retainedArtifacts, events, hydrated, connection, error, revision, catalogueRevision, baseRevision, versionRevisions, refresh };
}
