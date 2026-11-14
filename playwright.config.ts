import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  testMatch: "shell.spec.ts",
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
    { name: "chromium", use: { browserName: "chromium" } },
    { name: "brave", use: { browserName: "chromium", launchOptions: { executablePath: "/usr/bin/brave-browser" } } },
    { name: "edge", use: { browserName: "chromium", launchOptions: { executablePath: "/usr/bin/microsoft-edge" } } },
  ],
  webServer: {
    command: "npm run build --workspace apps/frontend && npm exec --workspace apps/frontend -- vite preview --host 127.0.0.1 --port 4174 --strictPort",
    url: "http://127.0.0.1:4174",
    reuseExistingServer: false,
    timeout: 120000,
  },
});
