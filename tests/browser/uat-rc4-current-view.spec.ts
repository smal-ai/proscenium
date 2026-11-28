import { createHash } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import type { ContextSnapshot, FrameAvailability, FrameReceipt, JobRecord, ShotPlan } from "../../apps/frontend/src/api";
import type { ContextPreparer } from "../../apps/frontend/src/api/studio";
import type { MockRoute } from "./fixtures";
import { base, job, pageOf, png, projectId, retained, StudioFixture, version } from "./studio-fixture";

const kit = version("asset-signal-lost-native-kit", "signal-lost-native-kit-v3", "Signal Lost Native Kit");
const scene = version("scene-signal-lost-animation", "signal-lost-animation-v1", "Signal Lost Animation", "scene");
const sequence = version("sequence-signal-lost-animation-draft", "signal-lost-animation-draft-v1", "Signal Lost Animation Draft", "sequence");
const board = version("storyboard-signal-lost-orbital-relay", "signal-lost-orbital-relay-v4", "Signal Lost Orbital Relay", "storyboard");
const sceneRef = { item_id: scene.item_id, version_id: scene.version_id };
const shotId = "sl-01-dormant-macro";
const secondShotId = "sl-02-conduit";
const macroJobId = `preview-${shotId}`;

async function openStudio(page: Page, origin: string) {
  const studio = new StudioFixture(page, origin);
  studio.versionRecords = [kit, scene, { ...sequence, derived_from: [sceneRef] }, board];
  studio.snapshot.items = studio.versionRecords.map(v => ({
    schema_version: "1.0", project_id: projectId, item_id: v.item_id, kind: v.kind, name: v.name,
    current_version_id: v.version_id, selected_version_id: null, state_revision: 1,
  }));
  const routes: MockRoute[] = [];
  const receiptsByJob = new Map<string, FrameReceipt[]>();
  const sha256 = createHash("sha256").update(png).digest("hex");
  const dimensions = { width: 1, height: 1 };
  const fps = { numerator: 24, denominator: 1 };

  function addPass(attempt: number, ordinals: number[], shot: string, frames: { start: number; end: number }) {
    const id = `preview-${shot}`;
    const previousAttemptId = id === macroJobId ? "attempt-1" : `attempt-${id}`;
    const render: JobRecord = {
      ...job(id, "running"), kind: "render", target: { target_id: shot, version: sceneRef, shot_id: shot },
      availability: "partial", attempt_number: attempt, attempt_id: attempt === 2 ? "attempt-2" : previousAttemptId, expected_frames: frames.end - frames.start + 1, completed_frames: ordinals.length,
      render_spec: { intent_id: shot, shot_id: shot, purpose: "draft_motion", source_closure_digest: scene.source.digest, scene_name: scene.name, camera: null, frames, fps, dimensions, engine: "Cycles", device: "CPU", samples: 32, colour_management: { view_transform: "AgX", look: "None", exposure: 0, gamma: 1 }, renderer_revision: "synthetic" },
    };
    const receipts = ordinals.map((source_frame): FrameReceipt => {
      const artifact_id = `${id}-f${source_frame}`;
      const summary = retained(artifact_id, "verified");
      Object.assign(summary.artifact, { purpose: "draft_motion", sha256, dimensions, source_version_id: scene.version_id });
      studio.retained.push({ ...summary, job_id: id });
      render.artifact_ids.push(artifact_id);
      return { schema_version: "1.0", receipt_id: `${previousAttemptId}-f${source_frame}`, project_id: projectId, job_id: id, attempt_id: previousAttemptId, version_id: scene.version_id, shot_id: shot, source_frame, artifact_id, output_path: `${artifact_id}.png`, sha256, dimensions, input_digest: render.input_digest };
    });
    if (attempt === 2) {
      const opening = receipts.find(receipt => receipt.source_frame === 1)!;
      receipts.splice(1, 0, { ...opening, receipt_id: "attempt-2-f1", attempt_id: render.attempt_id });
    }
    const last = ordinals.at(-1)!;
    const availability: FrameAvailability = { schema_version: "1.0", project_id: projectId, job_id: id, version_id: scene.version_id, source_range: frames, fps, available_ranges: [{ start: ordinals[0], end: last }], playable_ranges: [], pending_ranges: [{ start: last + 1, end: frames.end }], failed_ranges: [], state_revision: 1 };
    studio.snapshot.jobs.push(render);
    receiptsByJob.set(id, receipts);
    routes.push({ path: `${base}/jobs/${id}/frames`, body: pageOf(receipts) }, { path: `${base}/jobs/${id}/segments`, body: pageOf([]) }, { path: `${base}/jobs/${id}/availability`, body: availability });
  }
  addPass(2, Array.from({ length: 28 }, (_, index) => index + 1), shotId, { start: 1, end: 72 });
  addPass(1, [73], secondShotId, { start: 73, end: 168 });

  const shot = (shot_id: string, intent: string, start: number, end: number): ShotPlan["shots"][number] => ({
    shot_id, intent, camera_intent: "Follow the relay", motion_beats: [], scene_version: sceneRef, source_range: { start, end }, fps, frames: [],
  });
  const plan: ShotPlan = {
    schema_version: "1.0", plan_id: "orbital-relay", project_id: projectId, source_commit: board.source_commit,
    brief_artifact_ids: [], asset_dependencies: [{ item_id: kit.item_id, version_id: kit.version_id }], unresolved: [],
    shots: [shot(shotId, "Hold on the dormant relay", 1, 72), shot(secondShotId, "Track through the conduit", 73, 168)],
  };
  routes.push({ path: `${base}/versions/${board.version_id}/shot-plan`, body: plan });

  // Mirror the kind and pinned-identity checks from ContextValidator.request_references.
  // Without this guard the generic browser mock accepts impossible targets.
  routes.push({ method: "POST", path: `${base}/contexts`, handler: async route => {
    const context = route.request().postDataJSON() as ContextSnapshot;
    const target = context.request_target;
    const viewed = studio.versionRecords.find(v => v.version_id === context.version_id);
    const ref = target?.kind === "shot" ? target.storyboard_version : target?.kind === "item" ? target.version : null;
    const pinned = studio.versionRecords.find(v => v.version_id === ref?.version_id);
    const conflict = (context.version_id !== null && (!viewed || viewed.item_id !== context.item_id))
      || (ref && (!pinned || ref.item_id !== pinned.item_id || !["asset", "scene", "storyboard"].includes(pinned.kind)
        || (target?.kind === "shot" && pinned.kind !== "storyboard")
        || (context.version_id !== null && (ref.version_id !== context.version_id || ref.item_id !== context.item_id))))
      || ((target?.kind === "project" || target?.kind === "new_item") && (context.version_id !== null || context.item_id !== null));
    if (conflict) {
      await route.fulfill({ status: 409, json: { schema_version: "1.0", error: { code: "source_conflict", message: "Request target conflicts with source version", operation_id: null, retryable: false, details: {} } } });
      return;
    }
    const frame = context.target;
    if (frame?.render_job_id) {
      const render = studio.snapshot.jobs.find(job => job.job_id === frame.render_job_id);
      const slotReceipts = (receiptsByJob.get(frame.render_job_id) ?? []).filter(receipt => receipt.source_frame === frame.source_frame);
      if (frame.render_job_id === macroJobId && frame.source_frame === 1) {
        expect(slotReceipts.length).toBeGreaterThanOrEqual(2);
        expect(slotReceipts.map(receipt => receipt.receipt_id)).toEqual(["attempt-1-f1", "attempt-2-f1"]);
        expect(slotReceipts.map(receipt => receipt.attempt_id)).toEqual(["attempt-1", "attempt-2"]);
      }
      if (render?.target.version?.version_id !== frame.version_id || !slotReceipts.length || slotReceipts.some(receipt =>
        receipt.artifact_id !== frame.artifact_id || receipt.version_id !== frame.version_id || receipt.shot_id !== frame.shot_id
        || receipt.sha256 !== frame.displayed_sha256 || receipt.input_digest !== render?.input_digest
        || receipt.dimensions.width !== frame.dimensions.width || receipt.dimensions.height !== frame.dimensions.height
      )) {
        await route.fulfill({ status: 422, json: { schema_version: "1.0", error: { code: "frame_conflict", message: "Conflicting retained frame receipts", operation_id: null, retryable: false, details: {} } } });
        return;
      }
    }
    studio.contexts.push(context);
    await route.fulfill({ status: 201, json: context });
  } });

  await studio.install({ savedLayout: true, routes });
  await studio.open();
  await studio.ready();
  expect(await page.evaluate(() => window.devicePixelRatio)).toBe(test.info().project.use.deviceScaleFactor);
  return studio;
}

