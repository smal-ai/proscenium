import { useEffect, useState } from "react";
import { X } from "lucide-react";
import type { AnnotationSnapshot, VersionRecord } from "../../api/generated/contracts";
import { formatDraftStatusLabel, type Draft } from "./outbox";

export type AnnotationEntry = { annotation: AnnotationSnapshot; state: Draft["state"]; draftKey: string | null; source: "draft" | "server" };

const revision = (annotation: AnnotationSnapshot) => annotation.saved_revision ?? 0;
const snapshotState = (annotation: AnnotationSnapshot) => annotation.saved_revision !== null ? "saved" : "browser-local";
const draftState = (draft: Draft) => draft.state === "saved" ? snapshotState(draft.annotation) : draft.state;
const normaliseDraft = (draft: Draft): Draft => draft.state === "saved" && draft.annotation.saved_revision === null
  ? {...draft, state: "browser-local"} : draft;

export function draftFromSnapshot(annotation: AnnotationSnapshot, projectId: string): Draft {
  return {key: annotation.annotation_id, projectId, annotation: structuredClone(annotation), state: snapshotState(annotation)};
}

export function resolveAnnotationEntry(entry: AnnotationEntry, drafts: readonly Draft[], projectId: string): Draft {
  const local = entry.source === "draft" && entry.draftKey
    ? drafts.find(d => d.key === entry.draftKey && d.annotation.annotation_id === entry.annotation.annotation_id)
    : undefined;
  if (local && (local.state !== "saved" || revision(local.annotation) >= revision(entry.annotation))) {
    return normaliseDraft(local);
  }
  return draftFromSnapshot(entry.annotation, projectId);
}

export function mergeHydratedDrafts(stored: readonly Draft[], current: readonly Draft[]): Draft[] {
  const byKey = new Map(stored.map(d => [d.key, d]));
  for (const draft of current) {
    const hydrated = byKey.get(draft.key);
    if (!hydrated || (hydrated.state === "saved" && revision(hydrated.annotation) < revision(draft.annotation))) {
      byKey.set(draft.key, draft);
    }
  }
  return [...byKey.values()].map(normaliseDraft);
}

export function shortVersionTag(versionId: string | null | undefined): string {
  return versionId?.match(/v\d+$/i)?.[0].toLowerCase() ?? versionId ?? "unversioned";
}

export function mergeItemAnnotations({itemId, currentVersionId, drafts, saved, itemVersions}: {
  itemId: string; currentVersionId: string; drafts: readonly Draft[]; saved: readonly AnnotationSnapshot[]; itemVersions?: readonly VersionRecord[];
}) {
  const belongsToItem = ({target}: AnnotationSnapshot) => target.item_id === itemId || (target.item_id === null && target.version_id === currentVersionId);
  const byId = new Map<string, AnnotationEntry>();

  for (const annotation of saved) {
    if (!belongsToItem(annotation)) continue;
    const previous = byId.get(annotation.annotation_id);
    if (!previous || revision(annotation) >= revision(previous.annotation)) {
      byId.set(annotation.annotation_id, {annotation, state: snapshotState(annotation), draftKey: null, source: "server"});
    }
  }
  for (const draft of drafts) {
    const {annotation, key} = draft;
    if (!belongsToItem(annotation)) continue;
    const previous = byId.get(annotation.annotation_id);
    const state = draftState(draft);
    if (!previous || state !== "saved" || (previous.state === "saved" && revision(annotation) >= revision(previous.annotation))) {
      byId.set(annotation.annotation_id, {annotation, state, draftKey: key, source: "draft"});
    }
  }

  const byVersion = new Map<string | null, AnnotationEntry[]>();
  for (const entry of byId.values()) {
    const key = entry.annotation.target.version_id;
    if (!byVersion.has(key)) byVersion.set(key, []);
    byVersion.get(key)!.push(entry);
  }
  const order = new Map(itemVersions?.map((version, index) => [version.version_id, index]));
  const allItemGroups = [...byVersion].sort(([a], [b]) => {
    if (a === currentVersionId) return -1;
    if (b === currentVersionId) return 1;
    const aOrder = a === null ? Infinity : order.get(a) ?? Infinity;
    const bOrder = b === null ? Infinity : order.get(b) ?? Infinity;
    return aOrder === bOrder ? (b ?? "").localeCompare(a ?? "", undefined, {numeric: true}) : aOrder - bOrder;
  }).map(([versionId, entries]) => ({versionId, entries}));
  const thisVersion = byVersion.get(currentVersionId) ?? [];
  return {thisVersion, allItemGroups, olderVersionCount: byId.size - thisVersion.length};
}

