import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

export default defineConfig({
  testDir: ".", testMatch: "review-context.spec.ts", workers: 1,
  outputDir: "/tmp/opencode/proscenium-w05-context-results",
  use: {baseURL: "http://127.0.0.1:5187", viewport: {width: 1920, height: 1080}, headless: true},
  projects: [
    {name: "chromium", use: {browserName: "chromium"}},
    {name: "brave", use: {browserName: "chromium", launchOptions: {executablePath: "/usr/bin/brave-browser"}}},
    {name: "edge", use: {browserName: "chromium", launchOptions: {executablePath: "/usr/bin/microsoft-edge"}}},
  ],
  webServer: {
    command: "npx vite --host 127.0.0.1 --port 5187 --strictPort", cwd: resolve(__dirname, "../.."),
    url: "http://127.0.0.1:5187/tests/browser/review-context-harness.html", reuseExistingServer: false,
  },
});
