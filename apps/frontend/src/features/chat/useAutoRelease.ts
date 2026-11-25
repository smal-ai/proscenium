import { useCallback, useEffect, useState } from "react";
import type { ProjectSnapshot, RequestRecord } from "../../api";
import { heldRequestIds, isAutoReleasingWork } from "./resume";

export const AUTO_RELEASE_VISIBILITY_TIMEOUT_MS = 5_000;

export interface AutoReleaseController {
  requestId: string | null;
  start(requestId: string): void;
  settle(requestId: string, submitted: RequestRecord | null): void;
}

export function useAutoRelease(projectId: string | null, snapshot: ProjectSnapshot | null, requests: RequestRecord[]): AutoReleaseController {
  const [state, setState] = useState<{ phase: "submitting" | "settling"; requestId: string } | null>(null);
  const requestId = state?.requestId ?? null;
  const start = useCallback((id: string) => {
    // A competing submit can arrive before the composer publishes its busy state.
    setState(current => current?.phase === "submitting" ? current : { phase: "submitting", requestId: id });
  }, []);
  const settle = useCallback((id: string, submitted: RequestRecord | null) => {
    setState(current => current?.phase !== "submitting" || current.requestId !== id ? current
      : submitted ? { phase: "settling", requestId: submitted.request_id } : null);
  }, []);

  useEffect(() => {
    if (!requestId) return;
    if (snapshot?.project.project_id !== projectId || snapshot.project.state === "inactive" || !snapshot.execution?.fenced
      || (snapshot.active_work !== null
        ? snapshot.execution.hold_reason_code !== "awaiting_resume" || !isAutoReleasingWork(snapshot, requests, requestId)
        : (snapshot.execution.hold_reason_code ?? "awaiting_resume") !== "awaiting_resume")
      || [...heldRequestIds(snapshot, requests)].some(id => id !== requestId)
      || requests.some(request => request.request_id === requestId && ["cancelled", "failed", "rejected", "outcome_uncertain"].includes(request.receipt.state))) {
      setState(current => current?.requestId === requestId ? null : current);
    }
  }, [projectId, snapshot, requests, requestId]);

  useEffect(() => {
    if (!requestId) return;
    const timeout = window.setTimeout(() => setState(current => current?.requestId === requestId ? null : current), AUTO_RELEASE_VISIBILITY_TIMEOUT_MS);
    return () => window.clearTimeout(timeout);
  }, [requestId]);

  return { requestId, start, settle };
}
