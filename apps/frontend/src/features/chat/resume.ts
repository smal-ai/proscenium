import { ApiError, type ApiClient, type Event, type ProjectSnapshot, type RequestRecord, type ResumeCommand, type ResumeOperationStatus, type WorkRecord } from "../../api";
import { describeError, durableCommand, readPrivate } from "../../app/storage";

export function isNativeSessionMissing(snapshot: ProjectSnapshot | null): boolean {
  return snapshot?.execution?.hold_reason_code === "native_session_missing" || snapshot?.execution?.fence_reason === "native_session_missing";
}

export function canStartFreshSession(snapshot: ProjectSnapshot | null, opts?: {
  resuming?: boolean; pendingResume?: ResumeCommand | null; submitting?: boolean; stopping?: boolean;
}): boolean {
  if (!snapshot || opts?.resuming || opts?.pendingResume || opts?.submitting || opts?.stopping) return false;
  if (!snapshot.execution?.fenced && snapshot.active_work !== null
    && ["running", "awaiting_user", "stop_requested"].includes(snapshot.active_work.state)) return false;
  return true;
}

export function defaultResumeMode(snapshot: ProjectSnapshot | null, options: {
  uncertainStop: boolean; hasChosenResumeWork: boolean; canReleaseQueue: boolean;
}): ResumeCommand["mode"] {
  if (isNativeSessionMissing(snapshot)) return "reset_session";
  if (options.uncertainStop) return "reconcile_only";
  if (snapshot?.active_work?.state === "awaiting_resume" && options.hasChosenResumeWork) return "continue_work";
  return options.canReleaseQueue ? "release_held_queue" : "reconcile_only";
}

export function heldRequestIds(snapshot: ProjectSnapshot | null, requests: RequestRecord[]): Set<string> {
  const latest = new Map(requests.map(request => [request.request_id, request]));
  const ids = new Set<string>();
  for (const request of snapshot?.held_requests ?? []) {
    const current = latest.get(request.request_id);
    if (!current || current.receipt.state_revision < request.receipt.state_revision) ids.add(request.request_id);
  }
  for (const request of requests) {
    if (request.receipt.state === "held") ids.add(request.request_id);
  }
  return ids;
}

export function isAutoReleasingWork(snapshot: ProjectSnapshot | null, requests: RequestRecord[], autoReleasingRequestId: string | null): boolean {
  if (!snapshot || !autoReleasingRequestId || !snapshot.active_work || snapshot.active_work.state !== "running") return false;
  const request = requests.find(r => r.request_id === autoReleasingRequestId);
  return request?.receipt.work_id === snapshot.active_work.work_id;
}

export function isIdleAutoReleaseReady(snapshot: ProjectSnapshot | null, requests: RequestRecord[], events: Event[], autoReleasingRequestId: string | null = null): boolean {
  if (isNativeSessionMissing(snapshot)) return false;
  const reason = snapshot?.execution?.hold_reason_code;
  const idleReady = snapshot?.active_work === null && (reason ?? "awaiting_resume") === "awaiting_resume";
  const autoWorkReady = reason === "awaiting_resume" && isAutoReleasingWork(snapshot, requests, autoReleasingRequestId);
  return snapshot !== null && snapshot.execution?.fenced === true
    && (idleReady || autoWorkReady)
    && snapshot.pending_decisions.length === 0
    && !requests.some(r => r.submission.type === "stop" && r.receipt.state === "outcome_uncertain")
    && resumableWork(snapshot, events, requests).length === 0
    && ![...heldRequestIds(snapshot, requests)].some(id => id !== autoReleasingRequestId);
}

/** Admission is not completion. Keep the original operation until it settles. */
export async function awaitResume(api: ApiClient, projectId: string, operationId: string, budgetMs = 15000): Promise<ResumeOperationStatus | null> {
  const deadline = Date.now() + budgetMs;
  let delay = 200;
  while (Date.now() < deadline) {
    let status: ResumeOperationStatus;
    try {
      status = await api.call("getResumeOperation", { path: { project_id: projectId, operation_id: operationId }, signal: AbortSignal.timeout(Math.max(1, deadline - Date.now())) });
    } catch (error) {
      // Older synthetic clients may not expose the additive read operation.
      if (!(error instanceof ApiError) && error instanceof Error && /(?:not implemented|unsupported|unexpected|unhandled|unknown).*(?:getResumeOperation)|getResumeOperation.*(?:not implemented|unsupported|unexpected|unhandled|unknown)/i.test(error.message)) return null;
      throw error;
    }
    if (!status || !status.state) return null;
    if (["completed", "outcome_uncertain"].includes(status.state)) return status;
    await new Promise(resolve => setTimeout(resolve, Math.min(delay, Math.max(0, deadline - Date.now()))));
    delay = Math.min(delay * 1.6, 2000);
  }
  throw new Error("Resume is still awaiting confirmation. Reconcile the original Resume, not a new operation.");
}

export class ResumeConfirmationError extends Error {
  constructor(message: string, cause: unknown) {
    super(message, { cause });
    this.name = "ResumeConfirmationError";
  }
}

