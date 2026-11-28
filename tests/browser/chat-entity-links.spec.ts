import { expect, test } from "@playwright/test";
import type { RelationshipNodeSummary, RelationshipPage } from "../../apps/frontend/src/api";
import { base, deferred, job, pageOf, projectId, request, retained, stamp, StudioFixture, version } from "./studio-fixture";

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

test("an Assets selection supersedes a pending Copilot citation without changing the new Current view target", async ({ page, baseURL }) => {
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
    studio.emit("assistant.completed", { message_id: "slow-citation", text: `Inspect ${imageId}.` });
    const catalogue = page.getByRole("region", { name: "Version catalogue" });
    await catalogue.getByRole("button", { name: `Version history for ${itemName}` }).click();
    const assetVersion = catalogue.getByTitle(`${itemName} published v3 (${latestId})`, { exact: true });
    await expect(assetVersion).toBeVisible();
    const cancelled = page.waitForEvent("requestfailed", request => new URL(request.url()).pathname === imagePath);
    await page.locator(".assistant-message").getByRole("button", { name: "Concept image", exact: true }).click();
    await started.promise;
    await assetVersion.click();
    const review = page.getByRole("main", { name: "Review workspace" });
    const heading = review.getByRole("heading", { name: `${itemName} published v3`, exact: true });
    await expect(heading).toBeVisible();
    release.release();
    await finished.promise;
    await cancelled;
    await expect(heading).toBeVisible();
    await expect(review.locator(".retained-review")).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);

    await expect(page.getByRole("button", { name: "Choose request target and references" })).toHaveAttribute("title", `Target: Current view · Asset · ${itemName}`);
    await page.getByRole("textbox", { name: "Message Copilot" }).fill("Use the version opened from Assets.");
    await page.getByRole("button", { name: "Queue message", exact: true }).click();
    await expect.poll(() => studio.submissions.length).toBe(1);
    expect(studio.contexts[0]).toMatchObject({ version_id: latestId, request_target: { kind: "item", version: { item_id: itemId, version_id: latestId } }, attachments: [] });
    studio.assertHermetic();
  } finally { release.release(); }
});

