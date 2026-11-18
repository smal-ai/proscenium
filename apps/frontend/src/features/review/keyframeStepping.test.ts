import assert from "node:assert/strict";
import { test } from "node:test";
import type { FrameAvailability, FrameReceipt, JobRecord } from "../../api/generated/contracts";
import { canStepKeyframePass, describeKeyframePassStatus, resolveDirectKeyframeJump, sortedReadyFrames, stepKeyframePass } from "./keyframeStepping";

const dimensions = {width: 1920, height: 1080};
const fps = {numerator: 24, denominator: 1};
const digest = "a".repeat(64);
const frame = (n: number): FrameReceipt => ({
  schema_version: "1.0", receipt_id: `receipt-${n}`, project_id: "project", job_id: "pass", attempt_id: "attempt", version_id: "v1",
  shot_id: null, source_frame: n, artifact_id: `image-${n}`, output_path: `frame-${n}.png`, sha256: digest, dimensions, input_digest: digest,
});
const availability = (changes: Partial<FrameAvailability> = {}): FrameAvailability => ({
  schema_version: "1.0", project_id: "project", job_id: "pass", version_id: "v1", source_range: {start: 1, end: 24},
  fps, available_ranges: [], playable_ranges: [], pending_ranges: [], failed_ranges: [], state_revision: 1, ...changes,
});
const job = (state: JobRecord["state"]): JobRecord => ({
  schema_version: "1.0", project_id: "project", job_id: "pass", work_id: null, owner_operation_id: null,
  target: {target_id: "asset", version: {item_id: "asset", version_id: "v1"}, shot_id: null},
  attempt_id: "attempt", attempt_number: 1, kind: "render", state, state_revision: 1, availability: "partial", input_digest: digest,
  external_job_id: null, provider_id: null, owner_epoch: "owner", completed_frames: 1, expected_frames: 24, artifact_ids: [],
  render_spec: {intent_id: "intent", shot_id: null, purpose: "keyframe", source_closure_digest: digest, scene_name: "Scene", camera: null,
    frames: {start: 1, end: 24}, fps, dimensions, engine: "CYCLES", device: "CPU", samples: 8, renderer_revision: "test",
    colour_management: {view_transform: "AgX", look: "None", exposure: 0, gamma: 1}},
});

test("a completed one-frame pass disables both arrows, even with storyboard targets", () => {
  const readyFrames = [frame(1)];
  assert.equal(describeKeyframePassStatus({readyFrames, displayedFrame: 1, job: job("succeeded"), availability: availability()}), "Only rendered frame");
  for (const delta of [-1, 1] as const) {
    assert.equal(stepKeyframePass(readyFrames, 1, delta), null);
    assert.equal(canStepKeyframePass(readyFrames, 1, delta, 3), false);
    assert.equal(canStepKeyframePass(readyFrames, 2, delta, 3), false);
  }
});

test("a sparse pass deduplicates without mutating receipts and steps to the nearest rendered ordinal", () => {
  const first = frame(1), middle = frame(12), last = frame(24);
  const input = Object.freeze([last, first, middle, {...middle, receipt_id: "duplicate"}]);
  const readyFrames = sortedReadyFrames(input);
  assert.deepEqual(readyFrames, [first, middle, last]);
  assert.equal(input[0], last);
  assert.equal(stepKeyframePass(readyFrames, 1, 1), middle);
  assert.equal(stepKeyframePass(readyFrames, 12, 1), last);
  assert.equal(stepKeyframePass(readyFrames, 24, 1), null);
  assert.equal(stepKeyframePass(readyFrames, 24, -1), middle);
  assert.equal(stepKeyframePass(readyFrames, 12, -1), first);
  assert.equal(stepKeyframePass(readyFrames, 1, -1), null);
  assert.equal(stepKeyframePass(readyFrames, 13, -1), middle);
  assert.equal(stepKeyframePass(readyFrames, 13, 1), last);
  assert.equal(canStepKeyframePass(readyFrames, 24, 1, 0), false);
  assert.equal(canStepKeyframePass(readyFrames, 1, -1, 0), false);
  assert.equal(canStepKeyframePass(readyFrames, 12, -1, 0), true);
  assert.equal(canStepKeyframePass(readyFrames, 12, 1, 0), true);
  assert.equal(describeKeyframePassStatus({readyFrames, displayedFrame: 12, job: undefined, availability: null}), "2 of 3 ready");
  assert.deepEqual(resolveDirectKeyframeJump({requestedOrdinal: 12, displayedFrame: 1, readyFrames, job: undefined, availability: null}), {kind: "found", frame: middle});
});

test("ready counts and notices distinguish active rendering, inclusive failed ranges and unrequested frames", () => {
  const readyFrames = [frame(1), frame(12)];
  const pendingAvailability = availability({pending_ranges: [{start: 20, end: 22}], failed_ranges: [{start: 18, end: 19}]});
  const params = {readyFrames, displayedFrame: 12, job: job("running"), availability: pendingAvailability};
  assert.equal(describeKeyframePassStatus(params), "2 of 2 ready · rendering…");
  assert.equal(describeKeyframePassStatus({...params, readyFrames: [readyFrames[0]], displayedFrame: 1, job: undefined}), "1 frame ready · rendering…");
  assert.equal(describeKeyframePassStatus({...params, readyFrames: []}), null);
  for (const [ordinal, expected] of [[20, "is rendering"], [22, "is rendering"], [18, "failed to render"], [19, "failed to render"], [2, "is outside this pass"], [25, "is outside this pass"]] as const) {
    assert.deepEqual(resolveDirectKeyframeJump({...params, requestedOrdinal: ordinal}), {kind: "unavailable", revertFrame: 12, message: `Frame ${ordinal} ${expected}. Showing frame 12.`});
  }
});

test("an active job range is a fallback only before availability arrives", () => {
  const params = {readyFrames: [frame(1)], displayedFrame: 1, job: job("waiting_resource"), availability: null};
  assert.equal(describeKeyframePassStatus(params), "1 frame ready · rendering…");
  assert.deepEqual(resolveDirectKeyframeJump({...params, requestedOrdinal: 24}), {kind: "unavailable", revertFrame: 1, message: "Frame 24 is rendering. Showing frame 1."});
  for (const input of [{...params, requestedOrdinal: 25}, {...params, requestedOrdinal: 2, availability: availability()}, {...params, requestedOrdinal: 2, job: job("succeeded")}]) {
    assert.deepEqual(resolveDirectKeyframeJump(input), {kind: "unavailable", revertFrame: 1, message: `Frame ${input.requestedOrdinal} is outside this pass. Showing frame 1.`});
  }
});

test("storyboard bounds use one-based panels and standalone images cannot step", () => {
  for (const count of [0, 1]) for (const delta of [-1, 1] as const) assert.equal(canStepKeyframePass([], 1, delta, count), false);
  assert.equal(canStepKeyframePass([], 1, -1, 3), false);
  assert.equal(canStepKeyframePass([], 1, 1, 3), true);
  assert.equal(canStepKeyframePass([], 2, -1, 3), true);
  assert.equal(canStepKeyframePass([], 2, 1, 3), true);
  assert.equal(canStepKeyframePass([], 3, -1, 3), true);
  assert.equal(canStepKeyframePass([], 3, 1, 3), false);
});