export function executeResumeCommand(api: ApiClient, projectId: string, body: ResumeCommand, budgetMs?: number): Promise<ResumeOperationStatus | null> {
  const key = `resume.${projectId}`;
  const reconciling = readPrivate<ResumeCommand>(`command.${key}`) !== null;
  return durableCommand(key, () => body, async command => {
    const confirm = () => awaitResume(api, projectId, command.operation_id, budgetMs);
    if (reconciling) {
      try {
        const status = await confirm();
        if (status !== null) return status;
      } catch (error) {
        // Replay only when the original operation is absent or status reads are unsupported.
        if (!(error instanceof ApiError && error.status === 404)) throw new ResumeConfirmationError(describeError(error), error);
      }
    }
    try {
      await api.call("resumeProjectWork", { path: { project_id: projectId }, body: command, idempotencyKey: command.operation_id });
    } catch (error) {
      if (reconciling && error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        throw new ResumeConfirmationError(describeError(error), error);
      }
      throw error;
    }
    try {
      return await confirm();
    } catch (error) {
      // Status failures do not undo admission, even if the status endpoint returns 4xx.
      throw new ResumeConfirmationError(describeError(error), error);
    }
  });
}

/** Stopped work is intentionally absent from active_work, but remains in durable events. */
export function knownWork(snapshot: ProjectSnapshot | null, events: Event[]): WorkRecord[] {
  if (!snapshot) return [];
  const records = new Map<string, WorkRecord>();
  if (snapshot.active_work) records.set(snapshot.active_work.work_id, snapshot.active_work);
  for (const event of events) {
    if (event.project_id !== snapshot.project.project_id || event.event_type !== "work.state_changed") continue;
    const record = event.payload as unknown as WorkRecord;
    if (record.project_id !== snapshot.project.project_id) continue;
    const previous = records.get(record.work_id);
    if (!previous || record.state_revision > previous.state_revision) records.set(record.work_id, record);
  }
  return [...records.values()];
}

export function resumableWork(snapshot: ProjectSnapshot | null, events: Event[], requests: RequestRecord[] = []): WorkRecord[] {
  if (!snapshot || snapshot.project.native_session_id === null) return [];
  const { project_id: projectId, native_session_id: sessionId } = snapshot.project;
  const resetWorkIds = new Set<string>();
  const resetRequestIds = new Set<string>();
  for (const request of requests) {
    if (request.project_id !== projectId || request.receipt.detail_code !== "session_reset") continue;
    if (request.receipt.work_id) resetWorkIds.add(request.receipt.work_id);
    resetRequestIds.add(request.request_id);
  }
  for (const event of events) {
    if (event.project_id !== projectId || event.event_type !== "request.state_changed") continue;
    const request = event.payload as unknown as RequestRecord;
    if (request.receipt?.detail_code !== "session_reset") continue;
    if (request.receipt.work_id) resetWorkIds.add(request.receipt.work_id);
    if (event.work_id) resetWorkIds.add(event.work_id);
    if (request.request_id) resetRequestIds.add(request.request_id);
    if (event.request_id) resetRequestIds.add(event.request_id);
  }
  function canContinue(record: WorkRecord): boolean {
    return ["stopped", "failed", "awaiting_resume"].includes(record.state)
      && !(typeof sessionId === "string" && typeof record.native_session_id === "string" && record.native_session_id !== sessionId)
      && !resetWorkIds.has(record.work_id)
      && !(record.originating_request_id && resetRequestIds.has(record.originating_request_id));
  }
  const all = knownWork(snapshot, events);
  if (snapshot.active_work) {
    const current = all.find(record => record.work_id === snapshot.active_work!.work_id) ?? snapshot.active_work;
    return canContinue(current) ? [current] : [];
  }
  let latestWorkId: string | null = null;
  let latestSequence = -1;
  for (const event of events) {
    if (event.project_id !== snapshot.project.project_id || event.event_type !== "work.state_changed") continue;
    const record = event.payload as unknown as WorkRecord;
    if (record.project_id !== snapshot.project.project_id) continue;
    if (event.sequence > latestSequence) {
      latestSequence = event.sequence;
      latestWorkId = record.work_id;
    }
  }
  if (!latestWorkId) return [];
  const latest = all.find(record => record.work_id === latestWorkId);
  return latest && canContinue(latest) ? [latest] : [];
}

export function resumeCommand(
  snapshot: ProjectSnapshot, events: Event[], mode: ResumeCommand["mode"],
  targetWorkId: string, operationId: string,
): ResumeCommand {
  const projectCommand = { schema_version: "1.0" as const, operation_id: operationId, project_id: snapshot.project.project_id };
  if (mode === "reset_session") return { ...projectCommand, mode, work_id: null, expected_revision: snapshot.project.state_revision };
  const records = knownWork(snapshot, events);
  let target: WorkRecord | undefined;
  if (mode === "continue_work") {
    target = resumableWork(snapshot, events).find(record => record.work_id === targetWorkId);
    if (!target) throw new Error("Select the original stopped, failed or awaiting-Resume work first.");
  } else if (mode === "reconcile_only" && snapshot.active_work) {
    target = records.find(record => record.work_id === snapshot.active_work!.work_id);
  }
  return {
    ...projectCommand, mode, work_id: target?.work_id ?? null,
    expected_revision: target?.state_revision ?? snapshot.project.state_revision,
  };
}
