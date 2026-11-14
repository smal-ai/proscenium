import { useEffect, useState } from "react";
import type { SelectionRecord, VersionRecord } from "../../api/generated/contracts";
import type { CatalogueSurfaceProps } from "../../api/studio";
import { allPages } from "../review/records";

export function CatalogueBrowser({api, projectId, items, selectedVersionId, refreshRevision, onSelect}: CatalogueSurfaceProps) {
  const [versions, setVersions] = useState<VersionRecord[]>([]);
  const [selections, setSelections] = useState<SelectionRecord[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    Promise.all(items.map(item => allPages(cursor => api.call("listItemVersions", {path: {project_id: projectId, item_id: item.item_id}, query: {cursor, limit: 100}}))))
      .then(pages => {if (active) setVersions(pages.flat());}).catch(e => {if (active) setError(String(e));});
    allPages(cursor => api.call("listSelections", {path: {project_id: projectId}, query: {cursor, limit: 100}})).then(result => {if (active) setSelections(result);}).catch(e => {if (active) setError(String(e));});
    return () => {active = false;};
  }, [api, projectId, items, refreshRevision]);

  async function select(version: VersionRecord, withdraw = false) {
    const previous = selections.find(s => s.slot.kind === "item" && s.slot.item_id === version.item_id);
    const body = {schema_version: "1.0" as const, operation_id: crypto.randomUUID(), project_id: projectId,
      selection_id: previous?.selection_id ?? crypto.randomUUID(), expected_revision: previous?.state_revision ?? 0,
      slot: {kind: "item" as const, item_id: version.item_id}, choice: withdraw ? null : {kind: "version" as const, version: {item_id: version.item_id, version_id: version.version_id}}, action: withdraw ? "withdraw" as const : "select" as const};
    setBusy(true);
    try {
      await api.call("saveSelection", {path: {project_id: projectId}, body, idempotencyKey: body.operation_id});
      setSelections(await allPages(cursor => api.call("listSelections", {path: {project_id: projectId}, query: {cursor, limit: 100}})));
    } catch (e) {setError(String(e));} finally {setBusy(false);}
  }

  return <section className="review-catalogue" aria-label="Version catalogue">{items.map(item => {
    const selected = selections.find(s => s.slot.kind === "item" && s.slot.item_id === item.item_id && s.state === "selected");
    const ownerVersion = selected?.choice?.kind === "version" ? selected.choice.version.version_id : null;
    return <details key={item.item_id} open><summary>{item.name}</summary>
      {versions.filter(v => v.item_id === item.item_id).sort((a,b) => b.created_at.localeCompare(a.created_at)).map(version => <article key={version.version_id}>
        <button aria-pressed={version.version_id === selectedVersionId} onClick={() => onSelect(version)}>{version.name} <small>{version.version_id}</small></button>
        <p>{item.current_version_id === version.version_id ? "Current published" : "Historical"}{ownerVersion === version.version_id ? " · Owner selected" : ""}{selectedVersionId === version.version_id ? " · Viewing (pinned)" : ""}</p>
        {selectedVersionId === version.version_id && item.current_version_id !== version.version_id && <p>Newer published version available. Your view has not moved.</p>}
        <p>Technical assessment: not recorded. Source published · {version.artifact_ids.length} registered artefacts</p>
        <button disabled={busy || ownerVersion === version.version_id} onClick={() => void select(version)}>Select for item</button>
        {ownerVersion === version.version_id && <button disabled={busy} onClick={() => void select(version, true)}>Withdraw selection</button>}
      </article>)}
    </details>;
  })}{error && <p role="alert">{error}</p>}</section>;
}
