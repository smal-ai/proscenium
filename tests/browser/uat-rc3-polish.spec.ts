import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import type { Camera, JobRecord } from "../../apps/frontend/src/api";
import { projectBlenderPoint } from "../../apps/frontend/src/features/review/media";
import type { Draft } from "../../apps/frontend/src/features/review/outbox";
import { boardName, kitId, kitName, kitV, makePolishStudio, restoredRenderCamera } from "./rc3-polish-fixture";
import { base, deferred, job, projectId, request, StudioFixture } from "./studio-fixture";

const screenshotDir = process.env.PROSCENIUM_UAT_SCREENSHOT_DIR;
const reviewPanel = (page: Page) => page.locator("main.review-panel");
const navigator = (page: Page) => page.getByRole("complementary", { name: "Navigator", exact: true });
const kitHistory = (page: Page) => navigator(page).getByRole("button", { name: `Version history for ${kitName}`, exact: true });
const versionRow = (page: Page, ordinal: number) => navigator(page).locator(".catalogue-version-row").filter({ has: page.getByText(`v${ordinal}`, { exact: true }) });
const headerStatus = (page: Page) => reviewPanel(page).locator(".version-status-pill");
const dockButton = (dock: Locator, name: string) => dock.getByRole("button", { name, exact: true });
const outsidePass = "Frame 2 is outside this pass. Showing frame 1.";
const feedbackBox = (review: Locator) => review.getByRole("textbox", { name: "Feedback" });
const draftDock = (review: Locator) => review.locator(".annotation-dock");
const annotationDrawer = (review: Locator) => review.getByRole("button", { name: /^Annotations/ });

async function screenshot(page: Page, testInfo: TestInfo, file: string) {
  // Both projects run the assertions. DPR 1 owns the single review pack.
  if (!screenshotDir || testInfo.project.name !== "chromium-dpr1") return;
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

async function checkSameRow(...controls: Locator[]) {
  await Promise.all(controls.map(control => expect(control).toBeVisible()));
  const y = await Promise.all(controls.map(async control => (await control.boundingBox())!.y));
  expect(Math.max(...y) - Math.min(...y)).toBeLessThanOrEqual(4);
}

async function checkDock(review: Locator, confirming = false) {
  await expect(review.locator(".review-bar:not(.review-transport-bar)").getByRole("button", { name: /Close draft|Keep draft & close/ })).toHaveCount(0);
  const dock = draftDock(review);
  const actions = confirming ? ["Confirm discard", "Keep editing"] : ["Discard draft", "Save annotation", "Queue feedback"];
  await checkSameRow(...["Keep draft & close", ...actions].map(name => dockButton(dock, name)));
  await expect(dock.locator(".annotation-dock-target .toolbar-chip")).toHaveText("Pinned v3 · frame 1");
  expect((await dock.boundingBox())!.height).toBeLessThanOrEqual(120);
  return dock;
}

const liveCamera = (page: Page) => page.evaluate(id => {
    const raw = localStorage.getItem(`proscenium.owner.view.context.${id}`);
    return raw ? JSON.parse(raw).camera as Camera | null : null;
}, projectId);

async function pinCollar(page: Page, review: Locator, action = "Pin note") {
  const button = dockButton(review, action);
  await expect(button).toBeEnabled();
  await button.click();
  await expect.poll(() => liveCamera(page)).not.toBeNull();
  const [u, v] = projectBlenderPoint((await liveCamera(page))!, [-1.6, -1.6, 0.2]);
  const box = (await review.locator(".review-proxy canvas").boundingBox())!;
  await page.mouse.click(box.x + u * box.width, box.y + v * box.height);
}

async function drawStroke(page: Page, review: Locator, start = 0.47) {
  const surface = (await review.locator(".review-canvas").boundingBox())!;
  await page.mouse.move(surface.x + surface.width * 0.50, surface.y + surface.height * start);
  await page.mouse.down();
  await page.mouse.move(surface.x + surface.width * 0.57, surface.y + surface.height * (start + 0.04), { steps: 5 });
  await page.mouse.up();
}

async function orbit(page: Page, canvas: Locator) {
  const box = (await canvas.boundingBox())!;
  const x = box.x + box.width * 0.45;
  const y = box.y + box.height * 0.5;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - 90, y + 35, { steps: 8 });
  await page.mouse.up();
}

