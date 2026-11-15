import { useEffect, useRef, useState } from "react";
import type { Provenance, RetainedArtifactSummary, SelectionRecord, SelectionSave, VersionRecord } from "../../api/generated/contracts";
import { ApiError } from "../../api/client";
import type { CatalogueSurfaceProps } from "../../api/studio";
import { allPages } from "../review/records";
import { readPendingSelection, writePendingSelection, type PendingSelection } from "./pendingSelection";
import { mediaUrl } from "../review/media";

function ProvenanceDetails({provenance}: {provenance: Provenance | undefined}) {
  const source = provenance?.source_url?.trim();
  let link: string | null = null;
  try {const url = new URL(source ?? ""); if (url.protocol === "http:" || url.protocol === "https:") link = url.href;} catch { /* Unknown or unsafe source remains plain text. */ }
  return <dl className="catalogue-provenance">
    <dt>Source</dt><dd>{link ? <a href={link} target="_blank" rel="noopener noreferrer">{source}</a> : source || "Unknown"}</dd>
    <dt>Creator</dt><dd>{provenance?.creator?.trim() || "Unknown"}</dd>
    <dt>Licence</dt><dd>{provenance?.licence?.trim() || "Unknown"}</dd>
    {provenance?.note && <><dt>Provenance note</dt><dd>{provenance.note}</dd></>}
  </dl>;
}

export function CatalogueBrowser(props: CatalogueSurfaceProps) {
  return <Catalogue key={props.projectId} {...props}/>;
}

function Catalogue({api, projectId, items, selectedVersionId, refreshRevision, onSelect, retainedArtifacts, onCreateAsset, onInspectArtifact}: CatalogueSurfaceProps) {
  const [retained, setRetained] = useState<RetainedArtifactSummary[]>([]);
  const [retainedError, setRetainedError] = useState("");
  const [versions, setVersions] = useState<VersionRecord[]>([]);
  const [selections, setSelections] = useState<SelectionRecord[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [pending, setPending] = useState<PendingSelection | null>(null);
  const [query, setQuery] = useState("");
  const locked = useRef(false);

  useEffect(() => {
    if (retainedArtifacts !== undefined) return;
    const controller = new AbortController();
    setRetainedError("");
    void allPages(cursor => api.call("listRetainedArtifacts", { path: { project_id: projectId }, query: { cursor, limit: 100, kind: "image" }, signal: controller.signal }))
      .then(records => { if (!controller.signal.aborted) setRetained(records); })
      .catch(cause => {
        if (controller.signal.aborted || (cause instanceof ApiError && [404, 501].includes(cause.status))) return;
        setRetainedError(cause instanceof Error ? cause.message : "Retained outputs unavailable.");
      });
    return () => controller.abort();
  }, [api, projectId, refreshRevision, retainedArtifacts]);

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
    {onCreateAsset && <button className="primary new-asset" onClick={onCreateAsset}>New asset</button>}
    <label>Search assets<input aria-label="Search assets" type="search" value={query} onChange={event => setQuery(event.target.value)}/></label>
    {pending && <div role="status">Selection {pending.state}. Original operation {pending.command.operation_id}, selection {pending.command.selection_id}, expected revision {pending.command.expected_revision}.
      <button disabled={busy} onClick={() => void retry()}>{pending.state === "acknowledged" ? "Reload acknowledged selection" : "Retry original selection"}</button>
    </div>}
    <button disabled={busy} onClick={() => void reconcile()}>Reconcile selection status</button>
    {items.filter(item => [item.name, item.description, ...(item.tags ?? []), item.provenance?.source_url, item.provenance?.creator, item.provenance?.licence, item.provenance?.note].filter(Boolean).join(" ").toLowerCase().includes(query.trim().toLowerCase())).map(item => {
    const selected = selections.find(s => s.slot.kind === "item" && s.slot.item_id === item.item_id && s.state === "selected");
    const ownerVersion = selected?.choice?.kind === "version" ? selected.choice.version.version_id : null;
    return <details key={item.item_id} open><summary>{item.name}</summary>
      {item.description && <p>{item.description}</p>}
      {!!item.tags?.length && <p>Tags: {item.tags.join(", ")}</p>}
      <ProvenanceDetails provenance={item.provenance}/>
      {versions.filter(v => v.item_id === item.item_id).sort((a,b) => b.created_at.localeCompare(a.created_at)).map(version => <article key={version.version_id}>
        <button aria-pressed={version.version_id === selectedVersionId} onClick={() => onSelect(version)}>{version.name} <small>{version.version_id}</small></button>
        <p>{item.current_version_id === version.version_id ? "Current published" : "Historical"}{ownerVersion === version.version_id ? " · Owner selected" : ""}{selectedVersionId === version.version_id ? " · Viewing (pinned)" : ""}</p>
        {selectedVersionId === version.version_id && item.current_version_id !== version.version_id && <p>Newer published version available. Your view has not moved.</p>}
        <p>Technical assessment: not recorded. Source published · {version.artifact_ids.length} registered artefacts</p>
        {version.description && <p>{version.description}</p>}
        <ProvenanceDetails provenance={version.provenance}/>
        <button disabled={busy || !ready || !!pending || ownerVersion === version.version_id} onClick={() => void select(version)}>Select for item</button>
        {ownerVersion === version.version_id && <button disabled={busy || !ready || !!pending} onClick={() => void select(version, true)}>Withdraw selection</button>}
      </article>)}
    </details>;
  })}
    <section className="retained-outputs" aria-label="Retained outputs"><h3>Retained outputs</h3><p>Ready images, separate from published catalogue items. Concept images are not 3D meshes.</p>
      {(retainedArtifacts ?? retained).filter(record => record.artifact.project_id === projectId && record.artifact.kind === "image" && record.artifact.state === "ready" && ["image/png", "image/jpeg", "image/webp"].includes(record.artifact.media_type)).map(record => {
        const artifact = record.artifact;
        return <article key={artifact.artifact_id} className="retained-output"><strong>{artifact.purpose} image</strong><small>{record.published_versions.length ? `Also published · ${record.published_versions.length} version(s)` : "Unpublished retained media"} · {record.attribution_status === "verified" ? "Verified" : "Attribution unrecorded"}</small>
          <img src={mediaUrl(projectId, artifact.artifact_id)} alt={`Retained ${artifact.purpose} image`} loading="lazy" onError={event => { event.currentTarget.hidden = true; }} />
          <small>{artifact.dimensions ? `${artifact.dimensions.width} × ${artifact.dimensions.height}` : "Dimensions unrecorded"}</small><code>{artifact.artifact_id}</code>
          <div className="button-row"><a href={mediaUrl(projectId, artifact.artifact_id)} download={artifact.artifact_id}>Download image</a>{onInspectArtifact && <button onClick={() => onInspectArtifact(artifact)}>Inspect image</button>}</div>
        </article>;
      })}
      {retainedError && <p role="alert">{retainedError}</p>}
    </section>{error && <p role="alert">{error}</p>}</section>;
}
