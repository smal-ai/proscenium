import { expect, test } from "@playwright/test";
import { verifyLayout } from "./layout-journey";
import { StudioFixture, work } from "./studio-fixture";

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
