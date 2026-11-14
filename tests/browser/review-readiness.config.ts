import { defineConfig } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";

const root = resolve(homedir(), ".cache/proscenium-development/evidence/review-readiness");
mkdirSync(resolve(root, "profiles"), {recursive:true}); process.env.TMPDIR=resolve(root,"profiles");
export default defineConfig({
  testDir:".",testMatch:"review-readiness.spec.ts",workers:1,outputDir:resolve(root,"results"),
  use:{baseURL:"http://127.0.0.1:5191",viewport:{width:1920,height:1080},headless:true,launchOptions:{args:["--use-angle=swiftshader","--enable-unsafe-swiftshader"]}},
  webServer:{command:"npx vite --host 127.0.0.1 --port 5191 --strictPort",cwd:resolve(__dirname,"../.."),url:"http://127.0.0.1:5191/tests/browser/review-readiness-harness.html",reuseExistingServer:false},
});
