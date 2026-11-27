import { expect, test, type Page } from "@playwright/test";
import type { ConfigurationView, OwnerBootstrap, OwnerSession } from "../../apps/frontend/src/api";
import { installFakeStudioApi } from "./fixtures";

declare global {
  interface Window {
    pairingFixture: {
      body: OwnerBootstrap;
      csrf: string | null;
      signal?: AbortSignal | null;
      reply: (response: Response) => void;
    }[];
  }
}

const pairingKey = "proscenium.owner.pairing";
const sessionKey = "proscenium.owner.session";
const codeA = "a".repeat(32);
const codeB = "b".repeat(32);
const session = (name: string): OwnerSession => ({
  schema_version: "1.0", owner_session_id: `owner-${name}`, csrf_token: `token-${name}`,
  expires_at: "2026-11-28T00:00:00Z",
});
const rejection = { error: { message: "Fixture rejection" } };
const configuration = {
  schema_version: "1.0", revision: 0, generation_attempt_limit: 3, profiles: [], services: [],
} satisfies ConfigurationView;

async function setup(page: Page, baseURL: string) {
  const fixture = await installFakeStudioApi(page, [
    { path: "/api/v1/projects", body: { items: [], next_cursor: null } },
    { path: "/api/v1/configuration", body: configuration },
  ], new URL(baseURL).origin);
  await page.addInitScript(() => {
    const fetch = window.fetch.bind(window);
    window.pairingFixture = [];
    window.fetch = (input, init) => {
      if (input !== "/api/v1/owner/session") return fetch(input, init);
      // Deliberately allow an aborted transport to settle late. The component
      // must still ignore both outcomes and protect the newer busy state.
      return new Promise(reply => window.pairingFixture.push({
        reply, signal: init?.signal, body: JSON.parse(String(init?.body)),
        csrf: new Headers(init?.headers).get("X-CSRF-Token"),
      }));
    };
  });
  return fixture;
}

async function capture(page: Page, code: string): Promise<OwnerBootstrap> {
  await page.evaluate(code => { location.hash = `pair=${code}`; }, code);
  await page.waitForFunction(({ code, key }) => !location.hash && JSON.parse(localStorage.getItem(key) ?? "null")?.pairing_code === code, { code, key: pairingKey });
  return page.evaluate(key => JSON.parse(localStorage.getItem(key)!), pairingKey);
}

async function complete(page: Page, index: number, status: number, payload: object) {
  await page.evaluate(({ index, status, payload }) => new Promise<void>(done => {
    const response = new Response(JSON.stringify(payload), { status });
    const json = response.json.bind(response);
    response.json = async () => {
      try { return await json(); }
      finally { requestAnimationFrame(() => done()); }
    };
    window.pairingFixture[index].reply(response);
  }), { index, status, payload });
}

for (const outcome of ["success", "failure"] as const) {
  test(`pairing ignores a superseded ${outcome} and connects with the new operation`, async ({ page, baseURL }) => {
    const fixture = await setup(page, baseURL!);
    await page.goto("/");
    const connect = page.getByRole("button", { name: "Connect this browser" });
    const connecting = page.getByRole("button", { name: "Connecting…" });
    const original = await capture(page, codeA);
    await connect.click();
    await expect(connecting).toBeDisabled();

    const replacement = await capture(page, codeB);
    expect(replacement.operation_id).not.toBe(original.operation_id);
    await expect(connect).toBeEnabled();
    expect(await page.evaluate(() => window.pairingFixture[0].signal?.aborted)).toBe(true);

    await complete(page, 0, outcome === "success" ? 201 : 401, outcome === "success" ? session("old") : rejection);
    await expect(connect).toBeEnabled();
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(await page.evaluate(key => localStorage.getItem(key), sessionKey)).toBeNull();
    expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), pairingKey)).toEqual(replacement);
    expect(fixture.calls).toEqual([]);

    await connect.click();
    await expect(connecting).toBeDisabled();
    const posted = await page.evaluate(() => window.pairingFixture.map(({ body, csrf }) => ({ body, csrf })));
    expect(posted).toEqual([{ body: original, csrf: null }, { body: replacement, csrf: null }]);
    await complete(page, 1, 201, session("new"));
    await expect(page.getByRole("heading", { name: "No active project", exact: true })).toBeVisible();
    expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), sessionKey)).toEqual(session("new"));
    expect(await page.evaluate(key => localStorage.getItem(key), pairingKey)).toBeNull();
    await expect.poll(() => fixture.calls.filter(call => call.url.startsWith("/api/v1/projects")).length).toBe(1);
    expect(fixture.unconfigured).toEqual([]);
  });
}

test("an authenticated Studio discards a pasted pairing fragment without changing its session", async ({ page, baseURL }) => {
  const fixture = await setup(page, baseURL!);
  const active = session("existing");
  await page.addInitScript(({ key, value }) => {
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify(value));
  }, { key: sessionKey, value: active });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "No active project", exact: true })).toBeVisible();
  await page.evaluate(code => { location.hash = `pair=${code}`; }, codeB);
  await expect(page).toHaveURL(new URL("/", baseURL).href);
  expect(await page.evaluate(key => localStorage.getItem(key), pairingKey)).toBeNull();
  expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), sessionKey)).toEqual(active);
  await page.reload();
  await expect(page.getByRole("heading", { name: "No active project", exact: true })).toBeVisible();
  expect(fixture.unconfigured).toEqual([]);
});

test("a failed full-page pairing can be replaced in the same tab with an older session still stored", async ({ page, baseURL }) => {
  const fixture = await setup(page, baseURL!);
  const older = session("existing");
  await page.addInitScript(({ key, value }) => localStorage.setItem(key, JSON.stringify(value)), { key: sessionKey, value: older });
  await page.goto(`/#pair=${codeA}`);
  await expect(page).toHaveURL(new URL("/", baseURL).href);
  const connect = page.getByRole("button", { name: "Connect this browser" });
  const alert = page.getByRole("alert");
  const original: OwnerBootstrap = await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), pairingKey);
  expect(original.pairing_code).toBe(codeA);
  await connect.click();
  await complete(page, 0, 401, rejection);
  await expect(alert).toContainText("Pairing was not confirmed");
  expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), sessionKey)).toEqual(older);

  const replacement = await capture(page, codeB);
  await expect(page).toHaveURL(new URL("/", baseURL).href);
  expect(replacement.operation_id).not.toBe(original.operation_id);
  expect(replacement.client_nonce).not.toBe(original.client_nonce);
  await expect(alert).toHaveCount(0);
  await expect(connect).toBeEnabled();
  await connect.click();
  expect(await page.evaluate(() => window.pairingFixture.map(({ body }) => body))).toEqual([original, replacement]);
  await complete(page, 1, 201, session("replacement"));
  await expect(page.getByRole("heading", { name: "No active project", exact: true })).toBeVisible();
  expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), sessionKey)).toEqual(session("replacement"));
  expect(await page.evaluate(key => localStorage.getItem(key), pairingKey)).toBeNull();
  expect(fixture.unconfigured).toEqual([]);
});
