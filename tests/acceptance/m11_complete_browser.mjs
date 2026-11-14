// Same compiled owner browser, one original-work Continue. Actions never requeue work.
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {DatabaseSync} from 'node:sqlite';
const root=process.argv[2],action=process.argv[3];
const info=JSON.parse(await fs.readFile(`${root}/processes.json`,'utf8'));
const {chromium}=await import(pathToFileURL(`${info.dependencies}/playwright/index.mjs`));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${info.cdp}`);
const context=browser.contexts()[0],page=context.pages()[0];
await page.setViewportSize({width:1920,height:1080});page.setDefaultTimeout(15000);
const project='project-6efca2f5-8ccd-49bb-bea1-f552ef065224',work='work-4a7e644e78514109b4a5232316e3aacc',request='1a914aa8-2bbc-461a-a87d-1bb7431de02c';
const base=`/api/v1/projects/${project}`;
const database=new DatabaseSync(`${root}/../project/.proscenium/state.sqlite3`,{readOnly:true});
const row=(ns,id)=>{const r=database.prepare('SELECT body FROM records WHERE namespace=? AND id=?').get(ns,id);return r?JSON.parse(r.body):null;};
const rows=ns=>database.prepare('SELECT body FROM records WHERE namespace=?').all(ns).map(r=>JSON.parse(r.body));
const save=(name,value)=>fs.writeFile(`${root}/${name}.json`,JSON.stringify(value,null,2));
const get=path=>page.evaluate(async p=>{const r=await fetch(p,{cache:'no-store'});if(!r.ok)throw Error(`${p}: ${r.status}`);return r.json();},path);
async function collect(label){
 const result={projects:await get('/api/v1/projects'),snapshot:await get(`${base}/snapshot`),requests:await get(`${base}/requests`),jobs:await get(`${base}/jobs`),items:await get(`${base}/items`),versions:{},viewport:await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})),browser:browser.version()};
 for(const item of result.items.items)result.versions[item.item_id]=await get(`${base}/items/${item.item_id}/versions`);
 result.visible=await page.locator('body').innerText();await save(`public-${label}`,result);return result;
}
async function observe(){
 const sendWait=Date.now()+15000;
 while(true){try{await fs.access(`${root}/sdk-send-claimed.json`);break;}catch(error){if(error.code!=='ENOENT'||Date.now()>sendWait)throw error;await page.waitForTimeout(100);}}
 const claim=JSON.parse(await fs.readFile(`${root}/sdk-send-claimed.json`,'utf8'));
 const deadline=claim.at*1000+237000;
 const operation=JSON.parse(await fs.readFile(`${root}/owner-continue.json`,'utf8')).command.operation_id;
 while(Date.now()<deadline){
  const state=await collect('progress');
  const pending=rows('decisions').filter(d=>d.work_id===work&&d.state==='pending');
  if(pending.length){await save('unexpected-decision',pending);console.log(JSON.stringify({event:'COORDINATOR_DECISION_REQUIRED',decisions:pending}));throw Error(`Coordinator decision required, callback left alive: ${JSON.stringify(pending)}`);}
  const current=row('work',work),completion=row('copilot.completion',request),idle=row('copilot.idle','0cbee1a2-b5ed-4ab8-962b-ccf1d9f8a454');
  if(current.state==='completed'&&completion?.candidate&&completion.delivery_id===operation&&idle?.idle&&idle.delivery_id===operation){
   await save('terminal-readback',{work:current,completion,idle,requests:state.requests,elapsed_ms:Date.now()-claim.at*1000});return;
  }
  if(['failed','outcome_uncertain','stopped','awaiting_resume'].includes(current.state))throw Error(`Original work ${current.state}`);
  await page.waitForTimeout(500);
 }
 throw Error('Original work did not reach actual terminal completion within allocation');
}
try{
 if(action==='bootstrap'){
  await page.goto((await fs.readFile(`${root}/pairing.url`,'utf8')).trim());
  await page.getByRole('button',{name:'Connect this browser'}).click();
  await page.locator('button.project-trigger').waitFor();await page.locator('button.project-trigger').click();
  await page.getByLabel('Saved project',{exact:true}).selectOption(project);
  const activate=page.getByRole('button',{name:'Activate selected project',exact:true});
  const activation={selector:'button.project-trigger',selected_project:project,already_active:!(await activate.isEnabled())};
  if(await activate.isEnabled()){
   const response=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`${base}/activate`);
   await activate.click();const observed=await response;activation.status=observed.status();activation.command=observed.request().postDataJSON();if(!observed.ok())throw Error('Project activation refused');
  }
  await save('project-activation',activation);await page.getByRole('button',{name:'Close projects',exact:true}).click();
  await page.getByRole('textbox',{name:'Message Copilot'}).waitFor();
 }else if(action==='preflight'){
  const state=await collect('preflight');
  if(state.requests.items.filter(r=>r.submission.type==='queue').length!==2)throw Error('Original Queue history changed');
  if(!state.requests.items.some(r=>r.request_id===request))throw Error('Original request missing');
  if(row('work',work).state!=='stopped'||row('work',work).state_revision!==10)throw Error('Original Stop not confirmed at revision10');
  await page.getByLabel('Resume mode',{exact:true}).selectOption('continue_work');
  await page.getByLabel('Original work to continue',{exact:true}).selectOption(work);
  if(!await page.getByRole('button',{name:'Resume',exact:true}).isEnabled())throw Error('Continue button disabled');
  const processes=await (await browser.newBrowserCDPSession()).send('SystemInfo.getProcessInfo');await save('browser-processes',processes);
  await fs.writeFile(`${root}/ui-preflight.json`,JSON.stringify({status:'passed',work,request,viewport:state.viewport,stop_revision:10,queue_count:2}),{flag:'wx',mode:0o600});
 }else if(action==='continue'){
  await fs.readFile(`${root}/ui-preflight.json`);await collect('before-continue');
  await page.getByLabel('Resume mode',{exact:true}).selectOption('continue_work');
  await page.getByLabel('Original work to continue',{exact:true}).selectOption(work);
  await fs.writeFile(`${root}/continue.intent.json`,JSON.stringify({work_id:work,request_id:request}),{flag:'wx',mode:0o600});
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`${base}/resume`);
  await page.getByRole('button',{name:'Resume',exact:true}).click();const observed=await response;
  await save('owner-continue',{status:observed.status(),command:observed.request().postDataJSON(),receipt:await observed.json()});
  if(!observed.ok())throw Error('Continue refused. No retry');
  // Admission is not completion. Do not exit on an intermediate message/tool batch.
  await observe();
 }else if(action==='observe'){
  await observe();
 }else if(action==='catalogue'){
  await page.getByRole('button',{name:'Assets',exact:true}).click();
  await page.getByRole('button').filter({has:page.locator('small',{hasText:/^m11-playable-v1$/})}).click();
  const state=await collect('catalogue');
  const item=state.items.items.find(i=>i.item_id==='m11-playable');
  const version=state.versions['m11-playable'].items.find(v=>v.version_id==='m11-playable-v1');
  if(item?.current_version_id!=='m11-playable-v1'||version?.kind!=='unity_result'||version?.unity?.validation?.status!=='passed')throw Error('Unity catalogue metadata/current version mismatch');
  for(const id of ['m11-playmode-attestation','m11-playmode-xml','m11-playmode-metrics'])await save(`artifact-${id}`,await get(`${base}/artifacts/${id}`));
 }else if(action!=='collect')throw Error(action);
 await collect(action);await page.screenshot({path:`${root}/browser-${action}.png`});
 console.log(JSON.stringify({action,status:'passed',work:row('work',work).state}));
}catch(error){await save(`${action}-error`,{error:String(error),work:row('work',work)});await page.screenshot({path:`${root}/${action}-failure.png`}).catch(()=>{});console.error(String(error));console.error((await page.locator('body').innerText()).slice(-4000));process.exitCode=1;}
finally{database.close();await browser.close();}
