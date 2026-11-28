import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import type { FrameMap, RetainedArtifactSummary, ShotPlan, VersionRecord } from "../../apps/frontend/src/api";
import { base, deferred, projectId, retained, StudioFixture, version } from "./studio-fixture";

const review = (page: Page) => page.locator("main.review-panel");
const persistedContext = (page: Page) => page.evaluate(id => JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)!), projectId);

function publish(studio: StudioFixture, record: VersionRecord) {
  studio.versionRecords.push(record);
  studio.snapshot.items.push({ schema_version: "1.0", project_id: projectId, item_id: record.item_id, kind: record.kind,
    name: record.name, current_version_id: record.version_id, selected_version_id: null, state_revision: 1 });
}

function refreshRetained(studio: StudioFixture, id: string) {
  studio.emit("tool.completed", { invocation_id: `refresh-${studio.events.length}`, name: "get_artifact", state: "completed",
    summary: "Retained metadata changed", artifact_ids: [id], model_id: null });
}

function revoke(studio: StudioFixture, id: string) {
  studio.retained = studio.retained.map(row => row.artifact.artifact_id === id ? { ...row, artifact: { ...row.artifact, state: "unavailable" } } : row);
  refreshRetained(studio, id);
}

async function omitRetainedSummaries(studio: StudioFixture, id: string) {
  studio.retained = [];
  refreshRetained(studio, id);
  // The count changing to zero proves React consumed the omitted summaries.
  await expect(studio.page.getByRole("button", { name: "Retained media (0)", exact: true })).toBeVisible();
}

