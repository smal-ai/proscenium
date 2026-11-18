import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import type { Camera } from "../../apps/frontend/src/api";
import { projectBlenderPoint } from "../../apps/frontend/src/features/review/media";
import type { Draft } from "../../apps/frontend/src/features/review/outbox";
import { boardName, kitId, kitName, kitV, makePolishStudio, restoredRenderCamera } from "./rc3-polish-fixture";
import { base, deferred, projectId } from "./studio-fixture";

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
