// One connection, one context, one page. No retry, Close, Resume or reattach.
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import {setTimeout as delay} from 'node:timers/promises';
const root=process.argv[2];
const info=JSON.parse(await fs.readFile(`${root}/processes.json`,'utf8'));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${info.cdp}`);
const context=browser.contexts()[0],page=context.pages()[0];
await page.setViewportSize({width:1920,height:1080});
page.setDefaultTimeout(12000);
const trace=[];
page.on('request',r=>{if(r.method()==='POST')trace.push({method:r.method(),path:new URL(r.url()).pathname,at:new Date().toISOString()});});
async function poll(fn,seconds){const end=Date.now()+seconds*1000;while(Date.now()<end){const v=await fn();if(v)return v;await delay(200);}throw new Error('Bounded read timed out. Original effects retained; no retry.');}
async function get(path){return page.evaluate(async p=>{const r=await fetch(p,{cache:'no-store'});if(!r.ok)throw new Error(`GET ${p}: ${r.status}`);return r.json()},path);}
async function capture(label,p){
 const base=`/api/v1/projects/${p.project_id}`;
 const data={projects:await get('/api/v1/projects'),snapshot:await get(`${base}/snapshot`),requests:await get(`${base}/requests`),viewport:await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})),browser:browser.version(),text:await page.locator('body').innerText()};
 await fs.writeFile(`${root}/${label}.json`,JSON.stringify(data,null,2));
 await page.screenshot({path:`${root}/${label}.png`,fullPage:false});
 return data;
}
async function activate(p){
 await page.locator('.project-trigger').click();
 await page.getByLabel('Saved project',{exact:true}).selectOption(p.project_id);
 await page.getByRole('button',{name:'Activate selected project',exact:true}).click();
 await poll(async()=>{
  const projects=await get('/api/v1/projects');
  const record=projects.items.find(x=>x.project_id===p.project_id);
  return record&&record.state!=='inactive'&&(await page.locator('.project-trigger').innerText()).includes(p.name);
 },12);
 await page.locator('.project-popover').waitFor({state:'detached',timeout:12000});
}
async function send(label,p){
 const base=`/api/v1/projects/${p.project_id}`;
 if((await get(`${base}/requests`)).items.length)throw new Error('Request already exists; refusing another Send');
 await fs.writeFile(`${root}/send-${label}.claim`,'prepared',{flag:'wx',mode:0o600});
 await page.getByRole('textbox',{name:'Message Copilot',exact:true}).fill('SCRIPTED READ-ONLY TEST USER. Read only brief/title.md in this active project. Reply with its title and sentinel. Do not edit, delegate, use providers, or inspect other projects.');
 await page.getByRole('button',{name:'Queue message',exact:true}).click();
 await poll(async()=>{
  const s=await get(`${base}/snapshot`),r=await get(`${base}/requests`);
  return !s.active_work&&r.items.length===1&&r.items[0].receipt.state==='completed'&&s.project.native_session_id;
 },120);
 await page.getByRole('button',{name:'Refresh project metadata',exact:true}).click();
 await poll(async()=> (await page.locator('body').innerText()).includes(`M02-CLEAN-${label}-20261114`),10);
 return capture(label,p);
}
try{
 const log=await fs.readFile(`${root}/server.private.log`,'utf8');
 const pairing=log.match(/http:\/\/127\.0\.0\.1:\d+\/#pair=[^\s]+/);
 if(!pairing)throw new Error('Private pairing link unavailable');
 await page.goto(pairing[0]);
 await page.getByRole('button',{name:'Connect this browser',exact:true}).click();
 await page.locator('.project-trigger').waitFor();
 const projects=[];
 for(const label of ['A','B']){
  const body={schema_version:'1.0',operation_id:crypto.randomUUID(),path:`${root}/${label.toLowerCase()}`,create:true,name:`Clean R1 ${label}`};
  const result=await page.evaluate(async body=>{
   const owner=JSON.parse(localStorage.getItem('proscenium.owner.session'));
   const r=await fetch('/api/v1/projects',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':body.operation_id,'X-CSRF-Token':owner.csrf_token},body:JSON.stringify(body)});
   return {status:r.status,record:await r.json()};
  },body);
  if(result.status!==201)throw new Error(`Fixture project creation returned ${result.status}`);
  projects.push(result.record);
  await fs.writeFile(`${body.path}/brief/title.md`,`# Clean R1 ${label}\n\nSentinel: M02-CLEAN-${label}-20261114\n`);
 }
 await fs.writeFile(`${root}/setup.json`,JSON.stringify({fixture:'Authenticated project API and explicit local brief files, not model authoring',projects},null,2));
 await page.reload();await page.locator('.project-trigger').waitFor();
 await activate(projects[0]);const a=await send('A',projects[0]);
 await activate(projects[1]);
 const shutdown=await poll(async()=>{
  try{
   const text=await fs.readFile(`${root}/m/copilot-runtime/home/session-state/${a.snapshot.project.native_session_id}/events.jsonl`,'utf8');
   const event=text.split('\n').filter(Boolean).map(l=>JSON.parse(l)).find(e=>e.type==='session.shutdown');
   return event&&{id:event.id,type:event.type,timestamp:event.timestamp};
  }catch{return false;}
 },10);
 await delay(300);const b=await send('B',projects[1]);
 if(a.snapshot.project.native_session_id===b.snapshot.project.native_session_id)throw new Error('Session identity collision');
 await page.reload();await page.locator('.project-trigger').waitFor();
 await capture('quiet-reopen',projects[1]);
 await fs.writeFile(`${root}/browser-result.json`,JSON.stringify({result:'PASS',shutdown,connections:1,contexts:1,pages:1,resume_requests:trace.filter(r=>r.path.endsWith('/resume')).length,trace},null,2));
 console.log('Both original native requests completed through the actual Send controls.');
}catch(error){
 await fs.writeFile(`${root}/browser-result.json`,JSON.stringify({result:'FAIL',error:String(error).replace(/#pair=[^\s"]+/g,'#pair=[REDACTED]'),trace},null,2));
 await page.screenshot({path:`${root}/failure.png`,fullPage:false}).catch(()=>{});
 throw error;
}finally{await browser.close();}
