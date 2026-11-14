import { defineConfig } from "@playwright/test";

const browsers = [
  { name: "chromium", launchOptions: {} },
  { name: "brave", launchOptions: { executablePath: "/usr/bin/brave-browser" } },
  { name: "edge", launchOptions: { executablePath: "/usr/bin/microsoft-edge" } },
];
const suites = [
  { name: "shell", testMatch: "shell.spec.ts", baseURL: "http://127.0.0.1:4174" },
  { name: "review", testMatch: "review*.spec.ts", baseURL: "http://127.0.0.1:5186" },
  { name: "storyboards", testMatch: "storyboards.spec.ts", baseURL: "http://127.0.0.1:4176" },
];

export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  outputDir: "/tmp/opencode/proscenium-browser-results",
  use: {
    baseURL: "http://127.0.0.1:4174",
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    screenshot: "only-on-failure",
    trace: "off",
  },
  projects: [
    ...browsers.flatMap(browser => suites.map(suite => ({
      name: `${browser.name}-${suite.name}`,
      testMatch: suite.testMatch,
      use: { browserName: "chromium" as const, baseURL: suite.baseURL, launchOptions: browser.launchOptions },
    }))),
    { name: "chromium-review-dpr2", testMatch: "review*.spec.ts", use: { browserName: "chromium", baseURL: "http://127.0.0.1:5186", deviceScaleFactor: 2 } },
  ],
  webServer: [{
    command: "npm run build --workspace apps/frontend && npm exec --workspace apps/frontend -- vite preview --host 127.0.0.1 --port 4174 --strictPort",
    url: "http://127.0.0.1:4174",
    reuseExistingServer: false,
    timeout: 120000,
  }, {
    command: "npx vite --host 127.0.0.1 --port 5186 --strictPort",
    url: "http://127.0.0.1:5186/tests/browser/review-harness.html",
    reuseExistingServer: false,
  }, {
    command: "node tests/browser/storyboards-server.mjs",
    url: "http://127.0.0.1:4176",
    reuseExistingServer: false,
  }],
});
