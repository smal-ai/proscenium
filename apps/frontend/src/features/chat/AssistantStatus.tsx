import { useRef } from "react";
import { AlertTriangle, Pause } from "lucide-react";
import type { AssistantStatusProps } from "../../api/studio";
import type { ActivityEventData } from "../../api";
import { busyAgent, nativeAgents } from "./agents";
import { isIdleAutoReleaseReady } from "./resume";
import "./chat.css";

export type { AssistantStatusProps } from "../../api/studio";
const toolLabels: Record<string, string> = {
  prepare_image_generation: "Preparing image", prepare_audio_generation: "Preparing audio", submit_generation: "Submitting image", get_job: "Waiting on job",
  get_artifact: "Inspecting output", validate_authored_document: "Validating plan", checkpoint_sources: "Checkpointing sources",
  publish_outputs: "Publishing outputs", request_vlm_review: "Requesting VLM critique", get_vlm_review: "Waiting on VLM critique", escalate_vlm_review: "Requesting owner review", request_preview: "Requesting preview",
  assemble_sequence: "Assembling sequence", run_unity_workflow: "Running Unity workflow", bash: "Running command",
  skill: "Loading skill", view: "Reading project file", get_project_capabilities: "Checking capabilities",
  search_web: "Searching the web", web_fetch: "Fetching web page",
};
export function assistantState({ snapshot, requests, events, jobs, submitting, stopping, autoReleasingRequestId = null }: AssistantStatusProps): { state: string; label: string; detail?: string; reason?: string } {
  const work = snapshot?.active_work;
  const execution = snapshot?.execution;
  if (stopping || work?.state === "stop_requested" || requests.some(r => r.submission.type === "stop" && r.receipt.state === "stop_requested")) return { state: "stopping", label: "Stopping…" };
  if (work?.state === "outcome_uncertain" || requests.some(r => r.submission.type === "stop" && r.receipt.state === "outcome_uncertain") || execution?.hold_reason_code === "uncertain_stop") return { state: "recovery", label: "Needs recovery" };
  if (isIdleAutoReleaseReady(snapshot, requests, events, autoReleasingRequestId)) {
    if (submitting) return { state: "sending", label: "Sending…" };
    return autoReleasingRequestId !== null ? { state: "working", label: "Starting…" } : { state: "ready", label: "Ready" };
  }
  if (execution?.fenced || execution?.queue_released === false || ["queue_paused", "work_stopped", "resume_pending"].includes(execution?.hold_reason_code ?? "")
    || (!execution && snapshot?.project.state === "awaiting_resume") || work?.state === "awaiting_resume") return { state: "paused", label: "Paused", reason: execution?.hold_reason ?? execution?.fence_reason ?? "Execution awaits explicit Resume" };
  if (submitting) return { state: "sending", label: "Sending…" };
  if (snapshot?.pending_decisions.length) return { state: "attention", label: "Needs your input" };
  if (work?.state === "awaiting_user") return { state: "attention", label: "Needs your input" };
  if (work?.state === "running" && !execution?.fenced) {
    const activeEvents = events.filter(event => event.project_id === snapshot?.project.project_id && (event.work_id === work.work_id || (work.native_session_id && event.native_session_id === work.native_session_id)));
    const completed = new Set(activeEvents.filter(event => event.event_type === "tool.completed").map(event => `${event.native_session_id ?? "app"}:${(event.payload as unknown as ActivityEventData).invocation_id}`));
    const tool = [...activeEvents].sort((a, b) => b.sequence - a.sequence).find(event => event.event_type === "tool.started" && (event.payload as unknown as ActivityEventData).name !== "task" && !completed.has(`${event.native_session_id ?? "app"}:${(event.payload as unknown as ActivityEventData).invocation_id}`));
    if (tool) {
      const name = (tool.payload as unknown as ActivityEventData).name;
      const detail = toolLabels[name] ?? name.replaceAll("_", " ");
      return { state: "working", label: `Working · ${detail}…`, detail };
    }
    const specialist = nativeAgents(snapshot, activeEvents).filter(busyAgent).find(agent => !agent.work_id || agent.work_id === work.work_id);
    if (specialist) {
      const detail = specialist.display_name || specialist.name;
      return { state: "working", label: `Working · ${detail}…`, detail };
    }
    const unfinished = (jobs ?? snapshot?.jobs ?? []).filter(job => !["succeeded", "failed", "cancelled"].includes(job.state));
    if (unfinished.length) return { state: "working", label: `Working · ${unfinished.length} ${unfinished.length === 1 ? "job" : "jobs"} running…` };
    return { state: "working", label: "Working…" };
  }
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