for (const surface of ["Outliner", "View latest"] as const) for (const newer of [surface, "Copilot"] as const) {
  test(`the newer ${newer} click wins when ${surface} and Copilot are both waiting for reads`, async ({ page, baseURL }) => {
    const studio = new StudioFixture(page, baseURL!);
    const image = prepare(studio);
    const [initial, next] = studio.versionRecords;
    const [root, leaf] = [initial, next].map((record): Extract<RelationshipNodeSummary, { kind: "version" }> => ({
      kind: "version", ref: { item_id: record.item_id, version_id: record.version_id }, item_kind: record.kind,
      label: record.name, is_current: record === next, is_selected: false,
    }));
    const media = { started: deferred(), release: deferred(), finished: deferred(), path: imagePath };
    const versions = { started: deferred(), release: deferred(), finished: deferred(), path: versionPath };
    let holdVersions = false;
    await page.addInitScript(record => localStorage.setItem(`proscenium.owner.view.version.${record.project_id}`, JSON.stringify(record)), initial);
    await studio.install({ savedLayout: true, routes: [
      { path: imagePath, handler: async route => {
        media.started.release();
        try { await media.release.promise; await route.fulfill({ json: image }); }
        finally { media.finished.release(); }
      } },
      { path: versionPath, handler: async route => {
        if (!holdVersions) return route.fulfill({ json: pageOf(studio.versionRecords) });
        versions.started.release();
        try { await versions.release.promise; await route.fulfill({ json: pageOf(studio.versionRecords) }); }
        finally { versions.finished.release(); }
      } },
      { path: new RegExp(`${base}/versions/[^/]+/relationships$`), handler: route => {
        const viewingInitial = new URL(route.request().url()).pathname.endsWith(`/${versionId}/relationships`);
        const body: RelationshipPage = {
          schema_version: "1.0", project_id: projectId, root: viewingInitial ? root : leaf, direction: "uses", relationship_revision: 1,
          items: viewingInitial ? [{ relation: "declared_dependency", from: root, to: leaf, details: {} }] : [], nodes: [root, leaf],
          coverage: { state: "complete", reasons: [] }, next_cursor: null,
        };
        return route.fulfill({ json: body });
      } },
    ] });
    try {
      await studio.open();
      await studio.ready();
      const review = page.getByRole("main", { name: "Review workspace" });
      const initialHeading = review.getByRole("heading", { name: initial.name, exact: true });
      const outliner = page.getByRole("region", { name: "Published Outliner" });
      const openVersion = surface === "Outliner" ? outliner.getByRole("treeitem", { name: next.name, exact: true }).getByRole("button", { name: "Open version", exact: true })
        : review.getByRole("button", { name: "View v3", exact: true });
      if (surface === "Outliner") await page.getByRole("navigation", { name: "Navigator tabs" }).getByRole("button", { name: surface, exact: true }).click();
      await expect(initialHeading).toBeVisible();
      await expect(openVersion).toBeVisible();
      const originalOutliner = surface === "Outliner" ? await outliner.elementHandle() : null;
      studio.emit("assistant.completed", { message_id: "racing-citation", text: `Inspect ${imageId}.` });
      const citation = page.locator(".assistant-message").getByRole("button", { name: "Concept image", exact: true });
      await expect(citation).toBeVisible();
      holdVersions = true;
      const [old, latest] = newer === "Copilot" ? [versions, media] : [media, versions];
      const [oldClick, latestClick] = newer === "Copilot" ? [openVersion, citation] : [citation, openVersion];
      const cancelled = page.waitForEvent("requestfailed", request => new URL(request.url()).pathname === old.path);
      await oldClick.click();
      await old.started.promise;
      await latestClick.click();
      await latest.started.promise;
      old.release.release();
      await old.finished.promise;
      await expect(initialHeading).toBeVisible();
      await cancelled;
      if (originalOutliner) expect(await outliner.evaluate((current, original) => current === original, originalOutliner)).toBe(true);
      await expect(page.getByRole("alert")).toHaveCount(0);

      latest.release.release();
      await latest.finished.promise;
      await expect(review.getByRole("heading", { name: newer === "Copilot" ? `Retained concept image · ${imageId}` : next.name, exact: true })).toBeVisible();
      await expect(page.getByRole("alert")).toHaveCount(0);
      studio.assertHermetic();
    } finally { media.release.release(); versions.release.release(); }
  });
}

test("a cited Review image can be re-roled or removed from the draft without changing Current view", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  prepare(studio);
  await studio.install({ savedLayout: true });
  await studio.open();
  await studio.ready();
  studio.emit("assistant.completed", { message_id: "attached-citation", text: `Inspect ${imageId}.` });
  const cite = page.locator(".assistant-message").getByRole("button", { name: "Concept image", exact: true });
  const review = page.getByRole("main", { name: "Review workspace" });
  const heading = review.getByRole("heading", { name: `Retained concept image · ${imageId}` });
  const bar = page.getByRole("button", { name: "Choose request target and references" });
  const panel = page.getByRole("region", { name: "Request target", exact: true });
  const images = panel.getByRole("region", { name: "Attached images", exact: true });
  const role = images.getByRole("combobox", { name: "Review image 1 role" });
  const remove = images.getByRole("button", { name: "Remove review image 1" });
  const currentView = panel.getByRole("button", { name: "Current view", exact: true });
  const draft = page.getByRole("textbox", { name: "Message Copilot" });
  const queue = page.getByRole("button", { name: "Queue message", exact: true });

  await cite.click();
  await expect(heading).toBeVisible();
  await draft.fill("Use the inspected image as a mask.");
  await bar.click();
  await expect(currentView).toHaveAttribute("aria-pressed", "true");
  await expect(bar).toContainText("+1 context");
  await expect(role.locator("option")).toHaveText(["reference", "concept", "render", "markup", "mask"]);
  await role.selectOption("mask");
  await expect(remove).toBeVisible();
  await queue.click();
  await expect.poll(() => studio.submissions.length).toBe(1);
  expect(studio.contexts[0]).toMatchObject({ request_target: { kind: "project" }, attachments: [{ artifact_id: imageId, role: "mask" }] });
  await expect(draft).toHaveValue("");

  await draft.fill("Do not include the inspected image.");
  await bar.click();
  await expect(role).toHaveValue("reference"); // Successful submission clears draft-only overrides.
  await remove.click();
  await expect(images).toHaveCount(0);
  await expect(bar).not.toContainText("context");
  await expect(currentView).toHaveAttribute("aria-pressed", "true");
  await expect(heading).toBeVisible();

  await review.getByRole("button", { name: "Clear preview", exact: true }).click();
  await expect(heading).toHaveCount(0);
  await cite.click();
  await expect(role).toHaveValue("reference"); // Leaving Review prunes the removal override.
  await remove.click();
  await queue.click();
  await expect.poll(() => studio.submissions.length).toBe(2);
  expect(studio.contexts[1]).toMatchObject({ request_target: { kind: "project" }, attachments: [] });
  await expect(draft).toHaveValue("");
  await bar.click();
  await expect(currentView).toHaveAttribute("aria-pressed", "true");
  await expect(heading).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  studio.assertHermetic();
});

