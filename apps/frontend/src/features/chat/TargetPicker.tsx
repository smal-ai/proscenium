import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { ApiClient, ItemRecord, ShotPlan, VersionRecord, VersionRef } from "../../api";
import type { TurnSelection } from "../../api/studio";
import { describeError, id } from "../../app/storage";
import { resolveTarget, targetLabel, uniqueVersions, type DraftTarget } from "./target";

export function TargetPicker({ api, projectId, items, selection, target, references, includeCurrentViewAsReference, onTarget, onReferences, onIncludeView }: {
  api: ApiClient; projectId: string; items: ItemRecord[]; selection: TurnSelection; target: DraftTarget; references: VersionRef[];
  includeCurrentViewAsReference: boolean; onTarget: (target: DraftTarget) => void; onReferences: (refs: VersionRef[]) => void; onIncludeView: (include: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [catalogue, setCatalogue] = useState(items);
  const [itemId, setItemId] = useState(() => target !== "current_view" && target.kind === "item" ? target.version.item_id : target !== "current_view" && target.kind === "shot" ? target.storyboard_version.item_id : "");
  const [versions, setVersions] = useState<VersionRecord[]>([]);
  const [versionId, setVersionId] = useState("");
  const [plan, setPlan] = useState<ShotPlan | null>(null);
  const [targetShotName, setTargetShotName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setCatalogue(items); }, [items]);
  useEffect(() => {
    if (!open) return;
    let disposed = false;
    void (async () => {
      try {
        const found: ItemRecord[] = [];
        let cursor: string | undefined;
        do {
          const page = await api.call("listItems", { path: { project_id: projectId }, query: { limit: 100, ...(cursor ? { cursor } : {}) } });
          found.push(...(page.items ?? [])); cursor = page.next_cursor ?? undefined;
        } while (cursor && !disposed);
        if (!disposed) setCatalogue(found);
      } catch (e) { if (!disposed) setError(describeError(e)); }
    })();
    return () => { disposed = true; };
  }, [api, projectId, open]);
  useEffect(() => {
    if (!itemId || !open) return;
    let disposed = false;
    setBusy(true); setError(""); setVersions([]); setVersionId(""); setPlan(null);
    void (async () => {
      try {
        const found: VersionRecord[] = [];
        let cursor: string | undefined;
        do {
          const page = await api.call("listItemVersions", { path: { project_id: projectId, item_id: itemId }, query: { limit: 100, ...(cursor ? { cursor } : {}) } });
          found.push(...(page.items ?? [])); cursor = page.next_cursor ?? undefined;
        } while (cursor && !disposed);
        if (!disposed) {
          setVersions(found);
          const pinned = target !== "current_view" && (target.kind === "item" ? target.version : target.kind === "shot" ? target.storyboard_version : null);
          setVersionId(pinned && pinned.item_id === itemId ? pinned.version_id : catalogue.find(item => item.item_id === itemId)?.current_version_id ?? found[0]?.version_id ?? "");
        }
      } catch (e) { if (!disposed) setError(describeError(e)); }
      finally { if (!disposed) setBusy(false); }
    })();
    return () => { disposed = true; };
    // The target pin must not move when publications or browsing change.
  }, [api, projectId, itemId, open]);
  useEffect(() => {
    setPlan(null);
    if (!open || !versionId || catalogue.find(item => item.item_id === itemId)?.kind !== "storyboard") return;
    let disposed = false;
    void api.call("getVersionShotPlan", { path: { project_id: projectId, version_id: versionId } }).then(value => { if (!disposed) setPlan(value); }).catch(e => { if (!disposed) setError(describeError(e)); });
    return () => { disposed = true; };
  }, [api, projectId, versionId, itemId, catalogue, open]);
  const resolved = resolveTarget(target, selection);
  const shotVersionId = resolved.kind === "shot" ? resolved.storyboard_version.version_id : null;
  const shotId = resolved.kind === "shot" ? resolved.shot_id : null;
  useEffect(() => {
    setTargetShotName("");
    if (!shotVersionId || !shotId) return;
    let disposed = false;
    void api.call("getVersionShotPlan", { path: { project_id: projectId, version_id: shotVersionId } }).then(value => { if (!disposed) setTargetShotName(value.shots.find(shot => shot.shot_id === shotId)?.intent ?? ""); }).catch(() => { /* Exact identity remains inspectable when its friendly title is unavailable. */ });
    return () => { disposed = true; };
  }, [api, projectId, shotVersionId, shotId]);
  function choose(next: DraftTarget) { onTarget(next); setOpen(false); }
  const friendly = targetShotName && resolved.kind === "shot" ? `Shot · ${catalogue.find(item => item.item_id === resolved.storyboard_version.item_id)?.name ?? "Storyboard"} · ${targetShotName}` : targetLabel(resolved, catalogue);
  const label = target === "current_view" && resolved.kind !== "project" ? `Current view · ${friendly}` : friendly;
  const ref = itemId && versionId ? { item_id: itemId, version_id: versionId } : null;
  return <div className="target-picker" onKeyDown={event => { if (event.key === "Escape") setOpen(false); }}>
    <button className="target-bar" aria-label="Choose request target and references" aria-expanded={open} aria-controls="request-target-picker" onClick={() => setOpen(!open)} title={JSON.stringify(resolved)}>Target: {label}<ChevronDown size={13} /></button>
    {open && <section id="request-target-picker" className="target-options" aria-label="Request target and references">
      <div className="target-quick"><button onClick={() => choose("current_view")}>Current view{!selection.version_id ? " · Project" : ""}</button><button onClick={() => choose({ kind: "project" })}>Project</button>
        {(["asset", "scene", "storyboard"] as const).map(kind => <button key={kind} onClick={() => choose({ kind: "new_item", item_kind: kind, intent_id: id() })}>New {kind}</button>)}
      </div>
      <label>Specific asset, scene or storyboard<select aria-label="Published item" value={itemId} onChange={event => setItemId(event.target.value)}><option value="">Choose published item</option>{catalogue.filter(item => ["asset", "scene", "storyboard"].includes(item.kind)).map(item => <option key={item.item_id} value={item.item_id}>{item.kind} · {item.name}</option>)}</select></label>
      {itemId && <label>Immutable version<select aria-label="Published version" value={versionId} disabled={busy} onChange={event => setVersionId(event.target.value)}><option value="">{busy ? "Loading versions…" : "Choose version"}</option>{versions.map(version => <option key={version.version_id} value={version.version_id}>{version.name} · {version.version_id}{version.version_id === catalogue.find(item => item.item_id === itemId)?.current_version_id ? " · current" : ""}</option>)}</select></label>}
      {ref && <div className="button-row"><button onClick={() => choose({ kind: "item", version: ref })}>Use as target</button><button onClick={() => onReferences(uniqueVersions([...references, ref]))}>Add reference version</button></div>}
      {plan && ref && <label>Specific shot<select aria-label="Target storyboard shot" value="" onChange={event => { if (event.target.value) choose({ kind: "shot", storyboard_version: ref, shot_id: event.target.value }); }}><option value="">Choose exact shot</option>{plan.shots.map(shot => <option key={shot.shot_id} value={shot.shot_id}>{shot.intent || shot.shot_id}</option>)}</select></label>}
      {target !== "current_view" && target.kind === "new_item" && <label>Optional name<input aria-label="New item name" value={target.name ?? ""} onChange={event => onTarget({ ...target, name: event.target.value })} /></label>}
      <label className="include-view"><input type="checkbox" checked={includeCurrentViewAsReference} onChange={event => onIncludeView(event.target.checked)} />Include current published Review version as a reference</label>
      <small>Explicit targets do not capture hidden Review drafts or viewport images.</small>
      {references.length > 0 && <div className="reference-versions"><strong>Reference versions</strong>{references.map(ref => <div key={`${ref.item_id}:${ref.version_id}`} title={JSON.stringify(ref)}><span>{catalogue.find(item => item.item_id === ref.item_id)?.name ?? ref.item_id} · {ref.version_id}</span><button aria-label={`Remove reference ${ref.version_id}`} onClick={() => onReferences(references.filter(value => value.item_id !== ref.item_id || value.version_id !== ref.version_id))}>Remove</button></div>)}</div>}
      {error && <p role="alert">{error}</p>}
      <button className="subtle" onClick={() => setOpen(false)}>Close target picker</button>
    </section>}
  </div>;
}
