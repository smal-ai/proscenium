import { execFile } from "node:child_process";
import { chmod, mkdir, mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { promisify } from "node:util";
import { expect, test, type Page } from "@playwright/test";
import type { AnnotationSnapshot, Camera, ItemRecord, ProjectRecord, RelationshipDirection, RelationshipEdge, RelationshipEndpoint, RelationshipNodeSummary, RelationshipPage, RelationshipRoot, SelectionRecord, SelectionSave, ShotPlan, VersionRecord } from "../../apps/frontend/src/api";
import type { MockRoute } from "./fixtures";
import { base, deferred, pageOf, project, projectId, retained, snapshot, StudioFixture, version } from "./studio-fixture";

const graphPath = new RegExp(`${base}/versions/[^/]+/relationships$`);
const navigator = (page: Page) => page.getByRole("complementary", { name: "Navigator", exact: true });
const outliner = (page: Page) => navigator(page).getByRole("region", { name: "Published Outliner" });
const review = (page: Page) => page.locator("main.review-panel");
const graphCalls = (studio: StudioFixture) => studio.guard!.calls.filter(call => graphPath.test(call.url.split("?")[0]));
const versionEndpoint = ({ item_id, version_id }: VersionRecord) => ({ kind: "version" as const, ref: { item_id, version_id } });
function identity(node: RelationshipEndpoint): RelationshipEndpoint {
  if (node.kind === "version") return { kind: "version", ref: node.ref };
  if (node.kind === "shot") return { kind: "shot", board: node.board, shot_id: node.shot_id };
  if (node.kind === "panel") return { kind: "panel", board: node.board, shot_id: node.shot_id, frame_id: node.frame_id };
  return { kind: "artifact", artifact_id: node.artifact_id };
}

function item(record: VersionRecord, selected_version_id: string | null = null): ItemRecord {
  return { schema_version: "1.0", project_id: projectId, item_id: record.item_id, kind: record.kind, name: record.name,
    current_version_id: record.version_id, selected_version_id, state_revision: 1 };
}

function saveViewing(page: Page, record: VersionRecord) {
  return page.addInitScript(record => {
    localStorage.setItem(`proscenium.owner.view.version.${record.project_id}`, JSON.stringify(record));
  }, record);
}

function observeGraphAborts(page: Page, fragment = "/relationships?") {
  return page.addInitScript(fragment => {
    const original = window.fetch;
    const aborted = [] as string[];
    window.fetch = (input, init) => {
      if (typeof input === "string" && input.includes(fragment) && init?.signal) {
        init.signal.addEventListener("abort", () => {
          aborted.push(input);
          document.documentElement.dataset.abortedGraph = JSON.stringify(aborted);
        });
        return original(input, { ...init, signal: undefined });
      }
      return original(input, init);
    };
  }, fragment);
}

function graph(studio: StudioFixture, extraNodes: RelationshipNodeSummary[], edges: RelationshipEdge[]): MockRoute {
  return { path: graphPath, handler: route => {
    const url = new URL(route.request().url());
    const record = studio.versionRecords.find(record => record.version_id === url.pathname.split("/").at(-2))!;
    const endpoint = versionEndpoint(record);
    const shot = url.searchParams.get("shot_id");
    const root: RelationshipRoot = shot ? { kind: "shot", board: endpoint.ref, shot_id: shot } : endpoint;
    const direction: RelationshipDirection = url.searchParams.get("direction") === "used_by" ? "used_by" : "uses";
    const items = edges.map(edge => ({ ...edge, from: identity(edge.from as RelationshipEndpoint), to: identity(edge.to as RelationshipEndpoint) }))
      .filter(edge => JSON.stringify(direction === "uses" ? edge.from : edge.to) === JSON.stringify(root));
    const endpoints = new Set([root, ...items.flatMap(edge => [edge.from, edge.to])].flatMap(endpoint => [JSON.stringify(endpoint),
      ...(endpoint.kind === "shot" || endpoint.kind === "panel" ? [JSON.stringify({ kind: "version", ref: endpoint.board })] : [])]));
    const nodes = [...studio.versionRecords.map((record): RelationshipNodeSummary => {
      const owner = studio.snapshot.items.find(value => value.item_id === record.item_id);
      return { ...versionEndpoint(record), label: record.name, item_kind: record.kind,
        is_current: owner?.current_version_id === record.version_id, is_selected: owner?.selected_version_id === record.version_id };
    }), ...extraNodes].filter(node => endpoints.has(JSON.stringify(identity(node))));
    const result: RelationshipPage = { schema_version: "1.0", project_id: projectId, root, direction, relationship_revision: 1,
      items, nodes, coverage: { state: "complete", reasons: [] }, next_cursor: null };
    return route.fulfill({ json: result });
  } };
}

test("E1: an exact board exposes ordered unbound shots and missing panels without touching production", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const board = studio.versionRecords.find(record => record.version_id === "b-v1")!;
  const boardRef = versionEndpoint(board);
  const shots = ["z-first", "a-second"].map((shot_id, index) => ({ kind: "shot" as const, board: boardRef.ref,
    shot_id, label: index === 0 ? "Opening" : "Ending", intent: "Unbound test shot", binding_status: "unbound" as const }));
  const panel = { kind: "panel" as const, board: boardRef.ref, shot_id: "z-first", frame_id: "missing-panel", label: "Close-up to be made", role: "end" as const,
    authored_state: "missing" as const, artifact_id: null, artifact_state: "missing" as const, owner_selected: false };
  await studio.install({ catalogue: true, savedLayout: true, routes: [graph(studio, [...shots, panel], [
    ...shots.map((shot, ordinal): RelationshipEdge => ({ relation: "contains_shot", from: boardRef, to: shot, details: { ordinal } })),
    { relation: "contains_panel", from: shots[0], to: panel, details: { ordinal: 0, role: "end", composition: panel.label, state: "missing", artifact_id: null } },
  ])] });
  const head = version("board-b", "b-v2", "Newer board", "storyboard");
  studio.versionRecords.push(head);
  studio.snapshot.items = [item(head, board.version_id)];
  studio.retained = [];
  await saveViewing(page, board);
  await studio.open(); await studio.ready();
  await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
  const tree = outliner(page).getByRole("tree");
  await expect(outliner(page).getByRole("heading", { name: board.name })).toBeVisible();
  await expect(outliner(page).locator(".relationship-root-identity")).toContainText("storyboard · b-v1");
  await expect(outliner(page).locator(".relationship-root-identity")).toContainText("Selected");
  await expect(tree.getByRole("treeitem")).toHaveCount(2);
  expect(await tree.getByRole("treeitem").evaluateAll(rows => rows.map(row => row.querySelector("button")?.textContent?.slice(1)))).toEqual(["Opening", "Ending"]);
  await expect(tree.getByText("Scene: unbound", { exact: true })).toHaveCount(2);
  expect(graphCalls(studio).map(call => call.url)).toEqual([`${base}/versions/b-v1/relationships?direction=uses`]);
  await tree.getByRole("button", { name: "Opening" }).click();
  await expect(tree.getByRole("treeitem", { name: panel.label })).toContainText("Authored: missing · Image: missing");
  await expect(tree.getByRole("button", { name: "Inspect panel" })).toHaveCount(0);
  expect(graphCalls(studio).map(call => call.url)).toEqual([
    `${base}/versions/b-v1/relationships?direction=uses`, `${base}/versions/b-v1/relationships?direction=uses&shot_id=z-first`,
  ]);
  expect(studio.guard!.calls.filter(call => call.method !== "GET" || /proxy-mapping|\/content$/.test(call.url))).toEqual([]);
  studio.assertHermetic();
});

