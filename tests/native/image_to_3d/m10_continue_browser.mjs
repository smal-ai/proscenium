// One continuous Full-HD software browser. No Queue, private repair or synthetic success.
import { chromium, expect } from '@playwright/test';
import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs/promises';

const root=process.argv[2];
const info=JSON.parse(await fs.readFile(`${root}/host.json`,'utf8'));
const before=JSON.parse(await fs.readFile(`${root}/before.json`,'utf8'));
const original=info.original_fixture;
const pid='project-f7d845f4-1910-4099-8578-ce15509690da';
const workId='work-bc19a5da5fcd4aaf9ae552a13d06b20d';
const requestId='927cf238-56c2-454e-a063-014e072f9010';
const sessionId='5971843f-f216-40f8-b8cb-995675f48161';
const prefix=`/api/v1/projects/${pid}`;
const result={scripted_owner_input:true,new_queue_requests:0,sdk_continuation_sent:false,phase:'starting',native_children:0};
const db=new DatabaseSync(`${original}/project/.proscenium/state.sqlite3`,{readOnly:true});
function records(namespace){return db.prepare('SELECT id,body FROM records WHERE namespace=?').all(namespace).map(row=>({id:row.id,...JSON.parse(row.body)}));}
function record(namespace,id){const row=db.prepare('SELECT body FROM records WHERE namespace=? AND id=?').get(namespace,id);return row?JSON.parse(row.body):null;}
async function save(){await fs.writeFile(`${root}/browser-result.private.json`,JSON.stringify(result,null,2));}
function available(){return fs.readFile('/proc/meminfo','utf8').then(text=>Number(text.match(/^MemAvailable:\s+(\d+)/m)[1])*1024);}
const context=await chromium.launchPersistentContext(`${root}/browser-profile`,{
  headless:true,viewport:{width:1920,height:1080},deviceScaleFactor:1,
  args:['--no-sandbox','--disable-dev-shm-usage','--use-angle=swiftshader','--enable-unsafe-swiftshader','--window-size=1920,1080'],
});
const page=context.pages()[0];
page.setDefaultTimeout(20000);
let csrf=null;
page.on('response',async response=>{
  if(new URL(response.url()).pathname==='/api/v1/owner/session'&&response.ok()){
    const body=await response.json();if(body.csrf_token)csrf=body.csrf_token;
  }
});
const get=path=>page.evaluate(async p=>{const r=await fetch(p);if(!r.ok)throw new Error(`${p} ${r.status}`);return r.json();},path);
try{
  result.browser_version=context.browser().version();
  const cdp=await context.browser().newBrowserCDPSession();
  result.browser_processes=(await cdp.send('SystemInfo.getProcessInfo')).processInfo.map(({id,type})=>({pid:id,type}));
  const log=await fs.readFile(`${root}/server.private.log`,'utf8');
  const pairing=log.match(/http:\/\/127\.0\.0\.1:\d+\/#pair=[^\s]+/);
  if(!pairing)throw new Error('No private pairing URL on the one owned host');
  await page.goto(pairing[0]);
  await page.getByRole('button',{name:'Connect this browser'}).click();
  if(await page.getByRole('button',{name:'Choose a project',exact:true}).isVisible()){
    await page.getByRole('button',{name:'Choose a project',exact:true}).click();
    await page.getByLabel('Saved project',{exact:true}).selectOption(pid);
    await page.getByRole('button',{name:'Activate selected project',exact:true}).click();
  }
  await page.getByRole('textbox',{name:'Message Copilot'}).waitFor();
  if(await page.getByRole('button',{name:'Close projects',exact:true}).isVisible())await page.getByRole('button',{name:'Close projects',exact:true}).click();
  await page.getByRole('button',{name:'Assets',exact:true}).click();
  await page.getByRole('button').filter({has:page.locator('small',{hasText:/^m10-asset-v1$/})}).click();
  await page.getByRole('button',{name:'1 3D Inspection',exact:true}).click();
  await expect(page.getByRole('button',{name:'Render this view',exact:true})).toBeEnabled();
  const canvas=page.locator('.review-proxy canvas');
  result.renderer=await canvas.evaluate(element=>{const gl=element.getContext('webgl2');const ext=gl.getExtension('WEBGL_debug_renderer_info');return ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):'unknown';});
  if(!result.renderer.includes('SwiftShader'))throw new Error('GPU WebGL is not allocated');
  const box=await canvas.boundingBox();
  await page.mouse.click(box.x+box.width*.5,box.y+box.height*.5);
  await page.getByRole('button',{name:'Outliner',exact:true}).click();
  const object=page.getByRole('button').filter({has:page.locator('small',{hasText:/^e04-instance-001$/})});
  await expect(object).toHaveAttribute('aria-pressed','true');
  result.viewport=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}));
  await page.screenshot({path:`${root}/picked-object.private.png`});
  const rejected=JSON.parse(await fs.readFile(`${original}/context-rejection.json`,'utf8'));
  if(rejected.context.context_id===before.original_context.context_id)throw new Error('Never rewrite the admitted immutable context');
  if(!csrf)throw new Error('No CSRF from the actual owner-browser pairing');
  // Revalidate the exact originally rejected, actual-UI capture. This is not a new Queue
  // or a claim that its old camera is the new live pose. It preserves the immutable request context.
  result.context_capture=await page.evaluate(async({body,csrf,path})=>{
    const response=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Idempotency-Key':body.context_id,'X-CSRF-Token':csrf},body:JSON.stringify(body)});
    return {status:response.status,body:await response.json()};
  },{body:rejected.context,csrf,path:`${prefix}/contexts`});
  if(![200,201].includes(result.context_capture.status))throw new Error('Actual selected-object context is still rejected');
  result.phase='picked_context_validated';await save();

  await page.getByLabel('Resume mode').selectOption('reconcile_only');
  const metaResponse=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`${prefix}/resume`);
  await page.getByRole('button',{name:'Resume',exact:true}).click();
  const meta=await metaResponse;
  result.metadata_resume={status:meta.status(),command:meta.request().postDataJSON(),receipt:await meta.json()};
  const operationId=result.metadata_resume.command.operation_id;
  result.phase='metadata_reconciling';await save();
  const until=Date.now()+65000;
  while(Date.now()<until){
    const operation=record('copilot.resumes',operationId);
    if(operation&&['completed','outcome_uncertain','failed'].includes(operation.state)){result.metadata_operation=operation;break;}
    await page.waitForTimeout(1000);
  }
  result.after_metadata={request:record('requests',requestId),work:record('work',workId),association:record('copilot.session','native'),
    control:record('copilot.control','gate'),permissions:records('copilot.permissions'),invocations:records('invocations'),
    continuation_sends:records('copilot.continuation_sends'),resumes:records('copilot.resumes')};
  if(result.after_metadata.association?.session_id!==sessionId)throw new Error('Original native association changed');
  if(result.metadata_operation?.state!=='completed'){
    result.phase='blocked_before_send';result.blocker='The one metadata-only reconciliation did not complete. Preserve legacy permission/invocation evidence.';
    await save();
  }else{
    const live=result.after_metadata.invocations.filter(row=>row.work_id===workId&&!['completed','failed','cancelled'].includes(row.state));
    const unsettled=result.after_metadata.permissions.filter(row=>row.session_id===sessionId&&row.phase&&row.phase!=='completed');
    if(live.length||unsettled.length)throw new Error('Legacy native work/permission stages are not proven settled');
    if(await available()<5*1024**3)throw new Error('Insufficient protected host headroom before continuation');
    await page.getByLabel('Resume mode').selectOption('continue_work');
    await page.getByLabel('Original work to continue').selectOption(workId);
    const continuationResponse=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`${prefix}/resume`);
    await page.getByRole('button',{name:'Resume',exact:true}).click();
    const continued=await continuationResponse;
    result.continue_owner_command={status:continued.status(),command:continued.request().postDataJSON(),receipt:await continued.json()};
    result.phase='original_continuation_requested';await save();
    // Reserve room for the real Stop barrier inside the 180-second root allocation.
    const deadline=Date.now()+165000;
    while(Date.now()<deadline){
      const sends=records('copilot.continuation_sends');
      result.sdk_continuation_sent=sends.length>0;
      if(sends.length>1)throw new Error('Only one continuation send was allocated');
      const current=record('work',workId);
      const children=records('invocations').filter(row=>row.kind==='child');
      if(children.length)throw new Error('Native children are not allocated');
      if(await available()<5*1024**3){result.blocker='Protected host headroom threatened';break;}
      if(current&&['completed','failed','awaiting_resume','outcome_uncertain','stopped'].includes(current.state)&&sends.length){result.final_work=current;break;}
      await page.waitForTimeout(1000);
    }
    result.continuation_sends=records('copilot.continuation_sends');
    result.final_work=record('work',workId);result.jobs=records('jobs');result.versions=records('versions');
    if(result.final_work?.state==='running'){
      result.blocker??='Bounded original continuation did not settle before the Stop margin';
      const stop=page.getByRole('button',{name:'Stop',exact:true});
      if(await stop.isEnabled()){await stop.click();result.owner_stop_requested=true;}
    }
    result.phase=result.final_work?.state==='completed'?'native_complete':'original_outcome_retained';
  }
}catch(error){result.blocker=String(error);result.phase=result.sdk_continuation_sent?'blocked_original_send_retained':'blocked_before_send';}
finally{
  result.final_request=record('requests',requestId);result.final_work=record('work',workId);
  result.final_context=record('contexts',before.original_context.context_id);
  result.final_requests=records('requests');result.final_continuation_sends=records('copilot.continuation_sends');
  result.final_permissions=records('copilot.permissions');result.final_invocations=records('invocations');
  result.snapshot=await get(`${prefix}/snapshot`).catch(()=>null);
  await page.screenshot({path:`${root}/final.private.png`}).catch(()=>{});
  await save();db.close();await context.close();
  console.log(JSON.stringify({phase:result.phase,context_status:result.context_capture?.status,metadata_operation:result.metadata_operation?.state,
    continuation_sends:result.final_continuation_sends.length,blocker:result.blocker}));
}
