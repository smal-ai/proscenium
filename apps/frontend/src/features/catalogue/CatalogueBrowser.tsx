import { useEffect, useRef, useState } from "react";
import type { SelectionRecord, SelectionSave, VersionRecord } from "../../api/generated/contracts";
import { ApiError } from "../../api/client";
import type { CatalogueSurfaceProps } from "../../api/studio";
import { allPages } from "../review/records";
import { readPendingSelection, writePendingSelection, type PendingSelection } from "./pendingSelection";

export function CatalogueBrowser(props: CatalogueSurfaceProps) {
  return <Catalogue key={props.projectId} {...props}/>;
}

function Catalogue({api, projectId, items, selectedVersionId, refreshRevision, onSelect}: CatalogueSurfaceProps) {
  const [versions, setVersions] = useState<VersionRecord[]>([]);
  const [selections, setSelections] = useState<SelectionRecord[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [pending, setPending] = useState<PendingSelection | null>(null);
  const locked = useRef(false);

  async function authoritativeChoices() {
    const choices = await allPages(cursor => api.call("listSelections", {path: {project_id: projectId}, query: {cursor, limit: 100}}));
    setSelections(choices);
  }

  useEffect(() => {
    let active = true;
    Promise.all(items.map(item => allPages(cursor => api.call("listItemVersions", {path: {project_id: projectId, item_id: item.item_id}, query: {cursor, limit: 100}}))))
      .then(pages => {if (active) setVersions(pages.flat());}).catch(e => {if (active) setError(String(e));});
    Promise.all([readPendingSelection(projectId), allPages(cursor => api.call("listSelections", {path: {project_id: projectId}, query: {cursor, limit: 100}}))])
      .then(([command, choices]) => {if (active) {setPending(command); setSelections(choices); setReady(true);}})
      .catch(e => {if (active) {setReady(false); setError(String(e));}});
    return () => {active = false;};
  }, [api, projectId, items, refreshRevision]);

  async function select(version: VersionRecord, withdraw = false) {
    if (locked.current || pending || !ready) return;
    const previous = selections.find(s => s.slot.kind === "item" && s.slot.item_id === version.item_id);
    const body: SelectionSave = {schema_version: "1.0", operation_id: crypto.randomUUID(), project_id: projectId,
      selection_id: previous?.selection_id ?? crypto.randomUUID(), expected_revision: previous?.state_revision ?? 0,
      slot: {kind: "item" as const, item_id: version.item_id}, choice: withdraw ? null : {kind: "version" as const, version: {item_id: version.item_id, version_id: version.version_id}}, action: withdraw ? "withdraw" as const : "select" as const};
    locked.current = true; setBusy(true); setError("");
    try {
      const command: PendingSelection = {projectId, command: body, state: "pending"};
      // Durable reservation precedes the first network effect, including on new slots.
      await writePendingSelection(projectId, command);
      setPending(command);
      await dispatch(command);
    } catch (e) {
      setError(String(e));
      try {setPending(await readPendingSelection(projectId));} catch {setReady(false);}
    } finally {locked.current = false; setBusy(false);}
  }

  async function dispatch(original: PendingSelection) {
    const body = original.command;
    let acknowledged = original.state === "acknowledged";
    try {
      if (!acknowledged) {
        await api.call("saveSelection", {path: {project_id: projectId}, body, idempotencyKey: body.operation_id});
        acknowledged = true;
        const receipt: PendingSelection = {...original, state: "acknowledged"};
        await writePendingSelection(projectId, receipt, body.operation_id);
        setPending(receipt);
      }
    } catch (e) {
      // Only a definitive unadmitted command failure permits a new owner choice.
      // Network/5xx/timeouts and conflicting idempotency evidence remain blocked.
      const unadmitted = !acknowledged && e instanceof ApiError && e.status >= 400 && e.status < 500
        && ![408, 429].includes(e.status) && e.envelope.error.code !== "idempotency_conflict";
      if (unadmitted) {
        await writePendingSelection(projectId, null, body.operation_id);
        setPending(null); setReady(false);
        try {await authoritativeChoices(); setReady(true);} catch { /* No new choice until authoritative reload succeeds. */ }
      } else {
        const retained: PendingSelection = {...original, state: acknowledged ? "acknowledged" : "uncertain"};
        await writePendingSelection(projectId, retained, body.operation_id);
        setPending(retained);
      }
      throw e;
    }
    // Reading a matching choice is not proof of an uncertain command's admission.
    // Only its same-ID acknowledgement gets us here. Newer edits remain authoritative.
    await authoritativeChoices();
    await writePendingSelection(projectId, null, body.operation_id);
    setPending(null); setReady(true);
  }

  async function retry() {
    if (locked.current || !pending) return;
    const original = structuredClone(pending);
    locked.current = true; setBusy(true); setError("");
    try {await dispatch(original);} catch (e) {setError(String(e));}
    finally {locked.current = false; setBusy(false);}
  }

  async function reconcile() {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError("");
    try {
      const original = await readPendingSelection(projectId);
      setPending(original);
      if (original?.state === "acknowledged") await dispatch(original);
      else {await authoritativeChoices(); setReady(true);}
    } catch (e) {setError(String(e));}
    finally {locked.current = false; setBusy(false);}
  }

  return <section className="review-catalogue" aria-label="Version catalogue">
    {pending && <div role="status">Selection {pending.state}. Original operation {pending.command.operation_id}, selection {pending.command.selection_id}, expected revision {pending.command.expected_revision}.
      <button disabled={busy} onClick={() => void retry()}>{pending.state === "acknowledged" ? "Reload acknowledged selection" : "Retry original selection"}</button>
    </div>}
    <button disabled={busy} onClick={() => void reconcile()}>Reconcile selection status</button>
    {items.map(item => {
    const selected = selections.find(s => s.slot.kind === "item" && s.slot.item_id === item.item_id && s.state === "selected");
    const ownerVersion = selected?.choice?.kind === "version" ? selected.choice.version.version_id : null;
    return <details key={item.item_id} open><summary>{item.name}</summary>
      {versions.filter(v => v.item_id === item.item_id).sort((a,b) => b.created_at.localeCompare(a.created_at)).map(version => <article key={version.version_id}>
        <button aria-pressed={version.version_id === selectedVersionId} onClick={() => onSelect(version)}>{version.name} <small>{version.version_id}</small></button>
        <p>{item.current_version_id === version.version_id ? "Current published" : "Historical"}{ownerVersion === version.version_id ? " · Owner selected" : ""}{selectedVersionId === version.version_id ? " · Viewing (pinned)" : ""}</p>
        {selectedVersionId === version.version_id && item.current_version_id !== version.version_id && <p>Newer published version available. Your view has not moved.</p>}
        <p>Technical assessment: not recorded. Source published · {version.artifact_ids.length} registered artefacts</p>
        <button disabled={busy || !ready || !!pending || ownerVersion === version.version_id} onClick={() => void select(version)}>Select for item</button>
        {ownerVersion === version.version_id && <button disabled={busy || !ready || !!pending} onClick={() => void select(version, true)}>Withdraw selection</button>}
      </article>)}
    </details>;
  })}{error && <p role="alert">{error}</p>}</section>;
}
