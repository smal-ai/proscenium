import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { expect, test } from "@playwright/test";
import type { Artifact, Event, FrameAvailability, FrameReceipt, JobRecord, PreviewSegment } from "../../apps/frontend/src/api";
import type { MockRoute } from "./fixtures";
import { base, job, pageOf, projectId, retained, StudioFixture, version } from "./studio-fixture";

const fps = {numerator: 2, denominator: 1};
const dimensions = {width: 32, height: 18};
const hash = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");
const sceneVersion = version("signal-lost", "signal-lost-v1", "Signal Lost", "scene");
const scenes = [
  {prefix: "a44c", shot: "sl-01-dormant-macro", start: 1, colour: "red"},
  {prefix: "21f1", shot: "sl-02-conduit", start: 3, colour: "green"},
  {prefix: "0936", shot: "sl-03-console-wake", start: 5, colour: "blue"},
  {prefix: "2bd7", shot: "SL04_Exterior", start: 1, colour: "yellow"},
].map(scene => ({...scene, id: `preview-${scene.prefix}${hash(Buffer.from(scene.shot)).slice(4)}`}));

function renderScene(studio: StudioFixture, routes: MockRoute[], index: number, ready = true): JobRecord {
  const {id, start, shot, colour} = scenes[index];
  const proxy = index === 3;
  const purpose = proxy ? "proxy" : "draft_motion";
  const shotId = proxy ? null : shot;
  const frames = {start, end: start + (proxy ? 0 : 1)};
  const identity = {schema_version: "1.0" as const, project_id: projectId, job_id: id, attempt_id: `attempt-${id}`,
    version_id: sceneVersion.version_id, shot_id: shotId, input_digest: sceneVersion.source.digest};
  const input = ["-v", "error", "-f", "lavfi", "-i", `color=${colour}:s=${dimensions.width}x${dimensions.height}:r=${fps.numerator}`];
  const receipts: FrameReceipt[] = [];
  const segments: PreviewSegment[] = [];
  const artifact_ids: string[] = [];
  function media(artifact_id: string, bytes: Buffer, kind: Artifact["kind"], contentType: string) {
    const artefact: Artifact = {schema_version: "1.0", project_id: projectId, artifact_id, kind, purpose: kind === "video" ? "draft_motion" : "keyframe",
      sha256: hash(bytes), bytes: bytes.length, media_type: contentType, state: "ready", dimensions, derived_from: []};
    studio.retained.push({...retained(artifact_id, "verified"), artifact: artefact});
    studio.artifactContent.set(artifact_id, {contentType, bytes});
    artifact_ids.push(artifact_id);
    return artefact;
  }
  if (ready) {
    const png = execFileSync("ffmpeg", [...input, "-frames:v", "1", "-threads", "1", "-c:v", "png", "-f", "image2pipe", "pipe:1"]);
    for (let source_frame = start; source_frame <= frames.end; source_frame++) {
      const {artifact_id, sha256} = media(`${shot}-f${source_frame}`, png, "image", "image/png");
      receipts.push({...identity, source_frame, receipt_id: artifact_id, artifact_id, sha256, dimensions, output_path: `${artifact_id}.png`});
    }
    if (!proxy) {
      const clip = execFileSync("ffmpeg", [...input, "-frames:v", "2", "-threads", "1", "-c:v", "libvpx", "-f", "webm", "pipe:1"]);
      const {artifact_id, sha256} = media(`${shot}-clip`, clip, "video", "video/webm");
      segments.push({...identity, source_frames: frames, segment_id: artifact_id, frame_count: 2, fps, artifact_id, sha256, output_path: `${artifact_id}.webm`});
    }
  }
  const availability: FrameAvailability = {schema_version: "1.0", project_id: projectId, job_id: id,
    version_id: sceneVersion.version_id, source_range: frames, fps, available_ranges: ready ? [frames] : [],
    playable_ranges: segments.length ? [frames] : [], pending_ranges: ready ? [] : [frames], failed_ranges: [], state_revision: 1};
  routes.push({path: `${base}/jobs/${id}/frames`, body: pageOf(receipts)}, {path: `${base}/jobs/${id}/segments`, body: pageOf(segments)},
    {path: `${base}/jobs/${id}/availability`, body: availability});
  return {...job(id, ready ? "succeeded" : "planned"), kind: "render", target: {target_id: shot, shot_id: shotId,
    version: {item_id: sceneVersion.item_id, version_id: sceneVersion.version_id}}, availability: ready ? "complete" : "pending",
    completed_frames: receipts.length, expected_frames: frames.end - start + 1, artifact_ids,
    render_spec: {intent_id: id, shot_id: shotId, scene_name: shot, purpose, source_closure_digest: sceneVersion.source.digest,
      camera: null, frames, fps, dimensions, engine: "Cycles", device: "CPU", samples: 32, renderer_revision: "synthetic",
      colour_management: {view_transform: "AgX", look: "None", exposure: 0, gamma: 1}}};
}

