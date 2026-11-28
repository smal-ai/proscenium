import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiClient, ApiError } from "../../api/client";
import type { Artifact, ItemRecord, ProxyMapping, RelationshipEdge, RelationshipNodeSummary, RelationshipPage, SelectionRecord, ShotPlan } from "../../api/generated/contracts";
import { InspectionObjects } from "../review/InspectionObjects";
import { recordArtifactObservation, type ArtifactObservation } from "../readyArtifactIndex";
import { compatibleObjects, toggleObject } from "../review/state";
import { Outliner } from "./Outliner";
import { appendPage, coverageMessage, endpointKey, extendPath, groupEdges, readFailure, readRelationships, versionRoot } from "./model";
import { overlayNode, panelPlanReads, panelSelections } from "./overlays";

const board = { kind: "version" as const, ref: { item_id: "board", version_id: "board-v1" } };
const shot = { kind: "shot" as const, board: board.ref, shot_id: "same-id" };
const otherShot = { ...shot, board: { ...board.ref, version_id: "board-v2" } };
const scene = { kind: "version" as const, ref: { item_id: "landing", version_id: "landing-v1" } };
const hero = { kind: "version" as const, ref: { item_id: "hero", version_id: "hero-v3" } };

function emptyPage(): RelationshipPage {
  return { schema_version: "1.0", project_id: "project", root: hero, direction: "used_by", relationship_revision: 1,
    items: [], nodes: [], next_cursor: null, coverage: { state: "complete", reasons: [] } };
}

test("only ancestors block expansion, and the same shot ID in a different board version is distinct", () => {
  const root = new Set([endpointKey(board)]);
  const branch = extendPath(root, shot)!;
  const otherBranch = extendPath(root, otherShot)!;
  const left = extendPath(branch, scene)!;
  const right = extendPath(otherBranch, scene)!;
  assert.equal(extendPath(left, board), null);
  assert.equal(extendPath(left, scene), null);
  assert.deepEqual([...right], [endpointKey(board), endpointKey(otherShot), endpointKey(scene)]);
  assert.equal(root.size, 1);
});

test("grouped rows preserve wire ordinals, occurrence identity and separate provenance", () => {
  const bound: RelationshipEdge = { relation: "bound_scene", from: shot, to: scene, details: { source_range: null, fps: null } };
  const rows: RelationshipEdge[] = [
    { relation: "contains_shot", from: board, to: shot, details: { ordinal: 1 } },
    { relation: "contains_shot", from: board, to: { ...shot, shot_id: "alpha" }, details: { ordinal: 2 } },
    bound, { ...bound, details: { fps: null, source_range: null } },
    { ...bound, from: otherShot },
    { relation: "derived_from", from: scene, to: hero, details: {} },
  ];
  const forward = groupEdges(rows, "uses");
  assert.deepEqual(forward.structure.map(row => row.edge.details.ordinal), [1, 2]);
  assert.deepEqual(forward.inputs.map(row => row.node), [scene, scene]);
  assert.deepEqual(forward.provenance.map(row => row.node), [hero]);
  assert.deepEqual(groupEdges(rows, "used_by").inputs.map(row => row.node), [shot, otherShot]);
});

test("planned asset rows preserve exact shot occurrences and coexist with board references", () => {
  const planned: RelationshipEdge = { relation: "planned_asset", from: shot, to: hero, details: {} };
  const uses = groupEdges([
    planned,
    { relation: "bound_scene", from: shot, to: scene, details: { source_range: null, fps: null } },
    { relation: "contains_panel", from: shot, to: { ...shot, kind: "panel", frame_id: "start" }, details: { ordinal: 0, role: "start", composition: "", state: "missing", artifact_id: null } },
  ], "uses");
  assert.deepEqual(uses.inputs.map(row => [row.edge.relation, row.node]), [["planned_asset", hero], ["bound_scene", scene]]);
  assert.deepEqual(uses.structure.map(row => row.edge.relation), ["contains_panel"]);
  const reverse = groupEdges([planned, planned, { ...planned, from: otherShot },
    { relation: "board_reference", from: board, to: hero, details: {} },
    { relation: "derived_from", from: scene, to: hero, details: {} },
  ], "used_by");
  assert.deepEqual(reverse.inputs.map(row => row.node), [shot, otherShot, board]);
  assert.deepEqual(reverse.provenance.map(row => row.node), [scene]);
});

