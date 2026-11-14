import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

export default defineConfig({
  testDir: ".",
  testMatch: "shell.spec.ts",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  outputDir: "/tmp/opencode/proscenium-w04-browser-results",
  use: { baseURL: "http://127.0.0.1:4174", viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1, screenshot: "only-on-failure", trace: "off" },
  webServer: { command: "npm run build --workspace apps/frontend && npm exec --workspace apps/frontend -- vite preview --host 127.0.0.1 --port 4174 --strictPort", cwd: resolve(__dirname, "../.."), url: "http://127.0.0.1:4174", reuseExistingServer: false },
});
