import { useState } from "react";
import { Settings2 } from "lucide-react";
import type { ApiClient, ConfigurationView, ProjectSettings, ProjectSnapshot } from "../api";
import { describeError, durableCommand, id } from "./storage";

export function ConfigurationMenu({ api, configuration, snapshot, onRefresh }: {
  api: ApiClient; configuration: ConfigurationView | null; snapshot: ProjectSnapshot | null; onRefresh: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const profile = configuration?.profiles.find(p => p.profile_id === snapshot?.settings.default_profile_id);
  async function update(profileId: string, limit: number) {
    if (!snapshot) return;
    setBusy(true); setError("");
    try {
      await durableCommand<ProjectSettings, unknown>(`settings.${snapshot.project.project_id}`, () => ({ schema_version: "1.0", operation_id: id(), project_id: snapshot.project.project_id, expected_revision: snapshot.settings.state_revision, default_profile_id: profileId, generation_attempt_limit: limit }), body => api.call("updateProjectSettings", { path: { project_id: body.project_id }, body, idempotencyKey: body.operation_id }));
      await onRefresh();
    } catch (e) { setError(`${describeError(e)} Retry preserves the original settings command.`); }
    finally { setBusy(false); }
  }
  return <div className="configuration-menu"><button aria-label="Services & models" aria-expanded={open} onClick={() => setOpen(!open)}><Settings2 size={14} />{profile?.name ?? "Services & models"}</button>{open && <section className="configuration-popover" aria-label="Services and models"><h2>Services & models</h2><p>Configuration metadata only. Opening this menu starts no service.</p>
    {!configuration && <p role="status">Configuration unavailable. Published work remains readable.</p>}
    {configuration && <><label>Copilot profile<select aria-label="Copilot profile" disabled={busy || !snapshot} value={snapshot?.settings.default_profile_id ?? ""} onChange={e => void update(e.target.value, snapshot?.settings.generation_attempt_limit ?? 3)}><option value="" disabled>Choose a configured profile</option>{configuration.profiles.map(p => <option key={p.profile_id} disabled={p.scope_status === "unavailable"} value={p.profile_id}>{p.name} · {p.lead_model_id ?? "model unavailable"}</option>)}</select></label>
      {!configuration.profiles.length && <p className="notice">No Copilot profiles configured. There is no automatic model fallback.</p>}
      {configuration.profiles.map(p => <div key={p.profile_id} className="service-row"><strong>{p.name}</strong><code>{p.lead_model_id ?? "Lead model unavailable"}</code><small>Workers: {p.worker_model_ids.join(", ") || "not configured"} · {p.scope_status}</small></div>)}
      <label>Generation attempts per target<input type="number" aria-label="Generation attempt limit" min={1} key={`${snapshot?.project.project_id}.${snapshot?.settings.state_revision}`} defaultValue={snapshot?.settings.generation_attempt_limit ?? configuration.generation_attempt_limit} disabled={busy || !snapshot?.settings.default_profile_id} onBlur={e => { const limit = Number(e.target.value); if (snapshot?.settings.default_profile_id && Number.isInteger(limit) && limit > 0 && limit !== snapshot.settings.generation_attempt_limit) void update(snapshot.settings.default_profile_id, limit); }} /></label>
      <small>Changes apply to future requests. Existing work keeps its admitted settings.</small>
      <h3>Independent services</h3>{!configuration.services.length && <p>None configured.</p>}{configuration.services.map(s => <div className="service-row" key={s.provider_id}><strong>{s.provider_id}<span className={s.available ? "status online" : "status"}>{s.available === null ? "Unknown" : s.available ? "Available" : "Offline"}</span></strong><code>{s.model_id ?? "Model unavailable"}</code><small>{s.endpoint}</small><small>{s.capabilities.join(" · ")}</small></div>)}</>}
    {error && <p role="alert">{error}</p>}
  </section>}</div>;
}