test("E2: historical pins, duplicate shot IDs and repeated paths stay distinct", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const board = studio.versionRecords.find(record => record.version_id === "b-v1")!;
  const boardV2 = version("board-b", "b-v2", "Storyboard B newer", "storyboard");
  const otherBoard = version("board-c", "c-v1", "Storyboard C", "storyboard");
  const shared = version("shared", "shared-v1", "Shared landing", "scene");
  const otherScene = version("other", "other-v1", "Stairwell", "scene");
  const hero3 = { ...version("hero", "hero-v3", "Hero v3"), artifact_ids: [] };
  const hero4 = { ...version("hero", "hero-v4", "Hero v4"), artifact_ids: [] };
  studio.versionRecords.push(boardV2, otherBoard, shared, otherScene, hero3, hero4);
  const shots = [board, otherBoard].map(record => ({ kind: "shot" as const, board: versionEndpoint(record).ref,
    shot_id: "shot-1", label: "Cross landing", intent: "Cross landing", binding_status: "bound" as const }));
  const secondShot = { ...shots[0], shot_id: "shot-2", label: "Descend stairs", intent: "Descend stairs" };
  const edges: RelationshipEdge[] = [
    ...shots.map((shot, i): RelationshipEdge => ({ relation: "contains_shot", from: versionEndpoint([board, otherBoard][i]), to: shot, details: { ordinal: 0 } })),
    ...shots.map((shot): RelationshipEdge => ({ relation: "bound_scene", from: shot, to: versionEndpoint(shared), details: { source_range: { start: 1, end: 24 }, fps: null } })),
    { relation: "contains_shot", from: versionEndpoint(board), to: secondShot, details: { ordinal: 1 } },
    { relation: "bound_scene", from: secondShot, to: versionEndpoint(otherScene), details: { source_range: null, fps: null } },
    ...[shared, otherScene].map(scene => ({ relation: "declared_dependency" as const, from: versionEndpoint(scene), to: versionEndpoint(hero3), details: {} })),
    { relation: "derived_from", from: versionEndpoint(hero4), to: versionEndpoint(hero3), details: {} },
  ];
  await studio.install({ savedLayout: true, routes: [graph(studio, [...shots, secondShot], edges)] });
  studio.snapshot.items = [item(boardV2, board.version_id), item(otherBoard), item(shared, shared.version_id), item(otherScene), item(hero4, hero3.version_id)];
  await studio.open(); await studio.ready();
  await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
  const graphPane = outliner(page);
  await graphPane.getByRole("combobox", { name: "Published item", exact: true }).selectOption("hero");
  await graphPane.getByRole("combobox", { name: "Exact published version", exact: true }).selectOption("hero-v3");
  await expect(graphPane.getByText("No published dependencies", { exact: true })).toBeVisible();
  await expect(graphPane.locator(".relationship-root-identity")).toContainText(/Historical.*Selected/);
  await expect(graphPane.locator(".relationship-root-identity")).not.toContainText(/Latest|Viewing/);
  await graphPane.getByRole("button", { name: "Used by", exact: true }).click();
  const tree = graphPane.getByRole("tree");
  await expect(tree.getByRole("group", { name: "Origins / Derivatives" }).getByRole("treeitem")).toContainText(/Hero v4.*Latest/);
  const landing = tree.getByRole("treeitem", { name: shared.name });
  await expect(landing.locator(".relationship-row")).toContainText(/shared-v1.*LatestSelected/);
  const stairs = tree.getByRole("treeitem", { name: otherScene.name });
  await landing.getByRole("button", { name: shared.name, exact: true }).press("ArrowDown");
  await expect(stairs.getByRole("button", { name: otherScene.name })).toBeFocused();
  await stairs.getByRole("button", { name: otherScene.name }).press("ArrowUp");
  await expect(landing.getByRole("button", { name: shared.name })).toBeFocused();
  await landing.getByRole("button", { name: shared.name }).press("ArrowRight");
  await expect(landing.getByRole("treeitem", { name: "Cross landing" })).toHaveCount(2);
  await landing.getByRole("button", { name: shared.name }).press("ArrowRight");
  const firstLanding = landing.getByRole("treeitem", { name: "Cross landing" }).filter({ hasText: "b-v1" });
  await expect(firstLanding.getByRole("button", { name: "Cross landing" })).toBeFocused();
  await firstLanding.getByRole("button", { name: "Cross landing" }).press("ArrowLeft");
  await expect(landing.getByRole("button", { name: shared.name })).toBeFocused();
  await firstLanding.getByRole("button", { name: "Cross landing" }).press("Enter");
  await stairs.getByRole("button", { name: otherScene.name }).press("Enter");
  await stairs.getByRole("button", { name: "Descend stairs" }).press("Enter");
  const repeatedBoard = tree.getByRole("treeitem", { name: board.name, exact: true });
  await expect(repeatedBoard).toHaveCount(2);
  await repeatedBoard.first().getByRole("button", { name: board.name }).press("Enter");
  await expect(repeatedBoard.first().getByText("No published uses of this version")).toBeVisible();
  await expect(repeatedBoard.last().getByRole("button", { name: board.name })).toHaveAttribute("aria-expanded", "false");
  await repeatedBoard.last().getByRole("button", { name: board.name }).press("Enter");
  await expect(repeatedBoard.last().getByText("No published uses of this version")).toBeVisible();
  const beforeRefresh = graphCalls(studio).length;
  await graphPane.getByRole("button", { name: "Refresh relationships" }).press("Enter");
  await expect.poll(() => graphCalls(studio).length).toBe(beforeRefresh + 7);
  await expect(repeatedBoard.last().getByText("No published uses of this version")).toBeVisible();
  await landing.locator(":scope > .relationship-row").getByRole("button", { name: "Focus in Outliner" }).click();
  await expect(graphPane.getByRole("heading", { name: shared.name })).toBeFocused();
  const consumers = tree.getByRole("treeitem", { name: "Cross landing" });
  await expect(consumers).toHaveCount(2);
  const fromB = consumers.filter({ hasText: "b-v1" });
  const fromC = consumers.filter({ hasText: "c-v1" });
  await expect(fromB).toContainText("Selected");
  await expect(fromB).toContainText("Historical");
  await expect(fromB).not.toContainText("Latest");
  await expect(fromC).toContainText("Latest");
  await fromB.getByRole("button", { name: "Cross landing" }).click();
  await fromC.getByRole("button", { name: "Cross landing" }).click();
  await expect(fromB.getByRole("treeitem", { name: board.name })).toContainText("b-v1");
  await expect(fromC.getByRole("treeitem", { name: otherBoard.name })).toContainText("c-v1");

  await graphPane.getByRole("button", { name: "Uses", exact: true }).click();
  await expect(tree.getByRole("treeitem", { name: hero3.name })).toContainText(/hero-v3.*Selected/);
  await expect(tree.getByRole("treeitem", { name: hero4.name })).toHaveCount(0);
  await graphPane.getByRole("button", { name: "Back", exact: true }).click();
  await expect(graphPane.getByRole("heading", { name: hero3.name })).toBeFocused();
  await expect(review(page).getByText("Choose a published item", { exact: true })).toBeVisible();

  await graphPane.getByRole("button", { name: "Used by", exact: true }).click();
  await tree.getByRole("treeitem", { name: shared.name }).getByRole("button", { name: "Focus in Outliner" }).click();
  await fromB.getByRole("button", { name: "Open shot in Shots" }).click();
  await expect(navigator(page).getByRole("button", { name: "Shots", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(navigator(page).getByRole("combobox", { name: "Storyboard version" })).toHaveValue("b-v1");
  await expect(navigator(page).getByRole("heading", { name: "1. shot-1" })).toBeFocused();
  await expect(review(page).getByRole("heading", { name: board.name })).toBeVisible();
  expect(studio.submissions).toEqual([]);
  studio.assertHermetic();
});

async function unlockFixtureDirectories(directory: string): Promise<void> {
  await chmod(directory, 0o700);
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    // pytest leaves symlinks to its test directories. Never follow them.
    if (entry.isDirectory()) await unlockFixtureDirectories(join(directory, entry.name));
  }
}

test("E9: the restored Outliner shows both exact wardrobe histories", async ({ page, baseURL }, testInfo) => {
  const receipt = testInfo.outputPath("restored-relationships.json");
  await mkdir(dirname(receipt), { recursive: true });
  // Replay actual authenticated HTTP responses from the disposable E9 restore.
  // Other Studio APIs stay hermetic; no live Proscenium server is needed.
  // Only JSON goes in Playwright's output directory. pytest materialises
  // sealed snapshots whose directories are not writable by default.
  const workspace = await mkdtemp("/tmp/opencode/proscenium-e9-");
  try {
    await promisify(execFile)("uv", ["run", "pytest", "-q", "--basetemp", join(workspace, "pytest"),
      "tests/test_version_relationships.py::test_wardrobe_revision_and_restore_keep_both_outliner_histories"], {
      env: { ...process.env, PROSCENIUM_E9_RELATIONSHIPS_FIXTURE: receipt }, timeout: 20_000,
    });
  } finally {
    await unlockFixtureDirectories(workspace);
    await rm(workspace, { recursive: true });
  }
  const restored = JSON.parse(await readFile(receipt, "utf8")) as {
    pages: Record<string, Record<RelationshipDirection, RelationshipPage>>;
    versions: VersionRecord[];
  };
  const studio = new StudioFixture(page, baseURL!);
  studio.versionRecords = restored.versions.map(record => ({ ...record, project_id: projectId }));
  await studio.install({ savedLayout: true, routes: [{ path: graphPath, handler: route => {
    const url = new URL(route.request().url());
    const pin = url.pathname.split("/").at(-2)!;
    const direction = url.searchParams.get("direction") as RelationshipDirection;
    const response = restored.pages[pin]?.[direction];
    if (!response) throw new Error(`Missing restored relationship response for ${pin} ${direction}`);
    // Adapt only the enclosing project ID to the fixed Studio test harness.
    return route.fulfill({ json: { ...response, project_id: projectId } });
  } }] });
  const hero4 = studio.versionRecords.find(record => record.version_id === "hero-v4")!;
  studio.snapshot.items = [item(hero4)];
  await studio.open(); await studio.ready();
  await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
  const pane = outliner(page);
  await pane.getByRole("combobox", { name: "Published item", exact: true }).selectOption("hero");
  const versions = pane.getByRole("combobox", { name: "Exact published version", exact: true });
  const inputs = pane.getByRole("tree").getByRole("group", { name: "Inputs & bindings", exact: true });
  const row = (name: string) => inputs.getByRole("treeitem", { name, exact: true });
  const checkIncluded = async (name: string) => {
    await expect(row(name)).toHaveCount(1);
    await expect(row(name)).toContainText("Included by");
    await expect(row(name)).not.toContainText("Declared dependency");
  };
  await versions.selectOption("hero-v3");
  await expect(pane.getByRole("heading", { name: "Hero v3", exact: true })).toBeVisible();
  await pane.getByRole("button", { name: "Used by", exact: true }).click();
  await checkIncluded("Landing v2");
  await checkIncluded("Stairwell v2");
  await expect(inputs.getByText("Included by", { exact: true })).toHaveCount(2);
  await expect(row("Stairwell v3")).toHaveCount(0);

  await versions.selectOption("hero-v4");
  await expect(pane.getByRole("heading", { name: "Hero v4", exact: true })).toBeVisible();
  await checkIncluded("Stairwell v3");
  await expect(inputs.getByText("Included by", { exact: true })).toHaveCount(1);
  await expect(row("Landing v2")).toHaveCount(0);
  await expect(row("Stairwell v2")).toHaveCount(0);
  expect(graphCalls(studio).map(call => call.url)).toContain(`${base}/versions/hero-v4/relationships?direction=used_by`);
  studio.assertHermetic();
});

test("E3: shot intent stays separate from binding, board references and owner panel choices", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const board = studio.versionRecords.find(record => record.version_id === "b-v1")!;
  const newer = version(board.item_id, "b-v2", "Revised storyboard", "storyboard");
  const hero = { ...version("hero", "hero-v3", "Hero v3"), artifact_ids: [] };
  const scene = { ...version("landing", "landing-v1", "Landing", "scene"), artifact_ids: [] };
  studio.versionRecords.push(newer, hero, scene);
  const boardRef = versionEndpoint(board), heroRef = versionEndpoint(hero), sceneRef = versionEndpoint(scene);
  const shot = { kind: "shot" as const, board: boardRef.ref, shot_id: "stairs", label: "Descend the stairs", intent: "Descend the stairs", binding_status: "bound" as const };
  const panel = { kind: "panel" as const, board: boardRef.ref, shot_id: shot.shot_id, frame_id: "start", label: "Opening panel",
    role: "start" as const, authored_state: "available" as const, artifact_id: "verified", artifact_state: "ready" as const, owner_selected: true };
  const projected: ShotPlan = { schema_version: "1.0", project_id: projectId, plan_id: "stairs-plan", source_commit: board.source_commit,
    brief_artifact_ids: [], asset_dependencies: [heroRef.ref], unresolved: [], shots: [
      { shot_id: shot.shot_id, intent: shot.intent, camera_intent: "Static", motion_beats: ["Run"], planned_assets: [heroRef.ref],
        scene_version: sceneRef.ref, source_range: null, fps: null, frames: [{ frame_id: panel.frame_id, role: panel.role, composition: panel.label, artifact_id: panel.artifact_id, state: "available", owner_selected: true }] },
      { shot_id: "legacy", intent: "Old shot", camera_intent: "", motion_beats: ["Bring the coat"], scene_version: null, source_range: null, fps: null, frames: [] },
      { shot_id: "empty", intent: "No pins", camera_intent: "", motion_beats: [], planned_assets: [], scene_version: null, source_range: null, fps: null, frames: [] },
    ] };
  const selection: SelectionRecord = { schema_version: "1.0", project_id: projectId, selection_id: "board-v1-panel", state_revision: 1, state: "selected",
    actor: { kind: "user", id: "owner" }, choice: { kind: "artifact", artifact_id: panel.artifact_id },
    slot: { kind: "storyboard_frame", storyboard_version: boardRef.ref, frame_id: panel.frame_id, shot_id: shot.shot_id } };
  await studio.install({ catalogue: true, savedLayout: true, shotPlan: projected, routes: [
    { path: `${base}/selections`, body: pageOf([selection]) },
    { path: `${base}/versions/b-v2/shot-plan`, body: { ...projected, shots: projected.shots.map(row => ({ ...row, frames: row.frames.map(frame => ({ ...frame, owner_selected: false })) })) } },
    graph(studio, [shot, panel], [
      { relation: "contains_shot", from: boardRef, to: shot, details: { ordinal: 0 } },
      { relation: "contains_panel", from: shot, to: panel, details: { ordinal: 0, role: panel.role, composition: panel.label, state: "available", artifact_id: panel.artifact_id } },
      { relation: "planned_asset", from: shot, to: heroRef, details: {} },
      { relation: "bound_scene", from: shot, to: sceneRef, details: { source_range: null, fps: null } },
      { relation: "board_reference", from: boardRef, to: heroRef, details: {} },
    ]),
  ] });
  studio.snapshot.items = [item(newer, board.version_id), item(hero), item(scene)];
  await saveViewing(page, board);
  await studio.open(); await studio.ready();
  const pane = navigator(page);
  await pane.getByRole("button", { name: "Shots", exact: true }).click();
  const shots = pane.getByRole("region", { name: "Storyboard browser" });
  const versions = shots.getByRole("combobox", { name: "Storyboard version" });
  await expect(versions).toHaveValue(board.version_id);
  await expect(shots.getByText("Dependencies (1)", { exact: true })).toBeVisible();
  await expect(shots.getByText("Planned assets: hero / hero-v3", { exact: true })).toBeVisible();
  await expect(shots.getByText("Scene: landing / landing-v1", { exact: true })).toBeVisible();
  await expect(shots.getByText("Planned assets: Not recorded", { exact: true })).toBeVisible();
  await expect(shots.getByText("Planned assets: None declared", { exact: true })).toBeVisible();
  await expect(shots.getByText("owner_selected: yes", { exact: true })).toBeVisible();
  await versions.selectOption(newer.version_id);
  await expect(shots.getByText("owner_selected: no", { exact: true })).toBeVisible();
  await expect(shots.getByText("owner_selected: yes", { exact: true })).toHaveCount(0);

  await pane.getByRole("button", { name: "Outliner", exact: true }).click();
  const graphPane = outliner(page);
  await graphPane.getByRole("button", { name: shot.label, exact: true }).click();
  const shotRow = graphPane.getByRole("treeitem", { name: shot.label, exact: true });
  const inputs = shotRow.getByRole("group", { name: "Inputs & bindings", exact: true });
  const heroRow = inputs.getByRole("treeitem", { name: hero.name, exact: true });
  await expect(heroRow).toContainText("Planned asset");
  await expect(inputs.getByRole("treeitem", { name: scene.name, exact: true })).toContainText("Bound scene");
  await expect(shotRow.getByRole("group", { name: "Structure", exact: true }).getByRole("treeitem", { name: panel.label })).toContainText("Owner-selected image");
  await heroRow.getByRole("button", { name: "Focus in Outliner" }).click();
  await graphPane.getByRole("button", { name: "Used by", exact: true }).click();
  await expect(shotRow).toContainText(/Planned asset.*Shot stairs.*b-v1.*Historical.*Selected/);
  await expect(graphPane.getByRole("treeitem", { name: board.name, exact: true })).toContainText("Board reference");
  expect(studio.guard!.calls.every(call => call.method === "GET")).toBe(true);
  studio.assertHermetic();
});

