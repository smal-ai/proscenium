// Real owner UI + production HTTP. No routes, frames, contexts or player events are mocked.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { chromium } from '@playwright/test';

const root=path.resolve(process.argv[2]), phase=process.argv[3];
const config=JSON.parse(await fs.readFile(`${root}/config.private.json`,'utf8'));
const fixture=JSON.parse(await fs.readFile(`${root}/fixture.json`,'utf8'));
const write=(name,value)=>fs.writeFile(`${root}/${name}`,JSON.stringify(value,null,2)+'\n');
const log={phase,events:[],viewport:{width:1920,height:1080,dpr:1}};
let context,page,csrf;
async function state(final=false){return page.evaluate(async(final)=>{const r=await fetch(`/m08-harness/state?final=${final}`);if(!r.ok)throw new Error(`state ${r.status}`);return r.json();},final);}
async function post(route,body){return page.evaluate(async({route,body,csrf})=>{const r=await fetch(route,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});if(!r.ok)throw new Error(`${route} ${r.status}: ${await r.text()}`);return r.json();},{route,body,csrf});}
async function mark(label,body={}){const value=await post('/m08-harness/mark',{label,...body});log.events.push({label,host_ns:value.host_ns,last_png_exists:value.last_png_exists});return value;}
async function poll(check,seconds,description){const until=Date.now()+seconds*1000;let last;while(Date.now()<until){try{last=await state();if(check(last))return last;}catch(error){if(!description.includes('restart'))throw error;}await page.waitForTimeout(40);}if(last)await write(`timeout-${phase}.json`,last);throw new Error(`Bounded wait: ${description}`);}
async function reserve(label){for(const slot of [1,2,3]){try{const f=await fs.open(`${path.dirname(root)}/m08-a2a2097-cloud-${slot}.json`,'wx',0o600);await f.writeFile(JSON.stringify({root,label,slot,at:new Date().toISOString()}));await f.close();return slot;}catch(error){if(error.code!=='EEXIST')throw error;}}throw new Error('Three-root allocation exhausted. Inspect original reservations.');}
async function queue(text,label){const slot=await reserve(label);await fs.writeFile(`${root}/send-${label}.json`,JSON.stringify({slot,at:new Date().toISOString()}),{flag:'wx',mode:0o600});await page.getByRole('textbox',{name:'Message Copilot',exact:true}).fill(text);await page.getByRole('button',{name:'Queue message',exact:true}).click();log.events.push({label,slot,at:new Date().toISOString()});}

