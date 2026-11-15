import { expect, test } from "@playwright/test";
import { base, job, projectId, StudioFixture, work } from "./studio-fixture";
import type { ShotPlan } from "../../apps/frontend/src/api";

test("profile editing and deletion remain available when live status is offline", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ savedLayout: true });
  await page.route("**/api/v1/configuration/status", route => route.fulfill({ status: 503, json: { schema_version: "1.0", error: { code: "provider_unavailable", message: "Offline fixture", details: {} } } }));
  await studio.open(); await studio.ready();
  await page.getByRole("button", { name: "Project authoring", exact: true }).click();
  const menu = page.getByRole("region", { name: "Services and models", exact: true });
  await expect(menu.getByText("Live status unavailable. Profile editing remains available.")).toBeVisible();
  await expect(menu.getByRole("button", { name: "Delete profile Project authoring", exact: true })).toBeDisabled();
  await menu.getByRole("button", { name: "New profile" }).click();
  await menu.getByLabel("Profile name", { exact: true }).fill("Secondary");
  await menu.getByRole("checkbox", { name: "Use for this project" }).uncheck();
  await menu.getByRole("button", { name: "Save profile" }).click();
  await menu.getByRole("button", { name: "Edit profile Secondary", exact: true }).click();
  await expect(menu.getByLabel("Profile ID", { exact: true })).toHaveAttribute("readonly", "");
  await menu.getByLabel("Profile name", { exact: true }).fill("Secondary edited");
  await menu.getByLabel("Lead model", { exact: true }).fill("claude-opus-5.5");
  await menu.getByRole("button", { name: "Save profile" }).click();
  await expect(menu.getByRole("button", { name: "Edit profile Secondary edited", exact: true })).toBeVisible();
  expect(studio.configuration.profiles.at(-1)?.lead_model_id).toBe("claude-opus-5.5");
  await menu.getByRole("button", { name: "Delete profile Secondary edited", exact: true }).click();
  await expect(menu.getByRole("button", { name: "Edit profile Secondary edited", exact: true })).toHaveCount(0);
  expect(studio.configuration.profiles).toHaveLength(1);
  await menu.getByRole("button", { name: "Close services and models" }).click();
  await expect(menu).toHaveCount(0);
  studio.assertHermetic();
});

test("service refresh reads configuration and distinguishes unreachable and ambiguous recipes", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.configurationStatus.providers[0] = { ...studio.configurationStatus.providers[0], status: "unavailable", reason_code: "provider_unreachable", selected_models: [] };
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  await page.getByRole("button", { name: "Project authoring", exact: true }).click();
  const menu = page.getByRole("region", { name: "Services and models", exact: true });
  const comfy = menu.locator(".service-row").filter({ hasText: "local-comfy" });
  await expect(comfy).toContainText("Unreachable");
  studio.configurationStatus.providers[0] = { ...studio.configurationStatus.providers[0], status: "ambiguous", reason_code: "recipe_selection_required" };
  const before = studio.guard!.calls.filter(call => call.url.endsWith("/configuration")).length;
  await menu.getByRole("button", { name: "Refresh service status" }).click();
  await expect(comfy).toContainText("Recipe needed");
  expect(studio.guard!.calls.filter(call => call.url.endsWith("/configuration"))).toHaveLength(before + 1);
  studio.assertHermetic();
});

