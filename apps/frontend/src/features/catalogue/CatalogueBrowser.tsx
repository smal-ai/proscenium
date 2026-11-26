import { isReadyAudio } from "../retainedAudio";
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Ellipsis, Plus } from "lucide-react";
import type { Provenance, RetainedArtifactSummary, SelectionRecord, SelectionSave, VersionRecord } from "../../api/generated/contracts";
import { ApiError } from "../../api/client";
import type { CatalogueSurfaceProps } from "../../api/studio";
import { allPages } from "../review/records";
import { readPendingSelection, writePendingSelection, type PendingSelection } from "./pendingSelection";
import { mediaUrl } from "../review/media";
import { catalogueVersionKey, disclosureKey, displayItemTitle, formatItemStatusLine, indexCatalogueSelections, indexCatalogueVersions, shortVersionLabel, type OpenDisclosure } from "./catalogueHelpers";
import "./catalogue.css";

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

type CatalogueProps = CatalogueSurfaceProps & { projectName?: string };

export const CatalogueBrowser = memo(function CatalogueBrowser(props: CatalogueProps) {
  return <Catalogue key={props.projectId} {...props}/>;
});

function Catalogue({api, projectId, projectName, items, selectedVersionId, refreshRevision, onSelect, retainedArtifacts, onCreateAsset, onInspectArtifact, visible = true}: CatalogueProps) {
  const [retained, setRetained] = useState<RetainedArtifactSummary[]>([]);
  const [retainedError, setRetainedError] = useState("");
  const [versions, setVersions] = useState<VersionRecord[]>([]);
  const loadedRef = useRef(new Map<string, {fingerprint: string; versions: VersionRecord[]}>());
  const apiRef = useRef(api);
  const itemsKey = useMemo(() => catalogueVersionKey(items), [items]);
  const [selections, setSelections] = useState<SelectionRecord[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [pending, setPending] = useState<PendingSelection | null>(null);
  const [query, setQuery] = useState("");
  const [expandedItems, setExpandedItems] = useState(new Map<string, boolean>());
  const [searchExpansion, setSearchExpansion] = useState(new Map<string, boolean>());
  const [openDisclosure, setOpenDisclosure] = useState<OpenDisclosure>(null);
  const disclosureTriggers = useRef(new Map<string, HTMLButtonElement>());
  const openKey = openDisclosure ? disclosureKey(openDisclosure) : null;
  const [retainedExpanded, setRetainedExpanded] = useState<boolean | null>(null);
  const locked = useRef(false);

  // Check after refs and parent visibility are committed, including when the Navigator is hidden.
  useLayoutEffect(() => {
    if (!openKey) return;
    if (!visible || !disclosureTriggers.current.get(openKey)?.getClientRects().length) setOpenDisclosure(null);
  });
  useLayoutEffect(() => {
    if (!openKey) return;
    const dismiss = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      event.preventDefault();
      setOpenDisclosure(null);
      disclosureTriggers.current.get(openKey)?.focus();
    };
    window.addEventListener("keydown", dismiss);
    return () => window.removeEventListener("keydown", dismiss);
  }, [openKey]);

  function disclosureTrigger(disclosure: Exclude<OpenDisclosure, null>) {
    const key = disclosureKey(disclosure);
    const expanded = key === openKey;
    return {
      ref: (button: HTMLButtonElement | null) => {
        if (button) disclosureTriggers.current.set(key, button);
        else disclosureTriggers.current.delete(key);
      },
      "aria-expanded": expanded,
      onClick: () => setOpenDisclosure(expanded ? null : disclosure),
    };
  }

  useEffect(() => {
    if (retainedArtifacts !== undefined) return;
    const controller = new AbortController();
    setRetainedError("");
    void allPages(cursor => api.call("listRetainedArtifacts", { path: { project_id: projectId }, query: { cursor, limit: 100 }, signal: controller.signal }))
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
    const controller = new AbortController();
    const {signal} = controller;
    const loaded = loadedRef.current;
    if (apiRef.current !== api) {
      loaded.clear();
      setVersions([]);
      apiRef.current = api;
    }
    const fingerprints = new Map((JSON.parse(itemsKey) as [string, string | null, number][])
      .map(([itemId, head, state]) => [itemId, JSON.stringify([head, state, refreshRevision])]));
    const publish = () => setVersions([...fingerprints.keys()].flatMap(id => loaded.get(id)?.versions ?? []));
    let pruned = false;
    for (const id of loaded.keys()) if (!fingerprints.has(id)) { loaded.delete(id); pruned = true; }
    if (pruned) publish();
    const missing = [...fingerprints].filter(([id, fingerprint]) => loaded.get(id)?.fingerprint !== fingerprint);
    if (missing.length) void Promise.all(missing.map(async ([item_id, fingerprint]) => ({ item_id, fingerprint, versions: await allPages(cursor => api.call("listItemVersions", {path: {project_id: projectId, item_id}, query: {cursor, limit: 100}, signal})) })))
      .then(results => {
        if (signal.aborted) return;
        for (const {item_id, fingerprint, versions} of results) loaded.set(item_id, {fingerprint, versions});
        publish();
      }).catch(e => {if (!signal.aborted) setError(String(e));});
    return () => controller.abort();
  }, [api, projectId, itemsKey, refreshRevision]);

  useEffect(() => {
    const controller = new AbortController();
    const {signal} = controller;
    Promise.all([readPendingSelection(projectId), allPages(cursor => api.call("listSelections", {path: {project_id: projectId}, query: {cursor, limit: 100}, signal}))])
      .then(([command, choices]) => {if (!signal.aborted) {setPending(command); setSelections(choices); setReady(true);}})
      .catch(e => {if (!signal.aborted) {setReady(false); setError(String(e));}});
    return () => controller.abort();
  }, [api, projectId, refreshRevision]);

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

  const search = query.trim().toLowerCase();
  const itemPresentation = useMemo(() => {
    const itemNames = items.map(item => item.name);
    return items.map(item => ({
      item,
      title: displayItemTitle(item.name, projectName, itemNames),
      searchText: [item.name, item.description, ...(item.tags ?? []), item.provenance?.source_url, item.provenance?.creator, item.provenance?.licence, item.provenance?.note].filter(Boolean).join(" ").toLowerCase(),
    }));
  }, [items, projectName]);
  const versionsByItem = useMemo(() => indexCatalogueVersions(versions), [versions]);
  const selectedByItem = useMemo(() => indexCatalogueSelections(selections), [selections]);
  const pendingSlot = pending?.command.slot;
  const recoveryItemId = pendingSlot?.kind === "item" && items.some(item => item.item_id === pendingSlot.item_id) ? pendingSlot.item_id : null;
  const cannotSelect = busy || !ready || pending !== null;
  const recoveryBanner = (pending || !ready) && <div className="catalogue-recovery" role="status">
    <strong>Owner selection not confirmed</strong>
    {pending && <small>{pending.state === "acknowledged" ? "Command acknowledged. Reload the recorded choice." : "Original command retained. Retry uses the same operation."}</small>}
    <div className="catalogue-actions">
      <button disabled={busy} onClick={() => void reconcile()}>Check selection status</button>
      {pending && <button disabled={busy} onClick={() => void retry()}>{pending.state === "acknowledged" ? "Reload acknowledged selection" : "Retry original selection"}</button>}
    </div>
  </div>;
  const retainedList = useMemo(() => (retainedArtifacts ?? retained).filter(({artifact, job_id, published_versions = []}) =>
    artifact.project_id === projectId
    && (isReadyAudio(artifact) || (artifact.kind === "image" && artifact.state === "ready"
      && ["image/png", "image/jpeg", "image/webp"].includes(artifact.media_type)))
    && artifact.purpose !== "draft_motion"
    && !(Boolean(job_id) && published_versions.length > 0 && artifact.artifact_id.startsWith("preview-"))), [projectId, retainedArtifacts, retained]);
  const showRetained = retainedExpanded ?? (items.length === 0 || retainedList.length > 0);

  return <section className="review-catalogue" aria-label="Version catalogue">
    <div className="catalogue-toolbar">
      <input aria-label="Search assets" type="search" placeholder="Search assets…" value={query} onChange={event => {
        setQuery(event.target.value);
        setSearchExpansion(new Map());
        setOpenDisclosure(null);
      }}/>
      {onCreateAsset && <button className="primary new-asset-btn" aria-label="New asset" onClick={onCreateAsset}><Plus size={14} aria-hidden="true"/><span className="new-asset-label">New asset</span></button>}
    </div>
    <div className="catalogue-scroll">
      {!recoveryItemId && recoveryBanner}
      {itemPresentation.map(({item, title, searchText}) => {
        const itemVersions = versionsByItem.get(item.item_id) ?? [];
        const versionMatch = !!search && itemVersions.some(v => [v.version_id, v.description].some(value => value?.toLowerCase().includes(search)));
        const itemMatch = searchText.includes(search);
        if (!itemMatch && !versionMatch && recoveryItemId !== item.item_id) return null;

        const selected = selectedByItem.get(item.item_id);
        const ownerVersion = selected?.choice?.kind === "version" ? selected.choice.version.version_id : null;
        const activeItemVersion = itemVersions.find(v => v.version_id === ownerVersion)
          ?? itemVersions.find(v => v.version_id === item.current_version_id) ?? itemVersions[0];
        const viewedVersionId = itemVersions.find(v => v.version_id === selectedVersionId)?.version_id ?? null;
        const isExpanded = (search ? searchExpansion.get(item.item_id) : undefined) ?? (versionMatch || expandedItems.get(item.item_id) === true);
        const statusLine = formatItemStatusLine({kind: item.kind, viewedVersionId, latestVersionId: item.current_version_id, ownerVersionId: ownerVersion});
        const itemTrigger = disclosureTrigger({kind: "item", itemId: item.item_id});

        return <section key={item.item_id} className="catalogue-item-group">
          <div className="catalogue-item-row">
            <button className="catalogue-icon-btn" aria-expanded={isExpanded} aria-label={`Version history for ${item.name}`} onClick={() => {
              const setExpansion = search ? setSearchExpansion : setExpandedItems;
              setExpansion(previous => new Map(previous).set(item.item_id, !isExpanded));
            }}>
              {isExpanded ? <ChevronDown size={14} aria-hidden="true"/> : <ChevronRight size={14} aria-hidden="true"/>}
            </button>
            <button className="catalogue-item-header" aria-pressed={viewedVersionId !== null} title={item.name} disabled={!activeItemVersion} onClick={() => onSelect(activeItemVersion)}>
              <span className="catalogue-item-title"><strong>{title}</strong><span className="catalogue-version-count">{itemVersions.length} {itemVersions.length === 1 ? "version" : "versions"}</span></span>
              <span className="catalogue-status-line">{statusLine}</span>
            </button>
            <button type="button" className="catalogue-icon-btn" aria-label={`Details for ${item.name}`} {...itemTrigger}><Ellipsis size={15} aria-hidden="true"/></button>
          </div>
          {itemTrigger["aria-expanded"] && <div className="catalogue-details">
            <strong>{item.name}</strong><code>{item.item_id}</code>
            {item.description && <p>{item.description}</p>}
            {!!item.tags?.length && <p>Tags: {item.tags.join(", ")}</p>}
            <ProvenanceDetails provenance={item.provenance}/>
          </div>}
          {recoveryItemId === item.item_id && recoveryBanner}
          {isExpanded && <div className="catalogue-versions">
            {itemVersions.map(version => {
              const viewing = version.version_id === selectedVersionId;
              const ownerSelected = ownerVersion === version.version_id;
              const versionTrigger = disclosureTrigger({kind: "version", itemId: item.item_id, versionId: version.version_id});
              return <div key={version.version_id} className="catalogue-version">
                <div className="catalogue-version-line">
                  <button className="catalogue-version-row" aria-pressed={viewing} title={`${version.name} (${version.version_id})`} onClick={() => onSelect(version)}>
                    <code>{shortVersionLabel(version.version_id)}</code>
                    {viewing && <span className="catalogue-badge viewing">Viewing</span>}
                    {item.current_version_id === version.version_id && <span className="catalogue-badge">Latest</span>}
                    {ownerSelected && <span className="catalogue-badge owner-selected">Owner selected</span>}
                  </button>
                  <button type="button" className="catalogue-icon-btn" aria-label={`Actions for ${version.version_id}`} {...versionTrigger}><Ellipsis size={15} aria-hidden="true"/></button>
                </div>
                {versionTrigger["aria-expanded"] && <div className="catalogue-details">
                  <div className="catalogue-actions">
                    <button disabled={cannotSelect || ownerSelected} onClick={() => void select(version)}>Select for item</button>
                    {ownerSelected && <button disabled={cannotSelect} onClick={() => void select(version, true)}>Withdraw selection</button>}
                  </div>
                  <code>{version.version_id}</code>
                  <small>Git {version.source_commit.slice(0, 10)} · {version.artifact_ids.length} {version.artifact_ids.length === 1 ? "artefact" : "artefacts"}</small>
                  {version.description && <p>{version.description}</p>}
                  <ProvenanceDetails provenance={version.provenance}/>
                </div>}
              </div>;
            })}
            {itemVersions.length === 0 && <p>No versions available.</p>}
          </div>}
        </section>;
      })}
      <section className="retained-outputs" aria-label="Retained outputs">
        <h3><button className="retained-outputs-toggle" aria-expanded={showRetained} onClick={() => setRetainedExpanded(!showRetained)}>{showRetained ? <ChevronDown size={14} aria-hidden="true"/> : <ChevronRight size={14} aria-hidden="true"/>} Retained media ({retainedList.length})</button></h3>
        {showRetained && <><p>Ready images and audio, separate from published catalogue items. Concept images are not 3D meshes.</p>
          {retainedList.map(({artifact, published_versions, attribution_status}) => {
            const url = mediaUrl(projectId, artifact.artifact_id);
            const audio = isReadyAudio(artifact);
            const label = audio ? "audio" : "image";
            const preview = !audio && <img src={url} alt={`Retained ${artifact.purpose} image`} loading="lazy" onError={event => { event.currentTarget.hidden = true; }}/>;
            return <article key={artifact.artifact_id} className="retained-output">
              <strong>{artifact.purpose} {label}</strong>
              <small>{published_versions.length ? `Also published · ${published_versions.length} version(s)` : "Unpublished retained media"} · {attribution_status === "verified" ? "Verified" : "Attribution unrecorded"}</small>
              {!audio && onInspectArtifact ? <button className="retained-preview" aria-label={`Inspect retained ${artifact.purpose} image ${artifact.artifact_id}`} onClick={() => onInspectArtifact(artifact)}>{preview}</button> : preview}
              <small>{artifact.dimensions ? `${artifact.dimensions.width} × ${artifact.dimensions.height}` : audio ? (artifact.duration_seconds === undefined ? "Duration unrecorded" : `${artifact.duration_seconds} s`) : "Dimensions unrecorded"}</small><code>{artifact.artifact_id}</code>
              <div className="button-row"><a href={url} download={artifact.artifact_id}>Download {label}</a>{onInspectArtifact && <button onClick={() => onInspectArtifact(artifact)}>Inspect {label}</button>}</div>
            </article>;
          })}
        </>}
        {retainedError && <p role="alert">{retainedError}</p>}
      </section>
      {error && <p role="alert">{error}</p>}
    </div>
  </section>;
}
