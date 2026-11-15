import { useEffect, useState } from "react";
import { Plus, RefreshCw, Settings2, X } from "lucide-react";
import type { ApiClient, AuthoringProfileDelete, AuthoringProfileUpsert, ConfigurationStatus, ConfigurationView, ProjectSettings, ProjectSnapshot } from "../api";
import { describeError, durableCommand, id } from "./storage";

type Editor = { profileId: string; name: string; lead: string; worker: string; unity: string | null; editing: boolean; customId: boolean; useForProject: boolean };
type ProfileSummary = ConfigurationView["profiles"][number];

export function ConfigurationMenu({ api, configuration, snapshot, onRefresh, onConfigurationChange }: {
  api: ApiClient; configuration: ConfigurationView | null; snapshot: ProjectSnapshot | null; onRefresh: () => Promise<void>; onConfigurationChange: (view: ConfigurationView) => void;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<ConfigurationStatus | null>(null);
  const [probing, setProbing] = useState(false);
  const [statusError, setStatusError] = useState("");
  const [editor, setEditor] = useState<Editor | null>(null);
  const effectiveProfileId = snapshot?.settings.default_profile_id ?? configuration?.profiles.find(p => p.scope_status === "project_scoped")?.profile_id ?? "";
  const profile = configuration?.profiles.find(p => p.profile_id === effectiveProfileId);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setProbing(true); setStatusError("");
    void api.call("getConfigurationStatus").then(value => { if (active) setStatus(value); }).catch(() => { if (active) setStatusError("Live status unavailable. Profile editing remains available."); }).finally(() => { if (active) setProbing(false); });
    return () => { active = false; };
  }, [api, open]);
  async function refreshStatus() {
    setProbing(true); setStatusError("");
    await Promise.allSettled([
      api.call("getConfigurationStatus").then(setStatus).catch(() => setStatusError("Live status unavailable. Profile editing remains available.")),
      api.call("getConfigurationView").then(onConfigurationChange).catch(() => setStatusError("Configuration refresh unavailable. Retained configuration is shown.")),
    ]);
    setProbing(false);
  }
  async function update(profileId: string, limit: number) {
    if (!snapshot) return;
    await durableCommand<ProjectSettings, unknown>(`settings.${snapshot.project.project_id}`, () => ({ schema_version: "1.0", operation_id: id(), project_id: snapshot.project.project_id, expected_revision: snapshot.settings.state_revision, default_profile_id: profileId, generation_attempt_limit: limit }), body => api.call("updateProjectSettings", { path: { project_id: body.project_id }, body, idempotencyKey: body.operation_id }));
    await onRefresh();
  }
  async function act(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); }
    catch (e) { setError(`${describeError(e)} Retry preserves the original command.`); }
    finally { setBusy(false); }
  }
  function edit(p?: ProfileSummary) {
    setError("");
    setEditor({ profileId: p?.profile_id ?? "", name: p?.name ?? "", lead: p?.lead_model_id ?? "gpt-6.1-sol", worker: p?.worker_model_ids[0] ?? "gpt-6.1-sol", unity: p && p.worker_model_ids.length > 1 ? p.worker_model_ids.at(-1)! : null, editing: !!p, customId: !!p, useForProject: !p && !!snapshot });
  }
  async function save() {
    if (!editor || !configuration) return;
    const form = editor;
    await act(async () => {
      const view = await durableCommand<AuthoringProfileUpsert, ConfigurationView>(`profile.save.${form.profileId}`, () => ({ schema_version: "1.0", operation_id: id(), expected_revision: configuration.revision, profile_id: form.profileId, name: form.name.trim(), lead_model_id: form.lead.trim(), worker_model_ids: form.unity ? [form.worker.trim(), form.unity] : [form.worker.trim()] }), body => api.call("upsertAuthoringProfile", { path: { profile_id: body.profile_id }, body, idempotencyKey: body.operation_id }));
      onConfigurationChange(view);
      if (form.useForProject && snapshot) await update(form.profileId, snapshot.settings.generation_attempt_limit);
      setEditor(null);
    });
  }
  async function remove(p: ProfileSummary) {
    if (!configuration) return;
    await act(async () => {
      const view = await durableCommand<AuthoringProfileDelete, ConfigurationView>(`profile.delete.${p.profile_id}`, () => ({ schema_version: "1.0", operation_id: id(), expected_revision: configuration.revision, profile_id: p.profile_id }), body => api.call("deleteAuthoringProfile", { path: { profile_id: body.profile_id }, body, idempotencyKey: body.operation_id }));
      onConfigurationChange(view);
      if (editor?.profileId === p.profile_id) setEditor(null);
    });
  }
  const suggestions = [...new Set(["gpt-6.1-sol", "claude-sonnet-5.5", "claude-opus-5.5", "gemini-4-carbon", "gpt-5.3-codex", "claude-haiku-5.5", ...(configuration?.profiles.flatMap(p => [p.lead_model_id, ...p.worker_model_ids]).filter((m): m is string => !!m) ?? [])])];
  return <div className="configuration-menu"><button aria-label={profile?.name ?? "Services & models"} title="Services & models" aria-expanded={open} onClick={() => setOpen(!open)}><Settings2 size={14} />{profile?.name ?? "Services & models"}</button>{open && <section className="configuration-popover" aria-label="Services and models">
    <header className="section-heading"><h2>Services & models</h2><div className="configuration-actions"><button aria-label="Refresh service status" disabled={probing} onClick={() => void refreshStatus()}><RefreshCw size={13} /></button><button aria-label="Close services and models" onClick={() => setOpen(false)}><X size={14} /></button></div></header>
    <small>Read-only status checks never start services or consume generation attempts.</small>
    {!configuration && <p role="status">Configuration unavailable. Published work remains readable.</p>}
    {configuration && <><section className="configuration-section"><div className="section-heading"><h3>Authoring profiles</h3><button disabled={busy} onClick={() => edit()}><Plus size={13} />New profile</button></div>
      <label>Copilot profile<select aria-label="Copilot profile" disabled={busy || !snapshot} value={effectiveProfileId} onChange={e => void act(() => update(e.target.value, snapshot?.settings.generation_attempt_limit ?? 3))}><option value="" disabled>Choose a configured profile</option>{configuration.profiles.map(p => <option key={p.profile_id} disabled={p.scope_status !== "project_scoped"} value={p.profile_id}>{p.name}</option>)}</select></label>
      {!configuration.profiles.length && <p className="notice">No Copilot profiles configured. There is no automatic model fallback.</p>}
      {editor && <form className="profile-editor" onSubmit={e => { e.preventDefault(); void save(); }}>
        <label>Profile name<input aria-label="Profile name" placeholder="e.g. Claude Sonnet Authoring" required maxLength={120} disabled={busy} value={editor.name} onChange={e => { const name = e.target.value; setEditor({ ...editor, name, profileId: editor.customId ? editor.profileId : name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || `profile-${id().slice(0, 8)}` }); }} /></label>
        <label>Profile ID<input aria-label="Profile ID" required readOnly={editor.editing} disabled={busy} value={editor.profileId} onChange={e => setEditor({ ...editor, profileId: e.target.value, customId: true })} /></label>
        <div className="profile-form-row"><label>Lead model<input aria-label="Lead model" list="copilot-model-suggestions" required maxLength={120} disabled={busy} value={editor.lead} onChange={e => setEditor({ ...editor, lead: e.target.value })} /></label><label>Worker model<input aria-label="Worker model" list="copilot-model-suggestions" required maxLength={120} disabled={busy} value={editor.worker} onChange={e => setEditor({ ...editor, worker: e.target.value })} /></label></div>
        <datalist id="copilot-model-suggestions">{suggestions.map(model => <option key={model} value={model} />)}</datalist>
        <label className="profile-checkbox"><input type="checkbox" checked={editor.useForProject} disabled={busy || !snapshot} onChange={e => setEditor({ ...editor, useForProject: e.target.checked })} />Use for this project</label>
        <div className="button-row"><button className="primary" disabled={busy}>Save profile</button><button type="button" disabled={busy} onClick={() => setEditor(null)}>Cancel</button></div>
      </form>}
      {configuration.profiles.map(p => <div key={p.profile_id} className="service-row profile-row"><div className="section-heading"><strong>{p.name}{p.profile_id === effectiveProfileId && <span className="profile-badge">Active</span>}</strong><div className="configuration-actions"><button aria-label={`Edit profile ${p.name}`} disabled={busy} onClick={() => edit(p)}>Edit</button><button aria-label={`Delete profile ${p.name}`} disabled={busy || configuration.profiles.filter(x => x.scope_status === "project_scoped").length <= 1 || snapshot?.settings.default_profile_id === p.profile_id} onClick={() => void remove(p)}>Delete</button></div></div><small>Lead: <code>{p.lead_model_id ?? "Not configured"}</code> · Worker: <code>{p.worker_model_ids.join(", ") || "Not configured"}</code></small></div>)}
    </section><section className="configuration-section"><h3>Generation budget</h3><label>Generation attempts per target<input type="number" aria-label="Generation attempt limit" min={1} key={`${snapshot?.project.project_id}.${snapshot?.settings.state_revision}`} defaultValue={snapshot?.settings.generation_attempt_limit ?? configuration.generation_attempt_limit} disabled={busy || !snapshot || !effectiveProfileId} onBlur={e => { const limit = Number(e.target.value); if (snapshot && effectiveProfileId && Number.isInteger(limit) && limit > 0 && limit !== snapshot.settings.generation_attempt_limit) void act(() => update(effectiveProfileId, limit)); }} /></label><small>Changes apply to future requests. Existing work keeps its admitted settings.</small></section>
    <section className="configuration-section"><h3>Independent services</h3>{!configuration.services.length && <p>None configured.</p>}{configuration.services.map(s => {
      const rows = status?.providers.filter(p => p.provider_id === s.provider_id) ?? [];
      const connected = rows.length > 0 && rows.every(p => ["ready", "configured"].includes(p.status));
      const ambiguous = rows.some(p => p.status === "ambiguous");
      const unreachable = rows.some(p => p.reason_code === "provider_unreachable");
      const label = rows.length ? connected ? "Connected" : ambiguous ? "Recipe needed" : unreachable ? "Unreachable" : "Unavailable" : probing && !status && s.available === null ? "Checking…" : s.available === true ? "Available" : s.available === false ? "Offline" : "Configured";
      const models = [...new Set(rows.flatMap(p => p.selected_models))].join(", ");
      const recipe = rows.find(p => p.default_dimensions);
      return <div className="service-row" key={s.provider_id}><strong>{s.provider_id}<span className={`status${connected || (!rows.length && s.available === true) ? " online" : ambiguous ? " warning" : unreachable ? " offline" : ""}`} title={connected ? s.capabilities.includes("image") ? "Ready" : "Model listed" : undefined}>{label}</span></strong><code>{models || s.model_id || "Recipe unverified"}{recipe?.default_dimensions ? ` · ${recipe.default_dimensions.width}×${recipe.default_dimensions.height}${recipe.default_steps ? ` · ${recipe.default_steps} steps` : ""}` : ""}</code><small>{s.endpoint}</small><small>{s.capabilities.join(" · ")}</small></div>;
    })}</section></>}
    {status && <section className="configuration-section"><h3>Workstation bridges</h3><div className="bridge-grid">{([["Blender CPU", status.rendering_ready, "Ready"], ["Blender MCP", status.blender_mcp_configured, "Configured"], ["BlendSwap MCP", status.blendswap_mcp_configured, "Configured"], ["Unity Editor", status.unity_configured, "Configured"]] as const).map(([name, ready, label]) => <div key={name}><span>{name}</span><span className={ready ? "status online" : "status"}>{ready ? label : "Not configured"}</span></div>)}</div></section>}
    {statusError && <small role="status">{statusError}</small>}{error && <p role="alert">{error}</p>}
  </section>}</div>;
}
