import { expect, test } from "@playwright/test";
import { verifyLayout } from "./layout-journey";
import { base, deferred, job, projectId, request, retained, StudioFixture, work } from "./studio-fixture";
import type { MessageSubmit, ResumeCommand } from "../../apps/frontend/src/api";

test("hydration gates onboarding and starter intents never send or overwrite drafts", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const delayed = deferred(); studio.snapshotGate = delayed.promise; studio.requestsGate = delayed.promise;
  await studio.install({ created: true }); await studio.open();
  await expect(page.getByRole("button", { name: "Collapse Navigator" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Chat focus", exact: true })).toBeVisible();
  expect(await page.evaluate(id => localStorage.getItem(`proscenium.owner.layout.v1.${id}`), projectId)).toBeNull();
  delayed.release(); studio.snapshotGate = null; studio.requestsGate = null;
  await studio.ready();
  await expect(page.locator("main.review-panel")).toBeHidden();
  await expect(page.getByRole("button", { name: "Expand Navigator" })).toBeVisible();
  const input = page.getByRole("textbox", { name: "Message Copilot" });
  for (const [label, kind] of [["Plan a storyboard", "storyboard"], ["Create an asset", "asset"], ["Build a scene", "scene"]]) {
    await input.fill(""); await page.getByRole("button", { name: label, exact: true }).click();
    await expect(input).not.toHaveValue(""); await expect(input).toBeFocused();
    const target = await page.evaluate(id => JSON.parse(localStorage.getItem(`proscenium.owner.target.${id}`)!), projectId);
    expect(target).toMatchObject({ kind: "new_item", item_kind: kind });
    await input.fill("Keep my own wording"); await page.getByRole("button", { name: label, exact: true }).click();
    await expect(input).toHaveValue("Keep my own wording");
  }
  expect(studio.submissions).toEqual([]); studio.assertHermetic();
});

test("DPR 1 splitters, remembered tabs, layout and conversation following", async ({ page, baseURL }) => {
  await verifyLayout(page, baseURL!, 1);
});

test("hidden decoded Review preserves mode, frame and local annotation draft", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ catalogue: true, savedLayout: true }); await studio.open(); await studio.ready();
  await page.locator(".catalogue-item-header").filter({ hasText: "Asset A" }).click();
  await expect(page.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  const review = page.locator("main.review-panel");
  await expect(review.locator("img")).toBeVisible();
  const before = await review.innerText();
  await page.getByRole("button", { name: "Chat focus", exact: true }).click();
  await page.locator(".brand").click();
  for (const key of ["1", "2", "3", "Space", "ArrowLeft", "ArrowRight"]) await page.keyboard.press(key);
  await page.getByRole("button", { name: "Restore split view" }).click();
  expect(await review.innerText()).toBe(before);
  await expect(page.getByRole("button", { name: "Keyframes" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Annotate presented frame" }).click();
  const annotation = page.getByRole("textbox", { name: "Feedback" });
  // The real editor's labelled input is inspected, rather than injecting app state.
  await annotation.fill("Keep this local annotation");
  await page.getByRole("button", { name: "Chat focus", exact: true }).click();
  await page.locator(".brand").click(); await page.keyboard.press("3");
  await page.getByRole("button", { name: "Restore split view" }).click();
  await expect(annotation).toHaveValue("Keep this local annotation");
  expect(studio.guard?.calls.filter(c => c.method === "POST")).toEqual([]); studio.assertHermetic();
});

test("explicit target and historical references stay isolated during async submit", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ catalogue: true, savedLayout: true }); await studio.open(); await studio.ready();
  await page.locator(".catalogue-item-header").filter({ hasText: "Asset A" }).click();
  await expect(page.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  await page.getByRole("button", { name: "Annotate presented frame" }).click();
  await page.getByRole("textbox", { name: "Feedback" }).fill("Unrelated hidden annotation must never flush");
  await page.getByRole("button", { name: "Chat focus", exact: true }).click();
  await page.getByRole("button", { name: "Choose request target and references" }).click();
  await page.locator(".target-references-disclosure > summary").click();
  await page.getByLabel("Reference item", { exact: true }).selectOption("asset-a");
  await page.getByLabel("Reference version", { exact: true }).selectOption("a-v1");
  await page.getByRole("button", { name: "Add reference version" }).click();
  await page.getByRole("button", { name: "Existing…", exact: true }).click();
  await page.getByLabel("Published item", { exact: true }).selectOption("board-b");
  await page.getByLabel("Target storyboard shot").selectOption("shot-1");
  await page.getByRole("textbox", { name: "Message Copilot" }).fill("Use B shot one with A v1");
  const delayed = deferred(); studio.contextGate = delayed.promise;
  await page.getByRole("button", { name: "Queue message", exact: true }).click();
  await expect.poll(() => studio.contexts.length).toBe(1);
  await page.getByRole("button", { name: "Version history for Asset A" }).click();
  await page.locator(".catalogue-version-row").getByText("v1", { exact: true }).click();
  delayed.release();
  await expect.poll(() => studio.submissions.length).toBe(1);
  expect(studio.contexts[0]).toMatchObject({ request_target: { kind: "shot", storyboard_version: { item_id: "board-b", version_id: "b-v1" }, shot_id: "shot-1" }, reference_versions: [{ item_id: "asset-a", version_id: "a-v1" }], view_mode: "none", selected_objects: [], target: null, camera: null, range: null, attachments: [], annotations: [] });
  expect(studio.guard?.calls.filter(c => c.method === "POST").map(c => c.url)).toEqual([`${base}/contexts`, `${base}/requests`]);
  studio.assertHermetic();
});

test("Navigator asset-first creation and retained verified versus legacy PNGs", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  const outputs = page.getByRole("region", { name: "Retained outputs" });
  await expect(outputs.getByText(/Attribution unrecorded/)).toBeVisible();
  await expect(outputs.getByText(/Verified/)).toBeVisible();
  await expect(outputs.getByText(/Concept images are not 3D meshes/)).toBeVisible();
  for (const image of await outputs.locator("img").all()) expect(await image.getAttribute("src")).toMatch(new RegExp(`${base}/artifacts/(verified|legacy)/content`));
  await page.getByRole("button", { name: "New asset", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Message Copilot" })).toBeFocused();
  await expect(page.getByRole("button", { name: "Choose request target and references" })).toContainText("New asset");
  expect(studio.submissions).toEqual([]); studio.assertHermetic();
});

test("send mode is deliberate, stale Steer stays guarded, held cancellation and Resume polling", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!); studio.snapshot.active_work = work(); studio.snapshot.execution!.can_steer_work_id = "work";
  studio.requests = [request("held", "Held request", "held")];
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  const input = page.getByRole("textbox", { name: "Message Copilot" }); await input.fill("Retain this steer draft");
  await page.getByRole("button", { name: "Select send mode" }).click();
  await page.getByRole("menuitemradio", { name: /Steer/ }).click();
  expect(studio.submissions).toEqual([]);
  await page.getByLabel("Steer active work target").selectOption("work");
  await expect(page.getByRole("button", { name: "Steer active work", exact: true })).toBeEnabled();
  await studio.setWork("awaiting_resume", true);
  await expect(page.getByRole("button", { name: "Steer active work", exact: true })).toBeDisabled();
  await expect(input).toHaveValue("Retain this steer draft");
  await page.getByRole("button", { name: "Switch to Queue" }).click();
  await expect(page.locator(".queue-hold")).toContainText("Execution awaits explicit Resume");
  await page.getByRole("button", { name: "Cancel held request" }).click();
  await expect.poll(() => studio.requests[0].receipt.state).toBe("cancelled");
  expect(studio.submissions).toEqual([]);
  await page.getByLabel("Resume mode").selectOption("continue_work");
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await expect.poll(() => studio.resumePolls).toBeGreaterThan(0);
  await expect(page.getByRole("region", { name: "Resume awaiting confirmation" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Resume", exact: true })).toBeDisabled();
  studio.resumeComplete = true;
  await expect(page.getByRole("region", { name: "Resume awaiting confirmation" })).toHaveCount(0);
  expect(studio.resumePolls).toBeGreaterThan(1);
  studio.assertHermetic();
});

for (const intervening of ["none", "another Queue", "confirmed Stop"] as const) {
  test(`idle restart Queue auto-release with ${intervening}`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    studio.snapshot.execution = { ...studio.snapshot.execution!, fenced: true, hold_reason_code: "awaiting_resume", queue_released: false };
    studio.snapshot.project = { ...studio.snapshot.project, state_revision: 3 };
    studio.resumeComplete = true;
    const resumes: ResumeCommand[] = [];
    let replayedAfterSubmission = false;
    await studio.install({ savedLayout: true, routes: [
      { method: "POST", path: `${base}/requests`, handler: route => {
        const submission = route.request().postDataJSON() as MessageSubmit;
        studio.submissions.push(submission);
        const submitted = request(submission.request_id, "Explicit Queue", "held");
        submitted.submission = submission;
        const other = request("other-tab", "An earlier Queue", "held");
        studio.snapshot.held_requests = intervening === "another Queue" ? [other, submitted] : [submitted];
        studio.requests = [...studio.snapshot.held_requests];
        studio.snapshot.project.state_revision = 7;
        if (intervening === "confirmed Stop") studio.emit("work.state_changed", work("stopped"));
        studio.emit("request.accepted", submitted);
        return route.fulfill({ status: 202, json: submitted.receipt });
      } },
      { path: `${base}/events`, handler: route => {
        const query = new URL(route.request().url()).searchParams;
        const after = Number(query.get("after") ?? 0);
        if (studio.submissions.length) replayedAfterSubmission = true;
        const events = studio.events.filter(event => event.sequence > after).slice(0, Number(query.get("limit") ?? 100));
        return route.fulfill({ json: { schema_version: "1.0", events, next_after: events.at(-1)?.sequence ?? after, last_available: studio.events.length } });
      } },
      { method: "POST", path: `${base}/resume`, handler: route => {
        resumes.push(route.request().postDataJSON() as ResumeCommand);
        return route.fulfill({ status: 202, json: { state: "accepted" } });
      } },
    ] });
    await studio.open(); await studio.ready();
    const input = page.getByRole("textbox", { name: "Message Copilot" });
    const queue = page.getByRole("button", { name: "Queue message" });
    await input.fill("Explicit Queue"); await queue.click();
    await expect(input).toHaveValue("");
    await input.fill("Not submitted");
    await expect(queue).toBeEnabled(); // The first submission and its automatic recovery check finished.
    expect(studio.submissions).toHaveLength(1);
    if (intervening === "none") {
      expect(resumes).toHaveLength(1);
      expect(resumes[0]).toMatchObject({ mode: "release_held_queue", work_id: null, expected_revision: 7 });
    } else {
      expect(resumes).toEqual([]);
    }
    if (intervening !== "another Queue") expect(replayedAfterSubmission).toBe(true);
    studio.assertHermetic();
  });
}

test("Resume stays visible above the Composer while scrolling a long conversation", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.snapshot.active_work = work("awaiting_resume");
  studio.snapshot.execution!.fenced = true;
  studio.requests = Array.from({ length: 40 }, (_, i) => request(`history-${i}`, `Earlier prompt ${i}. ${"Storyboard detail. ".repeat(25)}`));
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  const input = page.getByRole("textbox", { name: "Message Copilot" });
  const resume = page.getByRole("button", { name: "Resume", exact: true });
  const scroll = page.locator(".chat-scroll");
  await input.fill("Keep this UAT draft");
  await expect.poll(() => scroll.evaluate(element => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(2);
  await expect(resume).toBeInViewport();
  const before = await resume.boundingBox();
  await scroll.hover(); await page.mouse.wheel(0, -700);
  await expect(page.getByRole("button", { name: "Jump to latest" })).toBeVisible();
  await expect(resume).toBeInViewport();
  expect((await resume.boundingBox())!.y).toBeCloseTo(before!.y, 0);
  await page.getByRole("button", { name: /^Context/ }).click();
  await expect(resume).toBeInViewport();
  await page.getByRole("button", { name: "Back to message" }).click();
  await expect(input).toHaveValue("Keep this UAT draft");
  await page.getByLabel("Resume mode").selectOption("continue_work");
  await expect(resume).toBeInViewport();
  await resume.click();
  const pending = page.getByRole("region", { name: "Resume awaiting confirmation" });
  await expect(pending).toBeInViewport();
  await expect.poll(() => studio.resumePolls).toBeGreaterThan(0);
  studio.resumeComplete = true;
  await expect(pending).toHaveCount(0);
  await expect(input).toHaveValue("Keep this UAT draft");
  expect(studio.submissions).toEqual([]);
  studio.assertHermetic();
});

test("fixed Composer footer and collapsed rail retain work on disconnect", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  await expect(page.locator(".assistant-heading .assistant-status")).toHaveCount(0);
  const status = page.locator(".composer-footer .assistant-status");
  await expect(status).toHaveAttribute("aria-label", "Ready");
  for (const [state, label, fenced] of [["running", "Working…", false], ["awaiting_resume", "Paused", true], ["stop_requested", "Stopping…", true], ["outcome_uncertain", "Needs recovery", true]] as const) {
    await studio.setWork(state, fenced); await expect(status).toHaveAttribute("aria-label", label);
    await page.getByRole("button", { name: "Collapse Assistant" }).click();
    await expect(page.locator(".right-panel .panel-rail .assistant-status")).toHaveAttribute("aria-label", label);
    await page.getByRole("button", { name: "Expand Assistant" }).click();
  }
  studio.snapshot.pending_decisions = [{ decision_id: "decision", state: "pending", state_revision: 1, kind: "user_input", prompt: "Synthetic question", options: [], fingerprint: "synthetic" } as unknown as NonNullable<typeof studio.snapshot>["pending_decisions"][number]];
  await studio.setWork("awaiting_user"); await expect(status).toHaveAttribute("aria-label", "Needs your input");
  studio.snapshot.pending_decisions = [];
  await studio.setWork("running"); await expect(status).toHaveAttribute("aria-label", "Working…");
  studio.emit("subagent.started", { invocation_id: "child", name: "authoring-specialist", display_name: "Retained child", state: "running", summary: "", artifact_ids: [], model_id: "synthetic" });
  studio.sockets.at(-1)!.close({ code: 1000, reason: "Synthetic disconnect" });
  await expect(status).toHaveAttribute("aria-label", "Working · Retained child… · reconnecting");
  await expect(page.getByRole("button", { name: /^Agents/ })).toHaveText("Agents1?");
  studio.assertHermetic();
});

test("awaiting-user status is not falsely reported as Ready without a decision projection", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.snapshot.active_work = work("awaiting_user");
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  await expect(page.locator(".assistant-heading .assistant-status")).toHaveCount(0);
  await expect(page.locator(".composer-footer .assistant-status")).toHaveAttribute("aria-label", "Needs your input");
});

test("a fresh native child event has a known live Agents count", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.snapshot.active_work = work();
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  studio.emit("subagent.started", { invocation_id: "child", name: "authoring-specialist", display_name: "Layout shot one", state: "running", summary: "", artifact_ids: [], model_id: "synthetic" });
  await expect(page.getByRole("button", { name: /^Agents/ })).toHaveText("Agents1");
});

