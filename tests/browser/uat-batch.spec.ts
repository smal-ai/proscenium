import { expect, test } from "@playwright/test";
import type { Event, MessageSubmit } from "../../apps/frontend/src/api";
import { verifyLayout } from "./layout-journey";
import { base, deferred, job, projectId, request, retained, StudioFixture, work } from "./studio-fixture";

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
  await expect(page.getByRole("region", { name: "Resume Copilot" })).toBeVisible();
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

for (const stalled of [false, true]) {
test(stalled ? "Resume appears after the auto-release deadline while Queue admission is stalled" : "Queue submission on an idle restart hold leaves release to the backend", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.snapshot.execution = { ...studio.snapshot.execution!, fenced: true, hold_reason_code: null };
  const ack = deferred();
  await studio.install({ savedLayout: true, routes: [
    { method: "POST", path: `${base}/requests`, handler: async route => {
      const body = route.request().postDataJSON() as MessageSubmit;
      studio.submissions.push(body);
      const held = request(body.request_id, "Explicit Queue", "held");
      held.submission = body;
      studio.requests.push(held);
      studio.snapshot.held_requests = [held];
      studio.emit("request.accepted", held);
      await ack.promise;
      await route.fulfill({ status: 202, json: held.receipt });
    } },
  ] });
  if (stalled) await page.clock.install();
  await studio.open(); await studio.ready();
  const input = page.getByRole("textbox", { name: "Message Copilot" });
  const queue = page.getByRole("button", { name: "Queue message" });
  const resume = page.getByRole("region", { name: "Resume Copilot" });
  const banner = page.locator(".queue-hold");
  const status = page.locator(".composer-footer .assistant-status");
  await expect(resume).toHaveCount(0);
  await expect(banner).toHaveCount(0);
  await expect(status).toHaveAttribute("aria-label", "Ready");
  await input.fill("Explicit Queue"); await queue.click();
  try {
    await expect(page.getByRole("button", { name: "Cancel held request" })).toBeVisible();
    await expect(resume).toHaveCount(0);
    await expect(banner).toHaveCount(0);
    if (stalled) {
      await page.clock.fastForward(5_001);
      await expect(resume).toBeVisible();
      await expect(banner).toBeVisible();
      await expect(status).toHaveAttribute("aria-label", "Paused");
      await expect(input).toHaveValue("Explicit Queue");
    }
  } finally {
    ack.release();
  }
  await expect(input).toHaveValue("");
  await expect(resume).toHaveCount(stalled ? 1 : 0);
  await input.fill("Not submitted");
  await expect(queue).toBeEnabled();
  expect(studio.submissions).toHaveLength(1);
  expect(studio.guard!.calls.filter(call => call.method === "POST").map(call => call.url)).toEqual([
    `${base}/contexts`, `${base}/requests`,
  ]);
  const second = request("other-tab", "Another held request", "held");
  studio.requests.push(second);
  studio.snapshot.held_requests.push(second);
  studio.emit("request.accepted", second);
  await expect(resume).toBeVisible();
  await expect(banner).toBeVisible();
  await expect(status).toHaveAttribute("aria-label", "Paused");
  studio.assertHermetic();
});
}

const historyTime = (offset: number) => new Date(Date.UTC(2026, 10, 24) + offset * 1000).toISOString();
function longHistory(count: number): Event[] {
  return Array.from({ length: count }, (_, i): Event => ({
    schema_version: "1.0", project_id: projectId, sequence: i + 1,
    occurred_at: historyTime(i),
    event_type: i % 75 === 0 ? "work.state_changed" : "assistant.completed",
    actor: { kind: "lead", id: "lead" }, request_id: null, work_id: null,
    native_session_id: null, native_event_id: null,
    payload: i % 75 === 0 ? work("completed", i + 1) : { message_id: `reply-${i + 1}`, text: `Assistant reply ${i + 1}`, native_agent_id: null },
  } as Event));
}

