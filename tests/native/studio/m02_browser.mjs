// Scripted test-user interactions. Native SDK association requests are real.
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import {setTimeout as delay} from 'node:timers/promises';
const root=process.argv[2], action=process.argv[3], argument=process.argv[4]??action;
const info=JSON.parse(await fs.readFile(`${root}/processes.json`,'utf8'));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${info.cdp}`);
const page=browser.contexts()[0].pages()[0];
await page.setViewportSize({width:1920,height:1080});
page.setDefaultTimeout(20000);
async function get(path){return page.evaluate(async p=>{const r=await fetch(p,{cache:'no-store'});if(!r.ok)throw new Error(`${p}:${r.status}`);return r.json()},path);}
async function active(){const ps=await get('/api/v1/projects');const p=ps.items.find(p=>p.state!=='inactive');if(!p)throw new Error('No active project');return p;}
async function collect(){
 const projects=await get('/api/v1/projects'), p=await active(), base=`/api/v1/projects/${p.project_id}`;
 const result={projects,snapshot:await get(`${base}/snapshot`),requests:await get(`${base}/requests`),items:await get(`${base}/items`),jobs:await get(`${base}/jobs`),versions:{},viewport:await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})),browser:browser.version()};
 for(const item of result.items.items)result.versions[item.item_id]=await get(`${base}/items/${item.item_id}/versions`);
 result.visible={source:await page.locator('.source-identity').count()?await page.locator('.source-identity').textContent():null,pinned:await page.locator('.review-catalogue button[aria-pressed=true]').count()?await page.locator('.review-catalogue button[aria-pressed=true]').textContent():null,text:await page.locator('body').innerText()};
 await fs.writeFile(`${root}/browser-${argument}.json`,JSON.stringify(result,null,2));
 return result;
}
try{
 if(action==='setup'){
  await page.goto((await fs.readFile(`${root}/pairing.url`,'utf8')).trim());
  await page.getByRole('button',{name:'Connect this browser'}).click();
 }
 if(action==='setup'||action==='create'){
  const label=action==='setup'?'A':argument;
  await page.locator('.project-trigger').click();
  await page.getByLabel('Workstation folder path').fill(`${root}/project-${label.toLowerCase()}`);
  await page.getByLabel('New project name').fill(`M02 project ${label}`);
  await page.getByRole('button',{name:'Create project',exact:true}).click();
  await page.getByRole('button',{name:'Activate selected project'}).click();
  await page.waitForFunction(label=>document.querySelector('.project-trigger')?.textContent?.includes(`M02 project ${label}`),label);
  if(await page.getByRole('button',{name:'Close projects'}).count())await page.getByRole('button',{name:'Close projects'}).click();
  const p=await active();
  await fs.writeFile(`${p.path}/brief/title.md`,`# M02 ${label} independent title\n\nSentinel: M02-${label}-only-20261114\n`);
 }else if(action==='switch'){
  await page.locator('.project-trigger').click();
  const ps=await get('/api/v1/projects'),project=ps.items.find(p=>p.name===`M02 project ${argument}`);
  await page.getByLabel('Saved project').selectOption(project.project_id);
  await page.getByRole('button',{name:'Activate selected project'}).click();
  await page.waitForFunction(label=>document.querySelector('.project-trigger')?.textContent?.includes(`M02 project ${label}`),argument);
  if(await page.getByRole('button',{name:'Close projects'}).count())await page.getByRole('button',{name:'Close projects'}).click();
 }else if(action==='send'){
  const p=await active(),base=`/api/v1/projects/${p.project_id}`;
  if((await fs.readdir(root)).filter(name=>name.startsWith('cloud-')&&name.endsWith('.claim')).length>=2)throw new Error('Two-root cloud allocation exhausted. No new Send.');
  if((await get(`${base}/requests`)).items.length)throw new Error('One association request already exists. Never resend.');
  await fs.writeFile(`${root}/cloud-${p.project_id}.claim`,'prepared scripted test-user request',{flag:'wx',mode:0o600});
  await page.getByRole('textbox',{name:'Message Copilot'}).fill('SCRIPTED M02 TEST-USER INPUT. Read only the actual active-project file brief/title.md. Reply with its title and sentinel. Do not edit files, author creative content, delegate, start a service, inspect another project, or call a provider. This single small request establishes this project\'s real native SDK session association.');
  await page.getByRole('button',{name:'Queue message',exact:true}).click();
  const deadline=Date.now()+120000;
  while(true){
   const snapshot=await get(`${base}/snapshot`),requests=await get(`${base}/requests`);
   if(!snapshot.active_work&&requests.items.length===1&&requests.items[0].receipt.state==='completed')break;
   if(Date.now()>deadline)throw new Error('Original request did not settle within allocation. Inspect, never resend.');
   await delay(500);
  }
 }else if(action==='view'){
  await page.getByRole('button',{name:'Assets',exact:true}).click();
  await page.locator('.review-catalogue button').filter({has:page.locator('small',{hasText:new RegExp(`^${argument}$`)})}).click();
 }else if(action==='release'){
  await page.getByLabel('Resume mode').selectOption('release_held_queue');
  await page.getByRole('button',{name:'Resume',exact:true}).click();
  const p=await active(),base=`/api/v1/projects/${p.project_id}`,deadline=Date.now()+120000;
  while(true){
   const snapshot=await get(`${base}/snapshot`),requests=await get(`${base}/requests`);
   if(!snapshot.active_work&&requests.items.length===1&&requests.items[0].receipt.state==='completed')break;
   if(Date.now()>deadline)throw new Error('Original held request failed to settle. No resubmission.');
   await delay(500);
  }
 }else if(action==='reload'){
  await page.reload();await page.getByRole('textbox',{name:'Message Copilot'}).waitFor();
 }else if(action==='metadata'){
  const p=await active(),metadata=browser.contexts()[0];
  const tab=await metadata.newPage();await tab.setViewportSize({width:1920,height:1080});
  const response=await tab.goto(`${info.origin}/api/v1/projects/${p.project_id}/items/${argument}/versions`);
  await fs.writeFile(`${root}/metadata-${argument}.json`,await tab.locator('body').innerText());
  await tab.screenshot({path:`${root}/metadata-${argument}.png`,fullPage:false});
  await tab.close();
  if(!response.ok())throw new Error(`Metadata API: ${response.status()}`);
 }else if(action==='cancel'){
  const p=await active(),base=`/api/v1/projects/${p.project_id}`,j=(await get(`${base}/jobs`)).items[0];
  const result=await page.evaluate(async ({base,j})=>{
   const entry=Object.keys(localStorage).find(k=>k.endsWith('session'));
   const owner=JSON.parse(localStorage.getItem(entry));
   const body={schema_version:'1.0',operation_id:crypto.randomUUID(),project_id:j.project_id,expected_revision:j.state_revision};
   const r=await fetch(`${base}/jobs/${j.job_id}/cancel`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':body.operation_id,'X-CSRF-Token':owner.csrf_token},body:JSON.stringify(body)});
   return {status:r.status,body:await r.json()};
  },{base,j});
  await fs.writeFile(`${root}/cancel.json`,JSON.stringify(result,null,2));
  if(![200,202].includes(result.status))throw new Error(`Cancellation: ${result.status}`);
 }
 const result=await collect();
 await page.screenshot({path:`${root}/browser-${argument}.png`,fullPage:true});
 console.log(JSON.stringify({project:result.snapshot.project,requests:result.requests.items.map(r=>({id:r.request_id,state:r.receipt.state})),items:result.items.items.map(i=>({id:i.item_id,current:i.current_version_id})),jobs:result.jobs.items.map(j=>({id:j.job_id,state:j.state})),visible:result.visible.source,viewport:result.viewport}));
}finally{await browser.close();}
