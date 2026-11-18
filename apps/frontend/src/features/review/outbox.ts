import type { AnnotationSave, AnnotationSnapshot } from "../../api/generated/contracts";
import { ApiClient, ApiError } from "../../api/client";

export type DraftState = "browser-local" | "saving" | "saved" | "conflict" | "failed";
export const VECTOR_MASK = "Editable binary brush mask";
export interface Draft {
  key: string;
  projectId: string;
  annotation: AnnotationSnapshot;
  operation?: AnnotationSave;
  state: DraftState;
  error?: string;
  targetBlob?: Blob;
  targetUploadId?: string;
  targetUploaded?: boolean;
  markup?: Blob;
  mask?: Blob;
  markupUploadId?: string;
  maskUploadId?: string;
  fallbackOperation?: AnnotationSave;
  fallbackSaved?: AnnotationSnapshot;
  supersededOperations?: AnnotationSave[];
}

export type DiscardMode = "delete_new" | "revert_edits";
export type DiscardPolicy =
  | { allowed: false }
  | { allowed: true; mode: DiscardMode; requiresConfirmation: boolean; buttonLabel: "Discard draft" | "Discard edits"; confirmPrompt: string };

export function classifyDraftDiscard(draft: Draft): DiscardPolicy {
  if (draft.state === "saving" || draft.state === "conflict" || draft.state === "saved" || draft.operation || draft.fallbackOperation) {
    return {allowed: false};
  }
  const {saved_revision, spatial_hits, target, markups, text} = draft.annotation;
  if (saved_revision !== null) {
    if (draft.state !== "browser-local") return {allowed: false};
    return {allowed: true, mode: "revert_edits", requiresConfirmation: true, buttonLabel: "Discard edits",
      confirmPrompt: `Discard unsaved edits and restore revision ${saved_revision}?`};
  }
  // Only an untouched browser-local 2D draft can disappear without confirmation.
  const requiresConfirmation = draft.state !== "browser-local" || spatial_hits.length > 0
    || target.origin === "proxy_capture" || markups.length > 0 || text.trim() !== "";
  return {allowed: true, mode: "delete_new", requiresConfirmation, buttonLabel: "Discard draft",
    confirmPrompt: requiresConfirmation ? "Discard this draft?" : ""};
}

export function formatDraftStateBadge(draft: Draft): string {
  return `${draft.state === "browser-local" ? "Draft on this browser" : draft.state}${draft.error ? `: ${draft.error}` : ""}`;
}

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("proscenium-review-v1", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("drafts", {keyPath: "key"});
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function transaction<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("drafts", mode);
    const request = action(tx.objectStore("drafts"));
    tx.oncomplete = () => { db.close(); resolve(request.result); };
    tx.onabort = tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

export const saveDraft = async (draft: Draft) => { await transaction("readwrite", store => store.put(draft)); };
export const deleteDraft = async (key: string): Promise<void> => {
  await transaction("readwrite", store => store.delete(key));
};

// A 3D pin and its 2D markup editor share a record, even across a view switch.
const pendingEdits = new Map<string, Promise<void>>();
const draftIdentity = (draft: Draft) => `${draft.projectId}:${draft.key}`;
export function saveDraftEdit(draft: Draft): Promise<void> {
  const identity = draftIdentity(draft);
  const write = waitForDraftEdits(draft).then(() => saveDraft(draft));
  const settled = write.then(() => {}, () => {});
  pendingEdits.set(identity, settled);
  void settled.then(() => { if (pendingEdits.get(identity) === settled) pendingEdits.delete(identity); });
  return write;
}

export async function waitForDraftEdits(draft: Draft): Promise<void> {
  await pendingEdits.get(draftIdentity(draft));
}

export const readDrafts = async (projectId: string): Promise<Draft[]> =>
  (await transaction<Draft[]>("readonly", store => store.getAll())).filter(d => d.projectId === projectId)
    .map(d => d.state === "saving" ? {...d,state:"failed",error:"Save interrupted. Original operation retained for retry."} : d);

/** Persist each identity before its first network effect. A retry never reconstructs a command. */
const inFlight = new Set<string>();
export async function flushDraft(api: ApiClient, source: Draft, notify: (draft: Draft) => void): Promise<Draft> {
  const identity = draftIdentity(source);
  if (inFlight.has(identity)) throw new Error("This annotation save is already in progress");
  inFlight.add(identity);
  try {return await flushOriginalDraft(api, source, notify);}
  finally {inFlight.delete(identity);}
}

async function flushOriginalDraft(api: ApiClient, source: Draft, notify: (draft: Draft) => void): Promise<Draft> {
  let draft = structuredClone(source);
  const update = async (next: Draft) => { draft = next; await saveDraft(draft); notify(draft); };
  await update({...draft, state: "saving", error: undefined});
  try {
    const annotation = structuredClone(draft.annotation);
    if (!draft.operation) {
      if (draft.targetBlob && !draft.targetUploaded) {
        if (!draft.targetUploadId) await update({...draft, targetUploadId: crypto.randomUUID()});
        const targetArtifact = await api.call("uploadImage", {path: {project_id: draft.projectId, upload_id: draft.targetUploadId!}, body: draft.targetBlob, idempotencyKey: draft.targetUploadId});
        if (targetArtifact.project_id !== draft.projectId || targetArtifact.state !== "ready" || targetArtifact.kind !== "image" || targetArtifact.sha256 !== annotation.target.displayed_sha256 || targetArtifact.dimensions?.width !== annotation.target.dimensions.width || targetArtifact.dimensions.height !== annotation.target.dimensions.height) {
          throw new Error("Clean capture upload acknowledgement does not match frozen PNG hash/dimensions");
        }
        annotation.target.artifact_id = targetArtifact.artifact_id;
        await update({...draft, annotation, targetUploaded: true});
      }
      if (draft.markup) {
        if (!draft.markupUploadId) await update({...draft, annotation, markupUploadId: crypto.randomUUID()});
        const artifact = await api.call("uploadImage", {path: {project_id: draft.projectId, upload_id: draft.markupUploadId!}, body: draft.markup, idempotencyKey: draft.markupUploadId});
        annotation.markup_artifact_id = artifact.artifact_id;
      }
      if (draft.mask) {
        if (!draft.maskUploadId) await update({...draft, annotation, maskUploadId: crypto.randomUUID()});
        const artifact = await api.call("uploadImage", {path: {project_id: draft.projectId, upload_id: draft.maskUploadId!}, body: draft.mask, idempotencyKey: draft.maskUploadId});
        for (const markup of annotation.markups) if (markup.kind === "mask" && markup.text === VECTOR_MASK) markup.mask_artifact_id = artifact.artifact_id;
      }
      const operation: AnnotationSave = {schema_version: "1.0", project_id: draft.projectId,
        operation_id: crypto.randomUUID(), expected_revision: annotation.saved_revision ?? 0, annotation};
      await update({...draft, annotation, operation});
    }
    const ack = await api.call("saveAnnotation", {path: {project_id: draft.projectId}, body: draft.operation!, idempotencyKey: draft.operation!.operation_id});
    await update({...draft, state: "saved", annotation: {...draft.operation!.annotation, saved_revision: ack.state_revision}});
  } catch (error) {
    await update({...draft, state: error instanceof ApiError && error.envelope.error.code === "revision_conflict" ? "conflict" : "failed", error: error instanceof Error ? error.message : String(error)});
  }
  return draft;
}
