import type { Page, Route } from "@playwright/test";

export interface MockRoute {
  method?: string;
  path: string | RegExp;
  status?: number;
  body?: unknown;
  contentType?: string;
  handler?: (route: Route) => Promise<void> | void;
}

/**
 * Hermetic browser fixture guard: every unconfigured external or API request fails
 * loudly so isolated UI tests never contact live models, providers or services.
 */
export async function installFakeStudioApi(page: Page, routes: MockRoute[], origin = "http://127.0.0.1:4179"): Promise<{ calls: Array<{ method: string; url: string }> }> {
  const calls: Array<{ method: string; url: string }> = [];
  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) {
      await route.abort("blockedbyclient");
      throw new Error(`Blocked external browser request: ${request.method()} ${request.url()}`);
    }
    if (!url.pathname.startsWith("/api/") && !url.pathname.startsWith("/internal/")) {
      await route.continue();
      return;
    }
    calls.push({ method: request.method(), url: `${url.pathname}${url.search}` });
    const matched = routes.find(entry =>
      (!entry.method || entry.method.toUpperCase() === request.method())
      && (typeof entry.path === "string" ? entry.path === url.pathname : entry.path.test(url.pathname)),
    );
    if (!matched) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "1.0",
          error: {
            code: "unconfigured_test_route",
            message: `Unconfigured browser test route: ${request.method()} ${url.pathname}`,
            operation_id: null,
            retryable: false,
            details: {},
          },
        }),
      });
      return;
    }
    if (matched.handler) {
      await matched.handler(route);
      return;
    }
    const raw = matched.body instanceof Uint8Array || Buffer.isBuffer(matched.body);
    await route.fulfill({
      status: matched.status ?? 200,
      contentType: matched.contentType ?? (raw ? "application/octet-stream" : "application/json"),
      body: raw ? Buffer.from(matched.body as Uint8Array) : JSON.stringify(matched.body ?? {}),
    });
  });
  return { calls };
}