async function browserDrafts(page: Page, advanceKey?: string): Promise<Draft[]> {
  return page.evaluate(key => new Promise<Draft[]>((resolve, reject) => {
    const open = indexedDB.open("proscenium-review-v1", 1);
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const db = open.result;
      const tx = db.transaction("drafts", key ? "readwrite" : "readonly");
      const store = tx.objectStore("drafts");
      const read = store.getAll();
      read.onsuccess = () => {
        const draft = (read.result as Draft[]).find(d => d.key === key);
        if (draft) {
          draft.state = "saving";
          store.put(draft);
        }
      };
      tx.oncomplete = () => { db.close(); resolve(read.result); };
      tx.onabort = () => { db.close(); reject(tx.error); };
    };
  }), advanceKey);
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
  await kitHistory(page).click();
  const menu = (n: number) => nav.getByRole("button", { name: `Actions for ${kitV(n)}`, exact: true });
  const details = dockButton(nav, `Details for ${kitName}`);
  await details.click();
  await expect(nav.locator(".catalogue-details code")).toHaveText(kitId);
  await expect(nav.locator(".catalogue-details")).toHaveCount(1);
  await menu(2).click();
  await expect(details).toHaveAttribute("aria-expanded", "false");
  await expect(nav.locator(".catalogue-details code")).toHaveText(kitV(2));
  await expect(nav.locator(".catalogue-details")).toHaveCount(1);
  await dockButton(nav, "Withdraw selection").press("Escape");
  await expect(menu(2)).toBeFocused();
  await expect(nav.locator(".catalogue-details")).toHaveCount(0);
  await menu(2).click();
  for (const selector of [".catalogue-scroll", ".review-catalogue"]) {
    expect(await nav.locator(selector).evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  }
  await screenshot(page, testInfo, "01b-navigator-assets-220px.png");
  const toolbar = nav.locator(".catalogue-toolbar");
  const toolbarY = (await toolbar.boundingBox())!.y;
  const scroll = nav.locator(".catalogue-scroll");
  const actualScroll = await scroll.evaluate(el => {
    el.scrollTop = el.scrollHeight;
    return el.scrollTop;
  });
  expect(actualScroll).toBeGreaterThan(0);
  expect((await toolbar.boundingBox())!.y).toBe(toolbarY);
  await scroll.evaluate(el => { el.scrollTop = 0; });
  await menu(2).press("Escape");
  await resize.press("End");
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
  await screenshot(page, testInfo, "03a-keyframes-single-frame-pass.png");
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
  await expect(transport).not.toContainText("rendering…");
  await jumpFrame(frame, 2);
  await expect(frame).toHaveValue("1");
  await expect(transport).toContainText("· f1");
  const notice = transport.getByText(outsidePass, {exact: true});
  await expect(notice).toBeVisible();
  await expect(transport.getByText(/Blender render/)).toBeInViewport({ratio: 1});
  expect(await notice.evaluate(el => {
    const box = el.getBoundingClientRect();
    return el.scrollWidth <= el.clientWidth && box.right <= el.closest(".review-transport-bar")!.getBoundingClientRect().right;
  })).toBe(true);
  await expect(review.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  await screenshot(page, testInfo, "03b-keyframes-sparse-pass-and-notice.png");
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
  await screenshot(page, testInfo, "04a-annotations-drawer-this-version.png");
  await dockButton(drawer, "All versions of this item (2)").click();
  const list = drawer.locator(".review-annotation-row");
  await expect(list.locator("strong")).toHaveText(["Keep the housing silhouette readable", "Break the specular line at the collar seam"]);
  await expect(list.locator(".review-annotation-meta")).toHaveCount(2);
  await expect(drawer).not.toContainText(/Board only|[a-f0-9]{8}-[a-f0-9]{4}-/i);
  const history = list.filter({ hasText: "Break the specular line" });
  await expect(history).toContainText("Open on v2");
  await expect(history).toContainText("Saved · rev 1");
  await screenshot(page, testInfo, "04b-annotations-drawer-all-versions.png");
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
  const feedback = feedbackBox(review);
  const dock = draftDock(review);
  const confirm = dockButton(dock, "Confirm discard");
  await expect(annotate).toBeEnabled();
  await annotate.click();
  const [untouched] = await browserDrafts(page);
  expect(untouched.annotation).toMatchObject({text: "", markups: [], saved_revision: null});
  await dockButton(dock, "Discard draft").click();
  await expect(dock).toHaveCount(0);
  await expect(confirm).toHaveCount(0);
  expect((await browserDrafts(page)).filter(d => d.key === untouched.key)).toHaveLength(0);
  await page.reload();
  await expect(headerStatus(page)).toHaveText("Viewing v3");
  expect((await browserDrafts(page)).filter(d => d.key === untouched.key)).toHaveLength(0);
  await expect(annotate).toBeEnabled();
  await jumpFrame(review.getByRole("spinbutton", { name: "Frame" }), 2);
  await expect(review).toContainText(outsidePass);
  await annotate.click();
  await checkDock(review);
  await feedback.fill("Bring the outer collar edge out of shadow");
  await drawStroke(page, review);
  await expect(review.locator(".review-canvas polyline")).toHaveCount(1);
  await expect(review.locator(".review-status-banner")).toHaveCount(0);
  await expect(review).not.toContainText(outsidePass);
  await screenshot(page, testInfo, "05a-annotation-2d-bottom-actions.png");
  await dockButton(dock, "Discard draft").click();
  await checkDock(review, true);
  await screenshot(page, testInfo, "05b-annotation-2d-confirm-discard.png");
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
  await checkDock(review, true);
  await dockButton(dock, "Confirm discard").click();
  await expect(dock).toHaveCount(0);
  await expect(review.getByTestId("proxy-frame-gate")).toBeVisible();
  await expect(review.getByRole("button", { name: /^Annotations/ }).locator(".toolbar-count")).toHaveText("1");
  expect(studio.saveAnnotationCalls).toEqual([]);
  expect(studio.uploadCalls).toEqual([]);
  studio.assertHermetic();
});

test("another tab's in-flight save survives a stale discard confirmation", async ({ page, baseURL }) => {
  const { studio } = await makePolishStudio(page, baseURL!);
  await openLatestKit(page);
  const review = reviewPanel(page);
  const dock = draftDock(review);
  const feedback = feedbackBox(review);
  await dockButton(review, "Annotate presented frame").click();
  await feedback.fill("This local note is being saved by another tab");
  await dockButton(dock, "Discard draft").click();
  const confirm = dockButton(dock, "Confirm discard");
  await expect(confirm).toBeVisible();
  await expect.poll(async () => (await browserDrafts(page))[0]?.annotation.text).toBe(await feedback.inputValue());
  const [before] = await browserDrafts(page);
  await browserDrafts(page, before.key);
  await confirm.click();
  await expect(review.getByRole("alert").filter({hasText: "This draft now has a save operation in progress or pending retry and cannot be discarded."})).toBeVisible();
  await expect(review.getByText("Saving…", {exact: true})).toBeVisible();
  await expect(feedback).toBeDisabled();
  expect(await browserDrafts(page)).toEqual([{...before, state: "saving"}]);
  expect(studio.saveAnnotationCalls).toEqual([]);
  studio.assertHermetic();
});

