import { expect, test } from "@playwright/test";
import type { ShotPlan } from "../../apps/frontend/src/api";
import { base, deferred, pageOf, projectId, StudioFixture, version } from "./studio-fixture";

function shot(number: number): ShotPlan["shots"][number] {
  return {
    shot_id: `shot-${number}`, intent: `Shot ${number}`, camera_intent: "Static", motion_beats: [],
    scene_version: null, source_range: null, fps: null,
    frames: (["start", "end"] as const).map(role => ({
      frame_id: `frame-${number}-${role}`, role, composition: "Wide",
      artifact_id: role === "start" ? "verified" : "legacy", state: "available", owner_selected: false,
    })),
  };
}

const shotPlan: ShotPlan = {
  schema_version: "1.0", project_id: projectId, plan_id: "first-open-plan", source_commit: "a".repeat(40),
  brief_artifact_ids: [], asset_dependencies: [], unresolved: [],
  shots: [shot(1), ...Array.from({ length: 7 }, (_, index) => shot(index + 2))],
};

test("a fresh browser opens the selected storyboard and its first ready panel without clicks", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ catalogue: true, savedLayout: false, created: false, shotPlan });
  studio.versionRecords.push(version("board-b", "b-v2", "Newer unselected storyboard", "storyboard"));
  const board = studio.snapshot.items.find(item => item.item_id === "board-b")!;
  board.current_version_id = "b-v2";
  board.selected_version_id = "b-v1";
  await studio.open();
  const navigation = page.getByRole("complementary", { name: "Navigator" });
  const review = page.locator("main.review-panel");
  await expect(navigation.getByRole("button", { name: "Shots", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(navigation.getByRole("combobox", { name: "Storyboard version" })).toHaveValue("b-v1");
  await expect(navigation.locator(".sb-shots > li")).toHaveCount(8);
  await expect(navigation.locator(".sb-frames img")).toHaveCount(16);
  await expect(review).toBeVisible();
  await expect(review.getByRole("button", { name: "Keyframes" })).toHaveAttribute("aria-pressed", "true");
  await expect(review.getByText("shot-1 · frame-1-start (1 of 16)", { exact: true })).toBeVisible();
  await expect(review.locator("img")).toHaveAttribute("src", `${base}/artifacts/verified/content`);
  await expect(review.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  expect(studio.guard!.calls.every(call => call.method === "GET")).toBe(true);
  studio.assertHermetic();
});

test("a tab click cancels first-open selection while the storyboard request is in flight", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const gate = deferred();
  const routePath = `${base}/items/board-b/versions`;
  await page.addInitScript(path => {
    const fetchBrowser = window.fetch;
    window.fetch = (input, init) => {
      if (typeof input === "string" && new URL(input, location.href).pathname === path && init?.signal) {
        document.documentElement.dataset.firstOpenStarted = "true";
        init.signal.addEventListener("abort", () => { document.documentElement.dataset.firstOpenAborted = "true"; });
      }
      return fetchBrowser(input, init);
    };
  }, routePath);
  await studio.install({ catalogue: true, routes: [{ path: routePath, handler: async route => {
    await gate.promise;
    await route.fulfill({ json: pageOf(studio.versionRecords.filter(record => record.item_id === "board-b")) });
  } }] });
  try {
    await studio.open();
    await expect(page.locator("html")).toHaveAttribute("data-first-open-started", "true");
    const navigation = page.getByRole("complementary", { name: "Navigator" });
    const outliner = navigation.getByRole("button", { name: "Outliner", exact: true });
    await outliner.click();
    await expect(page.locator("html")).toHaveAttribute("data-first-open-aborted", "true");
    gate.release();
    await expect(outliner).toHaveAttribute("aria-selected", "true");
    expect(await page.evaluate(id => localStorage.getItem(`proscenium.owner.view.version.${id}`), projectId)).toBeNull();
    studio.assertHermetic();
  } finally { gate.release(); }
});

for (const state of ["saved layout", "created marker", "saved version"] as const) {
  test(`first open leaves the ${state} in control`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    await studio.install({ catalogue: true, savedLayout: state === "saved layout", created: state === "created marker", shotPlan });
    if (state === "saved version") await page.addInitScript(record => {
      localStorage.setItem(`proscenium.owner.view.version.${record.project_id}`, JSON.stringify(record));
    }, studio.versionRecords.find(record => record.version_id === "a-v2")!);
    await studio.open();
    const navigation = page.getByRole("complementary", { name: "Navigator" });
    const review = page.locator("main.review-panel");
    if (state === "created marker") {
      await expect(review).toBeHidden();
      await expect(navigation.getByRole("button", { name: "Expand Navigator" })).toBeVisible();
    } else {
      await expect(navigation.locator('button.catalogue-item-header[title="Storyboard B"]')).toBeEnabled();
      await expect(navigation.getByRole("button", { name: "Assets", exact: true })).toHaveAttribute("aria-selected", "true");
      if (state === "saved version") await expect(review.getByRole("heading", { name: "Asset A v2" })).toBeVisible();
      else await expect(review.getByText("Choose a published item", { exact: true })).toBeVisible();
    }
    studio.assertHermetic();
  });
}