test("source inclusion is a typed input with canonical endpoints in both directions", () => {
  const inclusion: RelationshipEdge = { relation: "includes_source", from: scene, to: hero, details: {} };
  const edges: RelationshipEdge[] = [inclusion, { ...inclusion, relation: "derived_from" }, inclusion];
  const uses = groupEdges(edges, "uses");
  const usedBy = groupEdges(edges, "used_by");
  assert.deepEqual(uses.inputs.map(({ edge, node }) => [edge.relation, node]), [["includes_source", hero]]);
  assert.deepEqual(usedBy.inputs.map(({ edge, node }) => [edge.from, edge.to, node]), [[scene, hero, scene]]);
  assert.deepEqual(usedBy.provenance.map(({ edge }) => edge.relation), ["derived_from"]);
});

test("coverage, empty pages, cursor drift, HTTP and transport errors are distinct", () => {
  const page = emptyPage();
  assert.equal(coverageMessage(page)?.text, "No published uses of this version");
  assert.equal(coverageMessage({ ...page, direction: "uses" })?.text, "No published dependencies");
  assert.equal(coverageMessage({ ...page, next_cursor: "opaque" }), null);
  const partial = coverageMessage({ ...page, coverage: { state: "partial", reasons: ["indexing", "source_unavailable", "invalid_historical_record"] } });
  assert.equal(partial?.state, "partial");
  assert.match(partial!.text, /indexing, source_unavailable, invalid_historical_record/);
  const error = (status: number, code: string) => new ApiError(status, { schema_version: "1.0", error: { code, message: "not for display", operation_id: null, retryable: false, details: {} } });
  assert.equal(readFailure(error(409, "relationship_revision_changed")).stale, true);
  assert.equal(readFailure(error(409, "source_conflict")).stale, false);
  for (const status of [401, 403, 404]) assert.match(readFailure(error(status, "failure")).text, new RegExp(String(status)));
  assert.match(readFailure(new TypeError("NetworkError")).text, /could not be completed/);
  assert.match(readFailure(new DOMException("Timeout", "TimeoutError")).text, /timed out/);
  assert.throws(() => appendPage(page, { ...page, relationship_revision: 2 }), cause => readFailure(cause).stale);
});

test("the request checks exact scope and discards a late response even if transport ignores abort", async () => {
  const api = { call: async () => emptyPage() } as unknown as ApiClient;
  for (const [project, root, direction] of [["other", hero, "used_by"], ["project", versionRoot({ ...hero.ref, version_id: "hero-v4" }), "used_by"], ["project", hero, "uses"]] as const) {
    await assert.rejects(readRelationships(api, project, root, direction, new AbortController().signal), /scope mismatch/);
  }
  const abort = new AbortController();
  const pending = readRelationships(api, "project", hero, "used_by", abort.signal);
  abort.abort();
  await assert.rejects(pending, { name: "AbortError" });
});

test("only the latest-per-item reverse scope serialises latest_per_item", async context => {
  const fetch = context.mock.method(globalThis, "fetch", async (path: string) => {
    const direction = new URL(path, "http://localhost").searchParams.get("direction");
    return Response.json({ ...emptyPage(), direction });
  });
  const api = new ApiClient();
  const signal = new AbortController().signal;
  await readRelationships(api, "project", hero, "used_by", signal, "cursor", true);
  await readRelationships(api, "project", hero, "used_by", signal, undefined, false);
  await readRelationships(api, "project", hero, "uses", signal, undefined, true);
  assert.deepEqual(fetch.mock.calls.map(({ arguments: [path] }) => {
    assert.equal(typeof path, "string");
    return new URL(String(path), "http://localhost").search;
  }), [
    "?direction=used_by&cursor=cursor&latest_per_item=true", "?direction=used_by", "?direction=uses",
  ]);
});

test("an empty/hidden Outliner renders an honest picker state without an implied root", () => {
  const markup = renderToStaticMarkup(<Outliner api={{} as ApiClient} projectId="project" version={null} items={[]} retainedArtifacts={[]} retainedArtifactReadOrder={0} selectionRevision={0} hydrated visible={false}
    topologyRevision={1} connection="Connected" onOpenVersion={() => assert.fail()} onOpenShot={() => assert.fail()} onInspectArtifact={() => assert.fail()} />);
  assert.match(markup, /hidden=""/);
  assert.match(markup, /No published versions in this project to navigate/);
  assert.doesNotMatch(markup, /No published uses|role="tree"/);
});

