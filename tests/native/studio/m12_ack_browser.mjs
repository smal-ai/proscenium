import { chromium } from '@playwright/test';
import { readFile,writeFile,rename,open,readdir,readlink } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import crypto from 'node:crypto';

const root=process.argv[2];process.env.TMPDIR=path.join(root,'t');
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
const read=async name=>JSON.parse(await readFile(path.join(root,name),'utf8'));
const plan=await read('plan-private.json');
await writeFile(path.join(root,'driver-intent.json'),'{}',{flag:'wx',mode:0o600});
let server,context,page,cdp,generation=0;
const processes=new Map();
const report={status:'running',phase:'start',request_id:plan.request_id,processes:[],api:[],limits:[]};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function state(pid){try{const f=(await readFile(`/proc/${pid}/stat`,'utf8')).split(') ').at(-1).trim().split(/\s+/);return{pid:Number(pid),state:f[0],parent:Number(f[1]),start:f[19]};}catch{return null;}}
async function remember(pid,kind){const s=await state(pid);if(s&&s.state!=='Z'&&!processes.has(`${pid}:${s.start}`)){processes.set(`${pid}:${s.start}`,{...s,kind});}return s;}
async function capture(){
  if(cdp){const info=await cdp.send('SystemInfo.getProcessInfo');for(const p of info.processInfo)await remember(p.id,`chromium:${p.type}`);}
  const all=[];for(const entry of await readdir('/proc'))if(/^\d+$/.test(entry)){const s=await state(entry);if(s)all.push(s);}
  const parents=new Set([process.pid]);for(const p of processes.values()){const s=await state(p.pid);if(s&&s.start===p.start&&s.state!=='Z')parents.add(p.pid);}
  for(let changed=true;changed;){changed=false;for(const s of all)if(parents.has(s.parent)&&!parents.has(s.pid)){parents.add(s.pid);await remember(s.pid,'owned-descendant');changed=true;}}
  const ancestors=new Set([process.pid]);let ancestor=await state(process.pid);while(ancestor?.parent&&!ancestors.has(ancestor.parent)){ancestors.add(ancestor.parent);ancestor=await state(ancestor.parent);}
  for(const s of all){if(ancestors.has(s.pid))continue;try{const argv=(await readFile(`/proc/${s.pid}/cmdline`)).toString().split('\0');if(argv.some(arg=>{const value=arg.split('=').at(-1);return value===root||value.startsWith(root+'/');}))await remember(s.pid,'fixture-root-associated');}catch{}}
}
async function until(call,seconds=35){const end=Date.now()+seconds*1000;while(Date.now()<end){const v=await call();if(v)return v;await sleep(50);}throw new Error('Bounded deadline');}
async function start(){generation++;const log=await open(path.join(root,`host-${generation}-private.log`),'wx',0o600);try{server=spawn(plan.python,[path.join(repo,'tests/native/studio/m12_ack_host.py'),root,String(generation)],{cwd:repo,stdio:['ignore',log.fd,log.fd],env:{...process.env,TMPDIR:path.join(root,'t')},detached:true});server.start=(await remember(server.pid,'product-host')).start;await until(async()=>{if(server.exitCode!==null)throw new Error('Host exited before readiness');try{return(await fetch(plan.origin+'/health')).ok;}catch{return false;}});}finally{await log.close();}}
async function stop(){if(!server)return;const child=server;server=null;if((await state(child.pid))?.start===child.start)child.kill('SIGTERM');try{await until(async()=>{const s=await state(child.pid);return!s||s.state==='Z'||s.start!==child.start;},45);}catch{if((await state(child.pid))?.start===child.start)child.kill('SIGKILL');}await until(async()=>{const s=await state(child.pid);return!s||s.state==='Z'||s.start!==child.start;},5);}
async function mailbox(operation){const id=crypto.randomUUID(),file=path.join(root,'commands',id+'.tmp');await writeFile(file,JSON.stringify({operation}),{flag:'wx',mode:0o600});await rename(file,file.replace(/\.tmp$/,'.json'));const receipt=await until(async()=>{try{return await read('receipts/'+id+'.json');}catch{return false;}},60);if(receipt.status!=='completed')throw new Error('Private fixture operation failed');return receipt.result;}
async function api(url,body){const key=body.operation_id??body.request_id??body.context_id;const result=await page.evaluate(async({url,body,key})=>{const csrf=JSON.parse(localStorage.getItem('proscenium.owner.session')).csrf_token;const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key,'X-CSRF-Token':csrf},body:JSON.stringify(body)});return{status:response.status,body:await response.json()};},{url,body,key});report.api.push({url,status:result.status,code:result.body.error?.code??null});return result;}
try{
  await start();report.quiet_initial=await mailbox('inspect');
  if(report.quiet_initial.sdk_constructed||report.quiet_initial.send_reservations)throw new Error('Not quiet');
  context=await chromium.launchPersistentContext(path.join(root,'b'),{headless:true,viewport:{width:1920,height:1080},deviceScaleFactor:1,serviceWorkers:'block'});
  cdp=await context.browser().newBrowserCDPSession();await capture();
  report.browser_version=context.browser().version();report.viewport={width:1920,height:1080,dpr:1};page=context.pages()[0];
  await page.goto(plan.origin);const pair=await read('pairing-private.json');await page.evaluate(code=>localStorage.setItem('proscenium.owner.pairing',JSON.stringify({schema_version:'1.0',operation_id:crypto.randomUUID(),client_nonce:crypto.randomUUID(),pairing_code:code})),pair.code);await page.reload();await page.getByRole('button',{name:'Connect this browser'}).click();await page.waitForFunction(()=>localStorage.getItem('proscenium.owner.session'));
  const project=await api('/api/v1/projects',{schema_version:'1.0',operation_id:crypto.randomUUID(),path:plan.project_path,create:true,name:'Accepted ACK fault fixture'});if(project.status!==201)throw new Error('Project failed');
  report.project_id=project.body.project_id;const base=`/api/v1/projects/${report.project_id}`;
  const activated=await api(base+'/activate',{schema_version:'1.0',operation_id:crypto.randomUUID(),project_id:report.project_id,expected_revision:project.body.state_revision});if(activated.status!==200)throw new Error('Activation failed');
  const snapshot={schema_version:'1.0',context_id:plan.context_id,project_id:report.project_id,captured_at:new Date().toISOString(),item_id:null,version_id:null,view_mode:'keyframes',target:null,camera:null,range:null,selected_objects:[],attachments:[],annotations:[],approved_findings:[]};
  const captured=await api(base+'/contexts',snapshot);if(captured.status!==201)throw new Error('Capture failed');
  await capture();report.phase='one_actual_send';
  const queued=await api(base+'/requests',{schema_version:'1.0',request_id:plan.request_id,project_id:report.project_id,type:'queue',text:plan.prompt,context_id:plan.context_id});if(queued.status!==202)throw new Error('Queue failed');
  report.phase='accepted_native_message_then_real_cut';
  // Capture SDK/browser descendants while the real send runs. No extra inference.
  await capture();report.cut=await mailbox('cut');await capture();
  report.before_restart=await mailbox('inspect');await stop();
  await start();await page.reload();await capture();report.quiet_restart=await mailbox('inspect');
  if(report.quiet_restart.sdk_constructed||report.quiet_restart.gate_open||report.quiet_restart.send_reservations!==1)throw new Error('Restart executed');
  report.phase='explicit_original_admission_reconcile';
  const current=await page.evaluate(async base=>(await fetch(base+'/snapshot')).json(),base);const op=crypto.randomUUID();
  const admitted=await api(base+'/resume',{schema_version:'1.0',operation_id:op,project_id:report.project_id,work_id:null,expected_revision:current.project.state_revision,mode:'reconcile_only'});if(![200,202].includes(admitted.status))throw new Error('Resume failed');
  report.reconcile=await until(async()=>{const s=await mailbox('inspect');return s.resumes.find(r=>r.operation_id===op&&!['pending','dispatching'].includes(r.state));},60);
  report.final=await mailbox('inspect');await capture();
  report.joined_original=report.final.request_receipt?.native_message_id===report.cut.native_user_message_id&&Boolean(report.cut.native_user_message_id);
  report.status=report.cut.accepted_ack_loss&&report.joined_original&&report.final.send_reservations===1?'accepted_native_send_ack_loss_recovery_pass':'partial_accepted_ack_recovery';
}catch{report.status='accepted_ack_proof_incomplete';}
finally{
  await capture().catch(()=>{});if(context){await context.close();cdp=null;}await stop();
  report.browser_close_awaited=true;report.controller={pid:process.pid,start:(await state(process.pid))?.start};
  report.processes=[...processes.values()].filter(p=>p.pid!==process.pid);
  report.cleanup=await Promise.all(report.processes.map(async p=>{const s=await state(p.pid);return{pid:p.pid,start:p.start,kind:p.kind,original_alive:Boolean(s&&s.start===p.start&&s.state!=='Z'),kernel_entry_present:Boolean(s&&s.start===p.start)};}));
  await writeFile(path.join(root,'browser-result-private.json'),JSON.stringify(report,null,2),{flag:'wx',mode:0o600});
  console.log(JSON.stringify({status:report.status,phase:report.phase,root}));if(report.status!=='accepted_native_send_ack_loss_recovery_pass')process.exitCode=2;
}
