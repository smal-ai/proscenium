import { expect, test } from "@playwright/test";
import { verifyLayout } from "./layout-journey";
import { projectId, StudioFixture, work } from "./studio-fixture";

test("DPR 2 splitters, remembered tabs, layout and conversation following", async ({ page, baseURL }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-dpr2", "DPR 2 geometry belongs to the DPR 2 project");
  await verifyLayout(page, baseURL!, 2);
});

test("DPR 2 bottom-right live status and Chat-focus click-to-review geometry", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.snapshot.active_work = work();
  await studio.install({ catalogue: true, savedLayout: true }); await studio.open(); await studio.ready();
  expect(await page.evaluate(() => devicePixelRatio)).toBe(2);
  await expect(page.locator(".assistant-heading .assistant-status")).toHaveCount(0);
  const status = page.locator(".composer-footer .assistant-status");
  for (const focus of [false, true]) {
    if (focus) await page.getByRole("button", { name: "Chat focus", exact: true }).click();
    await expect(status).toBeVisible();
    expect((await status.boundingBox())!.y).toBeGreaterThan(850);
    expect((await status.boundingBox())!.x).toBeGreaterThan(1400);
  }
  studio.emit("tool.started", { invocation_id: "prepare", name: "prepare_image_generation", state: "running", summary: "", artifact_ids: [], model_id: null });
  await expect(status).toHaveAttribute("aria-label", "Working · Preparing image…");
  await expect(status).toHaveText("Working · Preparing image…");
  await expect(page.locator("main.review-panel")).toBeHidden();
  await page.locator(".catalogue-item-header").filter({ hasText: "Asset A" }).click();
  await expect(page.locator("main.review-panel")).toBeVisible();
  await expect(page.locator("main.review-panel h1")).toHaveText("Asset A v2");
  await expect(page.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  const review = page.locator("main.review-panel");
  expect((await review.getByRole("navigation", { name: "Review mode", exact: true }).boundingBox())!.height).toBeLessThanOrEqual(52);
  expect((await review.locator('[aria-label="Review transport"]').boundingBox())!.height).toBeLessThanOrEqual(52);
  await review.getByRole("button", { name: "Annotate presented frame" }).click();
  await expect(review.getByRole("navigation", { name: "Review mode", exact: true })).toHaveCount(0);
  await expect(review.locator('[aria-label="Review transport"]')).toHaveCount(0);
  await expect(review.getByRole("button", { name: "Stroke", exact: true })).toBeVisible();
  expect((await review.locator(".annotation-dock").boundingBox())!.height).toBeLessThanOrEqual(120);
  await review.getByRole("button", { name: "Close draft", exact: true }).click();
  const surface = review.locator(".review-surface");
  expect(await surface.evaluate(element => element.scrollHeight <= element.clientHeight)).toBe(true);
  await page.getByRole("button", { name: "Critic", exact: true }).click();
  await expect(page.locator(".assistant-footer .assistant-status")).toHaveAttribute("aria-label", "Working · Preparing image…");
  await page.getByRole("button", { name: "Collapse Assistant" }).click();
  const rail = page.locator(".right-panel .panel-rail .assistant-status");
  await expect(rail).toBeVisible();
  expect((await rail.boundingBox())!.y).toBeGreaterThan(900);
  await expect(rail).toHaveAttribute("aria-label", "Working · Preparing image…");
  studio.assertHermetic();
});

