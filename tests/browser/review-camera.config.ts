import { defineConfig } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";

const output = resolve(homedir(), ".cache/proscenium-development/evidence/review-camera");
mkdirSync(resolve(output, "profiles"), {recursive: true});
process.env.TMPDIR = resolve(output, "profiles");

export default defineConfig({
  testDir: ".", testMatch: "review-camera.spec.ts", workers: 1, outputDir: resolve(output, "results"),
  use: {baseURL: "http://127.0.0.1:5189", viewport: {width: 1920, height: 1080}, headless: true,
    launchOptions: {args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]}},
  projects: [{name: "chromium", use: {browserName: "chromium"}}, {name: "chromium-dpr2", use: {browserName: "chromium", deviceScaleFactor: 2}}],
  webServer: {command: "npx vite --host 127.0.0.1 --port 5189 --strictPort", cwd: resolve(__dirname, "../.."), url: "http://127.0.0.1:5189/tests/browser/review-camera-harness.html", reuseExistingServer: false},
});