const queue = (page: Page) => page.getByRole("button", { name: "Queue message", exact: true });
const message = (page: Page) => page.getByRole("textbox", { name: "Message Copilot" });
const attemptedWrites = (studio: StudioFixture) => studio.guard!.calls.filter(call => call.method === "POST" && (call.url === `${base}/contexts` || call.url === `${base}/requests`));

async function selectView(page: Page, name: string, label: string) {
  const navigator = page.getByRole("complementary", { name: "Navigator", exact: true });
  await navigator.locator(".catalogue-item-header").filter({ has: page.getByText(name, { exact: true }) }).click();
  await expect(page.getByRole("region", { name: "Review workspace" })).toBeVisible();
  await expect(page.locator(".target-bar-label")).toHaveText(`Target: Current view · ${label} · ${name}`);
}

async function selectSceneFrameOne(page: Page) {
  await selectView(page, scene.name, "Scene");
  await expect(page.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  await expect(page.getByRole("spinbutton", { name: "Frame", exact: true })).toHaveValue("1");
  const pass = page.getByRole("combobox", { name: "Shot / render job" });
  await expect(pass).toHaveValue(macroJobId);
  await expect(pass.locator('option[value^="preview-"]')).toHaveCount(2);
}

async function queueAndVerifyReceipts(page: Page, text: string) {
  await message(page).fill(text);
  const watch = (path: string) => page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === `${base}/${path}`);
  const context = watch("contexts");
  const request = watch("requests");
  await queue(page).click();
  expect((await context).status()).toBe(201);
  expect((await request).status()).toBe(202);
  await expect(message(page)).toHaveValue("");
}

