import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import type { ShotPlan, VersionRecord } from "../../apps/frontend/src/api";
import { emptySelection } from "../../apps/frontend/src/api/studio";
import { base, projectId, retained, StudioFixture, version } from "./studio-fixture";

const videoId = "generation-5117f934bc9f2dff6638dfec-artifact-0";
const provenanceId = "generation-5117f934bc9f2dff6638dfec-artifact-provenance";
const sequence = { ...version("wagenburg-05", "wagenburg-05-proof-v1", "Wagenburg 05", "sequence"), artifact_ids: [videoId, provenanceId] };
const board = { ...version("proof-board", "proof-board-v1", "Proof board", "storyboard"), artifact_ids: [videoId, "verified"] };
const shotId = "proof-shot";
const boardPlan: ShotPlan = {
  schema_version: "1.0", project_id: projectId, source_commit: board.source_commit, plan_id: "proof-plan",
  brief_artifact_ids: [], asset_dependencies: [], unresolved: [], shots: [{
    shot_id: shotId, intent: "Opening shot", camera_intent: "Static", motion_beats: [], scene_version: null, source_range: null, fps: null,
    frames: [{ frame_id: "proof-frame", role: "start", composition: "Wide", artifact_id: "verified", state: "available", owner_selected: false }],
  }],
};

const review = (page: Page) => page.getByRole("region", { name: "Review workspace" });
const viewed = ({ item_id, version_id }: VersionRecord) => ({ item_id, version_id });
// Studio persists the actual selection emitted by Review's onContextChange.
const emittedContext = (page: Page) => page.evaluate(id => JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`) ?? "null"), projectId);

async function openVersion(page: Page, origin: string, pinned: VersionRecord) {
  const studio = new StudioFixture(page, origin);
  studio.versionRecords = [pinned];
  studio.snapshot.items = [{
    schema_version: "1.0", project_id: projectId, item_id: pinned.item_id, kind: pinned.kind, name: pinned.name,
    current_version_id: pinned.version_id, selected_version_id: null, state_revision: 1,
  }];
  const clip = retained(videoId, "verified", "video/mp4");
  const bytes = execFileSync("ffmpeg", ["-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=32x32:r=2:d=1",
    "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-movflags", "+empty_moov+frag_keyframe", "-f", "mp4", "pipe:1"], { timeout: 10_000 });
  studio.artifactContent.set(videoId, { contentType: "video/mp4", bytes });
  studio.retained.push({ ...clip, published_versions: [viewed(pinned)], artifact: {
    ...clip.artifact, kind: "video", bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"),
    dimensions: { width: 32, height: 32 }, fps: { numerator: 2, denominator: 1 }, frame_count: 2, duration_seconds: 1,
  } });
  const provenance = retained(provenanceId, "verified", "application/json");
  studio.retained.push({ ...provenance, artifact: { ...provenance.artifact, kind: "document", purpose: "metadata", dimensions: undefined } });
  await studio.install({ savedLayout: true, routes: [{ path: `${base}/versions/${board.version_id}/shot-plan`, body: boardPlan }] });
  await studio.open();
  await studio.ready();
  await page.locator(".catalogue-item-header").filter({ has: page.getByText(pinned.name, { exact: true }) }).click();
  return studio;
}

async function queueCurrentView(page: Page, studio: StudioFixture) {
  await page.getByRole("textbox", { name: "Message Copilot" }).fill("Review this published video.");
  await page.getByRole("button", { name: "Queue message", exact: true }).click();
  // The actual Queue path invokes Review's registered onContextPreparer before posting.
  await expect.poll(() => studio.submissions.length).toBe(1);
  expect(studio.contexts).toHaveLength(1);
  expect(studio.submissions[0]).toMatchObject({ type: "queue", context_id: studio.contexts[0].context_id });
  return studio.contexts[0];
}

test("a video-only publication emits and submits its viewed sequence as animation", async ({ page, baseURL }) => {
  const studio = await openVersion(page, baseURL!, sequence);
  await expect(review(page).getByText("Published video", { exact: true })).toBeVisible();
  await expect(review(page).locator("video")).toHaveJSProperty("videoWidth", 32);
  // Sequence context pins the viewed IDs but cannot create an item request target.
  const playback = { ...emptySelection(), ...viewed(sequence), view_mode: "animation", request_target: null };
  await expect.poll(() => emittedContext(page)).toEqual(playback);
  expect(await queueCurrentView(page, studio)).toMatchObject(playback);
  studio.assertHermetic();
});

test("storyboard playback drops the open shot and keeps mode shortcuts out of focused video controls", async ({ page, baseURL }) => {
  const studio = await openVersion(page, baseURL!, board);
  const workspace = review(page);
  await expect(workspace.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  await expect.poll(() => emittedContext(page)).toMatchObject({
    ...viewed(board), view_mode: "keyframes", request_target: { kind: "shot", storyboard_version: viewed(board), shot_id: shotId },
    target: { artifact_id: "verified", shot_id: shotId }, attachments: [{ artifact_id: "verified", role: "concept" }],
  });

  const animation = workspace.getByRole("button", { name: "Animation", exact: true });
  await animation.click();
  const player = workspace.locator("video");
  await expect(player).toHaveJSProperty("videoWidth", 32);
  await player.focus();
  for (const key of ["1", "2", "3"]) {
    await expect(player).toBeFocused();
    await page.keyboard.press(key);
    await expect(animation).toHaveAttribute("aria-pressed", "true");
  }
  // The same shortcut still changes modes outside the native player.
  await animation.focus();
  await page.keyboard.press("2");
  await expect(animation).toHaveAttribute("aria-pressed", "true");
  await page.locator(".brand").click();
  await page.keyboard.press("2");
  await expect(workspace.getByRole("button", { name: "Keyframes" })).toHaveAttribute("aria-pressed", "true");
  await animation.click();

  const playback = { ...emptySelection(), ...viewed(board), view_mode: "animation", request_target: { kind: "item", version: viewed(board) } };
  await expect.poll(() => emittedContext(page)).toEqual(playback);
  expect(await queueCurrentView(page, studio)).toMatchObject(playback);
  studio.assertHermetic();
});