test("Discard edits restores the saved spatial pin, markup and camera without losing the outbox", async ({ page, baseURL }) => {
  const { studio } = await makePolishStudio(page, baseURL!);
  await openLatestKit(page);
  const review = reviewPanel(page);
  const dock = draftDock(review);
  const feedback = feedbackBox(review);
  const tools = review.getByLabel("Spatial pin tools");
  const marker = review.getByTestId("proxy-spatial-pin");
  const canvas = review.locator(".review-proxy canvas");
  const original = "Keep the collar seam exactly here";
  await dockButton(review, "3D Inspection").click();
  await pinCollar(page, review);
  await feedback.fill(original);
  await dockButton(tools, "Edit 2D markup").click();
  await drawStroke(page, review);
  const strokes = review.locator(".review-canvas polyline");
  await expect(strokes).toHaveCount(1);
  const originalStroke = await strokes.getAttribute("points");
  await dockButton(review, "Back to 3D view").click();
  await dockButton(dock, "Save annotation").click();
  await expect(tools).toContainText("Saved · rev 1");
  const [saved] = await browserDrafts(page);
  expect(saved.annotation).toMatchObject({text: original, saved_revision: 1});
  expect(saved.annotation.markups.map(markup => markup.kind)).toEqual(["pin", "stroke"]);
  expect(saved.annotation.camera).not.toBeNull();
  const expectedClean: Draft = {key: saved.key, projectId, state: "saved", annotation: saved.annotation};
  const keptDraft = async (text: string, marks: number) => {
    await expect.poll(async () => (await browserDrafts(page))[0]).toMatchObject({
      key: saved.key, state: "browser-local", annotation: {text, saved_revision: 1, markups: {length: marks}},
    });
  };

  await feedback.fill("Temporary collar edit");
  await dockButton(tools, "Edit 2D markup").click();
  await drawStroke(page, review, 0.63);
  await expect(strokes).toHaveCount(2);
  await dockButton(review, "Back to 3D view").click();
  await expect(tools).toContainText("Draft on this browser");
  await keptDraft("Temporary collar edit", 3);
  await expect(marker).toBeVisible();
  const markerPosition = async () => {
    const box = await marker.boundingBox();
    return box ? [box.x + box.width / 2, box.y + box.height / 2] : null;
  };
  const pinnedPosition = (await markerPosition())!;
  await orbit(page, canvas);
  await expect.poll(async () => {
    const position = await markerPosition();
    return position ? Math.hypot(position[0] - pinnedPosition[0], position[1] - pinnedPosition[1]) : 0;
  }).toBeGreaterThan(15);
  await dockButton(dock, "Discard edits").click();
  await expect(dock).toContainText("restore revision 1");
  await dockButton(dock, "Confirm discard").click();
  await expect(tools).toContainText("Saved · rev 1");
  await expect(feedback).toHaveValue(original);
  const [u, v] = projectBlenderPoint(saved.annotation.camera!, saved.annotation.spatial_hits[0].position);
  await expect.poll(async () => {
    const [current, fitted] = await Promise.all([markerPosition(), canvas.boundingBox()]);
    return current && fitted ? Math.hypot(current[0] - (fitted.x + u * fitted.width), current[1] - (fitted.y + v * fitted.height)) : Infinity;
  }).toBeLessThanOrEqual(0.5);
  expect(await browserDrafts(page)).toEqual([expectedClean]);
  await dockButton(tools, "Edit 2D markup").click();
  await expect(strokes).toHaveCount(1);
  await expect(strokes).toHaveAttribute("points", originalStroke!);
  await dockButton(dock, "Close").click();
  await expect(dock).toHaveCount(0);

  const reopenPin = async () => {
    await annotationDrawer(review).click();
    await review.locator(".review-annotation-row").filter({hasText: /Keep the collar seam exactly here|Re-edit from the acknowledged revision/}).click();
  };
  await page.reload();
  await expect(headerStatus(page)).toHaveText("Viewing v3");
  expect(await browserDrafts(page)).toEqual([expectedClean]);
  await reopenPin();
  await expect(tools).toContainText("Saved · rev 1");
  await expect(feedback).toHaveValue(original);
  await feedback.fill("Re-edit from the acknowledged revision");
  await keptDraft("Re-edit from the acknowledged revision", 2);
  await dockButton(dock, "Keep draft & close").click();
  await expect(dock).toHaveCount(0);

  let annotationsOffline = true;
  let baselineReads = 0;
  await page.route(url => url.pathname === `${base}/annotations`, route => {
    if (route.request().method() === "GET") {
      if (new URL(route.request().url()).searchParams.get("version_id") === kitV(3)) baselineReads++;
      if (annotationsOffline) return route.abort("failed");
    }
    return route.fallback();
  });
  await page.reload();
  await expect(headerStatus(page)).toHaveText("Viewing v3");
  await reopenPin();
  await keptDraft("Re-edit from the acknowledged revision", 2);
  const [dirty] = await browserDrafts(page);
  await dockButton(dock, "Discard edits").click();
  await expect(review.getByText("Saved annotation baseline is unavailable; reload annotations before discarding edits.", {exact: true})).toBeVisible();
  await expect(dockButton(dock, "Confirm discard")).toHaveCount(0);
  await expect(feedback).toHaveValue(dirty.annotation.text);
  expect(await browserDrafts(page)).toEqual([dirty]);
  expect(baselineReads).toBe(1);
  annotationsOffline = false;
  await dockButton(dock, "Discard edits").click();
  await expect(dock).toContainText("restore revision 1");
  await dockButton(dock, "Confirm discard").click();
  await expect(feedback).toHaveValue(original);
  const serverSnapshot = studio.annotations.find(a => a.annotation_id === saved.key)!;
  expect(await browserDrafts(page)).toEqual([{...expectedClean, annotation: serverSnapshot}]);
  expect(baselineReads).toBe(2);
  expect(studio.saveAnnotationCalls).toHaveLength(1);
  studio.assertHermetic();
});

