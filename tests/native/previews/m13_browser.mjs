// Real Full-HD owner UI, actual app routes/media. No route mocks or hidden model sends.
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
const root=process.argv[2], action=process.argv[3];
const info=JSON.parse(await fs.readFile(`${root}/processes.json`,'utf8'));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${info.cdp}`);
const page=browser.contexts()[0].pages()[0];
await page.setViewportSize({width:1920,height:1080});page.setDefaultTimeout(15000);
const p='/api/v1/projects/m13-cinematic';
const get=path=>page.evaluate(async path=>{const r=await fetch(path);if(!r.ok)throw Error(`${path}: ${r.status}`);return r.json();},path);
const save=(name,value)=>fs.writeFile(`${root}/${name}.json`,JSON.stringify(value,null,2));
async function collect(label){
  const value={snapshot:await get(`${p}/snapshot`),requests:await get(`${p}/requests`),events:await get(`${p}/events?after=0&limit=100`),
    items:await get(`${p}/items`),jobs:await get(`${p}/jobs`),annotations:await get(`${p}/annotations`),selections:await get(`${p}/selections`),
    browser:browser.version(),viewport:await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}))};
  value.versions={};value.artifacts={};
  for(const item of value.items.items){
    const versions=await get(`${p}/items/${item.item_id}/versions`);value.versions[item.item_id]=versions;
    if(item.kind==='sequence')for(const v of versions.items){
      for(const id of v.artifact_ids){const a=await get(`${p}/artifacts/${id}`);value.artifacts[id]=a;}
    }
  }
  for(const a of value.annotations.items)for(const id of [a.target.artifact_id,a.target.frame_map_artifact_id,a.target.media_artifact_id])if(id)value.artifacts[id]=await get(`${p}/artifacts/${id}`);
  value.view=await page.evaluate(()=>({version:JSON.parse(localStorage.getItem('proscenium.owner.view.version.m13-cinematic')??'null'),context:JSON.parse(localStorage.getItem('proscenium.owner.view.context.m13-cinematic')??'null')}));
  await save(`public-${label}`,value);return value;
}
async function waitJob(id){
  const start=Date.now();while(Date.now()-start<65000){const job=await get(`${p}/jobs/${id}`);if(['succeeded','failed','cancelled','outcome_uncertain'].includes(job.state)){if(job.state!=='succeeded')throw Error(`Encode ${id}: ${job.state}`);return job;}await page.waitForTimeout(250);}throw Error('Encode deadline exceeded');
}
async function send(number){
  const text=await fs.readFile(`${root}/${number===1?'first':'second'}.txt`,'utf8');
  const existing=(await get(`${p}/requests`)).items;
  const continued=await fs.access(`${root}/resume.intent.json`).then(()=>1,()=>0);
  if(existing.some(r=>r.submission.text===text)||existing.filter(r=>r.type==='queue'||r.submission.type==='queue').length+continued>=3)throw Error('Root identity/count already consumed');
  const hash=createHash('sha256').update(text).digest('hex');
  await fs.writeFile(`${root}/root-${number}.intent.json`,JSON.stringify({prompt_sha256:hash,state:'before-ui-send'}),{flag:'wx',mode:0o600});
  await page.getByRole('textbox',{name:'Message Copilot',exact:true}).fill(text);
  await page.getByRole('button',{name:'Queue message',exact:true}).click();
  const start=Date.now();let record;
  let released=false;
  while(Date.now()-start<169000){
    record=(await get(`${p}/requests`)).items.find(r=>r.submission.text===text);
    if(number===2 && record?.receipt.state==='held' && !released){
      const mode=page.getByLabel('Resume mode',{exact:true});
      if(await mode.isVisible()) {await mode.selectOption('release_held_queue');await page.getByRole('button',{name:'Resume',exact:true}).click();released=true;}
    }
    if(record&&['completed','failed','stopped','outcome_uncertain','awaiting_resume'].includes(record.receipt.state))break;
    if((await get(`${p}/snapshot`)).pending_decisions.length)throw Error('Unexpected decision. No unallocated approval/model request allowed.');
    await page.waitForTimeout(500);
  }
  await save(`root-${number}`,{record,elapsed_ms:Date.now()-start});
  if(!record||record.receipt.state!=='completed'){
    const stop=page.getByRole('button',{name:'Stop',exact:true});if(await stop.isEnabled())await stop.click();
    throw Error(`Root ${number}: ${record?.receipt.state}`);
  }
  await waitJob(`m13-encode-v${number}`);await collect(`root-${number}`);
}
async function view(version){
  await page.getByRole('button').filter({hasText:new RegExp(`^M13 two-shot cinematic.*${version}$`)}).click();
  await page.getByRole('button',{name:'3 Animation',exact:true}).click();
  await page.getByRole('link',{name:'Download final video',exact:true}).waitFor();
  await page.locator('video').waitFor();
}
async function downloads(label){
  await fs.mkdir(`${root}/downloads`,{recursive:true});const out=[];
  for(const name of ['Download final video','Download delivery manifest']){
    const link=page.getByRole('link',{name,exact:true});const href=await link.getAttribute('href');
    const pending=page.waitForEvent('download');await link.click();const download=await pending;
    const path=`${root}/downloads/${download.suggestedFilename()}`;await download.saveAs(path);
    const bytes=await fs.readFile(path);out.push({name,href,path,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')});
  }
  await save(`downloads-${label}`,out);
}
try{
  if(action==='setup'){
    await page.goto((await fs.readFile(`${root}/pairing.url`,'utf8')).trim());
    const pair=page.getByRole('button',{name:'Connect this browser'});if(await pair.isVisible())await pair.click();
    await page.getByRole('button',{name:'Choose a project',exact:true}).click();
    await page.getByLabel('Saved project',{exact:true}).selectOption('m13-cinematic');
    await page.getByRole('button',{name:'Activate selected project',exact:true}).click();
    await page.getByRole('textbox',{name:'Message Copilot',exact:true}).waitFor();
    await collect('setup');
  }else if(action==='send'){await send(Number(process.argv[4]));}
  else if(action==='reload'){await page.reload();await page.getByRole('textbox',{name:'Message Copilot',exact:true}).waitFor();}
  else if(action==='resume'){
    await page.goto((await fs.readFile(`${root}/pairing.url`,'utf8')).trim());
    const pair=page.getByRole('button',{name:'Connect this browser'});if(await pair.isVisible())await pair.click();
    await page.getByLabel('Resume mode',{exact:true}).waitFor();
    const pendingStop=page.getByRole('button',{name:'Reconcile original Stop',exact:true});
    if(await pendingStop.isVisible())await pendingStop.click();
    const snapshot=await get(`${p}/snapshot`);
    await page.getByLabel('Resume mode',{exact:true}).selectOption('continue_work');
    await page.getByLabel('Original work to continue',{exact:true}).selectOption(snapshot.active_work.work_id);
    await fs.writeFile(`${root}/resume.intent.json`,JSON.stringify({work_id:snapshot.active_work.work_id,prior_request_state:(await get(`${p}/requests`)).items[0].receipt.state}),{flag:'wx'});
    await page.getByRole('button',{name:'Resume',exact:true}).click();
    const start=Date.now();let record;
    while(Date.now()-start<125000){
      record=(await get(`${p}/requests`)).items.find(r=>r.submission.type==='queue');
      if(record?.receipt.state==='completed')break;
      await page.waitForTimeout(500);
    }
    await save('resume-result',{record,elapsed_ms:Date.now()-start});
    if(record?.receipt.state!=='completed')throw Error(`Continuation did not complete: ${record?.receipt.state}`);
    await waitJob('m13-encode-v1');await collect('resumed-v1');
  }
  else if(action==='collect'){await collect(process.argv[4]??'state');await page.screenshot({path:`${root}/${process.argv[4]??'state'}.png`});}
  else if(action==='review'){
    await view('m13-film-v1');
    const before=await collect('before-owner-selection');if(before.selections.items.length)throw Error('Model/technical success created an owner selection');
    const playback=await page.locator('video').evaluate(async element=>{
      const frames=[];let callback;
      const observe=(_now,data)=>{frames.push({mediaTime:data.mediaTime,presentedFrames:data.presentedFrames,width:data.width,height:data.height});callback=element.requestVideoFrameCallback(observe);};
      callback=element.requestVideoFrameCallback(observe);element.currentTime=0;await element.play();
      await new Promise((resolve,reject)=>{element.addEventListener('ended',resolve,{once:true});setTimeout(()=>reject(Error('Playback deadline')),4000);});
      element.cancelVideoFrameCallback(callback);return frames;
    });await save('playback-v1',playback);
    await page.getByLabel('Frame',{exact:true}).fill('5');
    await page.getByText('Sequence frame 5 ·',{exact:false}).waitFor();
    await page.getByRole('button',{name:'Annotate presented sequence frame',exact:true}).click();
    await page.getByRole('button',{name:'pin',exact:true}).click();
    await page.getByLabel('Pin text',{exact:true}).fill('M13 actual dissolved frame');
    const box=await page.getByLabel('Editable image-space markup',{exact:true}).boundingBox();
    await page.mouse.click(box.x+box.width*.5,box.y+box.height*.5);
    await page.getByLabel('Feedback',{exact:true}).fill('M13 v1 frame 5 / range 4-6: retain the exact original blend mapping');
    await page.getByLabel('Range start',{exact:true}).fill('4');await page.getByLabel('Range end',{exact:true}).fill('6');
    const saved=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/annotations'));
    await page.getByRole('button',{name:'Save annotation',exact:true}).click();if(!(await saved).ok())throw Error('Annotation save rejected');
    await page.screenshot({path:`${root}/annotation-v1.png`});
    await page.getByRole('button',{name:'Close draft',exact:true}).click();
    await downloads('v1');
    const card=page.locator('.review-catalogue article').filter({has:page.getByRole('button').filter({hasText:/^M13 two-shot cinematic.*m13-film-v1$/})});
    const select=card.getByRole('button',{name:'Select for item',exact:true});
    const selected=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/selections'));
    await select.click();if(!(await selected).ok())throw Error('Explicit owner selection rejected');
    await collect('review-v1');await fs.writeFile(`${root}/REPLAY`,'original invocation replay');
  }else if(action==='final'){
    await view('m13-film-v2');
    const playback=await page.locator('video').evaluate(async element=>{
      const frames=[];let callback;
      const observe=(_now,data)=>{frames.push({mediaTime:data.mediaTime,presentedFrames:data.presentedFrames,width:data.width,height:data.height});callback=element.requestVideoFrameCallback(observe);};
      callback=element.requestVideoFrameCallback(observe);element.currentTime=0;await element.play();
      await new Promise((resolve,reject)=>{element.addEventListener('ended',resolve,{once:true});setTimeout(()=>reject(Error('Playback deadline')),4000);});
      element.cancelVideoFrameCallback(callback);return frames;
    });await save('playback-v2',playback);
    await downloads('v2');
    const result=await collect('final');
    if(result.annotations.items[0].target.sequence_version_id!=='m13-film-v1')throw Error('Old annotation retargeted');
    if(result.selections.items[0].choice.version.version_id!=='m13-film-v1')throw Error('Owner choice silently followed v2');
    await page.screenshot({path:`${root}/v2-preserved-v1.png`});
    await page.getByText('Saved annotations and local drafts',{exact:true}).click();
    await page.getByRole('button').filter({hasText:/^M13 v1 frame 5 \/ range 4-6/}).click();
    await page.getByText('Pinned m13-film-v1 · frame 5',{exact:true}).waitFor();
    await collect('restored-v1');await page.screenshot({path:`${root}/restored-v1.png`});
  }else throw Error('Unknown action');
  console.log(JSON.stringify({action,status:'completed',url:page.url().split('#')[0]}));
}catch(error){await page.screenshot({path:`${root}/${action}-failure.png`});console.error(String(error));console.error((await page.locator('body').innerText()).slice(-9000));process.exitCode=1;}
finally{await browser.close();}
