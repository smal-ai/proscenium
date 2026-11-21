import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import type { Artifact, FrameAvailability, FrameReceipt, JobRecord, PreviewSegment } from "../../apps/frontend/src/api";
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

function webmClip(input: string[]): Buffer {
  // A file output finalises duration and seek metadata. The pipe muxer leaves
  // Chromium treating the last frame's start time as the whole clip duration.
  const dir = mkdtempSync(join(tmpdir(), "proscenium-clip-"));
  try {
    const path = join(dir, "clip.webm");
    execFileSync("ffmpeg", [...input, "-frames:v", "2", "-threads", "1", "-c:v", "libvpx", "-f", "webm", path]);
    return readFileSync(path);
  } finally {
    rmSync(dir, {recursive: true, force: true});
  }
}

function renderScene(studio: StudioFixture, routes: MockRoute[], index: number, {ready = true, tail, omitSecondReceipt = false, reportAvailability = true}: {
  ready?: boolean; tail?: "pending" | "failed" | "retained"; omitSecondReceipt?: boolean; reportAvailability?: boolean;
} = {}): JobRecord {
  const {id, start, shot, colour} = scenes[index];
  const proxy = index === 3;
  const purpose = proxy ? "proxy" : "draft_motion";
  const shotId = proxy ? null : shot;
  const playable = {start, end: start + (proxy ? 0 : 1)};
  const frames = {start, end: playable.end + (tail ? 2 : 0)};
  const receiptEnd = playable.end + (tail === "retained" ? 1 : 0) - (omitSecondReceipt ? 1 : 0);
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
    for (let source_frame = start; source_frame <= receiptEnd; source_frame++) {
      const {artifact_id, sha256} = media(`${shot}-f${source_frame}`, png, "image", "image/png");
      receipts.push({...identity, source_frame, receipt_id: artifact_id, artifact_id, sha256, dimensions, output_path: `${artifact_id}.png`});
    }
    if (!proxy) {
      const clip = webmClip(input);
      const {artifact_id, sha256} = media(`${shot}-clip`, clip, "video", "video/webm");
      segments.push({...identity, source_frames: playable, segment_id: artifact_id, frame_count: 2, fps, artifact_id, sha256, output_path: `${artifact_id}.webm`});
    }
  }
  const availability: FrameAvailability = {schema_version: "1.0", project_id: projectId, job_id: id,
    version_id: sceneVersion.version_id, source_range: frames, fps, available_ranges: ready ? [{start, end: receiptEnd}] : [],
    playable_ranges: segments.length ? [playable] : [], pending_ranges: !ready ? [frames] : tail === "pending" ? [{start: playable.end + 1, end: frames.end}] : [],
    failed_ranges: tail === "failed" || tail === "retained" ? [{start: receiptEnd + 1, end: frames.end}] : [], state_revision: 1};
  routes.push({path: `${base}/jobs/${id}/frames`, body: pageOf(receipts)}, {path: `${base}/jobs/${id}/segments`, body: pageOf(segments)},
    reportAvailability ? {path: `${base}/jobs/${id}/availability`, body: availability}
      : {path: `${base}/jobs/${id}/availability`, status: 503, body: {error: {code: "temporarily_unavailable"}}});
  if (!proxy) {
    const clipId = `${shot}-clip`;
    routes.push({path: `${base}/artifacts/${clipId}/content`, handler: route => {
      const clip = studio.artifactContent.get(clipId);
      if (!clip) return route.fulfill({status: 404});
      const {bytes, contentType} = clip;
      const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range ?? "");
      if (!range) return route.fulfill({contentType, body: bytes});
      const from = Number(range[1]);
      const to = range[2] ? Math.min(Number(range[2]), bytes.length - 1) : bytes.length - 1;
      if (from > to) return route.fulfill({status: 416, headers: {"content-range": `bytes */${bytes.length}`}});
      return route.fulfill({status: 206, contentType, body: bytes.subarray(from, to + 1), headers: {
        "accept-ranges": "bytes", "content-range": `bytes ${from}-${to}/${bytes.length}`,
      }});
    }});
  }
  return {...job(id, tail === "pending" ? "running" : tail ? "failed" : ready ? "succeeded" : "planned"), kind: "render", target: {target_id: shot, shot_id: shotId,
    version: {item_id: sceneVersion.item_id, version_id: sceneVersion.version_id}}, availability: tail || omitSecondReceipt ? "partial" : ready ? "complete" : "pending",
    completed_frames: receipts.length, expected_frames: frames.end - start + 1, artifact_ids,
    render_spec: {intent_id: id, shot_id: shotId, scene_name: shot, purpose, source_closure_digest: sceneVersion.source.digest,
      camera: null, frames, fps, dimensions, engine: "Cycles", device: "CPU", samples: 32, renderer_revision: "synthetic",
      ...(proxy ? {} : {review_output: {media_type: "image/png", encoding: "view_transformed_srgb", bit_depth: 8}}),
      colour_management: {view_transform: "AgX", look: "None", exposure: 0, gamma: 1}}};
}

