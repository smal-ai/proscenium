// Actual Full-HD browser. Scripted owner inputs, real SDK/native and app effects.
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import {setTimeout as delay} from 'node:timers/promises';
const root=process.argv[2];
const info=JSON.parse(await fs.readFile(`${root}/processes.json`,'utf8'));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${info.cdp}`);
const context=browser.contexts()[0],page=context.pages()[0];
await page.setViewportSize({width:1920,height:1080});
page.setDefaultTimeout(12000);
async function poll(fn,seconds){const end=Date.now()+seconds*1000;while(Date.now()<end){const r=await fn();if(r)return r;await delay(200);}throw new Error('Bounded observation timed out. Inspect original identities, never resubmit.');}
async function get(path){return page.evaluate(async p=>{const r=await fetch(p,{cache:'no-store'});if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json()},path);}
async function active(){return (await get('/api/v1/projects')).items.find(p=>p.state!=='inactive');}
async function switchTo(project){
 if((await active()).project_id===project.project_id)return;
 await page.locator('.project-trigger').click();
 await page.getByLabel('Saved project').selectOption(project.project_id);
 await page.getByRole('button',{name:'Activate selected project'}).click();
 await poll(async()=>{const p=await active();return p?.project_id===project.project_id},12);
}
async function snapshot(name){
 const p=await active(),base=`/api/v1/projects/${p.project_id}`;
 const value={projects:await get('/api/v1/projects'),snapshot:await get(`${base}/snapshot`),requests:await get(`${base}/requests`),jobs:await get(`${base}/jobs`),viewport:await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})),browser:browser.version(),text:await page.locator('body').innerText()};
 await fs.writeFile(`${root}/${name}.json`,JSON.stringify(value,null,2));
 await page.screenshot({path:`${root}/${name}.png`,fullPage:false});
 return value;
}
async function create(label){
 const existing=(await get('/api/v1/projects')).items.find(p=>p.path===`${root}/project-${label.toLowerCase()}`);
 if(existing){
  if((await get(`/api/v1/projects/${existing.project_id}/requests`)).items.length||existing.native_session_id)throw new Error('Cannot recreate a project with native/request effects');
  await switchTo(existing);
  await fs.writeFile(`${existing.path}/brief/title.md`,`# R1 project ${label}\n\nSentinel: M02-followup-${label}-20261114\n`);
  return existing;
 }
 await page.locator('.project-trigger').click();
 await page.getByLabel('Workstation folder path').fill(`${root}/project-${label.toLowerCase()}`);
 await page.getByLabel('New project name').fill(`M02 follow-up ${label}`);
 await page.getByRole('button',{name:'Create project',exact:true}).click();
 await page.getByRole('button',{name:'Activate selected project'}).click();
 await poll(async()=>{const p=await active();return p?.name===`M02 follow-up ${label}`&&p},12);
 // Activation remounts this popover. Do not race its detached Close button.
 const p=await active();
 await fs.writeFile(`${p.path}/brief/title.md`,`# R1 project ${label}\n\nSentinel: M02-followup-${label}-20261114\n`);
 return p;
}
async function send(label){
 const p=await active(),base=`/api/v1/projects/${p.project_id}`;
 if((await get(`${base}/requests`)).items.length)throw new Error('Original request already exists');
 if((await fs.readdir(root)).filter(n=>n.startsWith('cloud-')).length>=2)throw new Error('Two-root allocation exhausted');
 await fs.writeFile(`${root}/cloud-${label}.claim`,'prepared',{flag:'wx',mode:0o600});
 await page.getByRole('textbox',{name:'Message Copilot'}).fill('SCRIPTED M02 FOLLOW-UP TEST USER. Read only brief/title.md in this active project. Reply with its title and sentinel. Do not edit files, delegate, use providers, or inspect other projects.');
 await page.getByRole('button',{name:'Queue message',exact:true}).click();
 await poll(async()=>{const s=await get(`${base}/snapshot`),rs=await get(`${base}/requests`);return !s.active_work&&rs.items.length===1&&rs.items[0].receipt.state==='completed'},120);
 return snapshot(`r1-${label.toLowerCase()}`);
}
try{
 await page.goto((await fs.readFile(`${root}/pairing.url`,'utf8')).trim());
 await page.getByRole('button',{name:'Connect this browser'}).click();
 let a;
 if(info.r2_only){
  a=await active();
 }else if(info.reattach_settled_a_without_resending){
  a=(await get('/api/v1/projects')).items.find(p=>p.path===`${root}/project-a`);
  await switchTo(a);
  await fs.writeFile(`${root}/observe-reattach.request`,a.project_id,{flag:'wx'});
  await page.getByLabel('Resume mode').selectOption('reconcile_only');
  await page.getByRole('button',{name:'Resume',exact:true}).click();
  await poll(async()=>{try{return JSON.parse(await fs.readFile(`${root}/reattach-observed.json`,'utf8'))}catch{return false}},40);
  await snapshot('r1-a-quiet-reattach');
 }else{a=await create('A');await send('A');}
 let b;
 if(info.r2_only){
  b=await active();await snapshot('r2-only-initial');
 }else{
  b=await create('B');await delay(400);await send('B');
  await page.reload();await page.getByRole('textbox',{name:'Message Copilot'}).waitFor();
  await snapshot('r1-quiet-reopen');
 }
 await fs.writeFile(`${root}/planned.request`,'one scripted valid planned preview',{flag:'wx'});
 const plan=await poll(async()=>{try{return JSON.parse(await fs.readFile(`${root}/planned.json`,'utf8'))}catch{return false}},20);
 if(plan.status!=='completed')throw new Error(`Planned fixture failed: ${plan.code??plan.message}`);
 await page.getByRole('button',{name:'Refresh project metadata'}).click();
 await poll(async()=>{const js=await get(`/api/v1/projects/${b.project_id}/jobs`);return js.items.length===1&&js.items[0].state==='planned'},10);
 await page.locator('details.jobs > summary').click();
 await snapshot('r2-planned-closed');
 const http=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/cancel'));
 await page.getByRole('button',{name:'Request job cancellation',exact:true}).click();
 const response=await http;
 const cancel={status:response.status(),receipt:await response.json()};
 await fs.writeFile(`${root}/owner-cancel.json`,JSON.stringify(cancel,null,2));
 if(![200,202].includes(cancel.status))throw new Error(`Owner cancellation HTTP ${cancel.status}`);
 await poll(async()=>{const js=await get(`/api/v1/projects/${b.project_id}/jobs`);return js.items.length===1&&js.items[0].state==='cancelled'},15);
 await poll(async()=>{try{return JSON.parse(await fs.readFile(`${root}/cancel-observed.json`,'utf8'))}catch{return false}},5);
 await snapshot('r2-cancelled-closed');
 await fs.writeFile(`${root}/browser-result.json`,JSON.stringify({result:info.r2_only?'R2_completed_R1_incomplete':'completed',a,b,resume_clicked_for_b:false,settled_a_reconciled_without_send:!info.r2_only&&!!info.reattach_settled_a_without_resending,cancellation_dispatch_from_fixture:false},null,2));
 console.log(info.r2_only?'R2 completed through actual owner UI and background watcher. R1 remains incomplete.':'R1 and R2 browser stages completed without B Resume or manual cancellation dispatch.');
}catch(error){
 await fs.writeFile(`${root}/browser-result.json`,JSON.stringify({result:'failed',error:String(error)},null,2));
 await page.screenshot({path:`${root}/failure.png`,fullPage:false}).catch(()=>{});
 throw error;
}finally{await browser.close();}