test("E5: keyboard browsing, render floods and explicit historical navigation preserve drafts", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const [old, viewed] = studio.versionRecords;
  const relation: RelationshipEdge = { relation: "derived_from", from: versionEndpoint(viewed), to: versionEndpoint(old), details: {} };
  await studio.install({ catalogue: true, savedLayout: true, routes: [graph(studio, [], [relation])] });
  await saveViewing(page, viewed);
  await studio.open(); await studio.ready();
  await review(page).getByRole("button", { name: "Annotate presented frame" }).click();
  const feedback = review(page).getByRole("textbox", { name: "Feedback" });
  await feedback.fill("Unsent annotation on the current publication");
  const draft = page.getByRole("textbox", { name: "Message Copilot" });
  await draft.fill("Retain this Composer draft");
  const bar = page.locator(".target-bar");
  await bar.press("Enter");
  const picker = page.getByRole("region", { name: "Request target", exact: true });
  await picker.getByRole("button", { name: "Project", exact: true }).press("Enter");
  await picker.locator(".target-references-disclosure > summary").press("Enter");
  await picker.getByRole("combobox", { name: "Reference item", exact: true }).selectOption(old.item_id);
  await picker.getByRole("combobox", { name: "Reference version", exact: true }).selectOption(old.version_id);
  await picker.getByRole("button", { name: "Add reference version" }).press("Enter");
  await picker.getByRole("button", { name: "Close target picker" }).press("Enter");
  await expect(bar).toContainText("+1 ref");
  const composerTarget = (await bar.textContent())!;
  const before = studio.guard!.calls.length;
  await navigator(page).getByRole("button", { name: "Outliner", exact: true }).press("Enter");
  const graphPane = outliner(page);
  const oldRow = graphPane.getByRole("treeitem", { name: old.name });
  const toggle = oldRow.getByRole("button", { name: old.name });
  await toggle.press("ArrowRight");
  await expect(oldRow.getByText("No published dependencies", { exact: true })).toBeVisible();
  await toggle.press("ArrowLeft");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.press("Space");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await graphPane.getByRole("button", { name: "Used by", exact: true }).press("Enter");
  await expect(graphPane.getByText("No published uses of this version", { exact: true })).toBeVisible();
  await graphPane.getByRole("button", { name: "Uses", exact: true }).press("Space");
  await expect(toggle).toBeVisible();
  await expect(draft).toHaveValue("Retain this Composer draft");
  await expect(feedback).toHaveValue("Unsent annotation on the current publication");
  await expect(bar).toHaveText(composerTarget);

  const count = graphCalls(studio).length;
  const flood = async (prefix: string) => {
    const reviewVersionRead = page.waitForRequest(request => new URL(request.url()).pathname === `${base}/items/${viewed.item_id}/versions`);
    for (let frame = 1; frame <= 20; frame++) studio.emit("frame.available", { version_id: viewed.version_id, artifact_id: `${prefix}-${frame}`, job_id: "render-job", source_frame: frame });
    studio.emit("assistant.completed", { message_id: prefix, text: prefix, native_agent_id: null });
    await reviewVersionRead;
    await expect(page.getByText(prefix, { exact: true })).toBeVisible();
  };
  await navigator(page).getByRole("button", { name: "Shots", exact: true }).press("Enter");
  await flood("Hidden relationship test flood complete");
  await navigator(page).getByRole("button", { name: "Outliner", exact: true }).press("Enter");
  await expect(toggle).toBeVisible();
  await navigator(page).getByRole("button", { name: "Collapse Navigator" }).press("Enter");
  await flood("Collapsed relationship test flood complete");
  await navigator(page).getByRole("button", { name: "Expand Navigator" }).press("Enter");
  await expect(toggle).toBeVisible();
  expect(graphCalls(studio)).toHaveLength(count);
  expect(studio.guard!.calls.slice(before).filter(call => call.method !== "GET" || /proxy-mapping|\/content$/.test(call.url))).toEqual([]);
  await oldRow.getByRole("button", { name: "Open version", exact: true }).press("Enter");
  await expect(review(page).getByRole("heading", { name: old.name })).toBeVisible();
  await expect(draft).toHaveValue("Retain this Composer draft");
  await expect(bar).toHaveText(composerTarget);
  await graphPane.getByRole("button", { name: "Used by", exact: true }).press("Enter");
  await graphPane.getByRole("treeitem", { name: viewed.name }).getByRole("button", { name: "Open version", exact: true }).press("Enter");
  await expect(review(page).getByRole("heading", { name: viewed.name, exact: true })).toBeVisible();
  await review(page).getByRole("button", { name: /Annotations/ }).press("Enter");
  await review(page).getByRole("button", { name: /Unsent annotation on the current publication/ }).press("Enter");
  await expect(feedback).toHaveValue("Unsent annotation on the current publication");
  expect(studio.submissions).toEqual([]);
  expect(studio.saveAnnotationCalls).toEqual([]);
  studio.assertHermetic();
});

