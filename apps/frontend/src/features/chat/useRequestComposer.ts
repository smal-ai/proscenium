import { useEffect, useRef, useState } from "react";
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
  const [pendingNeedsConfirmation, setPendingNeedsConfirmation] = useState(() => Boolean(readPrivate(`request.${projectId}`)));
  const [pendingStopNeedsConfirmation, setPendingStopNeedsConfirmation] = useState(() => Boolean(readPrivate(`stop.${projectId}`)));
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState("");
  const mountedRef = useRef(false);
  const pendingRef = useRef(pending), pendingStopRef = useRef(pendingStop);
  const busyRef = useRef(false), stoppingRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  function persistIfOwner(next: PendingRequest | null, stop: boolean, expectedId: string) {
    const key = `${stop ? "stop" : "request"}.${projectId}`;
    const stored = readPrivate<PendingRequest>(key);
    if (stored?.submission.request_id === expectedId) {
      if (next === null) writePrivate(key, null);
      else {
        try { writePrivate(key, next); }
        catch { /* Keep the original durable command and still confirm the receipt. */ }
      }
    }
    const activePendingRef = stop ? pendingStopRef : pendingRef;
    if (mountedRef.current && activePendingRef.current?.submission.request_id === expectedId) {
      activePendingRef.current = next;
      (stop ? setPendingStop : setPending)(next);
    }
  }
  async function send(command: PendingRequest, retry: boolean): Promise<RequestRecord | null> {
    const stop = command.submission.type === "stop";
    const activeBusyRef = stop ? stoppingRef : busyRef;
    const activePendingRef = stop ? pendingStopRef : pendingRef;
    const setActiveBusy = stop ? setStopping : setBusy;
    const setNeedsConfirmation = stop ? setPendingStopNeedsConfirmation : setPendingNeedsConfirmation;
    const expectedId = command.submission.request_id;
    const key = `${stop ? "stop" : "request"}.${projectId}`;
    if (!mountedRef.current || activeBusyRef.current) return null;
    const stored = readPrivate<PendingRequest>(key);
    if (retry) {
      if (activePendingRef.current?.submission.request_id !== expectedId || (stored !== null && stored.submission.request_id !== expectedId)) return null;
    } else {
      if (activePendingRef.current !== null || stored !== null) return null;
      try { writePrivate(key, command); }
      catch (e) {
        if (mountedRef.current) setError(`${describeError(e)} Could not save request before sending. Nothing was sent.`);
        return null;
      }
    }
    activeBusyRef.current = true;
    if (!retry) {
      activePendingRef.current = command;
      (stop ? setPendingStop : setPending)(command);
    }
    setActiveBusy(true); setError("");
    setNeedsConfirmation(retry);
    const ownsState = () => mountedRef.current && activePendingRef.current?.submission.request_id === expectedId;
    function ensureStoredOwner(): boolean {
      if (readPrivate<PendingRequest>(key)?.submission.request_id === expectedId) return true;
      if (ownsState()) {
        activePendingRef.current = null;
        (stop ? setPendingStop : setPending)(null);
        setNeedsConfirmation(false);
        setError("The original request was already settled or rejected. Review your draft and submit again.");
      }
      return false;
    }
    function accept(record: RequestRecord): RequestRecord | null {
      const active = ownsState();
      if (active) { onAccepted(record); setNeedsConfirmation(false); }
      persistIfOwner(null, stop, expectedId);
      return active ? record : null;
    }
    const path = { project_id: projectId, request_id: expectedId };
    let stage: "lookup" | "context" | "submit" | "confirm" = "submit";
    try {
      if (retry || command.acknowledged) {
        stage = "lookup";
        const lookup = await api.call("getRequest", { path }).then(record => ({ record })).catch(e => {
          if (!(e instanceof ApiError) || e.status !== 404 || command.acknowledged) throw e;
          return null;
        });
        if (lookup) return accept(lookup.record);
      }
      if (command.context) {
        if (!ensureStoredOwner()) return null;
        stage = "context";
        await api.call("captureContext", { path: { project_id: projectId }, body: command.context, idempotencyKey: command.context.context_id });
        try { writePrivate(`context.${projectId}.${command.context.context_id}`, command.context); }
        catch { /* The command already retains the captured context. */ }
      }
      if (!ensureStoredOwner()) return null;
      stage = "submit";
      await api.call("submitRequest", { path: { project_id: projectId }, body: command.submission, idempotencyKey: command.submission.request_id });
      stage = "confirm";
      command = { ...command, acknowledged: true }; persistIfOwner(command, stop, expectedId);
      return accept(await api.call("getRequest", { path }));
    } catch (e) {
      const rejected = (stage === "context" || stage === "submit") && !command.acknowledged && e instanceof ApiError &&
        e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429 &&
        e.envelope.error.code !== "idempotency_conflict";
      if (!rejected && stage === "context" && !ensureStoredOwner()) return null;
      if (ownsState()) {
        setNeedsConfirmation(!rejected);
        setError(`${describeError(e)} ${rejected ? "This request was not admitted. Correct the input and submit again."
          : "The original request and captured context are retained. Reconcile before retrying. No prompt is replayed automatically."}`);
      }
      if (rejected) persistIfOwner(null, stop, expectedId);
      return null;
    } finally {
      activeBusyRef.current = false;
      if (mountedRef.current && (activePendingRef.current === null || ownsState())) setActiveBusy(false);
    }
  }
  async function submit(text: string, selection: TurnSelection, mode: "queue" | "steer", workId: string | null, profileId: string | null, requestId = id()): Promise<RequestRecord | null> {
    if (!mountedRef.current || pendingRef.current || busyRef.current || !text.trim()) return null;
    if (mode === "steer" && !workId) { setError("Steer requires an explicit active-work target."); return null; }
    const context: ContextSnapshot = {
      ...structuredClone(selection), schema_version: "1.0", context_id: id(), project_id: projectId, captured_at: new Date().toISOString(),
    };
    const common = { schema_version: "1.0" as const, request_id: requestId, project_id: projectId, text, context_id: context.context_id };
    const submission: MessageSubmit = mode === "steer" ? { ...common, type: "steer", target_work_id: workId! } : { ...common, type: "queue", ...(profileId ? { profile_id: profileId } : {}) };
    return send({ submission, context, acknowledged: false }, false);
  }
  async function stop(workId: string) {
    if (pendingStopRef.current || stoppingRef.current) return;
    await send({ submission: { schema_version: "1.0", request_id: id(), project_id: projectId, type: "stop", target_work_id: workId }, context: null, acknowledged: false }, false);
  }
  return { pending, pendingStop, pendingNeedsConfirmation, pendingStopNeedsConfirmation, busy, stopping, error, submit, stop, retry: (stop: boolean) => { const command = (stop ? pendingStopRef : pendingRef).current; return command ? send(command, true) : Promise.resolve(null); } };
}