test("indexed scene playback follows real presented frames and updates after a live preview reorder", async ({page, baseURL}, testInfo) => {
  const studio = new StudioFixture(page, baseURL!);
  studio.retained = [];
  const published = structuredClone(sceneVersion);
  studio.versionRecords = [published];
  studio.snapshot.items = [{schema_version: "1.0", project_id: projectId, kind: "scene", name: published.name,
    item_id: published.item_id, current_version_id: published.version_id, selected_version_id: null, state_revision: 1}];
  const routes: MockRoute[] = [];
  studio.snapshot.jobs = [2, 1, 3, 0].map(index => renderScene(studio, routes, index));
  await studio.install({savedLayout: true, routes});
  await studio.open();
  await studio.ready();
  expect(await page.evaluate(() => devicePixelRatio)).toBe(testInfo.project.name === "chromium-dpr2" ? 2 : 1);
  await page.locator(".catalogue-item-header").filter({hasText: "Signal Lost"}).click();
  const review = page.locator("main.review-panel");
  const transport = review.getByLabel("Review transport", {exact: true});
  const passes = transport.getByRole("combobox", {name: "Shot / render job"});
  const frame = transport.getByRole("spinbutton", {name: "Frame", exact: true});
  const annotate = review.getByRole("button", {name: "Annotate presented frame"});
  const playAll = transport.getByRole("button", {name: "Play all", exact: true});
  const play = transport.getByRole("button", {name: "Play", exact: true});
  const pause = transport.getByRole("button", {name: "Pause", exact: true});
  const complete = review.getByText("Full animation playback complete", {exact: true});
  const compact = async () => expect((await transport.boundingBox())!.height).toBeLessThanOrEqual(52);
  const expectedOptions = (order: number[]) => order.map((index, ordinal) => {
    const {shot, start} = scenes[index];
    return `${ordinal + 1} · ${shot} · f${start}–${start + 1} · draft_motion`;
  });
  await expect(passes).toHaveValue(scenes[0].id);
  await review.getByRole("button", {name: "Animation", exact: true}).click();
  await expect(passes.locator("option")).toHaveText(expectedOptions([0, 1, 2]));
  await expect(passes).toHaveValue(scenes[0].id);
  await expect(frame).toHaveValue("1");
  await compact();

  await passes.selectOption(scenes[1].id);
  await expect(frame).toHaveValue("3");
  await playAll.click();
  await expect(passes).toHaveValue(scenes[0].id);
  await expect(transport.getByText("Scene 1 of 3", {exact: true})).toBeVisible();
  await compact();
  await expect(review.locator("video.review-prebuffer")).toHaveAttribute("src", /sl-02-conduit-clip\/content$/);
  await expect(passes).toHaveValue(scenes[1].id);
  await expect(transport.getByText("Scene 2 of 3", {exact: true})).toBeVisible();
  await pause.click();
  await expect(play).toBeVisible();
  await expect(passes).toHaveValue(scenes[1].id);
  await expect(transport.getByText("Scene 2 of 3", {exact: true})).toBeVisible();
  await page.keyboard.press("Space");
  await expect(passes).toHaveValue(scenes[2].id);
  await expect(complete).toBeVisible();
  await expect(frame).toHaveValue("6");
  await expect(annotate).toBeEnabled();
  await expect.poll(() => page.evaluate(id => JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)!).target, projectId))
    .toMatchObject({source_frame: 6, artifact_id: "sl-03-console-wake-f6", render_job_id: scenes[2].id, media_artifact_id: "sl-03-console-wake-clip"});

  // Replay the last pass with the same requested start frame and a fresh seek nonce.
  await play.click();
  await expect(frame).toHaveValue("5");
  await expect(review.getByText("Playback complete", {exact: true})).toBeVisible();
  await expect(frame).toHaveValue("6");

  published.preview_job_ids = [2, 0, 1].map(index => scenes[index].id);
  // The backend worktree owns regeneration of the new event discriminant.
  studio.emit("preview.order_changed" as Event["event_type"], {item_id: published.item_id, version_id: published.version_id, preview_job_ids: published.preview_job_ids});
  await expect(passes.locator("option")).toHaveText(expectedOptions([2, 0, 1]));
  await playAll.click();
  await expect(passes).toHaveValue(scenes[2].id);
  await expect(transport.getByText("Scene 1 of 3", {exact: true})).toBeVisible();
  await expect(passes).toHaveValue(scenes[0].id);
  await expect(transport.getByText("Scene 2 of 3", {exact: true})).toBeVisible();
  await expect(passes).toHaveValue(scenes[1].id);
  await expect(complete).toBeVisible();
  await expect(frame).toHaveValue("4");
  await expect(annotate).toBeEnabled();
  studio.assertHermetic();
});
