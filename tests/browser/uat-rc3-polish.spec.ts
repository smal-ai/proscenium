import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import type { Camera } from "../../apps/frontend/src/api";
import { boardName, kitName, kitV, makePolishStudio } from "./rc3-polish-fixture";
import { projectId } from "./studio-fixture";

const screenshotDir = process.env.PROSCENIUM_UAT_SCREENSHOT_DIR ?? "/tmp/opencode/proscenium-rc3-uat-2026-11-18/screenshots/cycle-1";
const reviewPanel = (page: Page) => page.locator("main.review-panel");
const navigator = (page: Page) => page.getByRole("complementary", { name: "Navigator", exact: true });
const kitHistory = (page: Page) => navigator(page).getByRole("button", { name: `Version history for ${kitName}`, exact: true });
const versionRow = (page: Page, ordinal: number) => navigator(page).locator(".catalogue-version-row").filter({ has: page.getByText(`v${ordinal}`, { exact: true }) });
const headerStatus = (page: Page) => reviewPanel(page).locator(".version-status-pill");
const dockButton = (dock: Locator, name: string) => dock.getByRole("button", { name, exact: true });
const outsidePass = "Frame 2 is outside this pass. Showing frame 1.";

async function screenshot(page: Page, testInfo: TestInfo, file: string) {
  // Both projects run the assertions. DPR 1 owns the single review pack.
  if (testInfo.project.name !== "chromium-dpr1") return;
  await mkdir(screenshotDir, { recursive: true });
  const png = await page.screenshot({ path: join(screenshotDir, file), animations: "disabled", caret: "hide", scale: "css" });
  expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([1920, 1080]);
}

async function openLatestKit(page: Page) {
  await expect(kitHistory(page)).toHaveAttribute("aria-expanded", "false");
  await kitHistory(page).click();
  await versionRow(page, 3).click();
  await expect(headerStatus(page)).toHaveText("Viewing v3");
  await kitHistory(page).click();
}

async function jumpFrame(input: Locator, ordinal: number) {
  await input.fill(String(ordinal));
  await input.press("Enter");
}

async function checkDock(review: Locator) {
  await expect(review.locator(".review-bar:not(.review-transport-bar)").getByRole("button", { name: /Close draft|Keep draft & close/ })).toHaveCount(0);
  const dock = review.locator(".annotation-dock");
  for (const name of ["Keep draft & close", "Discard draft", "Save annotation", "Queue feedback"]) await expect(dockButton(dock, name)).toBeVisible();
  expect((await dock.boundingBox())!.height).toBeLessThanOrEqual(120);
  return dock;
}

async function pinCollar(page: Page, review: Locator) {
  const pin = review.getByRole("button", { name: "Pin note", exact: true });
  await expect(pin).toBeEnabled();
  await pin.click();
  const camera = () => page.evaluate(id => {
    const raw = localStorage.getItem(`proscenium.owner.view.context.${id}`);
    return raw ? JSON.parse(raw).camera as Camera | null : null;
  }, projectId);
  await expect.poll(camera).not.toBeNull();
  const live = (await camera())!;
  // Project the known, rotated Collar mesh centre from Blender world metres.
  const m = live.camera_to_world;
  const x = -1.6 - m[3], y = -1.6 - m[7], z = 0.2 - m[11];
  const vx = m[0] * x + m[4] * y + m[8] * z;
  const vy = m[1] * x + m[5] * y + m[9] * z;
  const vz = m[2] * x + m[6] * y + m[10] * z;
  const p = live.projection;
  const w = p[12] * vx + p[13] * vy + p[14] * vz + p[15];
  const u = ((p[0] * vx + p[1] * vy + p[2] * vz + p[3]) / w + 1) / 2;
  const v = (1 - (p[4] * vx + p[5] * vy + p[6] * vz + p[7]) / w) / 2;
  const box = (await review.locator(".review-proxy canvas").boundingBox())!;
  await page.mouse.click(box.x + u * box.width, box.y + v * box.height);
}

