import type { TurnSelection } from "../../api/studio";

/** Private recovery record, not a second wire protocol or a render job. */
export interface PendingProxyCapture {
  key: string;
  uploadId: string;
  png: Blob;
  sha256: string;
  dimensions: {width: number; height: number};
  selection: TurnSelection;
  sampleFrame: number;
  shotId: string | null;
  phase: "upload" | "handoff" | "uncertain";
}

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("proscenium-proxy-captures-v1", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("captures", {keyPath: "key"});
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function readProxyCapture(key: string): Promise<PendingProxyCapture | null> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("captures", "readonly"); const request = tx.objectStore("captures").get(key);
    tx.oncomplete = () => {db.close(); resolve(request.result ?? null);};
    tx.onabort = tx.onerror = () => {db.close(); reject(tx.error);};
  });
}

export async function writeProxyCapture(key: string, value: PendingProxyCapture | null, identity: string): Promise<void> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("captures", "readwrite"); const store = tx.objectStore("captures");
    const request = store.get(key); let conflict = false;
    request.onsuccess = () => {
      if (request.result && request.result.uploadId !== identity) {conflict = true; tx.abort(); return;}
      if (value) store.put(value); else store.delete(key);
    };
    tx.oncomplete = () => {db.close(); resolve();};
    tx.onabort = tx.onerror = () => {db.close(); reject(conflict ? new Error("A different capture is pending. Resolve it before taking another.") : tx.error);};
  });
}
