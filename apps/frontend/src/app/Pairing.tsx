import { useEffect, useRef, useState } from "react";
import { LockKeyhole, ArrowRight } from "lucide-react";
import { ApiClient } from "../api";
import type { OwnerBootstrap, OwnerSession } from "../api";
import { describeError, id, readPrivate, writePrivate } from "./storage";

/** Remove the fragment synchronously, before any application reads or telemetry. */
export function capturePairing({ discard = false } = {}): boolean {
  const code = new URLSearchParams(location.hash.slice(1)).get("pair");
  if (code === null) return false;
  history.replaceState(null, "", location.pathname + location.search);
  if (discard || !code) return true;
  const existing = readPrivate<OwnerBootstrap>("pairing");
  if (existing?.pairing_code !== code) writePrivate("pairing", {
    schema_version: "1.0", operation_id: id(), client_nonce: id(), pairing_code: code,
  } satisfies OwnerBootstrap);
  return true;
}

let activePairingView: (() => void) | null = null;

export function watchPairingHash(onCapture?: () => void, view: "studio" | "pairing" = "studio"): () => void {
  const target = window;
  const onHashChange = () => {
    const captured = capturePairing({ discard: !activePairingView && readPrivate<OwnerSession>("session") !== null });
    // The Studio listener may already have stripped the fragment before the view syncs.
    if (captured || view === "pairing") onCapture?.();
  };
  if (view === "pairing") activePairingView = onHashChange;
  target.addEventListener("hashchange", onHashChange);
  return () => {
    target.removeEventListener("hashchange", onHashChange);
    if (activePairingView === onHashChange) activePairingView = null;
  };
}

export function restoreOwner(api: ApiClient): boolean {
  const session = readPrivate<OwnerSession>("session");
  if (!session) return false;
  api.setCsrfToken(session.csrf_token);
  return true;
}

export function Pairing({ api, onPaired }: { api: ApiClient; onPaired: () => void }) {
  const [busyOperationId, setBusyOperationId] = useState<string | null>(null);
  const pending = useRef<{ operationId: string; controller: AbortController } | null>(null);
  const [{ currentPairing, error }, setPairingState] = useState(() => ({
    currentPairing: readPrivate<OwnerBootstrap>("pairing"), error: "",
  }));
  const busy = busyOperationId !== null && busyOperationId === currentPairing?.operation_id;
  useEffect(() => {
    const sync = () => {
      const current = readPrivate<OwnerBootstrap>("pairing");
      if (pending.current && pending.current.operationId !== current?.operation_id) {
        // Superseded HTTP responses must not replace the new request's session cookie.
        pending.current.controller.abort();
        pending.current = null;
      }
      setPairingState(previous => previous.currentPairing?.operation_id === current?.operation_id
        ? previous : { currentPairing: current, error: "" });
    };
    const stop = watchPairingHash(sync, "pairing");
    sync();
    return () => {
      stop();
      pending.current?.controller.abort();
    };
  }, []);
  async function pair() {
    const body = readPrivate<OwnerBootstrap>("pairing");
    if (!body || pending.current?.operationId === body.operation_id) return;
    const controller = new AbortController();
    pending.current = { operationId: body.operation_id, controller };
    const isCurrent = () => readPrivate<OwnerBootstrap>("pairing")?.operation_id === body.operation_id;
    setBusyOperationId(body.operation_id);
    setPairingState({ currentPairing: body, error: "" });
    try {
      const session = await api.call("pairOwnerSession", { body, idempotencyKey: body.operation_id, signal: controller.signal });
      if (!isCurrent() || controller.signal.aborted) return;
      api.setCsrfToken(session.csrf_token);
      writePrivate("session", session);
      writePrivate("pairing", null);
      onPaired();
    } catch {
      if (isCurrent() && !controller.signal.aborted) setPairingState(previous => previous.currentPairing?.operation_id === body.operation_id
        ? { ...previous, error: "Pairing was not confirmed. Retry uses the same operation and browser nonce. If the code has expired, open a fresh private pairing link from the workstation." }
        : previous);
    } finally {
      if (pending.current?.controller === controller) pending.current = null;
      setBusyOperationId(previous => previous === body.operation_id ? null : previous);
    }
  }
  return <main className="pairing"><section className="pairing-card">
    <div className="brand"><span className="brand-mark">P</span> PROSCENIUM</div>
    <LockKeyhole size={28} className="pairing-icon" />
    <h1>Your workstation. Your studio.</h1>
    <p>Connect this browser using the private pairing link printed by the workstation. The server keeps the owner session in an HttpOnly cookie.</p>
    {currentPairing ? <button className="primary" disabled={busy} onClick={() => void pair()}>{busy ? "Connecting…" : "Connect this browser"}<ArrowRight size={15} /></button> : <p className="notice">Open the workstation’s private pairing URL to continue. Pairing codes do not belong in chat.</p>}
    {error && <p role="alert">{describeError(new Error(error))}</p>}
    <small>Single owner · Same-origin connection · No model starts on connect</small>
  </section></main>;
}
