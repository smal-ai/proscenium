// Real compiled UI and original disk-backed runtime. No interception, new Queue or synthetic reply.
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
const root=process.argv[2], action=process.argv[3];
const info=JSON.parse(await fs.readFile(`${root}/host.private.json`,'utf8'));
const chrome=JSON.parse(await fs.readFile(`${root}/browser.private.json`,'utf8'));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${chrome.cdp}`);
const context=browser.contexts()[0];
const page=context.pages()[0]??await context.newPage();
await page.setViewportSize({width:1920,height:1080}); // Before navigation, pairing or any interaction.
page.setDefaultTimeout(20000);
const prefix=`/api/v1/projects/${info.project_id}`;
const A='work-6361ef13fbeb4f4884ff382bf96d8815';
const B='work-6c566aaa39084a139da0b42fcff0b555';
const interrupted='work-ea4ab25e133747dba8e1f74dad160d2a';
const get=async path=>page.evaluate(async path=>{const r=await fetch(path);if(!r.ok)throw new Error(`Read ${r.status}`);return r.json();},path);
const snapshot=()=>get(`${prefix}/snapshot`);
const requests=async()=>(await get(`${prefix}/requests?limit=100`)).items;
const status=async()=>JSON.parse(await fs.readFile(`${root}/status.json`,'utf8'));
const wait=async(probe,limit=45000)=>{const end=Date.now()+limit;while(Date.now()<end){const result=await probe();if(result)return result;await new Promise(r=>setTimeout(r,150));}throw new Error('Bounded observation timed out');};
const screenshot=async name=>page.screenshot({path:`${root}/${name}.png`,fullPage:false});
let result={action,at:new Date().toISOString()};
try {
 if(action==='setup'){
  await page.goto(info.pair_url);
  await page.getByRole('button',{name:'Connect this browser',exact:true}).click();
  await page.getByRole('textbox',{name:'Message Copilot',exact:true}).waitFor();
  result.browser=browser.version();
 }else if(action==='selector'){
  result.snapshot=await snapshot();result.requests=await requests();
  await page.getByLabel('Resume mode',{exact:true}).selectOption('continue_work');
  const selector=page.getByLabel('Original work to continue',{exact:true});
  result.options=await selector.locator('option').evaluateAll(nodes=>nodes.map(n=>({value:n.value,text:n.textContent})));
  await selector.selectOption(A);
  result.selected=await selector.inputValue();
  await selector.scrollIntoViewIfNeeded();
  result.known_original=(await status()).work.find(w=>w.work_id===A);
  if(result.selected!==A)throw new Error('Selector did not bind original A');
 }else if(action==='stop-b'){
  if((await fs.readdir(root)).some(name=>name.startsWith('ui-stop-b')))throw new Error('Inspect the exact previous Stop result, never allocate a replacement');
  result.before=await snapshot();
  if(result.before.active_work?.work_id!==B)throw new Error('Refuse Stop of any work other than exact original B');
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(`${prefix}/requests`));
  await page.getByRole('button',{name:'Stop',exact:true}).click();
  const r=await response;result.status=r.status();result.receipt=await r.json();result.command=r.request().postDataJSON();
  if(result.command.target_work_id!==B||result.command.type!=='stop')throw new Error('Wrong Stop target/type');
  if(!r.ok())throw new Error(`Stop admission ${result.status}`);
  result.after=await wait(async()=>{const s=await status();const stop=s['copilot.stops'].find(v=>v.work_id===B);return stop&&['confirmed','outcome_uncertain'].includes(stop.phase)&&s;});
 }else if(action==='continue-a'||action==='reconcile-b'){
  const mode=action==='continue-a'?'continue_work':'reconcile_only';
  result.before=await snapshot();result.requests_before=await requests();
  await page.getByLabel('Resume mode',{exact:true}).selectOption(mode);
  if(mode==='continue_work')await page.getByLabel('Original work to continue',{exact:true}).selectOption(A);
  else if(result.before.active_work?.work_id!==interrupted)throw new Error('Wrong interrupted work for Reconcile');
  const intended=mode==='continue_work'?A:interrupted;
  result.target_before=(await status()).work.find(w=>w.work_id===intended);
  await page.getByRole('button',{name:'Resume',exact:true}).scrollIntoViewIfNeeded();
  await screenshot(`before-${action}`);
  // Persist the UI action reservation before dispatch. No replacement operation on script rerun.
  await fs.writeFile(`${root}/${action}-prepared.json`,JSON.stringify({at:new Date().toISOString(),target:intended,revision:result.target_before.state_revision}),{flag:'wx',mode:0o600});
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(`${prefix}/resume`));
  await page.getByRole('button',{name:'Resume',exact:true}).click();
  const r=await response;result.status=r.status();result.receipt=await r.json();result.command=r.request().postDataJSON();
  if(result.command.work_id!==intended||result.command.expected_revision!==result.target_before.state_revision)throw new Error('Incorrect UI target/revision');
  if(result.status!==200&&result.status!==202)throw new Error(`Resume admission ${result.status}`);
  result.settled=await wait(async()=>{const s=await status();const op=s['copilot.resumes'].find(v=>v.body.operation_id===result.command.operation_id);return op&&!['pending','dispatching'].includes(op.state)&&s;},50000);
  const operation=result.settled['copilot.resumes'].find(v=>v.body.operation_id===result.command.operation_id);
  if(mode==='continue_work'&&operation.state==='completed'){
   result.execution=await wait(async()=>{const s=await status();const w=s.work.find(v=>v.work_id===A);return w&&['completed','failed','stopped','awaiting_resume'].includes(w.state)&&s;},180000);
  }
  result.requests_after=await requests();
 }else if(action==='collect'){
  result.snapshot=await snapshot();result.requests=await requests();result.observed=await status();
  result.events=[];let after=0;
  while(true){const p=await get(`${prefix}/events?after=${after}&limit=100`);result.events.push(...p.events);if(p.next_after>=p.last_available)break;after=p.next_after;}
  result.alerts=await page.getByRole('alert').allTextContents();
 }else throw new Error('Unknown action');
 result.viewport=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}));
 await screenshot(action);
 await fs.writeFile(`${root}/ui-${action}.json`,JSON.stringify(result,null,2));
 console.log(JSON.stringify({action,status:result.status,receipt:result.receipt,selected:result.selected,viewport:result.viewport}));
}catch(error){
 result.error=String(error);await fs.writeFile(`${root}/ui-${action}-failed.json`,JSON.stringify(result,null,2));
 await screenshot(`FAILED-${action}`);throw error;
}finally{await browser.close();}
