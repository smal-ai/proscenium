import { expect, test } from "@playwright/test";
import { permission, project, SyntheticStudio, version } from "./shell.fixture";

test.describe("W04 shell · synthetic HTTP/socket evidence", () => {
  test("pairs from a fragment, removes it and retries the same operation/nonce", async ({ page }) => {
    const studio = new SyntheticStudio(); studio.abortPairing = true;
    await studio.install(page, false);
    const consoleOutput: string[] = [];
    page.on("console", message => consoleOutput.push(message.text()));
    await page.goto("/#pair=synthetic-pairing-code-0000000000000000000000");
    await expect(page).toHaveURL("http://127.0.0.1:4174/");
    await page.getByRole("button", { name: "Connect this browser" }).click();
    await expect(page.getByRole("alert")).toContainText("Pairing was not confirmed");
    await page.reload();
    await page.getByRole("button", { name: "Connect this browser" }).click();
    await expect(page.getByRole("banner")).toBeVisible();
    expect(studio.pairAttempts).toHaveLength(2);
    expect(studio.pairAttempts[1]).toEqual(studio.pairAttempts[0]);
    expect(consoleOutput.join("\n")).not.toContain("synthetic-pairing-code");
    const cookies = await page.context().cookies();
    expect(cookies.find(c => c.name === "synthetic_owner")?.httpOnly).toBe(true);
    expect(await page.evaluate(() => localStorage.getItem("proscenium.owner.pairing"))).toBeNull();
  });

  test("renders Full HD geometry, paginated unsent history and offline metadata safely", async ({ page }) => {
    const studio = new SyntheticStudio(); studio.pagination = true;
    studio.record({ schema_version: "1.0", request_id: "request-never-dispatched", project_id: project.project_id, type: "queue", text: "Original held text <img src=x onerror=alert(1)>", context_id: "context-original" });
    studio.record({ schema_version: "1.0", request_id: "request-page-2", project_id: project.project_id, type: "queue", text: "Second page, never sent to native runtime", context_id: "context-original" });
    await studio.install(page); await page.goto("/");
    await expect(page.getByText("Second page, never sent to native runtime")).toBeVisible();
    expect(studio.pageReads.some(p => p.includes("cursor=synthetic-page-2"))).toBe(true);
    expect(await page.locator(".user-message img").count()).toBe(0);
    const geometry = await page.evaluate(() => ({
      header: document.querySelector(".studio-header")!.getBoundingClientRect().height,
      left: document.querySelector(".left-panel")!.getBoundingClientRect().width,
      right: document.querySelector(".right-panel")!.getBoundingClientRect().width,
      centre: document.querySelector(".review-panel")!.getBoundingClientRect().width,
      height: document.documentElement.scrollHeight,
      width: document.documentElement.scrollWidth,
    }));
    expect(geometry).toEqual({ header: 48, left: 280, right: 360, centre: 1280, height: 1080, width: 1920 });
    await page.getByRole("button", { name: "Services & models" }).click();
    await expect(page.getByText("synthetic-image-model", { exact: true })).toBeVisible();
    await expect(page.getByText("Offline", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Services & models" }).click();
    await page.locator("body").click({ position: { x: 900, y: 500 } });
    await page.keyboard.press("1");
    await expect(page.locator('[data-review-mode="3d_inspection"]')).toBeVisible();
    await expect(page.locator("[data-review-mode]")).toHaveCount(1);
    expect(studio.posts).toHaveLength(0);
  });

  test("freezes original version/images, reconciles lost ACK without replay and retains draft", async ({ page }) => {
    const studio = new SyntheticStudio(); studio.abortNextRequest = true;
    await studio.install(page); await page.goto("/");
    await page.getByRole("button", { name: "Lighting study" }).click();
    await page.getByRole("button", { name: /Historical publication/ }).click();
    await page.getByLabel("Attach images").setInputFiles({ name: "reference.png", mimeType: "image/png", buffer: Buffer.from("synthetic-image-bytes") });
    await expect(page.getByText("Next request context · 1 images")).toBeVisible();
    await page.getByLabel("Message Copilot").fill("Keep the original lighting direction");
    await page.getByRole("button", { name: "Queue message" }).click();
    await expect(page.getByText("Request awaiting confirmation", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Message Copilot")).toHaveValue("Keep the original lighting direction");
    const captured = [...studio.contexts.values()][0];
    expect(captured.version_id).toBe("version-old");
    expect(captured.attachments).toEqual([{ artifact_id: "image-ready", role: "reference" }]);
    await page.getByRole("button", { name: /Current publication/ }).click();
    await page.getByRole("button", { name: "Reconcile original request / retry same ID" }).click();
    await expect(page.getByText("Request awaiting confirmation", { exact: true })).toHaveCount(0);
    await expect(page.locator(".user-message")).toHaveCount(1);
    expect(studio.posts.filter(p => p.path.endsWith("/requests"))).toHaveLength(1);
    expect([...studio.contexts.values()][0]).toEqual(captured);
    await page.reload();
    await expect(page.locator(".user-message")).toHaveCount(1);
    expect(studio.posts.filter(p => p.path.endsWith("/requests"))).toHaveLength(1);
  });

  test("same-ID retry reads original first, while Stop bypasses an ambiguous Queue", async ({ page }) => {
    const studio = new SyntheticStudio(); studio.rejectNextRequest = true;
    await studio.install(page); await page.goto("/");
    await page.getByLabel("Message Copilot").fill("Original queued operation");
    await page.getByRole("button", { name: "Queue message" }).click();
    await expect(page.getByText("Request awaiting confirmation", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Stop", exact: true }).click();
    await expect.poll(() => studio.posts.filter(p => p.path.endsWith("/requests"))).toHaveLength(2);
    expect(studio.posts.filter(p => p.path.endsWith("/requests"))[1].body).toMatchObject({ type: "stop", target_work_id: "work-test" });
    await page.getByRole("button", { name: "Reconcile original request / retry same ID" }).click();
    await expect(page.getByText("Request awaiting confirmation", { exact: true })).toHaveCount(0);
    const queues = studio.posts.filter(p => p.path.endsWith("/requests") && p.body.type === "queue");
    expect(queues).toHaveLength(2); expect(queues[0].body).toEqual(queues[1].body); expect(queues[0].key).toBe(queues[1].key);
    expect(studio.pageReads.some(p => p.endsWith(`/requests/${queues[0].body.request_id}`))).toBe(true);
  });

  test("requires explicit Steer target and durable exact decision, held cancellation and Resume", async ({ page }) => {
    const studio = new SyntheticStudio(); studio.snapshot.pending_decisions = [structuredClone(permission)];
    studio.record({ schema_version: "1.0", request_id: "request-held", project_id: project.project_id, type: "queue", text: "Held request", context_id: "context-held" });
    await studio.install(page); await page.goto("/");
    await page.getByRole("button", { name: "Approve once" }).click();
    await expect(page.getByRole("button", { name: "Approve once" })).toHaveCount(0);
    expect(studio.posts.find(p => p.path.endsWith("/answer"))?.body).toMatchObject({ expected_revision: 7, fingerprint: permission.fingerprint, action: "approve_once", project_id: project.project_id });
    await page.getByLabel("Request mode").selectOption("steer");
    await page.getByLabel("Message Copilot").fill("Correct the active lighting task");
    await expect(page.getByRole("button", { name: "Steer active work", exact: true })).toBeDisabled();
    await page.getByLabel("Steer active work target").selectOption("work-test");
    await page.getByRole("button", { name: "Steer active work", exact: true }).click();
    await expect.poll(() => studio.requests.filter(r => r.submission.type === "steer").length).toBe(1);
    await page.locator('[data-request-id="request-held"]').getByRole("button", { name: "Cancel held request" }).click();
    await expect.poll(() => studio.requests[0].receipt.state).toBe("cancelled");
    await page.getByLabel("Resume mode").selectOption("release_held_queue");
    await page.getByRole("button", { name: "Resume", exact: true }).click();
    await expect.poll(() => studio.posts.find(p => p.path.endsWith("/resume"))?.body.mode).toBe("release_held_queue");
    expect(studio.posts.find(p => p.path.endsWith("/resume"))?.body.work_id).toBe("work-test");
  });

  test("registers workstation folders, rebinds and explicitly activates without sending model work", async ({ page }) => {
    const studio = new SyntheticStudio(); await studio.install(page); await page.goto("/");
    await page.getByRole("button", { name: project.name, exact: true }).click();
    await page.getByLabel("Workstation folder path").fill("/workstation/new-project");
    await page.getByLabel("New project name").fill("New production");
    await page.getByRole("button", { name: "Create project", exact: true }).click();
    await expect(page.getByLabel("Saved project")).toHaveValue("project-opened");
    expect(studio.projects.find(p => p.project_id === "project-opened")?.state).toBe("inactive");
    await page.getByLabel("Workstation folder path").fill("/workstation/moved-project");
    await page.getByRole("button", { name: "Rebind selected project to this path" }).click();
    await expect(page.locator(".project-location code")).toHaveText("/workstation/moved-project");
    await page.getByRole("button", { name: "Activate selected project" }).click();
    await expect(page.getByRole("button", { name: "New production", exact: true })).toBeVisible();
    expect(studio.posts.some(p => p.path.endsWith("/requests"))).toBe(false);
    expect(studio.posts.find(p => p.path.endsWith("/activate"))?.body.expected_revision).toBe(1);
    await page.getByRole("button", { name: "New production", exact: true }).click();
    await page.getByLabel("Workstation folder path").fill("/workstation/existing");
    await page.getByRole("button", { name: "Open folder", exact: true }).click();
    await expect.poll(() => studio.posts.filter(p => p.path.endsWith("/projects"))).toHaveLength(2);
    expect(studio.posts.filter(p => p.path.endsWith("/projects"))[1].body.create).toBe(false);
  });

  test("deduplicates replay, repairs socket gaps and reconnects without moving a historical selection", async ({ page }) => {
    const studio = new SyntheticStudio(); await studio.install(page); await page.goto("/");
    await expect(page.getByText("Connected", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Lighting study" }).click();
    await page.getByRole("button", { name: /Historical publication/ }).click();
    const delta = studio.emit("assistant.delta", { message_id: "message-test", text: "Part ", native_agent_id: null });
    studio.sockets.at(-1)!.send(JSON.stringify(delta));
    studio.emit("assistant.delta", { message_id: "message-test", text: "two", native_agent_id: null }, false);
    studio.emit("assistant.completed", { message_id: "message-test", text: "Authoritative final public text", native_agent_id: null });
    await expect(page.locator(".assistant-message")).toHaveCount(1);
    await expect(page.locator(".assistant-message p")).toHaveText("Authoritative final public text");
    studio.emit("publication.created", { schema_version: "1.0", publication_id: "publication-next", project_id: project.project_id, versions: [version("version-next")] });
    await expect(page.locator(".source-identity")).toContainText("version-old");
    const before = studio.posts.length;
    studio.sockets.at(-1)!.close({ code: 1012, reason: "Synthetic disconnect" });
    await expect.poll(() => studio.sockets.length, { timeout: 10000 }).toBe(2);
    await expect(page.getByText("Connected", { exact: true })).toBeVisible();
    await expect(page.locator(".assistant-message")).toHaveCount(1);
    expect(studio.posts.length).toBe(before);
    studio.sockets.at(-1)!.send(JSON.stringify({ schema_version: "1.0", type: "resync_required", project_id: project.project_id, reason: "cursor_expired" }));
    await expect(page.locator(".assistant-message p")).toHaveText("Authoritative final public text");
    await page.screenshot({ path: "/tmp/opencode/proscenium-w04-browser-results/synthetic-full-hd.png" });
  });
});
