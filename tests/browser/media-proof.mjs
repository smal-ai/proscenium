/** Isolated E03 codec/paused-frame oracle. Not a player or product UI test. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import os from 'node:os';
import { chromium } from '@playwright/test';

const root = path.resolve(process.argv[2] ?? '');
assert.equal(path.dirname(root), '/tmp/opencode');
assert.match(path.basename(root), /^proscenium-e03-/);
const source = JSON.parse(await readFile(path.join(root, 'result.json'), 'utf8'));
// Only known generated videos are served. No arbitrary path or source-file route.
const media = new Map();
for (const segment of source.segments) {
  const bytes = await readFile(path.join(root, segment.path));
  assert.equal(createHash('sha256').update(bytes).digest('hex'), segment.sha256);
  media.set(`/${segment.path}`, bytes);
}
const page = '<!doctype html><meta charset="utf-8"><title>E03 codec fixture only</title>'
  + '<style>body{margin:0}video{position:absolute;left:32px;top:32px;width:960px;'
  + 'height:640px;object-fit:contain;background:black}</style><video muted playsinline></video>';
const server = http.createServer((request, response) => {
  if (request.url === '/') {
    response.writeHead(200, { 'Content-Type': 'text/html' });
    response.end(page);
    return;
  }
  const bytes = media.get(request.url);
  if (!bytes) { response.writeHead(404).end(); return; }
  const headers = { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes',
    'Cache-Control': 'no-store' };
  const range = /^bytes=(\d+)-(\d*)$/.exec(request.headers.range ?? '');
  if (request.headers.range && !range) { response.writeHead(416).end(); return; }
  const start = range ? Number(range[1]) : 0;
  const end = range?.[2] ? Math.min(Number(range[2]), bytes.length - 1) : bytes.length - 1;
  if (start > end || start >= bytes.length) { response.writeHead(416).end(); return; }
  if (range) headers['Content-Range'] = `bytes ${start}-${end}/${bytes.length}`;
  headers['Content-Length'] = end - start + 1;
  response.writeHead(range ? 206 : 200, headers);
  response.end(bytes.subarray(start, end + 1));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const result = {
  scope: 'Actual H264 decoding and paused presented-frame correspondence, NOT uninterrupted playback',
  recorded_at: new Date().toISOString(), node: process.version,
  playwright: '1.65.1', platform: { platform: os.platform(), release: os.release(), arch: os.arch() },
  command: ['node', 'tests/browser/media-proof.mjs', root], origin, browsers: [],
  limitations: ['No product UI, progressive availability, boundary hand-off continuity or annotation test',
    'Letterboxing/DPR tests inspect intrinsic video pixels, not product pointer-coordinate transforms',
    'Headless software presentation only. Desktop graphics performance is unmeasured'],
};

async function captureSegment(page, segment, numbered) {
  return page.evaluate(async ({ segment, numbered }) => {
    const video = document.querySelector('video');
    function event(name) {
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`${name} timeout`)), 10000);
        video.addEventListener(name, () => { clearTimeout(timer); resolve(); }, { once: true });
        video.addEventListener('error', () => {
          clearTimeout(timer); reject(new Error(`MediaError ${video.error?.code}: ${video.error?.message}`));
        }, { once: true });
      });
    }
    video.pause();
    const loaded = event('loadeddata');
    video.src = `/${segment.path}`;
    video.load();
    await loaded;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const rows = [];
    for (let index = 0; index < 4; index++) {
      const centre = (index + 0.5) * segment.fps[1] / segment.fps[0];
      const seeked = event('seeked');
      const presented = new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('rVFC timeout')), 10000);
        video.requestVideoFrameCallback((now, metadata) => {
          clearTimeout(timer);
          ctx.drawImage(video, 0, 0);
          const cells = Array.from({ length: 4 }, (_, bit) =>
            Array.from(ctx.getImageData(56 + 64 * bit, 56, 1, 1).data).slice(0, 3));
          const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
          let nonBlack = 0;
          for (let pixel = 0; pixel < pixels.length; pixel += 4) {
            if (pixels[pixel] + pixels[pixel + 1] + pixels[pixel + 2] > 48) nonBlack++;
          }
          resolve({ index, requested_centre: centre, media_time: metadata.mediaTime,
            presented_frames_counter: metadata.presentedFrames, callback_now: now,
            cells, non_black_pixels: nonBlack,
            ordinal: numbered ? cells.reduce((n, rgb, bit) => n | (Math.min(...rgb) > 223 ? 1 << bit : 0), 0) : null });
        });
      });
      video.currentTime = centre;
      const [, row] = await Promise.all([seeked, presented]);
      rows.push(row);
    }
    const rect = video.getBoundingClientRect();
    return { path: segment.path, dimensions: [video.videoWidth, video.videoHeight],
      duration: video.duration, dpr: devicePixelRatio,
      element_rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      intrinsic_content_css: { x: rect.x, y: rect.y + 50, width: 960, height: 540 },
      rows };
  }, { segment, numbered });
}

try {
  for (const [name, executablePath] of [
    ['chromium', path.join(os.homedir(), '.cache/ms-playwright/chromium-1255/chrome-linux64/chrome')],
    ['brave', '/usr/bin/brave-browser'], ['edge', '/usr/bin/microsoft-edge'],
  ]) {
    let browser;
    const record = { name, executablePath, cases: [] };
    result.browsers.push(record);
    try {
      browser = await chromium.launch({ executablePath, headless: true,
        args: ['--disable-background-networking', '--no-first-run', '--disable-component-update'] });
      record.version = browser.version();
      for (const dpr of [1, 2]) {
        const context = await browser.newContext({ viewport: { width: 1920, height: 1080 },
          deviceScaleFactor: dpr });
        await context.route('**/*', route => route.request().url().startsWith(`${origin}/`)
          ? route.continue() : route.abort());
        const tab = await context.newPage();
        await tab.goto(origin);
        record.user_agent = await tab.evaluate(() => navigator.userAgent);
        record.can_play_type = await tab.evaluate(() => document.querySelector('video')
          .canPlayType('video/mp4; codecs="avc1.64001e"'));
        const segments = source.segments.filter(segment =>
          segment.family !== 'native' || (dpr === 1 && segment.source_interval[0] === 1));
        for (const segment of segments) {
          if (dpr === 2 && segment.family !== 'f04') continue;
          const numbered = segment.family !== 'native';
          const capture = await captureSegment(tab, segment, numbered);
          assert.deepEqual(capture.dimensions, [640, 360]);
          for (const row of capture.rows) {
            assert.ok(row.non_black_pixels > 10000, 'Blank frame');
            assert.ok(Math.abs(row.media_time - row.index * segment.fps[1] / segment.fps[0]) < 0.000002,
              `Presented timestamp mismatch: ${JSON.stringify(row)}`);
            if (numbered) {
              assert.equal(row.ordinal, segment.source_interval[0] + row.index);
              for (const rgb of row.cells) {
                assert.ok(Math.max(...rgb) - Math.min(...rgb) < 20);
                assert.ok(Math.max(...rgb) < 32 || Math.min(...rgb) > 223);
              }
            }
          }
          record.cases.push(capture);
        }
        await context.close();
      }
      record.status = 'codec_and_paused_frame_correspondence_pass';
    } catch (error) {
      record.status = 'failed';
      record.error = String(error.stack ?? error);
      process.exitCode = 1;
    } finally { await browser?.close(); }
  }
} finally {
  server.close();
  await writeFile(path.join(root, 'browser-result.json'), JSON.stringify(result, null, 2) + '\n');
}
console.log(JSON.stringify(result.browsers.map(({ name, status, version, error }) =>
  ({ name, status, version, error })), null, 2));