test("Services & models probes live status and creates a project authoring profile", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  await page.getByRole("button", { name: "Project authoring", exact: true }).click();
  const menu = page.getByRole("region", { name: "Services and models", exact: true });
  const comfy = menu.locator(".service-row").filter({ hasText: "local-comfy" });
  await expect(comfy).toContainText("Connected");
  await expect(comfy).toContainText("flux-2-klein-4b.safetensors");
  await expect(comfy).not.toContainText(/Unknown|Model unavailable/);
  await expect(menu.getByText("Workstation bridges", { exact: true })).toBeVisible();
  await expect(menu.locator(".bridge-grid")).toContainText("Blender CPUReady");
  await expect(menu.locator(".bridge-grid")).toContainText("BlendSwap MCPConfigured");
  await expect(menu.getByRole("spinbutton", { name: "Generation attempt limit" })).toBeEnabled();
  expect(studio.guard!.calls.some(call => call.url.endsWith("/configuration/status"))).toBe(true);
  await menu.getByRole("button", { name: "New profile" }).click();
  await menu.getByRole("textbox", { name: "Profile name", exact: true }).fill("Claude Sonnet Authoring");
  await expect(menu.getByRole("textbox", { name: "Profile ID", exact: true })).toHaveValue("claude-sonnet-authoring");
  await menu.getByLabel("Lead model", { exact: true }).fill("claude-sonnet-5.5");
  await menu.getByLabel("Worker model", { exact: true }).fill("gpt-5.3-codex");
  await menu.getByRole("button", { name: "Save profile" }).click();
  await expect(page.getByRole("button", { name: "Claude Sonnet Authoring", exact: true })).toBeVisible();
  await expect(menu.getByRole("combobox", { name: "Copilot profile" })).toHaveValue("claude-sonnet-authoring");
  await expect(menu.getByRole("spinbutton", { name: "Generation attempt limit" })).toBeEnabled();
  expect(studio.snapshot.settings.default_profile_id).toBe("claude-sonnet-authoring");
  expect(studio.configuration.profiles.at(-1)).toMatchObject({ lead_model_id: "claude-sonnet-5.5", worker_model_ids: ["gpt-5.3-codex"] });
  studio.assertHermetic();
});

for (const [state, code, reason, fenced, shown] of [
  ["running", "active_work_running", "Queued requests wait until the current work finishes.", false, false],
  ["awaiting_user", "awaiting_user", "Waiting for your answer.", false, false],
  ["running", null, "Execution fenced for reconciliation.", true, true],
  ["running", "queue_paused", "Queue explicitly paused.", false, true],
  ["awaiting_resume", "awaiting_resume", "Execution awaits explicit Resume.", false, true],
  ["stop_requested", "stop_requested", "Stop barrier pending.", false, true],
  ["outcome_uncertain", "uncertain_stop", "Original Stop outcome is uncertain.", false, true],
] as const) {
  test(`queue banner ${shown ? "shows" : "suppresses"} ${code ?? "fence"}`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    studio.snapshot.active_work = work(state);
    Object.assign(studio.snapshot.execution!, { hold_reason_code: code, hold_reason: reason, fenced });
    await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
    const banner = page.locator(".queue-hold");
    if (shown) { await expect(banner).toBeVisible(); await expect(banner).toContainText(reason); }
    else await expect(banner).toHaveCount(0);
    expect(studio.submissions).toEqual([]); studio.assertHermetic();
  });
}

