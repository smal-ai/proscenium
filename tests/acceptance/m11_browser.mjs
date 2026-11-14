// Real Full-HD compiled browser, no mocked API and no substitute model effects.
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const root=process.argv[2],action=process.argv[3],label=process.argv[4]??action;
const info=JSON.parse(await fs.readFile(`${root}/processes.json`,'utf8'));
const {chromium}=await import(pathToFileURL(`${info.dependencies}/playwright/index.mjs`));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${info.cdp}`);
const page=browser.contexts()[0].pages()[0];
await page.setViewportSize({width:1920,height:1080});
page.setDefaultTimeout(20000);
const save=async(name,value)=>fs.writeFile(`${root}/${name}.json`,JSON.stringify(value,null,2));
const get=async path=>page.evaluate(async p=>{const r=await fetch(p);if(!r.ok)throw new Error(`${p}:${r.status}`);return r.json()},path);
const active=async()=>(await get('/api/v1/projects')).items.find(p=>p.state!=='inactive');
async function collect(){
 const p=await active(),base=`/api/v1/projects/${p.project_id}`;
 const value={project:p,snapshot:await get(`${base}/snapshot`),requests:await get(`${base}/requests`),items:await get(`${base}/items`),jobs:await get(`${base}/jobs`),versions:{},viewport:await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})),browser:browser.version()};
 for(const item of value.items.items)value.versions[item.item_id]=await get(`${base}/items/${item.item_id}/versions`);
 value.visible=await page.locator('body').innerText();await save(`public-${label}`,value);return value;
}
try{
 if(action==='setup'||action==='pair'){
  await page.goto('about:blank');
  await page.goto((await fs.readFile(`${root}/pairing.url`,'utf8')).trim());
  await page.getByRole('button',{name:'Connect this browser'}).click();
  if(action==='setup'){
  await page.locator('.project-trigger').click();
  await page.getByLabel('Workstation folder path').fill(`${root}/project`);
  await page.getByLabel('New project name').fill('M11 playable Unity acceptance');
  await page.getByRole('button',{name:'Create project',exact:true}).click();
  await page.getByRole('button',{name:'Activate selected project'}).click();
  if(await page.getByRole('button',{name:'Close projects'}).count())await page.getByRole('button',{name:'Close projects'}).click();
  }
 }else if(action==='reconcile-fresh'){
  const project=await active();
  const result=await page.evaluate(async p=>{
   const key=Object.keys(localStorage).find(k=>k.endsWith('.session'));
   const session=JSON.parse(localStorage.getItem(key));
   const body={schema_version:'1.0',operation_id:crypto.randomUUID(),project_id:p.project_id,work_id:null,expected_revision:p.state_revision,mode:'reconcile_only'};
   const response=await fetch(`/api/v1/projects/${p.project_id}/resume`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':body.operation_id,'X-CSRF-Token':session.csrf_token},body:JSON.stringify(body)});
   return {status:response.status,body:await response.json(),request:body};
  },project);
  await save('fresh-reconcile-owner-http',result);await page.waitForTimeout(1000);
 }else if(action==='release'||action==='reconcile'){
  await page.getByLabel('Resume mode').selectOption(action==='release'?'release_held_queue':'reconcile_only');
  await page.getByRole('button',{name:'Resume',exact:true}).click();
  await page.waitForTimeout(1000);
 }else if(action==='send'){
  const p=await active(),base=`/api/v1/projects/${p.project_id}`;
  const text=await fs.readFile(`${root}/${label}.txt`,'utf8');
  const previous=(await get(`${base}/requests`)).items;
  if(previous.length>=3||previous.some(r=>r.submission.text===text))throw new Error('Root allocation exhausted or original prompt already sent; inspect, never resend');
  await fs.writeFile(`${root}/cloud-${previous.length+1}.claim`,JSON.stringify({prompt_sha256:createHash('sha256').update(text).digest('hex'),at:Date.now()}),{flag:'wx',mode:0o600});
  await page.getByRole('textbox',{name:'Message Copilot',exact:true}).fill(text);
  await page.getByRole('button',{name:'Queue message',exact:true}).click();
  const start=Date.now();let observed;
  while(Date.now()-start<300000){
   const snapshot=await get(`${base}/snapshot`);const requests=(await get(`${base}/requests`)).items;
   observed=requests.find(r=>r.submission.text===text);
   if(snapshot.pending_decisions.length)throw new Error('Unexpected owner decision; allocation does not permit an extra root');
   const jobs=(await get(`${base}/jobs`)).items;
   const job=jobs.find(j=>j.job_id==='m11-import-job');
   if(label==='first'&&job&&['starting','running'].includes(job.state)){
    await collect();await page.screenshot({path:`${root}/browser-${label}-before-disconnect.png`});
    await save('browser-disconnect',{at:Date.now(),job,request:observed,kind:'Navigated actual SPA away; closes owner WebSocket while independent Editor job runs'});
    await page.goto('about:blank');
    console.log(JSON.stringify({detached:true,job_id:job.job_id,root_request_id:observed?.request_id}));
    await browser.close();process.exit(0);
   }
   if(observed&&['completed','failed','stopped','outcome_uncertain','awaiting_resume'].includes(observed.receipt.state))break;
   await page.waitForTimeout(500);
  }
  await save(`request-${label}`,{observed,elapsed_ms:Date.now()-start});
  if(!observed||observed.receipt.state!=='completed'){
   const stop=page.getByRole('button',{name:'Stop',exact:true});if(await stop.isEnabled())await stop.click();
   throw new Error(`Root did not complete inside allocation: ${observed?.receipt.state}`);
  }
 }else if(action==='reconnect'){
  await page.goto(info.origin);await page.getByRole('textbox',{name:'Message Copilot'}).waitFor();
 }else if(action==='catalogue'){
  await page.getByRole('button',{name:'Assets',exact:true}).click();
  const entry=page.locator('.review-catalogue button').filter({hasText:'M11 playable'});if(await entry.count())await entry.first().click();
 }else if(action!=='collect')throw new Error(action);
 await collect();await page.screenshot({path:`${root}/browser-${label}.png`});
 console.log((await page.locator('body').innerText()).slice(-9000));
}finally{await browser.close();}