test("large conversations show the newest wave before older pages without refetching metadata or shifting the reading position", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  // Two waves are necessary to hold older pages back while the first four finish.
  const count = 750;
  studio.snapshot.event_sequence = count;
  studio.events = longHistory(count);
  studio.requests = [request("early-request", "Earlier prompt", "completed", historyTime(50.5)), request("anchor", "Keep this prompt in view", "completed", historyTime(175.5))];
  const older = deferred();
  const requested: number[] = [];
  await studio.install({ savedLayout: true, routes: [{ path: `${base}/events`, handler: async route => {
    const query = new URL(route.request().url()).searchParams;
    const after = Number(query.get("after"));
    const limit = Number(query.get("limit"));
    requested.push(after);
    if (after < 350) await older.promise;
    const events = studio.events.slice(after, after + limit);
    await route.fulfill({ json: { schema_version: "1.0", events, next_after: events.at(-1)?.sequence ?? after, last_available: count } });
  } }] });
  try {
    await studio.open(); await studio.ready();
    expect(requested[0]).toBe(650);
    expect(requested.slice(0, 4).sort((a, b) => b - a)).toEqual([650, 550, 450, 350]);
    await expect(page.getByText("Assistant reply 750", { exact: true })).toBeInViewport();
    await expect(page.getByText("Assistant reply 2", { exact: true })).toHaveCount(0);
    const viewport = page.locator(".chat-scroll");
    const anchor = page.locator('[data-entry-key="request.anchor"]');
    await anchor.evaluate(el => { const viewport = el.closest(".chat-scroll")!; viewport.scrollTop += el.getBoundingClientRect().top - viewport.getBoundingClientRect().top - 1; });
    await expect(page.getByRole("button", { name: "Jump to latest" })).toBeVisible();
    const anchorY = (await anchor.boundingBox())!.y;
    studio.emit("assistant.completed", { message_id: "live-reply", text: "Live appended reply", native_agent_id: null }, historyTime(751));
    older.release();
    await expect(page.getByText("Assistant reply 2", { exact: true })).toBeAttached();
    await expect(viewport.getByText("Live appended reply", { exact: true })).toBeAttached();
    await expect.poll(async () => Math.abs((await anchor.boundingBox())!.y - anchorY)).toBeLessThan(2);
    expect(studio.guard!.calls.filter(call => call.url === `${base}/snapshot`)).toHaveLength(1);
    studio.assertHermetic();
  } finally { older.release(); }
});

test("events committed after a zero-watermark snapshot populate the live projection", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const accepted = request("new-request", "Queued just after hydration", "held");
  const [event] = longHistory(1);
  await studio.install({ savedLayout: true, routes: [{ path: `${base}/events`, handler: route => {
    studio.events = [{ ...event, event_type: "request.accepted", request_id: accepted.request_id, payload: accepted } as Event];
    return route.fulfill({ json: { schema_version: "1.0", events: studio.events, next_after: 1, last_available: 1 } });
  } }] });
  await studio.open(); await studio.ready();
  await expect(page.getByText("Queued just after hydration", { exact: true })).toBeVisible();
  expect(studio.guard!.calls.filter(call => call.url === `${base}/snapshot`)).toHaveLength(1);
  studio.assertHermetic();
});

test("middle backfill above the hydration watermark delivers request and publication updates", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.events = longHistory(750).map(event => event.sequence === 220
    ? { ...event, event_type: "request.accepted", payload: request("middle", "Middle wave request", "held", historyTime(220)) } as Event
    : event.sequence === 221 ? { ...event, event_type: "publication.created", payload: {} } as Event : event);
  const older = deferred();
  await studio.install({ savedLayout: true, routes: [{ path: `${base}/events`, handler: async route => {
    const query = new URL(route.request().url()).searchParams;
    const after = Number(query.get("after")), limit = Number(query.get("limit"));
    if (after >= 100 && after < 350) await older.promise;
    const events = studio.events.slice(after, after + limit);
    await route.fulfill({ json: { schema_version: "1.0", events, next_after: events.at(-1)?.sequence ?? after, last_available: 750 } });
  } }] });
  try {
    await studio.open(); await studio.ready();
    await expect(page.getByText("Assistant reply 750", { exact: true })).toBeVisible();
    await expect(page.getByText("Middle wave request", { exact: true })).toHaveCount(0);
    // Wait for both hydrations before measuring the publication invalidation.
    await expect.poll(() => studio.guard!.calls.filter(c => c.url === `${base}/snapshot`).length).toBeGreaterThanOrEqual(2);
    const itemReads = () => studio.guard!.calls.filter(c => c.url.startsWith(`${base}/items?`)).length;
    await expect.poll(itemReads).toBeGreaterThanOrEqual(2);
    const before = itemReads();
    older.release();
    await expect(page.getByText("Middle wave request", { exact: true })).toBeVisible();
    await expect.poll(itemReads).toBeGreaterThan(before);
    studio.assertHermetic();
  } finally { older.release(); }
});