async function openScene(page: Page, baseURL: string, render: (studio: StudioFixture, routes: MockRoute[]) => JobRecord[]) {
  const studio = new StudioFixture(page, baseURL);
  studio.retained = [];
  const published = structuredClone(sceneVersion);
  studio.versionRecords = [published];
  studio.snapshot.items = [{schema_version: "1.0", project_id: projectId, kind: "scene", name: published.name,
    item_id: published.item_id, current_version_id: published.version_id, selected_version_id: null, state_revision: 1}];
  const routes: MockRoute[] = [];
  studio.snapshot.jobs = render(studio, routes);
  await studio.install({savedLayout: true, routes});
  await studio.open();
  await studio.ready();
  await page.locator(".catalogue-item-header").filter({hasText: "Signal Lost"}).click();
  return {studio, published, routes};
}

test("indexed scene playback follows real presented frames and updates after a live preview reorder", async ({page, baseURL}, testInfo) => {
  const {studio, published} = await openScene(page, baseURL!, (fixture, routes) => [2, 1, 3, 0].map(index => renderScene(fixture, routes, index)));
  expect(await page.evaluate(() => devicePixelRatio)).toBe(testInfo.project.name === "chromium-dpr2" ? 2 : 1);
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

  // Start directly on default Pass 1, including when its first video frame is already confirmed.
  await expect(annotate).toBeEnabled();
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

  await review.getByRole("button", {name: "Keyframes", exact: true}).click();
  await expect(frame).toHaveValue("6");
  await expect(review.locator(".review-image img")).toHaveAttribute("src", /sl-03-console-wake-f6\/content$/);
  await expect(annotate).toBeEnabled();
  await review.getByRole("button", {name: "Animation", exact: true}).click();
  await expect(annotate).toBeEnabled();
  await expect(frame).toHaveValue("6");

  // Replay the last pass with the same requested start frame and a fresh seek nonce.
  await play.click();
  await expect(frame).toHaveValue("5");
  await expect(review.getByText("Playback complete", {exact: true})).toBeVisible();
  await expect(frame).toHaveValue("6");

  published.preview_job_ids = [2, 0, 1].map(index => scenes[index].id);
  studio.emit("preview.order_changed", {...sceneVersion, preview_job_ids: published.preview_job_ids});
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

test("background raster receipt floods do not refetch catalogue or the viewed version", async ({page, baseURL}) => {
  const background = version("background", "background-v1", "Background render", "scene");
  let raster = {...job("background-job", "running"), kind: "render" as const,
    target: {target_id: "background", version: {item_id: background.item_id, version_id: background.version_id}, shot_id: null},
    completed_frames: 0, expected_frames: 200, availability: "partial" as const};
  const {studio} = await openScene(page, baseURL!, (fixture, routes) => {
    fixture.versionRecords.push(background);
    fixture.snapshot.items.push({schema_version: "1.0", project_id: projectId, kind: "scene", name: background.name,
      item_id: background.item_id, current_version_id: background.version_id, selected_version_id: null, state_revision: 1});
    return [renderScene(fixture, routes, 0), raster];
  });
  await expect(page.locator("main.review-panel .review-image img")).toBeVisible();
  const reads = () => studio.guard!.calls.filter(({method, url}) => method === "GET" &&
    /\/items\/[^/]+\/versions$|\/jobs\/[^/]+\/(frames|segments|availability)$|\/annotations$/.test(new URL(url, baseURL!).pathname)).length;
  const before = reads();
  for (let i = 1; i <= 20; i++) {
    const artifact_id = `background-${i}`;
    studio.emit("frame.available", {artifact_id, version_id: background.version_id, job_id: raster.job_id, source_frame: i});
    raster = {...raster, completed_frames: i, state_revision: i + 1, artifact_ids: [...raster.artifact_ids, artifact_id]};
    studio.emit("job.state_changed", raster);
  }
  // Wait for the actual event cursor, then let the coalescing timer drain.
  const sequence = studio.events.at(-1)!.sequence;
  await expect.poll(() => page.evaluate(id => Number(localStorage.getItem(`proscenium.owner.cursor.${id}`)), projectId)).toBe(sequence);
  await page.waitForTimeout(240);
  expect(reads()).toBe(before);
});

for (const reportAvailability of [true, false]) {
  test(`Play all stops inside a partial pass ${reportAvailability ? "with failed ranges" : "before availability is reported"}`, async ({page, baseURL}) => {
    const {studio} = await openScene(page, baseURL!, (fixture, routes) => [
      renderScene(fixture, routes, 1), renderScene(fixture, routes, 0, {tail: "failed", reportAvailability}),
    ]);
    const review = page.locator("main.review-panel");
    const transport = review.getByLabel("Review transport", {exact: true});
    const passes = transport.getByRole("combobox", {name: "Shot / render job"});
    const annotate = review.getByRole("button", {name: "Annotate presented frame"});
    const frame = transport.getByRole("spinbutton", {name: "Frame", exact: true});
    const pause = transport.getByRole("button", {name: "Pause", exact: true});
    const pauseAll = transport.getByRole("button", {name: "Pause all", exact: true});
    const stoppedAtGap = async () => {
      await expect(review.getByText(reportAvailability ? "Frame 3 failed to render. Playback stopped." : "Frame 3 is unavailable. Playback stopped.", {exact: true})).toBeVisible();
      await expect(review.getByText("Frame 3 pending. No later interval substituted.", {exact: true})).toHaveCount(0);
      await expect(passes).toHaveValue(scenes[0].id);
      await expect(pause).toHaveCount(0);
      await expect(pauseAll).toHaveCount(0);
      await expect(annotate).toBeDisabled();
    };
    await expect(passes).toHaveValue(scenes[0].id);
    await review.getByRole("button", {name: "Animation", exact: true}).click();
    await transport.getByRole("button", {name: "Play all", exact: true}).click();
    await expect(pauseAll).toBeVisible();
    await expect(frame).toHaveValue("2");
    await stoppedAtGap();

    await transport.getByRole("button", {name: "Play", exact: true}).click();
    await expect(pause).toBeVisible();
    await expect(review.locator("video:not(.review-prebuffer)")).toBeVisible();
    await expect(frame).toHaveValue("1");
    await expect(frame).toHaveValue("2");
    await stoppedAtGap();
    studio.assertHermetic();
  });
}

test("an unreceipted video frame invalidates annotation until a verified frame is presented", async ({page, baseURL}) => {
  const {studio} = await openScene(page, baseURL!, (fixture, routes) => [renderScene(fixture, routes, 0, {omitSecondReceipt: true})]);
  const review = page.locator("main.review-panel");
  const transport = review.getByLabel("Review transport", {exact: true});
  const annotate = review.getByRole("button", {name: "Annotate presented frame"});
  await review.getByRole("button", {name: "Animation", exact: true}).click();
  await expect(annotate).toBeEnabled();
  await transport.getByRole("button", {name: "Play", exact: true}).click();
  await expect(review.getByText("Presented frame 2 has no verified frame receipt", {exact: true})).toBeVisible();
  await expect(annotate).toBeDisabled();
  await expect(review.getByText("Playback complete", {exact: true})).toBeVisible();
  await expect(annotate).toBeDisabled();
  await transport.getByRole("button", {name: "Previous frame", exact: true}).click();
  await expect(annotate).toBeEnabled();
  studio.assertHermetic();
});

test("Play all stops when a buffering gap becomes terminal before failed ranges arrive", async ({page, baseURL}) => {
  const {studio, routes} = await openScene(page, baseURL!, (fixture, mediaRoutes) => [
    renderScene(fixture, mediaRoutes, 1), renderScene(fixture, mediaRoutes, 0, {tail: "pending"}),
  ]);
  const review = page.locator("main.review-panel");
  const transport = review.getByLabel("Review transport", {exact: true});
  const annotate = review.getByRole("button", {name: "Annotate presented frame"});
  const pause = transport.getByRole("button", {name: "Pause", exact: true});
  const pauseAll = transport.getByRole("button", {name: "Pause all", exact: true});
  const pending = studio.snapshot.jobs.find(job => job.job_id === scenes[0].id)!;
  const route = routes.find(route => route.path === `${base}/jobs/${pending.job_id}/availability`)!;
  const availability = route.body as FrameAvailability;
  const failed: JobRecord = {...pending, state: "failed", state_revision: pending.state_revision + 1};
  const failedAvailability: FrameAvailability = {...availability, pending_ranges: [], failed_ranges: [{start: 3, end: 4}], state_revision: 2};
  let releaseAvailability!: () => void;
  const availabilityGate = new Promise<void>(resolve => {releaseAvailability = resolve;});

  await review.getByRole("button", {name: "Animation", exact: true}).click();
  await expect(annotate).toBeEnabled();
  await transport.getByRole("button", {name: "Play all", exact: true}).click();
  await expect(review.getByText("Buffering at frame 3", {exact: true})).toBeVisible();
  await expect(pause).toBeVisible();
  await expect(pauseAll).toBeVisible();
  await expect(annotate).toBeDisabled();
  try {
    route.handler = async request => {
      await availabilityGate;
      await request.fulfill({json: failedAvailability});
    };
    studio.snapshot.jobs = studio.snapshot.jobs.map(job => job.job_id === failed.job_id ? failed : job);
    studio.emit("job.state_changed", failed);
    await expect(review.getByText("Frame 3 is unavailable. Playback stopped.", {exact: true})).toBeVisible();
    await expect(pause).toHaveCount(0);
    await expect(pauseAll).toHaveCount(0);
  } finally {
    releaseAvailability();
  }
  await expect(review.getByText("Frame 3 failed to render. Playback stopped.", {exact: true})).toBeVisible();
  await expect(annotate).toBeDisabled();
  await expect(transport.getByRole("combobox", {name: "Shot / render job"})).toHaveValue(scenes[0].id);
  studio.assertHermetic();
});

test("Play all can replay from a terminal still without losing annotation", async ({page, baseURL}) => {
  const {studio} = await openScene(page, baseURL!, (fixture, routes) => [
    renderScene(fixture, routes, 1), renderScene(fixture, routes, 0, {tail: "retained"}),
  ]);
  const review = page.locator("main.review-panel");
  const transport = review.getByLabel("Review transport", {exact: true});
  const annotate = review.getByRole("button", {name: "Annotate presented frame"});
  const frame = transport.getByRole("spinbutton", {name: "Frame", exact: true});
  const playAll = transport.getByRole("button", {name: "Play all", exact: true});
  const pauseAll = transport.getByRole("button", {name: "Pause all", exact: true});
  const stoppedOnStill = async () => {
    await expect(review.getByText("Video segment unavailable at frame 3. Playback stopped on verified frame.", {exact: true})).toBeVisible();
    await expect(review.getByRole("img", {name: "Available frame 3"})).toBeVisible();
    await expect(transport.getByRole("button", {name: "Pause", exact: true})).toHaveCount(0);
    await expect(pauseAll).toHaveCount(0);
    await expect(frame).toHaveValue("3");
    await expect(annotate).toBeEnabled();
  };
  await review.getByRole("button", {name: "Animation", exact: true}).click();
  await expect(annotate).toBeEnabled();
  await playAll.click();
  await stoppedOnStill();

  await playAll.click();
  await expect(pauseAll).toBeVisible();
  await expect(frame).toHaveValue("1");
  await expect(frame).toHaveValue("2");
  await stoppedOnStill();

  await transport.getByRole("button", {name: "Play", exact: true}).click();
  await expect(transport.getByRole("button", {name: "Pause", exact: true})).toBeVisible();
  await expect(frame).toHaveValue("1");
  await expect(frame).toHaveValue("2");
  await stoppedOnStill();

  await expect.poll(() => page.evaluate(id => JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)!).target, projectId))
    .toMatchObject({source_frame: 3, artifact_id: "sl-01-dormant-macro-f3", render_job_id: scenes[0].id, media_artifact_id: null});
  studio.assertHermetic();
});