test("DPR 2 3D spatial pin placement stays within 0.5 image pixel and keeps dock geometry bounded", async ({ page, baseURL }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-dpr2", "DPR 2 geometry belongs to the DPR 2 project");
  const studio = new StudioFixture(page, baseURL!);
  studio.installProxyFixture("a-v2", { proxyId: "proxy-a-v2", mapId: "map-a-v2", sampleFrame: 1 });
  await studio.install({ catalogue: true, savedLayout: true });
  await studio.open();
  await studio.ready();
  expect(await page.evaluate(() => devicePixelRatio)).toBe(2);

  await page.locator(".catalogue-item-header").filter({ hasText: "Asset A" }).click();
  const review = page.locator("main.review-panel");
  await review.getByRole("button", { name: "3D Inspection", exact: true }).click();
  const pinNoteBtn = review.getByRole("button", { name: "Pin note", exact: true });
  await expect(pinNoteBtn).toBeEnabled();
  await pinNoteBtn.click();

  const liveCam = await expect.poll(async () => {
    const raw = await page.evaluate(id => localStorage.getItem(`proscenium.owner.view.context.${id}`), projectId);
    return raw ? JSON.parse(raw).camera : null;
  }).not.toBeNull().then(async () => {
    const raw = await page.evaluate(id => localStorage.getItem(`proscenium.owner.view.context.${id}`), projectId);
    return JSON.parse(raw!).camera as { camera_to_world: number[]; projection: number[] };
  });

  // Project rotated, non-uniformly scaled Collar centre [-1.6, -1.6, 0.2] at DPR 2.
  const m = liveCam.camera_to_world;
  const wx = -1.6 - m[3], wy = -1.6 - m[7], wz = 0.2 - m[11];
  const vx = m[0] * wx + m[4] * wy + m[8] * wz;
  const vy = m[1] * wx + m[5] * wy + m[9] * wz;
  const vz = m[2] * wx + m[6] * wy + m[10] * wz;
  const p = liveCam.projection;
  const cw = p[12] * vx + p[13] * vy + p[14] * vz + p[15];
  const u = ((p[0] * vx + p[1] * vy + p[2] * vz + p[3]) / cw + 1) / 2;
  const v = (1 - (p[4] * vx + p[5] * vy + p[6] * vz + p[7]) / cw) / 2;

  const box = (await review.locator(".review-proxy canvas").boundingBox())!;
  const clickX = box.x + u * box.width;
  const clickY = box.y + v * box.height;
  await page.mouse.click(clickX, clickY);

  await expect(review.locator('[aria-label="Spatial pin tools"]')).toContainText("collar · collar-1");
  expect((await review.locator(".annotation-dock").boundingBox())!.height).toBeLessThanOrEqual(120);
  const surface = review.locator(".review-surface");
  expect(await surface.evaluate(element => element.scrollHeight <= element.clientHeight)).toBe(true);

  await review.getByRole("textbox", { name: "Feedback" }).fill("Tighten collar seam at DPR 2");
  await review.getByRole("button", { name: "Edit 2D markup", exact: true }).click();
  await expect(review.getByRole("button", { name: "Stroke", exact: true })).toBeVisible();
  const canvas2D = review.locator(".review-canvas");
  const box2D = (await canvas2D.boundingBox())!;
  await page.mouse.move(box2D.x + box2D.width * 0.45, box2D.y + box2D.height * 0.45);
  await page.mouse.down();
  await page.mouse.move(box2D.x + box2D.width * 0.55, box2D.y + box2D.height * 0.52, { steps: 4 });
  await page.mouse.up();
  await review.getByRole("button", { name: "Back to 3D view", exact: true }).click();
  await expect(review.locator('[aria-label="Spatial pin tools"]')).toContainText("collar · collar-1");

  await review.getByRole("button", { name: "Save annotation", exact: true }).click();
  await expect(review.locator('[aria-label="Spatial pin tools"] output')).toHaveText("saved");

  const saved = studio.annotations[0];
  expect(saved.markups.map(m => m.kind)).toEqual(["pin", "stroke"]);
  expect(saved.target.dimensions.width).toBeCloseTo(box.width * 2, -1);
  expect(saved.target.dimensions.height).toBeCloseTo(box.height * 2, -1);
  const [pinU, pinV] = saved.markups[0].points[0];
  const actualClickU = (clickX - box.x) / box.width;
  const actualClickV = (clickY - box.y) / box.height;
  expect(Math.abs(pinU - actualClickU) * saved.target.dimensions.width).toBeLessThanOrEqual(0.5);
  expect(Math.abs(pinV - actualClickV) * saved.target.dimensions.height).toBeLessThanOrEqual(0.5);
  expect(Math.abs(Math.hypot(...saved.spatial_hits[0].normal) - 1)).toBeLessThan(1e-4);

  studio.assertHermetic();
});