test("Jobs Context Agents replace textarea and restore draft caret plus accepted history", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.snapshot.jobs = [job("waiting", "waiting_resource"), job("cancelling", "cancel_requested"), job("uncertain", "outcome_uncertain"), job("finished", "succeeded")];
  const steer = request("steer", "Accepted Steer");
  steer.submission = { schema_version: "1.0", project_id: projectId, request_id: "steer", type: "steer", target_work_id: "work", text: "Accepted Steer", context_id: "context" };
  studio.requests = [request("old", "Accepted Queue", "completed", "2026-11-15T07:59:59Z"), steer, request("cancelled", "Accepted then cancelled", "cancelled")];
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  studio.emit("tool.started", { invocation_id: "wrapper", name: "task", state: "running", summary: "", artifact_ids: [], model_id: null });
  studio.emit("subagent.started", { invocation_id: "child", name: "authoring-specialist", display_name: "Layout shot one", state: "running", summary: "", artifact_ids: [], model_id: "synthetic" });
  const input = page.getByRole("textbox", { name: "Message Copilot" }); await input.fill("Unsent draft\nSecond line");
  await input.evaluate(e => (e as HTMLTextAreaElement).setSelectionRange(3, 3));
  for (const panel of ["Jobs", "Context", "Agents"]) {
    await page.getByRole("button", { name: new RegExp(`^${panel}`) }).click();
    await expect(input).toHaveCount(0);
    if (panel === "Agents") { await expect(page.getByText("Layout shot one", { exact: true }).last()).toBeVisible(); await expect(page.getByRole("button", { name: /^Agents/ })).toContainText("1"); }
    if (panel === "Jobs") {
      await expect(page.getByRole("button", { name: /^Jobs/ })).toHaveText("Jobs1 uncertain · 1 running · 1 queued?");
      const history = page.locator(".jobs-finished-group");
      await expect(history.getByText("Finished (1)")).toBeVisible();
      await expect(history).toHaveJSProperty("open", false);
    }
    await page.getByRole("button", { name: "Back to message" }).click();
    await expect(input).toHaveValue("Unsent draft\nSecond line"); await expect(input).toBeFocused();
    expect(await input.evaluate(e => (e as HTMLTextAreaElement).selectionStart)).toBe(3);
  }
  await input.evaluate(e => { const input = e as HTMLTextAreaElement; input.setSelectionRange(input.value.length, input.value.length); }); await input.press("ArrowUp");
  await expect(input).toHaveValue("Unsent draft\nSecond line");
  await input.evaluate(e => (e as HTMLTextAreaElement).setSelectionRange(0, 0)); await input.press("ArrowUp");
  await expect(input).toHaveValue("Accepted Steer"); await input.press("ArrowUp");
  await expect(input).toHaveValue("Accepted then cancelled"); await input.press("ArrowUp");
  await expect(input).toHaveValue("Accepted Queue"); await input.press("ArrowDown"); await input.press("ArrowDown");
  await input.press("ArrowDown");
  await expect(input).toHaveValue("Unsent draft\nSecond line");
  await input.evaluate(e => { (e as HTMLTextAreaElement).setSelectionRange(0, 0); e.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", isComposing: true, bubbles: true })); });
  await expect(input).toHaveValue("Unsent draft\nSecond line");
  studio.emit("subagent.completed", { invocation_id: "child", name: "authoring-specialist", display_name: "Layout shot one", state: "completed", summary: "", artifact_ids: [], model_id: "synthetic" });
  await expect(page.getByRole("button", { name: /^Agents/ })).toHaveText("Agents0");
  studio.assertHermetic();
});

test("conversation UTC ordering, blank suppression and safe publication-blocked output cards", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.retained.push(retained("html", "verified", "text/html"), retained("svg", "verified", "image/svg+xml"));
  studio.snapshot.execution!.work_outputs = [{ work_id: "work", request_id: "initial", status: "publication_blocked", summary: "Storyboard source must be AuthoredShotPlan JSON", reason_code: "schema_invalid", artifact_ids: ["verified", "html", "svg"], published_versions: [] }];
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  studio.emit("assistant.completed", { message_id: "blank", text: "", native_agent_id: null });
  studio.emit("assistant.completed", { message_id: "later", text: "Later UTC", native_agent_id: null }, "2026-11-15T08:00:01+00:00");
  studio.emit("assistant.completed", { message_id: "earlier", text: "Earlier UTC", native_agent_id: null }, "2026-11-15T08:00:00Z");
  await expect(page.locator(".assistant-message")).toHaveCount(2);
  expect(await page.locator(".assistant-message p").allTextContents()).toEqual(["Earlier UTC", "Later UTC"]);
  await expect(page.getByText("Finished · publication blocked", { exact: true })).toBeVisible();
  const cards = page.locator(".publication-status .retained-output-card");
  await expect(cards).toHaveCount(3); await expect(cards.locator("img")).toHaveCount(1);
  await expect(cards.locator("iframe,svg")).toHaveCount(0); await expect(cards.getByRole("link", { name: "Download output" })).toHaveCount(3);
  studio.assertHermetic();
});

test("delayed older snapshot cannot resurrect a completed work event", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!); studio.snapshot.active_work = work();
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  const delayed = deferred(); studio.snapshotGate = delayed.promise;
  const staleResponse = page.waitForResponse(async response => response.url().endsWith(`${base}/snapshot`) && (await response.json()).active_work?.state === "running");
  await page.getByRole("button", { name: "Refresh project metadata" }).click();
  // The request handler freezes running state before the newer event is delivered.
  await expect.poll(() => studio.guard!.calls.filter(c => c.url === `${base}/snapshot`).length).toBeGreaterThan(2);
  await studio.setWork(null);
  await expect(page.locator(".copilot-identity small")).toHaveText("No active work");
  delayed.release(); studio.snapshotGate = null;
  await (await staleResponse).finished();
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.locator(".assistant-heading .assistant-status")).toHaveCount(0);
  await expect(page.locator(".composer-footer .assistant-status")).toHaveAttribute("aria-label", "Ready");
  await expect(page.locator(".copilot-identity small")).toHaveText("No active work");
  studio.assertHermetic();
});