test("the object drawer retains exact pairs on mapping changes and multi-select toggles only the exact instance", () => {
  const pair = { object_id: "hero", instance_id: "hero-1" };
  const second = { ...pair, instance_id: "hero-2" };
  const old = { mapping_artifact_id: "map-1", sample_frame: 1, nodes: [pair, second].map(value => ({ ...value, display_name: "Hero" })) } as ProxyMapping;
  const newer = { ...old, mapping_artifact_id: "map-2", sample_frame: 24, nodes: [{ ...old.nodes[1] }] };
  const selected = toggleObject(toggleObject([], pair, false), second, true);
  assert.deepEqual(selected, [pair, second]);
  assert.equal(compatibleObjects(selected, old), selected);
  assert.deepEqual(compatibleObjects(selected, newer), [second]);
  assert.deepEqual(toggleObject(selected, second, true), [pair]);
  const markup = renderToStaticMarkup(<InspectionObjects mapping={old} selected={selected} unavailableReason={null} loading={false} onSelect={() => assert.fail()} />);
  assert.match(markup, /Objects at frame 1/);
  assert.equal(markup.match(/aria-pressed="true"/g)?.length, 2);
  const unavailable = renderToStaticMarkup(<InspectionObjects mapping={null} selected={[]} unavailableReason="Offline" pinned loading={false} onSelect={() => assert.fail()} />);
  assert.match(unavailable, /Pinned 3D proxy unavailable: Offline/);
  assert.doesNotMatch(unavailable, /aria-pressed|Objects at frame 24/);
});

test("live choices and non-ready evidence overlay immutable graph nodes without loosening readiness", () => {
  const asset: RelationshipNodeSummary = { ...hero, label: "Hero", item_kind: "asset", is_current: true, is_selected: true };
  const panel: RelationshipNodeSummary = { kind: "panel", board: shot.board, shot_id: shot.shot_id, frame_id: "frame", label: "Frame",
    role: "end", authored_state: "available", artifact_id: "image", artifact_state: "ready", owner_selected: true };
  const artefact: RelationshipNodeSummary = { kind: "artifact", artifact_id: "image", label: "Image", artifact_kind: "image", purpose: "keyframe", state: "ready" };
  const stored: SelectionRecord = { schema_version: "1.0", selection_id: "choice", project_id: "project", state: "selected", state_revision: 1,
    choice: { kind: "artifact", artifact_id: "image" }, slot: { kind: "storyboard_frame", storyboard_version: shot.board, shot_id: shot.shot_id, frame_id: panel.frame_id }, actor: { kind: "user", id: "owner" } };
  const withdrawn: SelectionRecord = { ...stored, state: "withdrawn", state_revision: 2, choice: null };
  const overlay = {
    items: new Map([[hero.ref.item_id, { item_id: hero.ref.item_id, current_version_id: "hero-v4", selected_version_id: null } as ItemRecord]]),
    artefacts: new Map([["image", { artefact: { state: "unavailable" } as Artifact, readOrder: 2 }]]),
    panels: panelSelections("project", [withdrawn, stored, { ...stored, project_id: "another", state_revision: 3 }]),
  };
  assert.deepEqual(overlayNode(asset, overlay, 1), { ...asset, is_current: false, is_selected: false });
  assert.deepEqual(overlayNode(panel, overlay, 1), { ...panel, artifact_state: "unavailable", owner_selected: false });
  assert.deepEqual(overlayNode(artefact, overlay, 1), { ...artefact, state: "unavailable" });
  assert.equal(panel.artifact_state, "ready");
  assert.equal(panel.owner_selected, true);
  const newer = { ...overlay, artefacts: new Map([["image", { artefact: { state: "ready" } as Artifact, readOrder: 3 }]]),
    panels: panelSelections("project", [{ ...stored, choice: { kind: "version", version: hero.ref } }]) };
  assert.deepEqual(overlayNode({ ...artefact, state: "corrupt" }, newer, 4), { ...artefact, state: "corrupt" });
  assert.deepEqual(overlayNode(panel, newer, 2), panel);
  assert.deepEqual(overlayNode({ ...panel, board: otherShot.board }, newer, 2),
    { ...panel, board: otherShot.board, owner_selected: false });
});