test("E5: in-flight reads are cancelled on direction, root and collapse, and stale cursors restart at page one", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const [old, current] = studio.versionRecords;
  const initial = deferred();
  const collapsed = deferred();
  const onRoot = deferred();
  const oldEndpoint = versionEndpoint(old);
  const currentEndpoint = versionEndpoint(current);
  const nodes: RelationshipNodeSummary[] = [old, current].map(record => ({ ...versionEndpoint(record), label: record.name, item_kind: "asset", is_current: record === current, is_selected: false }));
  let usesRequests = 0;
  let reverseRequests = 0;
  const routes: MockRoute[] = [{ path: graphPath, handler: async route => {
    const url = new URL(route.request().url());
    const direction = url.searchParams.get("direction") as RelationshipDirection;
    const root = url.pathname.includes(old.version_id) ? oldEndpoint : currentEndpoint;
    const isOld = root === oldEndpoint;
    const cursor = url.searchParams.get("cursor");
    if (cursor) return route.fulfill({ status: 409, json: { schema_version: "1.0", error: { code: "relationship_revision_changed", message: "Superseded", details: {} } } });
    if (isOld) await onRoot.promise;
    else if (direction === "used_by" && ++reverseRequests === 1) await collapsed.promise;
    else if (direction === "uses" && ++usesRequests === 1) await initial.promise;
    const items: RelationshipEdge[] = !isOld && direction === "uses" ? [{ relation: "declared_dependency", from: currentEndpoint, to: oldEndpoint, details: {} }] : [];
    const result: RelationshipPage = { schema_version: "1.0", project_id: projectId, direction, root, nodes, items, relationship_revision: usesRequests,
      coverage: !isOld && direction === "used_by" ? { state: "partial", reasons: ["source_unavailable", "indexing"] } : { state: "complete", reasons: [] }, next_cursor: usesRequests === 2 && items.length ? "signed-cursor" : null };
    await route.fulfill({ json: result });
  } }];
  // Keep the mocked operation running after abort to prove the client drops late responses.
  await observeGraphAborts(page);
  await studio.install({ catalogue: true, savedLayout: true, routes });
  await saveViewing(page, current);
  await studio.open(); await studio.ready();
  try {
    await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
    await expect.poll(() => usesRequests).toBe(1);
    const graphPane = outliner(page);
    await graphPane.getByRole("button", { name: "Used by", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-aborted-graph", /direction=uses/);
    await expect.poll(() => reverseRequests).toBe(1);
    await navigator(page).getByRole("button", { name: "Collapse Navigator" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-aborted-graph", /direction=used_by/);
    initial.release(); collapsed.release();
    await expect(graphPane).toBeHidden();
    await navigator(page).getByRole("button", { name: "Expand Navigator" }).click();
    await expect(graphPane.getByText(/source_unavailable, indexing/)).toBeVisible();
    await expect(graphPane.getByText("No published uses of this version")).toHaveCount(0);
    await graphPane.getByRole("button", { name: "Uses", exact: true }).click();
    const loadMore = graphPane.getByRole("button", { name: "Load more" });
    await loadMore.click();
    await expect(graphPane.getByRole("alert")).toContainText("Relationships changed");
    await expect(loadMore).toHaveCount(0);
    await graphPane.getByRole("button", { name: "Restart from page 1" }).click();
    const row = graphPane.getByRole("treeitem", { name: old.name });
    await expect(row).toBeVisible();
    await expect(graphPane.getByRole("alert")).toHaveCount(0);
    expect(graphCalls(studio).at(-1)?.url).toBe(`${base}/versions/${current.version_id}/relationships?direction=uses`);

    await row.getByRole("button", { name: "Focus in Outliner" }).click();
    await expect.poll(() => graphCalls(studio).some(call => call.url.includes(`/${old.version_id}/relationships`))).toBe(true);
    await graphPane.getByRole("button", { name: "Back", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-aborted-graph", new RegExp(`/${old.version_id}/relationships`));
    onRoot.release();
    await expect(graphPane.getByRole("heading", { name: current.name })).toBeVisible();
    await expect(row).toBeVisible();
    expect(studio.guard!.calls.every(call => call.method === "GET")).toBe(true);
    studio.assertHermetic();
  } finally { initial.release(); collapsed.release(); onRoot.release(); }
});

test("E5: project activation aborts the old tree and reactivation reads the active project's exact root again", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const [, viewed] = studio.versionRecords;
  const otherId = "other-project", otherBase = `/api/v1/projects/${otherId}`;
  const other = { ...project, project_id: otherId, name: "Other project", repository_id: "other-repo", path: "/synthetic/other" };
  const delayed = deferred();
  let active = projectId;
  let revision = 0;
  const activeRecord = (record: ProjectRecord): ProjectRecord => ({ ...record, state: active === record.project_id ? "active" : "inactive", state_revision: revision });
  let reads = 0;
  await observeGraphAborts(page);
  await page.addInitScript(projectId => {
    localStorage.setItem(`proscenium.owner.layout.v1.${projectId}`, JSON.stringify({ mode: "split", navigatorCollapsed: false, assistantCollapsed: false, navigatorWidth: 280, assistantWidth: 400 }));
  }, otherId);
  await studio.install({ catalogue: true, savedLayout: true, additionalProjectIds: [otherId], routes: [
    { path: "/api/v1/projects", handler: route => route.fulfill({ json: pageOf([activeRecord(project), activeRecord(other)]) }) },
    { method: "POST", path: new RegExp(`^(?:${base}|${otherBase})/activate$`), handler: route => {
      active = new URL(route.request().url()).pathname.split("/").at(-2)!;
      revision += 1;
      studio.snapshot.project = activeRecord(project);
      return route.fulfill({ json: { schema_version: "1.0", operation_id: route.request().postDataJSON().operation_id, resource_id: active, state: "updated", state_revision: revision, replayed: false } });
    } },
    { path: `${otherBase}/snapshot`, handler: route => route.fulfill({ json: { ...snapshot(), project: activeRecord(other) } }) },
    ...["requests", "items", "jobs", "artifacts", "reviews", "selections"].map(resource => ({ path: `${otherBase}/${resource}`, body: pageOf([]) })),
    { path: `${otherBase}/events`, body: { schema_version: "1.0", project_id: otherId, events: [], last_available: 0 } },
    { path: graphPath, handler: async route => {
      const read = ++reads;
      if (read === 1) await delayed.promise;
      const root = versionEndpoint(viewed);
      const result: RelationshipPage = { schema_version: "1.0", project_id: projectId, root, direction: "uses", relationship_revision: read,
        items: [], nodes: [{ ...root, item_kind: "asset", label: read === 1 ? "Obsolete response" : viewed.name, is_current: true, is_selected: false }],
        coverage: { state: "complete", reasons: [] }, next_cursor: null };
      await route.fulfill({ json: result });
    } },
  ] });
  await saveViewing(page, viewed);
  const open = () => navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
  async function activate(from: ProjectRecord, to: ProjectRecord) {
    await page.getByRole("button", { name: from.name, exact: true }).click();
    await page.getByRole("combobox", { name: "Saved project" }).selectOption(to.project_id);
    await page.getByRole("button", { name: "Activate selected project" }).click();
    await expect(page.getByRole("button", { name: to.name, exact: true })).toBeVisible();
    await studio.ready();
  }
  await studio.open(); await studio.ready();
  try {
    await open();
    await expect.poll(() => reads).toBe(1);
    await activate(project, other);
    await expect(page.locator("html")).toHaveAttribute("data-aborted-graph", /a-v2\/relationships/);
    await open();
    delayed.release();
    await expect(outliner(page).getByText("No published versions in this project to navigate.")).toBeVisible();
    await expect(outliner(page).getByRole("tree")).toHaveCount(0);
    expect(reads).toBe(1);
    await activate(other, project);
    await open();
    await expect(outliner(page).getByText("No published dependencies", { exact: true })).toBeVisible();
    await expect(outliner(page).getByRole("heading", { name: viewed.name })).toBeVisible();
    await expect(outliner(page).getByText("Obsolete response")).toHaveCount(0);
    expect(reads).toBe(2);
    studio.assertHermetic();
  } finally { delayed.release(); }
});

test("Navigator Space activates Outliner controls without starting Animation playback", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const [old, current] = studio.versionRecords;
  await studio.install({ catalogue: true, savedLayout: true, routes: [graph(studio, [], [
    { relation: "derived_from", from: versionEndpoint(current), to: versionEndpoint(old), details: {} },
  ])] });
  await saveViewing(page, old);
  await studio.open(); await studio.ready();
  const workspace = review(page);
  await workspace.getByRole("button", { name: "Animation", exact: true }).click();
  await expect(workspace.getByRole("textbox", { name: "Feedback" })).toHaveCount(0);
  const play = workspace.getByRole("button", { name: "Play", exact: true });
  const pause = workspace.getByRole("button", { name: "Pause", exact: true });
  await page.locator(".brand").click();
  await page.keyboard.press("Space");
  await expect(pause).toBeVisible(); // The shortcut still works with non-interactive focus.
  await page.keyboard.press("Space");
  await expect(play).toBeVisible();

  await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
  const graphPane = outliner(page);
  const usedBy = graphPane.getByRole("button", { name: "Used by", exact: true });
  await usedBy.press("Space");
  await expect(usedBy).toHaveAttribute("aria-pressed", "true");
  await expect(play).toBeVisible();
  const expand = graphPane.getByRole("button", { name: current.name, exact: true });
  await expand.press("Space");
  await expect(expand).toHaveAttribute("aria-expanded", "true");
  await expect(graphPane.getByText("No published uses of this version", { exact: true })).toBeVisible();
  await expect(play).toBeVisible();
  await expand.press("2");
  await expect(workspace.getByRole("button", { name: "Animation", exact: true })).toHaveAttribute("aria-pressed", "true");
  studio.assertHermetic();
});

test("Live item and panel choices and artefact revocations do not reread topology", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const [old, current, board] = studio.versionRecords;
  const boardRoot = versionEndpoint(board);
  const shot = { kind: "shot" as const, board: boardRoot.ref, shot_id: "shot-1", label: "Opening shot", intent: "Opening shot", binding_status: "unbound" as const };
  const panel = { kind: "panel" as const, board: boardRoot.ref, shot_id: shot.shot_id, frame_id: "frame-1", label: "Opening panel",
    role: "start" as const, authored_state: "available" as const, artifact_id: "panel-image", artifact_state: "ready" as const, owner_selected: true };
  const record: SelectionRecord = { schema_version: "1.0", project_id: projectId, selection_id: "frame-selection", state_revision: 1, state: "selected",
    actor: { kind: "user", id: "owner" }, choice: { kind: "artifact", artifact_id: panel.artifact_id },
    slot: { kind: "storyboard_frame", storyboard_version: boardRoot.ref, frame_id: panel.frame_id, shot_id: shot.shot_id } };
  let selections = [record];
  studio.retained = [retained(panel.artifact_id, "verified")];
  await studio.install({ catalogue: true, savedLayout: true, routes: [
    { path: `${base}/selections`, handler: route => route.fulfill({ json: pageOf(selections) }) },
    graph(studio, [shot, panel], [
      { relation: "contains_shot", from: boardRoot, to: shot, details: { ordinal: 0 } },
      { relation: "contains_panel", from: shot, to: panel, details: { ordinal: 0, role: panel.role, composition: panel.label, state: "available", artifact_id: panel.artifact_id } },
      ...[old, current].map(record => ({ relation: "board_reference" as const, from: boardRoot, to: versionEndpoint(record), details: {} })),
    ]),
  ] });
  studio.snapshot.items = [item(current, old.version_id), item(board)];
  await saveViewing(page, board);
  await studio.open(); await studio.ready();
  await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
  const graphPane = outliner(page);
  const row = (name: string) => graphPane.getByRole("treeitem", { name, exact: true });
  const selected = (name: string) => row(name).getByText("Selected", { exact: true });
  await expect(selected(old.name)).toBeVisible();
  await expect(selected(current.name)).toHaveCount(0);
  await row(shot.label).getByRole("button", { name: shot.label }).click();
  const inspect = row(panel.label).getByRole("button", { name: "Inspect panel", exact: true });
  await expect(inspect).toBeEnabled();
  await expect(row(panel.label)).toContainText("Owner-selected image");
  const relationshipReads = graphCalls(studio).length;
  const selectionReads = () => studio.guard!.calls.filter(call => call.url.startsWith(`${base}/selections`)).length;
  const before = selectionReads();

  studio.snapshot.items = [item(current, current.version_id), item(board)];
  selections = [{ ...record, state: "withdrawn", state_revision: 2, choice: null }];
  studio.emit("selection.changed", { resource_id: record.selection_id, state: "withdrawn", state_revision: 2 });
  await expect(selected(current.name)).toBeVisible();
  await expect(selected(old.name)).toHaveCount(0);
  await expect(row(panel.label)).not.toContainText("Owner-selected image");
  expect(selectionReads()).toBe(before + 1);

  studio.retained = studio.retained.map(row => ({ ...row, artifact: { ...row.artifact, state: "unavailable" } }));
  studio.emit("tool.completed", { invocation_id: "revocation", name: "get_artifact", state: "completed", summary: "Artefact revoked", artifact_ids: [panel.artifact_id], model_id: null });
  await expect(row(panel.label)).toContainText("Image: unavailable");
  await expect(inspect).toBeDisabled();
  expect(graphCalls(studio)).toHaveLength(relationshipReads);
  expect(selectionReads()).toBe(before + 1);
  studio.assertHermetic();
});

test("Outliner resolves version choices once per board without selecting later preview images", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const [, chosenVersion, board] = studio.versionRecords;
  const publishedImage = "publication-image-a", laterPreview = "later-preview-b";
  // listItemVersions exposes this merged view, not the immutable publication (image A only).
  chosenVersion.artifact_ids = [publishedImage, laterPreview];
  studio.retained = [retained(publishedImage, "verified"), retained(laterPreview, "verified")];
  const boardRoot = versionEndpoint(board);
  const shot = { kind: "shot" as const, board: boardRoot.ref, shot_id: "shot-1", label: "Opening shot", intent: "Opening", binding_status: "unbound" as const };
  const panels = [publishedImage, laterPreview].map((artifact_id, index) => ({ kind: "panel" as const, board: boardRoot.ref, shot_id: shot.shot_id,
    frame_id: `frame-${index}`, label: index === 0 ? "Published image A" : "Preview image B", role: index === 0 ? "start" as const : "end" as const,
    authored_state: "available" as const, artifact_id, artifact_state: "ready" as const, owner_selected: false }));
  const records = panels.map((panel): SelectionRecord => ({ schema_version: "1.0", project_id: projectId, selection_id: panel.frame_id,
    state_revision: 1, state: "selected", actor: { kind: "user", id: "owner" }, choice: { kind: "version", version: versionEndpoint(chosenVersion).ref },
    slot: { kind: "storyboard_frame", storyboard_version: boardRoot.ref, shot_id: shot.shot_id, frame_id: panel.frame_id } }));
  // Both slots can name the same version, but only the exact published image matches.
  const selections = [records[1]];
  const planPath = `${base}/versions/${board.version_id}/shot-plan`;
  await studio.install({ catalogue: true, savedLayout: true, routes: [
    { method: "POST", path: `${base}/selections`, handler: async route => {
      const body = route.request().postDataJSON() as SelectionSave;
      expect(body).toMatchObject({ slot: records[0].slot, choice: records[0].choice });
      const receipt = { schema_version: "1.0", operation_id: body.operation_id, resource_id: body.selection_id, state: "selected", state_revision: 1, replayed: false };
      selections.push(records[0]);
      await route.fulfill({ json: receipt });
      studio.emit("selection.changed", receipt);
    } },
    { method: "GET", path: `${base}/selections`, handler: route => route.fulfill({ json: pageOf(selections) }) },
    { path: planPath, handler: route => {
      const plan: ShotPlan = { schema_version: "1.0", project_id: projectId, plan_id: "plan", source_commit: board.source_commit,
        brief_artifact_ids: [], asset_dependencies: [], unresolved: [], shots: [{ shot_id: shot.shot_id, intent: shot.intent, camera_intent: "Static",
          motion_beats: [], scene_version: null, source_range: null, fps: null, frames: panels.map(panel => ({ frame_id: panel.frame_id, role: panel.role,
            artifact_id: panel.artifact_id, composition: panel.label, state: "available", owner_selected: panel.artifact_id === publishedImage && selections.includes(records[0]) })) }] };
      return route.fulfill({ json: plan });
    } },
    graph(studio, [shot, ...panels], [
      { relation: "contains_shot", from: boardRoot, to: shot, details: { ordinal: 0 } },
      ...panels.map((panel, ordinal) => ({ relation: "contains_panel" as const, from: shot, to: panel, details: { ordinal, role: panel.role, composition: panel.label, state: "available", artifact_id: panel.artifact_id } })),
    ]),
  ] });
  studio.snapshot.items = [item(board), item(chosenVersion)];
  await studio.open(); await studio.ready();
  await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
  const graphPane = outliner(page);
  await graphPane.getByRole("combobox", { name: "Published item", exact: true }).selectOption(board.item_id);
  await graphPane.getByRole("combobox", { name: "Exact published version", exact: true }).selectOption(board.version_id);
  await graphPane.getByRole("button", { name: shot.label, exact: true }).click();
  const row = (index: number) => graphPane.getByRole("treeitem", { name: panels[index].label, exact: true });
  await expect(row(1)).toBeVisible();
  await expect(row(0)).not.toContainText("Owner-selected image");
  await expect(row(1)).not.toContainText("Owner-selected image");
  const before = studio.guard!.calls.length;
  // Studio's image picker writes artefact choices. Save a version choice through
  // the owner route to exercise the other supported wire variant.
  const command: SelectionSave = { schema_version: "1.0", project_id: projectId, operation_id: "choose-version", selection_id: records[0].selection_id,
    expected_revision: 0, slot: records[0].slot, choice: records[0].choice, action: "select" };
  expect(await page.evaluate(async ({ path, command }) => (await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(command) })).ok,
    { path: `${base}/selections`, command })).toBe(true);
  await expect(row(0)).toContainText("Owner-selected image");
  await expect(row(1)).not.toContainText("Owner-selected image");
  const reads = studio.guard!.calls.slice(before).filter(call => call.method === "GET");
  expect(reads.filter(call => /\/items\/[^/]+\/versions(?:\?|$)/.test(call.url) || graphPath.test(call.url.split("?")[0]))).toEqual([]);
  expect(reads.filter(call => call.url === planPath)).toHaveLength(1);
  studio.assertHermetic();
});

