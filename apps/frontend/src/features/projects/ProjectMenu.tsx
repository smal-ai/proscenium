import { useState } from "react";
import { ChevronDown, FolderOpen, Plus, ArrowRight, MapPin, X } from "lucide-react";
import type { ApiClient, ProjectOpen, ProjectRebind, ProjectRecord, RevisionCommand } from "../../api";
import { describeError, durableCommand, id } from "../../app/storage";

export function ProjectMenu({ api, projects, activeId, onRefresh }: {
  api: ApiClient; projects: ProjectRecord[]; activeId: string | null; onRefresh: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState("");
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = projects.find(p => p.project_id === activeId);
  const selected = projects.find(p => p.project_id === chosen);
  async function perform(task: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await task(); await onRefresh(); }
    catch (e) { setError(`${describeError(e)} Unconfirmed commands retain their original identity. Retry the same action and original path after uncertainty.`); }
    finally { setBusy(false); }
  }
  function register(create: boolean) {
    void perform(async () => {
      const result = await durableCommand<ProjectOpen, ProjectRecord>(`project.${create ? "create" : "open"}`, () => ({
        schema_version: "1.0", operation_id: id(), path, create, ...(create && name ? { name } : {}),
      }), body => api.call("openProject", { body, idempotencyKey: body.operation_id }));
      setChosen(result.project_id);
    });
  }
  return <div className="project-menu">
    <button className="project-trigger" aria-expanded={open} onClick={() => setOpen(!open)}><FolderOpen size={16} /><span>{active?.name ?? "Choose a project"}</span><ChevronDown size={13} /></button>
    {open && <section className="project-popover" aria-label="Projects">
      <div className="section-heading"><h2>Workstation projects</h2><button aria-label="Close projects" onClick={() => setOpen(false)}><X size={15} /></button></div>
      <p>Register a folder, then activate it explicitly. Switching requires the current project to be settled.</p>
      <label>Saved project<select aria-label="Saved project" value={chosen} onChange={e => setChosen(e.target.value)}><option value="">Select a saved project</option>{projects.map(p => <option key={p.project_id} value={p.project_id}>{p.name} · {p.state}</option>)}</select></label>
      {selected && <div className="project-location"><code>{selected.path}</code><span>{selected.state}</span></div>}
      <button className="primary" disabled={busy || !selected || selected.project_id === activeId} onClick={() => selected && void perform(() => durableCommand<RevisionCommand, unknown>(`activate.${selected.project_id}`, () => ({ schema_version: "1.0", operation_id: id(), project_id: selected.project_id, expected_revision: selected.state_revision }), body => api.call("activateProject", { path: { project_id: selected.project_id }, body, idempotencyKey: body.operation_id })))}>Activate selected project<ArrowRight size={14} /></button>
      <div className="divider" />
      <label>Workstation folder path<input aria-label="Workstation folder path" value={path} onChange={e => setPath(e.target.value)} placeholder="/home/you/projects/film" spellCheck={false} /></label>
      <label>New project name<input aria-label="New project name" value={name} onChange={e => setName(e.target.value)} placeholder="Untitled project" /></label>
      <div className="button-row"><button disabled={busy || !path.trim()} onClick={() => register(false)}><FolderOpen size={14} />Open folder</button><button disabled={busy || !path.trim()} onClick={() => register(true)}><Plus size={14} />Create project</button></div>
      <button className="subtle" disabled={busy || !selected || !path.trim()} onClick={() => selected && void perform(() => durableCommand<ProjectRebind, unknown>(`rebind.${selected.project_id}`, () => ({ schema_version: "1.0", operation_id: id(), project_id: selected.project_id, expected_revision: selected.state_revision, new_path: path }), body => api.call("rebindProjectLocation", { path: { project_id: selected.project_id }, body, idempotencyKey: body.operation_id })))}><MapPin size={14} />Rebind selected project to this path</button>
      <small>Rebind verifies an already moved workspace. It does not move files or start Copilot.</small>
      {error && <p role="alert">{error}</p>}
    </section>}
  </div>;
}
