import type { Event, ProjectSnapshot, ResumeCommand, WorkRecord } from "../../api";

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
  return knownWork(snapshot, events).filter(record => ["stopped", "failed", "awaiting_resume"].includes(record.state));
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
