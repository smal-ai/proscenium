import type { ProjectSnapshot } from "../../api";

export type ComposerPanelMode = "message" | "target" | "jobs" | "agents";

export function draftBlocksSend(panel: ComposerPanelMode, draft: string, uploads: readonly { busy: boolean }[]): boolean {
  // A failed upload remains unresolved until it succeeds on retry or is removed.
  return (panel !== "message" && panel !== "target") || !draft.trim() || uploads.length > 0;
}

export interface SubmitKeyEvent {
  key: string;
  shiftKey: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  repeat?: boolean;
  keyCode?: number;
  nativeEvent?: { isComposing?: boolean; keyCode?: number };
  isComposing?: boolean;
}

export function isSubmitEnter(event: SubmitKeyEvent): boolean {
  const composing = event.nativeEvent?.isComposing ?? event.isComposing ?? false;
  const keyCode = event.nativeEvent?.keyCode ?? event.keyCode;
  if (composing || keyCode === 229 || event.key !== "Enter") return false;
  return !event.shiftKey && !event.altKey;
}

export function handleSubmitEnter(event: SubmitKeyEvent & { preventDefault(): void }, action: () => void): boolean {
  if (!isSubmitEnter(event)) return false;
  event.preventDefault();
  if (!event.repeat) action();
  return true;
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