try{
  const until=Date.now()+30000;while(true){try{if((await fetch(config.origin+'/health')).ok)break;}catch{}if(Date.now()>until)throw new Error('Host did not become ready');await new Promise(r=>setTimeout(r,80));}
  context=await chromium.launchPersistentContext(`${root}/browser-profile`,{executablePath:path.join(os.homedir(),'.cache/ms-playwright/chromium-1255/chrome-linux64/chrome'),headless:true,viewport:{width:1920,height:1080},deviceScaleFactor:1,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader','--disable-background-networking']});
  page=context.pages()[0];page.setDefaultTimeout(12000);log.browser=context.browser().version();
  if(phase==='initial'){
    const pair=page.waitForResponse(r=>r.url().endsWith('/api/v1/owner/session')&&r.request().method()==='POST');
    await page.goto((await fs.readFile(`${root}/pairing.private.url`,'utf8')).trim());
    await page.getByRole('button',{name:'Connect this browser'}).click();csrf=(await(await pair).json()).csrf_token;
    await fs.writeFile(`${root}/csrf.private`,csrf,{mode:0o600});
    // Install passive presentation observers BEFORE the model starts authoring.
    await page.evaluate(()=>{
      window.__m08Presentations=[];window.__m08Ended=[];
      const observed=new WeakSet();
      function install(){for(const video of document.querySelectorAll('.review-player video:not(.review-prebuffer)')){if(observed.has(video))continue;observed.add(video);const read=(now,meta)=>{window.__m08Presentations.push({now,src:video.currentSrc||video.src,mediaTime:meta.mediaTime,presentedFrames:meta.presentedFrames,paused:video.paused});video.requestVideoFrameCallback(read);};video.requestVideoFrameCallback(read);video.addEventListener('ended',()=>window.__m08Ended.push({now:performance.now(),src:video.currentSrc||video.src}));}}
      new MutationObserver(install).observe(document.body,{subtree:true,childList:true});install();
    });
    await page.getByRole('button',{name:'Assets',exact:true}).click();
    const started=Date.now();await queue(await fs.readFile(`${root}/prompt.txt`,'utf8'),'author-animation');
    let current=await poll(s=>s.versions.some(v=>v.version_id==='m08-scene-v1'),180,'actual source publication');
    log.publication_seconds=(Date.now()-started)/1000;
    await write('publication-first-observed.json',current);
    const motion=current.jobs.find(j=>j.render_spec?.intent_id==='m08-motion');assert.ok(motion);
    await page.getByRole('button',{name:'Refresh project metadata',exact:true}).click();
    await page.getByRole('button').filter({has:page.locator('small',{hasText:/^m08-scene-v1$/})}).click();
    await page.getByRole('button',{name:'1 3D Inspection',exact:true}).click();
    await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='Render this view'&&!b.disabled));
    log.events.push({label:'actual new-source proxy loaded, no Render this view click'});
    await page.getByRole('button',{name:'2 Keyframes',exact:true}).click();
    const key=current.jobs.find(j=>j.render_spec?.intent_id==='m08-keyframe');
    await page.getByLabel('Shot / render job',{exact:true}).selectOption(key.job_id);
    await page.waitForFunction(()=>document.querySelector('.review-image img')?.naturalWidth===640);
    log.events.push({label:'actual material-capable keyframe inspected'});
    await page.getByRole('button',{name:'3 Animation',exact:true}).click();
    await page.getByLabel('Shot / render job',{exact:true}).selectOption(motion.job_id);
    await page.getByLabel('Frame',{exact:true}).fill('12');
    await page.getByRole('status').filter({hasText:'Frame 12 pending'}).waitFor();
    await mark('pending-frame12',{job_id:motion.job_id});
    await page.getByLabel('Frame',{exact:true}).fill('1');
    await page.getByRole('button',{name:'Play',exact:true}).click();
    await page.waitForFunction(()=>window.__m08Presentations.some(row=>row.mediaTime>=1/24-1e-5),{},{timeout:20000});
    await page.getByRole('button',{name:'Annotate presented frame',exact:true}).click();
    await page.getByRole('button',{name:'pin',exact:true}).click();
    await page.getByLabel('Pin text',{exact:true}).fill('M08 motion pin');
    const box=await page.locator('.review-canvas').boundingBox();
    const scale=Math.min(box.width/640,box.height/360);
    await page.mouse.click(box.x+(box.width-640*scale)/2+320*scale,box.y+(box.height-360*scale)/2+180*scale);
    await page.getByLabel('Feedback',{exact:true}).fill('M08 retained motion note. Preserve this exact source frame and range; no source correction requested.');
    await page.getByLabel('Range start',{exact:true}).fill('1');await page.getByLabel('Range end',{exact:true}).fill('4');
    await page.getByRole('button',{name:'Save annotation',exact:true}).click();
    current=await poll(s=>s.annotations.length===1,15,'actual annotation save');
    const annotation=current.annotations[0];assert.equal(annotation.target.render_job_id,motion.job_id);assert.ok(annotation.target.source_frame>=1&&annotation.target.source_frame<=4);
    assert.deepEqual(annotation.range,{start:1,end:4});
    const saved=await mark('annotation-ready',{job_id:motion.job_id});assert.equal(saved.last_png_exists,false);
    await write('annotation-original.json',annotation);
    await page.getByRole('button',{name:'Close draft',exact:true}).click();
    await page.getByRole('button',{name:'Play',exact:true}).click();
    await page.waitForFunction(()=>window.__m08Ended.length>0,{},{timeout:15000});
    const played=await mark('first-segment-played',{presentations:await page.evaluate(()=>window.__m08Presentations),ended:await page.evaluate(()=>window.__m08Ended)});assert.equal(played.last_png_exists,false);
    const pause=page.getByRole('button',{name:'Pause',exact:true});if(await pause.count())await pause.click();
    await page.getByText('Saved annotations and local drafts',{exact:true}).click();
    await page.getByRole('button',{name:/M08 retained motion note.*saved/}).click();
    // This is the actual owner pin/editor, kept mounted while more output arrives.
    await post('/m08-harness/crash',{job_id:motion.job_id});
    log.events.push({label:'owned backend SIGKILL requested while exact renderer alive'});
    current=await poll(s=>s.owner_epoch!==played.motion_job.owner_epoch
      &&s.jobs.find(j=>j.job_id===motion.job_id)?.state==='succeeded',60,'passive restart/reconciliation');
    assert.equal(current.gate_open,false);assert.equal(current.sdk_object_created,false);
    assert.deepEqual(current.annotations[0],annotation);
    const held=current.jobs.find(j=>j.render_spec?.intent_id==='m08-held-output');assert.ok(held);assert.equal(held.state,'planned');
    assert.equal(current.requests.length,1);
    assert.equal(current.frames.filter(f=>f.job_id===motion.job_id).length,12);
    assert.equal(current.segments.filter(s=>s.job_id===motion.job_id).length,3);
    assert.equal(current.jobs.find(j=>j.job_id===motion.job_id).attempt_id,motion.attempt_id);
    await write('passive-recovery.json',current);
    await page.screenshot({path:`${root}/after-passive-recovery.png`});
    await page.reload();await page.getByText('Saved annotations and local drafts',{exact:true}).click();
    await page.getByRole('button',{name:/M08 retained motion note.*saved/}).click();
    await page.getByLabel('Pinned annotation',{exact:true}).waitFor();
    await page.screenshot({path:`${root}/annotation-restored.png`});
    log.status='progressive_note_playback_before_last_and_passive_worker_recovery_pass';
  }else{
    csrf=await fs.readFile(`${root}/csrf.private`,'utf8');await page.goto(config.origin);
    throw new Error('Explicit assembly continuation must be selected after inspecting original recovery outcome. No automatic replay.');
  }
}catch(error){log.status='failed';log.error=String(error.stack??error);if(page){await page.screenshot({path:`${root}/failure-${phase}.png`}).catch(()=>{});const last=await state(true).catch(()=>null);if(last)await write(`failure-${phase}.json`,last);}throw error;
}finally{await context?.close();log.browser_closed=true;await write(`browser-${phase}.json`,log);}
