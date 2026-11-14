// One continuous Full-HD owner browser. Reconciliation is not a model send.
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const proof=process.argv[2], action=process.argv[3];
const info=JSON.parse(await fs.readFile(`${proof}/processes.json`,'utf8'));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${info.cdp}`);
const page=browser.contexts()[0].pages()[0];
await page.setViewportSize({width:1920,height:1080});page.setDefaultTimeout(15000);
const get=path=>page.evaluate(async path=>{const r=await fetch(path);if(!r.ok)throw new Error(`${path}: ${r.status}`);return r.json();},path);
const project=async()=>(await get('/api/v1/projects')).items[0];
const save=(name,value)=>fs.writeFile(`${proof}/${name}.json`,JSON.stringify(value,null,2));
async function collect(label){
 const projectRecord=await project(),p=`/api/v1/projects/${projectRecord.project_id}`;
 const state={project:projectRecord,snapshot:await get(`${p}/snapshot`),items:await get(`${p}/items`),requests:await get(`${p}/requests`),jobs:await get(`${p}/jobs`),selections:await get(`${p}/selections`),versions:{},contexts:{}};
 for(const item of state.items.items)state.versions[item.item_id]=await get(`${p}/items/${item.item_id}/versions`);
 for(const r of state.requests.items)if(r.submission.context_id)state.contexts[r.submission.context_id]=await get(`${p}/contexts/${r.submission.context_id}`);
 state.view=await page.evaluate(id=>({context:JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)??'null'),viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio}}),projectRecord.project_id);
 state.browser=browser.version();await save(`public-${label}`,state);return state;
}
async function select(id){
 await page.getByRole('button',{name:'Assets',exact:true}).click();
 const button=page.locator('.review-catalogue article').filter({has:page.locator('small',{hasText:new RegExp(`^${id}$`)})}).getByRole('button').first();
 await button.click();
}
async function reconcile(){
 const before=await collect('before-release');assert.equal(before.requests.items.length,3);
 assert.ok(before.requests.items.every(r=>r.receipt.state==='completed'));
 await page.getByLabel('Resume mode').selectOption('release_held_queue');
 const ack=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/resume'));
 await page.getByRole('button',{name:'Resume',exact:true}).click();
 const response=await ack;assert.ok(response.ok(),await response.text());await save('release-receipt',await response.json());
 await page.waitForTimeout(1000);
 const after=await collect('released');assert.equal(after.requests.items.length,3);
 console.log(JSON.stringify({release_only:true,root_requests:3,active_work:after.snapshot.active_work}));
}
async function send(){
 const before=await collect('pre-send');assert.equal(before.requests.items.length,3);
 const text=await fs.readFile(`${proof}/prompt.txt`,'utf8');
 await fs.writeFile(`${proof}/single-send.claim`,JSON.stringify({original_completed_request_ids:before.requests.items.map(r=>r.request_id),text}),{flag:'wx',mode:0o600});
 const start=Date.now();await page.getByRole('textbox',{name:'Message Copilot',exact:true}).fill(text);
 await page.getByRole('button',{name:'Queue message',exact:true}).click();
 let request;const observations=[],p=`/api/v1/projects/${before.project.project_id}`;
 while(Date.now()-start<175000){
  const [requests,snapshot]=await Promise.all([get(`${p}/requests`),get(`${p}/snapshot`)]);
  request=requests.items.find(r=>!before.requests.items.some(p=>p.request_id===r.request_id));
  observations.push({elapsed_ms:Date.now()-start,request_id:request?.request_id,state:request?.receipt.state,pending_decision_ids:snapshot.pending_decisions.map(d=>d.decision_id)});
  if(request&&['completed','failed','stopped','outcome_uncertain','awaiting_resume'].includes(request.receipt.state))break;
  if(snapshot.pending_decisions.length)break;
  await page.waitForTimeout(800);
 }
 await save('request-observations',observations);
 await save('single-request',{request,elapsed_ms:Date.now()-start});
 if(!request||request.receipt.state!=='completed'){
  const stop=page.getByRole('button',{name:'Stop',exact:true});if(await stop.isEnabled())await stop.click();
  throw new Error(`Single request did not complete: ${request?.receipt.state}. Preserve IDs; do not replace.`);
 }
 console.log(JSON.stringify({request_id:request.request_id,state:request.receipt.state,elapsed_ms:Date.now()-start}));
 await collect('after-send');
}
try{
 if(action==='open'){
  await page.goto((await fs.readFile(`${proof}/pairing.url`,'utf8')).trim());
  await page.getByRole('button',{name:'Connect this browser'}).click();await page.getByRole('textbox',{name:'Message Copilot'}).waitFor();await collect('opened');
 }else if(action==='release')await reconcile();
 else if(action==='send')await send();
 else if(action==='collect')await collect(process.argv[4]??'state');
 else if(action==='review'){
  await select('asset-cube-v1');await page.screenshot({path:`${proof}/old-asset-history.png`});
  await select('asset-cube-v3');await page.locator('.review-image img').waitFor();
  await page.waitForFunction(()=>Object.keys(localStorage).filter(k=>k.startsWith('proscenium.owner.view.context.')).some(k=>JSON.parse(localStorage.getItem(k))?.target?.version_id==='asset-cube-v3'));
  const before=await collect('before-reclick');await select('asset-cube-v3');await page.waitForTimeout(300);
  const after=await collect('after-reclick');assert.deepEqual(after.view.context,before.view.context);
  assert.equal(after.view.context.target.origin,'blender_render');await save('same-current-context',{before:before.view,after:after.view,exactly_preserved:true});
  await page.screenshot({path:`${proof}/current-asset-reclicked.png`});
  const final=await collect('final');await page.reload();await page.getByRole('textbox',{name:'Message Copilot'}).waitFor();await page.locator('.review-image img').waitFor();
  const reopened=await collect('reopened');assert.equal(reopened.requests.items.length,final.requests.items.length);assert.deepEqual(reopened.jobs,final.jobs);
  await save('reopen-observation',{requests_before:final.requests.items.length,requests_after:reopened.requests.items.length,automatic_resume_sent:false,jobs_unchanged:true});
 }else throw new Error('Unknown action');
 await page.screenshot({path:`${proof}/${action}.png`,fullPage:false});
 console.log((await page.locator('body').innerText()).slice(-8000));
}finally{await browser.close();}
