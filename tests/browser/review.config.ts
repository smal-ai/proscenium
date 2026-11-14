import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

export default defineConfig({
  testDir: ".", testMatch: "review.spec.ts", workers: 1,
  outputDir: "/tmp/opencode/proscenium-w05-browser-results",
  use: {baseURL: "http://127.0.0.1:5185", viewport: {width: 1920, height: 1080}, headless: true},
  projects: [
    {name:"chromium",use:{browserName:"chromium"}},
    {name:"brave",use:{browserName:"chromium",launchOptions:{executablePath:"/usr/bin/brave-browser"}}},
    {name:"edge",use:{browserName:"chromium",launchOptions:{executablePath:"/usr/bin/microsoft-edge"}}},
    {name:"chromium-dpr2",use:{browserName:"chromium",deviceScaleFactor:2}},
  ],
  webServer: {command: "npx vite --host 127.0.0.1 --port 5185 --strictPort", cwd: resolve(__dirname, "../.."), url: "http://127.0.0.1:5185/tests/browser/review-harness.html", reuseExistingServer: false},
});