test("status stays bottom-right across Split, Chat focus, Critic and collapsed rail with live steps", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.snapshot.active_work = work();
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  await expect(page.locator(".assistant-heading .assistant-status")).toHaveCount(0);
  const status = page.locator(".composer-footer .assistant-status");
  const bottomRight = async () => {
    await expect(status).toBeVisible();
    await expect.poll(async () => (await status.boundingBox())!.y).toBeGreaterThan(850);
    expect((await status.boundingBox())!.x).toBeGreaterThan(1400);
  };
  await bottomRight();
  await page.getByRole("button", { name: "Chat focus", exact: true }).click(); await bottomRight();
  const activity = { invocation_id: "prepare", name: "prepare_image_generation", state: "running", summary: "", artifact_ids: [], model_id: null };
  studio.emit("tool.started", activity);
  await expect(status).toHaveAttribute("aria-label", "Working · Preparing image…");
  await expect(status).toHaveText("Working · Preparing image…");
  studio.emit("tool.completed", { ...activity, state: "completed" });
  const checkpoint = { ...activity, invocation_id: "checkpoint", name: "checkpoint_sources" };
  studio.emit("tool.started", checkpoint);
  await expect(status).toHaveAttribute("aria-label", "Working · Checkpointing sources…");
  await expect(status).toHaveText("Working · Checkpointing sources…");
  const child = { ...activity, invocation_id: "child", name: "authoring-specialist", display_name: "Layout shot one", model_id: "synthetic" };
  studio.emit("subagent.started", child);
  studio.emit("tool.completed", { ...checkpoint, state: "completed" });
  await expect(status).toHaveAttribute("aria-label", "Working · Layout shot one…");
  await expect(status).toHaveText("Working · Layout shot one…");
  studio.emit("subagent.completed", { ...child, state: "completed" });
  studio.snapshot.jobs = [job("running", "running"), job("waiting", "waiting_resource")];
  for (const record of studio.snapshot.jobs) studio.emit("job.state_changed", record);
  await expect(status).toHaveAttribute("aria-label", "Working · 2 jobs running…");
  await expect(status).toHaveText("Working · 2 jobs running…");
  await page.getByRole("button", { name: "Critic", exact: true }).click();
  await expect(status).toBeHidden();
  const critic = page.locator(".assistant-footer .assistant-status");
  await expect(critic).toBeVisible(); await expect(critic).toHaveText("Working · 2 jobs running…");
  expect((await critic.boundingBox())!.x).toBeGreaterThan(1400);
  expect((await critic.boundingBox())!.y).toBeGreaterThan(850);
  await page.getByRole("button", { name: "Collapse Assistant" }).click();
  const rail = page.locator(".right-panel .panel-rail .assistant-status");
  await expect(rail).toBeVisible(); await expect(rail).toHaveAttribute("aria-label", "Working · 2 jobs running…");
  expect((await rail.boundingBox())!.y).toBeGreaterThan(900);
  studio.assertHermetic();
});

test("Assets item header exits Chat focus and opens the current published version", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ catalogue: true, savedLayout: true }); await studio.open(); await studio.ready();
  await page.getByRole("button", { name: "Chat focus", exact: true }).click();
  await expect(page.locator("main.review-panel")).toBeHidden();
  await page.locator(".catalogue-item-header").filter({ hasText: "Asset A" }).click();
  await expect(page.locator("main.review-panel")).toBeVisible();
  await expect(page.locator("main.review-panel h1")).toHaveText("Asset A v2");
  await expect(page.getByRole("button", { name: "Keyframes" })).toHaveAttribute("aria-pressed", "true");
  studio.assertHermetic();
});

