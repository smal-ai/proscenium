import { test } from "@playwright/test";
import { verifyLayout } from "./layout-journey";

test("DPR 2 splitters, remembered tabs, layout and conversation following", async ({ page, baseURL }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-dpr2", "DPR 2 geometry belongs to the DPR 2 project");
  await verifyLayout(page, baseURL!, 2);
});
