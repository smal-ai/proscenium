import { expect, test } from "@playwright/test";
import { base, deferred, job, projectId, request, retained, stamp, StudioFixture, version } from "./studio-fixture";

const jobId = "generation-eed081ad00b22ddeca37d74d";
const imageId = `${jobId}-artifact-0`;
const provenanceId = `${jobId}-provenance`;
const reviewId = "review-founder-portrait";
const itemId = "asset-founder-portrait-2";
const versionId = `${itemId}-v2`;
const latestId = `${itemId}-v3`;
const imagePath = `${base}/artifacts/${imageId}`;
const versionPath = `${base}/items/${itemId}/versions`;
const reviewPath = `${base}/reviews/${reviewId}`;
const itemName = "Founder Portrait 2";
const reviewTitle = "Founder portrait likeness";
const ids = [jobId, imageId, provenanceId, reviewId, versionId, itemId];
const reply = `Generation job ${jobId} produced a concept (${imageId}) and provenance ${provenanceId}. VLM review: "${reviewId}". Published ${versionId}. Item: ${itemId}.

${ids.map(id => `\`${id}\``).join(", ")}

\`\`\`text
${ids.join("\n")}
\`\`\``;

function prepare(studio: StudioFixture) {
  const image = retained(imageId, "unrecorded");
  const provenance = retained(provenanceId, "unrecorded", "application/json");
  Object.assign(provenance.artifact, { kind: "document", purpose: "metadata" });
  studio.retained = [image, provenance];
  studio.snapshot.jobs = [job(jobId, "succeeded")];
  studio.snapshot.items = [{ schema_version: "1.0", project_id: projectId, item_id: itemId, kind: "asset", name: itemName, current_version_id: latestId, selected_version_id: versionId, state_revision: 3 }];
  studio.versionRecords = [version(itemId, versionId, `${itemName} published v2`), version(itemId, latestId, `${itemName} published v3`)].map(record => ({ ...record, artifact_ids: [] }));
  studio.reviews = [{ schema_version: "1.0", project_id: projectId, ticket: { schema_version: "1.0", review_id: reviewId, job_id: "vlm-only-job", status: "ready", title: reviewTitle, created_at: stamp, revision: 3, human_review_state: "resolved", awaiting_human: false }, criteria: "Check the founder portrait against the brief.", targets: [], telemetry_artifact_ids: [], findings: [] }];
  studio.requests = [request("user", `Does ${itemId} have a publication?`)];
  return image.artifact;
}

