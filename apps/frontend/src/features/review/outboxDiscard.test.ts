import assert from "node:assert/strict";
import { test, type TestContext } from "node:test";
import type { AnnotationSave, AnnotationSnapshot, Camera, Matrix4 } from "../../api/generated/contracts";
import { stableJson } from "./media";
import { classifyDraftDiscard, discardDraftAtomically, formatDraftStateBadge, formatDraftStatusLabel, type Draft } from "./outbox";

const annotation: AnnotationSnapshot = {
  annotation_id: "annotation-1", saved_revision: null,
  target: {origin: "blender_render", artifact_id: "frame-1", displayed_sha256: "1".repeat(64),
    dimensions: {width: 1920, height: 1080}, item_id: "asset", version_id: "v3", shot_id: null,
    render_job_id: "render-1", source_frame: 1, media_artifact_id: null},
  range: null, camera: null, text: "", markups: [], spatial_hits: [], spatial_status: "unavailable",
};
const draft: Draft = {key: annotation.annotation_id, projectId: "project", annotation, state: "browser-local"};
const operation: AnnotationSave = {
  schema_version: "1.0", project_id: draft.projectId, operation_id: "commit-1", expected_revision: 0, annotation,
};
const confirmNew = {
  allowed: true, mode: "delete_new", requiresConfirmation: true, buttonLabel: "Discard draft", confirmPrompt: "Discard this draft?",
};
const editedRevision = {...annotation, saved_revision: 3, text: "Unsaved notes"};

test("an untouched frame draft discards immediately, including whitespace-only notes", () => {
  const expected = {allowed: true, mode: "delete_new", requiresConfirmation: false, buttonLabel: "Discard draft", confirmPrompt: ""};
  assert.deepEqual(classifyDraftDiscard(draft), expected);
  assert.deepEqual(classifyDraftDiscard({...draft, annotation: {...annotation, text: " \n\t "}}), expected);
});

test("a text note on a new frame draft requires confirmation", () => {
  assert.deepEqual(classifyDraftDiscard({...draft, annotation: {...annotation, text: "Fix the seam."}}), confirmNew);
});

test("a stroke on a new frame draft requires confirmation even without text", () => {
  const markups: AnnotationSnapshot["markups"] = [{id: "stroke-1", kind: "stroke", points: [[0.2, 0.3], [0.4, 0.5]], colour: "#ff5555"}];
  assert.deepEqual(classifyDraftDiscard({...draft, annotation: {...annotation, markups}}), confirmNew);
});

test("a spatial hit alone requires confirmation", () => {
  const matrix: Matrix4 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  const camera: Camera = {space: "blender_world_m", kind: "orthographic", camera_to_world: matrix,
    projection: matrix, aspect: 16 / 9, near_m: 0.1, far_m: 100, source_camera_id: null, orthographic_height_m: 2};
  const spatial_hits: AnnotationSnapshot["spatial_hits"] = [{space: "blender_world_m", object_id: "collar",
    instance_id: "collar-1", proxy_artifact_id: "proxy", mapping_artifact_id: "mapping", version_id: "v3",
    sample_frame: 1, position: [1, 0, 0], normal: [0, 1, 0], camera}];
  assert.deepEqual(classifyDraftDiscard({...draft, annotation: {...annotation, camera, spatial_hits, spatial_status: "valid"}}), confirmNew);
});

test("an unmapped proxy capture requires confirmation without text, markups or spatial hits", () => {
  const target = {...annotation.target, origin: "proxy_capture" as const};
  assert.deepEqual(classifyDraftDiscard({...draft, annotation: {...annotation, target}}), confirmNew);
});

test("unsaved edits on a server revision require confirmation naming the revision to restore", () => {
  assert.deepEqual(classifyDraftDiscard({...draft, annotation: editedRevision}), {
    allowed: true, mode: "revert_edits", requiresConfirmation: true, buttonLabel: "Discard edits",
    confirmPrompt: "Discard unsaved edits and restore revision 3?",
  });
  assert.deepEqual(classifyDraftDiscard({...draft, annotation: editedRevision}, 5), {
    allowed: true, mode: "revert_edits", requiresConfirmation: true, buttonLabel: "Discard edits",
    confirmPrompt: "Discard unsaved edits and restore latest server revision 5 (edited from rev 3)?",
  });
});

test("a clean saved revision cannot be discarded, including a retained acknowledgement", () => {
  assert.deepEqual(classifyDraftDiscard({...draft, state: "saved", annotation: editedRevision}), {allowed: false});
  assert.deepEqual(classifyDraftDiscard({...draft, state: "saved", annotation: editedRevision, operation}), {allowed: false});
});

for (const state of ["saving", "conflict"] as const) {
  test(`a ${state} draft cannot be discarded even if no operation has been persisted yet`, () => {
    assert.deepEqual(classifyDraftDiscard({...draft, state}), {allowed: false});
  });
}

test("a failed save retains its original operation", () => {
  assert.deepEqual(classifyDraftDiscard({...draft, state: "failed", operation}), {allowed: false});
});