test("Retained-media inspection has no viewed catalogue root until Review returns to the pinned version", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const viewed = studio.versionRecords[1];
  await studio.install({ catalogue: true, savedLayout: true, routes: [graph(studio, [], [])] });
  await saveViewing(page, viewed);
  await studio.open(); await studio.ready();
  await expect(review(page).getByRole("heading", { name: viewed.name, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Inspect retained concept image legacy", exact: true }).click();
  await expect(review(page).getByText("Retained media · Not a catalogue version", { exact: true })).toBeVisible();
  await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
  const graphPane = outliner(page);
  await expect(graphPane.getByRole("combobox", { name: "Published item", exact: true })).toBeVisible();
  await expect(graphPane.getByText("Viewing in Review", { exact: true })).toHaveCount(0);
  expect(graphCalls(studio)).toHaveLength(0);
  await review(page).getByRole("button", { name: "Back to pinned version", exact: true }).click();
  await expect(graphPane.getByRole("heading", { name: viewed.name, exact: true })).toBeVisible();
  await expect(graphPane.getByText("Viewing in Review", { exact: true })).toBeVisible();
  await expect(graphPane.getByRole("combobox", { name: "Published item", exact: true })).toHaveCount(0);
  studio.assertHermetic();
});

test("Inspect artefact rejects a newly non-ready detail without navigating Review", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const sequence = version("sequence", "sequence-v1", "Assembly", "sequence");
  studio.versionRecords.push(sequence);
  const node = { kind: "artifact" as const, artifact_id: "sequence-audio", label: "Music", artifact_kind: "audio" as const, purpose: "final" as const, state: "ready" as const };
  const audio = { ...retained(node.artifact_id, "verified").artifact, kind: "audio" as const, media_type: "audio/flac" };
  let revoked = false;
  await studio.install({ savedLayout: true, routes: [
    { path: `${base}/artifacts/${node.artifact_id}`, handler: route => route.fulfill({ json: { ...audio, state: revoked ? "unavailable" : "ready" } }) },
    graph(studio, [node], [{ relation: "sequence_input", from: versionEndpoint(sequence), to: node, details: { role: "audio", track_id: "music", start_seconds: 0 } }]),
  ] });
  studio.snapshot.items = [item(sequence)];
  await saveViewing(page, sequence);
  await studio.open(); await studio.ready();
  await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
  const inspect = outliner(page).getByRole("button", { name: "Inspect artefact", exact: true });
  await expect(inspect).toBeEnabled();
  await expect(outliner(page).getByRole("button", { name: "Inspect panel", exact: true })).toHaveCount(0);
  revoked = true; // A revocation the retained metadata stream has not yet observed.
  await inspect.click();
  await expect(outliner(page).getByRole("alert")).toHaveText("The exact artefact is no longer available to inspect.");
  await expect(review(page).getByRole("heading", { name: sequence.name, exact: true })).toBeVisible();
  await expect(review(page).getByText("Retained media · Not a catalogue version")).toHaveCount(0);
  expect(graphCalls(studio)).toHaveLength(1);
  expect(studio.guard!.calls.some(call => call.url === `${base}/artifacts/${node.artifact_id}/content`)).toBe(false);
  studio.assertHermetic();
});

for (const kind of ["artifact", "panel"] as const) {
  test(`Newer retained readiness restores ${kind === "panel" ? "panel" : "artefact"} inspection without refetching relationships`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    const viewed = version("media-parent", "media-parent-v1", "Viewed publication", kind === "panel" ? "storyboard" : "sequence");
    studio.versionRecords.push(viewed);
    const root = versionEndpoint(viewed);
    const shot = { kind: "shot" as const, board: root.ref, shot_id: "opening", label: "Opening", intent: "Opening", binding_status: "unbound" as const };
    const media = retained("restored-media", "verified");
    if (kind === "artifact") media.artifact = { ...media.artifact, kind: "video", media_type: "video/mp4", purpose: "final" };
    const panel = { kind: "panel" as const, board: root.ref, shot_id: shot.shot_id, frame_id: "first", label: "Panel image", role: "start" as const,
      authored_state: "available" as const, artifact_id: media.artifact.artifact_id, artifact_state: "unavailable" as const, owner_selected: false };
    const artefact = { kind: "artifact" as const, artifact_id: media.artifact.artifact_id, label: "Sequence media", artifact_kind: "video" as const, purpose: "final" as const, state: "unavailable" as const };
    const status = (state: typeof media.artifact.state) => ({ ...media, artifact: { ...media.artifact, state } });
    const stale = deferred(), entered = deferred();
    let holdStaleRead = true;
    await studio.install({ savedLayout: true, routes: [
      { path: `${base}/artifacts`, handler: async route => {
        if (holdStaleRead) {
          holdStaleRead = false;
          entered.release();
          await stale.promise;
          return route.fulfill({ json: pageOf([status("ready"), retained("stale-read-delivered", "verified")]) });
        }
        await route.fulfill({ json: pageOf(studio.retained) });
      } },
      ...(kind === "panel" ? [{ path: `${base}/versions/${viewed.version_id}/shot-plan`, body: { schema_version: "1.0", project_id: projectId, plan_id: "board", shots: [], asset_dependencies: [], unresolved: [] } }] : []),
      graph(studio, kind === "panel" ? [shot, panel] : [artefact], kind === "panel" ? [
        { relation: "contains_shot", from: root, to: shot, details: { ordinal: 0 } },
        { relation: "contains_panel", from: shot, to: panel, details: { ordinal: 0, role: "start", composition: panel.label, state: "available", artifact_id: panel.artifact_id } },
      ] : [{ relation: "sequence_input", from: root, to: artefact, details: { role: "clip", entry_id: "clip", source_frames: { start: 1, end: 1 }, sequence_frames: { start: 1, end: 1 } } }]),
    ] });
    studio.snapshot.items = [item(viewed)];
    studio.retained = [status("unavailable")];
    await saveViewing(page, viewed);
    await studio.open(); await studio.ready();
    try {
      await entered.promise;
      await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
      const graphPane = outliner(page);
      if (kind === "panel") await graphPane.getByRole("button", { name: shot.label, exact: true }).click();
      const inspect = graphPane.getByRole("button", { name: kind === "panel" ? "Inspect panel" : "Inspect artefact", exact: true });
      const row = graphPane.getByRole("treeitem", { name: kind === "panel" ? panel.label : artefact.label, exact: true });
      await expect(inspect).toBeDisabled();
      const topologyReads = graphCalls(studio).length;
      const refreshRetained = () => studio.emit("tool.completed", { invocation_id: `readiness-${studio.events.length}`, name: "get_artifact", state: "completed", summary: "Retained status changed", artifact_ids: [media.artifact.artifact_id], model_id: null });

      // The Assets count acknowledges that React consumed the delayed snapshot.
      await navigator(page).getByRole("button", { name: "Assets", exact: true }).click();
      await expect(navigator(page).getByRole("button", { name: "Retained media (0)", exact: true })).toBeVisible();
      stale.release();
      await expect(navigator(page).getByRole("button", { name: "Retained media (2)", exact: true })).toBeVisible();
      await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
      await expect(row).toContainText("unavailable");
      await expect(inspect).toBeDisabled();
      expect(graphCalls(studio)).toHaveLength(topologyReads);

      studio.retained = [status("ready")];
      refreshRetained();
      await expect(inspect).toBeEnabled();
      studio.retained = [status("corrupt")];
      refreshRetained();
      await expect(row).toContainText("corrupt");
      await expect(inspect).toBeDisabled();
      studio.retained = [];
      const omitted = page.waitForResponse(response => new URL(response.url()).pathname === `${base}/artifacts`);
      refreshRetained();
      await (await omitted).finished();
      await expect(inspect).toBeDisabled(); // Omission cannot resurrect older ready evidence.
      expect(graphCalls(studio)).toHaveLength(topologyReads);

      studio.retained = [status("ready")];
      refreshRetained();
      await expect(inspect).toBeEnabled();
      expect(graphCalls(studio)).toHaveLength(topologyReads);
      await inspect.click();
      await expect(review(page).getByText("Retained media · Not a catalogue version", { exact: true })).toBeVisible();
      expect(graphCalls(studio)).toHaveLength(topologyReads);
      studio.assertHermetic();
    } finally { stale.release(); }
  });
}

