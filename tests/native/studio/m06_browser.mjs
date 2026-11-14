// Explicitly allocated real owner-browser driver. No mocked routes or extra model sends.
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import { createHash } from 'node:crypto';
const root=process.argv[2], action=process.argv[3];
const info=JSON.parse(await fs.readFile(`${root}/processes.json`,'utf8'));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${info.cdp}`);
const page=browser.contexts()[0].pages()[0];
await page.setViewportSize({width:1920,height:1080});
page.setDefaultTimeout(15000);
const get=async path=>page.evaluate(async path=>{const response=await fetch(path);if(!response.ok)throw new Error(`${path}: ${response.status}`);return response.json();},path);
const project=async()=>(await get('/api/v1/projects')).items[0];
const prefix=async()=>`/api/v1/projects/${(await project()).project_id}`;
const save=async(name,value)=>fs.writeFile(`${root}/${name}.json`,JSON.stringify(value,null,2));
async function collect(label){
  const p=await prefix();
  const result={project:await project(),snapshot:await get(`${p}/snapshot`),requests:await get(`${p}/requests`),items:await get(`${p}/items`),jobs:await get(`${p}/jobs`),selections:await get(`${p}/selections`),annotations:await get(`${p}/annotations`)};
  result.versions={};result.plans={};result.contexts={};result.artifacts={};
  for(const item of result.items.items){result.versions[item.item_id]=await get(`${p}/items/${item.item_id}/versions`);if(item.kind==='storyboard')for(const v of result.versions[item.item_id].items)result.plans[v.version_id]=await get(`${p}/versions/${v.version_id}/shot-plan`);}
  for(const r of result.requests.items)if(r.submission.context_id)result.contexts[r.submission.context_id]=await get(`${p}/contexts/${r.submission.context_id}`);
  const ids=new Set(Object.values(result.plans).flatMap(plan=>plan.shots.flatMap(s=>s.frames.flatMap(f=>f.artifact_id?[f.artifact_id]:[]))));
  for(const a of result.annotations.items){ids.add(a.target.artifact_id);if(a.markup_artifact_id)ids.add(a.markup_artifact_id);for(const m of a.markups)if(m.mask_artifact_id)ids.add(m.mask_artifact_id);}
  for(const id of ids)result.artifacts[id]=await get(`${p}/artifacts/${id}`);
  result.view=await page.evaluate(id=>({context:JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)??'null'),viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio}}),result.project.project_id);
  result.browser=browser.version();
  await save(`public-${label}`,result);return result;
}
async function send(file){
  const p=await prefix();const prior=(await get(`${p}/requests`)).items;
  const text=await fs.readFile(`${root}/${file}`,'utf8');
  if(prior.length>=2||prior.some(r=>r.submission.text===text))throw new Error('Two-root allocation or exact prompt already admitted. Inspect original records.');
  const hash=createHash('sha256').update(text).digest('hex');
  await fs.writeFile(`${root}/send-${hash}.json`,JSON.stringify({state:'prepared',prompt_sha256:hash}),{flag:'wx',mode:0o600});
  const start=Date.now();
  await page.getByRole('textbox',{name:'Message Copilot',exact:true}).fill(text);
  await page.getByRole('button',{name:'Queue message',exact:true}).click();
  let observed;
  while(Date.now()-start<175000){
    const records=(await get(`${p}/requests`)).items;observed=records.find(r=>r.submission.text===text);
    if(observed&&['completed','failed','stopped','outcome_uncertain','awaiting_resume'].includes(observed.receipt.state))break;
    const snapshot=await get(`${p}/snapshot`);
    if(snapshot.pending_decisions.length)throw new Error('Unexpected owner decision. No approval or additional request is permitted.');
    await page.waitForTimeout(500);
  }
  await save(`request-${prior.length+1}`,{observed,elapsed_ms:Date.now()-start});
  if(!observed||observed.receipt.state!=='completed'){
    const stop=page.getByRole('button',{name:'Stop',exact:true});if(await stop.isEnabled())await stop.click();
    throw new Error(`Root did not complete inside allocation: ${observed?.receipt.state}`);
  }
  console.log(JSON.stringify({request_id:observed.request_id,state:observed.receipt.state,elapsed_ms:Date.now()-start}));
}
async function revision(){
  const p=await prefix();const prior=(await get(`${p}/requests`)).items;
  if(prior.length!==1||prior[0].receipt.state!=='completed')throw new Error('Exactly one completed original root is required. No replay.');
  await page.locator('.sb-frame').nth(1).click();
  const instructions=(await fs.readFile(`${root}/second.txt`,'utf8')).trim();
  await page.getByLabel('Planning edit instructions',{exact:true}).fill(instructions);
  const hash=createHash('sha256').update(instructions).digest('hex');
  await fs.writeFile(`${root}/send-${hash}.json`,JSON.stringify({state:'prepared',prompt_sha256:hash}),{flag:'wx',mode:0o600});
  const start=Date.now();
  await page.getByRole('button',{name:'Queue plan edit',exact:true}).click();
  let observed;
  while(Date.now()-start<175000){
    observed=(await get(`${p}/requests`)).items.find(r=>r.request_id!==prior[0].request_id);
    if(observed&&['completed','failed','stopped','outcome_uncertain','awaiting_resume'].includes(observed.receipt.state))break;
    if((await get(`${p}/snapshot`)).pending_decisions.length)throw new Error('Unexpected owner decision; no extra inference authorised.');
    await page.waitForTimeout(500);
  }
  await save('request-2',{observed,elapsed_ms:Date.now()-start});
  if(!observed||observed.receipt.state!=='completed'){
    const stop=page.getByRole('button',{name:'Stop',exact:true});if(await stop.isEnabled())await stop.click();
    throw new Error(`Revision root did not complete inside allocation: ${observed?.receipt.state}`);
  }
  await collect('after-revision');
  console.log(JSON.stringify({request_id:observed.request_id,state:observed.receipt.state,elapsed_ms:Date.now()-start}));
}
try{
  if(action==='setup'){
    let log='';for(let i=0;i<80;i++){log=await fs.readFile(`${root}/server.private.log`,'utf8');if(log.includes('#pair='))break;await page.waitForTimeout(250);}
    const url=log.match(/http:\/\/127\.0\.0\.1:\d+\/#pair=[^\s]+/)[0];
    await page.goto(url);await page.getByRole('button',{name:'Connect this browser'}).click();
    await page.getByRole('button',{name:'Choose a project'}).click();
    await page.getByLabel('Workstation folder path').fill(`${root}/project`);
    await page.getByLabel('New project name').fill('M06 paper kite silent planning acceptance');
    await page.getByRole('button',{name:'Create project',exact:true}).click();
    await page.getByRole('button',{name:'Activate selected project'}).click();
  }
  if(action==='setup'||action==='upload'){
    const uploaded=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().includes('/uploads/'));
    await page.getByLabel('Attach images',{exact:true}).setInputFiles(`${root}/owner-reference.png`);
    const response=await uploaded; if(!response.ok())throw new Error(`Upload ${response.status()}`);
    await save('reference-upload',await response.json());
    await collect('setup');
  }else if(action==='send'){await send(process.argv[4]);await collect(process.argv[5]??'after-send');}
  else if(action==='collect'){await collect(process.argv[4]??'state');}
  else if(action==='eval'){console.log(JSON.stringify(await page.evaluate(await fs.readFile(`${root}/eval.js`,'utf8'))));}
  else if(action==='ui'){await eval(`(async()=>{${await fs.readFile(`${root}/ui.mjs`,'utf8')}\n})()`);}
  else if(action==='review'){await (await import('./m06_review.mjs')).review({page,root,save,collect,get});}
  else if(action==='revision'){await revision();}
  else if(action==='final'){await (await import('./m06_final.mjs')).finalReview({page,root,save,collect,get});}
  else if(action==='reload'){await page.reload();await page.getByRole('textbox',{name:'Message Copilot'}).waitFor();await collect(process.argv[4]??'reloaded');}
  else throw new Error('Unknown action');
  await page.screenshot({path:`${root}/${process.argv[5]??action}.png`,fullPage:false});
  console.log((await page.locator('body').innerText()).slice(-9000));
}finally{await browser.close();}
