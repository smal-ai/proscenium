import { expect, test, type Page } from "@playwright/test";
import type { Artifact, ItemRecord, SelectionRecord, SelectionSave, ShotPlan, VersionRecord } from "../../apps/frontend/src/api/generated/contracts";

const actor = { kind: "user", id: "synthetic-owner" } as const;
const syntheticImage = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360"><rect width="640" height="360" fill="#d3e2da"/><path d="M0 300 Q160 180 320 280 T640 240 V360 H0" fill="#6e9777"/><ellipse cx="330" cy="150" rx="80" ry="35" fill="#8bb254" transform="rotate(-30 330 150)"/><path d="M260 190 L400 110" stroke="#34583d" stroke-width="4"/><text x="24" y="32" font-family="sans-serif" font-size="20" fill="#233c32">SYNTHETIC CONCEPT - NOT A RENDER</text></svg>';
const image = (id: string, purpose: Artifact["purpose"] = "concept"): Artifact => ({
  schema_version: "1.0", artifact_id: id, project_id: "garden-project", kind: "image", purpose,
  sha256: "b".repeat(64), bytes: syntheticImage.length, media_type: "image/svg+xml", state: "ready", dimensions: { width: 640, height: 360 }, derived_from: [],
});
const version = (id: string): VersionRecord => ({
  schema_version: "1.0", project_id: "garden-project", item_id: "garden-board", version_id: id,
  kind: "storyboard", name: `Garden ${id}`, publication_id: `publication-${id}`, source_commit: "a".repeat(40),
  source: { entry_path: "storyboards/garden-plan/plan.json", files: [{ destination: "storyboards/garden-plan/plan.json", origin: { kind: "git", repository_id: "garden-repository", commit: "a".repeat(40), path: "storyboards/garden-plan/plan.json", sha256: "a".repeat(64) } }], dependencies: [], required_runtime: { application: "json", version: "1", features: [] }, digest: "a".repeat(64) },
  artifact_ids: [], preview_requests: [], preview_job_ids: [], created_at: "2026-11-14T12:00:00+02:00", actor,
});
const item: ItemRecord = { schema_version: "1.0", project_id: "garden-project", item_id: "garden-board", kind: "storyboard", name: "Garden", current_version_id: "board-v2", selected_version_id: null, state_revision: 1 };
const plan: ShotPlan = {
  schema_version: "1.0", plan_id: "garden-plan", project_id: "garden-project", source_commit: "a".repeat(40), treatment: "Watercolour garden", brief_artifact_ids: [], asset_dependencies: [], unresolved: ["Final image missing"],
  shots: [{ shot_id: "leaf-arc", intent: "Follow a leaf", camera_intent: "Slow camera arc", motion_beats: ["Leaf turns"], scene_version: { item_id: "scene-item", version_id: "scene-v2" }, source_range: { start: 1, end: 48 }, fps: { numerator: 24, denominator: 1 }, frames: [
    { frame_id: "leaf-start", role: "start", composition: "Leaf opening", artifact_id: "concept-a", state: "available", owner_selected: false },
    { frame_id: "leaf-mid", role: "intermediate", composition: "Arc midpoint", artifact_id: "render-a", state: "available", owner_selected: false },
    { frame_id: "leaf-end", role: "end", composition: "Leaf resting", artifact_id: null, state: "proposed", owner_selected: false },
  ] }],
};

