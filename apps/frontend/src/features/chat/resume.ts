import { ApiError, type ApiClient, type Event, type ProjectSnapshot, type ResumeCommand, type ResumeOperationStatus, type WorkRecord } from "../../api";

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

export function resumableWork(snapshot: ProjectSnapshot | null, events: Event[]): WorkRecord[] {
  if (!snapshot) return [];
  const all = knownWork(snapshot, events);
  if (snapshot.active_work) {
    const current = all.find(record => record.work_id === snapshot.active_work!.work_id) ?? snapshot.active_work;
    return ["stopped", "failed", "awaiting_resume"].includes(current.state) ? [current] : [];
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
  return latest && ["stopped", "failed", "awaiting_resume"].includes(latest.state) ? [latest] : [];
}

export function resumeCommand(
  snapshot: ProjectSnapshot, events: Event[], mode: ResumeCommand["mode"],
  targetWorkId: string, operationId: string,
): ResumeCommand {
  const records = knownWork(snapshot, events);
  let target: WorkRecord | undefined;
  if (mode === "continue_work") {
    target = resumableWork(snapshot, events).find(record => record.work_id === targetWorkId);
    if (!target) throw new Error("Select the original stopped, failed or awaiting-Resume work first.");
  } else if (mode === "reconcile_only" && snapshot.active_work) {
    target = records.find(record => record.work_id === snapshot.active_work!.work_id);
  }
  return {
    schema_version: "1.0", operation_id: operationId, project_id: snapshot.project.project_id,
    mode, work_id: target?.work_id ?? null,
    expected_revision: target?.state_revision ?? snapshot.project.state_revision,
  };
}