test("Copilot citations open the exact publication, fresh media and resolved Critic thread", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const image = prepare(studio);
  let artifactReads = 0;
  let revoked = false;
  await studio.install({ savedLayout: true, routes: [{ path: imagePath, handler: route => {
    artifactReads++;
    return route.fulfill({ json: { ...image, state: revoked ? "unavailable" : "ready" } });
  } }] });
  await studio.open();
  await studio.ready();
  const assistantTabs = page.getByRole("navigation", { name: "Assistant tabs" });
  const critic = assistantTabs.getByRole("button", { name: "Critic", exact: true });
  const copilot = assistantTabs.getByRole("button", { name: "Copilot", exact: true });
  const filter = page.getByRole("group", { name: "Review filter" });
  await critic.click();
  await expect(filter.getByRole("button", { name: "Pending", exact: true })).toHaveAttribute("aria-pressed", "true");
  await copilot.click();
  // Studio surfaces may already have read catalogue metadata while opening the project.
  const citationReads = () => studio.guard!.calls.filter(call => call.url.startsWith(versionPath) || call.url === reviewPath).length;
  const beforeReply = citationReads();

  studio.emit("assistant.completed", { message_id: "project-citations", text: reply });
  const message = page.locator(".assistant-message");
  for (const [label, rawId, kind] of [["Concept image", imageId, "artifact"], [reviewTitle, reviewId, "review"], [`${itemName} · v2`, versionId, "version"], [itemName, itemId, "item"]]) {
    const buttons = message.getByRole("button", { name: label, exact: true });
    await expect(buttons).toHaveCount(2);
    for (const button of await buttons.all()) {
      await expect(button).toHaveAttribute("title", rawId);
      await expect(button).toHaveAttribute("data-entity-kind", kind);
    }
  }
  await expect(message.locator("button, a")).toHaveCount(8);
  await expect(message.locator("p code")).toHaveText([jobId, provenanceId]);
  await expect(message.locator("pre code")).toHaveText(ids.join("\n"), { useInnerText: true });
  await expect(message.locator("pre button, pre a")).toHaveCount(0);
  await expect(page.locator(".user-message button, .user-message a")).toHaveCount(0);
  expect(artifactReads).toBe(0);
  expect(citationReads()).toBe(beforeReply);

  const review = page.getByRole("main", { name: "Review workspace" });
  const current = review.getByRole("heading", { name: `${itemName} published v2`, exact: true });
  const versionButton = message.getByRole("button", { name: `${itemName} · v2`, exact: true }).first();
  await page.getByRole("button", { name: "Chat focus", exact: true }).click();
  await expect(review).toBeHidden();
  const versionRead = page.waitForResponse(response => new URL(response.url()).pathname === versionPath);
  await versionButton.press("Enter");
  expect((await versionRead).status()).toBe(200);
  await expect(current).toBeVisible();
  await expect(page.getByRole("button", { name: "Chat focus", exact: true })).toHaveAttribute("aria-pressed", "false");

  const imageButton = message.getByRole("button", { name: "Concept image", exact: true }).first();
  await imageButton.click();
  await expect(review.getByRole("heading", { name: `Retained concept image · ${imageId}` })).toBeVisible();
  expect(artifactReads).toBe(1);
  await message.getByRole("button", { name: itemName, exact: true }).first().click();
  await expect(current).toBeVisible(); // The owner-selected v2, not head v3.
  revoked = true; // Detail changes before the loaded retained summary is refreshed.
  await imageButton.click();
  await expect(page.getByRole("alert")).toHaveText("The exact artefact is no longer available to inspect.");
  expect(artifactReads).toBe(2);
  await expect(current).toBeVisible();

  studio.versionRecords = studio.versionRecords.filter(record => record.version_id !== versionId);
  await versionButton.click();
  await expect(page.getByRole("alert")).toHaveText(`Exact publication ${versionId} is unavailable. No other version was opened.`);
  await expect(current).toBeVisible();

  const detail = page.waitForResponse(response => new URL(response.url()).pathname === reviewPath);
  await message.getByRole("button", { name: reviewTitle, exact: true }).first().click();
  expect((await detail).status()).toBe(200);
  await expect(critic).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".critic-thread-title")).toHaveText(reviewTitle);
  await expect(page.locator(".critic-thread")).toContainText(studio.reviews[0].criteria);
  await expect(filter.getByRole("button", { name: "All", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Back to reviews" }).click();
  await expect(page.getByRole("list", { name: "Reviews", exact: true }).getByText(reviewTitle)).toBeVisible();
  await copilot.click();
  await critic.click();
  await expect(page.locator(".critic-thread")).toHaveCount(0); // Consumed nonce does not reopen the thread.
  studio.assertHermetic();
});

test("a stale media click cannot replace a newer publication click", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const image = prepare(studio);
  const started = deferred(), release = deferred(), finished = deferred();
  await studio.install({ savedLayout: true, routes: [{ path: imagePath, handler: async route => {
    started.release();
    try { await release.promise; await route.fulfill({ json: image }); }
    finally { finished.release(); }
  } }] });
  try {
    await studio.open();
    await studio.ready();
    studio.emit("assistant.completed", { message_id: "citations", text: reply });
    const message = page.locator(".assistant-message");
    const imageRequest = page.waitForEvent("requestfailed", request => new URL(request.url()).pathname === imagePath);
    await message.getByRole("button", { name: "Concept image", exact: true }).first().click();
    await started.promise;
    await message.getByRole("button", { name: `${itemName} · v2`, exact: true }).first().click();
    await imageRequest;
    const review = page.getByRole("main", { name: "Review workspace" });
    const current = review.getByRole("heading", { name: `${itemName} published v2`, exact: true });
    await expect(current).toBeVisible();
    release.release();
    await finished.promise;
    await expect(current).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    studio.assertHermetic();
  } finally { release.release(); }
});