test("Collapsing an ancestor aborts a descendant lookup and fences late navigation", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const viewed = studio.versionRecords[1];
  const parent = version("staging", "staging-v1", "Staging", "scene");
  const child = version("rig", "rig-v1", "Rig");
  studio.versionRecords.push(parent, child);
  const gate = deferred(), entered = deferred();
  const path = `${base}/items/${child.item_id}/versions`;
  await observeGraphAborts(page, path);
  await studio.install({ catalogue: true, savedLayout: true, routes: [
    { path, handler: async route => { entered.release(); await gate.promise; await route.fulfill({ json: pageOf([child]) }); } },
    graph(studio, [], [
      { relation: "declared_dependency", from: versionEndpoint(viewed), to: versionEndpoint(parent), details: {} },
      { relation: "declared_dependency", from: versionEndpoint(parent), to: versionEndpoint(child), details: {} },
    ]),
  ] });
  await saveViewing(page, viewed);
  await studio.open(); await studio.ready();
  try {
    await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
    const graphPane = outliner(page);
    const toggle = graphPane.getByRole("button", { name: parent.name, exact: true });
    await toggle.click();
    const leaf = graphPane.getByRole("treeitem", { name: child.name, exact: true });
    await leaf.getByRole("button", { name: "Open version", exact: true }).click();
    await entered.promise;
    await toggle.click();
    await expect(leaf).toHaveCount(0);
    await expect(page.locator("html")).toHaveAttribute("data-aborted-graph", new RegExp(path));
    const reply = page.waitForResponse(response => new URL(response.url()).pathname === path);
    gate.release();
    await (await reply).finished();
    await expect(review(page).getByRole("heading", { name: viewed.name, exact: true })).toBeVisible();
    await expect(navigator(page).getByRole("button", { name: "Shots", exact: true })).toHaveAttribute("aria-selected", "false");
    await expect(graphPane.getByRole("heading", { name: viewed.name, exact: true })).toBeVisible();
    studio.assertHermetic();
  } finally { gate.release(); }
});