test("3D render presets restore the next live frame and retry the same frozen capture", async ({ page, baseURL }, testInfo) => {
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
    const [host, viewport, frame] = await Promise.all([review.locator(".review-proxy").boundingBox(), canvas.boundingBox(), gate.boundingBox()]);
    if (!host || !viewport || !frame) return false;
    return Math.abs(viewport.width / viewport.height - restoredRenderCamera.aspect) < 0.004
      && viewport.y > host.y + 10 && viewport.height - frame.height > 60
      && Math.abs(frame.width / frame.height - 16 / 9) < 0.001
      && Math.abs(frame.x - viewport.x) < 1 && Math.abs(frame.width - viewport.width) < 1
      && Math.abs(frame.y - viewport.y - (viewport.height - frame.height) / 2) < 1;
  }).toBe(true);
  await expect.poll(async () => (await liveCamera(page))?.aspect).toBeCloseTo(restoredRenderCamera.aspect, 2);
  await expect(gate).toHaveCSS("pointer-events", "none");
  await checkSameRow(dockButton(review, "Pin note"), output, render);
  await screenshot(page, testInfo, "07-3d-inspection-16x9-gate-narrow-split.png");

  async function checkCapture(width: number, height: number, trigger = render) {
    const index = studio.submissions.length;
    await trigger.click();
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
    return context.camera;
  }

  const attempts: Array<{path: string; key: string; bytes: Buffer}> = [];
  await page.route(url => url.pathname.startsWith(`${base}/uploads/`), route => {
    const request = route.request();
    attempts.push({path: new URL(request.url()).pathname, key: request.headers()["idempotency-key"], bytes: request.postDataBuffer()!});
    return attempts.length === 1 ? route.abort("failed") : route.fallback();
  });
  // Sample the actual drawing buffer and viewport in the first rAF following
  // the click, not after an upload round trip or a splitter-induced resize.
  await canvas.evaluate((el: HTMLCanvasElement, id) => {
    const gl = el.getContext("webgl2")!;
    function sample() {
      const {x, y, width: cssW, height: cssH} = el.getBoundingClientRect();
      const camera = JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)!).camera as Camera;
      return {box: {x, y, cssW, cssH}, intrinsic: [el.width, el.height],
        buffer: [gl.drawingBufferWidth, gl.drawingBufferHeight], viewport: Array.from(gl.getParameter(gl.VIEWPORT) as Int32Array), camera};
    }
    document.addEventListener("click", () => {
      const before = sample();
      requestAnimationFrame(() => { el.dataset.captureFrameProof = JSON.stringify({before, after: sample()}); });
    }, {capture: true, once: true});
  }, projectId);
  await render.click();
  await expect(canvas).toHaveAttribute("data-capture-frame-proof", /"after"/);
  type Sample = {box: {x: number; y: number; cssW: number; cssH: number}; intrinsic: number[]; buffer: number[]; viewport: number[]; camera: Camera};
  const {before, after} = JSON.parse((await canvas.getAttribute("data-capture-frame-proof"))!) as {before: Sample; after: Sample};
  expect(before.intrinsic).toEqual([Math.round(before.box.cssW * dpr), Math.round(before.box.cssH * dpr)]);
  expect(before.intrinsic[0]).not.toBe(1920);
  expect(before.buffer).toEqual(before.intrinsic);
  expect(before.viewport).toEqual([0, 0, ...before.intrinsic]);
  expect({...after, camera: undefined}).toEqual({...before, camera: undefined});
  const projectedMarker = ({box, camera}: Sample) => {
    const [u, v] = projectBlenderPoint(camera, [-1.6, -1.6, 0.2]);
    return [box.x + u * box.cssW, box.y + v * box.cssH];
  };
  const [beforeX, beforeY] = projectedMarker(before);
  const [afterX, afterY] = projectedMarker(after);
  expect(Math.hypot(afterX - beforeX, afterY - beforeY)).toBeLessThanOrEqual(0.5);

  const retry = dockButton(review, "Retry captured view upload (1920 × 1080)");
  await expect(output).toBeDisabled();
  await expect(output.locator("option:checked")).toHaveText("1920 × 1080 retained");
  await expect(retry).toBeEnabled();
  expect(attempts).toHaveLength(1);
  expect(studio.submissions).toEqual([]);
  const frozen = await page.evaluate(key => new Promise<{selection: {camera: Camera}; dimensions: {width: number; height: number}; uploadId: string}>((resolve, reject) => {
    const open = indexedDB.open("proscenium-proxy-captures-v1", 1);
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const db = open.result;
      const tx = db.transaction("captures", "readonly");
      const read = tx.objectStore("captures").get(key);
      tx.oncomplete = () => { db.close(); resolve(read.result); };
      tx.onabort = () => { db.close(); reject(tx.error); };
    };
  }), `${projectId}:${kitV(3)}`);
  expect(frozen.dimensions).toEqual({width: 1920, height: 1080});
  // Change the live orbit before retry to distinguish replay from recapture.
  await orbit(page, canvas);
  await expect.poll(async () => {
    const orbitCamera = await liveCamera(page);
    return orbitCamera ? Math.hypot(...[3, 7, 11].map(i => orbitCamera.camera_to_world[i] - before.camera.camera_to_world[i])) : 0;
  }).toBeGreaterThan(0.1);
  // Compare the wire payload, where JSON serialises signed zero as zero.
  expect(JSON.stringify(await checkCapture(1920, 1080, retry))).toBe(JSON.stringify(frozen.selection.camera));
  expect(attempts).toHaveLength(2);
  expect(attempts[1].path).toBe(attempts[0].path);
  expect(attempts[1].path).toBe(`${base}/uploads/${frozen.uploadId}`);
  expect(attempts[1].key).toBe(attempts[0].key);
  expect(attempts[1].key).toBe(frozen.uploadId);
  expect(Buffer.compare(attempts[1].bytes, attempts[0].bytes)).toBe(0);
  for (const {bytes} of attempts) expect([bytes.readUInt32BE(16), bytes.readUInt32BE(20)]).toEqual([1920, 1080]);

  await output.selectOption("16:10");
  await expect(gate).toHaveAttribute("data-preset", "16:10");
  await expect(output.locator("option:checked")).toHaveText("16:10 · 1920 × 1200");
  await checkCapture(1920, 1200);
  const narrowWidth = (await canvas.boundingBox())!.width;
  await left.press("Home");
  await expect(left).toHaveAttribute("aria-valuenow", "220");
  await expect.poll(async () => (await canvas.boundingBox())!.width).toBeGreaterThan(narrowWidth + 250);
  await output.selectOption("viewport");
  await expect(gate).toHaveCount(0);
  const pixels = () => canvas.evaluate((element: HTMLCanvasElement) => ({ width: element.width, height: element.height, cssWidth: element.getBoundingClientRect().width, cssHeight: element.getBoundingClientRect().height }));
  await expect.poll(async () => {
    const { width, height, cssWidth, cssHeight } = await pixels();
    return Math.abs(width - cssWidth * dpr) < 2 && Math.abs(height - cssHeight * dpr) < 2;
  }).toBe(true);
  const { width, height } = await pixels();
  await expect(output.locator("option:checked")).toHaveText(`Match viewport · ${width} × ${height}`);
  await checkCapture(width, height);
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
  await screenshot(page, testInfo, "08a-header-newer-version-available.png");
  const viewLatest = dockButton(review, "View v4");

  // Fail only reads of the review outbox. The version must not switch when
  // draft retention cannot be verified.
  await page.evaluate(() => {
    const original = Object.getOwnPropertyDescriptor(IDBDatabase.prototype, "transaction")!;
    IDBDatabase.prototype.transaction = function (...args: Parameters<IDBDatabase["transaction"]>) {
      if (this.name === "proscenium-review-v1" && args[0] === "drafts" && args[1] === "readonly") {
        Object.defineProperty(IDBDatabase.prototype, "transaction", original);
        throw new DOMException("Injected outbox read failure", "InvalidStateError");
      }
      return original.value.apply(this, args);
    };
  });
  await viewLatest.click();
  await expect(review.getByRole("alert").filter({hasText: "Could not verify browser drafts before switching versions"})).toBeVisible();
  await expect(headerStatus(page)).toHaveText("Viewing v3 · v4 available");
  await expect(feedback).toHaveValue("Keep this roughness adjustment on v3");

  const versionsPath = `${base}/items/${kitId}/versions`;
  await page.evaluate(path => {
    const original = window.fetch;
    window.fetch = function (input, options) {
      const requestURL = input instanceof Request ? input.url : String(input);
      const signal = options?.signal ?? (input instanceof Request ? input.signal : null);
      if (signal && new URL(requestURL, location.origin).pathname === path) {
        signal.addEventListener("abort", () => { document.body.dataset.latestRequestAborted = "true"; }, {once: true});
      }
      return original.call(window, input, options);
    };
  }, versionsPath);
  const held = deferred();
  const entered = deferred();
  const settled = deferred();
  await page.route(url => url.pathname === versionsPath, async route => {
    entered.release();
    await held.promise;
    try { await route.fallback(); } finally { settled.release(); }
  }, {times: 1});
  try {
    await viewLatest.click();
    await entered.promise;
    await expect(viewLatest).toBeDisabled();
    await navigator(page).locator(".catalogue-item-header").filter({hasText: "Orbital Relay"}).click();
    await expect(review.getByRole("heading", {name: boardName})).toBeVisible();
    await expect(page.locator("body")).toHaveAttribute("data-latest-request-aborted", "true");
  } finally { held.release(); }
  await settled.promise;
  await expect(review.getByRole("heading", {name: boardName})).toBeVisible();
  await expect(review.getByText("Draft kept on v3")).toHaveCount(0);

  await kitHistory(page).click();
  await versionRow(page, 3).click();
  await expect(headerStatus(page)).toHaveText("Viewing v3 · v4 available");
  await viewLatest.click();
  await expect(headerStatus(page)).toHaveText("Viewing v4");
  await expect(review.getByText("Draft kept on v3")).toBeVisible();
  await expect(feedback).toHaveCount(0);
  await screenshot(page, testInfo, "08b-header-switched-draft-kept-notice.png");
  await dockButton(review, "Dismiss kept draft notice").click();
  await expect(review.getByText("Draft kept on v3")).toHaveCount(0);
  await expect(headerStatus(page)).toHaveText("Viewing v4");
  await versionRow(page, 3).click();
  await review.getByRole("button", { name: /^Annotations/ }).click();
  await review.locator(".review-annotation-row").filter({ hasText: "Keep this roughness adjustment on v3" }).click();
  await expect(feedback).toHaveValue("Keep this roughness adjustment on v3");
  expect(studio.saveAnnotationCalls).toEqual([]);
  expect(studio.submissions).toEqual([]);
  expect(studio.contexts).toEqual([]);
  studio.assertHermetic();
});

