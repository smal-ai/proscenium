// Real compiled UI and real API/WebSocket/media requests through ONE SSH-forwarded origin.
// No mocks, HAR, tracing, headers/cookies/pairing data or private keys in output.
import { chromium } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';

const root = process.argv[2];
const spec = JSON.parse(await readFile(path.join(root, 'allocation.json'), 'utf8'));
const privatePair = JSON.parse(await readFile(path.join(root, 'pairing-private.json'), 'utf8'));
const requests = [];
const offOrigin = [];
let context;
try {
  context = await chromium.launchPersistentContext(path.join(root, 'chromium-private'), {
    headless: true, viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1,
    serviceWorkers: 'block', args: ['--disable-background-networking'],
  });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== spec.origin) {
      offOrigin.push(url.origin);
      return route.abort();
    }
    return route.continue();
  });
  context.on('response', response => requests.push({
    method: response.request().method(), path: new URL(response.url()).pathname, status: response.status(),
  }));
  const page = context.pages()[0];
  await page.goto(spec.origin, { waitUntil: 'networkidle', timeout: 15000 });
  // Populate only the browser-private pairing handover, not a URL that an error could log.
  await page.evaluate(code => localStorage.setItem('proscenium.owner.pairing', JSON.stringify({
    schema_version: '1.0', operation_id: crypto.randomUUID(), client_nonce: crypto.randomUUID(), pairing_code: code,
  })), privatePair.code);
  await page.reload({ waitUntil: 'networkidle' });
  const paired = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/owner/session' && r.status() === 201);
  await page.getByRole('button', { name: 'Connect this browser' }).click();
  await paired;
  await page.waitForFunction(() => localStorage.getItem('proscenium.owner.session') !== null);
  const result = await page.evaluate(async ({ projectPath, pngBase64 }) => {
    const check = (condition, name) => { if (!condition) throw new Error(name); };
    const csrf = JSON.parse(localStorage.getItem('proscenium.owner.session')).csrf_token;
    async function command(url, method, body, expected) {
      const response = await fetch(url, { method, headers: {
        'Content-Type': 'application/json', 'X-CSRF-Token': csrf, 'Idempotency-Key': body.operation_id,
      }, body: JSON.stringify(body) });
      check(response.status === expected, 'product command status');
      return response.json();
    }
    const project = await command('/api/v1/projects', 'POST', {
      schema_version: '1.0', operation_id: crypto.randomUUID(), path: projectPath, create: true, name: 'SSH disposable proof',
    }, 201);
    const base = `/api/v1/projects/${project.project_id}`;
    await command(`${base}/activate`, 'POST', {
      schema_version: '1.0', operation_id: crypto.randomUUID(), project_id: project.project_id, expected_revision: project.state_revision,
    }, 200);
    await command('/api/v1/projects', 'POST', {
      schema_version: '1.0', operation_id: crypto.randomUUID(), path: projectPath, create: false,
    }, 201);
    const snapshot = await (await fetch(`${base}/snapshot`)).json();
    check(snapshot.project.project_id === project.project_id, 'active project snapshot');
    const image = Uint8Array.from(atob(pngBase64), c => c.charCodeAt(0));
    const uploadId = crypto.randomUUID();
    const upload = await fetch(`${base}/uploads/${uploadId}`, {
      method: 'PUT', headers: { 'Content-Type': 'image/png', 'X-CSRF-Token': csrf, 'Idempotency-Key': uploadId }, body: image,
    });
    check(upload.status === 201, 'PNG upload');
    const artifact = await upload.json();
    const content = `${base}/artifacts/${artifact.artifact_id}/content`;
    const full = await fetch(content, { cache: 'no-store' });
    const bytes = new Uint8Array(await full.arrayBuffer());
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2, '0')).join('');
    check(full.status === 200 && hash === artifact.sha256 && bytes.length === image.length, 'full media integrity');
    const range = await fetch(content, { cache: 'no-store', headers: { Range: 'bytes=0-15' } });
    const part = new Uint8Array(await range.arrayBuffer());
    check(range.status === 206 && range.headers.get('Content-Range') === `bytes 0-15/${image.length}`, 'range status and headers');
    check(part.length === 16 && part.every((x, i) => x === image[i]), 'range exact PNG bytes');
    const events = await (await fetch(`${base}/events?after=0&limit=100`)).json();
    const expected = events.events.map(event => event.sequence);
    check(expected.length > 0, 'real durable project events');
    async function replay() {
      return new Promise((resolve, reject) => {
        const ws = new WebSocket(`${location.origin.replace('http:', 'ws:')}/api/v1/events`);
        const timer = setTimeout(() => { ws.close(); reject(new Error('WebSocket deadline')); }, 10000);
        const sequences = [];
        ws.onopen = () => ws.send(JSON.stringify({ schema_version: '1.0', type: 'subscribe', project_id: project.project_id, after_sequence: 0 }));
        ws.onerror = () => { clearTimeout(timer); reject(new Error('WebSocket transport')); };
        ws.onmessage = event => {
          const value = JSON.parse(event.data);
          if (value.sequence) sequences.push(value.sequence);
          if (value.type === 'caught_up') { clearTimeout(timer); ws.close(); resolve(sequences); }
        };
      });
    }
    const first = await replay();
    const reconnect = await replay();
    check(JSON.stringify(first) === JSON.stringify(expected) && JSON.stringify(reconnect) === JSON.stringify(expected), 'durable replay and reconnect');
    check(new Set(first).size === first.length, 'no replay duplicate');
    return { project_open_activate: true, png_upload: true, media_sha256: hash, media_bytes: image.length,
      range_status: range.status, range_exact_bytes: true, websocket_replay_sequences: first, websocket_reconnect_sequences: reconnect,
      owner_pairing: true };
  }, { projectPath: path.join(root, 'project'), pngBase64: spec.png_base64 });
  if (offOrigin.length) throw new Error('off-origin product traffic');
  const version = context.browser().version();
  const index = await readFile(spec.frontend + '/index.html');
  if (!requests.some(r => r.path.startsWith('/assets/') && r.status === 200)) throw new Error('compiled frontend assets not loaded');
  await context.close(); context = null;
  await writeFile(path.join(root, 'browser-result.json'), JSON.stringify({
    ...result, requests, off_origin_requests: 0, viewport: { width: 1920, height: 1080, dpr: 1 },
    browser: version, frontend_index_sha256: createHash('sha256').update(index).digest('hex'), browser_context_closed: true,
    interaction_scope: 'Actual compiled UI pairing, then scripted browser calls to real product routes. Not cumulative UI authoring acceptance.',
  }, null, 2), { flag: 'wx', mode: 0o600 });
} catch {
  // Never emit Playwright errors: navigation/evaluate diagnostics can include private arguments.
  if (context) await context.close();
  console.error('SSH browser proof failed. No private arguments emitted.');
  process.exitCode = 1;
}
