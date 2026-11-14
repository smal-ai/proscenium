// Actual compiled production UI and backend. No API interception or fake callbacks.
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import { createHash } from 'node:crypto';
const root=process.argv[2], action=process.argv[3];
const info=JSON.parse(await fs.readFile(`${root}/host.private.json`,'utf8'));
const chrome=JSON.parse(await fs.readFile(`${root}/browser.private.json`,'utf8'));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${chrome.cdp}`);
const context=browser.contexts()[0];
let page=context.pages()[0]??await context.newPage();
await page.setViewportSize({width:1920,height:1080});
page.setDefaultTimeout(20000);
const prefix=`/api/v1/projects/${info.project_id}`;
const get=async path=>page.evaluate(async path=>{const r=await fetch(path);if(!r.ok)throw new Error(`Read ${r.status}`);return r.json();},path);
const requests=async()=>(await get(`${prefix}/requests?limit=100`)).items;
const snapshot=async()=>get(`${prefix}/snapshot`);
async function screenshot(name){await page.screenshot({path:`${root}/${name}.png`,fullPage:true});}
async function waitFor(probe,description,timeout=170000){const end=Date.now()+timeout;while(Date.now()<end){const r=await probe();if(r)return r;await new Promise(r=>setTimeout(r,150));}throw new Error(`Timed out: ${description}`);}
async function select(label){
 await page.getByRole('button',{name:'Assets',exact:true}).click();
 await page.getByRole('button').filter({has:page.locator('small',{hasText:new RegExp(`^m03-version-${label}$`)})}).click();
 await page.getByRole('heading',{name:`M03 labelled ${label==='A'?'scene':'asset'} ${label}`,exact:true}).waitFor();
 await page.waitForFunction(label=>{const image=document.querySelector('.review-image img');return image?.src.includes(`m03-image-${label}`)&&image.complete&&image.naturalWidth===320;},label);
}
async function send(text,mode='queue'){
 const old=await requests();
 if(old.some(r=>r.submission.text===text))throw new Error('Already admitted: inspect the original, never resubmit');
 const path=`${root}/ui-send-${createHash('sha256').update(text).digest('hex')}.json`;
 await fs.writeFile(path,JSON.stringify({prepared_at:new Date().toISOString(),mode,prompt_sha256:createHash('sha256').update(text).digest('hex')}),{flag:'wx',mode:0o600});
 await page.getByLabel('Request mode',{exact:true}).selectOption(mode);
 if(mode==='steer'){
  const active=(await snapshot()).active_work;
  if(!active)throw new Error('No active work to steer');
  await page.getByLabel('Steer active work target',{exact:true}).selectOption(active.work_id);
 }
 await page.getByRole('textbox',{name:'Message Copilot',exact:true}).fill(text);
 const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(`${prefix}/requests`));
 await page.getByRole('button',{name:mode==='steer'?'Steer active work':'Queue message',exact:true}).click();
 const r=await response, body=await r.json();
 if(!r.ok())throw new Error(`Submission ${r.status()}: ${JSON.stringify(body)}`);
 await fs.writeFile(path,JSON.stringify({receipt:body,submission:r.request().postDataJSON()},null,2));
 return body;
}
let result={action,at:new Date().toISOString()};
try{
 if(action==='setup'){
  await page.goto(info.pair_url);
  await page.getByRole('button',{name:'Connect this browser',exact:true}).click();
  await page.getByRole('textbox',{name:'Message Copilot',exact:true}).waitFor();
  result.viewport=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}));
  result.browser=browser.version();
  result.requests_before=await requests();
 }else if(action==='start-a'){
  await select('A');
  result.receipt=await send('M03 bounded runtime test for scene A. First do the small direct lead task by running exactly `python3 m03_direct.py` with bash once. Then delegate exactly once to authoring-specialist. Ask that specialist to run exactly `python3 m03_hold.py` with bash in this active project; it is a harmless project-local heartbeat helper with a hard 70-second self-timeout. It must remain on that task until completion or native Stop, not start other work. Do not inspect other files, install anything, render, or use external services. Do not answer an owner question yourself. Report actual outcomes only.');
 }else if(action==='queue-b'){
  await waitFor(async()=>{const p=await get(`${prefix}/events?after=0&limit=100`);return p.events.find(e=>e.event_type==='subagent.started');},'real native child started');
  await select('B');
  result.receipt=await send('M03 queued request B. Read get_version for the version in THIS request\'s captured selection and report its actual labelled name. Do not use whichever item is now visible in the browser. Then use ask_user exactly once: ask whether TEST_SHARE_SIMULATION, a test-only in-project simulated consequential operation, is approved. Explain that no actual sharing, purchase, installation or external action occurs. Wait for the owner\'s exact decision. If declined, do nothing further and finish. Never perform real sharing, authoring, rendering or delegation for this request.');
  if(result.receipt.state!=='held')throw new Error('B was not held behind A');
  await select('A');
  result.view_after_queue=await page.getByRole('heading',{level:1}).innerText();
 }else if(action==='steer-stop'){
  const marker=`${root}/project/.proscenium/tool-output/m03/child.jsonl`;
  const started=await waitFor(async()=>{try{const rows=(await fs.readFile(marker,'utf8')).trim().split('\n').map(JSON.parse);return rows.find(r=>r.event==='started');}catch{return false;}},'actual child helper active');
  result.actual_child_pid=started.pid;
  const events=await get(`${prefix}/events?after=0&limit=100`);
  result.active_child_tool=events.events.find(e=>e.event_type==='tool.started'&&e.actor.kind==='subagent'&&e.payload.name==='bash');
  if(!result.active_child_tool)throw new Error('No attributed actual child builtin execution');
  await fs.writeFile(`${root}/start-independent`,'start owned admitted encoder\n');
  result.independent_before=await waitFor(async()=>{const j=await get(`${prefix}/jobs/m03-independent-encode`);return j.state==='running'&&j;},'independent admitted encoder running',15000);
  result.steer=await send('M03 Steer correction for this active work: report the actual bounded-helper outcome and preserve the original source/work identities. A native Stop must not be described as natural helper completion. This correction does not undo already executed actions. Do not delegate again.','steer');
  await screenshot('03-steer-before-stop');
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(`${prefix}/requests`));
  await page.getByRole('button',{name:'Stop',exact:true}).click();
  const r=await response;result.stop_admission=await r.json();
  await screenshot('04-stop-requested');
  result.stop=await waitFor(async()=>{const rows=await requests();return rows.find(v=>v.request_id===result.stop_admission.request_id&&['completed','outcome_uncertain','failed'].includes(v.receipt.state));},'Stop terminal/uncertain observation',45000);
  result.independent_after=await get(`${prefix}/jobs/m03-independent-encode`);
  result.held_after=(await requests()).filter(r=>r.receipt.state==='held');
  result.marker_before_observation=await fs.readFile(marker,'utf8');
  await new Promise(r=>setTimeout(r,3000));
  result.marker_after_observation=await fs.readFile(marker,'utf8');
  result.no_further_helper_effects=result.marker_before_observation===result.marker_after_observation;
  await screenshot('05-stop-observed');
 }else if(action==='reload'){
  result.before=(await requests()).map(r=>({id:r.request_id,state:r.receipt.state}));
  await page.reload();await page.getByRole('textbox',{name:'Message Copilot',exact:true}).waitFor();
  result.after=(await requests()).map(r=>({id:r.request_id,state:r.receipt.state}));
  result.user_entry_ids=await page.locator('.user-message').evaluateAll(nodes=>nodes.map(n=>n.dataset.requestId));
  result.unique_user_entries=new Set(result.user_entry_ids).size===result.user_entry_ids.length;
 }else if(action==='resume'){
  const mode=process.argv[4]??'reconcile_only';
  await page.getByLabel('Resume mode',{exact:true}).selectOption(mode);
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(`${prefix}/resume`));
  await page.getByRole('button',{name:'Resume',exact:true}).click();
  const r=await response;result.status=r.status();result.body=await r.json();result.command=r.request().postDataJSON();
  await new Promise(r=>setTimeout(r,1500));
  result.requests=await requests();
 }else if(action==='decision-disconnect'){
  result.decision=await waitFor(async()=>{const s=await snapshot();return s.pending_decisions[0];},'real native owner decision',175000);
  await page.locator('.decision-card').waitFor();
  await screenshot('08-decision-before-disconnect');
  const keeper=await context.newPage(); // Keep the browser session alive while all application connections close.
  await page.close(); // Real browser page/WS disconnect, NOT a native SDK transport fault.
  await new Promise(r=>setTimeout(r,1500));
  page=await context.newPage();await page.setViewportSize({width:1920,height:1080});
  await page.goto(info.origin);await page.locator('.decision-card').waitFor();
  result.restored=(await snapshot()).pending_decisions[0];
  await keeper.close();
  if(result.decision.decision_id!==result.restored.decision_id||result.decision.fingerprint!==result.restored.fingerprint||result.decision.state_revision!==result.restored.state_revision)throw new Error('Owner decision changed across disconnect');
  await screenshot('09-decision-restored');
 }else if(action==='decision-cycle'){
  await select('B');
  result.receipt=await send('M03 independent bounded owner-card test. Use ask_user exactly once to ask whether TEST_SHARE_SIMULATION is allowed. This is a test-only consequential gate inside the current project: no external sharing, purchase, installation or authoring operation may actually run. Offer DENY_TEST_SHARE_SIMULATION and ALLOW_LOCAL_TEST_ONLY. Wait for the owner\'s exact answer. If denied, explicitly report that nothing was executed and finish. Do not inspect files, use other tools or delegate.');
  result.before=await waitFor(async()=>{const s=await snapshot();return s.pending_decisions[0];},'real native user-input callback');
  await page.locator('.decision-card').waitFor();await screenshot('08-decision-before-disconnect');
  const keeper=await context.newPage();
  await page.close();await new Promise(r=>setTimeout(r,1500));
  page=await context.newPage();await page.setViewportSize({width:1920,height:1080});await page.goto(info.origin);
  await page.locator('.decision-card').waitFor();await keeper.close();
  result.restored=(await snapshot()).pending_decisions[0];
  if(result.before.decision_id!==result.restored.decision_id||result.before.fingerprint!==result.restored.fingerprint||result.before.state_revision!==result.restored.state_revision)throw new Error('Pending decision identity/revision/fingerprint did not survive');
  await screenshot('09-decision-restored');
  const privateBoundary=await waitFor(async()=>{try{return JSON.parse(await fs.readFile(`${root}/boundary.private.json`,'utf8'));}catch{return false;}},'private boundary test authority',5000);
  const prohibited={schema_version:'1.0',operation_id:'m03-forged-owner',project_id:info.project_id,expected_revision:result.before.state_revision,fingerprint:result.before.fingerprint,action:'answer',answer:'DENY_TEST_SHARE_SIMULATION'};
  const toolHeaders={'Origin':info.origin,'Authorization':`Bearer ${privateBoundary.token}`,'Content-Type':'application/json','Idempotency-Key':prohibited.operation_id};
  let forbidden=await fetch(`${info.origin}${prefix}/decisions/${result.before.decision_id}/answer`,{method:'POST',headers:toolHeaders,body:JSON.stringify(prohibited)});
  result.tool_to_owner={status:forbidden.status,body:await forbidden.json()};
  result.scope_rejections=[];
  for(const name of ['escape','environment']){
   const command={schema_version:'1.0',operation_id:`m03-block-${name}`,project_id:info.project_id,artifact_id:`m03-block-${name}`,staging_path:`.proscenium/tool-output/staging/${name}.png`,kind:'image',purpose:'reference',sha256:'a'.repeat(64),media_type:'image/png'};
   const response=await fetch(`${info.origin}/internal/v1/artifacts`,{method:'POST',headers:{...toolHeaders,'Idempotency-Key':command.operation_id},body:JSON.stringify(command)});
   result.scope_rejections.push({name,status:response.status,body:await response.json()});
  }
  const card=page.locator('.decision-card');await card.getByLabel('Decision answer',{exact:true}).fill('DENY_TEST_SHARE_SIMULATION');
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes('/decisions/')&&r.url().endsWith('/answer'));
  await card.getByRole('button',{name:'Send answer',exact:true}).click();
  const answer=await response;result.answer_status=answer.status();result.answer_receipt=await answer.json();result.answer_command=answer.request().postDataJSON();
  result.completed=await waitFor(async()=>{const rows=await requests();return rows.find(r=>r.request_id===result.receipt.request_id&&['completed','failed','outcome_uncertain'].includes(r.receipt.state));},'root after owner denial');
  result.owner_retry=await page.evaluate(async({prefix,decision,body})=>{
   const session=JSON.parse(localStorage.getItem('proscenium.owner.session'));
   const response=await fetch(`${prefix}/decisions/${decision}/answer`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token,'Idempotency-Key':body.operation_id},body:JSON.stringify(body)});
   return {status:response.status,body:await response.json()};
  },{prefix,decision:result.before.decision_id,body:result.answer_command});
  if(result.tool_to_owner.status!==401||result.scope_rejections.some(r=>r.status!==403))throw new Error('Caller/source boundary did not reject');
  if(result.completed.receipt.state!=='completed')throw new Error('Owner-input root did not complete');
 }else if(action==='decision-decline'){
  const card=page.locator('.decision-card');await card.waitFor();
  result.before=(await snapshot()).pending_decisions[0];
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes('/decisions/')&&r.url().endsWith('/answer'));
  await card.getByRole('button',{name:'Decline',exact:true}).click();
  const r=await response;result.status=r.status();result.receipt=await r.json();result.command=r.request().postDataJSON();
  result.completed=await waitFor(async()=>{const rows=await requests();return rows.find(r=>r.submission.type==='queue'&&r.submission.text.startsWith('M03 queued request B.')&&['completed','failed','outcome_uncertain'].includes(r.receipt.state));},'B completes after actual owner answer',175000);
 }else if(action==='transport'){
  await select('A');
  result.receipt=await send('M03 owned native transport test. Run exactly `python3 m03_transport.py` using bash once. It is an in-project harmless 35-second helper. Do not delegate, run other commands or retry the operation. Report only an actually observed outcome.');
  const marker=`${root}/project/.proscenium/tool-output/m03/transport.jsonl`;
  result.active=await waitFor(async()=>{try{return JSON.parse((await fs.readFile(marker,'utf8')).split('\n')[0]);}catch{return false;}},'accepted helper before owned transport cut');
  result.before_cut=(await requests()).find(r=>r.request_id===result.receipt.request_id);
  result.sdk_ack_loss_claimed=false;
  await fs.writeFile(`${root}/cut-owned-transport`,'close actual stdin transport, no delayed ACK simulation\n');
  result.after=await waitFor(async()=>{const s=await snapshot();const rows=await requests();const r=rows.find(r=>r.request_id===result.receipt.request_id);return r?.receipt.state==='outcome_uncertain'&&{snapshot:s,request:r};},'native transport uncertainty',30000);
 }else if(action==='collect'){
  result.snapshot=await snapshot();result.requests=await requests();
  result.events=[];let after=0;
  while(true){const p=await get(`${prefix}/events?after=${after}&limit=100`);result.events.push(...p.events);if(p.next_after>=p.last_available)break;after=p.next_after;}
  result.contexts={};for(const r of result.requests){if(r.submission.context_id)result.contexts[r.request_id]=await get(`${prefix}/contexts/${r.submission.context_id}`);}
  result.body_text=await page.locator('body').innerText();
  for(const summary of await page.locator('details.usage summary').all()){await summary.click();}
 }else throw new Error('Unknown action');
 await screenshot(`action-${action}-${process.argv[4]??'default'}`);
 result.viewport=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}));
 await fs.writeFile(`${root}/browser-${action}-${process.argv[4]??'default'}.json`,JSON.stringify(result,null,2));
 console.log(JSON.stringify({action,stop_state:result.stop?.receipt.state,status:result.status,receipt:result.receipt,viewport:result.viewport}));
}catch(error){
 result.error=String(error);await fs.writeFile(`${root}/browser-${action}-failed.json`,JSON.stringify(result,null,2));
 await screenshot(`FAILED-${action}`);throw error;
}finally{await browser.close();}