test("Current view queues an unassembled sequence as a viewed version", async ({ page, baseURL }) => {
  const studio = await openStudio(page, baseURL!);
  await selectView(page, sequence.name, "Sequence");
  await expect(page.getByText("No ready review image. Rendering is an explicit Queue request.")).toBeVisible();
  const bar = page.locator(".target-bar");
  await expect(bar).toHaveAttribute("title", `Target: Current view · Sequence · ${sequence.name}`);
  await expect(bar.locator(".target-ref-count")).toHaveCount(0);
  await bar.click();
  await expect(page.getByText(`Captures Sequence · ${sequence.name} from Review on submit.`)).toBeVisible();
  await expect(page.locator("#request-target-picker").getByRole("region", { name: "Attached images" })).toHaveCount(0);
  await page.getByRole("button", { name: "Close target picker" }).click();

  const text = "Review the draft before assembly.";
  await queueAndVerifyReceipts(page, text);
  expect(studio.contexts).toHaveLength(1);
  expect(studio.contexts[0]).toMatchObject({ item_id: sequence.item_id, version_id: sequence.version_id, request_target: null, target: null, attachments: [] });
  expect(studio.submissions).toHaveLength(1);
  expect(studio.submissions[0]).toMatchObject({ text, context_id: studio.contexts[0].context_id });
  await expect(page.locator(".user-message .stored-context-row > .target-badge")).toHaveText(`Viewed: Sequence · ${sequence.name}`);
  await expect(page.locator(".user-message .stored-context")).toHaveJSProperty("open", false);
  studio.assertHermetic();
});

