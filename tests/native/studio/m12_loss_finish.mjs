// Finish only the existing, proven-undispatched second request. No POST /requests.
import { chromium } from '@playwright/test';
import { readFile, writeFile, rename, open } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import crypto from 'node:crypto';

const root = process.argv[2];
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const read = async name => JSON.parse(await readFile(path.join(root, name), 'utf8'));
const plan = await read('plan-private.json');
await writeFile(path.join(root, 'loss-finish-started-private.json'), '{}', { flag: 'wx', mode: 0o600 });
const report = { status: 'running', processes: [], phase: 'quiet_start', request_id: plan.request_ids[1], session_id: plan.session_id };
let server, context, page, generation = 3;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function identity(pid) { try { const f = (await readFile(`/proc/${pid}/stat`, 'utf8')).split(') ').at(-1).split(' '); return f[0] === 'Z' ? null : f[19]; } catch { return null; } }
async function until(call, seconds=45) { const end=Date.now()+seconds*1000; while(Date.now()<end) { const value=await call(); if(value)return value; await sleep(100); } throw new Error('Bounded deadline'); }
async function start() {
  generation++;
  const log = await open(path.join(root, `server-${generation}-private.log`), 'wx', 0o600);
  server=spawn(plan.python,[path.join(repo,'tests/native/studio/m12_host.py'),root,String(generation)],{cwd:repo,stdio:['ignore',log.fd,log.fd],detached:true});
  server.start=await identity(server.pid); report.processes.push({pid:server.pid,start:server.start,generation});
  await until(async()=>{try{return(await fetch(`${plan.origin}/health`)).ok;}catch{return false;}});
  await log.close();
}
async function stop() {
  if(!server)return; const child=server;server=null;
  if(await identity(child.pid)===child.start) child.kill('SIGTERM');
  try { await until(async()=>await identity(child.pid)!==child.start,45); }
  catch { if(await identity(child.pid)===child.start)child.kill('SIGKILL'); }
  await until(async()=>await identity(child.pid)!==child.start,5);
}
async function mailbox(operation, extra={}) {
  const id=crypto.randomUUID(); const pending=path.join(root,'commands',`${id}.tmp`);
  await writeFile(pending,JSON.stringify({operation,...extra}),{flag:'wx',mode:0o600});await rename(pending,pending.replace(/\.tmp$/,'.json'));
  const receipt=await until(async()=>{try{return await read(`receipts/${id}.json`);}catch{return false;}},60);
  if(receipt.status!=='completed')throw new Error('Operator fixture command failed');return receipt.result;
}
const base=`/api/v1/projects/${plan.project_id}`;
async function resume(mode) {
  const result=await page.evaluate(async({base,project,mode})=>{
    const snapshot=await(await fetch(`${base}/snapshot`)).json();const operation_id=crypto.randomUUID();
    const csrf=JSON.parse(localStorage.getItem('proscenium.owner.session')).csrf_token;
    const response=await fetch(`${base}/resume`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,'Idempotency-Key':operation_id},body:JSON.stringify({schema_version:'1.0',operation_id,project_id:project,expected_revision:snapshot.project.state_revision,work_id:null,mode})});
    return{status:response.status,operation_id};
  },{base,project:plan.project_id,mode});
  if(result.status!==202&&result.status!==200)throw new Error('Owner Resume failed');
  return until(async()=>{const state=await mailbox('inspect');return state.resumes.find(r=>r.operation_id===result.operation_id&&!['pending','dispatching'].includes(r.state));},60);
}
try {
  await start();
  const quiet=await mailbox('inspect');const original=quiet.requests.find(r=>r.request_id===report.request_id);
  if(quiet.native_constructed||quiet.gate_open||quiet.sends.length!==1||original.receipt.state!=='held'||quiet.delivery_states[report.request_id]!==null||quiet.outbox_states[report.request_id]!=='pending')throw new Error('Existing request is not proven undispatched');
  report.prior_request_proven_undispatched={state:'held',outbox:'pending',sdk_delivery_record:false,sends:1};
  const pair=await mailbox('pairing');
  context=await chromium.launchPersistentContext(path.join(root,'browser-loss-private'),{headless:true,viewport:{width:1920,height:1080},deviceScaleFactor:1,serviceWorkers:'block'});
  page=context.pages()[0];await page.goto(plan.origin);
  await page.evaluate(code=>localStorage.setItem('proscenium.owner.pairing',JSON.stringify({schema_version:'1.0',operation_id:crypto.randomUUID(),client_nonce:crypto.randomUUID(),pairing_code:code})),pair.code);
  await page.reload();await page.getByRole('button',{name:'Connect this browser'}).click();await page.waitForFunction(()=>localStorage.getItem('proscenium.owner.session'));
  report.phase='arm_actual_send_boundary';
  report.arm=await mailbox('arm_loss');
  report.release=await resume('release_held_queue');
  await until(async()=>{try{return await read('reader-paused-at-send-private.json');}catch{return false;}},20);
  report.phase='cut_actual_pending_sdk_send';
  report.cut=await mailbox('cut_loss',{request_id:report.request_id});
  await stop();await start();await page.reload();
  const restarted=await mailbox('inspect');
  if(restarted.native_constructed||restarted.gate_open||restarted.sends.length!==2)throw new Error('Startup executed or lost send identity');
  report.quiet_restart={sdk_absent:true,gate_closed:true,sends:2};
  report.phase='reconcile_original_not_resubmit';
  report.reconcile=await resume('reconcile_only');
  const state=await mailbox('inspect');const history=await mailbox('history');
  await writeFile(path.join(root,'loss-final-history-private.json'),JSON.stringify(history),{flag:'wx',mode:0o600});
  const matches=history.users.filter(user=>user.client_turn_id===report.request_id);
  const request=state.requests.find(r=>r.request_id===report.request_id);
  report.original_request_receipt=request.receipt;
  report.native_matches=matches;
  report.cloud_sends=state.sends.length;
  report.gate_still_closed=!state.gate_open;
  report.actual_transport_loss_before_ack=report.cut.send_pending_before_cut&&report.cut.actual_stdout_fd_closed&&!report.cut.reader_pause_expired&&report.cut.pending_rpc_count_before_release>0&&report.cut.native_users_observed_before_cut.length===1&&report.cut.sdk_transport_closed;
  report.joined_original_without_duplicate=matches.length===1&&matches[0].message_id===request.receipt.native_message_id&&matches[0].message_id===report.cut.native_users_observed_before_cut[0]?.message_id&&state.sends.length===2;
  report.public_response_available=history.public_assistant_outputs_private.some(value=>value.content.includes('M12_TRANSPORT_ONCE'));
  report.status=report.actual_transport_loss_before_ack&&report.joined_original_without_duplicate?'genuine_sdk_transport_admission_recovery_pass':'partial_sdk_transport_recovery';
} catch { report.status='bounded_transport_finish_incomplete'; }
finally {
  if(context)await context.close();await stop();
  report.server_identities_gone=(await Promise.all(report.processes.map(async value=>await identity(value.pid)!==value.start))).every(Boolean);
  await writeFile(path.join(root,'loss-finish-result-private.json'),JSON.stringify(report,null,2),{flag:'wx',mode:0o600});
  console.log(JSON.stringify({status:report.status,root,server_identities_gone:report.server_identities_gone}));
  if(report.status!=='genuine_sdk_transport_admission_recovery_pass')process.exitCode=2;
}
