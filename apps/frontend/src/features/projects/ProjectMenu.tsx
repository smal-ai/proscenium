import { useState } from "react";
import { ChevronDown, FolderOpen, Plus, ArrowRight, MapPin, X } from "lucide-react";
import type { ApiClient, ProjectOpen, ProjectRebind, ProjectRecord, RevisionCommand } from "../../api";
import { describeError, durableCommand, id, readPrivate, writePrivate } from "../../app/storage";

function pendingRegistrations() {
  return {
    pendingCreate: readPrivate<ProjectOpen>("command.project.create"),
    pendingOpen: readPrivate<ProjectOpen>("command.project.open"),
  };
}

export function ProjectMenu({ api, projects, activeId, onRefresh }: {
  api: ApiClient; projects: ProjectRecord[]; activeId: string | null; onRefresh: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState("");
  const [{ pendingCreate, pendingOpen }, setPending] = useState(pendingRegistrations);
  const [path, setPath] = useState((pendingCreate ?? pendingOpen)?.path ?? "");
  const [name, setName] = useState(pendingCreate?.name ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = projects.find(p => p.project_id === activeId);
  const selected = projects.find(p => p.project_id === chosen);
  function toggle() {
    if (!open) {
      const pending = pendingRegistrations();
      setPending(pending);
      const original = pending.pendingCreate ?? pending.pendingOpen;
      if (!path.trim() && original) {
        setPath(original.path);
        if (!name.trim()) setName(pending.pendingCreate?.name ?? "");
      }
    }
    setOpen(!open);
  }
  async function perform(commandKey: string, task: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await task(); await onRefresh(); }
    catch (e) {
      const retained = readPrivate(`command.${commandKey}`) !== null;
      setError(describeError(e) + (retained ? " Unconfirmed commands retain their original identity. Reconcile the retained command before changing inputs." : ""));
    }
    finally { setPending(pendingRegistrations()); setBusy(false); }
  }
  function register(create: boolean, retained?: ProjectOpen) {
    const commandKey = `project.${create ? "create" : "open"}`;
    void perform(commandKey, async () => {
      const trimmedName = name.trim();
      const result = await durableCommand<ProjectOpen, ProjectRecord>(commandKey, () => retained ?? ({
        schema_version: "1.0", operation_id: id(), path: path.trim(), create, ...(create && trimmedName ? { name: trimmedName } : {}),
      }), body => api.call("openProject", { body, idempotencyKey: body.operation_id }));
      if (create) writePrivate(`created.${result.project_id}`, true);
      setChosen(result.project_id);
    });
  }
  function activate(project: ProjectRecord) {
    const commandKey = `activate.${project.project_id}`;
    void perform(commandKey, () => durableCommand<RevisionCommand, unknown>(commandKey, () => ({ schema_version: "1.0", operation_id: id(), project_id: project.project_id, expected_revision: project.state_revision }), body => api.call("activateProject", { path: { project_id: project.project_id }, body, idempotencyKey: body.operation_id })));
  }
  function rebind(project: ProjectRecord) {
    const commandKey = `rebind.${project.project_id}`;
    void perform(commandKey, () => durableCommand<ProjectRebind, unknown>(commandKey, () => ({ schema_version: "1.0", operation_id: id(), project_id: project.project_id, expected_revision: project.state_revision, new_path: path.trim() }), body => api.call("rebindProjectLocation", { path: { project_id: project.project_id }, body, idempotencyKey: body.operation_id })));
  }
  return <div className="project-menu">
    <button className="project-trigger" aria-expanded={open} onClick={toggle}><FolderOpen size={16} /><span>{active?.name ?? "Choose a project"}</span><ChevronDown size={13} /></button>
    {open && <section className="project-popover" aria-label="Projects">
      <div className="section-heading"><h2>Workstation projects</h2><button aria-label="Close projects" onClick={() => setOpen(false)}><X size={15} /></button></div>
      {active && <p>Active project ID: <code>{active.project_id}</code></p>}
      <p>Register a folder, then activate it explicitly. Switching requires the current project to be settled.</p>
      <label>Saved project<select aria-label="Saved project" value={chosen} onChange={e => setChosen(e.target.value)}><option value="">Select a saved project</option>{projects.map(p => <option key={p.project_id} value={p.project_id}>{p.name} · {p.state}</option>)}</select></label>
      {selected && <div className="project-location"><code>{selected.path}</code><span>{selected.state}</span></div>}
      <button className="primary" disabled={busy || !selected || selected.project_id === activeId} onClick={() => selected && activate(selected)}>Activate selected project<ArrowRight size={14} /></button>
      <div className="divider" />
      {([pendingCreate, pendingOpen] as const).map((pending, index) => {
        if (!pending) return null;
        const create = index === 0;
        const action = create ? "Create project" : "Open folder";
        return <section className="retry-card" key={action} aria-label={`${action} awaiting confirmation`}>
          <strong>{action} awaiting confirmation</strong>
          <code>{pending.path}{pending.name ? ` · ${pending.name}` : ""}</code>
          <p>Reconcile this original request with the server before changing the folder path.</p>
          <button className="primary" disabled={busy} onClick={() => register(create, pending)}>Reconcile {action}</button>
        </section>;
      })}
      <label>Workstation folder path<input aria-label="Workstation folder path" value={path} onChange={e => setPath(e.target.value)} placeholder="/home/you/projects/film" spellCheck={false} /></label>
      <label>New project name<input aria-label="New project name" value={name} onChange={e => setName(e.target.value)} placeholder="Untitled project" /></label>
      <div className="button-row"><button disabled={busy || !!pendingOpen || !path.trim()} onClick={() => register(false)}><FolderOpen size={14} />Open folder</button><button disabled={busy || !!pendingCreate || !path.trim()} onClick={() => register(true)}><Plus size={14} />Create project</button></div>
      <button className="subtle" disabled={busy || !selected || !path.trim()} onClick={() => selected && rebind(selected)}><MapPin size={14} />Rebind selected project to this path</button>
      <small>Rebind verifies an already moved workspace. It does not move files or start Copilot.</small>
      {error && <p role="alert">{error}</p>}
    </section>}
  </div>;
}
