// One new explicit owner follow-up Queue. Same retained project/session, no old-work Continue.
import { chromium, expect } from '@playwright/test';
import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs/promises';
import { createHash } from 'node:crypto';

const root=process.argv[2];
const host=JSON.parse(await fs.readFile(`${root}/host.json`,'utf8'));
const before=JSON.parse(await fs.readFile(`${root}/before.json`,'utf8'));
const original=host.original_fixture;
const project='project-f7d845f4-1910-4099-8578-ce15509690da';
const oldRequest='927cf238-56c2-454e-a063-014e072f9010';
const oldWork='work-bc19a5da5fcd4aaf9ae552a13d06b20d';
const sid='5971843f-f216-40f8-b8cb-995675f48161';
const prefix=`/api/v1/projects/${project}`;
const db=new DatabaseSync(`${original}/project/.proscenium/state.sqlite3`,{readOnly:true});
const rows=ns=>db.prepare('SELECT id,body FROM records WHERE namespace=?').all(ns).map(r=>({storage_id:r.id,...JSON.parse(r.body)}));
const row=(ns,id)=>{const r=db.prepare('SELECT body FROM records WHERE namespace=? AND id=?').get(ns,id);return r?JSON.parse(r.body):null;};
const digest=data=>createHash('sha256').update(data).digest('hex');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const result={phase:'starting',scripted_owner_follow_up:true,new_queue_requests:0,old_work_continue_commands:0,decisions_reported:[],resumes:[]};
const save=()=>fs.writeFile(`${root}/result.private.json`,JSON.stringify(result,null,2));
const context=await chromium.launchPersistentContext(`${root}/browser-profile`,{headless:true,
 viewport:{width:1920,height:1080},deviceScaleFactor:1,
 args:['--no-sandbox','--disable-dev-shm-usage','--use-angle=swiftshader','--enable-unsafe-swiftshader','--window-size=1920,1080',`--remote-debugging-port=${host.cdp}`]});