// The saved Signal Lost UAT reply, also exercised by chatMarkdown.test.tsx.
const signalLostReport = `Resumed without repeating renders or rebuilding source.

- **Source verified:** \`signal-lost-animation-v1\`, commit \`488544962f01b2455a00b14f5fb83fa3610ea63c\`.
- **Sequence validated and published:** \`signal-lost-animation-draft-v1\`, commit \`6054ad2181ee3359faf3ed131d5da76aa8f2c23e\`.

| Contributor | State | Verified frames |
|---|---|---:|
| Macro | Running; unchanged on second check | 28/72 |
| Conduit | Planned | 0/96 |
| Console | Planned | 0/72 |
| Exterior | Planned | 0/120 |
| Title | Planned | 0/72 |
| Proxy | Planned | 0/1 |

Inspected macro frames 1, 14 and 28. Verified partial MP4: \`attempt-bd7475de-6bb0-4f1a-bf55-a4f3f247d50a-s25-28\`—only four frames, not a finished shot.

**Blockers:** contributors remain incomplete; assembly was not submitted. VLM admission returned \`hash_mismatch\`; no review ticket was returned.

Full receipts and continuation state saved under \`assets/signal-lost-animation/audit-2e4c256e/\`, including \`resume-observations-r2.json\`.

Target remains 18 seconds, 24fps, 640×360, silent. Glass/transmission, sampling noise, nonphysical flare and separate title-stage cut limitations remain. **No completed final movie yet.**`;

const assistantPanel = (page: Page) => page.getByRole("complementary", { name: "Assistant panel" });
const composerMessage = (page: Page) => assistantPanel(page).getByRole("textbox", { name: "Message Copilot" });
const jobsPill = (page: Page) => assistantPanel(page).getByRole("button", { name: /^Jobs:/ });
const targetBar = (page: Page) => assistantPanel(page).getByRole("button", { name: "Choose request target and references" });

async function narrowAssistant(page: Page) {
  const resize = page.getByRole("separator", { name: "Resize Assistant" });
  await resize.press("Home");
  await expect(resize).toHaveAttribute("aria-valuenow", "320");
  await expect.poll(async () => (await assistantPanel(page).boundingBox())?.width).toBe(320);
}

