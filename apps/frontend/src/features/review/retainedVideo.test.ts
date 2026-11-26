import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Artifact } from "../../api";
import { clipSequenceDelivery, RetainedReview, SequenceReelReview } from "./ReviewChrome";

const video: Artifact = {
  schema_version: "1.0", artifact_id: "generation-h3-artifact-0", project_id: "project", state: "ready", kind: "video",
  purpose: "concept", media_type: "video/mp4", sha256: "a".repeat(64), bytes: 1234, derived_from: [],
  dimensions: { width: 864, height: 480 }, fps: { numerator: 24, denominator: 1 }, frame_count: 124,
  duration_seconds: 5.166667, sample_rate_hz: 48000, channels: 2,
};

test("standalone video Review exposes authenticated playback and verified metadata", () => {
  const render = (inspectedArtifact: Artifact) => renderToStaticMarkup(createElement(RetainedReview, {
    projectId: "project", version: null, inspectedArtifact, onClearArtifact: () => {},
  }));
  const markup = render(video);
  assert.match(markup, /<video controls="" preload="metadata" playsInline="" src="[^"]+\/generation-h3-artifact-0\/content" aria-label="Retained video generation-h3-artifact-0"/i);
  for (const text of ["864 × 480", "24 fps", "5.167 s", "48,000 Hz", "2 channels", "Download video", "Clear preview"]) {
    assert.ok(markup.includes(text), text);
  }
  assert.doesNotMatch(render({ ...video, state: "unavailable" }), /<video/);
  assert.doesNotMatch(render({ ...video, media_type: "video/webm" }), /<video/);
});

test("only a ready clip assembly pinned to the viewed sequence plays without a FrameMap", () => {
  const mapped: Artifact = { ...video, purpose: "final", source_version_id: "reel-v1", derived_from: [
    { kind: "encode", parameters: { frame_map_sha256: "b".repeat(64) }, input_artifact_ids: [video.artifact_id] },
  ] };
  const reel: Artifact = { ...mapped, artifact_id: "assembly-delivery", derived_from: [
    { kind: "encode", parameters: { assembly: "clip_concat" }, input_artifact_ids: [video.artifact_id] },
  ] };
  assert.equal(clipSequenceDelivery([video, mapped, reel], "reel-v1"), reel);
  assert.equal(clipSequenceDelivery([reel], "different-version"), undefined);
  assert.equal(clipSequenceDelivery([{ ...reel, state: "unavailable" }], "reel-v1"), undefined);
  const markup = renderToStaticMarkup(createElement(SequenceReelReview, { visible: true, projectId: "project", artifact: reel }));
  assert.match(markup, /<video [^>]+aria-label="Assembled clip sequence assembly-delivery"/);
  assert.match(markup, /Download video/);
});