for (const persistent of [false, true]) {
  test(`replacement watermark expiry ${persistent ? "fails visibly after a bounded retry" : "retries with fresh projections"}`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    studio.snapshot.event_sequence = 30;
    studio.events = longHistory(30);
    studio.requests = [request("old", "Expired generation prompt")];
    studio.snapshot.active_work = work("running", 5);
    studio.snapshot.execution!.fenced = true;
    let expiredReads = 0;
    await studio.install({ savedLayout: true, routes: [{ path: `${base}/events`, handler: route => {
      const query = new URL(route.request().url()).searchParams;
      const after = Number(query.get("after")), limit = Number(query.get("limit"));
      if (limit === 20 || (persistent && limit === 10)) {
        expiredReads++;
        studio.snapshot.event_sequence = 10;
        studio.events = longHistory(10);
        studio.requests = [request("replacement", "Current generation prompt")];
        studio.snapshot.active_work = null;
        studio.snapshot.execution!.fenced = false;
        return route.fulfill({ status: 410, json: { schema_version: "1.0", error: { code: "cursor_expired", message: "Replacement cursor expired" } } });
      }
      const events = studio.events.slice(after, after + limit);
      return route.fulfill({ json: { schema_version: "1.0", events, next_after: events.at(-1)?.sequence ?? after, last_available: studio.events.length } });
    } }] });
    await studio.open(); await studio.ready();
    await expect(page.getByText("Expired generation prompt", { exact: true })).toBeVisible();
    studio.snapshot.event_sequence = 20;
    studio.events = longHistory(20);
    studio.sockets.at(-1)!.send(JSON.stringify({ schema_version: "1.0", type: "resync_required", project_id: projectId, reason: "cursor_expired" }));
    if (persistent) {
      await expect(page.getByRole("alert")).toContainText("Replacement cursor expired");
      await expect(page.getByText("Current generation prompt", { exact: true })).toHaveCount(0);
    } else {
      await expect(page.getByText("Current generation prompt", { exact: true })).toBeVisible();
      await expect(page.getByText("Assistant reply 10", { exact: true })).toBeVisible();
    }
    expect(expiredReads).toBe(persistent ? 2 : 1);
    await expect(page.getByText("Expired generation prompt", { exact: true })).toHaveCount(0);
    await expect(page.locator(".copilot-identity small")).toHaveText("No active work");
    await expect(page.getByRole("region", { name: "Resume Copilot" })).toHaveCount(0);
    studio.assertHermetic();
  });
}

test("a collapsed tool group remains anchored when older members change its key", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.snapshot.event_sequence = 750;
  studio.events = longHistory(750).map(event => event.sequence >= 340 && event.sequence <= 360 ? {
    ...event, event_type: "tool.completed",
    payload: { invocation_id: `step-${event.sequence}`, name: "view", state: "completed", summary: "", artifact_ids: [], model_id: null },
  } as Event : event);
  const older = deferred();
  await studio.install({ savedLayout: true, routes: [{ path: `${base}/events`, handler: async route => {
    const query = new URL(route.request().url()).searchParams;
    const after = Number(query.get("after")), limit = Number(query.get("limit"));
    if (after < 350) await older.promise;
    const events = studio.events.slice(after, after + limit);
    await route.fulfill({ json: { schema_version: "1.0", events, next_after: events.at(-1)?.sequence ?? after, last_available: 750 } });
  } }] });
  try {
    await studio.open(); await studio.ready();
    const group = page.locator(".activity-group");
    await expect(group).toContainText("10 tool steps completed");
    await group.evaluate(el => { const viewport = el.closest(".chat-scroll")!; viewport.scrollTop += el.getBoundingClientRect().top - viewport.getBoundingClientRect().top; });
    await expect(page.getByRole("button", { name: "Jump to latest" })).toBeVisible();
    const y = (await group.boundingBox())!.y;
    older.release();
    await expect(group).toContainText("21 tool steps completed");
    await expect.poll(async () => Math.abs((await group.boundingBox())!.y - y)).toBeLessThan(2);
    studio.assertHermetic();
  } finally { older.release(); }
});