async function queueCurrentView(studio: StudioFixture) {
  const { page } = studio;
  await page.getByRole("textbox", { name: "Message Copilot" }).fill("Review this view without the revoked output.");
  const bar = page.getByRole("button", { name: "Choose request target and references" });
  await bar.click();
  const panel = page.getByRole("region", { name: "Request target", exact: true });
  await expect(panel.getByRole("button", { name: "Current view", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(panel.getByRole("region", { name: "Attached images" })).toHaveCount(0);
  await bar.press("Control+Enter");
  await expect(panel).toHaveCount(0);
  await expect.poll(() => studio.submissions.length).toBe(1);
  expect(studio.contexts).toHaveLength(1);
  expect(studio.submissions[0]).toMatchObject({ context_id: studio.contexts[0].context_id });
  return studio.contexts[0];
}

test("retained preview revocation replaces the image and excludes it from submitted context", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  await studio.install({ savedLayout: true });
  await studio.open(); await studio.ready();
  await page.getByRole("button", { name: /^Retained media \(\d+\)$/ }).click();
  await page.getByRole("button", { name: "Inspect retained concept image verified", exact: true }).click();
  const preview = review(page).locator(".retained-review");
  await expect(preview.locator("img")).toHaveJSProperty("naturalWidth", 1);
  await expect.poll(() => persistedContext(page)).toMatchObject({ attachments: [{ artifact_id: "verified", role: "reference" }] });

  revoke(studio, "verified");
  await expect(preview.getByText("Retained output unavailable.", { exact: true })).toBeVisible();
  await omitRetainedSummaries(studio, "verified");
  await expect(preview.getByText("Retained output unavailable.", { exact: true })).toBeVisible();
  await expect(preview.locator("img, audio, a[download]")).toHaveCount(0);
  await expect.poll(() => persistedContext(page)).toMatchObject({ target: null, attachments: [] });
  expect(await queueCurrentView(studio)).toMatchObject({ target: null, attachments: [] });
  studio.assertHermetic();
});

test("pinned keyframe revocation excludes both the target and attachment from Current view", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const pinned = version("keyframes", "keyframes-v1", "Pinned keyframe fixture");
  pinned.artifact_ids = ["verified"];
  studio.retained[0].artifact.purpose = "keyframe";
  publish(studio, pinned);
  await studio.install({ savedLayout: true });
  await studio.open(); await studio.ready();
  await page.locator(".catalogue-item-header").filter({ hasText: pinned.name }).click();
  const capture = review(page).getByRole("button", { name: "Annotate presented frame" });
  await expect(capture).toBeEnabled();
  await expect.poll(() => persistedContext(page)).toMatchObject({ target: { artifact_id: "verified" }, attachments: [{ artifact_id: "verified" }] });

  revoke(studio, "verified");
  await expect(capture).toBeDisabled();
  await expect(review(page).locator(".review-image img")).toHaveCount(0);
  expect(await queueCurrentView(studio)).toMatchObject({ version_id: pinned.version_id, target: null, attachments: [] });
  studio.assertHermetic();
});

test("Storyboard rejects stale frame and candidate metadata after revocation and omission", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const frame = retained("board-frame", "verified"), candidate = retained("candidate-frame", "verified");
  const frameMarker = retained("frame-revocation-observed", "verified");
  const candidateMarker = retained("candidate-revocation-observed", "verified");
  const frameId = frame.artifact.artifact_id, candidateId = candidate.artifact.artifact_id;
  const framePath = `${base}/artifacts/${frameId}`, candidatePath = `${base}/artifacts/${candidateId}`;
  const frameStarted = deferred(), candidateStarted = deferred(), releaseFrame = deferred(), releaseCandidate = deferred();
  const unavailable = (row: RetainedArtifactSummary): RetainedArtifactSummary => ({ ...row, artifact: { ...row.artifact, state: "unavailable" } });
  const shotPlan: ShotPlan = { schema_version: "1.0", project_id: projectId, plan_id: "revocation", source_commit: "a".repeat(40),
    brief_artifact_ids: [], asset_dependencies: [], unresolved: [], shots: [{ shot_id: "shot-1", intent: "Start", camera_intent: "Static",
      motion_beats: [], scene_version: null, source_range: null, fps: null,
      frames: [{ frame_id: "frame-1", role: "start", composition: "Wide", artifact_id: frameId, state: "available", owner_selected: false }] }] };
  await studio.install({ catalogue: true, savedLayout: true, shotPlan, routes: [
    { path: framePath, handler: async route => { frameStarted.release(); await releaseFrame.promise; await route.fulfill({ json: frame.artifact }); } },
    { path: candidatePath, handler: async route => { candidateStarted.release(); await releaseCandidate.promise; await route.fulfill({ json: candidate.artifact }); } },
    ...[frameMarker, candidateMarker].map(marker => ({ path: `${base}/artifacts/${marker.artifact.artifact_id}`, body: unavailable(marker).artifact })),
  ] });
  const board = page.getByRole("region", { name: "Storyboard browser" });
  const frameButton = board.getByRole("button", { name: /start · frame-1/ });
  const select = board.getByRole("button", { name: "Select image", exact: true });
  const inspect = board.getByRole("button", { name: "Inspect candidate", exact: true });
  const responseFor = (path: string) => page.waitForResponse(response => new URL(response.url()).pathname === path);
  async function revokeThenOmit(row: RetainedArtifactSummary, marker: RetainedArtifactSummary) {
    const markerId = marker.artifact.artifact_id;
    const card = page.locator(".retained-output-card").filter({ has: page.locator(`code[title="${markerId}"]`) });
    studio.retained = [unavailable(row), marker];
    refreshRetained(studio, markerId);
    await expect(card.getByRole("link", { name: "Download output" })).toBeVisible();
    studio.retained = [];
    refreshRetained(studio, markerId);
    // The card falls back to the stubbed detail route only once the list omission commits.
    await expect(card.getByText("Output unavailable", { exact: true })).toBeVisible();
  }
  try {
    await studio.open(); await studio.ready();
    await page.getByRole("button", { name: "Shots", exact: true }).click();
    await expect(board.getByRole("heading", { name: "Plan revocation" })).toBeVisible();
    await frameStarted.promise;
    await revokeThenOmit(frame, frameMarker);
    const staleFrame = responseFor(framePath);
    releaseFrame.release();
    expect(await (await staleFrame).finished()).toBeNull();
    await frameButton.click();
    await expect(frameButton).toContainText("Image: unavailable");
    await expect(frameButton.locator("img")).toHaveCount(0);
    await expect(select).toBeDisabled();

    await board.getByLabel("Alternative image artefact ID").fill(candidateId);
    await inspect.click();
    await candidateStarted.promise;
    await revokeThenOmit(candidate, candidateMarker);
    const staleCandidate = responseFor(candidatePath);
    releaseCandidate.release();
    expect(await (await staleCandidate).finished()).toBeNull();
    await expect(board.getByRole("alert")).toContainText("Candidate image is unavailable.");
    await expect(select).toBeDisabled();
    await expect(board.getByText(`Ready concept: ${candidateId}`, { exact: true })).toHaveCount(0);
    studio.retained = [frame];
    refreshRetained(studio, frameMarker.artifact.artifact_id);
    await expect(select).toBeEnabled();
    studio.assertHermetic();
  } finally { releaseFrame.release(); releaseCandidate.release(); }
});