const page=context.pages()[0];page.setDefaultTimeout(15000);
let csrf=null;
page.on('response',async r=>{if(new URL(r.url()).pathname==='/api/v1/owner/session'&&r.ok()){const b=await r.json();csrf=b.csrf_token;}});
async function exists(path){try{await fs.access(path);return true;}catch(error){if(error.code==='ENOENT')return false;throw error;}}
const get=path=>page.evaluate(async p=>{const r=await fetch(p);if(!r.ok)throw new Error(`${p} ${r.status}`);return r.json();},path);
async function resume(mode){
 if(!['reconcile_only','release_held_queue'].includes(mode))throw new Error('Old-work Continue is forbidden');
 await page.getByLabel('Resume mode').selectOption(mode);
 const responsePromise=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`${prefix}/resume`);
 await page.getByRole('button',{name:'Resume',exact:true}).click();
 const response=await responsePromise;const command=response.request().postDataJSON();
 const entry={status:response.status(),command,receipt:await response.json()};result.resumes.push(entry);await save();
 const deadline=Date.now()+45000;
 while(Date.now()<deadline){
  const operation=row('copilot.resumes',command.operation_id);
  if(operation&&['completed','outcome_uncertain','failed'].includes(operation.state)){entry.operation=operation;break;}
  await sleep(200);
 }
 if(entry.operation?.state!=='completed')throw new Error(`Metadata/held-gate operation did not complete: ${mode}`);
}
async function selectVersion(version){
 await page.getByRole('button',{name:'Assets',exact:true}).click();
 await page.getByRole('button').filter({has:page.locator('small',{hasText:new RegExp(`^${version}$`)})}).click();
}
async function decisions(){
 for(const decision of rows('decisions').filter(d=>d.work_id===result.work_id&&d.state==='pending')){
  if(!result.decisions_reported.includes(decision.decision_id)){
   result.decisions_reported.push(decision.decision_id);
   await fs.writeFile(`${root}/unexpected-decision.private.json`,JSON.stringify(decision,null,2));
   console.log(JSON.stringify({event:'COORDINATOR_DECISION_REQUIRED',decision}));await save();
  }
  // No automatic answer. Only an exact explicit coordinator response can populate this file.
  if(await exists(`${root}/coordinator-answer.json`)){
   const answer=JSON.parse(await fs.readFile(`${root}/coordinator-answer.json`,'utf8'));
   if(answer.source!=='coordinator-explicit-message'||answer.decision_id!==decision.decision_id||answer.fingerprint!==decision.fingerprint||!['approve_once','decline'].includes(answer.action))throw new Error('Answer does not bind this exact meaningful decision');
   const body={schema_version:'1.0',operation_id:answer.operation_id,project_id:project,expected_revision:decision.state_revision,fingerprint:decision.fingerprint,action:answer.action};
   result.coordinator_answer=await page.evaluate(async({body,csrf,path})=>{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':body.operation_id,'X-CSRF-Token':csrf},body:JSON.stringify(body)});return {status:r.status,body:await r.json()};},{body,csrf,path:`${prefix}/decisions/${decision.decision_id}/answer`});
   await fs.rename(`${root}/coordinator-answer.json`,`${root}/coordinator-answer-applied.private.json`);await save();
  }
 }
}
try{
 result.browser_version=context.browser().version();
 const cdp=await context.browser().newBrowserCDPSession();
 result.browser_processes=(await cdp.send('SystemInfo.getProcessInfo')).processInfo.map(({id,type})=>({pid:id,type}));
 const log=await fs.readFile(`${root}/server.private.log`,'utf8');
 const pairing=log.match(/http:\/\/127\.0\.0\.1:\d+\/#pair=[^\s]+/);if(!pairing)throw new Error('No private pairing URL');
 await page.goto(pairing[0]);
 const paired=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/v1/owner/session'&&r.request().method()==='POST');
 await page.getByRole('button',{name:'Connect this browser'}).click();
 result.pairing_status=(await paired).status();
 await page.locator('button.project-trigger').waitFor();
 await page.locator('button.project-trigger').click();
 await page.getByLabel('Saved project',{exact:true}).selectOption(project);
 const activate=page.getByRole('button',{name:'Activate selected project',exact:true});
 result.activation={selector:'button.project-trigger',selected_project:project,already_active:!(await activate.isEnabled())};
 if(await activate.isEnabled()){
  const activeResponse=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`${prefix}/activate`);
  await activate.click();const response=await activeResponse;
  result.activation.command=response.request().postDataJSON();result.activation.status=response.status();
 }
 await page.getByRole('button',{name:'Close projects',exact:true}).click();
 await page.getByRole('textbox',{name:'Message Copilot'}).waitFor();
 if(await page.getByLabel('Resume mode').isVisible()){
  await resume('reconcile_only');
  if(await page.getByLabel('Resume mode').isVisible())await resume('release_held_queue');
 }
 if(row('copilot.session','native')?.session_id!==sid)throw new Error('Original native association changed');
 if(JSON.stringify(row('work',oldWork))!==JSON.stringify(before.work))throw new Error('Historical completed work changed');
 if(JSON.stringify(row('requests',oldRequest))!==JSON.stringify(before.request))throw new Error('Historical request changed');
 await selectVersion('m10-asset-v1');
 result.viewport=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}));
 result.pre_send_source_hashes={};
 for(const [name,hash] of Object.entries(before.file_hashes)){
  result.pre_send_source_hashes[name]=digest(await fs.readFile(`${original}/${name}`));
  if(result.pre_send_source_hashes[name]!==hash)throw new Error(`Source changed before SDK send: ${name}`);
 }
 await page.screenshot({path:`${root}/before-queue.private.png`});
 result.phase='ui_preflight_ready';await save();
 await fs.writeFile(`${root}/ui-preflight-ready.json`,JSON.stringify({pairing_status:result.pairing_status,activation:result.activation,viewport:result.viewport,old_work_unchanged:true,old_request_unchanged:true,source_preserved:true,queue_count:rows('requests').length,sdk_sends:0},null,2));
 while(!(await exists(`${root}/send-authorised.json`))){if(Date.now()>host.deadline_epoch_ms-330000)throw new Error('Preflight gate bound reached before send');await sleep(100);}
 const authority=JSON.parse(await fs.readFile(`${root}/send-authorised.json`,'utf8'));
 if(authority.kind!=='one-new-explicit-follow-up'||authority.session_id!==sid)throw new Error('Wrong send allocation');
 if(await exists(`${root}/sdk-send-claimed.json`))throw new Error('Global send allowance already consumed');
 const prompt=await fs.readFile(`${root}/prompt.txt`,'utf8');
 await fs.writeFile(`${root}/owner-queue-claimed.json`,JSON.stringify({prompt_sha256:digest(prompt)}),{flag:'wx'});
 await page.getByRole('textbox',{name:'Message Copilot'}).fill(prompt);
 const admission=page.waitForResponse(r=>r.request().method()==='POST'&&(new URL(r.url()).pathname===`${prefix}/requests`||(new URL(r.url()).pathname===`${prefix}/contexts`&&r.status()>=400)));
 result.sent_epoch_ms=Date.now();
 await page.getByRole('button',{name:'Queue message',exact:true}).click();
 const response=await admission;
 result.queue={status:response.status(),path:new URL(response.url()).pathname,body:response.request().postDataJSON(),receipt:await response.json()};
 if(response.status()>=400)throw new Error(`Queue/context rejected: ${JSON.stringify(result.queue)}`);
 result.new_queue_requests=1;result.request_id=result.queue.body.request_id;result.work_id=result.queue.receipt.work_id;
 result.context_id=result.queue.body.context_id;result.new_context=row('contexts',result.context_id);
 if(row('requests',result.request_id)?.receipt.state==='held'&&await page.getByLabel('Resume mode').isVisible())await resume('release_held_queue');
 result.phase='new_follow_up_running';await save();
 const rootDeadline=result.sent_epoch_ms+238000;
 while(Date.now()<rootDeadline){
  await decisions();
  result.latest_request=row('requests',result.request_id);result.latest_work=row('work',result.work_id);
  if(rows('invocations').some(i=>i.kind==='child'&&!before.historical_records.invocations[i.storage_id]))throw new Error('Child execution prohibited');
  const free=Number((await fs.readFile('/proc/meminfo','utf8')).match(/^MemAvailable:\s+(\d+)/m)[1])*1024;
  result.minimum_headroom_bytes=Math.min(result.minimum_headroom_bytes??free,free);
  if(free<5*1024**3){result.blocker='Protected headroom threatened';break;}
  if(result.latest_work&&['completed','failed','stopped','awaiting_resume','outcome_uncertain'].includes(result.latest_work.state))break;
  await sleep(200);
 }
 result.latest_work=row('work',result.work_id);
 if(result.latest_work?.state==='running'||result.latest_work?.state==='awaiting_user'){
  result.blocker??='SDK root allocation bound reached';
  await page.getByRole('button',{name:'Stop',exact:true}).click();result.owner_stop_requested=true;
 }
 const jobsDeadline=Math.min(Date.now()+300000,host.deadline_epoch_ms-35000);
 while(Date.now()<jobsDeadline){
  result.jobs=rows('jobs').filter(j=>!before.historical_records.jobs[j.job_id]);
  if(result.jobs.every(j=>['succeeded','failed','cancelled'].includes(j.state)))break;
  await sleep(200);
 }
 if(!row('versions','m10-asset-v2'))throw new Error('No actual v2 publication');
 if(result.jobs.length!==2||result.jobs.some(j=>j.state!=='succeeded'))throw new Error('Expected exactly one successful proxy and one still');
 await selectVersion('m10-asset-v2');
 await page.getByRole('button',{name:'2 Keyframes',exact:true}).click();
 const image=page.locator('.review-image img');await expect(image).toBeVisible();
 await expect.poll(()=>image.evaluate(i=>i.complete&&i.naturalWidth===640&&i.naturalHeight===360)).toBe(true);
 result.v2_keyframe=await image.evaluate(i=>({src:i.getAttribute('src'),width:i.naturalWidth,height:i.naturalHeight,complete:i.complete}));
 await page.screenshot({path:`${root}/v2-keyframe.private.png`});
 await page.getByRole('button',{name:'1 3D Inspection',exact:true}).click();
 await expect(page.getByRole('button',{name:'Render this view',exact:true})).toBeEnabled();
 const canvas=page.locator('.review-proxy canvas');
 result.v2_renderer=await canvas.evaluate(element=>{const gl=element.getContext('webgl2');const ext=gl.getExtension('WEBGL_debug_renderer_info');return ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):'unknown';});
 if(!result.v2_renderer.includes('SwiftShader'))throw new Error('Expected software WebGL');
 const box=await canvas.boundingBox();await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);
 for(let i=0;i<50;i++)await page.mouse.wheel(0,-100);
 const a=await canvas.screenshot();
 await page.mouse.move(box.x+box.width*.72,box.y+box.height*.52);await page.mouse.down();
 await page.mouse.move(box.x+box.width*.72+65,box.y+box.height*.52+27,{steps:8});await page.mouse.up();
 const b=await canvas.screenshot();result.v2_orbit={before:digest(a),after:digest(b)};
 if(result.v2_orbit.before===result.v2_orbit.after)throw new Error('Proxy orbit pixels did not change');
 result.v2_mapping=await get(`${prefix}/versions/m10-asset-v2/proxy-mapping`);
 await page.screenshot({path:`${root}/v2-inspection.private.png`});
 result.public_snapshot=await get(`${prefix}/snapshot`);result.phase='v2_visible';
}catch(error){
 result.blocker=String(error);result.phase=result.new_queue_requests?'actual_outcome_retained':'pre_send_blocked';
 await save();await fs.writeFile(`${root}/observer-blocked.json`,JSON.stringify({phase:result.phase,blocker:result.blocker}));
 // Keep the SAME browser/host available for a bounded selector/observer repair. Never requeue.
 while(Date.now()<host.deadline_epoch_ms-15000&&!(await exists(`${root}/close-observer.json`))){
  if(result.sent_epoch_ms&&Date.now()-result.sent_epoch_ms>=238000&&['running','awaiting_user'].includes(row('work',result.work_id)?.state)&&!result.owner_stop_requested){
   await page.getByRole('button',{name:'Stop',exact:true}).click().catch(()=>{});result.owner_stop_requested=true;await save();
  }
  await sleep(250);
 }
}finally{
 result.final_old_request=row('requests',oldRequest);result.final_old_work=row('work',oldWork);
 result.final_requests=rows('requests');result.final_work=rows('work');result.final_contexts=rows('contexts');
 result.final_jobs=rows('jobs');result.final_versions=rows('versions');result.final_invocations=rows('invocations');
 result.final_permissions=rows('copilot.permissions');result.final_sends=rows('copilot.sends');
 result.final_association=row('copilot.session','native');
 await page.screenshot({path:`${root}/final.private.png`}).catch(()=>{});await save();
 await context.close();db.close();
 await fs.writeFile(`${root}/browser.done.json`,JSON.stringify({phase:result.phase,blocker:result.blocker??null}));
 console.log(JSON.stringify({phase:result.phase,request_id:result.request_id,work_id:result.work_id,blocker:result.blocker??null}));
}
