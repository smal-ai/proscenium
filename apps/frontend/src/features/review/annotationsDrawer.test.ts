import assert from "node:assert/strict";
import { test } from "node:test";
import type { AnnotationSnapshot, Camera, Markup, Matrix4, VersionRecord } from "../../api/generated/contracts";
import { formatAnnotationMeta, formatAnnotationTitle, mergeItemAnnotations, shortVersionTag } from "./AnnotationsDrawer";
import type { Draft } from "./outbox";

const itemId = "asset-a";
const currentVersionId = "a-v3";
const hash = "a".repeat(64);
function note(id: string, versionId: string | null = currentVersionId, targetItemId: string | null = itemId, revision: number | null = 1): AnnotationSnapshot {
  return {
    annotation_id: id, saved_revision: revision, text: "", range: null, camera: null, markups: [], spatial_hits: [], spatial_status: "unavailable",
    target: {origin: "reference", artifact_id: "image", displayed_sha256: hash, dimensions: {width: 1920, height: 1080},
      item_id: targetItemId, version_id: versionId, shot_id: null, render_job_id: null, source_frame: null, media_artifact_id: null},
  };
}
const draft = (annotation: AnnotationSnapshot, state: Draft["state"] = "browser-local"): Draft => ({key: annotation.annotation_id, projectId: "project", annotation, state});

test("version tags keep concise suffixes and fall back to the original identity", () => {
  assert.equal(shortVersionTag("signal-lost-native-kit-v3"), "v3");
  assert.equal(shortVersionTag("a-V20"), "v20");
  assert.equal(shortVersionTag("lookdev"), "lookdev");
  assert.equal(shortVersionTag(null), "unversioned");
  assert.equal(shortVersionTag(undefined), "unversioned");
});

test("scopes saved snapshots and project-wide drafts by item, allowing only current-version legacy entries", () => {
  const saved = [note("current"), note("old", "a-v2"), note("other-saved", "b-v3", "asset-b"), note("wrong-explicit-item", currentVersionId, "asset-b"), note("legacy-current", currentVersionId, null), note("legacy-old", "a-v2", null)];
  const drafts = [draft(note("local-current")), draft(note("local-old", "a-v1")), draft(note("other-draft", currentVersionId, "asset-b")), draft(note("legacy-local", currentVersionId, null)), draft(note("legacy-other", "a-v2", null))];
  const merged = mergeItemAnnotations({itemId, currentVersionId, saved, drafts});
  assert.deepEqual(merged.thisVersion.map(e => e.annotation.annotation_id), ["current", "legacy-current", "local-current", "legacy-local"]);
  assert.equal(merged.olderVersionCount, 2);
  assert.deepEqual(merged.allItemGroups.map(g => [g.versionId, g.entries.map(e => e.annotation.annotation_id)]), [
    ["a-v3", ["current", "legacy-current", "local-current", "legacy-local"]], ["a-v2", ["old"]], ["a-v1", ["local-old"]],
  ]);
});

test("prefers unsaved changes or equally new local acknowledgements over server snapshots without double counting", () => {
  const saved = [note("dirty", currentVersionId, itemId, 3), note("equal", currentVersionId, itemId, 2), note("stale", "a-v2", itemId, 4), note("ahead", "a-v2", itemId, 1)];
  const drafts = [draft(note("dirty", currentVersionId, itemId, 1), "failed"), draft(note("equal", currentVersionId, itemId, 2), "saved"), draft(note("stale", "a-v2", itemId, 3), "saved"), draft(note("ahead", "a-v2", itemId, 2), "saved")];
  const merged = mergeItemAnnotations({itemId, currentVersionId, saved, drafts});
  const entries = merged.allItemGroups.flatMap(g => g.entries);
  assert.equal(entries.length, 4);
  assert.equal(merged.thisVersion.length, 2);
  assert.equal(merged.olderVersionCount, 2);
  assert.equal(entries[0].annotation, drafts[0].annotation);
  assert.equal(entries[0].state, "failed");
  assert.equal(entries[1].annotation, drafts[1].annotation);
  assert.equal(entries[2].annotation, saved[2]);
  assert.equal(entries[3].annotation, drafts[3].annotation);
});

test("puts the current version first, then honours item version order or descending version IDs", () => {
  const saved = [note("one", "a-v1"), note("ten", "a-v10"), note("three"), note("two", "a-v2")];
  const params = {itemId, currentVersionId, saved, drafts: []};
  const versionIds = (itemVersions?: VersionRecord[]) => mergeItemAnnotations({...params, itemVersions}).allItemGroups.map(g => g.versionId);
  assert.deepEqual(versionIds(), ["a-v3", "a-v10", "a-v2", "a-v1"]);
  assert.deepEqual(versionIds(["a-v1", "a-v2", "a-v3"].map(version_id => ({version_id}) as VersionRecord)), ["a-v3", "a-v1", "a-v2", "a-v10"]);
});

test("uses readable titles, frame details and state badges without exposing annotation IDs", () => {
  const plain = note("uuid");
  const stroke: Markup = {id: "stroke", kind: "stroke", points: [[0, 0], [1, 1]]};
  const identity: Matrix4 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  const camera: Camera = {space: "blender_world_m", kind: "perspective", camera_to_world: identity, projection: identity, aspect: 1, near_m: 0.1, far_m: 100, source_camera_id: null};
  const spatial: AnnotationSnapshot = {...plain, camera, spatial_status: "valid", target: {...plain.target, source_frame: 12, sequence_frame: 24},
    spatial_hits: [{camera, proxy_artifact_id: "proxy", mapping_artifact_id: "map-v3", version_id: currentVersionId, sample_frame: 12, object_id: "collar", instance_id: "collar-1", space: "blender_world_m", position: [0, 0, 0], normal: [0, 0, 1]}]};
  assert.equal(formatAnnotationTitle({...plain, text: "  Move the collar \n"}), "Move the collar");
  assert.equal(formatAnnotationTitle({...plain, text: "  "}), "Untitled markup · Frame 1");
  assert.equal(formatAnnotationTitle(spatial), "Untitled 3D pin · Frame 24");
  assert.deepEqual(formatAnnotationMeta({annotation: spatial, state: "saved"}), {detail: "Frame 24 · 3D pin (collar-1)", statusLabel: "Saved · rev 1"});
  assert.deepEqual(formatAnnotationMeta({annotation: plain, state: "browser-local"}), {detail: "Frame 1 · Note", statusLabel: "Draft on this browser"});
  assert.deepEqual(formatAnnotationMeta({annotation: {...plain, target: {...plain.target, source_frame: 12}, markups: [stroke]}, state: "saving"}), {detail: "Frame 12 · 1 markup", statusLabel: "Saving…"});
  assert.deepEqual(formatAnnotationMeta({annotation: {...plain, markups: [stroke, {...stroke, id: "second"}]}, state: "failed"}), {detail: "Frame 1 · 2 markups", statusLabel: "Save failed"});
  assert.equal(formatAnnotationMeta({annotation: plain, state: "conflict"}).statusLabel, "Conflict");
});
