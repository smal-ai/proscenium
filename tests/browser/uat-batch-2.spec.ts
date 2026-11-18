import { expect, test } from "@playwright/test";
import { base, job, projectId, request, StudioFixture, work } from "./studio-fixture";
import type { Decision, DecisionAnswer, ShotPlan } from "../../apps/frontend/src/api";

test("pending shell Decision stays above Composer with a long bottom-scrolled Conversation", async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  const studio = new StudioFixture(page, baseURL!);
  studio.snapshot.active_work = work("awaiting_user");
  studio.requests = Array.from({ length: 40 }, (_, index) => request(`long-${index}`, `Shot ${index}: ${"Describe the relay installation and its camera movement. ".repeat(30)}`));
  const command = "git push origin main";
  const prompt = JSON.stringify({ canOfferSessionApproval: true, commandSegments: [{ fullCommandText: command }], fullCommandText: command, kind: "shell" });
  const decision: Decision = { schema_version: "1.0", project_id: projectId, decision_id: "permission", work_id: "work", kind: "permission", state: "pending", state_revision: 3, fingerprint: "b".repeat(64), prompt, options: ["approve_once", "decline"] };
  studio.snapshot.pending_decisions = [decision];
  await studio.install({ savedLayout: true });
  let answer: DecisionAnswer | null = null;
  await page.route(`**${base}/decisions/permission/answer`, async route => {
    answer = route.request().postDataJSON() as DecisionAnswer;
    studio.snapshot.pending_decisions = [];
    await route.fulfill({ json: { schema_version: "1.0", operation_id: answer.operation_id, resource_id: decision.decision_id, state: "updated", state_revision: 4, replayed: false } });
  });
  await studio.open(); await studio.ready();
  const scroll = page.locator(".chat-scroll");
  await expect(page.locator(".conversation .user-message")).toHaveCount(40);
  await scroll.evaluate(element => { element.scrollTop = element.scrollHeight; element.dispatchEvent(new Event("scroll")); });
  await expect.poll(() => scroll.evaluate(element => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(3);
  const recovery = page.locator(".chat-recovery");
  const card = recovery.locator(".decision-card");
  await expect(card).toBeInViewport({ ratio: 1 });
  await expect(card.getByRole("heading", { name: "Approve shell command?" })).toBeVisible();
  await expect(card.locator(":scope > .decision-code")).toHaveText(command);
  await expect(card.getByText(prompt, { exact: true })).toBeHidden();
  const details = card.locator(".decision-details");
  await expect(details).not.toHaveAttribute("open", "");
  await details.locator("summary").click();
  await expect(details).toContainText(prompt);
  await expect(details).toContainText("Exact revision 3 · Work work");
  await expect(details.locator(".fingerprint")).toHaveText(decision.fingerprint);
  await details.locator("summary").click();
  expect(await recovery.evaluate(element => element.nextElementSibling?.classList.contains("composer"))).toBe(true);
  const composer = page.locator(".composer");
  await expect(composer).toBeInViewport({ ratio: 1 });
  const cardBox = (await card.boundingBox())!, composerBox = (await composer.boundingBox())!;
  expect(cardBox.y + cardBox.height).toBeLessThanOrEqual(composerBox.y);
  expect(composerBox.y + composerBox.height).toBeLessThanOrEqual(1080);
  expect(await recovery.evaluate(element => parseFloat(getComputedStyle(element).maxHeight))).toBeCloseTo(453.6, 1);
  await card.getByRole("button", { name: "Approve once", exact: true }).click();
  await expect(card).toHaveCount(0);
  expect(answer).toMatchObject({ action: "approve_once", expected_revision: 3, fingerprint: decision.fingerprint });
  studio.assertHermetic();
});

test("multiple large Decisions share bounded recovery space without hiding Composer", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.snapshot.pending_decisions = Array.from({ length: 5 }, (_, index) => ({ schema_version: "1.0", project_id: projectId, decision_id: `permission-${index}`, work_id: "work", kind: "permission", state: "pending", state_revision: 1, fingerprint: "a".repeat(64), prompt: JSON.stringify({ kind: "shell", fullCommandText: `git push origin branch-${index}\n${"# Exact scope and destination\n".repeat(100)}` }), options: ["approve_once", "decline"] }));
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  const recovery = page.locator(".chat-recovery");
  await expect(recovery.locator(".decision-card")).toHaveCount(5);
  const geometry = await recovery.evaluate(element => ({ height: element.getBoundingClientRect().height, scrollHeight: element.scrollHeight, clientHeight: element.clientHeight, overflow: getComputedStyle(element).overflowY }));
  expect(geometry.height).toBeLessThanOrEqual(1080 * 0.42);
  expect(geometry.scrollHeight).toBeGreaterThan(geometry.clientHeight);
  expect(geometry.overflow).toBe("auto");
  for (const code of await recovery.locator(":scope > .decision-card > .decision-code").all()) expect((await code.boundingBox())!.height).toBeLessThanOrEqual(120);
  await expect(page.locator(".composer")).toBeInViewport({ ratio: 1 });
  await recovery.evaluate(element => { element.scrollTop = element.scrollHeight; });
  await expect(recovery.locator(".decision-card").last().getByRole("button", { name: "Approve once" })).toBeInViewport({ ratio: 1 });
  studio.assertHermetic();
});