test("Successful pagination and publication/reconnect invalidation respect Navigator visibility", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const viewed = studio.versionRecords[1];
  const inputs = [version("first", "first-v1", "First input"), version("second", "second-v1", "Second input")];
  const root = versionEndpoint(viewed);
  let revision = 1;
  await studio.install({ catalogue: true, savedLayout: true, routes: [{ path: graphPath, handler: route => {
    const cursor = new URL(route.request().url()).searchParams.get("cursor");
    const record = inputs[cursor ? 1 : 0];
    const endpoint = versionEndpoint(record);
    const result: RelationshipPage = { schema_version: "1.0", project_id: projectId, root, direction: "uses", relationship_revision: revision,
      nodes: [{ ...root, label: viewed.name, item_kind: "asset", is_current: true, is_selected: false }, { ...endpoint, label: record.name, item_kind: "asset", is_current: true, is_selected: false }],
      items: [{ relation: "declared_dependency", from: root, to: endpoint, details: {} }],
      coverage: { state: "complete", reasons: [] }, next_cursor: cursor ? null : `signed-${revision}` };
    return route.fulfill({ json: result });
  } }] });
  await saveViewing(page, viewed);
  await studio.open(); await studio.ready();
  await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
  const graphPane = outliner(page);
  const row = (index: number) => graphPane.getByRole("treeitem", { name: inputs[index].name, exact: true });
  await expect(row(0)).toBeVisible();
  const more = graphPane.getByRole("button", { name: "Load more", exact: true });
  await more.click();
  await expect(row(1)).toBeVisible();
  await expect(row(0)).toBeVisible();
  await expect(more).toHaveCount(0);
  expect(graphCalls(studio).map(call => new URL(call.url, baseURL).searchParams.get("cursor"))).toEqual([null, "signed-1"]);

  const publish = () => {
    revision++;
    studio.emit("publication.created", { schema_version: "1.0", project_id: projectId, publication_id: `publication-${revision}`,
      versions: [root.ref], job_ids: [], event_sequence: studio.events.length + 1, replayed: false });
  };
  const reconnect = async () => {
    const socketCount = studio.sockets.length;
    studio.sockets.at(-1)!.close({ code: 1000, reason: "Synthetic reconnect" });
    await expect.poll(() => studio.sockets.length).toBe(socketCount + 1);
    await studio.ready();
  };
  publish();
  await expect.poll(() => graphCalls(studio).length).toBe(3);
  await expect(row(1)).toHaveCount(0);
  await expect(more).toBeVisible();
  await reconnect();
  await expect.poll(() => graphCalls(studio).length).toBe(4);
  await expect(more).toBeVisible();

  await navigator(page).getByRole("button", { name: "Collapse Navigator" }).click();
  const itemRead = page.waitForRequest(request => new URL(request.url()).pathname === `${base}/items`);
  publish();
  await itemRead;
  await reconnect();
  expect(graphCalls(studio)).toHaveLength(4);
  await navigator(page).getByRole("button", { name: "Expand Navigator" }).click();
  await expect.poll(() => graphCalls(studio).length).toBe(5);
  await expect(row(0)).toBeVisible();
  expect(new URL(graphCalls(studio).at(-1)!.url, baseURL).searchParams.has("cursor")).toBe(false);
  studio.assertHermetic();
});

for (const fault of ["mapping revoked", "proxy revoked", "GLB load failed"] as const) {
  test(`E6: ordinary Objects clears selection when ${fault}`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    const mapping = studio.installProxyFixture("a-v2", { sampleFrame: 24 });
    const owner = studio.versionRecords.find(record => record.version_id === "a-v2")!;
    const glb = deferred();
    const content = `${base}/artifacts/${mapping.proxy_artifact_id}/content`;
    await studio.install({ catalogue: true, savedLayout: true, routes: fault === "GLB load failed" ? [{ path: content, handler: async route => {
      await glb.promise;
      return route.fulfill({ status: 422, json: { error: { code: "media_unavailable", message: "Fixture GLB failed" } } });
    } }] : [] });
    await saveViewing(page, owner);
    await studio.open(); await studio.ready();
    const region = review(page);
    await region.getByRole("button", { name: "3D Inspection", exact: true }).click();
    await region.getByRole("button", { name: "Objects at frame 24", exact: true }).click();
    const list = region.getByRole("region", { name: "Objects at frame 24", exact: true });
    const housing = list.getByRole("button", { name: "Housing housing-1" });
    await housing.click();
    await expect(housing).toHaveAttribute("aria-pressed", "true");
    const currentContext = () => page.evaluate(id => JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)!), projectId);
    await expect.poll(currentContext).toMatchObject({ selected_objects: [{ object_id: "housing", instance_id: "housing-1" }] });

    if (fault === "GLB load failed") {
      await expect.poll(() => studio.guard!.calls.some(call => call.url === content)).toBe(true);
      glb.release();
    } else {
      const id = fault === "mapping revoked" ? mapping.mapping_artifact_id! : mapping.proxy_artifact_id;
      studio.retained = studio.retained.map(row => row.artifact.artifact_id === id ? { ...row, artifact: { ...row.artifact, state: "unavailable" } } : row);
      studio.emit("tool.completed", { invocation_id: "revocation", name: "get_artifact", state: "completed", summary: "Artefact revoked", artifact_ids: [id], model_id: null });
    }
    await expect(list.getByRole("status")).toContainText(fault === "GLB load failed" ? "Could not load the inspection proxy" : "The mapping or proxy artefact is no longer available");
    await expect(list.getByRole("button")).toHaveCount(0);
    await expect.poll(currentContext).toMatchObject({ selected_objects: [] });
    await page.getByRole("textbox", { name: "Message Copilot" }).fill("Review without the unavailable proxy.");
    await page.getByRole("button", { name: "Queue message", exact: true }).click();
    await expect.poll(() => studio.contexts.at(-1)).toMatchObject({ selected_objects: [] });
    studio.assertHermetic();
  });
}