for (const deferredRead of [false, true]) {
  test(`sequence frame-map revocation ${deferredRead ? "discards an in-flight content response" : "invalidates decoded capture"}`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    const pinned = version("sequence", "sequence-v1", "Revocation sequence", "sequence");
    const videoId = "assembly", mapId = "frame-map";
    pinned.artifact_ids = [videoId, mapId, "verified"];
    publish(studio, pinned);
    const fps = { numerator: 2, denominator: 1 };
    const frame = (source_frame: number): FrameMap["frames"][number] => ({ sequence_frame: source_frame, contributors: [{
      shot_id: null, version_id: "scene-v1", job_id: "render", source_frame, weight: { numerator: 1, denominator: 1 },
    }] });
    const mapping: FrameMap = { schema_version: "1.0", project_id: projectId, sequence_version_id: pinned.version_id,
      frame_count: 2, fps, blend_space: "linear_rgb", frames: [frame(1), frame(2)] };
    const mapBytes = Buffer.from(JSON.stringify(mapping));
    const videoBytes = deferredRead ? Buffer.alloc(0) : execFileSync("ffmpeg", ["-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=32x32:r=2:d=1",
      "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-movflags", "+empty_moov+frag_keyframe", "-f", "mp4", "pipe:1"], { timeout: 10_000 });
    const media = (id: string, bytes: Buffer, kind: "video" | "frame_map", purpose: "final" | "metadata", media_type: string): RetainedArtifactSummary => {
      const row = retained(id, "verified");
      return { ...row, artifact: { ...row.artifact, kind, purpose, media_type, bytes: bytes.length,
        sha256: createHash("sha256").update(bytes).digest("hex"), dimensions: kind === "video" ? { width: 32, height: 32 } : undefined,
        ...(kind === "video" ? { fps, frame_count: 2 } : {}) } };
    };
    studio.retained.push(media(videoId, videoBytes, "video", "final", "video/mp4"), media(mapId, mapBytes, "frame_map", "metadata", "application/json"));
    studio.artifactContent.set(videoId, { bytes: videoBytes, contentType: "video/mp4" });
    const contentPath = `${base}/artifacts/${mapId}/content`;
    const started = deferred(), release = deferred();
    await studio.install({ savedLayout: true, routes: [{ path: contentPath, handler: async route => {
      started.release();
      if (deferredRead) await release.promise;
      await route.fulfill({ contentType: "application/json", body: mapBytes });
    } }] });
    try {
      await studio.open(); await studio.ready();
      await page.locator(".catalogue-item-header").filter({ hasText: pinned.name }).click();
      await started.promise;
      await review(page).getByRole("button", { name: "Animation", exact: true }).click();
      const player = review(page).getByLabel("Mapped sequence animation");
      const capture = review(page).getByRole("button", { name: "Annotate presented sequence frame" });
      if (!deferredRead) await expect(capture).toBeEnabled();
      else await expect(player).toHaveCount(0);

      const summaries = page.waitForResponse(response => new URL(response.url()).pathname === `${base}/artifacts`);
      revoke(studio, mapId);
      expect(await (await summaries).json()).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ artifact: expect.objectContaining({ artifact_id: mapId, state: "unavailable" }) })]) });
      // The marker proves the retained projection has reached the mounted React tree.
      const marker = retained("revocation-observed", "verified");
      studio.retained.push(marker);
      refreshRetained(studio, marker.artifact.artifact_id);
      await page.getByRole("button", { name: /^Retained media \(\d+\)$/ }).click();
      await expect(page.getByRole("button", { name: "Inspect retained concept image revocation-observed", exact: true })).toBeVisible();
      await expect(player).toHaveCount(0);
      await expect(capture).toHaveCount(0);

      const details = review(page).getByRole("button", { name: "Details", exact: true });
      await details.click();
      await omitRetainedSummaries(studio, mapId);
      if (deferredRead) {
        const response = page.waitForResponse(value => new URL(value.url()).pathname === contentPath);
        release.release();
        expect(await (await response).finished()).toBeNull();
      }
      // The read settles only after the content response, with the summary already omitted.
      await expect(review(page).getByText("verified: ready · concept", { exact: true })).toBeVisible();
      await expect(player).toHaveCount(0);
      await expect(capture).toHaveCount(0);
      expect(studio.uploadCalls).toEqual([]);
      studio.assertHermetic();
    } finally { release.release(); }
  });
}