test("Pass 1 stays selected while its first frame is pending, then shows the new receipt", async ({page, baseURL}) => {
  const {studio, routes} = await openScene(page, baseURL!, (fixture, mediaRoutes) => [
    renderScene(fixture, mediaRoutes, 1), renderScene(fixture, mediaRoutes, 0, {ready: false}),
  ]);
  const review = page.locator("main.review-panel");
  const passes = review.getByRole("combobox", {name: "Shot / render job"});
  const annotate = review.getByRole("button", {name: "Annotate presented frame"});
  await expect(passes).toHaveValue(scenes[0].id);
  await expect(passes.locator("option:checked")).toHaveText(/^1 · sl-01-dormant-macro/);
  await expect(review.getByText("Frame 1 pending. No ready review image yet.", {exact: true})).toBeVisible();
  await expect(annotate).toBeDisabled();
  await review.getByRole("button", {name: "Animation", exact: true}).click();
  await expect(passes).toHaveValue(scenes[0].id);
  await expect(review.getByText("Frame 1 pending. No later interval substituted.", {exact: true})).toBeVisible();
  await passes.selectOption(scenes[1].id);
  await expect(annotate).toBeEnabled();
  await passes.selectOption(scenes[0].id);
  await expect(review.getByText("Frame 1 pending. No later interval substituted.", {exact: true})).toBeVisible();
  await expect(annotate).toBeDisabled();

  const newlyReadyRoutes: MockRoute[] = [];
  const ready = {...renderScene(studio, newlyReadyRoutes, 0), state_revision: 2};
  for (const route of newlyReadyRoutes) Object.assign(routes.find(existing => existing.path === route.path)!, route);
  studio.snapshot.jobs = studio.snapshot.jobs.map(job => job.job_id === ready.job_id ? ready : job);
  studio.emit("job.state_changed", ready);
  await expect(annotate).toBeEnabled();
  await expect(passes).toHaveValue(scenes[0].id);
  await expect(review.getByRole("spinbutton", {name: "Frame", exact: true})).toHaveValue("1");
  studio.assertHermetic();
});
