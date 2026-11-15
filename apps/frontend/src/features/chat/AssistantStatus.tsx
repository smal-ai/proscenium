import { useRef } from "react";
import { AlertTriangle, Pause } from "lucide-react";
import type { AssistantStatusProps } from "../../api/studio";
import "./chat.css";

export type { AssistantStatusProps } from "../../api/studio";
export function assistantState({ snapshot, requests, submitting, stopping }: AssistantStatusProps): { state: string; label: string; reason?: string } {
  const work = snapshot?.active_work;
  const execution = snapshot?.execution;
  if (submitting) return { state: "sending", label: "Sending…" };
  if (snapshot?.pending_decisions.length) return { state: "attention", label: "Needs your input" };
  if (stopping || work?.state === "stop_requested" || requests.some(r => r.submission.type === "stop" && r.receipt.state === "stop_requested")) return { state: "stopping", label: "Stopping…" };
  if (work?.state === "outcome_uncertain" || requests.some(r => r.submission.type === "stop" && r.receipt.state === "outcome_uncertain") || execution?.hold_reason_code === "uncertain_stop") return { state: "recovery", label: "Needs recovery" };
  if (execution?.fenced || snapshot?.project.state === "awaiting_resume" || work?.state === "awaiting_resume") return { state: "paused", label: "Paused", reason: execution?.hold_reason ?? execution?.fence_reason ?? "Execution awaits explicit Resume" };
  if (work?.state === "running" && !execution?.fenced) return { state: "working", label: "Working…" };
  return { state: "ready", label: "Ready" };
}
export function AssistantStatus(props: AssistantStatusProps) {
  const last = useRef<ReturnType<typeof assistantState> | null>(null);
  const stale = props.connection !== "Connected" || props.hydrated === false;
  const derived = assistantState(props);
  if (!stale || derived.state !== "ready") last.current = derived;
  const state = stale && derived.state === "ready" ? last.current ?? { state: "unknown", label: "Last known" } : derived;
  const label = `${state.label}${stale ? " · reconnecting" : ""}`;
  return <span className={`assistant-status status-${state.state}${stale ? " stale" : ""}${props.compact ? " compact" : ""}`} role="status" title={state.reason ? `${label} · ${state.reason}` : label} aria-label={label}>
    {state.state === "recovery" ? <AlertTriangle size={12} /> : state.state === "paused" ? <Pause size={12} /> : <span className="assistant-status-dot" aria-hidden="true" />}
    {!props.compact && <span>{label}</span>}
  </span>;
}