async function noHorizontalOverflow(...regions: Locator[]) {
  for (const region of regions) {
    await expect.poll(() => region.evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
  }
}

test("Signal Lost report renders as Markdown and live deltas respect manual scrolling", async ({ page, baseURL }, testInfo) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.requests = Array.from({ length: 20 }, (_, i) => request(`earlier-${i}`, `Previous render observation ${i + 1}.`, "completed", "2026-11-15T07:59:59Z"));
  studio.requests.push(request("initial", "Report on the saved Signal Lost animation without repeating renders."));
  await studio.install(); await studio.open(); await studio.ready();
  const conversation = assistantPanel(page).locator(".conversation");
  const scroll = assistantPanel(page).locator(".chat-scroll");
  const delta = (text: string) => studio.emit("assistant.delta", { message_id: "saved-signal-lost-report", text });
  const split = signalLostReport.indexOf("Macro") + 2;
  delta(signalLostReport.slice(0, split));
  await expect(conversation.getByText("Resumed without repeating renders or rebuilding source.", { exact: true })).toBeVisible();
  delta(signalLostReport.slice(split));
  const table = conversation.getByRole("table");
  await expect(table.locator("tbody tr")).toHaveCount(6);
  await expect(table.getByRole("cell", { name: "28/72", exact: true })).toHaveCSS("text-align", "right");
  await expect(conversation.locator("strong").filter({ hasText: /^Blockers:$/ })).toBeVisible();
  await expect(conversation.locator("code").filter({ hasText: /^hash_mismatch$/ })).toBeVisible();
  await noHorizontalOverflow(conversation, scroll);
  await expect(table).toBeInViewport({ ratio: 1 });
  await screenshot(page, testInfo, "09a-chat-markdown-report.png");

  await narrowAssistant(page);
  await noHorizontalOverflow(conversation, scroll);
  await scroll.hover();
  await page.mouse.wheel(0, -480);
  const jump = assistantPanel(page).getByRole("button", { name: "Jump to latest" });
  await expect(jump).toBeVisible();
  const before = await scroll.evaluate(el => ({ top: el.scrollTop, height: el.scrollHeight }));
  delta("\n\n" + "Additional streaming progress, not an outcome. ".repeat(12));
  await expect(conversation.getByText(/^Additional streaming progress/)).toBeAttached();
  await expect.poll(() => scroll.evaluate(el => el.scrollHeight)).toBeGreaterThan(before.height);
  await expect(scroll).toHaveJSProperty("scrollTop", before.top);
  await expect(jump).toBeVisible();
  await noHorizontalOverflow(conversation, scroll);
  await jump.click();
  await expect.poll(() => scroll.evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop)).toBeLessThan(2);
  studio.assertHermetic();
});

test("Target picker keeps naming, References and Composer panels independent at 320px", async ({ page, baseURL }, testInfo) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.retained = [];
  await studio.install({ catalogue: true, savedLayout: true }); await studio.open(); await studio.ready();
  const assistant = assistantPanel(page);
  const composer = assistant.locator(".composer");
  const input = composerMessage(page);
  const bar = targetBar(page);
  const picker = assistant.locator("#request-target-picker");
  const modes = picker.getByRole("group", { name: "Target mode" });
  const kinds = picker.getByRole("group", { name: "New item kind" });
  const disclosure = picker.locator(".target-references-disclosure");
  const referenceToggle = disclosure.locator(":scope > summary");
  const button = (label: string) => picker.getByRole("button", { name: label, exact: true });
  const name = picker.getByRole("textbox", { name: "New item name" });
  const close = button("Close target picker");
  const draft = "Keep this Composer draft unsent";
  await input.fill(draft);
  await input.evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(3, 10));
  await bar.click();
  await expect(modes.getByRole("button")).toHaveText(["Current view", "Project", "Existing…", "New…"]);
  await expect(modes.getByRole("button", { name: "Current view" })).toHaveAttribute("aria-pressed", "true");
  await button("Project").click();
  await expect(picker.getByText(/^Project-wide planning/)).toBeVisible();
  await button("New…").click();
  await kinds.getByRole("button", { name: "Asset", exact: true }).click();
  await expect(name).toBeVisible();
  await name.fill("Broken relay dish");
  const intentId = JSON.parse((await bar.getAttribute("title"))!).intent_id;
  expect(intentId).toBeTruthy();
  await kinds.getByRole("button", { name: "Scene", exact: true }).click();
  await expect(name).toHaveValue("Broken relay dish");
  await kinds.getByRole("button", { name: "Asset", exact: true }).click();
  expect(JSON.parse((await bar.getAttribute("title"))!).intent_id).toBe(intentId);
  await expect(picker.getByRole("combobox", { name: "Published item", exact: true })).toHaveCount(0);
  await expect(assistant.getByRole("button", { name: "Queue message" })).toBeEnabled();
  await expect(input).toBeVisible();
  await expect(disclosure).toHaveJSProperty("open", false);
  await screenshot(page, testInfo, "09b-target-picker-new-asset.png");

  await narrowAssistant(page);
  await referenceToggle.click();
  const checkbox = disclosure.getByRole("checkbox", { name: "Include current Review version (none open in Review)" });
  await expect(checkbox).toBeDisabled();
  const [box, labelBox] = await Promise.all([checkbox.boundingBox(), disclosure.locator(".target-checkbox-row span").boundingBox()]);
  expect(box!.width).toBeLessThanOrEqual(20);
  expect(Math.abs(box!.y - labelBox!.y)).toBeLessThanOrEqual(6);
  expect(box!.x + box!.width).toBeLessThan(labelBox!.x);
  await disclosure.getByRole("combobox", { name: "Reference item", exact: true }).selectOption("asset-a");
  await disclosure.getByRole("combobox", { name: "Reference version", exact: true }).selectOption("a-v1");
  await button("Add reference version").click();
  await expect(bar).toContainText("New asset · Broken relay dish");
  await expect(bar).toContainText("+1 ref");
  await expect(disclosure.locator(".reference-chip")).toContainText("Asset A · a-v1");
  await noHorizontalOverflow(picker, composer, composer.locator(".composer-links"));
  expect(await picker.evaluate(el => el.getBoundingClientRect().height)).toBeLessThanOrEqual(280);
  const overlaps = await composer.evaluate(el => {
    const controls = Array.from(el.querySelectorAll(".target-bar, .target-options button, .target-options select, .target-options input, .target-options summary, textarea, .composer-links button, .composer-actions button"));
    const boxes = controls.flatMap(control => Array.from(control.getClientRects(), rect => {
      const clip = (control.closest(".target-options") ?? el).getBoundingClientRect();
      return { left: Math.max(rect.left, clip.left), right: Math.min(rect.right, clip.right), top: Math.max(rect.top, clip.top), bottom: Math.min(rect.bottom, clip.bottom) };
    }));
    return boxes.some((a, i) => boxes.slice(i + 1).some(b => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1));
  });
  expect(overlaps).toBe(false);

  await name.press("Enter");
  await expect(picker).toHaveCount(0);
  await expect(input).toBeFocused();
  await expect(input).toHaveJSProperty("selectionStart", 3);
  await expect(input).toHaveJSProperty("selectionEnd", 10);
  await bar.click();
  await expect(disclosure).toHaveJSProperty("open", true);
  await jobsPill(page).click();
  const jobs = assistant.getByRole("region", { name: "Independent jobs" });
  await expect(jobs).toBeVisible();
  await expect(picker).toHaveCount(0);
  await expect(input).toHaveCount(0);
  await bar.click();
  await expect(jobs).toHaveCount(0);
  await expect(input).toBeVisible();
  await button("Existing…").click();
  const publishedItem = picker.getByRole("combobox", { name: "Published item" });
  const publishedVersion = picker.getByRole("combobox", { name: "Published version" });
  await publishedItem.selectOption("asset-a");
  await publishedVersion.selectOption("a-v1");
  studio.addAssetVersion("a-v3", "Asset A v3");
  await page.getByRole("button", { name: "Refresh project metadata" }).click();
  await expect(navigator(page).locator(".catalogue-item-header").filter({ hasText: "Asset A" })).toContainText("Latest v3");
  await expect(publishedVersion.locator('option[value="a-v3"]')).toBeAttached();
  await expect(publishedVersion).toHaveValue("a-v1");
  expect(JSON.parse((await bar.getAttribute("title"))!)).toEqual({ kind: "item", version: { item_id: "asset-a", version_id: "a-v1" } });
  await publishedItem.selectOption("board-b");
  const shots = picker.getByRole("combobox", { name: "Target storyboard shot" });
  await shots.selectOption("shot-1");
  await expect(shots).toHaveValue("shot-1");
  await expect(disclosure.locator(".reference-chip")).toContainText("Asset A · a-v1");
  await noHorizontalOverflow(picker, composer);
  await page.getByRole("separator", { name: "Resize Assistant" }).press("End");
  await screenshot(page, testInfo, "09c-target-picker-existing-refs.png");
  await shots.selectOption("");
  expect(JSON.parse((await bar.getAttribute("title"))!)).toEqual({ kind: "item", version: { item_id: "board-b", version_id: "b-v1" } });
  await shots.press("Escape");
  await expect(input).toBeFocused();
  await expect(picker).toHaveCount(0);
  await bar.click(); await close.click();
  await expect(input).toBeFocused();
  await bar.click(); await bar.click();
  await expect(input).toBeFocused();
  await expect(input).toHaveValue(draft);
  await expect(input).toHaveJSProperty("selectionStart", 3);
  await expect(input).toHaveJSProperty("selectionEnd", 10);
  expect(studio.submissions).toEqual([]);
  studio.assertHermetic();
});