test("a failed image-only save retains its original fallback operation regardless of the visible draft state", () => {
  const fallbackOperation = {...operation, operation_id: "fallback-1"};
  assert.deepEqual(classifyDraftDiscard({...draft, state: "failed", fallbackOperation}), {allowed: false});
  assert.deepEqual(classifyDraftDiscard({...draft, fallbackOperation}), {allowed: false});
});

test("a failure before any save operation never gets the untouched-frame shortcut", () => {
  assert.deepEqual(classifyDraftDiscard({...draft, state: "failed"}), confirmNew);
  assert.deepEqual(classifyDraftDiscard({...draft, state: "failed", annotation: editedRevision}), {allowed: false});
});

test("the toolbar status uses compact labels for every state without leaking errors", () => {
  assert.equal(formatDraftStateBadge, formatDraftStatusLabel);
  for (const [state, label] of [
    ["browser-local", "Draft on this browser"], ["saving", "Saving…"], ["saved", "Saved · rev 3"],
    ["failed", "Save failed"], ["conflict", "Conflict"],
  ] as const) {
    const record = {...draft, state, annotation: editedRevision, error: "Connection lost"};
    assert.equal(formatDraftStatusLabel(record), label);
  }
  assert.equal(formatDraftStateBadge({...draft, state: "saved"}), "Draft on this browser");
});

// Small event-driven IndexedDB double. Requests must be queued before the transaction
// completes. Writes are isolated in a journal and only published on commit.
function useDraftStore(t: TestContext, records: Draft[], {abortPut = false} = {}) {
  const rows = new Map(records.map(row => [row.key, structuredClone(row)]));
  const transactions: string[][] = [];
  const previous = Object.getOwnPropertyDescriptor(globalThis, "indexedDB");
  Object.defineProperty(globalThis, "indexedDB", {configurable: true, value: {
    open(name: string, version: number) {
      assert.equal(name, "proscenium-review-v1");
      assert.equal(version, 1);
      const request = {
        onsuccess: () => {},
        result: {
          close() {},
          transaction(storeName: string, mode: string) {
            assert.equal(storeName, "drafts");
            assert.equal(mode, "readwrite");
            const log: string[] = [];
            transactions.push(log);
            const journal = new Map(rows);
            const pending: (() => void)[] = [];
            let active = true;
            function enqueue<T>(label: string, effect: () => T) {
              assert.ok(active, "request scheduled after transaction completed");
              log.push(label);
              const req = {result: undefined as T | undefined, onsuccess: () => {}};
              pending.push(() => { req.result = effect(); req.onsuccess(); });
              return req;
            }
            const tx = {
              error: null as Error | null,
              oncomplete: () => {},
              onabort: () => {},
              objectStore(store: string) {
                assert.equal(store, "drafts");
                return {
                  get: (key: string) => enqueue(`get:${key}`, () => structuredClone(journal.get(key))),
                  delete: (key: string) => enqueue(`delete:${key}`, () => journal.delete(key)),
                  put: (row: Draft) => enqueue(`put:${row.key}`, () => {
                    if (abortPut) throw new Error("Put failed");
                    journal.set(row.key, structuredClone(row));
                  }),
                };
              },
            };
            queueMicrotask(() => {
              try {
                while (pending.length) pending.shift()!();
              } catch (e) {
                active = false;
                tx.error = e as Error;
                tx.onabort();
                return;
              }
              active = false;
              rows.clear();
              for (const [key, row] of journal) rows.set(key, row);
              log.push("complete");
              tx.oncomplete();
            });
            return tx;
          },
        },
      };
      queueMicrotask(() => request.onsuccess());
      return request;
    },
  }});
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "indexedDB", previous);
    else Reflect.deleteProperty(globalThis, "indexedDB");
  });
  return {rows, transactions};
}

function confirmation(displayed: Draft) {
  return {projectId: displayed.projectId, key: displayed.key,
    expectedSnapshotJson: stableJson(displayed.annotation), expectedState: displayed.state};
}

const dirty = {...draft, annotation: editedRevision};
const baseline = {...annotation, saved_revision: 4, text: "Acknowledged notes", range: {start: 1, end: 9}};

test("new discard compares the canonical snapshot and commits one read-write transaction", async t => {
  const {text, ...fields} = annotation;
  const {rows, transactions} = useDraftStore(t, [{...draft, annotation: {text, ...fields}}]);
  assert.deepEqual(await discardDraftAtomically({...confirmation(draft), mode: "delete_new"}), {status: "deleted"});
  assert.equal(rows.size, 0);
  assert.deepEqual(transactions, [[`get:${draft.key}`, `delete:${draft.key}`, "complete"]]);
});

