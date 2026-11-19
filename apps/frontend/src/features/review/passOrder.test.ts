import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import type { Artifact, JobRecord } from "../../api/generated/contracts";
import { formatPassOptionLabels, orderVersionJobs, selectablePassJobs } from "./passOrder";

const render = (job_id: string, scene_name: string, start: number, end: number, purpose: NonNullable<JobRecord["render_spec"]>["purpose"] = "draft_motion", shot_id: string | null = scene_name): JobRecord => ({
  schema_version: "1.0", job_id, project_id: "project", work_id: null, owner_operation_id: null,
  target: {target_id: "scene", version: {item_id: "item", version_id: "version"}, shot_id},
  attempt_id: `attempt-${job_id}`, attempt_number: 1, kind: "render", state: "succeeded", state_revision: 1,
  availability: "complete", input_digest: "a".repeat(64), external_job_id: null, provider_id: null, owner_epoch: "owner",
  completed_frames: end - start + 1, expected_frames: end - start + 1, artifact_ids: [],
  render_spec: {intent_id: job_id, shot_id, purpose, source_closure_digest: "a".repeat(64), scene_name, camera: null,
    frames: {start, end}, fps: {numerator: 24, denominator: 1}, dimensions: {width: 640, height: 360}, engine: "Cycles", device: "CPU", samples: 32,
    colour_management: {view_transform: "AgX", look: "None", exposure: 0, gamma: 1}, renderer_revision: "synthetic"},
});

const scenes = [
  render(`preview-${createHash("sha256").update("macro").digest("hex")}`, "sl-01-dormant-macro", 1, 72),
  render(`preview-${createHash("sha256").update("conduit").digest("hex")}`, "sl-02-conduit", 73, 168),
  render(`preview-${createHash("sha256").update("console").digest("hex")}`, "sl-03-console-wake", 169, 240),
  render(`preview-${createHash("sha256").update("exterior").digest("hex")}`, "sl-04-exterior-reveal", 241, 360),
  render(`preview-${createHash("sha256").update("title").digest("hex")}`, "sl-05-title-lockup", 361, 432),
];
const proxy = render("proxy", "SL04_Exterior", 1, 1, "proxy", null);
const scrambled = [scenes[2], proxy, scenes[0], scenes[4], scenes[1], scenes[3]];
const bodies = [
  "sl-01-dormant-macro · f1–72 · draft_motion",
  "sl-02-conduit · f73–168 · draft_motion",
  "sl-03-console-wake · f169–240 · draft_motion",
  "sl-04-exterior-reveal · f241–360 · draft_motion",
  "sl-05-title-lockup · f361–432 · draft_motion",
];

test("Signal Lost passes use frame order, not hashed job ID or arrival order, and exclude the proxy", () => {
  const ordered = orderVersionJobs(scrambled);
  assert.deepEqual(ordered, [...scenes, proxy]);
  const passes = selectablePassJobs(ordered, "animation", []);
  assert.deepEqual(passes, scenes);
  assert.deepEqual([...formatPassOptionLabels(passes).values()], bodies.map((label, index) => `${index + 1} · ${label}`));
  for (const mode of ["keyframes", "3d_inspection"] as const) assert.deepEqual(selectablePassJobs(ordered, mode, []), scenes);
});

test("explicit preview order takes precedence and is re-indexed before remaining jobs", () => {
  const order = [4, 0, 2, 1, 3];
  const explicit = order.map(index => scenes[index].job_id);
  const passes = selectablePassJobs(orderVersionJobs(scrambled, explicit), "animation", []);
  assert.deepEqual([...formatPassOptionLabels(passes).values()], order.map((index, ordinal) => `${ordinal + 1} · ${bodies[index]}`));
  assert.deepEqual(orderVersionJobs(scrambled, [explicit[0], "unknown", explicit[0], explicit[1]]), [scenes[4], ...scenes.slice(0, 4), proxy]);
});

test("tied frame intervals compare numeric tokens case-insensitively", () => {
  const jobs = [render("a", "shot-10", 1, 72, "draft_motion", null), render("b", "shot-2", 1, 72), render("c", "SHOT-1", 1, 72)];
  assert.deepEqual(orderVersionJobs(jobs).map(job => job.job_id), ["c", "b", "a"]);
});

test("equal labels include the full short ID or the suffix of a long ID", () => {
  const jobs = [render("sparse-lookdev", "First Lookdev", 1, 24, "keyframe"), render("in-progress", "First Lookdev", 1, 24, "keyframe"), render("preview-0123456789", "First Lookdev", 1, 24, "keyframe")];
  assert.deepEqual([...formatPassOptionLabels(jobs).values()], [
    "1 · First Lookdev · f1–24 · keyframe · sparse-lookdev",
    "2 · First Lookdev · f1–24 · keyframe · in-progress",
    "3 · First Lookdev · f1–24 · keyframe · 23456789",
  ]);
});

test("only visual outputs are selectable, with a render-only fallback in Animation", () => {
  const still = render("single-frame", "First Lookdev", 1, 1, "keyframe");
  const sparse = render("sparse-lookdev", "First Lookdev", 1, 24, "keyframe");
  const generation: JobRecord = {...still, job_id: "concept", kind: "image_generation", render_spec: null, artifact_ids: ["concept"]};
  const image: Artifact = {schema_version: "1.0", project_id: "project", artifact_id: "concept", kind: "image", purpose: "concept", state: "ready", media_type: "image/png", bytes: 42, sha256: "a".repeat(64), dimensions: {width: 64, height: 64}, derived_from: []};
  const other = (["vlm_review", "unity", "mesh_generation", "encode"] as const).map(kind => ({...generation, job_id: kind, kind}));
  const ordered = orderVersionJobs([proxy, sparse, still, generation, ...other]);
  assert.deepEqual(selectablePassJobs(ordered, "animation", [image]), [sparse]);
  assert.deepEqual(selectablePassJobs([still, generation], "animation", [image]), [still]);
  assert.deepEqual(selectablePassJobs([generation], "animation", [image]), []);
  assert.deepEqual(selectablePassJobs(ordered, "keyframes", [image]), [still, sparse, generation]);
  assert.deepEqual(selectablePassJobs(ordered, "keyframes", []), [still, sparse]);
  assert.deepEqual(selectablePassJobs(ordered, "3d_inspection", [image]), [still, sparse]);
});