for (const surface of ["Navigator", "Conversation"] as const) {
  for (const control of ["preview", "Inspect image"] as const) {
    test(`${surface} retained ${control} exits Chat focus and captures standalone Current view without a fake version`, async ({ page, baseURL }) => {
      const studio = new StudioFixture(page, baseURL!);
      await studio.install({ catalogue: true, savedLayout: true }); await studio.open(); await studio.ready();
      await page.locator(".catalogue-item-header").filter({ hasText: "Asset A" }).click();
      await expect(page.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
      if (surface === "Conversation") {
        studio.emit("tool.completed", { invocation_id: "retained", name: "register_artifact", state: "completed", summary: "Retained concept", artifact_ids: ["legacy"], model_id: null });
      }
      await page.getByRole("button", { name: "Chat focus", exact: true }).click();
      await expect(page.locator("main.review-panel")).toBeHidden();
      const card = surface === "Navigator" ? page.locator(".retained-output").filter({ has: page.locator("code", { hasText: "verified" }) }) : page.locator(".retained-output-card").filter({ hasText: "legacy" });
      await expect(card).toBeVisible();
      if (control === "preview") await card.locator("img").click();
      else await card.getByRole("button", { name: "Inspect image" }).click();
      const id = surface === "Navigator" ? "verified" : "legacy";
      const review = page.locator("main.review-panel");
      await expect(review).toBeVisible();
      await expect(review.getByText("Unpublished retained output · Concept images are not 3D meshes", { exact: true })).toBeVisible();
      await expect(review.getByText(surface === "Navigator" ? "Verified" : "Attribution unrecorded", { exact: true })).toBeVisible();
      await expect(review.getByText("1 × 1", { exact: true })).toBeVisible();
      await expect(review.getByText("SHA-256 aaaaaaaaaaaa", { exact: true })).toBeVisible();
      await expect(review.getByRole("button", { name: "Back to pinned version" }).last()).toBeVisible();
      await expect(review.locator("img")).toHaveAttribute("src", `${base}/artifacts/${id}/content`);
      await expect.poll(() => page.evaluate(project => JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${project}`)!), projectId)).toMatchObject({ item_id: null, version_id: null, request_target: { kind: "project" }, attachments: [{ artifact_id: id, role: "reference" }] });
      await page.getByRole("textbox", { name: "Message Copilot" }).fill("Discuss the retained image in Current view");
      await page.getByRole("button", { name: "Queue message", exact: true }).click();
      await expect.poll(() => studio.contexts.length).toBe(1);
      expect(studio.contexts[0]).toMatchObject({ item_id: null, version_id: null, request_target: { kind: "project" }, attachments: [{ artifact_id: id, role: "reference" }] });
      expect(studio.contexts[0].attachments).toHaveLength(1);
      expect(await page.evaluate(project => JSON.parse(localStorage.getItem(`proscenium.owner.view.version.${project}`)!), projectId)).toMatchObject({ version_id: "a-v2" });
      studio.assertHermetic();
    });
  }
}

test("published storyboard displays and steps ready ShotPlan panels without Blender receipts", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const shotPlan: ShotPlan = { schema_version: "1.0", project_id: projectId, plan_id: "plan", source_commit: "a".repeat(40), brief_artifact_ids: [], asset_dependencies: [], unresolved: [], shots: [{ shot_id: "shot-1", intent: "Shot one", camera_intent: "Static", motion_beats: [], scene_version: null, source_range: null, fps: null, frames: [{ frame_id: "frame-1", role: "start", composition: "Wide", artifact_id: "verified", state: "available", owner_selected: false }, { frame_id: "frame-2", role: "end", composition: "Close", artifact_id: "legacy", state: "available", owner_selected: false }] }] };
  await studio.install({ catalogue: true, savedLayout: true, shotPlan }); await studio.open(); await studio.ready();
  await page.getByRole("button", { name: "Chat focus", exact: true }).click();
  await page.locator(".catalogue-item-header").filter({ hasText: "Storyboard B" }).click();
  const review = page.locator("main.review-panel");
  await expect(review).toBeVisible();
  await expect(review.getByRole("button", { name: "Keyframes" })).toHaveAttribute("aria-pressed", "true");
  for (const [label, id, direction] of [["shot-1 · frame-1 (1 of 2)", "verified", "Next frame"], ["shot-1 · frame-2 (2 of 2)", "legacy", "Previous frame"], ["shot-1 · frame-1 (1 of 2)", "verified", null]] as const) {
    await expect(review.getByText(label, { exact: true })).toBeVisible();
    await expect(review.locator("img")).toHaveAttribute("src", `${base}/artifacts/${id}/content`);
    await expect(review.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
    if (direction) await review.getByRole("button", { name: direction }).click();
  }
  expect(studio.guard!.calls.filter(call => /frame-receipts|\/frames/.test(call.url))).toEqual([]);
  expect(studio.snapshot.jobs).toEqual([]); studio.assertHermetic();
});

// No pinned version isolates standalone inspection from published Review hydration.
for (const surface of ["Navigator", "Conversation"] as const) {
  for (const control of ["preview", "Inspect image"] as const) {
    test(`${surface} standalone ${control} opens Review and submits its reference without any pinned version`, async ({ page, baseURL }) => {
      const studio = new StudioFixture(page, baseURL!);
      await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
      if (surface === "Conversation") studio.emit("tool.completed", { invocation_id: "retained", name: "register_artifact", state: "completed", summary: "Retained concept", artifact_ids: ["verified"], model_id: null });
      await page.getByRole("button", { name: "Chat focus", exact: true }).click();
      const card = surface === "Navigator" ? page.locator(".retained-output").filter({ has: page.locator("code", { hasText: "verified" }) }) : page.locator(".retained-output-card");
      if (control === "preview") await card.locator("img").click();
      else await card.getByRole("button", { name: "Inspect image" }).click();
      const review = page.locator("main.review-panel");
      await expect(review).toBeVisible();
      await expect(review.locator("img")).toHaveAttribute("src", `${base}/artifacts/verified/content`);
      await expect(review.getByText("Unpublished retained output · Concept images are not 3D meshes", { exact: true })).toBeVisible();
      await expect(review.getByText("Verified", { exact: true })).toBeVisible();
      await expect(review.getByText("1 × 1", { exact: true })).toBeVisible();
      await expect(review.getByText("SHA-256 aaaaaaaaaaaa", { exact: true })).toBeVisible();
      await expect(review.getByRole("button", { name: "Clear preview" })).toBeVisible();
      expect(await page.evaluate(project => localStorage.getItem(`proscenium.owner.view.version.${project}`), projectId)).toBeNull();
      await expect.poll(() => page.evaluate(project => JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${project}`)!), projectId)).toMatchObject({ item_id: null, version_id: null, attachments: [{ artifact_id: "verified", role: "reference" }] });
      await page.getByRole("textbox", { name: "Message Copilot" }).fill("Inspect this standalone Current view");
      await page.getByRole("button", { name: "Queue message", exact: true }).click();
      await expect.poll(() => studio.contexts.length).toBe(1);
      expect(studio.contexts[0]).toMatchObject({ item_id: null, version_id: null, request_target: { kind: "project" }, attachments: [{ artifact_id: "verified", role: "reference" }] });
      studio.assertHermetic();
    });
  }
}