test("version panel choices share one authoritative board read and never select a later render preview", async () => {
  // The merged version view includes the preview, but the publication only owns image A.
  const versionView = { ...hero.ref, artifact_ids: ["image-A", "preview-B"] };
  const panels = versionView.artifact_ids.map((artifact_id, index) => ({ kind: "panel", board: shot.board, shot_id: shot.shot_id,
    label: artifact_id, frame_id: `frame-${index}`, artifact_id, role: "start", authored_state: "available", artifact_state: "ready", owner_selected: false } as const));
  const records = panels.map((panel): SelectionRecord => ({ schema_version: "1.0", selection_id: panel.frame_id, project_id: "project", state: "selected", state_revision: 2,
    choice: { kind: "version", version: hero.ref }, slot: { kind: "storyboard_frame", storyboard_version: shot.board, shot_id: shot.shot_id, frame_id: panel.frame_id }, actor: { kind: "user", id: "owner" } }));
  const plan: ShotPlan = { schema_version: "1.0", project_id: "project", plan_id: "plan", source_commit: "a".repeat(40), brief_artifact_ids: [], asset_dependencies: [], unresolved: [],
    shots: [{ shot_id: shot.shot_id, intent: "", camera_intent: "", motion_beats: [], scene_version: null, source_range: null, fps: null,
      frames: panels.map(({ frame_id, role, artifact_id }) => ({ frame_id, role, artifact_id, composition: "", state: "available", owner_selected: artifact_id === "image-A" })) }] };
  const calls: unknown[] = [];
  const reads = panelPlanReads({ call: async (name: string, args: { path: unknown }) => { calls.push([name, args.path]); return plan; } } as unknown as ApiClient, "project");
  const pending = reads.read(panels[0].board);
  assert.equal(reads.read(panels[1].board), undefined);
  assert.equal(await pending, plan);
  assert.equal(reads.read(panels[0].board), undefined);
  const overlays = { items: new Map(), artefacts: new Map(), panels: panelSelections("project", records), shotPlans: new Map([[board.ref.version_id, plan]]) };
  assert.deepEqual(panels.map(panel => overlayNode(panel, overlays, 0)).map(node => node.kind === "panel" && node.owner_selected), [true, false]);
  assert.deepEqual(calls, [["getVersionShotPlan", { project_id: "project", version_id: board.ref.version_id }]]);
  reads.abort();
});

test("panel and standalone readiness follows newer evidence, not response delivery order", () => {
  const panel = { kind: "panel", board: shot.board, shot_id: shot.shot_id, frame_id: "frame", label: "Frame", role: "end",
    authored_state: "available", artifact_id: "image", artifact_state: "unavailable", owner_selected: false } as const;
  const artefact = { kind: "artifact", artifact_id: "image", label: "Image", artifact_kind: "image", purpose: "keyframe", state: "unavailable" } as const;
  const observations = new Map<string, ArtifactObservation>();
  const overlays = { items: new Map(), artefacts: observations };
  const observe = (state: Artifact["state"], order: number) => recordArtifactObservation(observations, { artifact_id: "image", state } as Artifact, order);
  const assertState = (order: number, state: Artifact["state"]) => {
    assert.deepEqual(overlayNode(panel, overlays, order), { ...panel, artifact_state: state });
    assert.deepEqual(overlayNode(artefact, overlays, order), { ...artefact, state });
  };
  assert.equal(observe("ready", 1), true);
  assertState(2, "unavailable"); // Older ready snapshot versus a newer non-ready page.
  assert.equal(observe("ready", 3), true);
  assertState(2, "ready");
  assert.equal(observe("unavailable", 1), false);
  assertState(2, "ready");
  assert.equal(observe("corrupt", 4), true);
  assertState(2, "corrupt");
  assert.equal(observe("ready", 3), false);
  assertState(2, "corrupt");
  assert.deepEqual(overlayNode({ ...artefact, state: "ready" }, overlays, 5), { ...artefact, state: "ready" });
  assert.deepEqual(overlayNode({ ...panel, artifact_state: "ready" }, overlays, 5), { ...panel, artifact_state: "ready" });
});