async function mockApi(page: Page, options: { failPlan?: boolean; uncertain?: boolean } = {}) {
  const mutations: SelectionSave[] = [];
  const reads: string[] = [];
  const records: SelectionRecord[] = [];
  const operations = new Set<string>();
  let lostAcknowledgement = false;
  await page.route("**/api/v1/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const cursor = url.searchParams.get("cursor");
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (request.method() !== "GET") {
      expect(path).toBe("/api/v1/projects/garden-project/selections");
      const command = request.postDataJSON() as SelectionSave;
      expect(request.headers()["idempotency-key"]).toBe(command.operation_id);
      mutations.push(command);
      if (!operations.has(command.operation_id)) {
        const index = records.findIndex(value => JSON.stringify(value.slot) === JSON.stringify(command.slot));
        const previous = records[index];
        expect(command.expected_revision).toBe(previous?.state_revision ?? 0);
        if (previous) expect(command.selection_id).toBe(previous.selection_id);
        const record: SelectionRecord = { schema_version: "1.0", selection_id: command.selection_id, project_id: command.project_id, slot: command.slot, choice: command.choice, state: command.action === "select" ? "selected" : "withdrawn", state_revision: command.expected_revision + 1, actor };
        if (index >= 0) records[index] = record;
        else records.push(record);
        operations.add(command.operation_id);
      }
      if (options.uncertain && !lostAcknowledgement) {
        lostAcknowledgement = true;
        return route.abort("failed");
      }
      return json({ schema_version: "1.0", operation_id: command.operation_id, resource_id: command.selection_id, state: command.action === "select" ? "selected" : "withdrawn", state_revision: command.expected_revision + 1, replayed: mutations.length > operations.size });
    }
    reads.push(`${path}${url.search}`);
    if (path.endsWith("/items")) return json({ schema_version: "1.0", items: cursor ? [item] : [], next_cursor: cursor ? null : "items-page-2" });
    if (path.endsWith("/versions")) return json({ schema_version: "1.0", items: cursor ? [version("board-v1")] : [version("board-v2")], next_cursor: cursor ? null : "versions-page-2" });
    if (path.endsWith("/selections")) return json({ schema_version: "1.0", items: cursor ? records : [], next_cursor: cursor ? null : "selections-page-2" });
    if (path.endsWith("/frames")) return json({ schema_version: "1.0", items: cursor ? [{
      schema_version: "1.0", receipt_id: "frame-receipt", project_id: "garden-project", job_id: "render-job", attempt_id: "render-attempt",
      version_id: "scene-v1", shot_id: "leaf-arc", source_frame: 17, artifact_id: "render-a", output_path: "renders/frame.png",
      sha256: "b".repeat(64), dimensions: { width: 640, height: 360 }, input_digest: "c".repeat(64),
    }] : [], next_cursor: cursor ? null : "frames-page-2" });
    if (path.endsWith("/shot-plan")) {
      if (options.failPlan) return json({ schema_version: "1.0", error: { code: "capability_unavailable", message: "Storyboard reader not integrated", operation_id: null, retryable: false, details: {} } }, 501);
      return json({ ...plan, plan_id: path.includes("board-v1") ? "prior-plan" : plan.plan_id });
    }
    if (path.endsWith("/content")) return route.fulfill({ contentType: "image/svg+xml", body: syntheticImage });
    if (path.includes("/artifacts/")) {
      const id = path.split("/").at(-1)!;
      return json(id === "render-a" ? { ...image(id, "keyframe"), source_version_id: "scene-v1" } : image(id));
    }
    throw new Error(`Unexpected API request: ${path}`);
  });
  return { mutations, reads, records };
}

test("paginated ordered frames, readable 280px panel and exact central context without execution", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Plan garden-plan" })).toBeVisible();
  const browser = page.getByRole("region", { name: "Storyboard browser" });
  expect((await browser.boundingBox())?.width).toBe(280);
  expect(await browser.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  const frames = browser.locator(".sb-frame");
  await expect(frames).toHaveCount(3);
  await expect(frames.nth(1)).toContainText("intermediate · leaf-mid");
  await expect(frames.nth(2)).toContainText("Proposed · image unavailable");
  await expect(browser).toContainText("Stale render");
  await expect(browser).toContainText("scene-item / scene-v2");
  await frames.first().click();
  await expect(page.getByTestId("navigation")).toHaveText("board-v2");
  const context = JSON.parse((await page.getByTestId("context").textContent())!);
  expect(context.view_mode).toBe("keyframes");
  expect(context.target).toEqual({ origin: "concept", artifact_id: "concept-a", displayed_sha256: "b".repeat(64), dimensions: { width: 640, height: 360 }, item_id: "garden-board", version_id: "board-v2", shot_id: "leaf-arc", render_job_id: null, source_frame: null, media_artifact_id: null });
  expect(context.attachments).toEqual([{ artifact_id: "concept-a", role: "concept", use: "leaf-arc / leaf-start / start: Leaf opening" }]);
  await expect(page.getByAltText("Central selected image")).toBeVisible();
  await page.getByRole("button", { name: "Queue plan edit" }).click();
  await expect(page.getByTestId("queued")).toContainText("Planning only: no image generation or scene execution");
  expect(state.mutations).toEqual([]);
  expect(state.reads.some(value => value.includes("items-page-2"))).toBe(true);
  expect(state.reads.some(value => value.includes("versions-page-2"))).toBe(true);
  expect(state.reads.some(value => value.includes("selections-page-2"))).toBe(true);
  await page.screenshot({ path: "docs/evidence/w06/storyboard-1920x1080.png" });
});