for (const [label, advancement] of [
  ["saving", {state: "saving"}], ["operation", {operation}], ["conflict", {state: "conflict"}],
  ["fallback operation", {fallbackOperation: operation}], ["saved", {state: "saved"}],
] as const) {
  test(`discard preserves a draft that durably advanced to ${label} after confirmation`, async t => {
    const stored = {...draft, ...advancement};
    const {rows, transactions} = useDraftStore(t, [stored]);
    assert.deepEqual(await discardDraftAtomically({...confirmation(draft), mode: "delete_new"}), {status: "protected", stored});
    assert.deepEqual(rows.get(draft.key), stored);
    assert.deepEqual(transactions, [[`get:${draft.key}`, "complete"]]);
  });
}

for (const [label, change] of [
  ["snapshot", {annotation: {...annotation, text: "Another tab's edits"}}],
  ["discardable state", {state: "failed"}],
  ["project", {projectId: "another-project"}],
] as const) {
  test(`a ${label} mismatch preserves the stored row unchanged`, async t => {
    const stored = {...draft, ...change};
    const {rows, transactions} = useDraftStore(t, [stored]);
    assert.deepEqual(await discardDraftAtomically({...confirmation(draft), mode: "delete_new"}), {status: "mismatch", stored});
    assert.deepEqual(rows.get(draft.key), stored);
    assert.deepEqual(transactions, [[`get:${draft.key}`, "complete"]]);
  });
}

test("revert replaces a dirty override with a clean clone of the acknowledged baseline", async t => {
  const stored = {...dirty, targetBlob: new Blob(["draft capture"]), targetUploadId: "dirty-upload"};
  const {rows, transactions} = useDraftStore(t, [stored]);
  const outcome = await discardDraftAtomically({...confirmation(dirty), mode: "revert_edits", cleanBaseline: baseline});
  const restored = {...draft, annotation: baseline, state: "saved"};
  assert.deepEqual(outcome, {status: "reverted", restored});
  assert.notEqual(outcome.status === "reverted" && outcome.restored.annotation, baseline);
  assert.deepEqual(rows.get(draft.key), restored);
  assert.deepEqual(transactions, [[`get:${draft.key}`, `put:${draft.key}`, "complete"]]);
});

test("revert with an old key removes that key and writes the canonical row in the same transaction", async t => {
  const legacy = {...dirty, key: "legacy-key"};
  const {rows, transactions} = useDraftStore(t, [legacy]);
  const outcome = await discardDraftAtomically({...confirmation(legacy), mode: "revert_edits", cleanBaseline: baseline});
  assert.equal(outcome.status, "reverted");
  assert.equal(rows.has(legacy.key), false);
  assert.deepEqual(rows.get(draft.key)?.annotation, baseline);
  assert.deepEqual(transactions, [[`get:${legacy.key}`, `get:${draft.key}`, `delete:${legacy.key}`, `put:${draft.key}`, "complete"]]);
});

test("revert with an old key does not overwrite an existing canonical row", async t => {
  const legacy = {...dirty, key: "legacy-key"};
  const canonical = {...dirty, projectId: "another-project"};
  const {rows, transactions} = useDraftStore(t, [legacy, canonical]);
  assert.deepEqual(await discardDraftAtomically({...confirmation(legacy), mode: "revert_edits", cleanBaseline: baseline}),
    {status: "mismatch", stored: legacy});
  assert.deepEqual([...rows.values()], [legacy, canonical]);
  assert.deepEqual(transactions, [[`get:${legacy.key}`, `get:${draft.key}`, "complete"]]);
});

test("an aborted replacement leaves the original override intact", async t => {
  const legacy = {...dirty, key: "legacy-key"};
  const {rows, transactions} = useDraftStore(t, [legacy], {abortPut: true});
  await assert.rejects(discardDraftAtomically({...confirmation(legacy), mode: "revert_edits", cleanBaseline: baseline}), /Put failed/);
  assert.deepEqual([...rows.values()], [legacy]);
  assert.deepEqual(transactions, [[`get:${legacy.key}`, `get:${draft.key}`, `delete:${legacy.key}`, `put:${draft.key}`]]);
});

test("revert without an acknowledged baseline rejects before starting a transaction", async t => {
  const {rows, transactions} = useDraftStore(t, [dirty]);
  for (const cleanBaseline of [undefined, null, annotation]) {
    await assert.rejects(discardDraftAtomically({...confirmation(dirty), mode: "revert_edits", cleanBaseline}),
      /Acknowledged server baseline is required to discard edits/);
  }
  assert.deepEqual(rows.get(draft.key), dirty);
  assert.deepEqual(transactions, []);
});

test("discard handles a missing row, restoring the baseline only for revert", async t => {
  const {rows, transactions} = useDraftStore(t, []);
  assert.deepEqual(await discardDraftAtomically({...confirmation(draft), mode: "delete_new"}), {status: "deleted"});
  const outcome = await discardDraftAtomically({...confirmation(dirty), mode: "revert_edits", cleanBaseline: baseline});
  assert.equal(outcome.status, "reverted");
  assert.deepEqual(rows.get(draft.key)?.annotation, baseline);
  assert.deepEqual(transactions, [[`get:${draft.key}`, "complete"], [`get:${draft.key}`, `put:${draft.key}`, "complete"]]);
});