test("journal resync replaces older projections with a lower watermark", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.events = longHistory(12);
  studio.snapshot.event_sequence = 12;
  studio.snapshot.active_work = work("running", 5);
  studio.snapshot.execution = { ...studio.snapshot.execution!, fenced: true, hold_reason_code: "provider_unavailable" };
  studio.requests = [request("stale", "Stale request")];
  await studio.install({ savedLayout: true }); await studio.open(); await studio.ready();
  await expect(page.getByText("Stale request", { exact: true })).toBeVisible();
  await expect(page.locator(".copilot-identity small")).toHaveText("running");
  await expect(page.getByRole("region", { name: "Resume Copilot" })).toBeVisible();
  studio.events = longHistory(3);
  studio.requests = [request("replacement", "Replacement request")];
  studio.snapshot.event_sequence = 3;
  studio.snapshot.active_work = null;
  studio.snapshot.execution = { ...studio.snapshot.execution!, fenced: false, queue_released: true, hold_reason_code: null };
  studio.sockets.at(-1)!.send(JSON.stringify({ schema_version: "1.0", type: "resync_required", project_id: projectId, reason: "cursor_expired" }));
  await expect(page.getByText("Replacement request", { exact: true })).toBeVisible();
  await expect(page.getByText("Stale request", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Assistant reply 12", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Assistant reply 3", { exact: true })).toBeVisible();
  await expect(page.locator(".copilot-identity small")).toHaveText("No active work");
  await expect(page.locator(".composer-footer .assistant-status")).toHaveAttribute("aria-label", "Ready");
  await expect(page.getByRole("region", { name: "Resume Copilot" })).toHaveCount(0);
  await expect.poll(() => page.evaluate(id => localStorage.getItem(`proscenium.owner.cursor.${id}`), projectId)).toBe("3");
  studio.assertHermetic();
});

for (const status of [500, 410]) {
  test(`older backfill recovers after HTTP ${status}`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    studio.snapshot.event_sequence = 750;
    studio.events = longHistory(750);
    let failed = false;
    let oldestReads = 0;
    const older = deferred();
    await studio.install({ savedLayout: true, routes: [{ path: `${base}/events`, handler: async route => {
      const query = new URL(route.request().url()).searchParams;
      const after = Number(query.get("after"));
      const limit = Number(query.get("limit"));
      if (after === 0) {
        oldestReads++;
        if (!failed) {
          await older.promise;
          failed = true;
          return route.fulfill({ status, json: { schema_version: "1.0", error: { code: status === 410 ? "cursor_expired" : "fixture_error", message: "Older history unavailable" } } });
        }
      }
      const events = studio.events.slice(after, after + limit);
      await route.fulfill({ json: { schema_version: "1.0", events, next_after: events.at(-1)?.sequence ?? after, last_available: studio.events.length } });
    } }] });
    try {
      await studio.open(); await studio.ready();
      await expect(page.getByText("Assistant reply 750", { exact: true })).toBeVisible();
      older.release();
      if (status === 500) {
        await expect(page.getByRole("alert")).toContainText("Older history unavailable");
        await page.getByRole("button", { name: "Refresh project metadata" }).click();
      }
      await expect(page.getByText("Assistant reply 2", { exact: true })).toBeAttached();
      await expect(page.getByRole("alert").filter({ hasText: "Older history unavailable" })).toHaveCount(0);
      expect(oldestReads).toBe(2);
      expect(studio.guard!.calls.filter(call => call.url === `${base}/snapshot`)).toHaveLength(2);
      studio.assertHermetic();
    } finally { older.release(); }
  });
}

for (const active of ["running", "awaiting_user"] as const) {
  test(`Queue behind ${active} work is cancellable without showing Resume`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    studio.snapshot.active_work = work(active);
    studio.snapshot.execution!.hold_reason_code = active === "running" ? "active_work_running" : "awaiting_user";
    await studio.install({ savedLayout: true, routes: [
      { method: "POST", path: `${base}/requests`, handler: async route => {
        const body = route.request().postDataJSON() as MessageSubmit;
        studio.submissions.push(body);
        const held = request(body.request_id, "Follow-up", "held");
        held.submission = body;
        studio.requests.push(held);
        studio.snapshot.held_requests = [held];
        studio.emit("request.accepted", held);
        await route.fulfill({ status: 202, json: held.receipt });
      } },
    ] });
    await studio.open(); await studio.ready();
    await page.getByRole("textbox", { name: "Message Copilot" }).fill("Follow-up");
    await page.getByRole("button", { name: "Queue message" }).click();
    await expect(page.getByRole("button", { name: "Cancel held request" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Resume Copilot" })).toHaveCount(0);
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
  const beforeRefresh = studio.guard!.calls.filter(c => c.url === `${base}/snapshot`).length;
  const staleResponse = page.waitForResponse(async response => response.url().endsWith(`${base}/snapshot`) && (await response.json()).active_work?.state === "running");
  await page.getByRole("button", { name: "Refresh project metadata" }).click();
  // The request handler freezes running state before the newer event is delivered.
  await expect.poll(() => studio.guard!.calls.filter(c => c.url === `${base}/snapshot`).length).toBeGreaterThan(beforeRefresh);
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
