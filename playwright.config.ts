import { defineConfig } from "@playwright/test";

// Software WebGL keeps isolated browser verification independent of GPU/native allocations.
const softwareWebGL = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"];
const port = Number(process.env.PROSCENIUM_BROWSER_PORT ?? "4179");
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    ["json", { outputFile: process.env.PROSCENIUM_BROWSER_REPORT ?? "/tmp/opencode/proscenium-browser-report.json" }],
  ],
  outputDir: "/tmp/opencode/proscenium-browser-results",
  use: {
    baseURL,
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    screenshot: "only-on-failure",
    trace: "off",
  },
  projects: [
    {
      name: "chromium-dpr1",
      testIgnore: "*dpr2*.spec.ts",
      use: { browserName: "chromium", deviceScaleFactor: 1, launchOptions: { args: softwareWebGL } },
    },
    {
      name: "chromium-dpr2",
      testMatch: ["*dpr2*.spec.ts", "uat-rc3-polish.spec.ts", "uat-rc4-current-view.spec.ts"],
      use: { browserName: "chromium", deviceScaleFactor: 2, launchOptions: { args: softwareWebGL } },
    },
  ],
  webServer: {
    command: `npm run build --workspace apps/frontend && npm exec --workspace apps/frontend -- vite preview --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 120000,
  },
});
