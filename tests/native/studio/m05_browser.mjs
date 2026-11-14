// Real owner UI, no mocked routes. Cloud sends require this run's explicit allocation.
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const root=process.argv[2], action=process.argv[3];
const info=JSON.parse(await fs.readFile(`${root}/processes.json`,'utf8'));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${info.cdp}`);
const page=browser.contexts()[0].pages()[0];
await page.setViewportSize({width:1920,height:1080});page.setDefaultTimeout(15000);
const get=path=>page.evaluate(async path=>{const r=await fetch(path);if(!r.ok)throw new Error(`${path}: ${r.status}`);return r.json();},path);
const project=async()=>(await get('/api/v1/projects')).items[0];
const prefix=async()=>`/api/v1/projects/${(await project()).project_id}`;
const save=(name,value)=>fs.writeFile(`${root}/${name}.json`,JSON.stringify(value,null,2));
async function collect(label){
 const p=await prefix();const state={project:await project(),snapshot:await get(`${p}/snapshot`),items:await get(`${p}/items`),requests:await get(`${p}/requests`),jobs:await get(`${p}/jobs`),selections:await get(`${p}/selections`)};
 state.versions={};state.frames={};state.artifacts={};state.contexts={};
 for(const item of state.items.items)state.versions[item.item_id]=await get(`${p}/items/${item.item_id}/versions`);
 for(const job of state.jobs.items){state.frames[job.job_id]=await get(`${p}/jobs/${job.job_id}/frames`);for(const id of job.artifact_ids)state.artifacts[id]=await get(`${p}/artifacts/${id}`);}
 for(const r of state.requests.items)if(r.submission.context_id)state.contexts[r.submission.context_id]=await get(`${p}/contexts/${r.submission.context_id}`);
 state.view=await page.evaluate(id=>({context:JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)??'null'),viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio}}),state.project.project_id);
 state.browser=browser.version();await save(`public-${label}`,state);return state;
}
async function select(version){
 await page.getByRole('button',{name:'Assets',exact:true}).click();
 await page.getByLabel('Search assets',{exact:true}).fill('');
 await page.locator('.review-catalogue article').filter({has:page.locator('small',{hasText:new RegExp(`^${version}$`)})}).getByRole('button').first().click();
}
async function send(file,label){
 const p=await prefix();const prior=(await get(`${p}/requests`)).items;
 const text=await fs.readFile(`${root}/${file}`,'utf8');
 assert.ok(prior.length<3&&!prior.some(r=>r.submission.text===text),'Original request already admitted or three-root cap exhausted');
 const hash=createHash('sha256').update(text).digest('hex');await fs.writeFile(`${root}/send-${hash}.claim`,text,{flag:'wx',mode:0o600});
 const start=Date.now();await page.getByRole('textbox',{name:'Message Copilot',exact:true}).fill(text);await page.getByRole('button',{name:'Queue message',exact:true}).click();
 let request;
 while(Date.now()-start<175000){
  request=(await get(`${p}/requests`)).items.find(r=>r.submission.text===text);
  if(request&&['completed','failed','stopped','outcome_uncertain','awaiting_resume'].includes(request.receipt.state))break;
  if((await get(`${p}/snapshot`)).pending_decisions.length)break;
  await page.waitForTimeout(500);
 }
 await save(`request-${prior.length+1}`,{request,elapsed_ms:Date.now()-start});
 if(!request||request.receipt.state!=='completed'){const stop=page.getByRole('button',{name:'Stop',exact:true});if(await stop.isEnabled())await stop.click();throw new Error(`Root did not complete inside bound: ${request?.receipt.state}`);}
 console.log(JSON.stringify({request_id:request.request_id,state:request.receipt.state,elapsed_ms:Date.now()-start}));await collect(label);
}
try{
 if(action==='setup'){
  await page.goto((await fs.readFile(`${root}/pairing.url`,'utf8')).trim());
  await page.getByRole('button',{name:'Connect this browser'}).click();await page.getByRole('button',{name:'Choose a project'}).click();
  await page.getByLabel('Workstation folder path').fill(`${root}/project`);await page.getByLabel('New project name').fill('M05 real asset acquisition and reuse');
  await page.getByRole('button',{name:'Create project',exact:true}).click();await page.getByRole('button',{name:'Activate selected project'}).click();
  await collect('empty');
 }else if(action==='collect')await collect(process.argv[4]??'state');
 else if(action==='select')await select(process.argv[4]);
 else if(action==='send')await send(process.argv[4],process.argv[5]??'sent');
 else if(action==='wait-jobs'){
  const end=Date.now()+300000;let jobs;
  do{jobs=(await get(`${await prefix()}/jobs`)).items.filter(j=>!process.argv[4]||j.target.version?.version_id===process.argv[4]);if(jobs.length&&jobs.every(j=>['succeeded','failed','cancelled','outcome_uncertain'].includes(j.state)))break;await page.waitForTimeout(500);}while(Date.now()<end);
  assert.ok(jobs.length&&jobs.every(j=>j.state==='succeeded'),JSON.stringify(jobs.map(j=>({id:j.job_id,state:j.state,error:j.error_code}))));await collect(process.argv[5]??'jobs-complete');
 }else if(action==='ui')await eval(`(async()=>{${await fs.readFile(`${root}/ui.mjs`,'utf8')}\n})()`);
 else if(action==='final')await(await import('./m05_final.mjs')).finish({page,root,collect,get,save,select});
 else throw new Error('Unknown action');
 await page.screenshot({path:`${root}/${process.argv[5]??process.argv[4]??action}.png`,fullPage:false});
 console.log((await page.locator('body').innerText()).slice(-8000));
}finally{await browser.close();}