test("Target shows a published Review frame as read-only evidence and submits its canonical attachment", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  const image = prepare(studio);
  const current = studio.versionRecords.find(record => record.version_id === latestId)!;
  current.artifact_ids = [image.artifact_id];
  await studio.install({ savedLayout: true });
  await studio.open();
  await studio.ready();
  await page.getByRole("region", { name: "Version catalogue" }).getByTitle(itemName, { exact: true }).click();
  await expect(page.getByRole("main", { name: "Review workspace" }).getByRole("heading", { name: current.name, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Annotate presented frame" })).toBeEnabled();
  await page.getByRole("textbox", { name: "Message Copilot" }).fill("Use the published Review frame.");
  await page.getByRole("button", { name: "Choose request target and references" }).click();
  const panel = page.getByRole("region", { name: "Request target", exact: true });
  const images = panel.getByRole("region", { name: "Attached images", exact: true });
  await expect(panel.getByRole("button", { name: "Current view", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(images.getByRole("img", { name: "Review image 1" })).toBeVisible();
  await expect(images.getByText("Review frame · concept", { exact: true })).toBeVisible();
  await expect(images.getByText("Switch target mode to exclude Review frame evidence.")).toBeVisible();
  await expect(images.getByRole("combobox")).toHaveCount(0);
  await expect(images.getByRole("button", { name: /^Remove/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Queue message", exact: true }).click();
  await expect.poll(() => studio.submissions.length).toBe(1);
  expect(studio.contexts[0]).toMatchObject({
    request_target: { kind: "item", version: { item_id: itemId, version_id: latestId } },
    target: { artifact_id: imageId, origin: "concept", version_id: latestId },
    attachments: [{ artifact_id: imageId, role: "concept" }],
  });
  await expect(page.getByRole("alert")).toHaveCount(0);
  studio.assertHermetic();
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

test("re-clicking the same Critic citation retries a failed detail read", async ({ page, baseURL }) => {
  const studio = new StudioFixture(page, baseURL!);
  prepare(studio);
  let detailReads = 0;
  await studio.install({ savedLayout: true, routes: [{ path: reviewPath, handler: route => {
    if (++detailReads === 1) return route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "Temporary review failure", details: {} } } });
    return route.fulfill({ json: studio.reviews[0] });
  } }] });
  await studio.open();
  await studio.ready();
  studio.emit("assistant.completed", { message_id: "retry-review-citation", text: `Check ${reviewId}.` });
  const citation = page.locator(".assistant-message").getByRole("button", { name: reviewTitle, exact: true });
  const critic = page.getByRole("region", { name: "Critic", exact: true });
  await citation.click();
  await expect(critic.getByRole("alert")).toContainText(`Could not open review ${reviewId}.`);
  expect(detailReads).toBe(1);
  await page.getByRole("navigation", { name: "Assistant tabs" }).getByRole("button", { name: "Copilot", exact: true }).click();
  await citation.click();
  await expect(critic.locator(".critic-thread")).toContainText(studio.reviews[0].criteria);
  expect(detailReads).toBe(2);
  await expect(critic.getByRole("alert")).toHaveCount(0);
  studio.assertHermetic();
});
