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
  markup?: Blob;
  mask?: Blob;
  markupUploadId?: string;
  maskUploadId?: string;
  supersededOperations?: AnnotationSave[];
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
export const readDrafts = async (projectId: string): Promise<Draft[]> =>
  (await transaction<Draft[]>("readonly", store => store.getAll())).filter(d => d.projectId === projectId)
    .map(d => d.state === "saving" ? {...d,state:"failed",error:"Save interrupted. Original operation retained for retry."} : d);

/** Persist each identity before its first network effect. A retry never reconstructs a command. */
export async function flushDraft(api: ApiClient, source: Draft, notify: (draft: Draft) => void): Promise<Draft> {
  let draft = structuredClone(source);
  const update = async (next: Draft) => { draft = next; await saveDraft(draft); notify(draft); };
  await update({...draft, state: "saving", error: undefined});
  try {
    const annotation = structuredClone(draft.annotation);
    if (!draft.operation) {
      if (draft.markup) {
        if (!draft.markupUploadId) await update({...draft, markupUploadId: crypto.randomUUID()});
        const artifact = await api.call("uploadImage", {path: {project_id: draft.projectId, upload_id: draft.markupUploadId!}, body: draft.markup, idempotencyKey: draft.markupUploadId});
        annotation.markup_artifact_id = artifact.artifact_id;
      }
      if (draft.mask) {
        if (!draft.maskUploadId) await update({...draft, maskUploadId: crypto.randomUUID()});
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
