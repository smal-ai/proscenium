import type { Attachment, ItemRecord, RequestTarget, VersionRef } from "../../api";
import { emptySelection, type ContextPreparer, type TurnSelection } from "../../api/studio";

export type DraftTarget = "current_view" | RequestTarget;
export function resolveTarget(target: DraftTarget, selection: TurnSelection): RequestTarget {
  if (target !== "current_view") return structuredClone(target);
  if (!selection.item_id || !selection.version_id) return { kind: "project" };
  const version = { item_id: selection.item_id, version_id: selection.version_id };
  return selection.target?.shot_id ? { kind: "shot", storyboard_version: version, shot_id: selection.target.shot_id } : { kind: "item", version };
}
export function targetLabel(target: RequestTarget, items: ItemRecord[] = []): string {
  if (target.kind === "project") return "Project";
  if (target.kind === "new_item") return `New ${target.item_kind}${target.name ? ` · ${target.name}` : ""}`;
  const ref = target.kind === "item" ? target.version : target.storyboard_version;
  const item = items.find(item => item.item_id === ref.item_id);
  return `${target.kind === "shot" ? "Shot" : item ? item.kind[0].toUpperCase() + item.kind.slice(1) : "Item"} · ${item?.name ?? ref.item_id}${target.kind === "shot" ? ` · ${target.shot_id}` : ""}`;
}
export function uniqueVersions(refs: VersionRef[]): VersionRef[] {
  return [...new Map(refs.map(ref => [`${ref.item_id}:${ref.version_id}`, ref])).values()];
}
/** Explicit targets must not inherit viewport media, findings or hidden drafts. */
export function cleanTargetContext(target: RequestTarget, refs: VersionRef[], attachments: Attachment[]): TurnSelection {
  const ref = target.kind === "item" ? target.version : target.kind === "shot" ? target.storyboard_version : null;
  return { ...emptySelection(), item_id: ref?.item_id ?? null, version_id: ref?.version_id ?? null, request_target: target, reference_versions: structuredClone(refs), attachments: structuredClone(attachments) };
}
export async function prepareSubmissionContext(input: { target: DraftTarget; selection: TurnSelection; references: VersionRef[]; attachments: Attachment[] }, prepare?: ContextPreparer): Promise<TurnSelection> {
  // Clone synchronously. Preparation may await a capture while Review navigates.
  const frozen = structuredClone(input);
  const target = resolveTarget(frozen.target, frozen.selection);
  const hasCurrentView = Boolean(frozen.selection.version_id || frozen.selection.annotations.length || frozen.selection.attachments.length);
  if (frozen.target !== "current_view" || !hasCurrentView) return cleanTargetContext(target, frozen.references, frozen.attachments);
  const captured = prepare ? await prepare() : frozen.selection;
  const standaloneChanged = !frozen.selection.version_id && !frozen.selection.annotations.length
    && captured.attachments.map(a => a.artifact_id).join("\0") !== frozen.selection.attachments.map(a => a.artifact_id).join("\0");
  if (captured.item_id !== frozen.selection.item_id || captured.version_id !== frozen.selection.version_id || (captured.target?.shot_id ?? null) !== (frozen.selection.target?.shot_id ?? null) || standaloneChanged) throw new Error("Review changed during context capture. The draft and original target are retained. Submit again after checking the target.");
  return { ...captured, request_target: target, reference_versions: frozen.references, attachments: [...captured.attachments, ...frozen.attachments] };
}
export function contextCount(selection: TurnSelection): number {
  const images = new Set(selection.attachments.map(a => a.artifact_id));
  if (selection.target?.artifact_id) images.add(selection.target.artifact_id);
  for (const annotation of selection.annotations) {
    images.add(annotation.target.artifact_id);
    if (annotation.markup_artifact_id) images.add(annotation.markup_artifact_id);
    for (const markup of annotation.markups) if (markup.mask_artifact_id) images.add(markup.mask_artifact_id);
  }
  return (selection.request_target && selection.request_target.kind !== "project" ? 1 : 0) + uniqueVersions(selection.reference_versions ?? []).length + images.size + selection.selected_objects.length + selection.annotations.length + selection.approved_findings.length;
}
