import type { SelectionSave } from "../../api/generated/contracts";

/** Private browser recovery metadata. The command itself is the generated wire shape. */
export interface PendingSelection {
  projectId: string;
  command: SelectionSave;
  state: "pending" | "uncertain" | "acknowledged";
}

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("proscenium-catalogue-v1", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("commands", {keyPath: "projectId"});
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function readPendingSelection(projectId: string): Promise<PendingSelection | null> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("commands", "readonly");
    const request = tx.objectStore("commands").get(projectId);
    tx.oncomplete = () => {db.close(); resolve(request.result ?? null);};
    tx.onabort = tx.onerror = () => {db.close(); reject(tx.error);};
  });
}

/** add reserves an empty project slot. Later updates/deletes CAS the original ID.
 * A late acknowledgement in another tab cannot overwrite a newer owner command.
 */
export async function writePendingSelection(projectId: string, next: PendingSelection | null, expectedOperationId?: string): Promise<void> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("commands", "readwrite");
    const store = tx.objectStore("commands");
    let conflict = false;
    if (!expectedOperationId) {
      if (!next) {db.close(); reject(new Error("Missing selection command")); return;}
      store.add(next);
    } else {
      const current = store.get(projectId);
      current.onsuccess = () => {
        if (current.result?.command.operation_id !== expectedOperationId) {
          conflict = true; tx.abort(); return;
        }
        if (next) store.put(next); else store.delete(projectId);
      };
    }
    tx.oncomplete = () => {db.close(); resolve();};
    tx.onabort = tx.onerror = () => {db.close(); reject(conflict ? new Error("Pending selection changed in another tab. Reload its original command.") : tx.error);};
  });
}