test("Assets distinguishes viewing from owner selection and scopes recovery to the item", async ({ page, baseURL }, testInfo) => {
  const { studio, commands } = await makePolishStudio(page, baseURL!);
  await openLatestKit(page);
  const nav = navigator(page);
  const resize = page.getByRole("separator", { name: "Resize Navigator" });
  await resize.press("Shift+ArrowRight");
  await resize.press("Shift+ArrowRight");
  const kit = nav.locator(".catalogue-item-header").filter({ hasText: "Native Asset Kit / First Lookdev" });
  const board = nav.locator(".catalogue-item-header").filter({ hasText: "Orbital Relay" });
  await expect(kit).toHaveAttribute("title", kitName);
  await expect(board).toHaveAttribute("title", boardName);
  await expect(kit).toContainText("Asset · Viewing v3 (latest) · Owner selected v2");
  await expect(board).toContainText("Storyboard · Latest v4");
  await expect(kit).toBeInViewport({ ratio: 1 });
  await expect(board).toBeInViewport({ ratio: 1 });
  await expect(nav.locator(".catalogue-scroll")).toHaveJSProperty("scrollTop", 0);
  await expect(nav.locator(".catalogue-version-row")).toHaveCount(0);
  await expect(nav.getByRole("button", { name: /Reconcile selection status|Check selection status/ })).toHaveCount(0);
  await expect(nav.locator("footer")).toHaveCount(0);
  await expect(reviewPanel(page).getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  await screenshot(page, testInfo, "01-navigator-assets-collapsed.png");

  await resize.press("Home");
  await expect(resize).toHaveAttribute("aria-valuenow", "220");
  await expect(nav.getByRole("button", { name: "New asset", exact: true })).toBeInViewport();
  await expect(nav.getByRole("searchbox", { name: "Search assets" })).toBeInViewport();
  await resize.press("End");
  await kitHistory(page).click();
  await expect(headerStatus(page)).toHaveText("Viewing v3");
  await expect(nav.locator(".catalogue-version-row code")).toHaveText(["v3", "v2", "v1"]);
  await versionRow(page, 1).click();
  await expect(nav.locator('.catalogue-version-row[aria-pressed="true"]')).toHaveCount(1);
  await expect(versionRow(page, 1)).toHaveAttribute("aria-pressed", "true");
  await expect(versionRow(page, 1)).toContainText("Viewing");
  await expect(versionRow(page, 2)).toContainText("Owner selected");
  await expect(versionRow(page, 2)).toHaveAttribute("aria-pressed", "false");
  await expect(headerStatus(page)).toHaveText("Viewing v1 · v3 available");
  expect(commands).toHaveLength(0);
  const menu = (n: number) => nav.getByRole("button", { name: `Actions for ${kitV(n)}`, exact: true });
  await menu(2).click();
  await expect(dockButton(nav, "Select for item")).toBeDisabled();
  await expect(dockButton(nav, "Withdraw selection")).toBeEnabled();
  await expect(reviewPanel(page).getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  await screenshot(page, testInfo, "02-navigator-assets-expanded-and-menu.png");

  await menu(1).click();
  await dockButton(nav, "Select for item").click();
  const recovery = nav.locator(".catalogue-item-group").filter({ has: page.locator(".catalogue-item-header").filter({ hasText: "Native Asset Kit / First Lookdev" }) }).locator(".catalogue-recovery");
  await expect(recovery).toContainText("Owner selection not confirmed");
  await expect(dockButton(recovery, "Retry original selection")).toBeEnabled();
  await dockButton(recovery, "Check selection status").click();
  await expect(dockButton(recovery, "Retry original selection")).toBeEnabled();
  expect(commands).toHaveLength(1);
  await dockButton(recovery, "Retry original selection").click();
  await expect(recovery).toHaveCount(0);
  expect(commands[1]).toEqual(commands[0]);
  await expect(versionRow(page, 1)).toContainText("Owner selected");
  studio.assertHermetic();
});

test("Keyframes follows rendered ordinals, not missing frames", async ({ page, baseURL }, testInfo) => {
  const { studio } = await makePolishStudio(page, baseURL!);
  await openLatestKit(page);
  const review = reviewPanel(page);
  const transport = review.getByLabel("Review transport", { exact: true });
  const frame = transport.getByRole("spinbutton", { name: "Frame", exact: true });
  const prev = dockButton(transport, "Previous frame");
  const next = dockButton(transport, "Next frame");
  const pass = transport.getByRole("combobox", { name: "Shot / render job" });
  await expect(transport.getByText("Only rendered frame")).toBeVisible();
  await expect(prev).toBeDisabled();
  await expect(next).toBeDisabled();
  await jumpFrame(frame, 2);
  await expect(frame).toHaveValue("1");
  await expect(transport).toContainText(outsidePass);

  await pass.selectOption("in-progress");
  await expect(transport).not.toContainText(outsidePass);
  await expect(transport).toContainText("1 frame ready · rendering…");
  await jumpFrame(frame, 2);
  await expect(transport).toContainText("Frame 2 is rendering. Showing frame 1.");
  await jumpFrame(frame, 13);
  await expect(transport).toContainText("Frame 13 failed to render. Showing frame 1.");
  await pass.selectOption("sparse-lookdev");
  await expect(transport).toContainText("1 of 3 ready");
  await jumpFrame(frame, 2);
  await expect(frame).toHaveValue("1");
  await expect(transport).toContainText("· f1");
  await expect(transport).toContainText(outsidePass);
  await expect(review.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  await screenshot(page, testInfo, "03-keyframes-single-and-sparse-stepping.png");
  await next.click();
  await expect(frame).toHaveValue("12");
  await expect(transport).toContainText("· f12");
  await expect(review.locator(".review-image img")).toHaveAttribute("src", /\/sparse-lookdev-f12\/content$/);
  await expect(transport).not.toContainText(outsidePass);
  await page.locator(".brand").click();
  await page.keyboard.press("ArrowRight");
  await expect(frame).toHaveValue("24");
  await expect(next).toBeDisabled();
  await prev.click();
  await expect(frame).toHaveValue("12");
  studio.assertHermetic();
});

test("Annotations shows current notes by default and opens historical notes on their original version", async ({ page, baseURL }, testInfo) => {
  const { studio } = await makePolishStudio(page, baseURL!);
  const review = reviewPanel(page);
  await navigator(page).locator(".catalogue-item-header").filter({ hasText: "Orbital Relay" }).click();
  await dockButton(review, "Annotate presented frame").click();
  await review.getByRole("textbox", { name: "Feedback" }).fill("Board only: shift the introduction earlier");
  await dockButton(review.locator(".annotation-dock"), "Keep draft & close").click();
  await expect(review.getByRole("button", { name: /^Annotations/ }).locator(".toolbar-count")).toHaveText("2");
  await openLatestKit(page);
  const annotations = review.getByRole("button", { name: /^Annotations/ });
  const drawer = review.getByRole("region", { name: "Saved annotations and local drafts" });
  await expect(annotations.locator(".toolbar-count")).toHaveText("1");
  await annotations.click();
  await expect(dockButton(drawer, "This version · v3")).toHaveAttribute("aria-pressed", "true");
  await expect(drawer.locator(".review-annotation-row strong")).toHaveText(["Keep the housing silhouette readable"]);
  await dockButton(drawer, "All versions of this item (2)").click();
  const list = drawer.locator(".review-annotation-row");
  await expect(list.locator("strong")).toHaveText(["Keep the housing silhouette readable", "Break the specular line at the collar seam"]);
  await expect(list.locator(".review-annotation-meta")).toHaveCount(2);
  await expect(drawer).not.toContainText(/Board only|[a-f0-9]{8}-[a-f0-9]{4}-/i);
  const history = list.filter({ hasText: "Break the specular line" });
  await expect(history).toContainText("Open on v2");
  await expect(history).toContainText("Saved · rev 1");
  await screenshot(page, testInfo, "04-annotations-drawer-scoped-and-history.png");
  await history.click();
  await expect(headerStatus(page)).toHaveText("Viewing v2 · v3 available");
  await expect(review.getByRole("textbox", { name: "Feedback" })).toHaveValue("Break the specular line at the collar seam");
  studio.assertHermetic();
});

test("2D bottom dock discards a new draft and restores acknowledged edits without writes to the server", async ({ page, baseURL }, testInfo) => {
  const { studio } = await makePolishStudio(page, baseURL!);
  await openLatestKit(page);
  const review = reviewPanel(page);
  const annotate = dockButton(review, "Annotate presented frame");
  await expect(annotate).toBeEnabled();
  await jumpFrame(review.getByRole("spinbutton", { name: "Frame" }), 2);
  await expect(review).toContainText(outsidePass);
  await annotate.click();
  const dock = await checkDock(review);
  const feedback = review.getByRole("textbox", { name: "Feedback" });
  await feedback.fill("Bring the outer collar edge out of shadow");
  const surface = (await review.locator(".review-canvas").boundingBox())!;
  await page.mouse.move(surface.x + surface.width * 0.50, surface.y + surface.height * 0.47);
  await page.mouse.down();
  await page.mouse.move(surface.x + surface.width * 0.57, surface.y + surface.height * 0.51, { steps: 5 });
  await page.mouse.up();
  await expect(review.locator(".review-status-banner")).toHaveCount(0);
  await expect(review).not.toContainText(outsidePass);
  await dockButton(dock, "Discard draft").click();
  await expect(dockButton(dock, "Confirm discard")).toBeVisible();
  await expect(dockButton(dock, "Keep editing")).toBeVisible();
  expect((await dock.boundingBox())!.height).toBeLessThanOrEqual(120);
  await screenshot(page, testInfo, "05-annotation-2d-bottom-actions-and-discard.png");
  await dockButton(dock, "Keep editing").click();
  await expect(feedback).toHaveValue("Bring the outer collar edge out of shadow");
  await dockButton(dock, "Discard draft").click();
  await dockButton(dock, "Confirm discard").click();
  await expect(dock).toHaveCount(0);
  const annotations = review.getByRole("button", { name: /^Annotations/ });
  await expect(annotations.locator(".toolbar-count")).toHaveText("1");
  await annotations.click();
  await review.locator(".review-annotation-row").filter({ hasText: "Keep the housing silhouette readable" }).click();
  await feedback.fill("Temporary edit to a saved note");
  await dockButton(dock, "Discard edits").click();
  await dockButton(dock, "Confirm discard").click();
  await expect(feedback).toHaveValue("Keep the housing silhouette readable");
  await expect(dockButton(dock, "Close")).toBeVisible();
  expect(studio.saveAnnotationCalls).toEqual([]);
  expect(studio.uploadCalls).toEqual([]);
  expect(studio.submissions).toEqual([]);
  studio.assertHermetic();
});

test("3D pin tools stay above the proxy and discarding removes the local pin", async ({ page, baseURL }, testInfo) => {
  const { studio } = await makePolishStudio(page, baseURL!);
  await openLatestKit(page);
  const review = reviewPanel(page);
  await dockButton(review, "3D Inspection").click();
  await expect(review.getByTestId("proxy-frame-gate")).toBeVisible();
  await pinCollar(page, review);
  const tools = review.getByLabel("Spatial pin tools");
  await expect(tools).toContainText("collar · collar-1");
  await expect(tools).toContainText("Draft on this browser");
  for (const name of ["Restore pinned view", "Reposition pin", "Edit 2D markup"]) await expect(dockButton(tools, name)).toBeVisible();
  const dock = await checkDock(review);
  await expect(review.getByTestId("proxy-frame-gate")).toHaveCount(0);
  await review.getByRole("textbox", { name: "Feedback" }).fill("Chamfer the collar lip without moving the seam");
  await screenshot(page, testInfo, "06-spatial-pin-3d-bottom-actions.png");
  await dockButton(dock, "Discard draft").click();
  await dockButton(dock, "Confirm discard").click();
  await expect(dock).toHaveCount(0);
  await expect(review.getByTestId("proxy-frame-gate")).toBeVisible();
  await expect(review.getByRole("button", { name: /^Annotations/ }).locator(".toolbar-count")).toHaveText("1");
  expect(studio.saveAnnotationCalls).toEqual([]);
  expect(studio.uploadCalls).toEqual([]);
  studio.assertHermetic();
});

test("3D render presets preserve PNG dimensions and camera aspect across splitters and DPR", async ({ page, baseURL }, testInfo) => {
  const { studio } = await makePolishStudio(page, baseURL!);
  await openLatestKit(page);
  const review = reviewPanel(page);
  const dpr = testInfo.project.name === "chromium-dpr2" ? 2 : 1;
  expect(await page.evaluate(() => devicePixelRatio)).toBe(dpr);
  await dockButton(review, "3D Inspection").click();
  const output = review.getByRole("combobox", { name: "Render output format" });
  const gate = review.getByTestId("proxy-frame-gate");
  const canvas = review.locator(".review-proxy canvas");
  const render = dockButton(review, "Render this view");
  await expect(output).toHaveValue("16:9");
  await expect(output.locator("option:checked")).toHaveText("16:9 · 1920 × 1080");
  await expect(render).toBeEnabled();

  const left = page.getByRole("separator", { name: "Resize Navigator" });
  const right = page.getByRole("separator", { name: "Resize Assistant" });
  const wide = (await canvas.boundingBox())!.width;
  await left.press("End");
  await right.press("End");
  await expect.poll(async () => (await canvas.boundingBox())!.width).toBeLessThan(wide - 300);
  await expect.poll(async () => {
    const [viewport, frame] = await Promise.all([canvas.boundingBox(), gate.boundingBox()]);
    return Boolean(viewport && frame && viewport.height - frame.height > 100 && Math.abs(frame.width / frame.height - 16 / 9) < 0.001 && Math.abs(frame.y + frame.height / 2 - (viewport.y + viewport.height / 2)) < 1);
  }).toBe(true);
  await expect(gate).toHaveCSS("pointer-events", "none");
  await screenshot(page, testInfo, "07-3d-inspection-16x9-gate-narrow-split.png");

  async function capture(width: number, height: number) {
    const index = studio.submissions.length;
    await render.click();
    await expect.poll(() => studio.submissions.length).toBe(index + 1);
    expect(studio.submissions[index]).toMatchObject({ text: "Render this view" });
    const context = studio.contexts[index];
    const artifact = studio.uploads.get(studio.uploadCalls[index])!;
    expect(artifact).toMatchObject({ media_type: "image/png", dimensions: { width, height } });
    const bytes = studio.artifactContent.get(artifact.artifact_id)!.bytes;
    expect(bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    expect([bytes.readUInt32BE(16), bytes.readUInt32BE(20)]).toEqual([width, height]);
    expect(context.target).toMatchObject({ artifact_id: artifact.artifact_id, dimensions: { width, height }, version_id: kitV(3) });
    expect(Math.abs(context.camera!.aspect - width / height)).toBeLessThan(1e-6);
    await expect(render).toBeEnabled();
  }
  await capture(1920, 1080);
  await output.selectOption("16:10");
  await expect(gate).toHaveAttribute("data-preset", "16:10");
  await expect(output.locator("option:checked")).toHaveText("16:10 · 1920 × 1200");
  await capture(1920, 1200);
  const narrowWidth = (await canvas.boundingBox())!.width;
  await left.press("Home");
  await expect(left).toHaveAttribute("aria-valuenow", "220");
  await expect.poll(async () => (await canvas.boundingBox())!.width).toBeGreaterThan(narrowWidth + 250);
  await output.selectOption("viewport");
  await expect(gate).toHaveCount(0);
  const pixels = () => canvas.evaluate((element: HTMLCanvasElement) => ({ width: element.width, height: element.height, cssWidth: element.closest(".review-proxy")!.clientWidth, cssHeight: element.closest(".review-proxy")!.clientHeight }));
  await expect.poll(async () => {
    const { width, height, cssWidth, cssHeight } = await pixels();
    return Math.abs(width - cssWidth * dpr) < 2 && Math.abs(height - cssHeight * dpr) < 2;
  }).toBe(true);
  const { width, height } = await pixels();
  await expect(output.locator("option:checked")).toHaveText(`Match viewport · ${width} × ${height}`);
  await capture(width, height);
  studio.assertHermetic();
});

test("Publication announces a newer version and switching preserves the old local draft", async ({ page, baseURL }, testInfo) => {
  const { studio, publishNextVersion } = await makePolishStudio(page, baseURL!);
  await openLatestKit(page);
  const review = reviewPanel(page);
  await dockButton(review, "Annotate presented frame").click();
  const feedback = review.getByRole("textbox", { name: "Feedback" });
  await feedback.fill("Keep this roughness adjustment on v3");
  publishNextVersion();
  await expect(headerStatus(page)).toHaveText("Viewing v3 · v4 available");
  await expect(feedback).toHaveValue("Keep this roughness adjustment on v3");
  await expect(review).not.toContainText("New publications do not move this view");
  await screenshot(page, testInfo, "08-header-newer-version-available.png");
  await dockButton(review, "View v4").click();
  await expect(headerStatus(page)).toHaveText("Viewing v4");
  await expect(review.getByText("Draft kept on v3")).toBeVisible();
  await expect(feedback).toHaveCount(0);
  await kitHistory(page).click();
  await versionRow(page, 3).click();
  await review.getByRole("button", { name: /^Annotations/ }).click();
  await review.locator(".review-annotation-row").filter({ hasText: "Keep this roughness adjustment on v3" }).click();
  await expect(feedback).toHaveValue("Keep this roughness adjustment on v3");
  expect(studio.saveAnnotationCalls).toEqual([]);
  expect(studio.submissions).toEqual([]);
  expect(studio.contexts).toEqual([]);
  studio.assertHermetic();
});