test("Jobs badge updates empty, active, failed attention and all-succeeded history live", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  const badge = page.getByRole("button", { name: /^Jobs:/ });
  await expect(badge).toHaveClass("empty-jobs"); await expect(badge).toHaveText("Jobs0");
  await expect(badge.locator(".jobs-indicator")).toHaveCount(0);
  const first = job("one", "running"), second = job("two", "waiting_resource");
  for (const record of [first, second]) studio.emit("job.state_changed", record);
  await expect(badge).toHaveClass("active-jobs"); await expect(badge).toHaveText("Jobs2 active");
  await expect(badge.locator(".jobs-indicator")).toBeVisible();
  studio.emit("job.state_changed", { ...first, state: "failed", state_revision: 2, error_code: "corrupt_provider_output" });
  studio.emit("job.state_changed", { ...second, state: "succeeded", state_revision: 2 });
  await expect(badge).toHaveClass("attention-jobs"); await expect(badge).toHaveText("Jobs2 done");
  await badge.click();
  const panel = page.getByRole("region", { name: "Independent jobs" });
  await expect(panel.getByText("Failure: corrupt_provider_output", { exact: true })).toBeVisible();
  await expect(panel.getByText("Provider: synthetic", { exact: true })).toHaveCount(2);
  studio.emit("job.state_changed", { ...first, state: "succeeded", state_revision: 3 });
  await expect(badge).toHaveClass("done-jobs"); await expect(badge).toHaveText("Jobs2 done");
  await expect(badge.locator(".jobs-indicator")).toHaveCount(0);
  await expect(panel.getByText("Failure: corrupt_provider_output")).toHaveCount(0);
  studio.assertHermetic();
});

