import { useState } from "react";
import { LockKeyhole, ArrowRight } from "lucide-react";
import { ApiClient } from "../api";
import type { OwnerBootstrap, OwnerSession } from "../api";
import { describeError, id, readPrivate, writePrivate } from "./storage";

/** Remove the fragment synchronously, before any application reads or telemetry. */
export function capturePairing(): void {
  const code = new URLSearchParams(location.hash.slice(1)).get("pair");
  if (!code) return;
  history.replaceState(null, "", location.pathname + location.search);
  const existing = readPrivate<OwnerBootstrap>("pairing");
  if (existing?.pairing_code !== code) writePrivate("pairing", {
    schema_version: "1.0", operation_id: id(), client_nonce: id(), pairing_code: code,
  } satisfies OwnerBootstrap);
}

export function restoreOwner(api: ApiClient): boolean {
  const session = readPrivate<OwnerSession>("session");
  if (!session) return false;
  api.setCsrfToken(session.csrf_token);
  return true;
}

export function Pairing({ api, onPaired }: { api: ApiClient; onPaired: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function pair() {
    const body = readPrivate<OwnerBootstrap>("pairing");
    if (!body) return;
    setBusy(true);
    setError("");
    try {
      const session = await api.call("pairOwnerSession", { body, idempotencyKey: body.operation_id });
      api.setCsrfToken(session.csrf_token);
      writePrivate("session", session);
      writePrivate("pairing", null);
      onPaired();
    } catch { setError("Pairing was not confirmed. Retry uses the same operation and browser nonce. If the code has expired, open a fresh private pairing link from the workstation."); }
    finally { setBusy(false); }
  }
  return <main className="pairing"><section className="pairing-card">
    <div className="brand"><span className="brand-mark">P</span> PROSCENIUM</div>
    <LockKeyhole size={28} className="pairing-icon" />
    <h1>Your workstation. Your studio.</h1>
    <p>Connect this browser using the private pairing link printed by the workstation. The server keeps the owner session in an HttpOnly cookie.</p>
    {readPrivate("pairing") ? <button className="primary" disabled={busy} onClick={() => void pair()}>{busy ? "Connecting…" : "Connect this browser"}<ArrowRight size={15} /></button> : <p className="notice">Open the workstation’s private pairing URL to continue. Pairing codes do not belong in chat.</p>}
    {error && <p role="alert">{describeError(new Error(error))}</p>}
    <small>Single owner · Same-origin connection · No model starts on connect</small>
  </section></main>;
}
