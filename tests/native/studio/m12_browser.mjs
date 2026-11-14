// Exactly two permitted root sends, tracked durably by the actual SDK method in m12_host.
// All browser/private history receipts stay on private disk. No trace/HAR/auth logs.
import { chromium } from '@playwright/test';
import { readFile, writeFile, rename, open } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import crypto from 'node:crypto';
import { commandIdentity } from './m12_client.mjs';

const root = process.argv[2];
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const plan = JSON.parse(await readFile(path.join(root, 'plan-private.json'), 'utf8'));
await writeFile(path.join(root, 'driver-started-private.json'), JSON.stringify({ started: new Date().toISOString() }), { flag: 'wx', mode: 0o600 });
const report = { status: 'running', phases: [], processes: [], limits: [], cloud_send_limit: 2 };
let server, context, page, generation = 0;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function json(name) { return JSON.parse(await readFile(path.join(root, name), 'utf8')); }
async function pidStart(pid) {
  try { const fields = (await readFile(`/proc/${pid}/stat`, 'utf8')).split(') ').at(-1).split(' '); return fields[0] === 'Z' ? null : fields[19]; }
  catch { return null; }
}
async function until(operation, seconds = 30) {
  const end = Date.now() + seconds * 1000;
  while (Date.now() < end) { const result = await operation(); if (result) return result; await sleep(100); }
  throw new Error('Bounded observation deadline');
}
async function start() {
  generation++;
  const log = await open(path.join(root, `server-${generation}-private.log`), 'wx', 0o600);
  server = spawn(plan.python, [path.join(repo, 'tests/native/studio/m12_host.py'), root, String(generation)], { cwd: repo, stdio: ['ignore', log.fd, log.fd], detached: true });
  server.start = await pidStart(server.pid);
  report.processes.push({ pid: server.pid, start: server.start, generation });
  await until(async () => { try { return (await fetch(`${plan.origin}/health`)).ok; } catch { return false; } });
  await log.close();
}
async function stop() {
  if (!server) return;
  const child = server; server = null;
  if (await pidStart(child.pid) === child.start) child.kill('SIGTERM');
  try { await until(async () => (await pidStart(child.pid)) !== child.start, 45); }
  catch { if (await pidStart(child.pid) === child.start) child.kill('SIGKILL'); }
  await until(async () => (await pidStart(child.pid)) !== child.start, 5);
}
async function mailbox(operation, extra = {}) {
  const id = crypto.randomUUID();
  const pending = path.join(root, 'commands', `${id}.tmp`);
  await writeFile(pending, JSON.stringify({ operation, ...extra }), { flag: 'wx', mode: 0o600 });
  await rename(pending, pending.replace(/\.tmp$/, '.json'));
  return until(async () => {
    try { return await json(`receipts/${id}.json`); } catch { return false; }
  }, 60);
}
async function inspect() {
  const receipt = await mailbox('inspect');
  if (receipt.status !== 'completed') throw new Error('Private inspection failed');
  return receipt.result;
}
async function api(route, body, method = 'POST') {
  const result = await page.evaluate(async ({ route, body, method, key }) => {
    const csrf = JSON.parse(localStorage.getItem('proscenium.owner.session')).csrf_token;
    const response = await fetch(route, { method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf,
      'Idempotency-Key': key }, body: JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  }, { route, body, method, key: commandIdentity(body) });
  (report.api_statuses ??= []).push({ route, status: result.status, code: result.body.error?.code ?? null });
  return result;
}
const base = `/api/v1/projects/${plan.project_id}`;
async function resume(mode) {
  const snapshot = await page.evaluate(async base => (await fetch(`${base}/snapshot`)).json(), base);
  const operation_id = crypto.randomUUID();
  const response = await api(`${base}/resume`, { schema_version: '1.0', operation_id, project_id: plan.project_id,
    expected_revision: snapshot.project.state_revision, work_id: null, mode });
  if (response.status !== 202 && response.status !== 200) throw new Error('Owner Resume admission failed');
  const current = await until(async () => {
    const state = await inspect();
    return state.resumes.find(value => value.operation_id === operation_id && !['pending','dispatching'].includes(value.state));
  }, 60);
  return current;
}
async function queue(number) {
  const captured = await api(`${base}/contexts`, plan.contexts[number]);
  if (captured.status !== 201) throw new Error('Context capture failed');
  const response = await api(`${base}/requests`, { schema_version: '1.0', project_id: plan.project_id,
    request_id: plan.request_ids[number], type: 'queue', text: plan.prompts[number], context_id: plan.contexts[number].context_id });
  if (response.status !== 202) throw new Error('Queue admission failed');
  return response.body;
}
try {
  report.phase = 'initial_quiet_start';
  await start();
  const quiet1 = await inspect();
  if (quiet1.native_constructed || quiet1.gate_open || quiet1.sends.length) throw new Error('Initial startup not quiet');
  context = await chromium.launchPersistentContext(path.join(root, 'browser-private'), { headless: true, viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1, serviceWorkers: 'block' });
  page = context.pages()[0];
  const pair = await json('pairing-private.json');
  await page.goto(plan.origin);
  await page.evaluate(code => localStorage.setItem('proscenium.owner.pairing', JSON.stringify({ schema_version:'1.0', operation_id:crypto.randomUUID(),client_nonce:crypto.randomUUID(),pairing_code:code })), pair.code);
  await page.reload();
  await page.getByRole('button', { name: 'Connect this browser' }).click();
  await page.waitForFunction(() => localStorage.getItem('proscenium.owner.session'));
  const media = await page.evaluate(async ({ base, context }) => {
    const targets = context.annotations.map(a => a.target);
    const results = [];
    for (const target of targets) {
      const response = await fetch(`${base}/artifacts/${target.artifact_id}/content`, { cache: 'no-store' });
      const bytes = await response.arrayBuffer();
      const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2,'0')).join('');
      results.push({ artifact_id: target.artifact_id, status: response.status, sha256: hash, matches: hash === target.displayed_sha256 });
    }
    return results;
  }, { base, context: plan.contexts[0] });
  if (!media.length || media.some(value => !value.matches || value.status !== 200)) throw new Error('Restored annotation media missing');
  report.media = media;
  report.phase = 'read_original_native_history';
  report.initial_reconcile = await resume('reconcile_only');
  const initialHistory = await mailbox('history');
  if (initialHistory.status !== 'completed') throw new Error('Native old-history read failed');
  await writeFile(path.join(root, 'initial-history-private.json'), JSON.stringify(initialHistory.result), { flag: 'wx', mode: 0o600 });
  report.old_public_ids_restored = plan.original_native_message_ids.every(id => initialHistory.result.users.some(user => user.message_id === id));
  if (!report.old_public_ids_restored || initialHistory.result.session_id !== plan.session_id) throw new Error('Old native message identity mismatch');
  report.phase = 'held_queue_and_restart';
  await queue(0);
  const held = await inspect();
  const heldRequest = held.requests.find(r => r.request_id === plan.request_ids[0]);
  if (heldRequest.receipt.state !== 'held' || held.sends.length) throw new Error('Held Queue executed before Resume');
  await stop();
  await start();
  await page.reload();
  const quiet2 = await inspect();
  const rehydrated = quiet2.requests.find(r => r.request_id === plan.request_ids[0]);
  if (quiet2.native_constructed || quiet2.gate_open || quiet2.sends.length || JSON.stringify(rehydrated.submission) !== JSON.stringify(heldRequest.submission)) throw new Error('Held request restart changed identity or executed');
  report.held_restart = { same_submission: true, request_id: plan.request_ids[0], context_id: rehydrated.submission.context_id, startup_sdk_absent: true, sends: 0 };
  report.phase = 'explicit_release_and_one_readonly_continuation';
  report.release = await resume('release_held_queue');
  const completed = await until(async () => {
    const state = await inspect();
    const request = state.requests.find(r => r.request_id === plan.request_ids[0]);
    if (['failed','outcome_uncertain','awaiting_user'].includes(request.receipt.state)) return request;
    return request.receipt.state === 'completed' ? request : false;
  }, 175);
  report.continuation_receipt = completed.receipt;
  const after = await mailbox('history');
  await writeFile(path.join(root, 'continued-history-private.json'), JSON.stringify(after), { flag: 'wx', mode: 0o600 });
  const oldOutputs = new Set(initialHistory.result.public_assistant_outputs_private.map(value => value.event_id));
  const newOutputs = after.result.public_assistant_outputs_private.filter(value => !oldOutputs.has(value.event_id));
  const text = newOutputs.at(-1)?.content ?? '';
  const answer = JSON.parse(text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, ''));
  report.continuity = { same_session: after.result.session_id === plan.session_id, marker_observed: answer.marker_token === plan.marker_token,
    relocated_cwd_observed: answer.cwd === path.join(root, 'restored/project'),
    blue_history_observed: /blue/i.test(answer.previous_visual_correction?.object ?? '') && /orange/i.test(answer.previous_visual_correction?.preserved_object ?? '') && answer.previous_visual_correction.before_height === 2 && answer.previous_visual_correction.after_height === 4,
    pinned_version_observed: answer.annotation_pinned_version === 'loop-v1-clean', request_completed: completed.receipt.state === 'completed' };
  if (!Object.values(report.continuity).every(Boolean)) throw new Error('Read-only native continuity not established');
  report.phase = 'actual_stdout_transport_cut';
  report.loss_arm = await mailbox('arm_loss');
  if (report.loss_arm.status !== 'completed') throw new Error('Actual stdout read gate unavailable');
  await queue(1);
  report.transport_cut = await mailbox('cut_loss', { request_id: plan.request_ids[1] });
  await stop();
  await start();
  await page.reload();
  const quiet3 = await inspect();
  if (quiet3.native_constructed || quiet3.gate_open || quiet3.sends.length !== 2) throw new Error('Transport restart not quiet');
  report.phase = 'quiet_transport_restart_and_original_request_join';
  report.loss_reconcile = await resume('reconcile_only');
  const joined = await inspect();
  const request = joined.requests.find(r => r.request_id === plan.request_ids[1]);
  report.loss_request = request.receipt;
  const finalHistory = await mailbox('history');
  await writeFile(path.join(root, 'final-history-private.json'), JSON.stringify(finalHistory), { flag: 'wx', mode: 0o600 });
  report.loss_unique_native_users = finalHistory.status === 'completed' ? finalHistory.result.users.filter(user => user.client_turn_id === plan.request_ids[1]).length : null;
  const cut = report.transport_cut.result;
  report.actual_transport_loss_before_ack = report.transport_cut.status === 'completed' && cut.send_pending_before_cut && cut.actual_stdout_fd_closed && !cut.reader_pause_expired && cut.pending_rpc_count_before_release > 0 && cut.native_users_observed_before_cut.length === 1 && cut.sdk_transport_closed;
  report.loss_joined_without_resend = Boolean(request.receipt.native_message_id) && report.loss_unique_native_users === 1 && joined.sends.length === 2;
  report.status = report.loss_joined_without_resend && report.actual_transport_loss_before_ack ? 'bounded_native_recovery_pass' : 'partial_transport_reconciliation';
} catch {
  // Never emit Playwright errors or native content into public logs.
  report.status = 'bounded_recovery_incomplete';
  report.limits.push('A bounded driver assertion or observation failed. Inspect private phase receipts without exporting native reasoning/auth.');
} finally {
  if (context) await context.close();
  await stop();
  report.browser_closed = true;
  report.server_identities_gone = (await Promise.all(report.processes.map(async value => (await pidStart(value.pid)) !== value.start))).every(Boolean);
  await writeFile(path.join(root, 'driver-result-private.json'), JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
  console.log(JSON.stringify({ status: report.status, root, server_identities_gone: report.server_identities_gone }));
  if (report.status !== 'bounded_native_recovery_pass') process.exitCode = 2;
}