for (const update of ["revokes", "omits"] as const) {
  test(`a committed retained refresh that ${update} the artefact fences a delayed ready click`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    const image = prepare(studio);
    const started = deferred(), release = deferred(), finished = deferred();
    await studio.install({ savedLayout: true, routes: [{ path: imagePath, handler: async route => {
      started.release();
      try { await release.promise; await route.fulfill({ json: image }); }
      finally { finished.release(); }
    } }] });
    try {
      await studio.open();
      await studio.ready();
      studio.emit("assistant.completed", { message_id: "revocation-race", text: reply });
      const message = page.locator(".assistant-message");
      const review = page.getByRole("main", { name: "Review workspace" });
      const current = review.getByRole("heading", { name: `${itemName} published v2`, exact: true });
      await message.getByRole("button", { name: `${itemName} · v2`, exact: true }).first().click();
      await expect(current).toBeVisible();
      const imageButtons = message.getByRole("button", { name: "Concept image", exact: true });
      const cancelled = page.waitForEvent("requestfailed", request => new URL(request.url()).pathname === imagePath);
      await imageButtons.first().click();
      await started.promise;

      studio.retained = studio.retained.flatMap(summary => summary.artifact.artifact_id !== imageId ? [summary]
        : update === "omits" ? [] : [{ ...summary, artifact: { ...image, state: "unavailable" } }]);
      studio.emit("publication.created", { project_id: projectId, versions: [] });
      await expect(imageButtons).toHaveCount(0); // The newer retained list has committed.
      release.release();
      await finished.promise;
      await expect(page.getByRole("alert")).toHaveText("The exact artefact is no longer available to inspect.");
      await cancelled;
      await expect(current).toBeVisible();
      await expect(review.locator(".retained-review")).toHaveCount(0);
      studio.assertHermetic();
    } finally { release.release(); }
  });
}

test("the detail read can resolve a pending cached Critic citation and open it under All", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  prepare(studio);
  const [resolved] = studio.reviews;
  studio.reviews = [{ ...resolved, ticket: { ...resolved.ticket, status: "pending", revision: 2, human_review_state: "pending", awaiting_human: true } }];
  const started = deferred(), release = deferred();
  await studio.install({ savedLayout: true, routes: [{ path: reviewPath, handler: async route => {
    started.release();
    await release.promise;
    await route.fulfill({ json: resolved });
  } }] });
  try {
    await studio.open();
    await studio.ready();
    studio.emit("assistant.completed", { message_id: "cached-review-citation", text: `Check ${reviewId}.` });
    await page.locator(".assistant-message").getByRole("button", { name: reviewTitle, exact: true }).click();
    await started.promise;
    const critic = page.getByRole("region", { name: "Critic", exact: true });
    await expect(critic).toBeVisible();
    const filter = critic.getByRole("group", { name: "Review filter" });
    const pending = filter.getByRole("button", { name: /^Pending/ });
    await expect(critic.locator(".critic-thread-title")).toHaveText(reviewTitle);
    await expect(pending).toHaveAttribute("aria-pressed", "true");
    release.release();
    await expect(filter.getByRole("button", { name: "All", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(critic.locator(".critic-thread-title")).toHaveText(reviewTitle);
    await expect(critic.getByRole("button", { name: "Done without changes" })).toHaveCount(0);
    await pending.click();
    await expect(pending).toHaveAttribute("aria-pressed", "true");
    studio.assertHermetic();
  } finally { release.release(); }
});