test("Pending Existing target blocks queue and hidden Review capture until a target is chosen", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ catalogue: true, savedLayout: true }); await studio.open(); await studio.ready();
  await navigator(page).locator(".catalogue-item-header").filter({ hasText: "Asset A" }).click();
  const review = reviewPanel(page);
  await review.getByRole("button", { name: "Annotate presented frame" }).click();
  await feedbackBox(review).fill("This hidden Review draft must not be captured.");
  await page.getByRole("button", { name: "Chat focus", exact: true }).click();
  const bar = targetBar(page);
  const input = composerMessage(page);
  const assistant = assistantPanel(page);
  const queue = assistant.getByRole("button", { name: "Queue message", exact: true });
  const context = assistant.getByRole("button", { name: /^Context/ });
  const picker = assistant.locator("#request-target-picker");
  const mode = (name: string) => picker.getByRole("button", { name, exact: true });
  const warning = "Choose a published item for Existing… or switch target mode before sending.";
  await input.fill("Use a published target only");
  await expect(bar).toContainText("Target: Current view");
  await expect(context).toHaveText(/^Context[1-9]\d*$/);
  await expect(queue).toBeEnabled();
  await bar.click();
  await mode("Existing…").click();
  await expect(bar).toHaveText("Target: Choose published item…");
  await expect(bar).toHaveAttribute("aria-invalid", "true");
  await expect(picker.locator(".target-pending-hint")).toBeVisible();
  await expect(context).toHaveText("Context0");
  await expect(queue).toBeDisabled();
  await input.press("Control+Enter");
  await expect(assistant.getByRole("alert")).toHaveText(warning);
  expect(studio.contexts).toEqual([]);
  expect(studio.submissions).toEqual([]);
  // Preparing this hidden Review draft would have tried to persist the annotation.
  expect(studio.guard?.calls.filter(call => ["POST", "PUT", "PATCH", "DELETE"].includes(call.method))).toEqual([]);

  await mode("Close target picker").click();
  await expect(picker).toHaveCount(0);
  await expect(bar).toHaveText("Target: Choose published item…");
  await expect(queue).toBeDisabled();
  await context.click();
  const preview = assistant.getByRole("region", { name: "Next request context" }).locator(".context-preview");
  await expect(preview).toBeVisible();
  await expect(preview.locator(".target-badge, .annotation-context, img, code")).toHaveCount(0);
  await expect(preview).not.toContainText("Version ");
  await bar.click();
  await expect(mode("Existing…")).toHaveAttribute("aria-pressed", "true");
  await expect(picker.getByRole("combobox", { name: "Published item", exact: true })).toHaveValue("");
  await mode("Project").click();
  await expect(queue).toBeEnabled();
  await expect(assistant.getByText(warning, { exact: true })).toHaveCount(0);
  await mode("Existing…").click();
  await expect(queue).toBeDisabled();
  await mode("Current view").click();
  await expect(queue).toBeEnabled();
  await mode("Existing…").click();
  await expect(queue).toBeDisabled();
  await picker.getByRole("combobox", { name: "Published item", exact: true }).selectOption("board-b");
  await expect(bar).not.toHaveAttribute("aria-invalid");
  await expect(queue).toBeEnabled();
  await queue.click();
  await expect.poll(() => studio.submissions.length).toBe(1);
  expect(studio.contexts).toHaveLength(1);
  expect(studio.contexts[0]).toMatchObject({ request_target: { kind: "item", version: { item_id: "board-b", version_id: "b-v1" } }, annotations: [], attachments: [] });
  expect(studio.guard?.calls.filter(call => call.method === "POST").map(call => call.url)).toEqual([`${base}/contexts`, `${base}/requests`]);
  studio.assertHermetic();
});