test("Current view keeps the scene target and the exact shot-labelled frame receipt", async ({ page, baseURL }) => {
  const studio = await openStudio(page, baseURL!);
  await selectSceneFrameOne(page);
  await queueAndVerifyReceipts(page, "Review the opening relay frame.");
  expect(studio.contexts).toHaveLength(1);
  expect(studio.contexts[0]).toMatchObject({
    ...sceneRef, request_target: { kind: "item", version: sceneRef },
    target: { artifact_id: `${macroJobId}-f1`, render_job_id: macroJobId, shot_id: shotId, source_frame: 1 },
  });
  expect(studio.submissions).toHaveLength(1);
  expect(studio.submissions[0]).toMatchObject({ context_id: studio.contexts[0].context_id });
  studio.assertHermetic();
});

async function holdReviewPreparation(page: Page) {
  // A decoded keyframe normally prepares synchronously. Decorate the registered
  // Review preparer in this browser only, before it reads the live workspace.
  // No production test hook and no mocked capture payload.
  await page.getByRole("region", { name: "Review workspace" }).evaluate(node => {
    type Hook = { memoizedState: unknown; next: Hook | null };
    type Fiber = { return: Fiber | null; memoizedState: Hook | null; memoizedProps?: { onContextPreparer?: (prepare: ContextPreparer) => void } };
    type TestGate = { entered: boolean; release: () => void };
    const prop = Object.keys(node).find(key => key.startsWith("__reactFiber$"));
    if (!prop) throw new Error("Review React fibre is unavailable");
    let fibre: Fiber | null = (node as unknown as Record<string, Fiber>)[prop];
    let register: NonNullable<NonNullable<Fiber["memoizedProps"]>["onContextPreparer"]> | undefined;
    const refs = new Map<{ current: unknown }, unknown>();
    for (; fibre; fibre = fibre.return) {
      register ??= fibre.memoizedProps?.onContextPreparer;
      // Function components have linked hooks. Skip root and class state.
      for (let hook = fibre.memoizedState; hook && "memoizedState" in hook; hook = hook.next) {
        const state = hook.memoizedState;
        if (state && typeof state === "object" && "current" in state && typeof state.current === "function") refs.set(state, state.current);
      }
    }
    if (!register) throw new Error("Review preparation registration is unavailable");
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    const gate: TestGate = { entered: false, release };
    const wrapped: ContextPreparer = async () => {
      gate.entered = true;
      await pending;
      return original();
    };
    register(wrapped);
    const changed = [...refs].filter(([ref]) => ref.current === wrapped);
    if (changed.length !== 1) throw new Error("Could not locate the registered Review preparer");
    const original = changed[0][1] as ContextPreparer;
    Object.assign(window, { rc4ReviewGate: gate });
  });
  return {
    entered: () => page.evaluate(() => (window as unknown as { rc4ReviewGate: { entered: boolean } }).rc4ReviewGate.entered),
    release: () => page.evaluate(() => (window as unknown as { rc4ReviewGate: { release: () => void } }).rc4ReviewGate.release()),
  };
}

test("Current view aborts same-shot frame drift before any context or request write", async ({ page, baseURL }) => {
  const studio = await openStudio(page, baseURL!);
  await selectSceneFrameOne(page);
  const gate = await holdReviewPreparation(page);
  const text = "Keep this draft when the opening frame changes.";
  await message(page).fill(text);
  await queue(page).click();
  await expect.poll(gate.entered).toBe(true);
  expect(attemptedWrites(studio)).toEqual([]);

  await page.getByRole("button", { name: "Next frame", exact: true }).click();
  await expect(page.getByRole("spinbutton", { name: "Frame", exact: true })).toHaveValue("2");
  await expect(page.getByRole("combobox", { name: "Shot / render job" })).toHaveValue(macroJobId);
  await expect(page.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  await gate.release();

  await expect(page.getByText("Review changed during context capture. The draft and original target are retained. Submit again after checking the target.")).toBeVisible();
  await expect(message(page)).toHaveValue(text);
  await expect(queue(page)).toBeEnabled();
  expect(attemptedWrites(studio)).toEqual([]);
  studio.assertHermetic();
});
