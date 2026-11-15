import assert from "node:assert/strict";
import { test } from "node:test";
import { URL } from "node:url";
import { Buffer } from "node:buffer";
import { build } from "esbuild";

async function load(relative) {
  const result = await build({ entryPoints: [new URL(relative, import.meta.url).pathname], bundle: true, platform: "node", format: "esm", write: false });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}
const { annotationContext } = await load("./context.ts");
const { frameContext } = await load("../storyboards/model.ts");
const version = { project_id: "project", item_id: "storyboard", version_id: "pinned-version" };
const shot = { shot_id: "exact-shot" };
const frame = { frame_id: "frame", role: "start", composition: "Wide view" };
const target = { origin: "concept", artifact_id: "image", displayed_sha256: "hash", dimensions: { width: 512, height: 512 }, item_id: "asset", version_id: "asset-version", shot_id: null, render_job_id: null, source_frame: null, media_artifact_id: null };

test("storyboard actions retain the exact shot target even without ready media", () => {
  const selection = frameContext(version, shot, frame);
  assert.deepEqual(selection.request_target, { kind: "shot", storyboard_version: { item_id: "storyboard", version_id: "pinned-version" }, shot_id: "exact-shot" });
  assert.equal(selection.execution_scope, "default");
  assert.equal(selection.target, null);
  assert.deepEqual(selection.attachments, []);
});

test("concept media never changes a storyboard action into an asset request", () => {
  const selection = frameContext(version, shot, frame, { ...target, project_id: "project", state: "ready", kind: "image", purpose: "concept", sha256: "hash" });
  assert.equal(selection.request_target.kind, "shot");
  assert.equal(selection.request_target.storyboard_version.version_id, "pinned-version");
  assert.equal(selection.target.origin, "concept");
  assert.equal(selection.target.shot_id, "exact-shot");
});

test("annotation feedback pins its own immutable version without modifying the draft", () => {
  const annotation = { annotation_id: "annotation", saved_revision: 1, target, range: null, camera: null, markups: [], text: "Feedback", spatial_hits: [], spatial_status: "unavailable" };
  const before = JSON.parse(JSON.stringify(annotation));
  const selection = annotationContext(annotation);
  assert.deepEqual(selection.request_target, { kind: "item", version: { item_id: "asset", version_id: "asset-version" } });
  selection.annotations[0].text = "Changed copy";
  selection.target.version_id = "different";
  assert.deepEqual(annotation, before);
});

test("legacy annotation with no version does not invent a published item target", () => {
  const selection = annotationContext({ target: { ...target, item_id: null, version_id: null }, markups: [], range: null, camera: null });
  assert.equal(selection.request_target, undefined);
  assert.equal(selection.item_id, null);
});
