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

export function orderedEventBuffer<T extends { sequence: number }>() {
  const events: T[] = [];
  let needsSort = false;
  return {
    append(event: T) {
      if (events.length && event.sequence < events[events.length - 1].sequence) needsSort = true;
      events.push(event);
    },
    publish() {
      if (needsSort) events.sort((a, b) => a.sequence - b.sequence);
      needsSort = false;
      return events.slice();
    },
    clear() { events.length = 0; needsSort = false; },
  };
}

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

export async function readProjectHydration(api: ApiClient, path: { project_id: string }) {
  const snapshotRead = api.call("getProjectSnapshot", { path });
  const jobFallback = () => allPages(cursor => api.call("listJobs", { path, query: { cursor, limit: 100 } }));
  const results = await Promise.allSettled([
    snapshotRead,
    allPages(cursor => api.call("listRequests", { path, query: { cursor, limit: 100 } })),
    allPages(cursor => api.call("listItems", { path, query: { cursor, limit: 100 } })),
    snapshotRead.then(snapshot => Array.isArray(snapshot.jobs) ? snapshot.jobs : jobFallback(), jobFallback),
  ]);
  const [snapshot, requests, items, jobs] = results;
  // The jobs read resolves from either a valid snapshot field or a completed fallback crawl.
  const jobsReady = jobs.status === "fulfilled";
  const ready = snapshot.status === "fulfilled" && requests.status === "fulfilled" && items.status === "fulfilled" && jobsReady;
  const errors = results.flatMap(result => result.status === "rejected" ? [result.reason as unknown] : []);
  return { snapshot, requests, items, jobs, ready, errors };
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
  incomingRead: Omit<ProjectSnapshot, "jobs"> & { jobs?: unknown },
  cursor: number,
  knownWork: ReadonlyMap<string, WorkRecord>,
): ProjectSnapshot {
  const hasIncomingJobs = Array.isArray(incomingRead.jobs);
  const currentJobs = Array.isArray(current?.jobs) ? current.jobs : [];
  const incoming = hasIncomingJobs ? incomingRead as ProjectSnapshot : { ...incomingRead, jobs: currentJobs };
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
    jobs: hasIncomingJobs ? mergeJobs(currentJobs, incoming.jobs) : currentJobs,
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
  const [backfillError, setBackfillError] = useState("");
  const [revision, setRevision] = useState(0);
  const [catalogueRevision, setCatalogueRevision] = useState(0);
  const [baseRevision, setBaseRevision] = useState(0);
  const [versionRevisions, setVersionRevisions] = useState<Record<string, number>>({});
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const refresh = useCallback(() => refreshRef.current(), []);
  useEffect(() => {
    setSnapshot(null); setRequests([]); setItems([]); setJobs([]); setReviews([]); setRetainedArtifacts([]); setEvents([]); setHydrated(false); setError("");
    setVersionRevisions({});
    setBackfillError("");
    if (!projectId) { setConnection("No active project"); return; }
    let disposed = false;
    let socket: WebSocket | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let cursor = 0;
    let loadEpoch = 0;
    let hydratedWatermark = 0;
    let latestWorkSequence = 0;
    let authoritativeSnapshotSequence = 0;
    let hydrateEpoch = 0;
    let appliedHydrateEpoch = 0;
    let replaying: { epoch: number; promise: Promise<void> } | null = null;
    let pendingBackfill: { end: number; floor: number; liveAbove: number } | null = null;
    let backfilling: { epoch: number; promise: Promise<void> } | null = null;
    let failedBackfillEpoch: number | null = null;
    let resyncing: Promise<void> | null = null;
    let retainedLoad: { epoch: number; queued: boolean } | null = null;
    let snapshotRefresh: { epoch: number; queued: boolean } | null = null;
    const path = { project_id: projectId };
    const seenNative = new Set<string>();
    const journal = new Map<number, Event>();
    const orderedEvents = orderedEventBuffer<Event>();
    const knownWork = new Map<string, WorkRecord>();
    const knownJobs = new Map<string, JobRecord>();
    const decisionSequences = new Map<string, number>();
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
    async function loadRetainedArtifacts(replace = false): Promise<void> {
      if (disposed) return;
      if (retainedLoad?.epoch === loadEpoch) {
        retainedLoad.queued = true;
        return;
      }
      const crawl = { epoch: loadEpoch, queued: false };
      retainedLoad = crawl;
      if (replace) setRetainedArtifacts([]);
      try {
        do {
          crawl.queued = false;
          try {
            const summaries = await allPages(c => api.call("listRetainedArtifacts", { path, query: { cursor: c, limit: 100 } }));
            if (currentLoad(crawl.epoch)) setRetainedArtifacts(summaries);
          } catch (e) {
            if (currentLoad(crawl.epoch) && e instanceof ApiError && (e.status === 401 || e.status >= 500)) report(e);
          }
        } while (crawl.queued && currentLoad(crawl.epoch));
      } finally {
        if (retainedLoad === crawl) retainedLoad = null;
      }
    }
    async function hydrate(replace = false) {
      const myEpoch = ++hydrateEpoch;
      const myLoad = loadEpoch;
      const { snapshot: s, requests: r, items: i, jobs: j, ready, errors } = await readProjectHydration(api, path);
      if (!currentLoad(myLoad) || myEpoch < appliedHydrateEpoch) return;
      void loadRetainedArtifacts(replace);
      appliedHydrateEpoch = myEpoch;
      const hydratedSnapshot = s.status === "fulfilled"
        ? !Array.isArray(s.value.jobs) && j.status === "fulfilled" ? { ...s.value, jobs: j.value } : s.value
        : null;
      if (hydratedSnapshot) {
        hydratedWatermark = hydratedSnapshot.event_sequence;
        authoritativeSnapshotSequence = Math.max(authoritativeSnapshotSequence, hydratedSnapshot.event_sequence);
        if (hydratedSnapshot.active_work) {
          const prev = knownWork.get(hydratedSnapshot.active_work.work_id);
          if (!prev || hydratedSnapshot.active_work.state_revision >= prev.state_revision) knownWork.set(hydratedSnapshot.active_work.work_id, hydratedSnapshot.active_work);
        }
        if (!replace) {
          setSnapshot(prev => mergeSnapshot(prev, hydratedSnapshot, cursor, knownWork));
          setItems(prev => mergeItems(prev, hydratedSnapshot.items));
          const {reviews} = hydratedSnapshot;
          if (reviews) setReviews(prev => mergeReviews(prev, reviews));
        }
      }
      if (j.status === "fulfilled") recordJobs(j.value);
      if (replace) {
        setSnapshot(hydratedSnapshot ? mergeSnapshot(null, hydratedSnapshot, cursor, knownWork) : null);
        setRequests(r.status === "fulfilled" ? r.value : []);
        setItems(i.status === "fulfilled" ? i.value : s.status === "fulfilled" ? s.value.items : []);
        if (j.status === "fulfilled") setJobs(j.value);
        setReviews(s.status === "fulfilled" ? s.value.reviews ?? [] : []);
      } else {
        if (r.status === "fulfilled") setRequests(prev => mergeRequests(prev, r.value));
        if (i.status === "fulfilled") setItems(prev => mergeItems(prev, i.value));
        if (j.status === "fulfilled") setJobs(prev => mergeJobs(prev, j.value));
      }
      if (ready) setHydrated(true);
      for (const error of errors) report(error);
      setRevision(n => n + 1);
      setCatalogueRevision(n => n + 1);
      setBaseRevision(n => n + 1);
    }
    refreshRef.current = async () => {
      if (resyncing) return resyncing;
      const myEpoch = loadEpoch;
      await hydrate();
      void resumeBackfill(myEpoch);
    };
    function recordEvent(event: Event): boolean {
      if (event.project_id !== projectId || journal.has(event.sequence)) return false;
      const nativeKey = event.native_session_id && event.native_event_id ? `${event.native_session_id}:${event.native_event_id}` : null;
      if (nativeKey && seenNative.has(nativeKey)) return false;
      if (nativeKey) seenNative.add(nativeKey);
      journal.set(event.sequence, event);
      orderedEvents.append(event);
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
      if (!disposed) setEvents(orderedEvents.publish());
    }
    async function refreshSnapshot() {
      if (disposed) return;
      if (snapshotRefresh?.epoch === loadEpoch) {
        snapshotRefresh.queued = true;
        return;
      }
      const flight = { epoch: loadEpoch, queued: false };
      snapshotRefresh = flight;
      try {
        do {
          flight.queued = false;
          try {
            const fresh = await api.call("getProjectSnapshot", { path });
            if (currentLoad(flight.epoch)) {
              authoritativeSnapshotSequence = Math.max(authoritativeSnapshotSequence, fresh.event_sequence);
              setSnapshot(prev => mergeSnapshot(prev, fresh, cursor, knownWork));
            }
          } catch { /* Event state remains authoritative offline. */ }
        } while (flight.queued && currentLoad(flight.epoch));
      } finally {
        if (snapshotRefresh === flight) snapshotRefresh = null;
      }
    }
    function apply(event: Event) {
      if (disposed || event.project_id !== projectId || event.sequence <= cursor) return;
      rememberCursor(event.sequence);
      if (!recordEvent(event)) return;
      applyLiveProjection(event);
    }
    function applyLiveProjection(event: Event, backfilled = false) {
      if (event.event_type.startsWith("request.")) {
        const record = event.payload as unknown as RequestRecord;
        setRequests(records => mergeRequests(records, [record]));
      }
      if (event.event_type === "work.state_changed") {
        const work = event.payload as unknown as WorkRecord;
        const prevWork = knownWork.get(work.work_id);
        if (!prevWork || work.state_revision >= prevWork.state_revision) knownWork.set(work.work_id, work);
        if (event.sequence >= latestWorkSequence && (!backfilled || event.sequence > authoritativeSnapshotSequence)) {
          latestWorkSequence = event.sequence;
          setSnapshot(s => {
            if (!s) return s;
            if (s.active_work && s.active_work.work_id === work.work_id && work.state_revision < s.active_work.state_revision) return s;
            return {
              ...s,
              event_sequence: Math.max(s.event_sequence, event.sequence),
              active_work: TERMINAL_WORK.has(work.state) ? (s.active_work?.work_id === work.work_id ? null : s.active_work) : work,
            };
          });
        }
        void refreshSnapshot();
      }
      if (event.event_type === "project.state_changed") {
        const project = event.payload as unknown as ProjectSnapshot["project"];
        setSnapshot(s => s && project.state_revision >= s.project.state_revision ? { ...s, event_sequence: Math.max(s.event_sequence, event.sequence), project } : s);
        void refreshSnapshot(); // The project projection does not carry the execution gate.
      }
      if (event.event_type === "decision.requested" || event.event_type === "decision.resolved") {
        const decision = event.payload as unknown as ProjectSnapshot["pending_decisions"][number];
        const latest = decisionSequences.get(decision.decision_id) ?? 0;
        if (event.sequence > latest) {
          decisionSequences.set(decision.decision_id, event.sequence);
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
          void refreshSnapshot();
        }
      }
      if (event.event_type === "publication.created") {
        const myEpoch = loadEpoch;
        void allPages(c => api.call("listItems", { path, query: { cursor: c, limit: 100 } })).then(value => { if (currentLoad(myEpoch)) setItems(prev => mergeItems(prev, value)); }).catch(e => { if (currentLoad(myEpoch)) report(e); });
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
      return { incoming, next: windows.at(-1)!.after };
    }
    function recordWave(incoming: Event[], liveAbove: number, backfilled = false) {
      for (const event of incoming.sort((a, b) => a.sequence - b.sequence)) {
        if (event.sequence <= liveAbove) recordHistoricalEvents([event]);
        else if (recordEvent(event)) applyLiveProjection(event, backfilled);
      }
    }
    async function resumeBackfill(myEpoch: number, recoverExpiry = true) {
      if (!currentLoad(myEpoch) || !pendingBackfill) return;
      if (backfilling?.epoch === myEpoch) return backfilling.promise;
      const promise = (async () => {
        while (pendingBackfill && currentLoad(myEpoch)) {
          const { end, floor, liveAbove }: { end: number; floor: number; liveAbove: number } = pendingBackfill;
          const wave = await fetchWave(end, floor, myEpoch);
          if (!wave || !currentLoad(myEpoch)) return;
          recordWave(wave.incoming, liveAbove, true);
          publishEvents();
          pendingBackfill = wave.next > floor ? { end: wave.next, floor, liveAbove } : null;
          if (pendingBackfill === null) setBackfillError("");
        }
      })().catch(async e => {
        if (!currentLoad(myEpoch)) return;
        failedBackfillEpoch = myEpoch;
        if (expired(e) && recoverExpiry) await resync().catch(report);
        else {
          setBackfillError(describeError(e));
          if (e instanceof ApiError && e.status === 401) onUnauthorised();
        }
      }).finally(() => {
        if (backfilling?.promise === promise) backfilling = null;
      });
      backfilling = { epoch: myEpoch, promise };
      return promise;
    }
    async function initialLoad(myEpoch: number) {
      if (!currentLoad(myEpoch)) return;
      const baseline = hydratedWatermark;
      let head = baseline;
      let floor = 0;
      let prefix: Event[] = [];
      let wave: Awaited<ReturnType<typeof fetchWave>> = null;
      if (head > 0) {
        wave = await fetchWave(head, 0, myEpoch);
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
      recordWave([...prefix, ...(wave?.incoming ?? [])], baseline);
      rememberCursor(head);
      publishEvents();
      pendingBackfill = wave && wave.next > floor ? { end: wave.next, floor, liveAbove: baseline } : null;
      if (pendingBackfill && head > baseline) void hydrate(); // Refresh projections spanning the gap between prefix and tail.
    }
    async function resync() {
      if (disposed) return;
      if (resyncing) return resyncing;
      function resetGeneration() {
        ++loadEpoch;
        versionRefresh.cancel();
        hydratedWatermark = 0;
        latestWorkSequence = 0; authoritativeSnapshotSequence = 0;
        pendingBackfill = null; backfilling = null; setBackfillError("");
        rememberCursor(0); journal.clear(); orderedEvents.clear(); seenNative.clear(); knownWork.clear(); knownJobs.clear(); decisionSequences.clear(); receiptArtifacts.clear(); setEvents([]);
        setSnapshot(null); setRequests([]); setItems([]); setJobs([]); setReviews([]); setRetainedArtifacts([]);
        setHydrated(false); setError(""); setVersionRevisions({});
      }
      resyncing = (async () => {
        for (let attempt = 0; attempt < 2; attempt++) {
          resetGeneration();
          const myEpoch = loadEpoch;
          await hydrate(true);
          try { await initialLoad(myEpoch); return; }
          catch (e) {
            if (!currentLoad(myEpoch)) return;
            if (expired(e) && attempt === 0) continue;
            resetGeneration();
            throw e;
          }
        }
      })();
      try { await resyncing; }
      finally { resyncing = null; }
      void resumeBackfill(loadEpoch, false);
    }
    function connect() {
      if (disposed) return;
      setConnection("Reconnecting");
      const url = new URL(routes.connectProjectEvents.path, location.href);
      url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(url);
      socket.onopen = () => {
        socket?.send(JSON.stringify({ schema_version: "1.0", type: "subscribe", project_id: projectId, after_sequence: cursor }));
        void resumeBackfill(loadEpoch);
      };
      let messages = Promise.resolve();
      socket.onmessage = message => {
        const myEpoch = loadEpoch;
        messages = messages.then(async () => {
          if (resyncing) await resyncing;
          if (!currentLoad(myEpoch)) return;
          const frame = JSON.parse(String(message.data)) as Event | SocketControl;
          if (frame.project_id !== projectId) return;
          if ("type" in frame) {
            const control = frame as SocketControl;
            if (control.type === "welcome") {
              setConnection("Replaying");
              if (cursor > control.last_sequence || (cursor > 0 && cursor < control.earliest_sequence - 1)) await resync().catch(report);
            } else if (control.type === "caught_up") {
              if (control.through_sequence > cursor) await replayForward(loadEpoch);
              setConnection("Connected");
              if (failedBackfillEpoch !== loadEpoch) void resumeBackfill(loadEpoch);
            } else if (control.type === "resync_required") await resync().catch(report);
          } else {
            if (frame.sequence > cursor + 1) await replayForward(loadEpoch);
            if (frame.sequence > cursor + 1) throw new Error(JOURNAL_GAP);
            if (frame.sequence > cursor) { apply(frame); publishEvents(); }
          }
        }).catch(handleReplayError);
      };
      socket.onerror = () => { if (!disposed) setConnection("Connection unavailable"); };
      socket.onclose = () => { if (!disposed) { setConnection("Disconnected · retrying"); timer = setTimeout(() => {
        void (async () => {
          if (resyncing) await resyncing;
          const myEpoch = loadEpoch;
          await hydrate();
          if (cursor === 0) await initialLoad(myEpoch);
          else {
            void resumeBackfill(myEpoch);
            await replayForward(myEpoch);
          }
        })().catch(handleReplayError).finally(connect);
      }, 2000); } };
    }
    async function handleReplayError(e: unknown) {
      if (disposed) return;
      if (expired(e)) await resync().catch(report);
      else report(e);
    }
    const firstEpoch = loadEpoch;
    void hydrate().then(() => initialLoad(firstEpoch)).catch(handleReplayError).finally(connect);
    return () => { disposed = true; loadEpoch++; pendingBackfill = null; backfilling = null; latestWorkSequence = 0; authoritativeSnapshotSequence = 0; versionRefresh.cancel(); clearTimeout(timer); socket?.close(); refreshRef.current = async () => {}; };
  }, [api, projectId, onUnauthorised]);
  return { snapshot, requests, items, jobs, reviews, retainedArtifacts, events, hydrated, connection, error: error || backfillError, revision, catalogueRevision, baseRevision, versionRevisions, refresh };
}