for (const [kind, prompt, heading, detail] of [
  ["permission", JSON.stringify({ kind: "mcp", serverName: "blender", toolName: "create_generation", toolTitle: "Create generation" }), "Approve MCP tool call?", "blender · create_generation · Create generation"],
  ["permission", JSON.stringify({ kind: "read", path: "storyboards/plan.json" }), "Approve file access?", "storyboards/plan.json"],
  ["permission", JSON.stringify({ kind: "write", fileName: "storyboards/plan.json" }), "Approve file access?", "storyboards/plan.json"],
  ["permission", "Confirm this exact operation", "", "Confirm this exact operation"],
  ["permission", "null", "", "null"],
  ["permission", "{invalid JSON", "", "{invalid JSON"],
  ["user_input", "Which camera angle?", "", "Which camera angle?"],
  ["generation_budget", "Extend this task?", "", "Extend this task?"],
] as const) {
  test(`Decision formats ${kind} ${prompt} without exposing metadata by default`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    studio.snapshot.pending_decisions = [{ schema_version: "1.0", project_id: projectId, decision_id: "decision", work_id: "work", kind, state: "pending", state_revision: 1, fingerprint: "a".repeat(64), prompt, options: [] }];
    await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
    const card = page.locator(".chat-recovery .decision-card");
    if (heading) await expect(card.getByRole("heading", { name: heading })).toBeVisible();
    await expect(card.getByText(detail, { exact: true })).toBeVisible();
    await expect(card.locator(".fingerprint")).toBeHidden();
    studio.assertHermetic();
  });
}

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

test("Navigator and Assistant panels open at maximum width by default on an existing project", async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ catalogue: true, savedLayout: false });
  await studio.open(); await studio.ready();
  const navigator = page.getByRole("complementary", { name: "Navigator", exact: true });
  const assistant = page.getByRole("complementary", { name: "Assistant panel" });
  await expect.poll(async () => (await navigator.boundingBox())!.width).toBe(520);
  await expect.poll(async () => (await assistant.boundingBox())!.width).toBe(760);
  studio.assertHermetic();
});

