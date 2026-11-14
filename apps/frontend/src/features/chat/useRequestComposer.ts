import { useState } from "react";
import { ApiError } from "../../api";
import type { ApiClient, ContextSnapshot, MessageSubmit, RequestRecord } from "../../api";
import type { TurnSelection } from "../../api/studio";
import { describeError, id, readPrivate, writePrivate } from "../../app/storage";

export interface PendingRequest {
  submission: MessageSubmit;
  context: ContextSnapshot | null;
  acknowledged: boolean;
}

export function useRequestComposer(api: ApiClient, projectId: string, onAccepted: (record: RequestRecord) => void) {
  const [pending, setPending] = useState<PendingRequest | null>(() => readPrivate(`request.${projectId}`));
  const [pendingStop, setPendingStop] = useState<PendingRequest | null>(() => readPrivate(`stop.${projectId}`));
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState("");
  function persist(value: PendingRequest | null, stop: boolean) {
    writePrivate(`${stop ? "stop" : "request"}.${projectId}`, value);
    (stop ? setPendingStop : setPending)(value);
  }
  async function send(command: PendingRequest, retry: boolean): Promise<boolean> {
    const stop = command.submission.type === "stop";
    (stop ? setStopping : setBusy)(true); setError("");
    persist(command, stop);
    const path = { project_id: projectId, request_id: command.submission.request_id };
    try {
      if (retry || command.acknowledged) {
        try {
          const record = await api.call("getRequest", { path });
          onAccepted(record); persist(null, stop); return true;
        } catch (e) {
          if (!(e instanceof ApiError) || e.status !== 404 || command.acknowledged) throw e;
        }
      }
      if (command.context) await api.call("captureContext", { path: { project_id: projectId }, body: command.context, idempotencyKey: command.context.context_id });
      await api.call("submitRequest", { path: { project_id: projectId }, body: command.submission, idempotencyKey: command.submission.request_id });
      command = { ...command, acknowledged: true }; persist(command, stop);
      const record = await api.call("getRequest", { path });
      onAccepted(record); persist(null, stop); return true;
    } catch (e) {
      setError(`${describeError(e)} The original request and captured context are retained. Reconcile before retrying. No prompt is replayed automatically.`);
      return false;
    } finally { (stop ? setStopping : setBusy)(false); }
  }
  async function submit(text: string, selection: TurnSelection, mode: "queue" | "steer", workId: string | null, profileId: string | null): Promise<boolean> {
    if (pending || busy || !text.trim()) return false;
    if (mode === "steer" && !workId) { setError("Steer requires an explicit active-work target."); return false; }
    const context: ContextSnapshot = {
      ...structuredClone(selection), schema_version: "1.0", context_id: id(), project_id: projectId, captured_at: new Date().toISOString(),
    };
    const common = { schema_version: "1.0" as const, request_id: id(), project_id: projectId, text, context_id: context.context_id };
    const submission: MessageSubmit = mode === "steer" ? { ...common, type: "steer", target_work_id: workId! } : { ...common, type: "queue", ...(profileId ? { profile_id: profileId } : {}) };
    return send({ submission, context, acknowledged: false }, false);
  }
  async function stop(workId: string) {
    if (pendingStop || stopping) return;
    await send({ submission: { schema_version: "1.0", request_id: id(), project_id: projectId, type: "stop", target_work_id: workId }, context: null, acknowledged: false }, false);
  }
  return { pending, pendingStop, busy, stopping, error, submit, stop, retry: (stop: boolean) => { const command = stop ? pendingStop : pending; return command ? send(command, true) : Promise.resolve(false); } };
}