const annotationFrame = ({target}: AnnotationSnapshot) => target.sequence_frame ?? target.source_frame ?? 1;

export function formatAnnotationTitle(annotation: AnnotationSnapshot): string {
  return annotation.text.trim() || `Untitled ${annotation.spatial_hits.length ? "3D pin" : "markup"} · Frame ${annotationFrame(annotation)}`;
}

export function formatAnnotationMeta({annotation, state}: Pick<AnnotationEntry, "annotation" | "state">): {detail: string; statusLabel: string} {
  const hit = annotation.spatial_hits[0];
  const count = annotation.markups.length;
  const kind = hit ? `3D pin (${hit.instance_id})` : count ? `${count} markup${count === 1 ? "" : "s"}` : "Note";
  return {detail: `Frame ${annotationFrame(annotation)} · ${kind}`, statusLabel: formatDraftStatusLabel({state, annotation})};
}

export function AnnotationsDrawer({version, merged, onClose, onRestore}: {
  version: VersionRecord;
  merged: ReturnType<typeof mergeItemAnnotations>;
  onClose: () => void;
  onRestore: (entry: AnnotationEntry) => void;
}) {
  const [scopeTab, setScopeTab] = useState<"this_version" | "all_item">("this_version");
  useEffect(() => { setScopeTab("this_version"); }, [version.item_id]);
  const shortVersion = shortVersionTag(version.version_id);
  const totalItemCount = merged.thisVersion.length + merged.olderVersionCount;

  function row(entry: AnnotationEntry) {
    const {annotation, state} = entry;
    const {detail, statusLabel} = formatAnnotationMeta(entry);
    const entryVersionId = annotation.target.version_id;
    return <button type="button" className="toolbar-btn review-annotation-row" key={annotation.annotation_id} onClick={() => onRestore(entry)}>
      <strong>{formatAnnotationTitle(annotation)}</strong>
      <span className="review-annotation-meta">
        <span>{detail}</span>
        <span className="toolbar-badge" data-state={state}>{statusLabel}</span>
        {entryVersionId && entryVersionId !== version.version_id && <span className="toolbar-badge accent">Open on {shortVersionTag(entryVersionId)}</span>}
      </span>
    </button>;
  }

  return <section className="review-drawer review-annotations-drawer" aria-label="Saved annotations and local drafts">
    <header className="review-annotations-heading">
      <h2>Annotations · {version.name}</h2>
      <div className="segmented-control review-annotation-scopes" role="group" aria-label="Annotation scope">
        <button type="button" aria-pressed={scopeTab === "this_version"} onClick={() => setScopeTab("this_version")}>This version · {shortVersion}</button>
        <button type="button" aria-pressed={scopeTab === "all_item"} onClick={() => setScopeTab("all_item")}>All versions of this item ({totalItemCount})</button>
      </div>
      <button type="button" className="toolbar-btn toolbar-icon-btn drawer-close" aria-label="Close review drawer" onClick={onClose}><X size={14}/></button>
    </header>
    <div className="review-drawer-items">
      {scopeTab === "this_version" ? (merged.thisVersion.length ? merged.thisVersion.map(row) : <div className="review-annotations-empty">
        <span>No annotations on {shortVersion}.</span>
        {merged.olderVersionCount > 0 && <button type="button" className="toolbar-btn" onClick={() => setScopeTab("all_item")}>Show {merged.olderVersionCount} on other versions</button>}
      </div>) : (totalItemCount ? merged.allItemGroups.map(({versionId, entries}) => <section key={versionId ? `version:${versionId}` : "unversioned"} className="review-annotation-version">
        <h3>{shortVersionTag(versionId)}{versionId === version.version_id ? " · Current view" : ""}</h3>
        {entries.map(row)}
      </section>) : <div className="review-annotations-empty">No annotations on this item.</div>)}
    </div>
  </section>;
}
