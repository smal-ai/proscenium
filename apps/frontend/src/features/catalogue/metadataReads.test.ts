import assert from "node:assert/strict";
import { test } from "node:test";
import type { Artifact, ItemRecord, RetainedArtifactSummary, SelectionRecord, ShotPlan, VersionRecord } from "../../api";
import { nonReadyArtifactIndex, seedReadyArtifacts } from "../readyArtifactIndex";
import { readStoryboardCatalogue, shotPlanArtifactIds, storyboardHeads } from "../storyboards/model";
import { catalogueVersionKey, indexCatalogueSelections, indexCatalogueVersions } from "./catalogueHelpers";

const asset = { item_id: "asset", current_version_id: "asset-v1", state_revision: 1, kind: "asset", name: "Asset" } as ItemRecord;
const board = { ...asset, item_id: "board", current_version_id: "board-v1", kind: "storyboard" } satisfies ItemRecord;

test("catalogue and storyboard version keys track the relevant changes, not record identity", () => {
  const items = [asset, board];
  assert.equal(catalogueVersionKey(items), catalogueVersionKey(items.map(item => ({ ...item, name: "Updated label" }))));
  assert.notEqual(catalogueVersionKey(items), catalogueVersionKey([{ ...asset, state_revision: 2 }, board]));
  assert.notEqual(catalogueVersionKey(items), catalogueVersionKey([asset]));
  assert.deepEqual(storyboardHeads(items), storyboardHeads([{ ...asset, current_version_id: "asset-v2" }, { ...board, state_revision: 2 }]));
  assert.notDeepEqual(storyboardHeads(items), storyboardHeads([asset, { ...board, current_version_id: "board-v2" }]));
});

test("catalogue groups versions newest first and preserves the first selected choice", () => {
  const early = { item_id: "asset", version_id: "asset-v1", created_at: "2026-11-24T10:00:00Z" } as VersionRecord;
  const late = { ...early, version_id: "asset-v2", created_at: "2026-11-25T10:00:00Z" };
  const other = { ...early, item_id: "board", version_id: "board-v1" };
  const versions = [early, other, late];
  assert.deepEqual([...indexCatalogueVersions(versions)], [["asset", [late, early]], ["board", [other]]]);
  assert.deepEqual(versions, [early, other, late]);
  const selected = { slot: { kind: "item", item_id: "asset" }, state: "selected", selection_id: "first" } as SelectionRecord;
  const choices = [{ ...selected, state: "withdrawn" }, selected, { ...selected, selection_id: "later" }];
  assert.equal(indexCatalogueSelections(choices as SelectionRecord[]).get("asset"), selected);
});

test("explicit Storyboard Refresh discovers a new board missed by the supplied catalogue", async () => {
  const staleItems: ItemRecord[] = [];
  const published = { item_id: board.item_id, version_id: board.current_version_id } as VersionRecord;
  const calls: string[] = [];
  const readItems = async () => { calls.push("listItems"); return [asset, board]; };
  const readVersions = async (id: string) => { calls.push(`listItemVersions:${id}`); return [published]; };
  const initial = await readStoryboardCatalogue(storyboardHeads(staleItems), 0, 0, readItems, readVersions);
  assert.deepEqual(initial, { catalogue: null, boards: [], versions: [] });
  assert.deepEqual(calls, []);

  const refreshed = await readStoryboardCatalogue(storyboardHeads(staleItems), 1, 0, readItems, readVersions);
  assert.deepEqual(calls, ["listItems", "listItemVersions:board"]);
  assert.deepEqual(refreshed, { catalogue: [asset, board], boards: [["board", "board-v1"]], versions: [published] });
  assert.deepEqual(staleItems, []);
});

test("shot metadata resolves unique references and revokes previously ready artefacts only in the active project", () => {
  const plan = {
    schema_version: "1.0", plan_id: "plan", project_id: "active", source_commit: "a".repeat(40),
    brief_artifact_ids: [], asset_dependencies: [], unresolved: [],
    shots: [{ shot_id: "shot", intent: "", camera_intent: "", motion_beats: [], scene_version: null, source_range: null, fps: null,
      frames: ["authored", "authored", null].map((artifact_id, i) => ({ frame_id: `frame-${i}`, role: "start", composition: "", artifact_id, state: artifact_id ? "available" : "missing", owner_selected: false })) }],
  } satisfies ShotPlan;
  const selected = { project_id: "active", slot: { kind: "storyboard_frame", storyboard_version: { item_id: "board", version_id: "board-v1" } }, state: "selected", choice: { kind: "artifact", artifact_id: "chosen" } } as SelectionRecord;
  const records = [selected, { ...selected, project_id: "other", choice: { kind: "artifact", artifact_id: "foreign" } }, { ...selected, state: "withdrawn", choice: { kind: "artifact", artifact_id: "former" } }] as SelectionRecord[];
  const ids = shotPlanArtifactIds(plan, "board-v1", records);
  assert.deepEqual([...ids], ["authored", "chosen"]);
  assert.deepEqual([...shotPlanArtifactIds(plan, "board-v2", records)], ["authored"]);
  const ready = { project_id: "active", artifact_id: "authored", state: "ready" } as Artifact;
  const chosen = { ...ready, artifact_id: "chosen" };
  const foreign = { ...ready, project_id: "other", state: "corrupt" } satisfies Artifact;
  const unrelated = { ...ready, artifact_id: "unrelated" };
  const unready = { ...ready, artifact_id: "chosen", state: "unavailable" } satisfies Artifact;
  const summaries = [ready, foreign, unrelated, unready].map(artifact => ({ artifact }) as RetainedArtifactSummary);
  const foreignReady = { ...ready, project_id: "other" };
  const review = new Map<string, Artifact>([["active:chosen", chosen], ["other:authored", foreignReady]]);
  seedReadyArtifacts(review, "active", summaries);
  assert.deepEqual([...review], [["other:authored", foreignReady], ["active:authored", ready], ["active:unrelated", unrelated]]);
  const boards = new Map<string, Artifact>([["active:chosen", chosen]]);
  seedReadyArtifacts(boards, "active", summaries, ids);
  assert.deepEqual([...boards], [["active:authored", ready]]);
  const display = nonReadyArtifactIndex("active", summaries).get("chosen") ?? chosen;
  assert.equal(display, unready);
  assert.notEqual(display.state, "ready");
});