for (const revoked of ["mapping", "proxy"] as const) {
  test(`E6: both 2D markup send paths require image-only confirmation after pinned ${revoked} revocation`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    const mapping = studio.installProxyFixture("a-v2");
    const owner = studio.versionRecords.find(record => record.version_id === "a-v2")!;
    const camera: Camera = { space: "blender_world_m", kind: "perspective", camera_to_world: [1, 0, 0, 0, 0, 1, 0, -6, 0, 0, 1, 3, 0, 0, 0, 1],
      projection: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, -0.2, 0, 0, -1, 0], aspect: 1, near_m: 0.1, far_m: 100, source_camera_id: null, vertical_fov_degrees: 50 };
    studio.retained.push(retained("marked-2d-pin", "verified"));
    const original: AnnotationSnapshot = { annotation_id: "saved-2d-pin", saved_revision: 1, text: "Saved collar pin", camera,
      markup_artifact_id: "marked-2d-pin", markups: [{ id: "pin-mark", kind: "pin", points: [[0.5, 0.5]], text: "Saved collar pin", width_px: 4 }],
      target: { origin: "proxy_capture", artifact_id: "verified", displayed_sha256: "a".repeat(64), dimensions: { width: 1, height: 1 }, item_id: owner.item_id,
        version_id: owner.version_id, shot_id: null, render_job_id: null, source_frame: 1, media_artifact_id: null },
      range: { start: 1, end: 1 }, spatial_status: "valid", spatial_hits: [{ proxy_artifact_id: mapping.proxy_artifact_id, mapping_artifact_id: mapping.mapping_artifact_id!,
        version_id: owner.version_id, sample_frame: 1, object_id: "collar", instance_id: "collar-1", space: "blender_world_m", position: [1, 1, 1], normal: [0, 0, 1], camera }] };
    studio.annotations.push(structuredClone(original));
    await studio.install({ catalogue: true, savedLayout: true });
    await saveViewing(page, owner);
    await studio.open(); await studio.ready();
    const region = review(page);
    await region.getByRole("button", { name: /Annotations/ }).click();
    await region.getByRole("button", { name: /Saved collar pin/ }).click();
    await region.getByRole("button", { name: "Edit 2D markup", exact: true }).click();
    const editor = region.getByRole("region", { name: "Pinned annotation", exact: true });
    await expect(editor.getByRole("img", { name: "Pinned clean annotation target" })).toHaveJSProperty("naturalWidth", 1);
    await expect(editor.getByRole("button", { name: "Queue feedback", exact: true })).toBeEnabled();

    const id = revoked === "mapping" ? mapping.mapping_artifact_id! : mapping.proxy_artifact_id;
    studio.retained = studio.retained.map(row => row.artifact.artifact_id === id ? { ...row, artifact: { ...row.artifact, state: "unavailable" } } : row);
    studio.emit("tool.completed", { invocation_id: "revocation", name: "get_artifact", state: "completed", summary: "Pinned artefact revoked", artifact_ids: [id], model_id: null });
    await expect(editor.getByRole("alert")).toContainText("Original 3D proxy is unavailable");
    await expect(editor.getByRole("button", { name: "Save annotation", exact: true })).toHaveCount(0);
    const confirm = editor.getByRole("checkbox", { name: "Send image-only snapshot without 3D spatial hit" });
    const feedback = editor.getByRole("button", { name: "Queue image-only feedback", exact: true });
    await expect(confirm).not.toBeChecked();
    await expect(feedback).toBeDisabled();
    await expect.poll(() => page.evaluate(project => JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${project}`)!), projectId))
      .toMatchObject({ selected_objects: [], annotations: [] });

    const composer = page.getByRole("textbox", { name: "Message Copilot" });
    const send = page.getByRole("button", { name: "Queue message", exact: true });
    await composer.fill("Apply the image-only feedback from Current view.");
    await send.click();
    await expect(page.getByText("Original 3D proxy is unavailable. Confirm image-only fallback in Review before sending.", { exact: true })).toBeVisible();
    expect(studio.contexts).toEqual([]);
    expect(studio.submissions).toEqual([]);
    expect(studio.saveAnnotationCalls).toEqual([]);

    await confirm.check();
    await expect(feedback).toBeEnabled();
    // Exercise both as the first sender across the mapping and proxy cases.
    const actions = revoked === "mapping" ? [feedback, send] : [send, feedback];
    for (const [index, action] of actions.entries()) {
      await action.click();
      await expect.poll(() => studio.submissions.length).toBe(index + 1);
      expect(studio.contexts[index]).toMatchObject({ version_id: owner.version_id, view_mode: "keyframes", selected_objects: [],
        annotations: [{ spatial_status: "unavailable", spatial_hits: [], derived_from: original.annotation_id, target: original.target, markups: original.markups }],
        attachments: [{ artifact_id: "verified", role: "reference" }, { artifact_id: "marked-2d-pin", role: "markup" }] });
    }
    expect(studio.saveAnnotationCalls).toHaveLength(1);
    const derived = studio.annotations.find(annotation => annotation.derived_from === original.annotation_id)!;
    expect(derived.annotation_id).not.toBe(original.annotation_id);
    expect(studio.contexts.map(context => context.annotations)).toEqual([[derived], [derived]]);
    expect(studio.annotations.find(annotation => annotation.annotation_id === original.annotation_id)).toEqual(original);
    studio.assertHermetic();
  });
}

test("E6: Objects uses the old pinned map, retains compatible pairs and never substitutes an unavailable map", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const scene = version("landing", "landing-v1", "Landing without proxy", "scene");
  const oldMap = studio.installProxyFixture("a-v2", { proxyId: "proxy-1", mapId: "map-1", sampleFrame: 1 });
  studio.installProxyFixture("a-v2", { proxyId: "proxy-2", mapId: "map-2", sampleFrame: 24, smallDark: true });
  const owner = studio.versionRecords.find(record => record.version_id === "a-v2")!;
  studio.versionRecords.push(scene);
  const camera: Camera = { space: "blender_world_m", kind: "perspective", camera_to_world: [1, 0, 0, 0, 0, 1, 0, -6, 0, 0, 1, 3, 0, 0, 0, 1],
    projection: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, -0.2, 0, 0, -1, 0], aspect: 1, near_m: 0.1, far_m: 100, source_camera_id: null, vertical_fov_degrees: 50 };
  studio.annotations.push({ annotation_id: "old-pin", saved_revision: 1, text: "Historical collar pin", markups: [], camera,
    target: { origin: "proxy_capture", artifact_id: "verified", displayed_sha256: "a".repeat(64), dimensions: { width: 1, height: 1 }, item_id: owner.item_id,
      version_id: owner.version_id, shot_id: null, render_job_id: null, source_frame: 1, media_artifact_id: null },
    range: { start: 1, end: 1 }, spatial_status: "valid", spatial_hits: [{ proxy_artifact_id: oldMap.proxy_artifact_id, mapping_artifact_id: oldMap.mapping_artifact_id!,
      version_id: owner.version_id, sample_frame: 1, object_id: "collar", instance_id: "collar-1", space: "blender_world_m", position: [1, 1, 1], normal: [0, 0, 1], camera }] });
  await studio.install({ catalogue: true, savedLayout: true, routes: [graph(studio, [], [{ relation: "declared_dependency", from: versionEndpoint(scene), to: versionEndpoint(owner), details: {} }])] });
  studio.snapshot.items.push(item(scene));
  await saveViewing(page, owner);
  await studio.open(); await studio.ready();
  const region = review(page);
  const reopen = async () => {
    await region.getByRole("button", { name: /Annotations/ }).click();
    await region.getByRole("button", { name: /Historical collar pin/ }).click();
  };
  await region.getByRole("button", { name: "3D Inspection", exact: true }).click();
  await expect(region.getByRole("button", { name: "Objects at frame 24" })).toBeVisible();
  await reopen();
  await region.getByRole("button", { name: "Objects at frame 1" }).click();
  const list = region.getByRole("region", { name: "Objects at frame 1" });
  const housing = list.getByRole("button", { name: "Housing housing-1" });
  const collar = list.getByRole("button", { name: "Collar collar-1" });
  await expect(collar).toHaveAttribute("aria-pressed", "true");
  await housing.click({ modifiers: ["Control"] });
  await expect(housing).toHaveAttribute("title", "housing / housing-1");
  await expect(list.getByRole("button", { pressed: true })).toHaveCount(2);
  await collar.click({ modifiers: ["Meta"] });
  await expect(collar).toHaveAttribute("aria-pressed", "false");
  await collar.click({ modifiers: ["Meta"] });
  await expect(list.getByRole("button", { pressed: true })).toHaveCount(2);

  await region.locator(".annotation-dock").getByRole("button", { name: "Close", exact: true }).click();
  const newest = region.getByRole("region", { name: "Objects at frame 24" });
  await expect(newest.getByRole("button", { pressed: true })).toHaveCount(1);
  await expect(newest.getByRole("button", { name: "Housing housing-1" })).toHaveAttribute("aria-pressed", "true");
  await expect(newest.getByText("Collar", { exact: true })).toHaveCount(0);

  studio.unavailableProxyIds.add("map-1");
  const before = studio.guard!.calls.length;
  await reopen();
  await expect(region.getByRole("button", { name: "Objects", exact: true })).toHaveAttribute("aria-expanded", "true");
  const unavailable = region.getByRole("region", { name: "Objects", exact: true });
  await expect(unavailable).toContainText("Pinned 3D proxy unavailable");
  await expect(unavailable.getByRole("button")).toHaveCount(0);
  expect(studio.guard!.calls.slice(before).filter(call => call.url.includes("proxy-mapping")).map(call => call.url)).toEqual([
    `${base}/versions/a-v2/proxy-mapping?mapping_artifact_id=map-1`,
  ]);
  expect(studio.annotations[0].spatial_hits[0]).toMatchObject({ mapping_artifact_id: "map-1", proxy_artifact_id: "proxy-1", object_id: "collar", instance_id: "collar-1" });

  await navigator(page).locator(".catalogue-item-header").filter({ hasText: scene.name }).click();
  await expect(region.getByRole("heading", { name: scene.name })).toBeVisible();
  await navigator(page).getByRole("button", { name: "Outliner", exact: true }).click();
  const relationship = outliner(page).getByRole("treeitem", { name: owner.name });
  await expect(relationship).toContainText("Declared dependency");
  const beforeExpand = studio.guard!.calls.length;
  await relationship.getByRole("button", { name: owner.name }).click();
  await expect(relationship.getByText("No published dependencies", { exact: true })).toBeVisible();
  expect(studio.guard!.calls.slice(beforeExpand).filter(call => /proxy-mapping|\/content$/.test(call.url))).toEqual([]);
  expect(studio.submissions).toEqual([]);
  studio.assertHermetic();
});
