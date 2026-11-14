import { defineConfig } from '@playwright/test';
import path from 'node:path';

export default defineConfig({
  testDir: '../../browser', testMatch: 'shell.spec.ts', workers: 1, retries: 0,
  reporter: 'list',
  outputDir: '/home/quintin/.cache/proscenium-development/m03-resume-proof-e3cddf8/ui-checks',
  use: {
    baseURL: 'http://127.0.0.1:4297', viewport: {width: 1920, height: 1080},
    deviceScaleFactor: 1, screenshot: 'only-on-failure', trace: 'off',
    launchOptions: { executablePath: path.resolve('tests/native/copilot/m03_resume_chromium.py') },
  },
  webServer: {
    command: 'uv run --offline python tests/native/copilot/m03_resume_control.py ui-server',
    cwd: path.resolve('.'), url: 'http://127.0.0.1:4297', reuseExistingServer: false,
  },
});
