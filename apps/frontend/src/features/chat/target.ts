import type { Attachment, ItemRecord, RequestTarget, VersionRef } from "../../api";
import { emptySelection, type ContextPreparer, type TurnSelection } from "../../api/studio";

export type DraftTarget = "current_view" | RequestTarget;
export type ReviewAttachmentOverrides = Record<string, Attachment["role"] | null>;

/** Evidence that ContextValidator.attachments derives from captured context, independently of explicit attachments. */
export function lockedReviewAttachmentIds(selection: TurnSelection): ReadonlySet<string> {
  const ids = new Set<string>();
  if (selection.target) ids.add(selection.target.artifact_id);
  for (const annotation of selection.annotations) {
    ids.add(annotation.target.artifact_id);
    if (annotation.markup_artifact_id) ids.add(annotation.markup_artifact_id);
    for (const markup of annotation.markups) {
      if (markup.kind === "mask" && markup.mask_artifact_id) ids.add(markup.mask_artifact_id);
    }
  }
  for (const finding of selection.approved_findings) ids.add(finding.target.artifact_id);
  return ids;
}

export function applyReviewAttachmentOverrides(selection: TurnSelection, overrides?: ReviewAttachmentOverrides): Attachment[] {
  const { attachments } = selection;
  if (!overrides) return attachments;
  const locked = lockedReviewAttachmentIds(selection);
  return attachments.flatMap(attachment => {
    if (locked.has(attachment.artifact_id) || !Object.hasOwn(overrides, attachment.artifact_id)) return [attachment];
    const role = overrides[attachment.artifact_id];
    return role === null ? [] : [{ ...attachment, role }];
  });
}

export function versionRequestTarget(version: VersionRef, itemKind?: ItemRecord["kind"], shotId?: string | null): RequestTarget | null {
  if (itemKind === "sequence" || itemKind === "unity_result") return null;
  const ref = { item_id: version.item_id, version_id: version.version_id };
  return itemKind === "storyboard" && shotId ? { kind: "shot", storyboard_version: ref, shot_id: shotId } : { kind: "item", version: ref };
}

export function resolveTarget(target: DraftTarget, selection: TurnSelection, items: ItemRecord[] = []): RequestTarget | null {
  if (target !== "current_view") return structuredClone(target);
  const itemKind = items.find(item => item.item_id === selection.item_id)?.kind
    ?? (selection.target?.origin === "sequence_render" ? "sequence" : undefined);
  const request = selection.request_target;
  if (request === null) return null;
  if (request !== undefined) {
    if (request.kind === "shot" && itemKind && itemKind !== "storyboard") return versionRequestTarget(request.storyboard_version, itemKind);
    if (request.kind === "item" && (itemKind === "sequence" || itemKind === "unity_result")) return null;
    return structuredClone(request);
  }
  if (!selection.item_id || !selection.version_id) return { kind: "project" };
  return versionRequestTarget({ item_id: selection.item_id, version_id: selection.version_id }, itemKind, selection.target?.shot_id);
}

export function viewedSelectionLabel(selection: Pick<TurnSelection, "item_id" | "version_id">, items: ItemRecord[] = []): string | null {
  if (!selection.item_id || !selection.version_id) return null;
  const item = items.find(item => item.item_id === selection.item_id);
  const kind = item?.kind === "unity_result" ? "Unity result" : item ? item.kind[0].toUpperCase() + item.kind.slice(1) : "Version";
  return `${kind} · ${item?.name ?? selection.version_id}`;
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
export function cleanTargetContext(target: RequestTarget | null, refs: VersionRef[], attachments: Attachment[]): TurnSelection {
  const ref = target?.kind === "item" ? target.version : target?.kind === "shot" ? target.storyboard_version : null;
  return { ...emptySelection(), item_id: ref?.item_id ?? null, version_id: ref?.version_id ?? null, request_target: target ?? { kind: "project" }, reference_versions: structuredClone(refs), attachments: structuredClone(attachments) };
}
export async function prepareSubmissionContext(input: { target: DraftTarget; selection: TurnSelection; references: VersionRef[]; attachments: Attachment[]; items?: ItemRecord[]; reviewAttachmentOverrides?: ReviewAttachmentOverrides }, prepare?: ContextPreparer): Promise<TurnSelection> {
  // Clone synchronously. Preparation may await a capture while Review navigates.
  const frozen = structuredClone(input);
  const target = resolveTarget(frozen.target, frozen.selection, frozen.items);
  const hasCurrentView = Boolean(frozen.selection.version_id || frozen.selection.annotations.length || frozen.selection.attachments.length);
  if (frozen.target !== "current_view" || !hasCurrentView) return cleanTargetContext(target, frozen.references, frozen.attachments);
  const captured = prepare ? await prepare() : frozen.selection;
  // Compare the unedited attachments: removal from a draft is not Review navigation.
  const standaloneChanged = !frozen.selection.version_id && !frozen.selection.annotations.length
    && captured.attachments.map(a => a.artifact_id).join("\0") !== frozen.selection.attachments.map(a => a.artifact_id).join("\0");
  const pinnedShot = target?.kind === "shot" ? target.shot_id : null;
  const presented = frozen.selection.target;
  const shotChanged = presented ? (captured.target?.shot_id ?? null) !== (presented.shot_id ?? null)
    : pinnedShot !== null && captured.target !== null && captured.target.shot_id !== pinnedShot;
  const presentedFrameChanged = presented !== null && (
    captured.target?.artifact_id !== presented.artifact_id
    || (captured.target?.source_frame ?? null) !== (presented.source_frame ?? null)
    || (captured.target?.sequence_frame ?? null) !== (presented.sequence_frame ?? null)
  );
  if (captured.item_id !== frozen.selection.item_id || captured.version_id !== frozen.selection.version_id || shotChanged || presentedFrameChanged || standaloneChanged) throw new Error("Review changed during context capture. The draft and original target are retained. Submit again after checking the target.");
  const reviewAttachments = applyReviewAttachmentOverrides(captured, frozen.reviewAttachmentOverrides);
  return { ...captured, request_target: target, reference_versions: frozen.references, attachments: [...reviewAttachments, ...frozen.attachments] };
}
