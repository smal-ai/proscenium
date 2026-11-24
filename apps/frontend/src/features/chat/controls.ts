import type { ProjectSnapshot } from "../../api";

export function isSubmitEnter(event: {
  key: string;
  shiftKey: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  nativeEvent?: { isComposing?: boolean };
  isComposing?: boolean;
}): boolean {
  const composing = event.nativeEvent?.isComposing ?? event.isComposing ?? false;
  if (composing || event.key !== "Enter") return false;
  return !event.shiftKey && !event.altKey;
}

/** Explicit backend null is a denial, not permission to use the legacy fallback. */
export function steerWorkId(snapshot: ProjectSnapshot | null): string | null {
  const work = snapshot?.active_work;
  return snapshot?.execution ? snapshot.execution.can_steer_work_id : work && ["running", "awaiting_user"].includes(work.state) && snapshot?.project.state === "active" ? work.work_id : null;
}
export function stopWorkId(snapshot: ProjectSnapshot | null): string | null {
  const work = snapshot?.active_work;
  return snapshot?.execution ? snapshot.execution.can_stop_work_id : work && ["running", "awaiting_user"].includes(work.state) && snapshot?.project.state === "active" ? work.work_id : null;
}