test("Independent jobs shows real frame progress, actionable failure and collapsed settled history", async ({ page, baseURL }, testInfo) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.retained = [];
  const render = (id: string, shot_id: string | null, expected_frames: number, completed_frames: number | null): JobRecord => ({
    ...job(id, completed_frames === null ? "planned" : "running"), kind: "render", completed_frames, expected_frames,
    provider_id: null, target: { target_id: shot_id ?? "asset-a", shot_id, version: shot_id ? { item_id: "board-b", version_id: "b-v1" } : { item_id: "asset-a", version_id: "a-v1" } },
    availability: completed_frames === null ? "pending" : "partial",
    render_spec: { intent_id: id, shot_id, purpose: shot_id ? "draft_motion" : "proxy", source_closure_digest: "a".repeat(64), scene_name: "Signal Lost", camera: null,
      frames: { start: 1, end: expected_frames }, fps: { numerator: 24, denominator: 1 }, dimensions: { width: 640, height: 360 }, engine: "Cycles", device: "CPU", samples: 32,
      colour_management: { view_transform: "AgX", look: "None", exposure: 0, gamma: 1 }, renderer_revision: "synthetic" },
  });
  studio.snapshot.jobs = [
    render("macro-render", "sl-01-dormant-macro", 72, 28), render("conduit-render", "sl-02-conduit", 96, null), render("console-render", "sl-03-console", 72, null),
    render("exterior-render", "sl-04-exterior", 120, null), render("title-render", "sl-05-title", 72, null), render("preview-relay", null, 1, null),
    { ...job("finished-lookdev", "succeeded"), target: { target_id: "lookdev", version: { item_id: "asset-a", version_id: "a-v2" }, shot_id: null } },
    { ...job("finished-concept", "succeeded"), target: { target_id: "relay concept", version: null, shot_id: null } },
    { ...job("failed-review", "failed"), kind: "vlm_review", error_code: "hash_mismatch", target: { target_id: "animation draft", version: null, shot_id: null } },
  ];
  await studio.install({ catalogue: true, savedLayout: true }); await studio.open(); await studio.ready();
  const draft = composerMessage(page);
  await draft.fill("Don't queue the final assembly yet.");
  await draft.evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(6, 11));
  const badge = jobsPill(page);
  await expect(badge).toHaveText("Jobs1 failed · 1 running · 5 queued");
  await expect(badge).toHaveClass("attention-jobs");
  await badge.click();
  const panel = assistantPanel(page).getByRole("region", { name: "Independent jobs" });
  await expect(panel.getByRole("heading")).toHaveText(["Needs attention (1)", "Running (1)", "Planned / waiting (5)"]);
  await expect(panel.getByText("Failure: hash_mismatch", { exact: true })).toBeVisible();
  const row = (title: string) => panel.locator(".job-row").filter({ has: page.getByText(title, { exact: true }) });
  const macro = row("sl-01-dormant-macro");
  await expect(macro).toContainText("28/72 frames");
  const progress = macro.locator(".job-progress-bar");
  const fill = progress.locator(".job-progress-fill");
  await expect(progress).toHaveAttribute("aria-hidden", "true");
  await expect(fill).toHaveAttribute("style", /width: 39%/);
  await expect(fill).toHaveCSS("background-color", "rgb(155, 157, 255)");
  await expect(row("sl-02-conduit").locator(".job-progress")).toHaveText("96 frames");
  await expect(row("sl-03-console").locator(".job-progress")).toHaveText("72 frames");
  await expect(row("sl-04-exterior").locator(".job-progress")).toHaveText("120 frames");
  await expect(row("sl-05-title").locator(".job-progress")).toHaveText("72 frames");
  await expect(row("Proxy · Asset A").locator(".job-progress")).toHaveText("1 frame");
  await expect(panel.getByText("0/96 frames", { exact: true })).toHaveCount(0);
  const history = panel.locator(".jobs-finished-group");
  const finished = history.locator(":scope > summary");
  await expect(finished).toHaveText("Finished (2)");
  await expect(history).toHaveJSProperty("open", false);
  await expect(panel.getByText("Asset A · image generation", { exact: true })).toBeHidden();
  await expect(panel.getByRole("button", { name: "Request job cancellation" })).toHaveCount(0);
  await expect(finished).toBeInViewport({ ratio: 1 });
  await screenshot(page, testInfo, "09d-independent-jobs-collapsed-history.png");

  await finished.click();
  await expect(history).toHaveJSProperty("open", true);
  await expect(history.locator(".job-state-pill")).toHaveText(["succeeded", "succeeded"]);
  await expect(history.getByText("Asset A · image generation", { exact: true })).toBeVisible();
  await macro.getByText("Details", { exact: true }).click();
  await expect(macro.locator("code").filter({ hasText: /^macro-render$/ })).toBeVisible();
  await expect(macro.getByText("Provider: not reported", { exact: true })).toBeVisible();
  await expect(macro.getByRole("button", { name: "Request job cancellation" })).toBeEnabled();
  await panel.getByRole("button", { name: "Back to message" }).click();
  await expect(draft).toBeFocused();
  await expect(draft).toHaveValue("Don't queue the final assembly yet.");
  await expect(draft).toHaveJSProperty("selectionStart", 6);
  await expect(draft).toHaveJSProperty("selectionEnd", 11);
  expect(studio.guard!.calls.filter(call => call.method === "POST")).toEqual([]);
  studio.assertHermetic();
});