test("select, supersede and withdraw preserve exact version slot and revisions", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/");
  await page.locator(".sb-frame").first().click();
  await page.getByRole("button", { name: "Select image", exact: true }).click();
  await expect(page.locator(".sb-frame").first()).toContainText("owner_selected: yes");
  await page.getByLabel("Alternative image artefact ID").fill("concept-b");
  await page.getByRole("button", { name: "Inspect candidate" }).click();
  await expect(page.getByText("Ready concept: concept-b")).toBeVisible();
  await page.getByRole("button", { name: "Supersede selection" }).click();
  await expect.poll(() => state.records[0]?.choice).toEqual({ kind: "artifact", artifact_id: "concept-b" });
  await expect(page.locator(".sb-frame").first()).toContainText("owner_selected: no");
  await expect(page.locator(".sb-frame").first()).toContainText("Authored image remains concept-a");
  await expect(page.getByRole("button", { name: "Withdraw", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Withdraw", exact: true }).click();
  await expect(page.locator(".sb-frame").first()).toContainText("owner_selected: no (withdrawn)");
  expect(state.mutations.map(value => value.expected_revision)).toEqual([0, 1, 2]);
  for (const command of state.mutations) expect(command.slot).toEqual({ kind: "storyboard_frame", storyboard_version: { item_id: "garden-board", version_id: "board-v2" }, shot_id: "leaf-arc", frame_id: "leaf-start" });
  expect(state.mutations[2].choice).toBeNull();
  await page.getByLabel("Storyboard version").selectOption("board-v1");
  await expect(page.getByRole("heading", { name: "Plan prior-plan" })).toBeVisible();
  await expect(page.locator(".sb-frame").first()).toContainText("owner_selected: no");
  await page.locator(".sb-frame").first().click();
  await page.getByRole("button", { name: "Select image", exact: true }).click();
  await expect.poll(() => state.mutations.length).toBe(4);
  expect(state.mutations[3].expected_revision).toBe(0);
  expect(state.mutations[3].selection_id).not.toBe(state.mutations[0].selection_id);
  expect(plan.shots[0].frames[0].owner_selected).toBe(false);
});

test("lost selection acknowledgement retains original operation for explicit retry", async ({ page }) => {
  const state = await mockApi(page, { uncertain: true });
  await page.goto("/");
  await page.locator(".sb-frame").first().click();
  await page.getByRole("button", { name: "Select image", exact: true }).click();
  await expect(page.getByRole("button", { name: "Retry exact operation" })).toBeVisible();
  expect(state.mutations).toHaveLength(1);
  await page.getByRole("button", { name: "Retry exact operation" }).click();
  await expect(page.locator(".sb-frame").first()).toContainText("owner_selected: yes");
  expect(state.mutations).toHaveLength(2);
  expect(state.mutations[1]).toEqual(state.mutations[0]);
  expect(state.records).toHaveLength(1);
});

test("missing render identity and unavailable backend remain explicit without generation", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/");
  await page.locator(".sb-frame").nth(1).click();
  await expect(page.getByText("Exact render-frame context unavailable.", { exact: false })).toBeVisible();
  const context = JSON.parse((await page.getByTestId("context").textContent())!);
  expect(context.target).toBeNull();
  expect(context.attachments).toEqual([]);
  expect(state.mutations).toEqual([]);
});

test("unavailable shot-plan route is a visible error, not empty success", async ({ page }) => {
  const state = await mockApi(page, { failPlan: true });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Storyboard reader not integrated");
  await expect(page.locator(".sb-frame")).toHaveCount(0);
  expect(state.mutations).toEqual([]);
});

test("render context uses paginated exact receipts, not storyboard timing or array index", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/?receipts");
  await page.locator(".sb-frame").nth(1).click();
  const context = JSON.parse((await page.getByTestId("context").textContent())!);
  expect(context.item_id).toBe("garden-board");
  expect(context.version_id).toBe("board-v2");
  expect(context.target).toEqual({ origin: "blender_render", artifact_id: "render-a", displayed_sha256: "b".repeat(64), dimensions: { width: 640, height: 360 }, item_id: "scene-item", version_id: "scene-v1", shot_id: "leaf-arc", render_job_id: "render-job", source_frame: 17, media_artifact_id: null });
  expect(context.attachments[0].role).toBe("render");
  expect(state.reads.some(value => value.includes("frames-page-2"))).toBe(true);
  expect(state.mutations).toEqual([]);
});