test("Critic realtime badge, Pending/All SAST cards, persisted drafts and human resolution workflow", async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  const studio = new StudioFixture(page, baseURL!);
  const target = {
    origin: "concept" as const,
    item_id: "asset-a",
    version_id: "a-v2",
    shot_id: null,
    render_job_id: null,
    sequence_frame: null,
    source_frame: null,
    artifact_id: "verified",
    media_artifact_id: null,
    displayed_sha256: "a".repeat(64),
    dimensions: { width: 1, height: 1 },
  };
  // Routine agent self-check starts in All only (0 pending).
  studio.reviews.push({
    schema_version: "1.0",
    project_id: projectId,
    ticket: {
      schema_version: "1.0",
      review_id: "rev-self",
      job_id: "job-self",
      status: "ready",
      title: "Agent internal check",
      created_at: "2026-11-18T12:00:00Z",
      revision: 2,
      human_review_state: "not_requested",
      awaiting_human: false,
      question: null,
      finding_count: 1,
      unreviewed_count: 1,
      error_code: null,
    },
    criteria: "Routine check.",
    targets: [target],
    telemetry_artifact_ids: [],
    findings: [
      {
        schema_version: "1.0",
        finding_id: "rev-self-f0",
        review_id: "rev-self",
        project_id: projectId,
        revision: 1,
        target,
        observation: "Slight rim highlight.",
        recommendation: "Dim rim light by 10%.",
        evidence: [{ kind: "visual", artifact_id: "verified", description: "Upper-left edge." }],
        state: "unreviewed",
      },
    ],
    diagnostics: null,
  });

  await studio.install({ catalogue: true, savedLayout: true });
  await studio.open(); await studio.ready();

  const criticTab = page.getByRole("button", { name: "Critic", exact: true });
  await expect(criticTab).toHaveAttribute("data-pending-reviews", "0");
  await expect(criticTab.locator(".tab-count")).toHaveCount(0);

  // Live escalated review with 2 findings arrives while user is on Copilot -> badge shows 1 (per review, not per finding).
  const escalatedDetail = {
    schema_version: "1.0" as const,
    project_id: projectId,
    ticket: {
      schema_version: "1.0" as const,
      review_id: "rev-escalated",
      job_id: "job-escalated",
      status: "ready" as const,
      title: "Orbital dish collar & rim light",
      created_at: "2026-11-18T14:05:00Z",
      revision: 3,
      human_review_state: "pending" as const,
      awaiting_human: true,
      question: "Should the dish silhouette stay centered or offset camera-left?",
      finding_count: 2,
      unreviewed_count: 2,
      error_code: null,
    },
    criteria: "Verify dish collar seats flush on dorsal spine and rim light preserves silhouette.",
    targets: [target],
    telemetry_artifact_ids: [],
    findings: [
      {
        schema_version: "1.0" as const,
        finding_id: "rev-esc-f0",
        review_id: "rev-escalated",
        project_id: projectId,
        revision: 1,
        target,
        observation: "Dish collar floats above dorsal spine.",
        recommendation: "Seat dish collar flush against the dorsal spine.",
        evidence: [{ kind: "visual" as const, artifact_id: "verified", description: "Gap at spine mount." }],
        state: "unreviewed" as const,
      },
      {
        schema_version: "1.0" as const,
        finding_id: "rev-esc-f1",
        review_id: "rev-escalated",
        project_id: projectId,
        revision: 1,
        target,
        observation: "Rim highlight clips specular detail on dish edge.",
        recommendation: "Lower rim key exposure by 0.7 stops.",
        evidence: [{ kind: "visual" as const, artifact_id: "verified", description: "Clipped upper rim." }],
        state: "unreviewed" as const,
      },
    ],
    diagnostics: {
      finish_reason: "stop",
      prompt_tokens: 1800,
      completion_tokens: 420,
      reasoning_tokens: 300,
      reasoning_effort: "medium",
      rubric_revision: "proscenium-vlm-2",
      error_code: null,
    },
  };
  studio.reviews.unshift(escalatedDetail);
  studio.emit("review.changed", escalatedDetail.ticket);

  await expect(criticTab).toHaveAttribute("data-pending-reviews", "1");
  await expect(criticTab.locator(".tab-count")).toHaveText("1");

  await criticTab.click();
  const critic = page.getByRole("region", { name: "Critic", exact: true });
  await expect(critic.getByRole("button", { name: "Load existing reviews" })).toHaveCount(0);
  await expect(critic.locator("select")).toHaveCount(0);

  // Default Pending filter shows 1 card with ONLY review name + 24h SAST date/time.
  const cards = critic.locator(".critic-review-card");
  await expect(cards).toHaveCount(1);
  await expect(cards.first().locator(".critic-card-title")).toHaveText("Orbital dish collar & rim light");
  await expect(cards.first().locator(".critic-card-time")).toHaveText("2026-11-18 16:05 SAST");
  expect((await cards.first().innerText()).trim().split(/\n+/)).toEqual([
    "Orbital dish collar & rim light",
    "2026-11-18 16:05 SAST",
  ]);

  // All filter reveals the routine self-check alongside the pending review.
  await critic.getByRole("button", { name: "All", exact: true }).click();
  await expect(cards).toHaveCount(2);
  await critic.getByRole("button", { name: "Pending (1)", exact: true }).click();
  await expect(cards).toHaveCount(1);

  // Open the pending review thread, stage an edit, and verify persistence across tab switch + reload.
  await cards.first().click();
  await expect(critic.locator(".critic-question")).toContainText(
    "Should the dish silhouette stay centered or offset camera-left?",
  );
  await expect(critic.locator(".critic-finding")).toHaveCount(2);

  const secondFinding = critic.locator(".critic-finding").nth(1);
  await secondFinding.getByRole("checkbox", { name: "Include finding" }).check();
  const instructionBox = secondFinding.getByRole("textbox", { name: "Approved instruction rev-esc-f1" });
  await instructionBox.fill("Lower rim key exposure by 0.7 stops and offset dish camera-left.");

  await page.getByRole("button", { name: "Copilot", exact: true }).click();
  await criticTab.click();
  await expect(instructionBox).toHaveValue("Lower rim key exposure by 0.7 stops and offset dish camera-left.");

  await page.reload();
  await studio.ready();
  await criticTab.click();
  await expect(critic.getByRole("textbox", { name: "Approved instruction rev-esc-f1" })).toHaveValue(
    "Lower rim key exposure by 0.7 stops and offset dish camera-left.",
  );

  // Rejecting finding 0 while finding 1 remains unreviewed keeps badge at 1.
  await critic.locator(".critic-finding").first().getByRole("button", { name: "Reject finding" }).click();
  await expect(criticTab).toHaveAttribute("data-pending-reviews", "1");

  // Submitting the remaining finding sends approved_findings pinned to asset-a / a-v2 and clears badge to 0!
  await critic.getByRole("button", { name: "Queue selected findings once" }).click();
  await expect(criticTab).toHaveAttribute("data-pending-reviews", "0");
  await expect(criticTab.locator(".tab-count")).toHaveCount(0);
  expect(studio.submissions).toHaveLength(1);
  expect(studio.contexts.at(-1)?.request_target).toEqual({
    kind: "item",
    version: { item_id: "asset-a", version_id: "a-v2" },
  });
  expect(studio.contexts.at(-1)?.approved_findings).toEqual([
    {
      finding_id: "rev-esc-f1",
      finding_revision: 1,
      approved_text: "Lower rim key exposure by 0.7 stops and offset dish camera-left.",
      target,
    },
  ]);

  // Zero-defect escalated question review shows "No defects reported." and clears via "Done without changes".
  const questionOnly = {
    schema_version: "1.0" as const,
    project_id: projectId,
    ticket: {
      schema_version: "1.0" as const,
      review_id: "rev-q-only",
      job_id: "job-q-only",
      status: "ready" as const,
      title: "Zero-defect framing check",
      created_at: "2026-11-18T15:30:00Z",
      revision: 2,
      human_review_state: "pending" as const,
      awaiting_human: true,
      question: "Confirm this wide establishing angle before scene assembly?",
      finding_count: 0,
      unreviewed_count: 0,
      error_code: null,
    },
    criteria: "Check wide establishing framing.",
    targets: [target],
    telemetry_artifact_ids: [],
    findings: [],
    diagnostics: null,
  };
  studio.reviews.unshift(questionOnly);
  studio.emit("review.changed", questionOnly.ticket);
  await expect(criticTab).toHaveAttribute("data-pending-reviews", "1");
  await criticTab.click();
  await critic.getByRole("button", { name: "Back to reviews" }).click();
  await critic.locator(".critic-review-card").first().click();
  await expect(critic).toContainText("No defects reported.");
  await critic.getByRole("button", { name: "Done without changes" }).click();
  await expect(criticTab).toHaveAttribute("data-pending-reviews", "0");
  await expect(critic.getByRole("heading", { name: "No pending reviews" })).toBeVisible();

  studio.assertHermetic();
});

