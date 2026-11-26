import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Artifact, VersionRecord } from "../../api";
import { emptySelection } from "../../api/studio";
import { unmappedVideoContext } from "./context";
import { clipSequenceDelivery, directPublishedVideo, RetainedReview, SequenceReelReview } from "./ReviewChrome";

const video: Artifact = {
  schema_version: "1.0", artifact_id: "generation-h3-artifact-0", project_id: "project", state: "ready", kind: "video",
  purpose: "concept", media_type: "video/mp4", sha256: "a".repeat(64), bytes: 1234, derived_from: [],
  dimensions: { width: 864, height: 480 }, fps: { numerator: 24, denominator: 1 }, frame_count: 124,
  duration_seconds: 5.166667, sample_rate_hz: 48000, channels: 2,
};

const publishedVideo: Artifact = {
  ...video, artifact_id: "generation-5117f934bc9f2dff6638dfec-artifact-0",
  frame_count: 243, duration_seconds: 10.125, sample_rate_hz: 32000,
};
const provenance: Artifact = {
  schema_version: "1.0", artifact_id: "generation-5117f934bc9f2dff6638dfec-artifact-provenance", project_id: "project",
  state: "ready", kind: "document", purpose: "metadata", media_type: "application/json", sha256: "b".repeat(64), bytes: 42, derived_from: [],
};
const publishedSequence = {
  version_id: "wagenburg-05-proof-v1", kind: "sequence", artifact_ids: [publishedVideo.artifact_id, provenance.artifact_id], preview_job_ids: [],
} satisfies Pick<VersionRecord, "version_id" | "kind" | "artifact_ids" | "preview_job_ids">;

const mappedDelivery: Artifact = { ...video, artifact_id: "frame-mapped-delivery", purpose: "final", source_version_id: "reel-v1", derived_from: [
  { kind: "encode", parameters: { frame_map_sha256: "b".repeat(64) }, input_artifact_ids: [video.artifact_id] },
] };

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

test("a ready clip assembly pinned to the viewed sequence takes precedence over a direct MP4", () => {
  const reel: Artifact = { ...mappedDelivery, artifact_id: "assembly-delivery", derived_from: [
    { kind: "encode", parameters: { assembly: "clip_concat" }, input_artifact_ids: [video.artifact_id] },
  ] };
  const version = { ...publishedSequence, version_id: "reel-v1", artifact_ids: [video.artifact_id, reel.artifact_id] };
  const artifacts = [video, mappedDelivery, reel];
  const direct = directPublishedVideo(artifacts, version);
  assert.equal(direct, video);
  assert.equal(clipSequenceDelivery(artifacts, version.version_id) ?? direct, reel);
  assert.equal(clipSequenceDelivery([reel], "different-version"), undefined);
  assert.equal(clipSequenceDelivery([{ ...reel, state: "unavailable" }], "reel-v1"), undefined);
  const markup = renderToStaticMarkup(createElement(SequenceReelReview, { visible: true, projectId: "project", artifact: reel }));
  assert.match(markup, /<video [^>]+aria-label="Assembled clip sequence assembly-delivery"/);
  assert.match(markup, /Download video/);
});

test("a concept MP4 pinned directly to a sequence, asset or scene needs no assembly", () => {
  const artifacts = [provenance, publishedVideo];
  assert.equal(clipSequenceDelivery(artifacts, publishedSequence.version_id), undefined);
  assert.equal(directPublishedVideo(artifacts, publishedSequence), publishedVideo);
  assert.equal(directPublishedVideo(artifacts, { ...publishedSequence, version_id: "asset-v1", kind: "asset" }), publishedVideo);
  assert.equal(directPublishedVideo(artifacts, { ...publishedSequence, version_id: "scene-v1", kind: "scene" }), publishedVideo);
  assert.equal(directPublishedVideo([video, ...artifacts], {
    ...publishedSequence, artifact_ids: [...publishedSequence.artifact_ids, video.artifact_id],
  }), publishedVideo);
});

test("direct published playback excludes frame-mapped, transient, unready and unpinned artefacts", () => {
  const rejected: [string, Artifact][] = [
    ["frame-mapped delivery", mappedDelivery],
    ["attempt ID", { ...video, artifact_id: "attempt-h3-artifact-0" }],
    ["preview ID", { ...video, artifact_id: "preview-h3-artifact-0" }],
    ["draft motion", { ...video, purpose: "draft_motion" }],
    ["inspection", { ...video, purpose: "inspection" }],
    ["unavailable", { ...video, state: "unavailable" }],
    ["non-MP4", { ...video, media_type: "video/webm" }],
  ];
  for (const [label, artifact] of rejected) {
    assert.equal(directPublishedVideo([artifact], { ...publishedSequence, artifact_ids: [artifact.artifact_id] }), undefined, label);
  }
  assert.equal(directPublishedVideo([video], publishedSequence), undefined, "not pinned to the viewed version");
});

test("published video Review exposes native controls, the authenticated download and verified metadata", () => {
  const markup = renderToStaticMarkup(createElement(SequenceReelReview, {
    visible: true, projectId: "project", artifact: publishedVideo, heading: "Published video",
  }));
  const url = `/api/v1/projects/project/artifacts/${publishedVideo.artifact_id}/content`;
  assert.match(markup, /<strong>Published video<\/strong>/);
  assert.ok(markup.includes(`<video controls="" preload="metadata" playsInline="" src="${url}" aria-label="Published video ${publishedVideo.artifact_id}"`));
  assert.ok(markup.includes(`href="${url}" download="${publishedVideo.artifact_id}"`));
  for (const text of ["864 × 480", "24 fps", "10.125 s", "32,000 Hz", "2 channels", "Download video"]) {
    assert.ok(markup.includes(text), text);
  }
});

test("unmapped playback pins the viewed version without borrowing frame or shot context", () => {
  const sequence = { ...publishedSequence, item_id: "wagenburg-05" };
  const board = { item_id: "proof-board", version_id: "proof-board-v1" };
  assert.deepEqual(unmappedVideoContext(sequence), {
    ...emptySelection(), item_id: sequence.item_id, version_id: sequence.version_id, view_mode: "animation", request_target: null,
  });
  assert.deepEqual(unmappedVideoContext({ ...board, kind: "storyboard" }), {
    ...emptySelection(), ...board, view_mode: "animation", request_target: { kind: "item", version: board },
  });
});