test("Review uses compact mode and transport bars with a single annotation editor", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ catalogue: true, savedLayout: true }); await studio.open(); await studio.ready();
  await page.getByRole("button", { name: "Asset A v2 a-v2" }).click();
  const review = page.locator("main.review-panel");
  const modes = review.getByRole("navigation", { name: "Review mode", exact: true });
  await expect(modes).toBeVisible();
  expect((await modes.boundingBox())!.height).toBeLessThanOrEqual(52);
  const modeNames = ["3D Inspection", "Keyframes", "Animation"];
  const boxes = [];
  for (const [index, name] of modeNames.entries()) {
    const button = modes.getByRole("button", { name, exact: true });
    await expect(button).toHaveAttribute("aria-keyshortcuts", String(index + 1));
    await expect(button).toHaveAttribute("title", `${name} (${index + 1})`);
    boxes.push((await button.boundingBox())!);
    await page.locator(".brand").click(); await page.keyboard.press(String(index + 1));
    await expect(button).toHaveAttribute("aria-pressed", "true");
  }
  expect(new Set(boxes.map(box => box.y)).size).toBe(1);
  expect(boxes[0].x).toBeLessThan(boxes[1].x);
  expect(boxes[1].x).toBeLessThan(boxes[2].x);
  await page.keyboard.press("2");
  const transport = review.locator('[aria-label="Review transport"]');
  const aligned = async () => {
    await expect(transport).toBeVisible();
    expect((await transport.boundingBox())!.height).toBeLessThanOrEqual(52);
    const bounds = await Promise.all([
      transport.getByRole("button", { name: "Previous frame", exact: true }),
      transport.getByRole("spinbutton", { name: "Frame", exact: true }),
      transport.getByRole("button", { name: "Next frame", exact: true }),
    ].map(control => control.boundingBox()));
    expect(new Set(bounds.map(box => box!.y)).size).toBe(1);
    expect(bounds[0]!.x).toBeLessThan(bounds[1]!.x);
    expect(bounds[1]!.x).toBeLessThan(bounds[2]!.x);
  };
  await aligned();
  await expect(review.getByRole("combobox", { name: "Shot / render job" })).toHaveCount(0);
  await expect(review.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  await review.getByRole("button", { name: "Annotate presented frame" }).click();
  await expect(modes).toHaveCount(0); await expect(transport).toHaveCount(0);
  const editor = review.getByRole("region", { name: "Pinned annotation", exact: true });
  await expect(editor.locator(":scope > .review-bar")).toHaveCount(1);
  for (const name of ["Stroke", "Arrow", "Region", "Pin", "Eraser", "Mask", "Close draft"]) await expect(editor.getByRole("button", { name, exact: true })).toBeVisible();
  const width = editor.getByRole("spinbutton", { name: "Width (image px)", exact: true });
  await expect(width).toBeVisible();
  expect(await width.locator("..").evaluate(label => getComputedStyle(label).flexDirection)).toBe("row");
  await expect(editor.locator(".annotation-dock .review-transport-bar")).toHaveCount(1);
  expect((await editor.locator(".annotation-dock").boundingBox())!.height).toBeLessThanOrEqual(120);
  await editor.getByRole("textbox", { name: "Feedback", exact: true }).fill("Do not switch modes while typing");
  await page.keyboard.press("1");
  await expect(editor).toBeVisible();
  await editor.getByRole("button", { name: "Close draft", exact: true }).click();
  await expect(modes.getByRole("button", { name: "Keyframes", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(editor).toHaveCount(0); await aligned();
  const surface = review.locator(".review-surface");
  expect(await surface.evaluate(element => element.scrollHeight <= element.clientHeight)).toBe(true);
  expect(studio.guard!.calls.filter(call => call.method === "POST")).toEqual([]);
  studio.assertHermetic();
});

test("ten unchanged waiting-resource job events cause no retained or catalogue HTTP churn", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const waiting = job("waiting", "waiting_resource");
  studio.snapshot.jobs = [waiting];
  await studio.install({ catalogue: true, savedLayout: true }); await studio.open(); await studio.ready();
  await expect(page.getByRole("button", { name: "Asset A v2 a-v2" })).toBeVisible();
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  const reads = () => studio.guard!.calls.filter(call => call.method === "GET" && (new URL(call.url, baseURL).pathname === `${base}/artifacts` || /\/items\/[^/]+\/versions$/.test(new URL(call.url, baseURL).pathname)));
  const before = reads().length;
  for (let i = 0; i < 10; i++) studio.emit("job.state_changed", { ...waiting, state_revision: i + 2 });
  await expect.poll(() => page.evaluate(project => JSON.parse(localStorage.getItem(`proscenium.owner.cursor.${project}`)!), projectId)).toBe(10);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  expect(reads().slice(before)).toEqual([]);
  await expect(page.getByRole("button", { name: /^Jobs:/ })).toHaveText("Jobs1 active");
  studio.assertHermetic();
});
