import type { ProjectSnapshot } from "../../api";

/** Explicit backend null is a denial, not permission to use the legacy fallback. */
export function steerWorkId(snapshot: ProjectSnapshot | null): string | null {
  const work = snapshot?.active_work;
  return snapshot?.execution ? snapshot.execution.can_steer_work_id : work && ["running", "awaiting_user"].includes(work.state) && snapshot?.project.state === "active" ? work.work_id : null;
}
export function stopWorkId(snapshot: ProjectSnapshot | null): string | null {
  const work = snapshot?.active_work;
  return snapshot?.execution ? snapshot.execution.can_stop_work_id : work && ["running", "awaiting_user"].includes(work.state) && snapshot?.project.state === "active" ? work.work_id : null;
}
