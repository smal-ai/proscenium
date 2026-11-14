// Actual Full-HD owner UI. No new Queue, no route mocks or direct publication call.
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const root=process.argv[2],action=process.argv[3];
const info=JSON.parse(await fs.readFile(`${root}/processes.json`,'utf8'));
const {chromium}=await import(pathToFileURL(`${info.dependencies}/playwright/index.mjs`));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${info.cdp}`);
const page=browser.contexts()[0].pages()[0];await page.setViewportSize({width:1920,height:1080});page.setDefaultTimeout(20000);
const project='project-6efca2f5-8ccd-49bb-bea1-f552ef065224',work='work-4a7e644e78514109b4a5232316e3aacc',request='1a914aa8-2bbc-461a-a87d-1bb7431de02c';
const base=`/api/v1/projects/${project}`;
const get=path=>page.evaluate(async path=>{const r=await fetch(path,{cache:'no-store'});if(!r.ok)throw Error(`${path}: ${r.status}`);return r.json()},path);
const save=(name,value)=>fs.writeFile(`${root}/${name}.json`,JSON.stringify(value,null,2));
async function collect(label){
 const result={projects:await get('/api/v1/projects'),snapshot:await get(`${base}/snapshot`),requests:await get(`${base}/requests`),jobs:await get(`${base}/jobs`),items:await get(`${base}/items`),versions:{},viewport:await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})),browser:browser.version()};
 for(const item of result.items.items)result.versions[item.item_id]=await get(`${base}/items/${item.item_id}/versions`);
 result.visible=await page.locator('body').innerText();await save(`public-${label}`,result);return result;
}
try{
 if(action==='pair'){
  await page.goto('about:blank');await page.goto((await fs.readFile(`${root}/pairing.url`,'utf8')).trim());
  await page.getByRole('button',{name:'Connect this browser'}).click();await page.getByRole('textbox',{name:'Message Copilot'}).waitFor();
 }else if(action==='reconcile'){
  await page.getByLabel('Resume mode',{exact:true}).selectOption('reconcile_only');
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/resume'));
  await page.getByRole('button',{name:'Resume',exact:true}).click();const observed=await response;
  await save('owner-reconcile',{status:observed.status(),body:await observed.json()});if(!observed.ok())throw Error('Owner reconcile refused');
  await page.waitForTimeout(1500);
 }else if(action==='continue'){
  const before=await collect('before-continue');
  if(before.requests.items.length!==2||!before.requests.items.some(r=>r.request_id===request))throw Error('Original request history changed');
  await page.getByLabel('Resume mode',{exact:true}).selectOption('continue_work');
  await page.getByLabel('Original work to continue',{exact:true}).selectOption(work);
  await fs.writeFile(`${root}/continue.intent.json`,JSON.stringify({work_id:work,request_id:request,at:Date.now()}),{flag:'wx',mode:0o600});
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/resume'));
  await page.getByRole('button',{name:'Resume',exact:true}).click();const observed=await response;
  await save('owner-continue',{status:observed.status(),body:await observed.json()});if(!observed.ok())throw Error('Original Continue refused; do not retry an uncertain command');
  const start=Date.now();let record;
  while(Date.now()-start<300000){
   const state=await collect('progress');record=state.requests.items.find(r=>r.request_id===request);
   if(state.requests.items.length!==2)throw Error('A replacement Queue was created');
   if(record?.receipt.state==='completed')break;
   if(['failed','stopped','outcome_uncertain'].includes(record?.receipt.state))throw Error(`Continuation ${record.receipt.state}`);
   await page.waitForTimeout(1000);
  }
  await save('continue-result',{record,elapsed_ms:Date.now()-start});
  if(record?.receipt.state!=='completed')throw Error('Original continuation did not complete within allocation');
 }else if(action==='catalogue'){
  await page.getByRole('button',{name:'Assets',exact:true}).click();
  await page.locator('.review-catalogue button').filter({hasText:'M11 playable F01 Unity scene'}).first().click();
 }else if(action!=='collect')throw Error(action);
 await collect(action);await page.screenshot({path:`${root}/browser-${action}.png`});
 console.log((await page.locator('body').innerText()).slice(-8000));
}catch(error){await page.screenshot({path:`${root}/${action}-failure.png`});console.error(String(error));console.error((await page.locator('body').innerText()).slice(-6000));process.exitCode=1;}
finally{await browser.close();}
