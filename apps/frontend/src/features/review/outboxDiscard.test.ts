import assert from "node:assert/strict";
import { test } from "node:test";
import type { AnnotationSave, AnnotationSnapshot, Camera, Matrix4 } from "../../api/generated/contracts";
import { classifyDraftDiscard, formatDraftStateBadge, type Draft } from "./outbox";

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

test("the status badge humanises browser-local without changing save workflow labels", () => {
  assert.equal(formatDraftStateBadge(draft), "Draft on this browser");
  for (const state of ["saving", "saved", "failed", "conflict"] as const) {
    assert.equal(formatDraftStateBadge({...draft, state}), state);
  }
  assert.equal(formatDraftStateBadge({...draft, state: "failed", error: "Connection lost"}), "failed: Connection lost");
});