function projectWorldToCanvasUv(camera: { camera_to_world: number[]; projection: number[] }, world: [number, number, number]): [number, number] {
  // Invert rigid camera_to_world [R | t; 0 0 0 1] -> [R^T | -R^T t]
  const m = camera.camera_to_world;
  const r00 = m[0], r01 = m[1], r02 = m[2], tx = m[3];
  const r10 = m[4], r11 = m[5], r12 = m[6], ty = m[7];
  const r20 = m[8], r21 = m[9], r22 = m[10], tz = m[11];
  const vx = r00 * (world[0] - tx) + r10 * (world[1] - ty) + r20 * (world[2] - tz);
  const vy = r01 * (world[0] - tx) + r11 * (world[1] - ty) + r21 * (world[2] - tz);
  const vz = r02 * (world[0] - tx) + r12 * (world[1] - ty) + r22 * (world[2] - tz);
  const p = camera.projection;
  const cx = p[0] * vx + p[1] * vy + p[2] * vz + p[3];
  const cy = p[4] * vx + p[5] * vy + p[6] * vz + p[7];
  const cw = p[12] * vx + p[13] * vy + p[14] * vz + p[15];
  return [(cx / cw + 1) / 2, (1 - cy / cw) / 2];
}

test("3D spatial pin resolves multi-primitive & scaled child instances, survives orbit/reload/v3 switch, and gates unavailable-proxy fallback", async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  const studio = new StudioFixture(page, baseURL!);
  // Register two maps on a-v2 (map-a-v2-early + map-a-v2) to exercise exact mapping_artifact_id retention.
  studio.installProxyFixture("a-v2", { proxyId: "proxy-a-v2-early", mapId: "map-a-v2-early", sampleFrame: 1 });
  studio.installProxyFixture("a-v2", { proxyId: "proxy-a-v2", mapId: "map-a-v2", sampleFrame: 12 });
  await studio.install({ catalogue: true, savedLayout: true });
  await studio.open();
  await studio.ready();

  // Open Asset A v2 and switch to 3D Inspection.
  await page.locator(".catalogue-item-header").filter({ hasText: "Asset A" }).click();
  const review = page.locator("main.review-panel");
  await expect(review.locator("h1")).toHaveText("Asset A v2");
  await review.getByRole("button", { name: "3D Inspection", exact: true }).click();

  const pinNoteBtn = review.getByRole("button", { name: "Pin note", exact: true });
  await expect(pinNoteBtn).toBeEnabled();
  const canvas = review.locator(".review-proxy canvas");
  await canvas.evaluate(el => { (el as HTMLElement).dataset.mountToken = "webgl-mounted-once"; });

  // Enable Pin note and verify drag-to-orbit does NOT place a pin.
  await pinNoteBtn.click();
  await expect(pinNoteBtn).toHaveAttribute("aria-pressed", "true");
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.5 + 48, box.y + box.height * 0.5 + 18, { steps: 6 });
  await page.mouse.up();
  await expect(review.getByTestId("proxy-spatial-pin")).toHaveCount(0);
  await expect(pinNoteBtn).toHaveAttribute("aria-pressed", "true");

  // Read live orbited camera from stored selection context to click exact Housing and Collar centres.
  const liveCam = await expect.poll(async () => {
    const raw = await page.evaluate(id => localStorage.getItem(`proscenium.owner.view.context.${id}`), projectId);
    return raw ? JSON.parse(raw).camera : null;
  }).not.toBeNull().then(async () => {
    const raw = await page.evaluate(id => localStorage.getItem(`proscenium.owner.view.context.${id}`), projectId);
    return JSON.parse(raw!).camera as { camera_to_world: number[]; projection: number[] };
  });

  // First click multi-primitive parent Housing (proxy [1.6, 0, -1.6] -> Blender [1.6, 1.6, 0]).
  const [housingU, housingV] = projectWorldToCanvasUv(liveCam, [1.6, 1.6, 0]);
  await page.mouse.click(box.x + housingU * box.width, box.y + housingV * box.height);
  await expect(review.locator('[aria-label="Spatial pin tools"]')).toContainText("housing · housing-1");
  expect(await canvas.evaluate(el => (el as HTMLElement).dataset.mountToken)).toBe("webgl-mounted-once");

  // Reposition onto rotated, non-uniformly scaled child Collar (proxy [-1.6, 0, 1.6] + y=0.2 -> Blender [-1.6, -1.6, 0.2]).
  // Re-measure canvas bounds after the bottom annotation dock resized the viewport height.
  await review.getByRole("button", { name: "Reposition pin", exact: true }).click();
  await expect.poll(async () => {
    const dims = await canvas.evaluate(el => ({ w: (el as HTMLCanvasElement).width, h: (el as HTMLCanvasElement).height, rectH: Math.round(el.getBoundingClientRect().height) }));
    return dims.h === dims.rectH ? dims.h : null;
  }).not.toBeNull();
  const collarBox = (await canvas.boundingBox())!;
  const dockedCam = {
    ...liveCam,
    projection: [liveCam.projection[5] / (collarBox.width / collarBox.height), ...liveCam.projection.slice(1)],
  };
  const [collarU, collarV] = projectWorldToCanvasUv(dockedCam, [-1.6, -1.6, 0.2]);
  const clickX = collarBox.x + collarU * collarBox.width;
  const clickY = collarBox.y + collarV * collarBox.height;
  await page.mouse.click(clickX, clickY);
  await expect(review.locator('[aria-label="Spatial pin tools"]')).toContainText("collar · collar-1");
  expect(await canvas.evaluate(el => (el as HTMLElement).dataset.mountToken)).toBe("webgl-mounted-once");

  // Orbiting moves the anchored 3D pin marker on screen; Restore pinned view brings it back.
  const pinMarker = review.getByTestId("proxy-spatial-pin");
  await expect(pinMarker).toBeVisible();
  const initialPinBox = (await pinMarker.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.45 - 90, box.y + box.height * 0.5 + 35, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => {
    const moved = await pinMarker.boundingBox();
    return moved ? Math.hypot(moved.x - initialPinBox.x, moved.y - initialPinBox.y) : 0;
  }).toBeGreaterThan(15);

  await review.getByRole("button", { name: "Restore pinned view", exact: true }).click();
  await expect.poll(async () => {
    const restored = await pinMarker.boundingBox();
    return restored ? Math.hypot(restored.x - initialPinBox.x, restored.y - initialPinBox.y) : 999;
  }).toBeLessThan(2);

  // Enter note and simulate a lost save acknowledgement followed by page reload.
  await review.getByRole("textbox", { name: "Feedback" }).fill("Chamfer the collar lip by 2 mm");
  studio.dropNextSaveAck = true;
  await review.getByRole("button", { name: "Save annotation", exact: true }).click();
  await expect(review.locator('[aria-label="Spatial pin tools"] output')).toContainText("failed");
  expect(studio.uploadCalls).toHaveLength(2);
  expect(studio.saveAnnotationCalls).toHaveLength(1);
  const originalOpId = studio.saveAnnotationCalls[0].operation_id;

  await page.reload();
  await studio.ready();
  const retryBtn = review.getByRole("button", { name: "Retry original save", exact: true });
  await expect(retryBtn).toBeVisible();
  await retryBtn.click();
  await expect(review.locator('[aria-label="Spatial pin tools"] output')).toHaveText("saved");
  // Zero duplicate image uploads, same operation_id replayed, and Save did NOT queue work.
  expect(studio.uploadCalls).toHaveLength(2);
  expect(studio.saveAnnotationCalls).toHaveLength(2);
  expect(studio.saveAnnotationCalls[1].operation_id).toBe(originalOpId);
  expect(studio.annotations).toHaveLength(1);
  expect(studio.submissions).toHaveLength(0);

  // Verify <= 0.5 image pixel pin placement accuracy and unit Blender-world surface normal.
  const saved = studio.annotations[0];
  const hit = saved.spatial_hits[0];
  expect(hit.instance_id).toBe("collar-1");
  expect(hit.mapping_artifact_id).toBe("map-a-v2");
  expect(hit.sample_frame).toBe(12);
  expect(Math.abs(Math.hypot(...hit.normal) - 1)).toBeLessThan(1e-4);
  const projectedUv = projectWorldToCanvasUv(hit.camera, hit.position);
  const [pinU, pinV] = saved.markups[0].points[0];
  const actualClickU = (clickX - collarBox.x) / collarBox.width;
  const actualClickV = (clickY - collarBox.y) / collarBox.height;
  expect(Math.abs(pinU - projectedUv[0]) * saved.target.dimensions.width).toBeLessThanOrEqual(0.5);
  expect(Math.abs(pinV - projectedUv[1]) * saved.target.dimensions.height).toBeLessThanOrEqual(0.5);
  expect(Math.abs(pinU - actualClickU) * saved.target.dimensions.width).toBeLessThanOrEqual(0.5);
  expect(Math.abs(pinV - actualClickV) * saved.target.dimensions.height).toBeLessThanOrEqual(0.5);

  // Publish Asset A v3, switch to v3, reopen the v2 spatial annotation, and confirm Queue targets v2!
  studio.addAssetVersion("a-v3", "Asset A v3");
  await review.getByRole("button", { name: "Close draft", exact: true }).click();
  await page.getByRole("button", { name: "Refresh project metadata" }).click();
  await page.locator(".catalogue-item-header").filter({ hasText: "Asset A" }).click();
  await expect(review.locator("h1")).toHaveText("Asset A v3");

  await review.getByRole("button", { name: /Annotations/ }).click();
  await review.locator(".review-drawer-items button").filter({ hasText: "Chamfer the collar lip by 2 mm" }).click();
  await expect(review.locator("h1")).toHaveText("Asset A v2");
  expect(studio.guard!.calls.some(c => c.url.includes("/versions/a-v2/proxy-mapping?mapping_artifact_id=map-a-v2"))).toBe(true);

  await review.getByRole("button", { name: "Queue feedback", exact: true }).click();
  await expect(review.getByRole("button", { name: "Feedback queued", exact: true })).toBeVisible();
  expect(studio.submissions).toHaveLength(1);
  const queuedCtx = studio.contexts.at(-1)!;
  expect(queuedCtx.version_id).toBe("a-v2");
  expect(queuedCtx.request_target).toEqual({ kind: "item", version: { item_id: "asset-a", version_id: "a-v2" } });
  expect(queuedCtx.view_mode).toBe("3d_inspection");
  expect(queuedCtx.selected_objects).toEqual([{ object_id: "collar", instance_id: "collar-1" }]);
  expect(queuedCtx.annotations[0].spatial_hits[0]).toMatchObject({
    version_id: "a-v2",
    mapping_artifact_id: "map-a-v2",
    proxy_artifact_id: "proxy-a-v2",
    object_id: "collar",
    instance_id: "collar-1",
  });

  // Make the original v2 proxy unavailable; reload shows the marked snapshot and blocks Queue until explicit fallback confirmation.
  studio.unavailableProxyIds.add("map-a-v2");
  await page.reload();
  await studio.ready();
  await expect(review.locator('[aria-label="Unavailable proxy marked snapshot"]')).toBeVisible();
  const fallbackQueueBtn = review.getByRole("button", { name: "Queue image-only feedback", exact: true });
  await expect(fallbackQueueBtn).toBeDisabled();
  await review.getByRole("checkbox", { name: "Send image-only snapshot without 3D spatial hit" }).check();
  await expect(fallbackQueueBtn).toBeEnabled();
  await fallbackQueueBtn.click();
  await expect(review.getByRole("button", { name: "Feedback queued", exact: true })).toBeVisible();
  expect(studio.submissions).toHaveLength(2);
  const fallbackCtx = studio.contexts.at(-1)!;
  expect(fallbackCtx.version_id).toBe("a-v2");
  expect(fallbackCtx.view_mode).toBe("keyframes");
  expect(fallbackCtx.selected_objects).toEqual([]);
  expect(fallbackCtx.annotations[0]).toMatchObject({
    spatial_hits: [],
    spatial_status: "unavailable",
    derived_from: saved.annotation_id,
  });

  studio.assertHermetic();
});
