import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "storyboards.spec.ts",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  outputDir: "/tmp/opencode/proscenium-w06-browser-results",
  use: { baseURL: "http://127.0.0.1:4176", viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 },
  webServer: {
    command: "node storyboards-server.mjs",
    url: "http://127.0.0.1:4176",
    reuseExistingServer: false,
  },
});
