import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";
const output = process.env.M07_REVIEW_RESULTS;
if (!output) throw new Error("Set M07_REVIEW_RESULTS to a new evidence directory");
export default defineConfig({
  testDir:".",testMatch:"review-job.spec.ts",workers:1,outputDir:output,
  reporter:[["list"],["json",{outputFile:resolve(output,"report.json")}]],
  use:{baseURL:"http://127.0.0.1:5196",viewport:{width:1920,height:1080},headless:true},
  projects:[{name:"chromium",use:{browserName:"chromium"}},{name:"brave",use:{browserName:"chromium",launchOptions:{executablePath:"/usr/bin/brave-browser"}}},{name:"edge",use:{browserName:"chromium",launchOptions:{executablePath:"/usr/bin/microsoft-edge"}}}],
  webServer:{command:"npx vite --host 127.0.0.1 --port 5196 --strictPort",cwd:resolve(__dirname,"../.."),url:"http://127.0.0.1:5196/tests/browser/review-job-harness.html",reuseExistingServer:false},
});
